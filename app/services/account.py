"""Account lifecycle service.

Handles permanent account deletion with full cascade across all data stores:
DynamoDB (worlds, nodes, usage), Pinecone (vectors), Cognito (identity),
and Stripe (subscriptions).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import stripe
from aws_lambda_powertools import Logger

import app.services.pinecone as pinecone_service
from app.core.config import settings
from app.models.entities.usage import UserUsage
from app.models.entities.world_meta import WorldMeta
from app.services.secret_manager import get_secret_value

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


def delete_account(user_id: str, cognito_username: str) -> None:
  """Permanently delete a user account and all associated data.

  Steps (order matters for safety):
  1. Cancel any active Stripe subscription
  2. Delete all user-owned worlds (nodes + vectors + metadata)
  3. Delete the user's usage record
  4. Delete the Cognito user

  Args:
    user_id: The Cognito ``sub`` (UUID) of the user.
    cognito_username: The Cognito username for admin API calls.
  """
  logger.info("Starting account deletion for user %s", user_id)

  # 1. Cancel Stripe subscription (if any)
  _cancel_stripe_subscription(user_id)

  # 2. Delete all worlds owned by this user
  _delete_all_user_worlds(user_id)

  # 3. Delete the usage record
  _delete_usage_record(user_id)

  # 4. Delete the Cognito user
  _delete_cognito_user(cognito_username)

  logger.info("Account deletion complete for user %s", user_id)


def _cancel_stripe_subscription(user_id: str) -> None:
  """Cancel any active Stripe subscription for the user.

  Queries all non-terminal subscription statuses (active, past_due,
  trialing, unpaid) to ensure nothing is missed.
  """
  try:
    usage = UserUsage.get(UserUsage.pk(user_id), UserUsage.sk())
    if not usage.stripe_customer_id:
      return

    stripe.api_key = get_secret_value(settings.STRIPE_API_KEY_PARAM)
    customer_id = str(usage.stripe_customer_id)

    for status in ("active", "past_due", "trialing", "unpaid"):
      subscriptions = stripe.Subscription.list(customer=customer_id, status=status, limit=10)
      for sub in subscriptions.data:
        stripe.Subscription.cancel(sub.id)
        logger.info("Cancelled Stripe subscription %s (was %s) for user %s", sub.id, status, user_id)

  except UserUsage.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
    logger.info("No usage record found for Stripe cancellation (user %s)", user_id)
  except Exception:
    logger.exception("Failed to cancel Stripe subscriptions for user %s", user_id)
    # Non-fatal: proceed with deletion even if Stripe cancel fails.
    # Stripe will auto-cancel when the customer is deleted or payments fail.


def _delete_all_user_worlds(user_id: str) -> None:
  """Delete every world owned by the user, including nodes and vectors."""
  gsi1_pk = WorldMeta.gsi1_pk(user_id)
  worlds: list[WorldMeta] = list(WorldMeta.GSI1.query(hash_key=gsi1_pk))  # type: ignore[reportUnknownMemberType]

  logger.info("Deleting %d worlds for user %s", len(worlds), user_id)

  for world in worlds:
    world_id = str(world.id)
    try:
      # Delete all items under this world's partition (metadata + nodes)
      pk = WorldMeta.pk(world_id)
      items = list(WorldMeta.query(pk))
      if items:
        with WorldMeta.batch_write() as batch:
          for item in items:
            batch.delete(item)

      # Delete Pinecone vectors for this world
      try:
        pinecone_service.delete_records(filter={"world_id": world_id})
      except Exception:
        logger.exception("Failed to delete Pinecone records for world %s", world_id)

    except Exception:
      logger.exception("Failed to delete world %s for user %s", world_id, user_id)


def _delete_usage_record(user_id: str) -> None:
  """Delete the UserUsage DynamoDB record."""
  try:
    usage = UserUsage.get(UserUsage.pk(user_id), UserUsage.sk())
    usage.delete()
    logger.info("Deleted usage record for user %s", user_id)
  except UserUsage.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
    logger.info("No usage record to delete for user %s", user_id)
  except Exception:
    logger.exception("Failed to delete usage record for user %s", user_id)


def _delete_cognito_user(cognito_username: str) -> None:
  """Delete the user from the Cognito User Pool."""
  if not settings.COGNITO_USER_POOL_ID:
    logger.warning("COGNITO_USER_POOL_ID not set; skipping Cognito user deletion")
    return

  try:
    client = _get_cognito_client()
    client.admin_delete_user(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=cognito_username,
    )
    logger.info("Deleted Cognito user %s", cognito_username)
  except Exception:
    logger.exception("Failed to delete Cognito user %s", cognito_username)
    raise
