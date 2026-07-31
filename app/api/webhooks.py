"""Stripe webhook handler.

This router is registered *without* the ``get_current_user`` dependency
because Stripe calls it directly (no JWT).  Authentication is performed
via Stripe signature verification instead.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import stripe
from fastapi import APIRouter, HTTPException, Request, status
from pynamodb.exceptions import PutError

from app.core.config import PRICE_TO_TIER, settings
from app.core.observability import MetricUnit, logger, metrics
from app.core.posthog import capture as ph_capture
from app.core.posthog import identify as ph_identify
from app.models.entities.rate_limit import RateLimitRecord
from app.services.cognito import get_user_contact_info, update_user_tier
from app.services.email import (
  send_payment_failed,
  send_subscription_cancellation_scheduled,
  send_subscription_ended,
  send_subscription_plan_change_scheduled,
  send_subscription_renewed,
  send_subscription_welcome,
)
from app.services.secret_manager import get_secret_value
from app.services.usage import (
  clear_pending_cancellation,
  clear_pending_plan_change,
  get_or_create_usage,
  reset_period,
  set_pending_cancellation,
  set_pending_plan_change,
  update_subscription_status,
  update_tier,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_stripe_api_key() -> str:
  return get_secret_value(settings.STRIPE_API_KEY_PARAM)


def _identify_user(user_id: str, email: str | None = None, name: str | None = None) -> None:
  """Associate a Cognito user ID with person properties in PostHog.

  Webhook handlers only have the Cognito sub (UUID) — without an explicit
  identify call PostHog creates an anonymous person with no email or name.
  """
  props: dict[str, str] = {}
  if email:
    props["email"] = email
  if name:
    props["name"] = name
  if props:
    ph_identify(user_id, props)


def _get_webhook_secret() -> str:
  return get_secret_value(settings.STRIPE_WEBHOOK_SECRET_PARAM)


def _resolve_tier_from_subscription(subscription: dict[str, Any]) -> str | None:
  """Determine the tier name from a Stripe Subscription's price ID."""
  items = subscription.get("items")
  if not items or not isinstance(items, dict):
    return None
  items_dict = cast(dict[str, Any], items)
  data_list = items_dict.get("data")
  if not data_list or not isinstance(data_list, list) or len(data_list) == 0:
    return None
  first_item = cast(dict[str, Any], data_list[0])
  price_obj = first_item.get("price", {})
  if not isinstance(price_obj, dict):
    return None
  price_dict = cast(dict[str, Any], price_obj)
  price_id: str = str(price_dict.get("id", ""))
  return PRICE_TO_TIER.get(price_id)


def _user_id_from_subscription(subscription: dict[str, Any]) -> str | None:
  """Extract user_id stored as subscription metadata."""
  raw_metadata: Any = subscription.get("metadata")
  if not raw_metadata or not isinstance(raw_metadata, dict):
    return None
  metadata = cast(dict[str, Any], raw_metadata)
  user_id_val = metadata.get("user_id")
  return str(user_id_val) if user_id_val is not None else None


def _resolve_tier_from_pending_update(pending_update: dict[str, Any]) -> str | None:
  """Determine the tier name from a Stripe pending_update's subscription_items."""
  items = pending_update.get("subscription_items")
  if not items or not isinstance(items, list) or len(items) == 0:
    return None
  first_item = cast(dict[str, Any], items[0])
  price = first_item.get("price")
  if isinstance(price, dict):
    price_id = str(cast(dict[str, Any], price).get("id", ""))
  elif isinstance(price, str):
    price_id = price
  else:
    return None
  return PRICE_TO_TIER.get(price_id)


def _price_id_from_phase_item(item: dict[str, Any]) -> str:
  """Extract a price ID from a subscription schedule phase item.

  The ``price`` field may be an expanded object or a plain string ID.
  """
  price = item.get("price")
  if isinstance(price, dict):
    return str(cast(dict[str, Any], price).get("id", ""))
  if isinstance(price, str):
    return price
  return ""


