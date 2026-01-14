"""Worlds controller scaffolding for CRUD and discovery operations."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, status

import app.services.worlds as world_service
from app.core.security import User, get_current_user
from app.models.dtos.world_meta import WorldCreateRequest, WorldMetaDTO, WorldShareRequest
from app.services.worlds import WorldNotFoundError, get_world_entity

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
  print(f"Listing worlds for user {user.id}")
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
  # Check authorization
  try:
    world = world_service.get_world(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

  if not world.can_user_read(user.id):
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail=f"You are not authorized to access world {world_id}",
    )

  return world.to_dto()


@router.post(
  "/",
  status_code=status.HTTP_200_OK,
  response_model=WorldMetaDTO,
  summary="Initialize a new world",
)
async def create_world(payload: WorldCreateRequest, user: User = Depends(get_current_user)) -> WorldMetaDTO:
  """Create a new world."""
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
) -> WorldMetaDTO:
  """Apply partial updates to an existing world."""
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

  # Check authorization
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

  if not world.can_user_write(user.id):
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail=f"You are not authorized to delete world {world_id}",
    )

  world_service.delete_world(world_id)


@router.post(
  "/{world_id}/share",
  response_model=WorldMetaDTO,
  summary="Share a world with a user",
)
async def share_world(
  payload: WorldShareRequest,
  world_id: str = Path(..., description="Identifier for the world"),
  user: User = Depends(get_current_user),
) -> WorldMetaDTO:
  """Share a world with a user."""
  # Check authorization
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

  if not world.can_user_write(user.id):
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail=f"You are not authorized to share world {world_id}",
    )

  world = world_service.share_world(world_id, payload.shared_with)
  return world.to_dto()
