"""Worlds controller scaffolding for CRUD and discovery operations."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, status

import app.services.worlds as world_service
from app.core.security import User, get_current_user
from app.models.dtos.world_meta import WorldCreateRequest, WorldMetaDTO
from app.services.worlds import WorldNotFoundError

router = APIRouter(prefix="/worlds", tags=["worlds"])

_NOT_IMPLEMENTED = HTTPException(
  status_code=status.HTTP_501_NOT_IMPLEMENTED,
  detail="Worlds controller is scaffolded only; implement persistence and business logic.",
)


def _raise_not_implemented() -> NoReturn:
  """Consistently signal that the route is not yet implemented."""

  raise _NOT_IMPLEMENTED


@router.get("", response_model=list[WorldMetaDTO], summary="List available worlds")
async def list_worlds(user: User = Depends(get_current_user)) -> list[WorldMetaDTO]:
  """Return a discoverable set of worlds (paged feed TBD)."""

  try:
    return world_service.list_worlds(user.id)
  except NotImplementedError:
    _raise_not_implemented()


@router.get(
  "/{world_id}",
  response_model=WorldMetaDTO,
  summary="Fetch a single world by identifier",
)
async def get_world(
  world_id: str = Path(..., description="Identifier for the world"),
) -> WorldMetaDTO:
  """Retrieve a single world by its identifier."""
  try:
    return world_service.get_world(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post(
  "/init",
  status_code=status.HTTP_201_CREATED,
  response_model=WorldMetaDTO,
  summary="Initialize a new world",
)
async def initialize_world(
  payload: WorldCreateRequest, user: User = Depends(get_current_user)
) -> WorldMetaDTO:
  """Initialize a new world scaffold; actual persistence wiring is pending."""
  return world_service.create_world(payload, user.id)


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
    return world_service.update_world(world_id, payload.model_dump(exclude_unset=True))
  except NotImplementedError:
    _raise_not_implemented()
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.delete(
  "/{world_id}",
  status_code=status.HTTP_204_NO_CONTENT,
  summary="Delete a world",
)
async def delete_world(world_id: str = Path(..., description="Identifier for the world")) -> None:
  """Delete a world and any associated state (implementation pending)."""

  try:
    world_service.delete_world(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post(
  "/{world_id}/generate-lore",
  response_model=WorldMetaDTO,
  summary="Generate lore for a world",
)
async def generate_lore(
  world_id: str = Path(..., description="Identifier for the world"),
) -> WorldMetaDTO:
  """Generate lore for a world."""
  try:
    return world_service.generate_lore(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post(
  "/{world_id}/generate-start-node",
  response_model=WorldMetaDTO,
  summary="Generate the first story node for a world",
)
async def generate_start_node(
  world_id: str = Path(..., description="Identifier for the world"),
) -> WorldMetaDTO:
  """Generate the first story node for a world."""
  try:
    return await world_service.generate_start_node(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
