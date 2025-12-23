"""DTOs for user progress responses."""

from __future__ import annotations

from datetime import datetime

from app.models.dtos.base import DTOModel


class UserProgressDTO(DTOModel):
  """User progress response DTO."""

  pk: str
  sk: str
  current_node_id: str
  last_played: datetime | None = None
  world_title: str | None = None
  created_at: datetime | None = None
  updated_at: datetime | None = None
