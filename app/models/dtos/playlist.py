"""DTOs for soundtrack playlists."""

from __future__ import annotations

from pydantic import Field

from app.models.dtos.base import DTOModel


class PlaylistTrackDTO(DTOModel):
  """Slim public-facing track within a playlist."""

  soundtrack_id: str
  title: str | None = None
  description: str | None = None
  audio_url: str | None = None
  duration_seconds: float | None = None
  content_rating: str | None = None
  loop_strategy: str | None = None


class PlaylistDTO(DTOModel):
  """Soundtrack playlist response DTO."""

  id: str
  description: str | None = None
  tracks: list[PlaylistTrackDTO] = Field(default_factory=list)
  generated_at: str | None = None
  created_at: str | None = None
