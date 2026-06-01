"""DTOs for session-based API responses."""

from __future__ import annotations

from pydantic import BaseModel

from app.models.dtos.base import DTOModel
from app.models.dtos.world_meta import WorldMetaDTO


class WorldSessionCreateRequest(BaseModel):
  """Payload for creating or retrieving a user's session for a root world."""

  invite_token: str | None = None


class WorldSessionSummaryDTO(DTOModel):
  """Dashboard summary for a user's playthrough session."""

  id: str
  root_world_id: str
  role: str
  last_visited_node_id: str | None = None
  visited_node_count: int = 0
  joined_at: str | None = None
  last_accessed_at: str | None = None
  created_at: str | None = None
  updated_at: str | None = None
  world: WorldMetaDTO


class WorldSessionDTO(DTOModel):
  """World session response DTO."""

  id: str
  root_world_id: str
  role: str
  last_visited_node_id: str | None = None
  visited_node_count: int = 0
  soundtrack_playlist_id: str | None = None
  created_at: str | None = None
  updated_at: str | None = None
  world: WorldMetaDTO


class WorldCreateResponseDTO(DTOModel):
  """Response returned when creating a root world and its owner playthrough."""

  world: WorldMetaDTO
  session: WorldSessionDTO


class SessionLinkHandoffDTO(DTOModel):
  """Minimal root-world routing data for accessible session-link handoffs."""

  root_world_id: str
  title: str | None = None
  description: str | None = None
  world_image_url: str | None = None
  world_image_alt_text: str | None = None
