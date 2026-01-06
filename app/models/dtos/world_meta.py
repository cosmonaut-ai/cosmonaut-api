"""DTOs for world metadata responses."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.dtos.base import DTOModel


class GenerationStatus(str, Enum):
  """Status of the world generation."""

  INITIALIZED = "initialized"
  GENERATING_LORE = "generating_lore"
  GENERATING_START_NODE = "generating_start_node"
  GENERATING_NARRATOR_PROFILE = "generating_narrator_profile"
  COMPLETED = "completed"
  FAILED = "failed"


class WorldCreateRequest(BaseModel):
  """Payload for creating a new world."""

  visibility: str = "private"
  world_prompt: str = Field(..., description="The prompt for the world.")
  narrator_profile: str | None = None
  node_text_length: int | None = None


class CharacterDTO(DTOModel):
  name: str | None = None
  description: str | None = None
  relationships: list[str] | None = None


class LocationDTO(DTOModel):
  name: str | None = None
  description: str | None = None
  connections: list[str] | None = None


class WorldMetaDTO(DTOModel):
  """World metadata response DTO."""

  model_config = ConfigDict(extra="ignore", from_attributes=True)

  id: str | None = None
  title: str | None = None
  description: str | None = None
  genre: str | None = None
  score: str | None = None
  generation_status: GenerationStatus | None = None
  author_id: str | None = None
  root_node_id: str | None = None
  visibility: str | None = None
  world_prompt: str | None = None
  setting: str | None = None
  narrative_context: str | None = None
  characters: list[CharacterDTO] | None = None
  locations: list[LocationDTO] | None = None
  potential_endings: list[str] | None = None
  narrator_profile: str | None = None
  node_text_length: int | None = None
  story_max_nodes: int | None = None
  world_image_url: str | None = None
  world_image_alt_text: str | None = None
  world_image_width: str | None = None
  world_image_height: str | None = None
  world_image_size: str | None = None
  created_at: str | None = None
  updated_at: str | None = None
