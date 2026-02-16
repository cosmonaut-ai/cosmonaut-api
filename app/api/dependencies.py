"""Reusable FastAPI dependencies for world authorization."""

from __future__ import annotations

from app.core.security import User
from app.models.entities.world_meta import WorldMeta
from app.services.worlds import WorldNotFoundError, get_world_entity
from fastapi import HTTPException, status


def require_world_read(world_id: str, user: User) -> WorldMeta:
  """Fetch the world and verify read access. Raises 404 if not found, 403 if unauthorized."""
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
  if not world.can_user_read(user.id):
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail=f"You are not authorized to access world {world_id}",
    )
  return world


def require_world_write(world_id: str, user: User) -> WorldMeta:
  """Fetch the world and verify write access. Raises 404 if not found, 403 if unauthorized."""
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
  if not world.can_user_write(user.id):
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail=f"You are not authorized to modify world {world_id}",
    )
  return world
