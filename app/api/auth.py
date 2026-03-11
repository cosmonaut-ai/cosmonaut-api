import time
from typing import Literal
from urllib.parse import urlparse

import stripe
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError
from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator

from app.core.cloudfront import create_signed_cookies
from app.core.config import get_tier_limits, settings
from app.core.security import User, get_current_user
from app.models.entities.rate_limit import RateLimitRecord
from app.services.account import delete_account
from app.services.email import send_feedback_email
from app.services.newsletter import subscribe, unsubscribe
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

  @field_validator("success_url", "cancel_url")
  @classmethod
  def validate_url_domain(cls, v: str) -> str:
    parsed = urlparse(v)
    if parsed.scheme != "https" or parsed.netloc != settings.FRONTEND_DOMAIN:
      raise ValueError(f"URL must belong to {settings.FRONTEND_DOMAIN}")
    return v


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
  audio_narrations_used: int
  audio_narrations_limit: int
  period_end: str | None
  pending_cancellation: bool
  cancellation_date: str | None
  subscription_status: str | None
  pending_tier: str | None
  pending_tier_date: str | None
  newsletter_opted_in: bool


class FeedbackRequest(BaseModel):
  category: Literal["bug", "feature", "feedback", "other"]
  message: str


class NewsletterRequest(BaseModel):
  opted_in: bool


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------


@router.post("/session")
async def create_session(response: Response, current_user: User = Depends(get_current_user)):
  try:
    private_key = get_secret_value(settings.CLOUDFRONT_PRIVATE_KEY_PARAM)
  except (ValueError, OSError, ClientError):
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
  usage = get_or_create_usage(current_user.id, email=current_user.email)
  tier = str(usage.tier) if usage.tier else "FREE"
  limits = get_tier_limits(tier)

  return UsageResponse(
    tier=tier,
    nodes_used=int(usage.nodes_used or 0),
    nodes_limit=limits["nodes"],
    worlds_created=int(usage.worlds_created or 0),
    worlds_limit=limits["worlds"],
    worlds_stored=count_user_worlds(current_user.id),
    worlds_stored_limit=limits["saved_worlds"],
    audio_narrations_used=int(usage.audio_narrations_used or 0),
    audio_narrations_limit=limits["audio_limit"],
    period_end=usage.period_end.isoformat() if usage.period_end else None,
    pending_cancellation=bool(usage.pending_cancellation),
    cancellation_date=usage.cancellation_date.isoformat() if usage.cancellation_date else None,
    subscription_status=str(usage.subscription_status) if usage.subscription_status else None,
    pending_tier=str(usage.pending_tier) if usage.pending_tier else None,
    pending_tier_date=usage.pending_tier_date.isoformat() if usage.pending_tier_date else None,
    newsletter_opted_in=bool(usage.newsletter_opted_in),
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


@router.delete("/account", status_code=200, summary="Permanently delete user account")
async def delete_user_account(current_user: User = Depends(get_current_user)) -> dict[str, str]:
  """Permanently delete the authenticated user's account and all associated data.

  This action is irreversible. It will:
  - Cancel any active Stripe subscription
  - Delete all owned worlds, story nodes, and vector embeddings
  - Delete usage records
  - Delete the Cognito user identity
  """
  try:
    delete_account(user_id=current_user.id, cognito_username=current_user.username, email=current_user.email)
    return {"status": "deleted"}
  except ClientError:
    logger.exception("Account deletion failed for user %s", current_user.id)
    raise HTTPException(status_code=500, detail="Account deletion failed. Please contact support.")


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


# ---------------------------------------------------------------------------
# Feedback endpoint
# ---------------------------------------------------------------------------

FEEDBACK_COOLDOWN_SECONDS = 300  # 5 minutes between submissions


@router.post("/feedback", status_code=200, summary="Submit user feedback")
async def submit_feedback(
  payload: FeedbackRequest = Body(...),
  current_user: User = Depends(get_current_user),
) -> dict[str, str]:
  """Submit feedback via email.  Rate-limited to one submission per 5 minutes."""
  rate_pk = f"RATE#FEEDBACK#{current_user.id}"
  rate_sk = "LIMIT"
  now = int(time.time())

  try:
    existing = RateLimitRecord.get(rate_pk, rate_sk)
    if existing.expiration and int(existing.expiration) > now:
      retry_after = int(existing.expiration) - now
      raise HTTPException(
        status_code=429,
        detail="Please wait before submitting more feedback.",
        headers={"Retry-After": str(retry_after)},
      )
  except RateLimitRecord.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
    pass

  record = RateLimitRecord(
    PK=rate_pk,
    SK=rate_sk,
    expiration=now + FEEDBACK_COOLDOWN_SECONDS,
  )
  record.save()

  usage = get_or_create_usage(current_user.id)
  tier = str(usage.tier) if usage.tier else "FREE"

  send_feedback_email(
    user_email=current_user.email or "unknown",
    user_id=current_user.id,
    tier=tier,
    category=payload.category,
    message=payload.message,
  )

  return {"status": "submitted"}


# ---------------------------------------------------------------------------
# Newsletter endpoint
# ---------------------------------------------------------------------------


@router.post("/newsletter", status_code=200, summary="Update newsletter preference")
async def update_newsletter(
  payload: NewsletterRequest = Body(...),
  current_user: User = Depends(get_current_user),
) -> dict[str, str]:
  """Subscribe or unsubscribe the user from the product newsletter."""
  usage = get_or_create_usage(current_user.id)
  usage.newsletter_opted_in = payload.opted_in
  usage.save()

  email = current_user.email
  if email:
    if payload.opted_in:
      subscribe(email)
    else:
      unsubscribe(email)

  return {"status": "subscribed" if payload.opted_in else "unsubscribed"}
