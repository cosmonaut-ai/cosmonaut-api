"""Usage tracking and quota enforcement service.

Provides atomic check-and-increment operations backed by DynamoDB conditional
updates to prevent race conditions.  The ``UserUsage`` record is lazily created
on first access (defaults to the FREE tier).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from aws_lambda_powertools import Logger
from pynamodb.exceptions import UpdateError

from app.core.config import TIER_LIMITS, settings
from app.models.entities.usage import UserUsage

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class QuotaExceededError(Exception):
  """Raised when a user has reached their tier's usage limit."""

  def __init__(self, metric: str, limit: int):
    super().__init__(f"Quota exceeded: {metric} limit is {limit}")
    self.metric = metric
    self.limit = limit


class StorageQuotaExceededError(Exception):
  """Raised when a user has reached their tier's storage limit for saved worlds."""

  def __init__(self, metric: str, limit: int, current: int):
    super().__init__(
      f"Storage quota exceeded: you have {current} {metric} "
      f"(limit is {limit}). Delete existing worlds or upgrade your plan."
    )
    self.metric = metric
    self.limit = limit
    self.current = current


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
  reset_days = TIER_LIMITS.get(tier, TIER_LIMITS["FREE"])["reset_days"]
  return datetime.now(timezone.utc) + timedelta(days=reset_days)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_or_create_usage(user_id: str) -> UserUsage:
  """Return the ``UserUsage`` record, creating a FREE-tier default if absent.

  Also performs a *lazy period reset*: if ``period_end`` has passed the
  counters are zeroed and a new period is started.
  """
  try:
    usage = UserUsage.get(UserUsage.pk(user_id), UserUsage.sk())
  except UserUsage.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
    usage = UserUsage(
      PK=UserUsage.pk(user_id),
      SK=UserUsage.sk(),
      user_id=user_id,
      tier="FREE",
      nodes_used=0,
      worlds_created=0,
      audio_narrations_used=0,
      period_end=_new_period_end("FREE"),
    )
    usage.save()
    logger.info(f"Created default FREE usage record for user {user_id}")
    return usage

  # Lazy period reset
  now = datetime.now(timezone.utc)
  if usage.period_end and now > usage.period_end:
    tier = str(usage.tier) if usage.tier else "FREE"
    usage.nodes_used = 0
    usage.worlds_created = 0
    # Skip resetting audio_narrations_used for FREE tier to enforce a lifetime cap.
    # Paid tiers reset audio usage each billing period.
    if tier != "FREE":
      usage.audio_narrations_used = 0
    usage.period_end = _new_period_end(tier)
    usage.updated_at = now
    usage.save()
    logger.info(f"Period reset for user {user_id} (tier={tier})")

  return usage


def check_storage_quota(user_id: str) -> None:
  """Ensure the user has room for another saved world.

  Unlike the periodic rate limit, this counts *actual stored worlds* via a
  GSI1 count query so the value cannot drift from reality.

  Raises ``StorageQuotaExceededError`` when the user is at capacity.
  """
  # Lazy import to avoid circular dependency (usage -> worlds -> usage)
  from app.services.worlds import count_user_worlds

  usage = get_or_create_usage(user_id)
  tier = str(usage.tier) if usage.tier else "FREE"
  limits = TIER_LIMITS.get(tier, TIER_LIMITS["FREE"])
  saved_worlds_limit: int = limits["saved_worlds"]

  current_count = count_user_worlds(user_id)
  if current_count >= saved_worlds_limit:
    raise StorageQuotaExceededError("saved_worlds", saved_worlds_limit, current_count)


def check_and_increment(user_id: str, metric: Literal["worlds", "nodes", "audio"]) -> None:
  """Atomically increment *metric* if the user is within their tier's quota.

  Raises ``QuotaExceededError`` when the limit has been reached.

  Implementation note: we first ensure the record exists (+ lazy reset) then
  perform a DynamoDB *conditional update* so the increment only succeeds when
  the current counter value is below the tier limit.
  """
  usage = get_or_create_usage(user_id)
  tier = str(usage.tier) if usage.tier else "FREE"
  limits = TIER_LIMITS.get(tier, TIER_LIMITS["FREE"])
  limit_key = _METRIC_LIMIT_KEY[metric]
  limit_value: int = limits[limit_key]

  attr_name = _METRIC_ATTR[metric]
  attr = getattr(UserUsage, attr_name)

  try:
    usage.update(
      actions=[attr.set((attr | 0) + 1)],
      condition=((attr < limit_value) | attr.does_not_exist()),
    )
  except UpdateError as exc:
    logger.error(f"Quota exceeded for {metric} for user {user_id} with limit {limit_value}", exc_info=True)
    raise QuotaExceededError(metric, limit_value) from exc


