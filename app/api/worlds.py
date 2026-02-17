"""Worlds controller scaffolding for CRUD and discovery operations."""

from __future__ import annotations

from typing import NoReturn

from aws_lambda_powertools import Logger
from fastapi import APIRouter, Depends, HTTPException, Path, status

import app.services.worlds as world_service
from app.api.dependencies import require_world_read, require_world_write
from app.core.config import settings
from app.core.security import User, get_current_user
from app.models.dtos.world_meta import WorldCreateRequest, WorldMetaDTO, WorldUpdateSharingRequest
from app.services.email import send_world_invite
from app.services.usage import QuotaExceededError, StorageQuotaExceededError
from app.services.worlds import WorldNotFoundError

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

router = APIRouter(prefix="/worlds", tags=["worlds"])

_NOT_IMPLEMENTED = HTTPException(
  status_code=status.HTTP_501_NOT_IMPLEMENTED,
  detail="Worlds controller is scaffolded only; implement persistence and business logic.",
)


def _raise_not_implemented() -> NoReturn:
  """Consistently signal that the route is not yet implemented."""

  raise _NOT_IMPLEMENTED


@router.get("/", response_model=list[WorldMetaDTO], summary="List available worlds")
async def list_worlds(user: User = Depends(get_current_user)) -> list[WorldMetaDTO]:
  """Return a discoverable set of worlds (paged feed TBD)."""
  logger.info(f"Listing worlds for user {user.id}")
  try:
    worlds = world_service.list_worlds(user.id)
    return [world.to_dto() for world in worlds]
  except NotImplementedError:
    _raise_not_implemented()


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
  return world.to_dto()


@router.post(
  "/",
  status_code=status.HTTP_200_OK,
  response_model=WorldMetaDTO,
  summary="Initialize a new world",
)
async def create_world(payload: WorldCreateRequest, user: User = Depends(get_current_user)) -> WorldMetaDTO:
  """Create a new world."""
  try:
    world = world_service.create_world(payload, user.id)
  except StorageQuotaExceededError as e:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
  except QuotaExceededError as e:
    raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
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

  try:
    world = world_service.update_world(world_id, payload)
    return world.to_dto()
  except NotImplementedError:
    _raise_not_implemented()
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


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
  previous_shared: set[str] = set(world.shared_with or [])
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
