"""DTOs for world metadata responses."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.dtos.base import DTOModel


class GenerationStatus(str, Enum):
  """Status of the world generation."""

  INITIALIZED = "initialized"
  GENERATING_LORE = "generating_lore"
  GENERATING_NARRATOR_PROFILE = "generating_narrator_profile"
  COMPLETED = "completed"
  FAILED = "failed"


class WorldVisibility(str, Enum):
  """Visibility of the world."""

  PRIVATE = "private"
  PUBLIC = "public"


class WorldLength(str, Enum):
  """Length preset for a world's story branches."""

  SHORT = "short"
  MEDIUM = "medium"
  LONG = "long"


class WorldCreateRequest(BaseModel):
  """Payload for creating a new world."""

  visibility: WorldVisibility = WorldVisibility.PRIVATE
  world_prompt: str = Field(..., max_length=2000, description="The prompt for the world.")
  world_length: WorldLength = Field(default=WorldLength.MEDIUM, description="Story length preset (short/medium/long).")
  family_friendly: bool = Field(default=False, description="If true, story content is made suitable for children.")


class WorldUpdateSharingRequest(BaseModel):
  """Payload for sharing a world with a user."""

  shared_with: list[EmailStr] | None = Field(default=None, max_length=50)
  visibility: WorldVisibility | None = None

  @field_validator("shared_with", mode="before")
  @classmethod
  def normalize_emails(cls, v: list[str] | None) -> list[str] | None:
    if v is None:
      return v
    return [email.strip().lower() for email in v]


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
  visibility: WorldVisibility | None = None
  shared_with: list[str] | None = None
  world_prompt: str | None = None
  setting: str | None = None
  narrative_context: str | None = None
  characters: list[CharacterDTO] | None = None
  locations: list[LocationDTO] | None = None
  potential_endings: list[str] | None = None
  narrator_profile: str | None = None
  node_text_length: int | None = None
  story_max_nodes: int | None = None
  world_length: str | None = None
  family_friendly: bool | None = None
  world_image_url: str | None = None
  world_image_alt_text: str | None = None
  world_image_width: str | None = None
  world_image_height: str | None = None
  world_image_size: str | None = None
  created_at: str | None = None
  updated_at: str | None = None
