"""Reusable FastAPI dependencies for world authorization."""

from __future__ import annotations

from fastapi import Depends

from app.core.errors import ForbiddenError, SessionAccessDeniedError
from app.core.observability import logger
from app.core.security import User, get_current_user
from app.models.entities.world_meta import WorldMeta
from app.models.entities.world_session import WorldSession
from app.services.sessions import find_session
from app.services.usage import get_or_create_usage
from app.services.worlds import get_world_entity


def require_onboarded(current_user: User = Depends(get_current_user)) -> User:
  """Reject requests from users who have not completed onboarding."""
  record = get_or_create_usage(
    current_user.id,
    email=current_user.email,
    app_username=current_user.app_username,
    cognito_username=current_user.username,
    email_verified=current_user.email_verified,
    enabled=True,
  )
  if not record.is_onboarded:
    raise ForbiddenError("Onboarding required")
  return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
  """Reject requests from users outside the Admin or Owner Cognito groups."""
  if not current_user.is_admin:
    logger.warning("Admin access denied for user %s", current_user.id)
    raise ForbiddenError("Admin access required")
  return current_user


def require_session_read(session_id: str, user: User) -> tuple[WorldSession, WorldMeta]:
  """Verify that the current user can read a concrete playthrough session."""
  session = find_session(session_id)
  if session is not None:
    if user.id not in [str(m) for m in session.members]:
      raise SessionAccessDeniedError(f"User {user.id} is not a member of session {session_id}")
    world = get_world_entity(str(session.root_world_id))
    return session, world

  from app.core.errors import SessionNotFoundError

  raise SessionNotFoundError(f"Session not found: {session_id}")


def require_world_read(
  world_id: str,
  user: User,
  invite_token: str | None = None,
) -> WorldMeta:
  """Verify access to a root world without creating or resolving sessions."""
  world = get_world_entity(world_id)
  if not world.can_user_read(user.id):
    if invite_token:
      from app.services.invite_tokens import validate_invite_token

      token_entity = validate_invite_token(invite_token, world_id)
      if token_entity:
        return world
    raise ForbiddenError(f"Not authorized to access world {world_id}")
  return world


def require_world_write(world_id: str, user: User) -> WorldMeta:
  """Verify that the current user owns a root world."""
  world = get_world_entity(world_id)
  if not world.can_user_write(user.id):
    raise ForbiddenError(f"Not authorized to modify world {world_id}")
  return world
