import time
from typing import Literal
from urllib.parse import urlparse

import stripe
from botocore.exceptions import ClientError
from fastapi import APIRouter, Body, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator

from app.core.cloudfront import create_signed_cookies
from app.core.config import get_tier_limits, settings
from app.core.errors import AppError, BadRequestError, ExternalServiceError, RateLimitError
from app.core.observability import MetricUnit, logger, metrics
from app.core.posthog import posthog_client
from app.core.security import User, get_current_user
from app.models.entities.rate_limit import RateLimitRecord
from app.services.account import delete_account
from app.services.cognito import update_user_username
from app.services.email import send_feedback_email
from app.services.newsletter import subscribe, unsubscribe
from app.services.secret_manager import get_secret_value
from app.services.stripe_client import create_billing_portal_session, create_checkout_session
from app.services.usage import get_or_create_usage
from app.services.username import check_availability, reserve_username, validate_username

router = APIRouter(prefix="/auth", tags=["auth"])


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
  username: str | None
  display_name: str
  is_onboarded: bool
  tier: str
  nodes_used: int
  nodes_limit: int
  worlds_created: int
  worlds_limit: int
  audio_narrations_used: int
  audio_narrations_limit: int
  period_end: str | None
  pending_cancellation: bool
  cancellation_date: str | None
  subscription_status: str | None
  pending_tier: str | None
  pending_tier_date: str | None
  newsletter_opted_in: bool


class UsernameCheckResponse(BaseModel):
  available: bool
  username: str


class UsernameSetRequest(BaseModel):
  username: str = Field(..., min_length=3, max_length=30)


class UsernameSetResponse(BaseModel):
  username: str


