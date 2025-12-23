"""DTOs for story node responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.models.dtos.base import DTOModel


class ChoiceDTO(DTOModel):
  """A choice option in a story node."""

  label: str
  target: str | None = None


class StoryNodeDTO(DTOModel):
  """Story node response DTO."""

  id: str | None = None
  world_id: str | None = None
  text: str | None = None
  story_summary: str | None = None
  title: str | None = None
  choices: list[ChoiceDTO] = Field(default_factory=list)
  parent_id: str | None = None
  ancestors: list[str] = Field(default_factory=list)
  created_at: datetime | None = None
  updated_at: datetime | None = None
