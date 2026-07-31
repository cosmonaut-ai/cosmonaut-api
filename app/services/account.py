"""Account lifecycle service.

Handles permanent account deletion with full cascade across all data stores:
DynamoDB (worlds, nodes, usage, username reservation), Pinecone (vectors),
Cognito (identity), and Stripe (subscriptions).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import stripe

from app.core.config import settings
from app.core.observability import logger
from app.models.entities.session_membership import SessionMembership
from app.models.entities.user import UsageTombstone, UsernameReservation, UserRecord
from app.models.entities.world_meta import WorldMeta
from app.services.cognito import get_cognito_client
from app.services.newsletter import unsubscribe as newsletter_unsubscribe
from app.services.secret_manager import get_secret_value
from app.services.sessions import delete_session, delete_sessions_for_world, delete_user_memberships
from app.services.worlds import hard_delete_orphaned_world


async def delete_account(user_id: str, cognito_username: str, email: str | None = None) -> None:
  """Permanently delete a user account and all associated data.

  Steps (order matters for safety):
  1. Cancel any active Stripe subscription and delete the customer
  2. Unsubscribe from newsletter
  3. Delete all user-owned worlds (nodes + vectors + S3 objects + metadata + sessions)
  4. Delete all remaining session data (sessions for non-owned worlds + memberships)
  5. Release the username reservation so it can be claimed again
  6. Tombstone the usage record (preserves quota for re-registration abuse prevention)
  7. Delete the Cognito user

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

  # 4. Delete all session data for this user (WorldSession + NodeSession partitions,
  # then the SessionMembership records). For owned worlds, step 3 already deleted
  # sessions via delete_sessions_for_world; delete_session handles that no-op gracefully.
  try:
    memberships = list(
      SessionMembership.query(
        SessionMembership.pk(user_id),
        SessionMembership.SK.startswith("SMEMBER#"),
      )
    )
    for membership in memberships:
      try:
        delete_session(str(membership.session_id))
      except Exception:
        logger.warning(
          "Failed to delete session %s for user %s (non-fatal)",
          membership.session_id,
          user_id,
          exc_info=True,
        )
    delete_user_memberships(user_id)
  except Exception:
    logger.exception("Failed to delete session data for user %s", user_id)

  # 5. Release the username reservation
  _delete_username_reservation(user_id)

  # 6. Tombstone the usage record
  if email:
    _tombstone_usage_record(user_id, email)
  else:
    _delete_usage_record(user_id)

  # 7. Delete the Cognito user
  _delete_cognito_user(cognito_username)

  logger.info("Account deletion complete for user %s", user_id)


def _cancel_stripe_subscription(user_id: str) -> None:
  """Cancel any active Stripe subscription and delete the customer record."""
  try:
    record = UserRecord.get(UserRecord.pk(user_id), UserRecord.sk())
    if not record.usage.stripe_customer_id:
      return

    stripe.api_key = get_secret_value(settings.STRIPE_API_KEY_PARAM)
    customer_id = str(record.usage.stripe_customer_id)

    for status in ("active", "past_due", "trialing", "unpaid"):
      subscriptions = stripe.Subscription.list(customer=customer_id, status=status, limit=10)
      for sub in subscriptions.data:
        stripe.Subscription.cancel(sub.id)
        logger.info("Cancelled Stripe subscription %s (was %s) for user %s", sub.id, status, user_id)

    stripe.Customer.delete(customer_id)
    logger.info("Deleted Stripe customer %s for user %s", customer_id, user_id)

  except UserRecord.DoesNotExist:
    logger.info("No usage record found for Stripe cancellation (user %s)", user_id)
  except Exception:
    logger.exception("Failed to clean up Stripe for user %s", user_id)


def _delete_all_user_worlds(user_id: str) -> None:
  """Delete every world owned by the user (GDPR right to erasure).

  Unconditionally hard-deletes owned worlds regardless of whether other
  users have active sessions — those sessions are removed first.
  """
  gsi1_pk = WorldMeta.gsi1_pk(user_id)
  worlds: list[WorldMeta] = list(WorldMeta.GSI1.query(hash_key=gsi1_pk))  # type: ignore  # PynamoDB GSI query return type

  logger.info("Deleting %d worlds for user %s", len(worlds), user_id)

  for world in worlds:
    world_id = str(world.id)
    try:
      delete_sessions_for_world(world_id)
    except Exception:
      logger.exception("Failed to delete sessions for world %s", world_id)

    try:
      hard_delete_orphaned_world(world_id)
    except Exception:
      logger.exception("Failed to delete world %s for user %s", world_id, user_id)


def _delete_username_reservation(user_id: str) -> None:
  """Free the user's username so it can be claimed by a new account."""
  try:
    record = UserRecord.get(UserRecord.pk(user_id), UserRecord.sk())
    if not record.username:
      return
    sentinel = UsernameReservation.get(
      UsernameReservation.pk(str(record.username)),
      UsernameReservation.sk(),
    )
    sentinel.delete()
    logger.info("Released username '%s' for user %s", record.username, user_id)
  except (UserRecord.DoesNotExist, UsernameReservation.DoesNotExist):
    pass
  except Exception:
    logger.exception("Failed to release username for user %s", user_id)


def _delete_usage_record(user_id: str) -> None:
  """Delete the UserRecord DynamoDB item."""
  try:
    record = UserRecord.get(UserRecord.pk(user_id), UserRecord.sk())
    record.delete()
    logger.info("Deleted usage record for user %s", user_id)
  except UserRecord.DoesNotExist:
    logger.info("No usage record to delete for user %s", user_id)
  except Exception:
    logger.exception("Failed to delete usage record for user %s", user_id)


_TOMBSTONE_TTL_DAYS = 365


def _tombstone_usage_record(user_id: str, email: str) -> None:
  """Replace the usage record with a tombstone for re-registration abuse prevention."""
  try:
    record = UserRecord.get(UserRecord.pk(user_id), UserRecord.sk())
  except UserRecord.DoesNotExist:
    return

  u = record.usage
  email_hash = hashlib.sha256(email.lower().strip().encode()).hexdigest()
  now = datetime.now(UTC)
  ttl_epoch = int((now + timedelta(days=_TOMBSTONE_TTL_DAYS)).timestamp())

  tombstone = UsageTombstone(
    PK=UsageTombstone.pk(email_hash),
    SK=UsageTombstone.sk(),
    email_hash=email_hash,
    worlds_created=int(u.worlds_created or 0),
    nodes_used=int(u.nodes_used or 0),
    audio_narrations_used=int(u.audio_narrations_used or 0),
    period_end=u.period_end,
    deleted_at=now,
    expiration=ttl_epoch,
  )
  tombstone.save()

  record.delete()
  logger.info("Tombstoned usage for user %s (1-year TTL, period_end=%s)", user_id, u.period_end)


def _delete_cognito_user(cognito_username: str) -> None:
  """Delete the user from the Cognito User Pool."""
  if not settings.COGNITO_USER_POOL_ID:
    logger.warning("COGNITO_USER_POOL_ID not set; skipping Cognito user deletion")
    return

  try:
    client = get_cognito_client()
    client.admin_delete_user(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=cognito_username,
    )
    logger.info("Deleted Cognito user %s", cognito_username)
  except Exception:
    logger.exception("Failed to delete Cognito user %s", cognito_username)
    raise
