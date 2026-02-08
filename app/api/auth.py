from typing import Literal

import stripe
from aws_lambda_powertools import Logger
from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel

from app.core.cloudfront import create_signed_cookies
from app.core.config import TIER_LIMITS, settings
from app.core.security import User, get_current_user
from app.services.secret_manager import get_secret_value
from app.services.usage import get_or_create_usage
from app.services.worlds import count_user_worlds

router = APIRouter(prefix="/auth", tags=["auth"])

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)


# ---------------------------------------------------------------------------
# Request / Response DTOs
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
  tier: Literal["EXPLORER", "COSMONAUT"]
  success_url: str
  cancel_url: str


class CheckoutResponse(BaseModel):
  checkout_url: str


class BillingPortalResponse(BaseModel):
  portal_url: str


class UsageResponse(BaseModel):
  tier: str
  nodes_used: int
  nodes_limit: int
  worlds_created: int
  worlds_limit: int
  worlds_stored: int
  worlds_stored_limit: int
  period_end: str | None
  pending_cancellation: bool
  cancellation_date: str | None
  subscription_status: str | None
  pending_tier: str | None
  pending_tier_date: str | None


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------


@router.post("/session")
async def create_session(response: Response, current_user: User = Depends(get_current_user)):
  try:
    private_key = get_secret_value(settings.CLOUDFRONT_PRIVATE_KEY_PARAM)
  except Exception:
    logger.error("Could not retrieve signing key", exc_info=True)
    raise HTTPException(status_code=500, detail="Could not retrieve signing key")

  resource_url = f"https://*{settings.COOKIE_DOMAIN}/*"
  cookies = create_signed_cookies(resource_url, settings.CLOUDFRONT_KEY_PAIR_ID, private_key)

  for key, value in cookies.items():
    response.set_cookie(
      key=key, value=value, httponly=True, secure=True, samesite="none", domain=settings.COOKIE_DOMAIN
    )

  return {"status": "session_created"}


# ---------------------------------------------------------------------------
# Usage & Subscription endpoints
# ---------------------------------------------------------------------------

_TIER_PRICE_MAP: dict[str, str] = {
  "EXPLORER": settings.STRIPE_PRICE_EXPLORER,
  "COSMONAUT": settings.STRIPE_PRICE_COSMONAUT,
}


@router.get("/usage", response_model=UsageResponse, summary="Get current usage and quota info")
async def get_usage(current_user: User = Depends(get_current_user)) -> UsageResponse:
  """Return the authenticated user's tier, usage counters, and limits."""
  usage = get_or_create_usage(current_user.id)
  tier = str(usage.tier) if usage.tier else "FREE"
  limits = TIER_LIMITS.get(tier, TIER_LIMITS["FREE"])

  return UsageResponse(
    tier=tier,
    nodes_used=int(usage.nodes_used or 0),
    nodes_limit=limits["nodes"],
    worlds_created=int(usage.worlds_created or 0),
    worlds_limit=limits["worlds"],
    worlds_stored=count_user_worlds(current_user.id),
    worlds_stored_limit=limits["saved_worlds"],
    period_end=usage.period_end.isoformat() if usage.period_end else None,
    pending_cancellation=bool(usage.pending_cancellation),
    cancellation_date=usage.cancellation_date.isoformat() if usage.cancellation_date else None,
    subscription_status=str(usage.subscription_status) if usage.subscription_status else None,
    pending_tier=str(usage.pending_tier) if usage.pending_tier else None,
    pending_tier_date=usage.pending_tier_date.isoformat() if usage.pending_tier_date else None,
  )


@router.post("/checkout", response_model=CheckoutResponse, summary="Create a Stripe Checkout session")
async def create_checkout(
  payload: CheckoutRequest = Body(...),
  current_user: User = Depends(get_current_user),
) -> CheckoutResponse:
  """Create a Stripe Checkout Session for a new subscription.

  The ``client_reference_id`` is set to the authenticated user's ID so that
  the webhook can associate the resulting subscription with the correct user.
  """
  price_id = _TIER_PRICE_MAP.get(payload.tier)
  if not price_id:
    raise HTTPException(status_code=400, detail=f"No Stripe price configured for tier {payload.tier}")

  stripe.api_key = get_secret_value(settings.STRIPE_API_KEY_PARAM)

  try:
    session = stripe.checkout.Session.create(
      mode="subscription",
      line_items=[{"price": price_id, "quantity": 1}],
      client_reference_id=current_user.id,
      customer_email=current_user.email if current_user.email else "",
      success_url=payload.success_url,
      cancel_url=payload.cancel_url,
    )
  except stripe.StripeError as e:
    logger.error(f"Stripe checkout session creation failed: {e}")
    raise HTTPException(status_code=502, detail="Failed to create checkout session") from e

  return CheckoutResponse(checkout_url=session.url or "")


@router.post("/billing-portal", response_model=BillingPortalResponse, summary="Create a Stripe Billing Portal session")
async def create_billing_portal(
  current_user: User = Depends(get_current_user),
) -> BillingPortalResponse:
  """Create a Stripe Billing Portal session so the user can manage their
  subscription (cancel, change plan, update payment method).
  """
  usage = get_or_create_usage(current_user.id)
  if not usage.stripe_customer_id:
    raise HTTPException(status_code=400, detail="No active subscription found")

  stripe.api_key = get_secret_value(settings.STRIPE_API_KEY_PARAM)

  try:
    customer_id = str(usage.stripe_customer_id)
    if settings.STRIPE_PORTAL_CONFIG_ID:
      session = stripe.billing_portal.Session.create(
        customer=customer_id,
        configuration=settings.STRIPE_PORTAL_CONFIG_ID,
      )
    else:
      session = stripe.billing_portal.Session.create(customer=customer_id)
  except stripe.StripeError as e:
    logger.error(f"Stripe billing portal session creation failed: {e}")
    raise HTTPException(status_code=502, detail="Failed to create billing portal session") from e

  return BillingPortalResponse(portal_url=session.url or "")
