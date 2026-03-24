"""Reusable FastAPI dependencies for world authorization."""

from __future__ import annotations

from app.core.errors import ForbiddenError, SessionAccessDeniedError
from app.core.security import User
from app.models.dtos.story_node import ChoiceDTO, StoryNodeDTO
from app.models.dtos.world_meta import GenerationStatus, WorldMetaDTO
from app.models.entities.node_session import NodeSession
from app.models.entities.session_membership import SessionMembership
from app.models.entities.world_meta import WorldMeta
from app.models.entities.world_session import WorldSession
from app.services.sessions import find_or_create_session, find_session
from app.services.worlds import get_world_entity


def require_session_read(world_id: str, user: User) -> tuple[WorldSession, WorldMeta]:
  """Resolve world_id (which may be a session_id or root_world_id), verify access.

  Dual-resolution strategy:
    1. Try world_id as a session_id -> verify membership
    2. Fall back to root_world_id -> verify world-level access -> auto-create session

  Uses find_session (non-throwing) to avoid exception-based control flow that
  generates Sentry noise via the Powertools tracer.
  """
  session = find_session(world_id)
  if session is not None:
    if user.id not in [str(m) for m in session.members]:
      raise SessionAccessDeniedError(f"User {user.id} is not a member of session {world_id}")
    world = get_world_entity(str(session.root_world_id))
    return session, world

  world = get_world_entity(world_id)
  if not world.can_user_read(user.id, user.email):
    raise ForbiddenError(f"Not authorized to access world {world_id}")
  session = find_or_create_session(world_id, user.id, world=world)
  return session, world


def require_session_write(world_id: str, user: User) -> tuple[WorldSession, WorldMeta]:
  """Same as session_read but also verifies world authorship."""
  session, world = require_session_read(world_id, user)
  if not world.can_user_write(user.id):
    raise ForbiddenError(f"Not authorized to modify world {world_id}")
  return session, world


# =============================================================================
# DTO conversion helpers (Phase 4)
# =============================================================================


def membership_to_world_dto(membership: SessionMembership) -> WorldMetaDTO:
  """Construct a WorldMetaDTO from denormalized SessionMembership fields."""
  return WorldMetaDTO(
    id=str(membership.session_id),
    shareable_id=str(membership.root_world_id),
    title=membership.title,
    description=membership.description,
    genre=membership.genre,
    generation_status=GenerationStatus(membership.generation_status) if membership.generation_status else None,
    root_node_id=membership.root_node_id,
    world_image_url=membership.world_image_url,
    world_image_alt_text=membership.world_image_alt_text,
    world_length=membership.world_length,
    family_friendly=membership.family_friendly == "true" if membership.family_friendly else None,
    created_at=str(membership.root_created_at) if membership.root_created_at else None,
    author_id=str(membership.user_id) if membership.role == "owner" else None,
    visibility=None,
  )


def node_session_to_list_dto(ns: NodeSession, session_id: str) -> StoryNodeDTO:
  """Build a graph-compatible StoryNodeDTO from NodeSession.

  Provides enough data for the graph page: node identity, title, parent_id
  for tree structure, and choice count. Full node detail (text, choice labels,
  audio) is fetched on-demand via get_node when the user interacts.
  """
  choices: list[ChoiceDTO] = []
  for state in ns.base_choice_states:
    choices.append(ChoiceDTO(label="", is_created=bool(state.is_explored)))
  for cc in ns.custom_choices:
    choices.append(
      ChoiceDTO(
        label=str(cc.label),
        is_created=bool(cc.is_explored),
        is_custom=True,
        creator=str(cc.creator_id),
      )
    )
  return StoryNodeDTO(
    id=str(ns.node_id),
    world_id=session_id,
    title=ns.title,
    parent_id=ns.parent_id,
    choices=choices,
  )