def _resolve_scheduled_plan_change(
  schedule_id: str,
) -> tuple[str | None, int | None]:
  """Retrieve a subscription schedule and return (pending_tier, effective_ts).

  The billing portal schedules end-of-period downgrades as a second phase on
  a ``SubscriptionSchedule``.  If the schedule has more than one phase and the
  next phase maps to a known tier, we return that tier and its start timestamp.
  Returns ``(None, None)`` when there is no actionable pending change.
  """
  stripe.api_key = _get_stripe_api_key()
  try:
    schedule = stripe.SubscriptionSchedule.retrieve(schedule_id)
  except stripe.StripeError as exc:
    logger.warning(f"Failed to retrieve subscription schedule {schedule_id}: {exc}")
    return None, None

  raw_phases: Any = schedule.get("phases")
  if not raw_phases or not isinstance(raw_phases, list) or len(raw_phases) < 2:
    return None, None

  # The next phase is the scheduled change
  next_phase = cast(dict[str, Any], raw_phases[1])
  items = next_phase.get("items")
  if not items or not isinstance(items, list) or len(items) == 0:
    return None, None

  first_item = cast(dict[str, Any], items[0])
  price_id = _price_id_from_phase_item(first_item)
  tier = PRICE_TO_TIER.get(price_id)
  start_date: int | None = next_phase.get("start_date")
  return tier, start_date


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def _handle_checkout_completed(event: stripe.Event) -> None:
  """New subscription via Checkout – set tier, reset counters, sync Cognito."""
  session = event["data"]["object"]
  user_id: str | None = session.get("client_reference_id")
  if not user_id:
    logger.warning("checkout.session.completed missing client_reference_id; skipping")
    return

  stripe.api_key = _get_stripe_api_key()
  subscription_id = session.get("subscription")
  if not subscription_id:
    logger.warning("checkout.session.completed has no subscription; skipping")
    return

  sub = stripe.Subscription.retrieve(subscription_id)
  tier = _resolve_tier_from_subscription(sub) or "EXPLORER"
  customer_id = str(session.get("customer", ""))

  # Store user_id in subscription metadata for future webhook lookups
  stripe.Subscription.modify(subscription_id, metadata={"user_id": user_id})

  update_tier(user_id, tier, stripe_customer_id=customer_id)
  update_user_tier(user_id, tier)

  email, name = get_user_contact_info(user_id)
  _identify_user(user_id, email, name)
  if email:
    send_subscription_welcome(email, name, tier)

  ph_capture("subscription_started", distinct_id=user_id, properties={"tier": tier, "source": "server"})
  logger.info(f"Checkout completed: user={user_id} tier={tier}")


def _handle_invoice_paid(event: stripe.Event) -> None:
  """Renewal payment succeeded – restore paid tier (if downgraded), reset counters, extend period."""
  invoice = event["data"]["object"]
  subscription_id = invoice.get("subscription")
  if not subscription_id:
    return

  stripe.api_key = _get_stripe_api_key()
  sub = stripe.Subscription.retrieve(subscription_id)
  user_id = _user_id_from_subscription(sub)
  if not user_id:
    logger.warning(f"invoice.payment_succeeded: no user_id in subscription {subscription_id} metadata")
    return

  tier = _resolve_tier_from_subscription(sub) or "EXPLORER"

  update_tier(user_id, tier)
  update_user_tier(user_id, tier)
  reset_period(user_id)
  clear_pending_cancellation(user_id)

  email, name = get_user_contact_info(user_id)
  _identify_user(user_id, email, name)
  if email:
    send_subscription_renewed(email, name, tier)

  ph_capture("subscription_renewed", distinct_id=user_id, properties={"tier": tier, "source": "server"})
  logger.info(f"Invoice paid (renewal): user={user_id} tier={tier}")


