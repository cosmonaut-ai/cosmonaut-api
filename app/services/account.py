"""Account lifecycle service.

Handles permanent account deletion with full cascade across all data stores:
DynamoDB (worlds, nodes, usage), Pinecone (vectors), Cognito (identity),
and Stripe (subscriptions).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import stripe

import app.services.pinecone as pinecone_service
from app.core.config import settings
from app.core.observability import logger
from app.models.entities.usage import UsageTombstone, UserUsage
from app.models.entities.world_meta import WorldMeta
from app.services.newsletter import unsubscribe as newsletter_unsubscribe
from app.services.s3 import delete_objects_by_prefix
from app.services.secret_manager import get_secret_value
from app.services.user_progress import delete_all_user_progress

if TYPE_CHECKING:
  from mypy_boto3_cognito_idp.client import CognitoIdentityProviderClient

_cognito_client: CognitoIdentityProviderClient | None = None


def _get_cognito_client() -> CognitoIdentityProviderClient:
  global _cognito_client
  if _cognito_client is None:
    import boto3

    _cognito_client = boto3.client("cognito-idp", region_name=settings.AWS_REGION)
  return _cognito_client


async def delete_account(user_id: str, cognito_username: str, email: str | None = None) -> None:
  """Permanently delete a user account and all associated data.

  Steps (order matters for safety):
  1. Cancel any active Stripe subscription and delete the customer
  2. Unsubscribe from newsletter
  3. Delete all user-owned worlds (nodes + vectors + S3 objects + metadata)
  4. Delete all user progress records
  5. Tombstone the usage record (preserves quota for re-registration abuse prevention)
  6. Delete the Cognito user

  Args:
    user_id: The Cognito ``sub`` (UUID) of the user.
    cognito_username: The Cognito username for admin API calls.
    email: The user's email address (for newsletter unsubscribe and tombstone).
  """
  logger.info("Starting account deletion for user %s", user_id)

  # 1. Cancel Stripe subscription (if any)
  _cancel_stripe_subscription(user_id)

  # 2. Unsubscribe from newsletter
  if email:
    try:
      await newsletter_unsubscribe(email)
    except Exception:
      logger.exception("Failed to unsubscribe newsletter for user %s", user_id)

  # 3. Delete all worlds owned by this user
  _delete_all_user_worlds(user_id)

  # 4. Delete all progress records
  try:
    delete_all_user_progress(user_id)
  except Exception:
    logger.exception("Failed to delete progress records for user %s", user_id)

  # 5. Tombstone the usage record (preserves quota for re-registration abuse prevention)
  if email:
    _tombstone_usage_record(user_id, email)
  else:
    _delete_usage_record(user_id)

  # 6. Delete the Cognito user
  _delete_cognito_user(cognito_username)

  logger.info("Account deletion complete for user %s", user_id)


def _cancel_stripe_subscription(user_id: str) -> None:
  """Cancel any active Stripe subscription and delete the customer record."""
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

    stripe.Customer.delete(customer_id)
    logger.info("Deleted Stripe customer %s for user %s", customer_id, user_id)

  except UserUsage.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
    logger.info("No usage record found for Stripe cancellation (user %s)", user_id)
  except Exception:
    logger.exception("Failed to clean up Stripe for user %s", user_id)


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

      # Delete S3 objects (images + audio) for this world
      try:
        count = delete_objects_by_prefix(f"worlds/{world_id}/")
        count += delete_objects_by_prefix(f"audio/{world_id}/")
        if count:
          logger.info("Deleted %d S3 objects for world %s", count, world_id)
      except Exception:
        logger.exception("Failed to delete S3 objects for world %s", world_id)

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


_TOMBSTONE_TTL_DAYS = 365


def _tombstone_usage_record(user_id: str, email: str) -> None:
  """Replace the usage record with a tombstone for re-registration abuse prevention."""
  try:
    usage = UserUsage.get(UserUsage.pk(user_id), UserUsage.sk())
  except UserUsage.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
    return

  email_hash = hashlib.sha256(email.lower().strip().encode()).hexdigest()
  now = datetime.now(UTC)
  ttl_epoch = int((now + timedelta(days=_TOMBSTONE_TTL_DAYS)).timestamp())

  tombstone = UsageTombstone(
    PK=UsageTombstone.pk(email_hash),
    SK=UsageTombstone.sk(),
    email_hash=email_hash,
    worlds_created=int(usage.worlds_created or 0),
    nodes_used=int(usage.nodes_used or 0),
    audio_narrations_used=int(usage.audio_narrations_used or 0),
    period_end=usage.period_end,
    deleted_at=now,
    expiration=ttl_epoch,
  )
  tombstone.save()

  usage.delete()
  logger.info("Tombstoned usage for user %s (1-year TTL, period_end=%s)", user_id, usage.period_end)


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
