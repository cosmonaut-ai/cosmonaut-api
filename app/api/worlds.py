"""Root world controller for canonical world metadata and sharing."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path, Query, status

import app.services.worlds as world_service
from app.api.dependencies import require_world_read, require_world_write
from app.api.mappers import session_to_dto
from app.core.errors import ForbiddenError
from app.core.observability import logger
from app.core.posthog import capture as ph_capture
from app.core.security import User, get_current_user
from app.models.dtos.session import WorldCreateResponseDTO, WorldSessionCreateRequest, WorldSessionDTO
from app.models.dtos.world_meta import (
  InviteTokenDTO,
  WorldCreateRequest,
  WorldMetaDTO,
  WorldUpdateRequest,
  WorldUpdateSharingRequest,
  WorldVisibility,
)
from app.services.invite_tokens import (
  create_invite_token,
  delete_invite_token,
  get_active_token,
  redeem_invite_token,
  token_to_dto,
  validate_invite_token,
)
from app.services.rate_limiter import check_rate_limit
from app.services.sessions import (
  ensure_root_node_session,
  find_or_create_session,
  get_session_membership,
  revoke_unauthorized_sessions,
)

router = APIRouter(prefix="/worlds", tags=["worlds"])


@router.get("/featured", response_model=list[WorldMetaDTO], summary="List featured public worlds")
async def list_featured_worlds(
  user: User = Depends(get_current_user),
) -> list[WorldMetaDTO]:
  """Return featured root worlds with public visibility, ordered by featured_order ascending."""
  worlds = world_service.list_featured_worlds()
  return [w.to_dto() for w in worlds]


@router.get(
  "/{world_id}",
  response_model=WorldMetaDTO,
  summary="Fetch a root world by identifier",
)
async def get_world(
  world_id: str = Path(..., description="Root world identifier"),
  invite: str | None = Query(None, description="Invite token for reading private worlds"),
  user: User = Depends(get_current_user),
) -> WorldMetaDTO:
  """Retrieve canonical root-world metadata without creating a playthrough session."""
  world = require_world_read(world_id, user, invite_token=invite)
  dto = world.to_dto()
  if world.author_id != user.id:
    dto.shared_with = None
  return dto


@router.post(
  "/",
  status_code=status.HTTP_200_OK,
  response_model=WorldCreateResponseDTO,
  summary="Initialize a new root world and owner session",
)
async def create_world(payload: WorldCreateRequest, user: User = Depends(get_current_user)) -> WorldCreateResponseDTO:
  """Create a canonical root world and the owner's initial playthrough session."""
  check_rate_limit(user.id, "create-world")
  world, session = world_service.create_world(payload, user.id)
  membership = get_session_membership(user.id, str(world.id), str(session.id))
  ph_capture(
    "world_created",
    distinct_id=user.id,
    properties={"visibility": world.visibility, "world_id": str(world.id), "source": "server"},
  )
  return WorldCreateResponseDTO(
    world=world.to_dto(),
    session=session_to_dto(session, world, membership, user.id),
  )


@router.patch(
  "/{world_id}",
  response_model=WorldMetaDTO,
  summary="Update fields on an existing root world",
)
async def update_world(
  payload: WorldUpdateRequest,
  world_id: str = Path(..., description="Root world identifier"),
  user: User = Depends(get_current_user),
) -> WorldMetaDTO:
  """Apply partial updates to a root world owned by the caller.

  Only user-editable fields are accepted; system-managed fields
  (generation_status, featured_order, image URLs, etc.) are rejected
  by the ``WorldUpdateRequest`` schema.
  """
  require_world_write(world_id, user)
  world = world_service.update_world(world_id, payload)
  return world.to_dto()