def _handle_subscription_updated(event: stripe.Event) -> None:
  """Handle all subscription state transitions from Stripe.

  Covers: scheduled cancellation (cancel_at / cancel_at_period_end),
  cancellation reversal, plan change, past_due, unpaid, and paused.
  """
  subscription = event["data"]["object"]
  user_id = _user_id_from_subscription(subscription)
  if not user_id:
    logger.warning("customer.subscription.updated: no user_id in metadata; skipping")
    return

  sub_status = subscription.get("status", "")
  cancel_at_period_end = subscription.get("cancel_at_period_end", False)
  cancel_at = subscription.get("cancel_at")  # Unix timestamp or None

  email, name = get_user_contact_info(user_id)
  _identify_user(user_id, email, name)

  # Persist raw Stripe status for frontend visibility
  update_subscription_status(user_id, sub_status)

  # -- Active with scheduled cancellation (cancel_at OR cancel_at_period_end) --
  if sub_status == "active" and (cancel_at_period_end or cancel_at):
    if cancel_at:
      cancel_dt = datetime.fromtimestamp(cancel_at, tz=UTC)
    else:
      period_end_ts = subscription.get("current_period_end")
      cancel_dt = datetime.fromtimestamp(period_end_ts, tz=UTC) if period_end_ts else datetime.now(UTC)

    # Idempotency: Stripe often fires multiple subscription.updated events
    # in quick succession (e.g. cancel_at_period_end + schedule attachment).
    # Only send the cancellation email if we haven't already recorded this
    # exact cancellation date.
    usage = get_or_create_usage(user_id)
    already_pending = (
      usage.usage.pending_cancellation
      and usage.usage.cancellation_date
      and abs((usage.usage.cancellation_date - cancel_dt).total_seconds()) < 60
    )

    set_pending_cancellation(user_id, cancel_dt)

    if not already_pending and email:
      send_subscription_cancellation_scheduled(email, name, cancel_dt)

    if not already_pending:
      ph_capture(
        "subscription_cancellation_scheduled",
        distinct_id=user_id,
        properties={"cancel_at": cancel_dt.isoformat(), "source": "server"},
      )
    logger.info(
      "Subscription cancellation scheduled: user=%s cancel_at=%s email_sent=%s",
      user_id,
      cancel_dt.isoformat(),
      not already_pending,
    )
    return

  # -- Active with no cancellation scheduled (plan change or cancellation reversal) --
  if sub_status == "active":
    # Always clear pending cancellation – safe no-op when not pending.
    # Handles the case where a user un-cancels via the billing portal.
    clear_pending_cancellation(user_id)

    # Check for a scheduled plan change (e.g. downgrade at end of billing period).
    # The billing portal uses subscription schedules; the API may use pending_update.
    pending_tier: str | None = None
    effective_ts: int | None = None

    schedule_id = subscription.get("schedule")
    if schedule_id and isinstance(schedule_id, str):
      pending_tier, effective_ts = _resolve_scheduled_plan_change(schedule_id)

    if not pending_tier:
      raw_pending_update: Any = subscription.get("pending_update")
      if raw_pending_update and isinstance(raw_pending_update, dict):
        pending_update = cast(dict[str, Any], raw_pending_update)
        pending_tier = _resolve_tier_from_pending_update(pending_update)
        effective_ts = pending_update.get("expires_at")

    if pending_tier:
      effective_dt = datetime.fromtimestamp(effective_ts, tz=UTC) if effective_ts else datetime.now(UTC)
      set_pending_plan_change(user_id, pending_tier, effective_dt)

      if email:
        send_subscription_plan_change_scheduled(email, name, pending_tier, effective_dt)

      logger.info(f"Scheduled plan change: user={user_id} pending_tier={pending_tier} at={effective_dt.isoformat()}")
      # Don't fall through to update_tier – the subscription items haven't
      # changed yet (the change is scheduled for end-of-period).  Calling
      # update_tier here would wipe out the pending plan change we just saved
      # and unnecessarily reset usage counters.
      return

    # No schedule or pending_update – clear any previously stored pending plan change
    clear_pending_plan_change(user_id)

    new_tier = _resolve_tier_from_subscription(subscription)
    if new_tier:
      # Only apply the change if the tier actually differs from the user's
      # current tier.  Stripe fires subscription.updated for many reasons
      # (metadata edits, payment method changes, the Subscription.modify call
      # in _handle_checkout_completed, etc.) — none of which are plan changes.
      current_record = get_or_create_usage(user_id)
      current_tier = str(current_record.usage.tier) if current_record.usage.tier else "FREE"

      if new_tier != current_tier:
        customer_id = str(subscription.get("customer", ""))
        update_tier(user_id, new_tier, stripe_customer_id=customer_id)
        update_user_tier(user_id, new_tier)

        ph_capture(
          "subscription_plan_changed",
          distinct_id=user_id,
          properties={"new_tier": new_tier, "old_tier": current_tier, "source": "server"},
        )
        logger.info(f"Subscription plan changed: user={user_id} {current_tier} → {new_tier}")
      else:
        logger.info(
          "Subscription updated but tier unchanged (%s) for user=%s; skipping update_tier",
          current_tier,
          user_id,
        )
    return

  # -- Past due (payment failed, Stripe retrying) --
  if sub_status == "past_due":
    logger.warning(f"Subscription past due: user={user_id}")
    return

  # -- Unpaid (all retries exhausted) --
  if sub_status == "unpaid":
    update_tier(user_id, "FREE")
    update_user_tier(user_id, "FREE")
    logger.warning(f"Subscription unpaid, downgraded to FREE: user={user_id}")
    return

  # -- Paused --
  if sub_status == "paused":
    logger.info(f"Subscription paused: user={user_id}")
    return

  # -- Catch-all for unexpected statuses (incomplete, incomplete_expired, trialing, etc.) --
  logger.warning(f"Unhandled subscription status '{sub_status}' for user={user_id}")


