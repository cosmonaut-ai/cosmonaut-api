"""DTOs for soundtrack library items."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from app.models.dtos.base import DTOModel

MAX_SOUNDTRACK_FILE_SIZE_BYTES = 100 * 1024 * 1024
MAX_BULK_SOUNDTRACK_CREATE_ITEMS = 100


class SoundtrackStatus(str, Enum):
  """Lifecycle status for a soundtrack library item."""

  DRAFT = "draft"
  ACTIVE = "active"
  DISABLED = "disabled"
  REJECTED = "rejected"


class SoundtrackContentRating(str, Enum):
  """Age-level content rating aligned with world vocabulary levels."""

  CHILD = "child"
  TEEN = "teen"
  ADULT = "adult"


class SoundtrackLoopStrategy(str, Enum):
  """Playback looping behavior for a soundtrack."""

  CROSSFADE = "crossfade"
  FADE_RESTART = "fade_restart"
  NONE = "none"


class SoundtrackProvider(str, Enum):
  """Source provider for generated soundtrack assets."""

  SUNO = "suno"


class SoundtrackDTO(DTOModel):
  """Soundtrack library item response DTO."""

  id: str | None = None
  status: SoundtrackStatus = SoundtrackStatus.DRAFT

  title: str | None = None
  description: str | None = None
  prompt: str | None = None

  audio_url: str | None = None
  s3_key: str | None = None
  file_size_bytes: int | None = None
  duration_seconds: float | None = None
  content_type: str | None = None

  content_rating: SoundtrackContentRating = SoundtrackContentRating.ADULT
  loop_strategy: SoundtrackLoopStrategy = SoundtrackLoopStrategy.CROSSFADE

  provider: SoundtrackProvider = SoundtrackProvider.SUNO
  provider_track_id: str | None = None
  generated_at: str | None = None

  pinecone_record_id: str | None = None
  pinecone_upserted_at: str | None = None

  quality_score: float | None = None
  curation_notes: str | None = None
  created_by: str | None = None
  reviewed_by: str | None = None
  reviewed_at: str | None = None

  created_at: str | None = None
  updated_at: str | None = None


class SoundtrackUploadDTO(DTOModel):
  """Presigned S3 upload target for a soundtrack file."""

  upload_url: str
  method: str = "PUT"
  headers: dict[str, str]
  expires_in_seconds: int


class SoundtrackCreateRequest(DTOModel):
  """Create a draft soundtrack and return a direct-upload target."""

  title: str | None = Field(default=None, max_length=200)
  description: str | None = Field(default=None, max_length=2000)
  prompt: str | None = Field(default=None, max_length=4000)

  content_type: str = Field(default="audio/mpeg", max_length=100)
  file_size_bytes: int | None = Field(default=None, ge=0, le=MAX_SOUNDTRACK_FILE_SIZE_BYTES)
  duration_seconds: float | None = Field(default=None, ge=0)

  content_rating: SoundtrackContentRating = SoundtrackContentRating.ADULT
  loop_strategy: SoundtrackLoopStrategy = SoundtrackLoopStrategy.CROSSFADE

  provider: SoundtrackProvider = SoundtrackProvider.SUNO
  provider_track_id: str | None = Field(default=None, max_length=200)
  generated_at: str | None = None

  quality_score: float | None = Field(default=None, ge=0, le=100)
  curation_notes: str | None = Field(default=None, max_length=4000)


class SoundtrackCreateResponse(DTOModel):
  """Create response containing the draft entity and upload target."""

  soundtrack: SoundtrackDTO
  upload: SoundtrackUploadDTO


class SoundtrackBulkCreateRequest(DTOModel):
  """Create multiple draft soundtracks and direct-upload targets."""

  items: list[SoundtrackCreateRequest] = Field(..., min_length=1, max_length=MAX_BULK_SOUNDTRACK_CREATE_ITEMS)


class SoundtrackBulkCreateResponse(DTOModel):
  """Bulk create response containing draft entities and upload targets."""

  items: list[SoundtrackCreateResponse]


class SoundtrackUpdateRequest(DTOModel):
  """Partial update request for soundtrack metadata and review state."""

  status: SoundtrackStatus | None = None
  title: str | None = Field(default=None, max_length=200)
  description: str | None = Field(default=None, max_length=2000)
  prompt: str | None = Field(default=None, max_length=4000)

  file_size_bytes: int | None = Field(default=None, ge=0, le=MAX_SOUNDTRACK_FILE_SIZE_BYTES)
  duration_seconds: float | None = Field(default=None, ge=0)
  content_type: str | None = Field(default=None, max_length=100)

  content_rating: SoundtrackContentRating | None = None
  loop_strategy: SoundtrackLoopStrategy | None = None

  provider: SoundtrackProvider | None = None
  provider_track_id: str | None = Field(default=None, max_length=200)
  generated_at: str | None = None

  quality_score: float | None = Field(default=None, ge=0, le=100)
  curation_notes: str | None = Field(default=None, max_length=4000)