def update_subscription_status(user_id: str, subscription_status: str) -> UserUsage:
  """Persist the raw Stripe subscription status for frontend visibility."""
  usage = get_or_create_usage(user_id)
  usage.subscription_status = subscription_status
  usage.updated_at = datetime.now(timezone.utc)
  usage.save()
  logger.info(f"Updated subscription_status for user {user_id} to {subscription_status}")
  return usage


def update_tier(
  user_id: str,
  tier: str,
  stripe_customer_id: str | None = None,
) -> UserUsage:
  """Set the user's subscription tier (called by the Stripe webhook).

  Resets usage counters and starts a new billing period for the given tier.
  Clears any pending cancellation state.
  """
  usage = get_or_create_usage(user_id)
  now = datetime.now(timezone.utc)

  usage.tier = tier
  if stripe_customer_id is not None:
    usage.stripe_customer_id = stripe_customer_id
  usage.nodes_used = 0
  usage.worlds_created = 0
  usage.audio_narrations_used = 0
  usage.period_end = _new_period_end(tier)
  usage.pending_cancellation = False
  usage.cancellation_date = None  # type: ignore[assignment]
  usage.pending_tier = None  # type: ignore[assignment]
  usage.pending_tier_date = None  # type: ignore[assignment]
  usage.subscription_status = "active" if tier != "FREE" else None  # type: ignore[assignment]
  usage.updated_at = now
  usage.save()

  logger.info(f"Updated tier for user {user_id} to {tier}")
  return usage


def set_pending_cancellation(user_id: str, cancellation_date: datetime) -> UserUsage:
  """Mark a subscription as pending cancellation (cancel_at or cancel_at_period_end)."""
  usage = get_or_create_usage(user_id)
  usage.pending_cancellation = True
  usage.cancellation_date = cancellation_date
  usage.subscription_status = "active"
  usage.updated_at = datetime.now(timezone.utc)
  usage.save()
  logger.info(f"Set pending cancellation for user {user_id} (ends {cancellation_date.isoformat()})")
  return usage


def clear_pending_cancellation(user_id: str) -> UserUsage:
  """Clear pending cancellation state (e.g. after a successful renewal or un-cancel)."""
  usage = get_or_create_usage(user_id)
  if usage.pending_cancellation:
    usage.pending_cancellation = False
    usage.cancellation_date = None  # type: ignore[assignment]
    usage.subscription_status = "active"
    usage.updated_at = datetime.now(timezone.utc)
    usage.save()
    logger.info(f"Cleared pending cancellation for user {user_id}")
  return usage


def set_pending_plan_change(user_id: str, pending_tier: str, effective_date: datetime) -> UserUsage:
  """Record a scheduled plan change (e.g. downgrade at end of billing period)."""
  usage = get_or_create_usage(user_id)
  usage.pending_tier = pending_tier
  usage.pending_tier_date = effective_date
  usage.updated_at = datetime.now(timezone.utc)
  usage.save()
  logger.info(f"Set pending plan change for user {user_id}: {pending_tier} on {effective_date.isoformat()}")
  return usage


def clear_pending_plan_change(user_id: str) -> UserUsage:
  """Clear pending plan change state (e.g. change was applied or cancelled)."""
  usage = get_or_create_usage(user_id)
  if usage.pending_tier:
    usage.pending_tier = None  # type: ignore[assignment]
    usage.pending_tier_date = None  # type: ignore[assignment]
    usage.updated_at = datetime.now(timezone.utc)
    usage.save()
    logger.info(f"Cleared pending plan change for user {user_id}")
  return usage


def reset_period(user_id: str) -> UserUsage:
  """Reset usage counters and extend the billing period (e.g. on renewal)."""
  usage = get_or_create_usage(user_id)
  tier = str(usage.tier) if usage.tier else "FREE"
  now = datetime.now(timezone.utc)

  usage.nodes_used = 0
  usage.worlds_created = 0
  usage.audio_narrations_used = 0
  usage.period_end = _new_period_end(tier)
  usage.pending_cancellation = False
  usage.cancellation_date = None  # type: ignore[assignment]
  usage.pending_tier = None  # type: ignore[assignment]
  usage.pending_tier_date = None  # type: ignore[assignment]
  usage.subscription_status = "active"
  usage.updated_at = now
  usage.save()

  logger.info(f"Period reset (renewal) for user {user_id}")
  return usage


def get_usage_info(user_id: str) -> UserUsage:
  """Return the current usage record for display purposes."""
  return get_or_create_usage(user_id)
