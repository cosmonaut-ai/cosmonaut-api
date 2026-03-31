"""DTOs for world metadata responses."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.dtos.base import DTOModel


class GenerationStatus(str, Enum):
  """Status of the world generation."""

  INITIALIZED = "initialized"
  GENERATING_LORE = "generating_lore"
  GENERATING_NARRATOR_PROFILE = "generating_narrator_profile"
  COMPLETED = "completed"
  FAILED = "failed"


class ImageGenerationStatus(str, Enum):
  """Status of the world cover image generation."""

  PENDING = "pending"
  COMPLETED = "completed"
  FAILED = "failed"


class WorldVisibility(str, Enum):
  """Visibility of the world."""

  PRIVATE = "private"
  UNLISTED = "unlisted"
  PUBLIC = "public"


class WorldLength(str, Enum):
  """Length preset for a world's story branches."""

  SHORT = "short"
  MEDIUM = "medium"
  LONG = "long"


class VocabLevel(str, Enum):
  """Controls vocabulary complexity and reading level."""

  CHILD = "child"
  TEEN = "teen"
  ADULT = "adult"


class ContentFilter(str, Enum):
  """Controls how explicit graphic material (mainly violence) can be."""

  NONE = "none"
  MODERATE = "moderate"
  STRICT = "strict"


class WorldCreateRequest(BaseModel):
  """Payload for creating a new world."""

  visibility: WorldVisibility = WorldVisibility.PRIVATE
  world_prompt: str = Field(..., max_length=2000, description="The prompt for the world.")
  world_length: WorldLength = Field(default=WorldLength.MEDIUM, description="Story length preset (short/medium/long).")
  vocab_level: VocabLevel = Field(default=VocabLevel.ADULT, description="Vocabulary complexity level.")
  content_filter: ContentFilter = Field(
    default=ContentFilter.NONE, description="Content filtering strictness for graphic material."
  )
  max_choices: int | None = Field(default=None, ge=2, le=10, description="Fixed choice count per node.")


class WorldUpdateSharingRequest(BaseModel):
  """Payload for updating world visibility and the shared_with allowlist."""

  shared_with: list[str] | None = Field(default=None, max_length=50)
  visibility: WorldVisibility | None = None


class InviteTokenDTO(DTOModel):
  """Response DTO for an invite token."""

  token: str
  root_world_id: str
  created_at: str
  expires_at: str
  use_count: int
  invite_url: str


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
  session_id: str | None = None
  shareable_id: str | None = None
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
  vocab_level: str | None = None
  content_filter: str | None = None
  max_choices: int | None = None
  world_image_url: str | None = None
  world_image_alt_text: str | None = None
  world_image_width: str | None = None
  world_image_height: str | None = None
  world_image_size: str | None = None
  image_generation_status: ImageGenerationStatus | None = None
  featured_order: int | None = None
  created_at: str | None = None
  updated_at: str | None = None
