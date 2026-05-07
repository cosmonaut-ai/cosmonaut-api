"""Usage tracking and quota enforcement service.

Provides atomic check-and-increment operations backed by DynamoDB conditional
updates to prevent race conditions.  The ``UserRecord`` is lazily created
on first access (defaults to the FREE tier).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pynamodb.exceptions import UpdateError

from app.core.config import get_tier_limits
from app.core.errors import QuotaError
from app.core.observability import MetricUnit, logger, metrics, tracer
from app.models.entities.user import UsageData, UsageTombstone, UserRecord

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class QuotaExceededError(QuotaError):
  """Raised when a user has reached their tier's usage limit."""

  def __init__(self, metric: str, limit: int):
    super().__init__(f"Quota exceeded: {metric} limit is {limit}")
    self.metric = metric
    self.limit = limit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_METRIC_ATTR = {
  "worlds": "worlds_created",
  "nodes": "nodes_used",
  "audio": "audio_narrations_used",
}

_METRIC_LIMIT_KEY = {
  "worlds": "worlds",
  "nodes": "nodes",
  "audio": "audio_limit",
}


def _new_period_end(tier: str) -> datetime:
  """Calculate the next period end timestamp for *tier*."""
  reset_days = get_tier_limits(tier)["reset_days"]
  return datetime.now(UTC) + timedelta(days=reset_days)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _check_and_consume_tombstone(email: str) -> dict[str, Any] | None:
  """Look up and delete a usage tombstone for the given email.

  Returns the carried-forward counters if a valid tombstone exists,
  or None if no tombstone is found.
  """
  email_hash = hashlib.sha256(email.lower().strip().encode()).hexdigest()
  try:
    tombstone = UsageTombstone.get(UsageTombstone.pk(email_hash), UsageTombstone.sk())
  except UsageTombstone.DoesNotExist:
    return None

  result = {
    "nodes_used": int(tombstone.nodes_used or 0),
    "worlds_created": int(tombstone.worlds_created or 0),
    "audio_narrations_used": int(tombstone.audio_narrations_used or 0),
    "period_end": tombstone.period_end,
  }

  tombstone.delete()
  return result


@tracer.capture_method
def get_or_create_usage(user_id: str, email: str | None = None) -> UserRecord:
  """Return the ``UserRecord``, creating a FREE-tier default if absent.

  Also performs a *lazy period reset*: if ``period_end`` has passed the
  counters are zeroed and a new period is started.
  """
  try:
    record = UserRecord.get(UserRecord.pk(user_id), UserRecord.sk())
  except UserRecord.DoesNotExist:
    carried = _check_and_consume_tombstone(email) if email else None
    now = datetime.now(UTC)

    if carried:
      period_still_active = carried["period_end"] and carried["period_end"] > now

      record = UserRecord(
        PK=UserRecord.pk(user_id),
        SK=UserRecord.sk(),
        user_id=user_id,
        usage=UsageData(
          tier="FREE",
          nodes_used=carried["nodes_used"] if period_still_active else 0,
          worlds_created=carried["worlds_created"] if period_still_active else 0,
          audio_narrations_used=carried["audio_narrations_used"],
          period_end=carried["period_end"] if period_still_active else _new_period_end("FREE"),
        ),
      )
      logger.info(
        "Created usage for user %s from tombstone (period_active=%s, audio_carried=%d)",
        user_id,
        period_still_active,
        carried["audio_narrations_used"],
      )
    else:
      record = UserRecord(
        PK=UserRecord.pk(user_id),
        SK=UserRecord.sk(),
        user_id=user_id,
        usage=UsageData(
          tier="FREE",
          nodes_used=0,
          worlds_created=0,
          audio_narrations_used=0,
          period_end=_new_period_end("FREE"),
        ),
      )
      logger.info("Created default FREE usage record for user %s", user_id)

    record.save()
    return record

  # Lazy period reset
  u = record.usage
  now = datetime.now(UTC)
  if u.period_end and now > u.period_end:
    tier = str(u.tier) if u.tier else "FREE"
    u.nodes_used = 0
    u.worlds_created = 0
    # FREE and EXPLORER share a lifetime audio pool (10 narrations, never resets).
    # Only COSMONAUT gets fresh audio quota each billing period.
    if tier == "COSMONAUT":
      u.audio_narrations_used = 0
    u.period_end = _new_period_end(tier)
    record.updated_at = now
    record.save()
    logger.info(f"Period reset for user {user_id} (tier={tier})")

  return record


@tracer.capture_method
def check_and_increment(user_id: str, metric: Literal["worlds", "nodes", "audio"], email: str | None = None) -> None:
  """Atomically increment *metric* if the user is within their tier's quota.

  Raises ``QuotaExceededError`` when the limit has been reached.

  Implementation note: we first ensure the record exists (+ lazy reset) then
  perform a DynamoDB *conditional update* so the increment only succeeds when
  the current counter value is below the tier limit.
  """
  record = get_or_create_usage(user_id, email=email)
  tier = str(record.usage.tier) if record.usage.tier else "FREE"
  limits = get_tier_limits(tier)
  limit_key = _METRIC_LIMIT_KEY[metric]
  limit_value: int = limits[limit_key]

  attr_name = _METRIC_ATTR[metric]
  attr = getattr(UserRecord.usage, attr_name)

  try:
    record.update(
      actions=[attr.set((attr | 0) + 1)],
      condition=((attr < limit_value) | attr.does_not_exist()),
    )
  except UpdateError as exc:
    metrics.add_metric(name="QuotaExceeded", unit=MetricUnit.Count, value=1)
    metrics.add_dimension(name="metric_type", value=metric)
    logger.error(f"Quota exceeded for {metric} for user {user_id} with limit {limit_value}", exc_info=True)
    raise QuotaExceededError(metric, limit_value) from exc


