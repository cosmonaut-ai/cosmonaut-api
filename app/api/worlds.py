"""Worlds controller scaffolding for CRUD and discovery operations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status

import app.services.worlds as world_service
from app.api.dependencies import (
  membership_to_world_dto,
  require_session_read,
  require_session_write,
)
from app.core.observability import logger
from app.core.security import User, get_current_user
from app.models.dtos.base import PaginatedResponse
from app.models.dtos.world_meta import (
  InviteTokenDTO,
  WorldCreateRequest,
  WorldMetaDTO,
  WorldUpdateSharingRequest,
  WorldVisibility,
)
from app.services.invite_tokens import (
  create_invite_token,
  delete_invite_token,
  get_active_token,
  token_to_dto,
)
from app.services.rate_limiter import check_rate_limit
from app.services.sessions import (
  delete_session_for_user,
  get_session_for_user,
  list_user_sessions,
  revoke_unauthorized_sessions,
)

router = APIRouter(prefix="/worlds", tags=["worlds"])


@router.get("/", response_model=PaginatedResponse[WorldMetaDTO], summary="List available worlds")
async def list_worlds(
  user: User = Depends(get_current_user),
  limit: int = Query(50, ge=1, le=200, description="Maximum number of worlds to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[WorldMetaDTO]:
  """Return worlds for the authenticated user with cursor-based pagination."""
  logger.info(f"Listing worlds for user {user.id}")
  memberships, next_cursor = list_user_sessions(user.id, limit, cursor)
  items = [membership_to_world_dto(m) for m in memberships]
  return PaginatedResponse(items=items, next_cursor=next_cursor)


@router.get(
  "/{world_id}",
  response_model=WorldMetaDTO,
  summary="Fetch a single world by identifier",
)
async def get_world(
  world_id: str = Path(..., description="Identifier for the world"),
  invite: str | None = Query(None, description="Invite token for accessing private worlds"),
  user: User = Depends(get_current_user),
) -> WorldMetaDTO:
  """Retrieve a single world by its identifier."""
  session, world = require_session_read(world_id, user, invite_token=invite)
  dto = world.to_dto()
  dto.id = str(session.id)
  dto.shareable_id = str(session.root_world_id)
  if world.author_id != user.id:
    dto.shared_with = None
  return dto


@router.post(
  "/",
  status_code=status.HTTP_200_OK,
  response_model=WorldMetaDTO,
  summary="Initialize a new world",
)
async def create_world(payload: WorldCreateRequest, user: User = Depends(get_current_user)) -> WorldMetaDTO:
  """Create a new world."""
  check_rate_limit(user.id, "create-world")
  world = world_service.create_world(payload, user.id)
  session = get_session_for_user(user.id, str(world.id))
  dto = world.to_dto()
  dto.shareable_id = str(world.id)
  if session:
    dto.id = str(session.id)
  return dto


@router.patch(
  "/{world_id}",
  response_model=WorldMetaDTO,
  summary="Update fields on an existing world",
)
async def update_world(
  payload: WorldMetaDTO,
  world_id: str = Path(..., description="Identifier for the world"),
  user: User = Depends(get_current_user),
) -> WorldMetaDTO:
  """Apply partial updates to an existing world."""
  session, _ = require_session_write(world_id, user)
  world = world_service.update_world(str(session.root_world_id), payload)
  dto = world.to_dto()
  dto.id = str(session.id)
  dto.shareable_id = str(session.root_world_id)
  return dto


@router.delete(
  "/{world_id}",
  status_code=status.HTTP_204_NO_CONTENT,
  summary="Remove a world from the user's library",
)
async def delete_world(
  world_id: str = Path(..., description="Identifier for the world"), user: User = Depends(get_current_user)
) -> None:
  """Delete the caller's session for a world.

  Any session member can remove their own session.  If the world becomes
  orphaned (zero sessions remaining), it is permanently hard-deleted.
  """
  session, _ = require_session_read(world_id, user)
  root_world_id = str(session.root_world_id)

  is_orphaned = delete_session_for_user(
    session_id=str(session.id),
    user_id=user.id,
    root_world_id=root_world_id,
  )

  if is_orphaned:
    world_service.hard_delete_orphaned_world(root_world_id)


@router.post(
  "/{world_id}/sharing",
  response_model=WorldMetaDTO,
  summary="Share a world with a user",
)
async def update_sharing(
  payload: WorldUpdateSharingRequest,
  world_id: str = Path(..., description="Identifier for the world"),
  user: User = Depends(get_current_user),
) -> WorldMetaDTO:
  """Update visibility and shared_with allowlist for a world."""
  session, world = require_session_write(world_id, user)
  root_world_id = str(session.root_world_id)
  previous_visibility = world.visibility

  # Apply shared_with via update_world (visibility is in the immutable
  # skip-list there, so we handle it directly below).
  world_dto = WorldMetaDTO(shared_with=payload.shared_with)
  world = world_service.update_world(root_world_id, world_dto)

  # Apply visibility directly — this is the only endpoint allowed to
  # change it, ensuring the cascade always fires.
  if payload.visibility is not None and payload.visibility.value != world.visibility:
    world.visibility = payload.visibility.value
    world.save()

  is_now_private = (payload.visibility == WorldVisibility.PRIVATE) or (
    payload.visibility is None and world.visibility == WorldVisibility.PRIVATE.value
  )
  transitioning_to_private = (
    payload.visibility == WorldVisibility.PRIVATE
    and previous_visibility != WorldVisibility.PRIVATE.value
  )

  if is_now_private or transitioning_to_private:
    resolved_shared = [str(s) for s in (world.shared_with or [])]
    revoke_unauthorized_sessions(
      root_world_id=root_world_id,
      author_id=user.id,
      shared_with=resolved_shared,
    )

  dto = world.to_dto()
  dto.id = str(session.id)
  dto.shareable_id = root_world_id
  return dto


# ---------------------------------------------------------------------------
# Invite token endpoints
# ---------------------------------------------------------------------------


@router.post(
  "/{world_id}/invite-token",
  response_model=InviteTokenDTO,
  summary="Create or retrieve an invite token",
)
async def create_or_get_invite_token(
  world_id: str = Path(..., description="Identifier for the world"),
  user: User = Depends(get_current_user),
) -> InviteTokenDTO:
  """Create a 24-hour invite token for the world, or return the existing one."""
  session, _ = require_session_write(world_id, user)
  root_world_id = str(session.root_world_id)
  token = create_invite_token(root_world_id, user.id)
  return token_to_dto(token)


@router.get(
  "/{world_id}/invite-token",
  response_model=InviteTokenDTO | None,
  summary="Get the active invite token",
)
async def get_invite_token(
  world_id: str = Path(..., description="Identifier for the world"),
  user: User = Depends(get_current_user),
) -> InviteTokenDTO | None:
  """Return the active invite token for a world, if one exists."""
  session, _ = require_session_write(world_id, user)
  root_world_id = str(session.root_world_id)
  token = get_active_token(root_world_id)
  if token is None:
    return None
  return token_to_dto(token)


@router.delete(
  "/{world_id}/invite-token",
  status_code=status.HTTP_204_NO_CONTENT,
  summary="Revoke the active invite token",
)
async def revoke_invite_token(
  world_id: str = Path(..., description="Identifier for the world"),
  user: User = Depends(get_current_user),
) -> None:
  """Revoke the active invite token for a world."""
  session, _ = require_session_write(world_id, user)
  root_world_id = str(session.root_world_id)
  token = get_active_token(root_world_id)
  if token is not None:
    delete_invite_token(str(token.token))
