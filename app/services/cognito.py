"""Cognito helpers for updating user attributes from server-side events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aws_lambda_powertools import Logger

from app.core.config import settings

if TYPE_CHECKING:
  from mypy_boto3_cognito_idp.client import CognitoIdentityProviderClient

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

_cognito_client: CognitoIdentityProviderClient | None = None


def _get_cognito_client() -> CognitoIdentityProviderClient:
  global _cognito_client
  if _cognito_client is None:
    import boto3

    _cognito_client = boto3.client("cognito-idp", region_name=settings.AWS_REGION)
  return _cognito_client


def update_user_tier(user_id: str, tier: str) -> None:
  """Update the ``custom:tier`` attribute on a Cognito user.

  Called after Stripe webhook processing so that subsequent JWTs reflect
  the new subscription tier.
  """
  if not settings.COGNITO_USER_POOL_ID:
    logger.warning("COGNITO_USER_POOL_ID not set; skipping tier sync")
    return

  client = _get_cognito_client()
  try:
    client.admin_update_user_attributes(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=user_id,
      UserAttributes=[{"Name": "custom:tier", "Value": tier}],
    )
    logger.info(f"Synced Cognito custom:tier={tier} for user {user_id}")
  except Exception:
    logger.exception(f"Failed to update Cognito tier for user {user_id}")
    # Non-fatal: the DynamoDB record is the source of truth for quota
    # enforcement. The Cognito attribute is a convenience for JWT claims.