def release_quota(user_id: str, metric: Literal["worlds", "nodes", "audio"]) -> None:
  """Reverse a prior ``check_and_increment`` after a failed operation.

  Best-effort: logs a warning if the decrement fails (e.g. counter already
  at zero due to a concurrent period reset) but never raises, so the
  original error always propagates unobstructed.
  """
  attr_name = _METRIC_ATTR[metric]
  attr = getattr(UserRecord.usage, attr_name)
  try:
    record = get_or_create_usage(user_id)
    record.update(
      actions=[attr.set((attr | 0) - 1)],
      condition=(attr > 0),
    )
  except Exception:
    logger.warning(f"Failed to release {metric} quota for user {user_id} — counter may be slightly inflated")


def update_subscription_status(user_id: str, subscription_status: str) -> UserRecord:
  """Persist the raw Stripe subscription status for frontend visibility."""
  record = get_or_create_usage(user_id)
  record.usage.subscription_status = subscription_status
  record.updated_at = datetime.now(UTC)
  record.save()
  logger.info(f"Updated subscription_status for user {user_id} to {subscription_status}")
  return record


def update_tier(
  user_id: str,
  tier: str,
  stripe_customer_id: str | None = None,
) -> UserRecord:
  """Set the user's subscription tier (called by the Stripe webhook).

  Resets usage counters and starts a new billing period for the given tier.
  Clears any pending cancellation state.

  Audio reset logic:
  - FREE and EXPLORER share a lifetime audio pool (10 narrations, never resets).
    Changing between FREE ↔ EXPLORER must NOT reset audio_narrations_used.
  - COSMONAUT has its own monthly pool (150/month, resets each period).
    Upgrading TO Cosmonaut resets audio; downgrading FROM Cosmonaut does not.
  """
  record = get_or_create_usage(user_id)
  now = datetime.now(UTC)
  u = record.usage

  old_tier = str(u.tier) if u.tier else "FREE"

  u.tier = tier
  if stripe_customer_id is not None:
    u.stripe_customer_id = stripe_customer_id
  u.nodes_used = 0
  u.worlds_created = 0

  # Only reset audio when upgrading TO Cosmonaut (which has its own monthly pool).
  # FREE ↔ EXPLORER transitions preserve the shared lifetime audio counter.
  if tier == "COSMONAUT" and old_tier != "COSMONAUT":
    u.audio_narrations_used = 0

  u.period_end = _new_period_end(tier)
  u.pending_cancellation = False
  u.cancellation_date = None
  u.pending_tier = None
  u.pending_tier_date = None
  u.subscription_status = "active" if tier != "FREE" else None
  record.updated_at = now
  record.save()

  logger.info(f"Updated tier for user {user_id} to {tier} (from {old_tier})")
  return record


def set_pending_cancellation(user_id: str, cancellation_date: datetime) -> UserRecord:
  """Mark a subscription as pending cancellation (cancel_at or cancel_at_period_end)."""
  record = get_or_create_usage(user_id)
  record.usage.pending_cancellation = True
  record.usage.cancellation_date = cancellation_date
  record.usage.subscription_status = "active"
  record.updated_at = datetime.now(UTC)
  record.save()
  logger.info(f"Set pending cancellation for user {user_id} (ends {cancellation_date.isoformat()})")
  return record


def clear_pending_cancellation(user_id: str) -> UserRecord:
  """Clear pending cancellation state (e.g. after a successful renewal or un-cancel)."""
  record = get_or_create_usage(user_id)
  if record.usage.pending_cancellation:
    record.usage.pending_cancellation = False
    record.usage.cancellation_date = None
    record.usage.subscription_status = "active"
    record.updated_at = datetime.now(UTC)
    record.save()
    logger.info(f"Cleared pending cancellation for user {user_id}")
  return record


def set_pending_plan_change(user_id: str, pending_tier: str, effective_date: datetime) -> UserRecord:
  """Record a scheduled plan change (e.g. downgrade at end of billing period)."""
  record = get_or_create_usage(user_id)
  record.usage.pending_tier = pending_tier
  record.usage.pending_tier_date = effective_date
  record.updated_at = datetime.now(UTC)
  record.save()
  logger.info(f"Set pending plan change for user {user_id}: {pending_tier} on {effective_date.isoformat()}")
  return record


def clear_pending_plan_change(user_id: str) -> UserRecord:
  """Clear pending plan change state (e.g. change was applied or cancelled)."""
  record = get_or_create_usage(user_id)
  if record.usage.pending_tier:
    record.usage.pending_tier = None
    record.usage.pending_tier_date = None
    record.updated_at = datetime.now(UTC)
    record.save()
    logger.info(f"Cleared pending plan change for user {user_id}")
  return record


def reset_period(user_id: str) -> UserRecord:
  """Reset usage counters and extend the billing period (e.g. on renewal)."""
  record = get_or_create_usage(user_id)
  u = record.usage
  tier = str(u.tier) if u.tier else "FREE"
  now = datetime.now(UTC)

  u.nodes_used = 0
  u.worlds_created = 0
  # FREE and EXPLORER share a lifetime audio pool — only Cosmonaut resets audio.
  if tier == "COSMONAUT":
    u.audio_narrations_used = 0
  u.period_end = _new_period_end(tier)
  u.pending_cancellation = False
  u.cancellation_date = None
  u.pending_tier = None
  u.pending_tier_date = None
  u.subscription_status = "active"
  record.updated_at = now
  record.save()

  logger.info(f"Period reset (renewal) for user {user_id}")
  return record
