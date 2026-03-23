"""DTOs for session-based API responses (used in Phase 4+)."""

from __future__ import annotations

from app.models.dtos.base import DTOModel


class WorldSessionDTO(DTOModel):
  """World session response DTO."""

  id: str
  root_world_id: str
  members: list[str]
  created_by: str
  visited_node_count: int = 0
  created_at: str | None = None


class NodeSessionDTO(DTOModel):
  """Node session response DTO."""

  node_id: str
  session_id: str
  root_world_id: str
  title: str | None = None


class SessionMembershipDTO(DTOModel):
  """Session membership response DTO with denormalized world metadata."""

  session_id: str
  root_world_id: str
  role: str
  joined_at: str | None = None

  # Denormalized from WorldMeta
  title: str | None = None
  description: str | None = None
  genre: str | None = None
  world_length: str | None = None
  world_image_url: str | None = None
  world_image_alt_text: str | None = None
  root_created_at: str | None = None
  generation_status: str | None = None

  # Live session state
  last_visited_node_id: str | None = None
  visited_node_count: int = 0
