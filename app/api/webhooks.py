"""Stripe webhook handler.

This router is registered *without* the ``get_current_user`` dependency
because Stripe calls it directly (no JWT).  Authentication is performed
via Stripe signature verification instead.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

import stripe
from aws_lambda_powertools import Logger
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import PRICE_TO_TIER, settings
from app.services.cognito import update_user_tier
from app.services.secret_manager import get_secret_value
from app.services.usage import (
  clear_pending_cancellation,
  clear_pending_plan_change,
  reset_period,
  set_pending_cancellation,
  set_pending_plan_change,
  update_subscription_status,
  update_tier,
)

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_stripe_api_key() -> str:
  return get_secret_value(settings.STRIPE_API_KEY_PARAM)


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

  logger.info(f"Checkout completed: user={user_id} tier={tier}")


def _handle_invoice_paid(event: stripe.Event) -> None:
  """Renewal payment succeeded – reset counters, extend period."""
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

  reset_period(user_id)
  clear_pending_cancellation(user_id)
  logger.info(f"Invoice paid (renewal): user={user_id}")


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

  # Persist raw Stripe status for frontend visibility
  update_subscription_status(user_id, sub_status)

  # -- Active with scheduled cancellation (cancel_at OR cancel_at_period_end) --
  if sub_status == "active" and (cancel_at_period_end or cancel_at):
    if cancel_at:
      cancel_dt = datetime.fromtimestamp(cancel_at, tz=timezone.utc)
    else:
      period_end_ts = subscription.get("current_period_end")
      cancel_dt = (
        datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if period_end_ts else datetime.now(timezone.utc)
      )
    set_pending_cancellation(user_id, cancel_dt)
    logger.info(f"Subscription cancellation scheduled: user={user_id} cancel_at={cancel_dt.isoformat()}")
    return

  # -- Active with no cancellation scheduled (plan change or cancellation reversal) --
  if sub_status == "active":
    # Always clear pending cancellation – safe no-op when not pending.
    # Handles the case where a user un-cancels via the billing portal.
    clear_pending_cancellation(user_id)

    # Check for a scheduled plan change (e.g. downgrade at end of billing period)
    raw_pending_update: Any = subscription.get("pending_update")
    if raw_pending_update and isinstance(raw_pending_update, dict):
      pending_update = cast(dict[str, Any], raw_pending_update)
      pending_tier = _resolve_tier_from_pending_update(pending_update)
      if pending_tier:
        expires_at = pending_update.get("expires_at")
        effective_dt = (
          datetime.fromtimestamp(expires_at, tz=timezone.utc) if expires_at else datetime.now(timezone.utc)
        )
        set_pending_plan_change(user_id, pending_tier, effective_dt)
        logger.info(f"Scheduled plan change: user={user_id} pending_tier={pending_tier} at={effective_dt.isoformat()}")
    else:
      # No pending_update – clear any previously stored pending plan change
      clear_pending_plan_change(user_id)

    new_tier = _resolve_tier_from_subscription(subscription)
    if new_tier:
      customer_id = str(subscription.get("customer", ""))
      update_tier(user_id, new_tier, stripe_customer_id=customer_id)
      update_user_tier(user_id, new_tier)
      logger.info(f"Subscription plan changed: user={user_id} new_tier={new_tier}")
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
  """Payment attempt failed – mark subscription as past_due for frontend visibility."""
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
  logger.warning(f"Invoice payment failed: user={user_id} sub={subscription_id}")


def _handle_subscription_deleted(event: stripe.Event) -> None:
  """Final downgrade – voluntary cancel-at-period-end or failed payment retries."""
  subscription = event["data"]["object"]
  user_id = _user_id_from_subscription(subscription)
  if not user_id:
    logger.warning("customer.subscription.deleted: no user_id in metadata; skipping")
    return

  update_tier(user_id, "FREE")
  update_user_tier(user_id, "FREE")
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

  handler = _EVENT_HANDLERS.get(event_type)
  if handler:
    try:
      handler(event)
    except Exception:
      logger.exception(f"Error handling Stripe event {event_type}")
      # Return 200 even on internal errors to prevent Stripe retries for
      # transient failures.  The error is logged for investigation.
  else:
    logger.info(f"Unhandled Stripe event type: {event_type}")

  return {"status": "ok"}