class FeedbackRequest(BaseModel):
  category: Literal["bug", "feature", "feedback", "other"]
  message: str = Field(..., min_length=10, max_length=10000)


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
    raise AppError("Could not retrieve signing key") from None

  resource_url = f"https://*{settings.COOKIE_DOMAIN}/*"
  cookies = create_signed_cookies(resource_url, settings.CLOUDFRONT_KEY_PAIR_ID, private_key)

  for key, value in cookies.items():
    response.set_cookie(
      key=key, value=value, httponly=True, secure=True, samesite="none", domain=settings.COOKIE_DOMAIN
    )

  metrics.add_metric(name="AuthSessionCreated", unit=MetricUnit.Count, value=1)
  posthog_client.capture("user_session_created", distinct_id=current_user.id, properties={"tier": current_user.tier})
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
  record = get_or_create_usage(current_user.id, email=current_user.email)
  u = record.usage
  tier = str(u.tier) if u.tier else "FREE"
  limits = get_tier_limits(tier)

  display = str(record.username) if record.username else (current_user.email or current_user.id[:8])

  # Lazy sync: if DynamoDB has a username but the JWT doesn't, backfill Cognito.
  # This is a safety net for users missed by the one-time backfill script.
  if record.username and not current_user.app_username:
    update_user_username(current_user.id, str(record.username))

  return UsageResponse(
    username=str(record.username) if record.username else None,
    display_name=display,
    is_onboarded=bool(record.is_onboarded),
    tier=tier,
    nodes_used=int(u.nodes_used or 0),
    nodes_limit=limits["nodes"],
    worlds_created=int(u.worlds_created or 0),
    worlds_limit=limits["worlds"],
    audio_narrations_used=int(u.audio_narrations_used or 0),
    audio_narrations_limit=limits["audio_limit"],
    period_end=u.period_end.isoformat() if u.period_end else None,
    pending_cancellation=bool(u.pending_cancellation),
    cancellation_date=u.cancellation_date.isoformat() if u.cancellation_date else None,
    subscription_status=str(u.subscription_status) if u.subscription_status else None,
    pending_tier=str(u.pending_tier) if u.pending_tier else None,
    pending_tier_date=u.pending_tier_date.isoformat() if u.pending_tier_date else None,
    newsletter_opted_in=bool(record.newsletter_opted_in),
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
    raise BadRequestError(f"No Stripe price configured for tier {payload.tier}")

  try:
    checkout_url = create_checkout_session(
      price_id=price_id,
      user_id=current_user.id,
      customer_email=current_user.email or "",
      success_url=payload.success_url,
      cancel_url=payload.cancel_url,
    )
  except stripe.StripeError as e:
    logger.error(f"Stripe checkout session creation failed: {e}")
    raise ExternalServiceError("Failed to create checkout session") from e

  posthog_client.capture("checkout_initiated", distinct_id=current_user.id, properties={"tier": payload.tier})
  return CheckoutResponse(checkout_url=checkout_url)


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
    await delete_account(user_id=current_user.id, cognito_username=current_user.username, email=current_user.email)
    posthog_client.capture("account_deleted", distinct_id=current_user.id)
    return {"status": "deleted"}
  except ClientError:
    logger.exception("Account deletion failed for user %s", current_user.id)
    raise AppError("Account deletion failed. Please contact support.") from None


@router.post("/billing-portal", response_model=BillingPortalResponse, summary="Create a Stripe Billing Portal session")
async def create_billing_portal(
  current_user: User = Depends(get_current_user),
) -> BillingPortalResponse:
  """Create a Stripe Billing Portal session so the user can manage their
  subscription (cancel, change plan, update payment method).
  """
  record = get_or_create_usage(current_user.id)
  if not record.usage.stripe_customer_id:
    raise BadRequestError("No active subscription found")

  try:
    portal_url = create_billing_portal_session(customer_id=str(record.usage.stripe_customer_id))
  except stripe.StripeError as e:
    logger.error(f"Stripe billing portal session creation failed: {e}")
    raise ExternalServiceError("Failed to create billing portal session") from e

  return BillingPortalResponse(portal_url=portal_url)


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
      raise RateLimitError("Please wait before submitting more feedback.", retry_after=retry_after)
  except RateLimitRecord.DoesNotExist:
    pass

  record = RateLimitRecord(
    PK=rate_pk,
    SK=rate_sk,
    expiration=now + FEEDBACK_COOLDOWN_SECONDS,
  )
  record.save()

  record = get_or_create_usage(current_user.id)
  tier = str(record.usage.tier) if record.usage.tier else "FREE"

  send_feedback_email(
    user_email=current_user.email or "unknown",
    user_id=current_user.id,
    tier=tier,
    category=payload.category,
    message=payload.message,
  )

  posthog_client.capture(
    "feedback_submitted",
    distinct_id=current_user.id,
    properties={"category": payload.category, "message_length": len(payload.message), "tier": tier},
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
  record = get_or_create_usage(current_user.id)
  record.newsletter_opted_in = payload.opted_in
  record.save()

  email = current_user.email
  if email:
    if payload.opted_in:
      await subscribe(email)
    else:
      await unsubscribe(email)

  posthog_client.capture("newsletter_preference_updated", distinct_id=current_user.id, properties={"opted_in": payload.opted_in})
  return {"status": "subscribed" if payload.opted_in else "unsubscribed"}


# ---------------------------------------------------------------------------
# Username endpoints
# ---------------------------------------------------------------------------


@router.get("/username/check", response_model=UsernameCheckResponse, summary="Check username availability")
async def check_username(
  username: str = Query(..., min_length=3, max_length=30),
  current_user: User = Depends(get_current_user),
) -> UsernameCheckResponse:
  """Return whether *username* is available (case-insensitive).

  Validates format first; invalid usernames are never 'available'.
  """
  try:
    cleaned = validate_username(username)
  except BadRequestError:
    return UsernameCheckResponse(available=False, username=username)

  return UsernameCheckResponse(
    available=check_availability(cleaned),
    username=cleaned,
  )


@router.post("/username", response_model=UsernameSetResponse, summary="Set username (one-time)")
async def set_username(
  payload: UsernameSetRequest = Body(...),
  current_user: User = Depends(get_current_user),
) -> UsernameSetResponse:
  """Atomically reserve a username for the authenticated user.

  Usernames are permanent and cannot be changed once set.
  """
  stored = reserve_username(current_user.id, payload.username)
  posthog_client.capture("username_set", distinct_id=current_user.id)
  return UsernameSetResponse(username=stored)


# ---------------------------------------------------------------------------
# User lookup
# ---------------------------------------------------------------------------


class UserInfo(BaseModel):
  id: str
  display_name: str


@router.get("/users/batch", response_model=list[UserInfo], summary="Batch-resolve user display names")
async def batch_lookup_users(
  ids: str = Query(..., description="Comma-separated user IDs (max 50)"),
  _current_user: User = Depends(get_current_user),
) -> list[UserInfo]:
  """Return display names for a list of user IDs.

  Used by the share modal to show human-readable names for the
  shared_with allowlist instead of raw Cognito sub UUIDs.
  """
  from app.models.entities.user import UserRecord

  user_ids = [uid.strip() for uid in ids.split(",") if uid.strip()][:50]
  results: list[UserInfo] = []
  for uid in user_ids:
    try:
      record = UserRecord.get(UserRecord.pk(uid), UserRecord.sk())
      display = str(record.username) if record.username else uid[:8]
      results.append(UserInfo(id=uid, display_name=display))
    except UserRecord.DoesNotExist:
      results.append(UserInfo(id=uid, display_name=uid[:8]))
  return results
