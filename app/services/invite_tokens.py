"""Invite token service for sharing private worlds via time-limited links.

Enforces a one-active-token-per-world constraint. Tokens expire after
24 hours and are auto-cleaned by DynamoDB TTL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.observability import logger, tracer
from app.models.dtos.world_meta import InviteTokenDTO, WorldVisibility
from app.models.entities.world_invite_token import WorldInviteToken
from app.models.entities.world_meta import WorldMeta
from app.services.worlds import get_world_entity

_TOKEN_TTL = timedelta(hours=24)


@tracer.capture_method
def create_invite_token(root_world_id: str, created_by: str) -> WorldInviteToken:
  """Create a 24-hour invite token, or return the existing active one."""
  existing = get_active_token(root_world_id)
  if existing is not None:
    return existing

  token = str(uuid.uuid4())
  now = datetime.now(UTC)
  expires = now + _TOKEN_TTL
  expiration_epoch = int(expires.timestamp())

  item = WorldInviteToken(
    PK=WorldInviteToken.pk(token),
    SK=WorldInviteToken.sk(),
    token=token,
    root_world_id=root_world_id,
    created_by=created_by,
    expires_at=expires.isoformat(),
    use_count=0,
    expiration=expiration_epoch,
    GSI3PK=WorldInviteToken.gsi3_pk(root_world_id),
    GSI3SK=WorldInviteToken.gsi3_sk(token),
  )
  item.save()

  logger.info("Created invite token %s for world %s (expires %s)", token, root_world_id, expires.isoformat())
  return item


@tracer.capture_method
def get_active_token(root_world_id: str) -> WorldInviteToken | None:
  """Return the active (non-expired) invite token for a world, if any."""
  results = list(
    WorldInviteToken.GSI3.query(
      hash_key=WorldInviteToken.gsi3_pk(root_world_id),
      range_key_condition=WorldInviteToken.GSI3SK.startswith("INVITE#"),
    )
  )

  now = datetime.now(UTC)
  for result in results:
    token_str = str(result.PK).removeprefix("INVITE#")
    try:
      item = WorldInviteToken.get(WorldInviteToken.pk(token_str), WorldInviteToken.sk())
    except WorldInviteToken.DoesNotExist:
      continue

    expires = datetime.fromisoformat(str(item.expires_at))
    if expires.tzinfo is None:
      expires = expires.replace(tzinfo=UTC)
    if expires > now:
      return item

  return None


@tracer.capture_method
def validate_invite_token(token: str, root_world_id: str) -> WorldInviteToken | None:
  """Validate a token: exists, not expired, belongs to the correct world."""
  try:
    item = WorldInviteToken.get(WorldInviteToken.pk(token), WorldInviteToken.sk())
  except WorldInviteToken.DoesNotExist:
    return None

  if str(item.root_world_id) != root_world_id:
    return None

  expires = datetime.fromisoformat(str(item.expires_at))
  if expires.tzinfo is None:
    expires = expires.replace(tzinfo=UTC)
  if expires <= datetime.now(UTC):
    return None

  return item


@tracer.capture_method
def redeem_invite_token(token: str, user_id: str, root_world_id: str) -> None:
  """Increment use_count and, for private worlds, auto-add to shared_with."""
  try:
    item = WorldInviteToken(PK=WorldInviteToken.pk(token), SK=WorldInviteToken.sk())
    item.update(
      actions=[WorldInviteToken.use_count.set((WorldInviteToken.use_count | 0) + 1)],
      add_version_condition=False,
    )
  except Exception:
    logger.warning("Failed to increment use_count for token %s", token, exc_info=True)

  try:
    world = get_world_entity(root_world_id)
    if world.visibility == WorldVisibility.PRIVATE.value:
      current = [str(s) for s in (world.shared_with or [])]
      if user_id not in current:
        world.update(
          actions=[WorldMeta.shared_with.set(WorldMeta.shared_with.append([user_id]))],
        )
        logger.info("Auto-added user %s to shared_with for private world %s", user_id, root_world_id)
  except Exception:
    logger.warning("Failed to auto-add user %s to shared_with for world %s", user_id, root_world_id, exc_info=True)


@tracer.capture_method
def delete_invite_token(token: str) -> None:
  """Hard-delete an invite token (owner revocation)."""
  try:
    item = WorldInviteToken.get(WorldInviteToken.pk(token), WorldInviteToken.sk())
    item.delete()
    logger.info("Deleted invite token %s", token)
  except WorldInviteToken.DoesNotExist:
    pass


def token_to_dto(token: WorldInviteToken) -> InviteTokenDTO:
  """Convert a WorldInviteToken entity to its API DTO."""
  domain = settings.FRONTEND_DOMAIN or "cosmonaut-ai.com"
  world_id = str(token.root_world_id)
  token_str = str(token.token)
  return InviteTokenDTO(
    token=token_str,
    root_world_id=world_id,
    created_at=str(token.created_at.isoformat()) if token.created_at else "",
    expires_at=str(token.expires_at),
    use_count=int(token.use_count or 0),
    invite_url=f"https://{domain}/worlds/{world_id}?invite={token_str}",
  )