@router.post(
  "/{world_id}/sessions",
  status_code=status.HTTP_200_OK,
  response_model=WorldSessionDTO,
  summary="Find or create the caller's playthrough session for a root world",
)
async def create_world_session(
  world_id: str = Path(..., description="Root world identifier"),
  payload: WorldSessionCreateRequest | None = Body(default=None),
  user: User = Depends(get_current_user),
) -> WorldSessionDTO:
  """Idempotently create or return the caller's session for a root world."""
  world = world_service.get_world_entity(world_id)
  invite_token = payload.invite_token if payload else None

  if invite_token:
    token_entity = validate_invite_token(invite_token, world_id)
    if token_entity:
      redeem_invite_token(invite_token, user.id, world_id)
      world = world_service.get_world_entity(world_id)
    elif not world.can_user_read(user.id):
      raise ForbiddenError("Invalid or expired invite token")

  if not world.can_user_read(user.id):
    raise ForbiddenError(f"Not authorized to access world {world_id}")

  session = find_or_create_session(world_id, user.id, world=world)
  ensure_root_node_session(session, world)
  membership = get_session_membership(user.id, world_id, str(session.id))

  ph_capture(
    "world_joined",
    distinct_id=user.id,
    properties={"visibility": world.visibility, "world_id": world_id, "source": "server"},
  )
  return session_to_dto(session, world, membership, user.id)


@router.post(
  "/{world_id}/sharing",
  response_model=WorldMetaDTO,
  summary="Update root-world sharing settings",
)
async def update_sharing(
  payload: WorldUpdateSharingRequest,
  world_id: str = Path(..., description="Root world identifier"),
  user: User = Depends(get_current_user),
) -> WorldMetaDTO:
  """Update visibility and shared_with allowlist for a root world owned by the caller."""
  world = require_world_write(world_id, user)
  previous_visibility = world.visibility

  # Apply shared_with via update_world (visibility is restricted to this endpoint).
  world_dto = WorldMetaDTO(shared_with=payload.shared_with)
  world = world_service.update_world(world_id, world_dto)

  if payload.visibility is not None and payload.visibility.value != world.visibility:
    world.visibility = payload.visibility.value
    world.save()

  is_now_private = (payload.visibility == WorldVisibility.PRIVATE) or (
    payload.visibility is None and world.visibility == WorldVisibility.PRIVATE.value
  )
  transitioning_to_private = (
    payload.visibility == WorldVisibility.PRIVATE and previous_visibility != WorldVisibility.PRIVATE.value
  )

  if is_now_private or transitioning_to_private:
    resolved_shared = [str(s) for s in (world.shared_with or [])]
    revoke_unauthorized_sessions(
      root_world_id=world_id,
      author_id=user.id,
      shared_with=resolved_shared,
    )

  ph_capture(
    "world_shared",
    distinct_id=user.id,
    properties={
      "visibility": world.visibility,
      "shared_with_count": len(world.shared_with or []),
      "world_id": world_id,
      "source": "server",
    },
  )
  return world.to_dto()


# ---------------------------------------------------------------------------
# Invite token endpoints
# ---------------------------------------------------------------------------


@router.post(
  "/{world_id}/invite-token",
  response_model=InviteTokenDTO,
  summary="Create or retrieve an invite token",
)
async def create_or_get_invite_token(
  world_id: str = Path(..., description="Root world identifier"),
  user: User = Depends(get_current_user),
) -> InviteTokenDTO:
  """Create a 24-hour invite token for a root world, or return the existing one."""
  require_world_write(world_id, user)
  token = create_invite_token(world_id, user.id)
  return token_to_dto(token)


@router.get(
  "/{world_id}/invite-token",
  response_model=InviteTokenDTO | None,
  summary="Get the active invite token",
)
async def get_invite_token(
  world_id: str = Path(..., description="Root world identifier"),
  user: User = Depends(get_current_user),
) -> InviteTokenDTO | None:
  """Return the active invite token for a root world, if one exists."""
  require_world_write(world_id, user)
  token = get_active_token(world_id)
  if token is None:
    return None
  return token_to_dto(token)


@router.delete(
  "/{world_id}/invite-token",
  status_code=status.HTTP_204_NO_CONTENT,
  summary="Revoke the active invite token",
)
async def revoke_invite_token(
  world_id: str = Path(..., description="Root world identifier"),
  user: User = Depends(get_current_user),
) -> None:
  """Revoke the active invite token for a root world."""
  require_world_write(world_id, user)
  token = get_active_token(world_id)
  if token is not None:
    logger.info("Revoking invite token for world %s by user %s", world_id, user.id)
    delete_invite_token(str(token.token))
