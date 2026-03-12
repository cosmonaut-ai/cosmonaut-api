"""Worlds controller scaffolding for CRUD and discovery operations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status

import app.services.worlds as world_service
from app.api.dependencies import require_world_read, require_world_write
from app.core.config import settings
from app.core.observability import logger
from app.core.security import User, get_current_user
from app.models.dtos.base import PaginatedResponse
from app.models.dtos.world_meta import WorldCreateRequest, WorldMetaDTO, WorldUpdateSharingRequest
from app.services.email import send_world_invite
from app.services.rate_limiter import check_rate_limit

router = APIRouter(prefix="/worlds", tags=["worlds"])


@router.get("/", response_model=PaginatedResponse[WorldMetaDTO], summary="List available worlds")
async def list_worlds(
  user: User = Depends(get_current_user),
  limit: int = Query(50, ge=1, le=200, description="Maximum number of worlds to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[WorldMetaDTO]:
  """Return worlds for the authenticated user with cursor-based pagination."""
  logger.info(f"Listing worlds for user {user.id}")
  worlds, next_cursor = world_service.list_worlds(user.id, limit, cursor)
  return PaginatedResponse(items=[world.to_dto() for world in worlds], next_cursor=next_cursor)


@router.get(
  "/{world_id}",
  response_model=WorldMetaDTO,
  summary="Fetch a single world by identifier",
)
async def get_world(
  world_id: str = Path(..., description="Identifier for the world"),
  user: User = Depends(get_current_user),
) -> WorldMetaDTO:
  """Retrieve a single world by its identifier."""
  world = require_world_read(world_id, user)
  dto = world.to_dto()
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
  return world.to_dto()


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
  require_world_write(world_id, user)
  world = world_service.update_world(world_id, payload)
  return world.to_dto()


@router.delete(
  "/{world_id}",
  status_code=status.HTTP_204_NO_CONTENT,
  summary="Delete a world",
)
async def delete_world(
  world_id: str = Path(..., description="Identifier for the world"), user: User = Depends(get_current_user)
) -> None:
  """Delete a world and any associated state (implementation pending)."""
  require_world_write(world_id, user)
  world_service.delete_world(world_id)


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
  """Share a world with a user."""
  world = require_world_write(world_id, user)

  # Identify newly added emails before persisting the update
  previous_shared: set[str] = {str(x) for x in (world.shared_with or [])}
  new_shared: set[str] = set(payload.shared_with or [])
  newly_added = new_shared - previous_shared

  world_dto = WorldMetaDTO(
    visibility=payload.visibility,
    shared_with=payload.shared_with,
  )

  world = world_service.update_world(world_id, world_dto)

  # Send invite emails to newly added users (non-blocking)
  if newly_added:
    inviter_name = user.email or user.username or "Someone"
    world_title = str(world.title) if world.title else "Untitled World"
    world_url = f"https://{settings.FRONTEND_DOMAIN}/worlds/{world_id}"
    for email in newly_added:
      send_world_invite(email, inviter_name, world_title, world_url)

  return world.to_dto()
