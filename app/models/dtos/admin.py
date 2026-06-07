"""DTOs for admin-only API responses and requests."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.models.dtos.base import DTOModel
from app.models.dtos.soundtrack import SoundtrackDTO
from app.models.dtos.story_node import StoryNodeContextDTO
from app.models.dtos.usage import UsageStateDTO
from app.models.dtos.world_meta import WorldMetaDTO

AdminTier = Literal["FREE", "EXPLORER", "COSMONAUT"]
AdminContentFilter = Literal["none", "moderate", "strict"]


class AdminCognitoUserDTO(DTOModel):
  """App-user directory metadata surfaced in the admin UI."""

  username: str | None = None
  cognito_username: str | None = None
  sub: str
  email: str
  tier: str
  stripe_customer_id: str | None = None
  email_verified: bool | None = None
  created_at: str | None = None
  status: str | None = None
  enabled: bool | None = None


class AdminUserUsageDTO(UsageStateDTO):
  """DynamoDB usage/subscription record for an arbitrary user."""

  user_id: str
  stripe_customer_id: str | None = None
  saved_world_count: int
  created_at: str | None = None
  updated_at: str | None = None


class AdminWorldMetaDTO(WorldMetaDTO):
  """World metadata enriched for admin moderation views."""

  soundtrack_description: str | None = None


class AdminSessionMemberDTO(DTOModel):
  """Member-level session state for admin playthrough inspection."""

  user_id: str
  role: str | None = None
  last_visited_node_id: str | None = None
  visited_node_count: int = 0
  joined_at: str | None = None
  last_accessed_at: str | None = None


class AdminSessionDTO(DTOModel):
  """World session state surfaced for admin support/debugging."""

  id: str
  root_world_id: str
  created_by: str | None = None
  members: list[str]
  member_details: list[AdminSessionMemberDTO]
  per_member_progress: dict[str, str]
  visited_node_count: int = 0
  soundtrack_playlist_id: str | None = None
  created_at: str | None = None
  updated_at: str | None = None
  world: AdminWorldMetaDTO | None = None


class AdminUserGroupsResponse(DTOModel):
  groups: list[str]


class AdminTierUpdateRequest(DTOModel):
  tier: AdminTier


class AdminTierUpdateResponse(DTOModel):
  success: bool
  tier: AdminTier
  warning: str | None = None


class AdminFeaturedUpdateRequest(DTOModel):
  """Promote a world to featured, optionally at a specific order."""

  order: int | None = Field(default=None, ge=1)


class AdminFeaturedOrderItem(DTOModel):
  world_id: str = Field(..., min_length=1)
  featured_order: int = Field(..., ge=1)


class AdminFeaturedOrderUpdateRequest(DTOModel):
  items: list[AdminFeaturedOrderItem] = Field(..., min_length=1)


class AdminSoundtrackMatchRequest(DTOModel):
  """Preview the soundtracks returned by playlist matching for arbitrary text."""

  query: str = Field(..., min_length=3, max_length=2000)
  content_filter: AdminContentFilter = "none"
  top_k: int = Field(default=10, ge=1, le=20)


class AdminSoundtrackMatchHit(DTOModel):
  rank: int
  soundtrack_id: str | None = None
  pinecone_record_id: str | None = None
  score: float | None = None
  matched_text: str | None = None
  missing_soundtrack: bool = False
  soundtrack: SoundtrackDTO | None = None


class AdminSoundtrackMatchResponse(DTOModel):
  query: str
  content_filter: AdminContentFilter
  allowed_ratings: list[str]
  matches: list[AdminSoundtrackMatchHit]


class AdminWorldContextPreviewRequest(DTOModel):
  """Preview the vector context that would be considered for story generation."""

  world_id: str = Field(..., min_length=1)
  node_id: str | None = Field(default=None, min_length=1)
  text: str | None = Field(default=None, min_length=3, max_length=10000)
  ancestor_node_ids: list[str] = Field(default_factory=list)
  world_facts_top_k: int = Field(default=20, ge=0, le=50)
  branch_facts_top_k: int = Field(default=20, ge=0, le=50)
  similar_nodes_top_k: int = Field(default=3, ge=0, le=10)


class AdminWorldContextFactHit(DTOModel):
  id: str
  text: str
  origin_node_id: str | None = None


class AdminWorldContextSimilarNodeHit(DTOModel):
  id: str
  text: str
  world_id: str | None = None


class AdminWorldContextPreviewResponse(DTOModel):
  world_id: str
  source_node_id: str | None = None
  query_text: str
  ancestor_node_ids: list[str]
  stored_context: StoryNodeContextDTO | None = None
  world_facts: list[AdminWorldContextFactHit]
  branch_facts: list[AdminWorldContextFactHit]
  similar_nodes: list[AdminWorldContextSimilarNodeHit]