def _handle_invoice_payment_failed(event: stripe.Event) -> None:
  """Payment attempt failed -- mark as past_due but keep current tier limits.

  Stripe's smart-retry logic will re-attempt the charge over the next few days.
  During this grace window the user keeps their paid-tier limits so they aren't
  penalized for a temporary card issue. The frontend can check subscription_status
  to show a payment-update prompt.

  The actual downgrade to FREE only happens when:
  - All retries are exhausted (subscription status becomes 'unpaid'), handled
    in _handle_subscription_updated, OR
  - The subscription is deleted, handled in _handle_subscription_deleted.
  """
  invoice = event["data"]["object"]
  subscription_id = invoice.get("subscription")
  if not subscription_id:
    return

  stripe.api_key = _get_stripe_api_key()
  sub = stripe.Subscription.retrieve(subscription_id)
  user_id = _user_id_from_subscription(sub)
  if not user_id:
    logger.warning(f"invoice.payment_failed: no user_id in subscription {subscription_id} metadata")
    return

  update_subscription_status(user_id, "past_due")

  email, name = get_user_contact_info(user_id)
  _identify_user(user_id, email, name)
  if email:
    send_payment_failed(email, name)

  ph_capture(
    "subscription_payment_failed",
    distinct_id=user_id,
    properties={"subscription_id": subscription_id, "source": "server"},
  )
  logger.warning(f"Invoice payment failed: user={user_id} sub={subscription_id} -- marked past_due (grace period)")


def _handle_subscription_deleted(event: stripe.Event) -> None:
  """Final downgrade – voluntary cancel-at-period-end or failed payment retries."""
  subscription = event["data"]["object"]
  user_id = _user_id_from_subscription(subscription)
  if not user_id:
    logger.warning("customer.subscription.deleted: no user_id in metadata; skipping")
    return

  update_tier(user_id, "FREE")
  update_user_tier(user_id, "FREE")

  email, name = get_user_contact_info(user_id)
  _identify_user(user_id, email, name)
  if email:
    send_subscription_ended(email, name)

  ph_capture("subscription_cancelled", distinct_id=user_id, properties={"source": "server"})
  logger.info(f"Subscription deleted → FREE: user={user_id}")


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

_EVENT_HANDLERS: dict[str, Callable[[stripe.Event], None]] = {
  "checkout.session.completed": _handle_checkout_completed,
  "invoice.payment_succeeded": _handle_invoice_paid,
  "invoice.payment_failed": _handle_invoice_payment_failed,
  "customer.subscription.updated": _handle_subscription_updated,
  "customer.subscription.deleted": _handle_subscription_deleted,
}


@router.post("/stripe", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request) -> dict[str, str]:
  """Receive and process Stripe webhook events.

  Authentication is performed via Stripe signature verification (not JWT).
  """
  payload = await request.body()
  sig_header = request.headers.get("stripe-signature", "")

  try:
    event = stripe.Webhook.construct_event(
      payload,
      sig_header,
      _get_webhook_secret(),
    )
  except stripe.SignatureVerificationError as e:
    logger.warning(f"Stripe signature verification failed: {e}")
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature") from e
  except ValueError as e:
    logger.warning(f"Invalid Stripe payload: {e}")
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload") from e

  event_type: str = event.get("type", "")
  logger.info(f"Stripe webhook received: {event_type}")

  # Idempotency: atomically claim the event before processing to prevent
  # duplicate handling from concurrent Stripe deliveries.
  event_id: str = event.get("id", "")
  idempotency_pk = RateLimitRecord.pk(f"WEBHOOK#{event_id}") if event_id else ""
  if event_id:
    try:
      claim_record = RateLimitRecord(
        PK=idempotency_pk,
        SK=RateLimitRecord.sk("PROCESSED"),
        expiration=int(time.time()) + 172800,  # 48 hours TTL
      )
      claim_record.save(condition=RateLimitRecord.PK.does_not_exist())
    except PutError:
      logger.info("Duplicate webhook event %s, skipping", event_id)
      return {"status": "already_processed"}

  handler_fn = _EVENT_HANDLERS.get(event_type)
  if handler_fn:
    try:
      handler_fn(event)
      metrics.add_metric(name="WebhookProcessed", unit=MetricUnit.Count, value=1)
    except Exception as exc:
      logger.exception(f"Error handling Stripe event {event_type}")
      raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Webhook handler failed",
      ) from exc
  else:
    logger.info(f"Unhandled Stripe event type: {event_type}")

  return {"status": "ok"}
