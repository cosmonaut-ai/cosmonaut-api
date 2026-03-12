"""Centralized Stripe client setup and typed operation wrappers."""

from __future__ import annotations

import stripe

from app.core.config import settings
from app.core.observability import tracer
from app.services.secret_manager import get_secret_value


def _ensure_api_key() -> None:
  """Set the Stripe API key from SSM Parameter Store (idempotent)."""
  if not stripe.api_key:
    stripe.api_key = get_secret_value(settings.STRIPE_API_KEY_PARAM)


@tracer.capture_method
def create_checkout_session(
  *,
  price_id: str,
  user_id: str,
  customer_email: str,
  success_url: str,
  cancel_url: str,
) -> str:
  """Create a Stripe Checkout Session and return its URL."""
  _ensure_api_key()
  session = stripe.checkout.Session.create(
    mode="subscription",
    line_items=[{"price": price_id, "quantity": 1}],
    client_reference_id=user_id,
    customer_email=customer_email,
    success_url=success_url,
    cancel_url=cancel_url,
  )
  return session.url or ""


@tracer.capture_method
def create_billing_portal_session(*, customer_id: str) -> str:
  """Create a Stripe Billing Portal Session and return its URL."""
  _ensure_api_key()
  if settings.STRIPE_PORTAL_CONFIG_ID:
    session = stripe.billing_portal.Session.create(
      customer=customer_id,
      configuration=settings.STRIPE_PORTAL_CONFIG_ID,
    )
  else:
    session = stripe.billing_portal.Session.create(customer=customer_id)
  return session.url or ""
