"""DTO conversion helpers for API controllers."""

from __future__ import annotations

from app.models.dtos.session import WorldSessionDTO, WorldSessionSummaryDTO
from app.models.dtos.story_node import ChoiceDTO, StoryNodeDTO
from app.models.dtos.world_meta import ImageGenerationStatus, WorldGenerationStatus, WorldMetaDTO
from app.models.entities.node_session import NodeSession
from app.models.entities.session_membership import SessionMembership
from app.models.entities.world_meta import WorldMeta
from app.models.entities.world_session import WorldSession


def membership_to_world_summary_dto(membership: SessionMembership) -> WorldMetaDTO:
  """Construct an embedded WorldMetaDTO from denormalized SessionMembership fields."""
  return WorldMetaDTO(
    id=str(membership.root_world_id),
    title=membership.title,
    description=membership.description,
    genre=membership.genre,
    generation_status=WorldGenerationStatus(membership.generation_status) if membership.generation_status else None,
    root_node_id=membership.root_node_id,
    world_image_url=membership.world_image_url,
    world_image_alt_text=membership.world_image_alt_text,
    image_generation_status=ImageGenerationStatus(membership.image_generation_status)
    if membership.image_generation_status
    else None,
    world_length=membership.world_length,
    vocab_level=membership.vocab_level,
    content_filter=membership.content_filter,
    created_at=str(membership.root_created_at) if membership.root_created_at else membership.joined_at.isoformat(),
    author_id=str(membership.user_id) if membership.role == "owner" else None,
    visibility=None,
  )


def membership_to_session_summary_dto(membership: SessionMembership) -> WorldSessionSummaryDTO:
  """Construct a dashboard session summary from denormalized SessionMembership fields."""
  return WorldSessionSummaryDTO(
    id=str(membership.session_id),
    root_world_id=str(membership.root_world_id),
    role=str(membership.role),
    last_visited_node_id=str(membership.last_visited_node_id) if membership.last_visited_node_id else None,
    visited_node_count=int(membership.visited_node_count or 0),
    joined_at=membership.joined_at.isoformat() if membership.joined_at else None,
    last_accessed_at=membership.last_accessed_at.isoformat() if membership.last_accessed_at else None,
    created_at=str(membership.root_created_at) if membership.root_created_at else membership.joined_at.isoformat(),
    world=membership_to_world_summary_dto(membership),
  )


def session_to_dto(
  session: WorldSession,
  world: WorldMeta,
  membership: SessionMembership | None,
  user_id: str,
) -> WorldSessionDTO:
  """Construct a session detail DTO with full embedded root world data."""
  progress_map = session.per_member_progress.attribute_values if session.per_member_progress else {}
  last_visited = progress_map.get(user_id)
  role = str(membership.role) if membership is not None else ("owner" if world.author_id == user_id else "member")
  world_dto = world.to_dto()
  if world.author_id != user_id:
    world_dto.shared_with = None
  return WorldSessionDTO(
    id=str(session.id),
    root_world_id=str(session.root_world_id),
    role=role,
    last_visited_node_id=str(last_visited) if last_visited else None,
    visited_node_count=int(session.visited_node_count or 0),
    created_at=session.created_at.isoformat() if session.created_at else None,
    updated_at=session.updated_at.isoformat() if session.updated_at else None,
    world=world_dto,
  )


def node_session_to_list_dto(ns: NodeSession, root_world_id: str) -> StoryNodeDTO:
  """Build a graph-compatible StoryNodeDTO from a NodeSession overlay."""
  choices: list[ChoiceDTO] = []
  for state in ns.base_choice_states:
    choices.append(ChoiceDTO(label="", is_explored=bool(state.is_explored)))
  for cc in ns.custom_choices:
    choices.append(
      ChoiceDTO(
        label=str(cc.label),
        is_created=True,
        is_explored=bool(cc.is_explored),
        is_custom=True,
        creator=str(cc.creator_id),
      )
    )
  return StoryNodeDTO(
    id=str(ns.node_id),
    world_id=root_world_id,
    title=ns.title,
    parent_id=ns.parent_id,
    choices=choices,
  )
