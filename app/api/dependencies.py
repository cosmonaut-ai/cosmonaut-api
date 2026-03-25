"""Reusable FastAPI dependencies for world authorization."""

from __future__ import annotations

from fastapi import Depends

from app.core.errors import ForbiddenError, SessionAccessDeniedError
from app.core.security import User, get_current_user
from app.models.dtos.story_node import ChoiceDTO, StoryNodeDTO
from app.models.dtos.world_meta import GenerationStatus, ImageGenerationStatus, WorldMetaDTO
from app.models.entities.node_session import NodeSession
from app.models.entities.session_membership import SessionMembership
from app.models.entities.world_meta import WorldMeta
from app.models.entities.world_session import WorldSession
from app.services.sessions import find_or_create_session, find_session, get_session_for_user
from app.services.usage import get_or_create_usage
from app.services.worlds import get_world_entity


def require_onboarded(current_user: User = Depends(get_current_user)) -> User:
  """Reject requests from users who have not completed onboarding."""
  record = get_or_create_usage(current_user.id, email=current_user.email)
  if not record.is_onboarded:
    raise ForbiddenError("Onboarding required")
  return current_user


def require_session_read(
  world_id: str,
  user: User,
  invite_token: str | None = None,
) -> tuple[WorldSession, WorldMeta]:
  """Resolve world_id (which may be a session_id or root_world_id), verify access.

  Dual-resolution strategy:
    1. Try world_id as a session_id -> verify membership
    2. Fall back to root_world_id -> verify world-level access -> auto-create session

  When an ``invite_token`` is provided and the user does not otherwise have
  access, the token is validated and redeemed (granting a session + optional
  shared_with auto-add for private worlds).
  """
  session = find_session(world_id)
  if session is not None:
    if user.id not in [str(m) for m in session.members]:
      raise SessionAccessDeniedError(f"User {user.id} is not a member of session {world_id}")
    world = get_world_entity(str(session.root_world_id))
    return session, world

  world = get_world_entity(world_id)
  if not world.can_user_read(user.id):
    if invite_token:
      from app.services.invite_tokens import redeem_invite_token, validate_invite_token

      token_entity = validate_invite_token(invite_token, world_id)
      if token_entity:
        redeem_invite_token(invite_token, user.id, world_id)
        session = find_or_create_session(world_id, user.id, world=world)
        return session, world
    raise ForbiddenError(f"Not authorized to access world {world_id}")
  session = find_or_create_session(world_id, user.id, world=world)
  return session, world


def require_world_read(
  world_id: str,
  user: User,
  invite_token: str | None = None,
) -> tuple[WorldSession | None, WorldMeta]:
  """Resolve world_id and verify access WITHOUT auto-creating a session.

  Same dual-resolution and invite-token logic as ``require_session_read``,
  but returns ``None`` for the session when the user has no existing session
  instead of lazily creating one.
  """
  session = find_session(world_id)
  if session is not None:
    if user.id not in [str(m) for m in session.members]:
      raise SessionAccessDeniedError(f"User {user.id} is not a member of session {world_id}")
    world = get_world_entity(str(session.root_world_id))
    return session, world

  world = get_world_entity(world_id)
  if not world.can_user_read(user.id):
    if invite_token:
      from app.services.invite_tokens import redeem_invite_token, validate_invite_token

      token_entity = validate_invite_token(invite_token, world_id)
      if token_entity:
        redeem_invite_token(invite_token, user.id, world_id)
        existing = get_session_for_user(user.id, world_id)
        return existing, world
    raise ForbiddenError(f"Not authorized to access world {world_id}")
  existing = get_session_for_user(user.id, world_id)
  return existing, world


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
    id=str(membership.root_world_id),
    session_id=str(membership.session_id),
    title=membership.title,
    description=membership.description,
    genre=membership.genre,
    generation_status=GenerationStatus(membership.generation_status) if membership.generation_status else None,
    root_node_id=membership.root_node_id,
    world_image_url=membership.world_image_url,
    world_image_alt_text=membership.world_image_alt_text,
    image_generation_status=ImageGenerationStatus(membership.image_generation_status)
    if membership.image_generation_status
    else None,
    world_length=membership.world_length,
    vocab_level=membership.vocab_level,
    content_filter=membership.content_filter,
    created_at=str(membership.root_created_at) if membership.root_created_at else None,
    author_id=str(membership.user_id) if membership.role == "owner" else None,
    visibility=None,
  )


def node_session_to_list_dto(ns: NodeSession, root_world_id: str) -> StoryNodeDTO:
  """Build a graph-compatible StoryNodeDTO from NodeSession.

  Provides enough data for the graph page: node identity, title, parent_id
  for tree structure, and choice count. Full node detail (text, choice labels,
  audio) is fetched on-demand via get_node when the user interacts.
  """
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
