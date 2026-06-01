"""Cognito helpers for updating user attributes and managing user state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.observability import logger, tracer

if TYPE_CHECKING:
  from mypy_boto3_cognito_idp.client import CognitoIdentityProviderClient

_cognito_client: CognitoIdentityProviderClient | None = None


def get_cognito_client() -> CognitoIdentityProviderClient:
  global _cognito_client
  if _cognito_client is None:
    import boto3

    _cognito_client = boto3.client("cognito-idp", region_name=settings.AWS_REGION)
  return _cognito_client


def _resolve_cognito_username(client: CognitoIdentityProviderClient, user_id: str) -> str | None:
  """Resolve the Cognito username for a given ``sub`` (user ID).

  The ``admin_update_user_attributes`` API requires the actual Cognito
  username, which may differ from the ``sub`` UUID (e.g. when the user
  signed up with an email/alias or via a federated identity provider).
  """
  try:
    response = client.list_users(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Filter=f'sub = "{user_id}"',
      Limit=1,
    )
    users = response.get("Users", [])
    if users:
      return users[0].get("Username")
  except Exception:
    logger.exception(f"Failed to look up Cognito username for sub {user_id}")
  return None


@tracer.capture_method
def get_user_contact_info(user_id: str) -> tuple[str, str]:
  """Return ``(email, name)`` for a Cognito user identified by ``sub``.

  Uses the same ``list_users`` call that ``_resolve_cognito_username`` makes,
  so it costs a single API call.  Returns ``("", "")`` on failure — callers
  should treat a missing email as a reason to skip the email send.
  """
  if not settings.COGNITO_USER_POOL_ID:
    logger.warning("COGNITO_USER_POOL_ID not set; cannot look up user contact info")
    return "", ""

  client = get_cognito_client()
  try:
    response = client.list_users(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Filter=f'sub = "{user_id}"',
      Limit=1,
    )
    users = response.get("Users", [])
    if not users:
      logger.warning(f"No Cognito user found for sub {user_id}")
      return "", ""

    attrs = {a["Name"]: a.get("Value", "") for a in users[0].get("Attributes", [])}
    email = attrs.get("email", "")
    name = attrs.get("given_name") or attrs.get("name") or ""
    return email, name
  except Exception:
    logger.exception(f"Failed to look up contact info for sub {user_id}")
    return "", ""


@tracer.capture_method
def update_user_tier(user_id: str, tier: str) -> None:
  """Update the ``custom:tier`` attribute on a Cognito user.

  Called after Stripe webhook processing so that subsequent JWTs reflect
  the new subscription tier.
  """
  if not settings.COGNITO_USER_POOL_ID:
    logger.warning("COGNITO_USER_POOL_ID not set; skipping tier sync")
    return

  client = get_cognito_client()

  # The user_id is the Cognito ``sub`` UUID.  admin_update_user_attributes
  # requires the actual Cognito username, so look it up first.
  username = _resolve_cognito_username(client, user_id)
  if not username:
    logger.warning(f"Could not resolve Cognito username for sub {user_id}; skipping tier sync")
    return

  try:
    client.admin_update_user_attributes(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=username,
      UserAttributes=[{"Name": "custom:tier", "Value": tier}],
    )
    logger.info(f"Synced Cognito custom:tier={tier} for user {user_id}")
  except Exception:
    logger.exception(f"Failed to update Cognito tier for user {user_id}")
    # Non-fatal: the DynamoDB record is the source of truth for quota
    # enforcement. The Cognito attribute is a convenience for JWT claims.


@tracer.capture_method
def update_user_username(user_id: str, app_username: str) -> None:
  """Update the ``custom:username`` attribute on a Cognito user.

  Called after username reservation so that subsequent JWTs carry the
  app-level handle directly in the ID token claims.
  """
  if not settings.COGNITO_USER_POOL_ID:
    logger.warning("COGNITO_USER_POOL_ID not set; skipping username sync")
    return

  client = get_cognito_client()

  cognito_username = _resolve_cognito_username(client, user_id)
  if not cognito_username:
    logger.warning(f"Could not resolve Cognito username for sub {user_id}; skipping username sync")
    return

  try:
    client.admin_update_user_attributes(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=cognito_username,
      UserAttributes=[{"Name": "custom:username", "Value": app_username}],
    )
    logger.info(f"Synced Cognito custom:username={app_username} for user {user_id}")
  except Exception:
    logger.exception(f"Failed to update Cognito username for user {user_id}")
    # Non-fatal: DynamoDB is the source of truth. The Cognito attribute
    # is a convenience for JWT claims; lazy sync will catch failures.


@tracer.capture_method
def ban_user(user_id: str) -> bool:
  """Disable a Cognito user and invalidate all active sessions.

  Prevents the user from authenticating and immediately revokes any
  existing tokens via a global sign-out.

  Returns True if both the disable and the global sign-out succeeded,
  False if the sign-out step failed (the account is still disabled).
  """
  if not settings.COGNITO_USER_POOL_ID:
    logger.warning("COGNITO_USER_POOL_ID not set; skipping ban")
    return False

  client = get_cognito_client()
  username = _resolve_cognito_username(client, user_id)
  if not username:
    raise ValueError(f"Could not resolve Cognito username for sub {user_id}")

  try:
    client.admin_disable_user(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=username,
    )
  except Exception:
    logger.exception("Failed to disable Cognito user %s (sub %s)", username, user_id)
    raise RuntimeError(f"Failed to disable Cognito user {user_id}") from None
  logger.info("Disabled Cognito user %s (sub %s)", username, user_id)

  try:
    client.admin_user_global_sign_out(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=username,
    )
    logger.info("Global sign-out for user %s", username)
    return True
  except Exception:
    logger.exception("Global sign-out failed for user %s; existing tokens may remain valid", username)
    return False


@tracer.capture_method
def unban_user(user_id: str) -> None:
  """Re-enable a previously disabled Cognito user."""
  if not settings.COGNITO_USER_POOL_ID:
    logger.warning("COGNITO_USER_POOL_ID not set; skipping unban")
    return

  client = get_cognito_client()
  username = _resolve_cognito_username(client, user_id)
  if not username:
    raise ValueError(f"Could not resolve Cognito username for sub {user_id}")

  try:
    client.admin_enable_user(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=username,
    )
  except Exception:
    logger.exception("Failed to re-enable Cognito user %s (sub %s)", username, user_id)
    raise RuntimeError(f"Failed to re-enable Cognito user {user_id}") from None
  logger.info("Re-enabled Cognito user %s (sub %s)", username, user_id)
