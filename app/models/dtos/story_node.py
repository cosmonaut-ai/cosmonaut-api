"""DTOs for story node responses."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from app.models.dtos.base import DTOModel


class ChoiceDTO(DTOModel):
  """A choice option in a story node."""

  label: str
  outcome: str | None = None
  target: str | None = None
  is_created: bool = False
  is_custom: bool = False
  creator: str | None = None  # User ID for custom choices


class ChooseRequestDTO(DTOModel):
  """Request body for the choose endpoint."""

  choice_index: int | None = None  # For selecting existing choices
  custom_choice: str | None = Field(None, max_length=200)  # For free text choices


class StoryNodeProcessingStatus(str, Enum):
  """Status of the story node processing (async fact extraction)."""

  PENDING = "pending"
  PROCESSING = "processing"
  COMPLETED = "completed"
  FAILED = "failed"


class GenerationStatus(str, Enum):
  """Status of story node text generation."""

  INITIALIZED = "initialized"
  GENERATING = "generating"
  COMPLETED = "completed"
  FAILED = "failed"


class StoryNodeContextDTO(DTOModel):
  """Context to be provided to the LLM when generating a new story node."""

  world_facts: list[str]
  branch_facts: list[str]
  similar_nodes: list[str]


class StoryNodeDTO(DTOModel):
  """Story node response DTO."""

  id: str | None = None
  world_id: str | None = None
  text: str | None = None
  story_summary: str | None = None
  title: str | None = None
  choices: list[ChoiceDTO] = Field(default_factory=list)
  parent_choice: ChoiceDTO | None = None
  parent_id: str | None = None
  context: StoryNodeContextDTO | None = None
  ancestors: list[str] = Field(default_factory=list)
  created_at: datetime | None = None
  updated_at: datetime | None = None
  processing_status: StoryNodeProcessingStatus = StoryNodeProcessingStatus.PENDING
  generation_status: GenerationStatus = GenerationStatus.INITIALIZED
  audio: dict[str, str] = Field(default_factory=dict)
