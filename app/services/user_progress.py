"""User progress service for tracking last-visited story node per world.

Provides upsert, lookup, and bulk-delete operations for the ``UserProgress``
entity, which records the most recently visited node for each user-world pair.
"""

from __future__ import annotations

from aws_lambda_powertools import Logger

from app.core.config import settings
from app.models.entities.user_progress import UserProgress

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)


def update_progress(user_id: str, world_id: str, node_id: str) -> None:
  """Upsert the user's last-visited node for a world."""
  progress = UserProgress(
    PK=UserProgress.pk(user_id),
    SK=UserProgress.sk(world_id),
    user_id=user_id,
    world_id=world_id,
    current_node_id=node_id,
  )
  progress.save()


def get_progress(user_id: str, world_id: str) -> str | None:
  """Return the last-visited node ID for the given user and world, or None."""
  try:
    progress = UserProgress.get(UserProgress.pk(user_id), UserProgress.sk(world_id))
    return str(progress.current_node_id)
  except UserProgress.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
    return None


def delete_all_user_progress(user_id: str) -> None:
  """Delete all progress records for a user (used during account deletion)."""
  pk = UserProgress.pk(user_id)
  items = list(UserProgress.query(pk, UserProgress.SK.startswith("PROGRESS#")))

  if not items:
    return

  with UserProgress.batch_write() as batch:
    for item in items:
      batch.delete(item)

  logger.info("Deleted %d progress records for user %s", len(items), user_id)
