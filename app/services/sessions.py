"""Session service layer for world session CRUD operations.

Manages WorldSession, NodeSession, and SessionMembership entities.
All methods are synchronous, matching existing service patterns.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.errors import SessionNotFoundError
from app.core.observability import logger, tracer
from app.models.entities.node_session import BaseChoiceStateMap, NodeSession
from app.models.entities.session_membership import SessionMembership
from app.models.entities.world_session import WorldSession
from app.utils.pagination import decode_cursor, encode_cursor

if TYPE_CHECKING:
  from app.models.entities.world_meta import WorldMeta


# =============================================================================
# WorldSession Operations
# =============================================================================


@tracer.capture_method
def create_session(
  root_world_id: str,
  creator_id: str,
  members: list[str],
  world: WorldMeta | None = None,
) -> WorldSession:
  """Create a WorldSession and SessionMembership records for each member.

  Args:
    root_world_id: The root world this session is for.
    creator_id: The user creating the session.
    members: List of user_ids to add as members.
    world: Optional WorldMeta for denormalizing metadata onto memberships.

  Returns:
    The created WorldSession.
  """
  session_id = str(uuid.uuid4())
  now = datetime.now(UTC)

  session = WorldSession(
    PK=WorldSession.pk(session_id),
    SK=WorldSession.sk(),
    id=session_id,
    root_world_id=root_world_id,
    members=members,
    created_by=creator_id,
    GSI3PK=WorldSession.gsi3_pk(root_world_id),
    GSI3SK=WorldSession.gsi3_sk(session_id),
  )
  session.save()

  for member_id in members:
    role = "owner" if member_id == creator_id else "member"
    _create_membership(session_id, root_world_id, member_id, role, now, world)

  logger.info("Created session %s for world %s (members=%s)", session_id, root_world_id, members)
  return session


def find_session(session_id: str) -> WorldSession | None:
  """Fetch a WorldSession by ID, returning None if not found.

  Preferred over get_session() when the caller expects the session may not
  exist (e.g. resolving an ambiguous world_id that could be a session_id
  or a root_world_id), since it avoids exception-based control flow that
  generates Sentry noise via the tracer.
  """
  try:
    return WorldSession.get(WorldSession.pk(session_id), WorldSession.sk())
  except WorldSession.DoesNotExist:
    return None


@tracer.capture_method
def get_session(session_id: str) -> WorldSession:
  """Fetch a WorldSession by ID. Raises SessionNotFoundError if not found."""
  session = find_session(session_id)
  if session is None:
    raise SessionNotFoundError(f"Session not found: {session_id}")
  return session


@tracer.capture_method
def get_session_for_user(user_id: str, root_world_id: str) -> WorldSession | None:
  """Find a user's existing session for a specific world.

  Queries the SessionMembership by composite SK prefix, then fetches
  the associated WorldSession.

  Returns:
    The WorldSession if found, None otherwise.
  """
  prefix = SessionMembership.sk_prefix_for_world(root_world_id)
  results = list(
    SessionMembership.query(
      SessionMembership.pk(user_id),
      SessionMembership.SK.startswith(prefix),
      limit=1,
    )
  )
  if not results:
    return None

  try:
    return get_session(str(results[0].session_id))
  except SessionNotFoundError:
    logger.warning(
      "SessionMembership references non-existent session %s for user %s",
      results[0].session_id,
      user_id,
    )
    return None


@tracer.capture_method
def find_or_create_session(
  root_world_id: str,
  user_id: str,
  world: WorldMeta | None = None,
) -> WorldSession:
  """Find a user's session for a world, or create one if none exists.

  Used for lazy session initialization: when a user first interacts with
  a world (choose, generate-text), their session is created on demand.

  Args:
    root_world_id: The root world ID.
    user_id: The user's ID.
    world: Optional WorldMeta for denormalization and role determination.

  Returns:
    The existing or newly created WorldSession.
  """
  existing = get_session_for_user(user_id, root_world_id)
  if existing:
    return existing

  session = create_session(root_world_id, creator_id=user_id, members=[user_id], world=world)
  logger.info("Lazily created session %s for user %s in world %s", session.id, user_id, root_world_id)
  return session


@tracer.capture_method
def add_member(
  session_id: str,
  user_id: str,
  root_world_id: str,
  role: str = "member",
) -> SessionMembership:
  """Add a member to an existing session."""
  session = get_session(session_id)
  session.update(
    actions=[WorldSession.members.set(WorldSession.members.append([user_id]))],
  )

  now = datetime.now(UTC)
  return _create_membership(session_id, root_world_id, user_id, role, now)


@tracer.capture_method
def list_user_sessions(
  user_id: str,
  limit: int = 50,
  cursor: str | None = None,
) -> tuple[list[SessionMembership], str | None]:
  """List all sessions for a user with cursor-based pagination.

  Queries GSI2 with scan_index_forward=False so results are sorted
  by most recently accessed first.

  Returns:
    Tuple of (memberships, next_cursor). next_cursor is None when exhausted.
  """
  results = SessionMembership.GSI2.query(
    hash_key=SessionMembership.gsi2_pk(user_id),
    scan_index_forward=False,
    page_size=limit,
    limit=limit,
    last_evaluated_key=decode_cursor(cursor),
  )
  items = list(results)
  return items, encode_cursor(results.last_evaluated_key)


# =============================================================================
# NodeSession Operations
# =============================================================================


@tracer.capture_method
def create_node_session(
  session_id: str,
  node_id: str,
  root_world_id: str,
  title: str | None = None,
  base_choice_count: int = 0,
  parent_id: str | None = None,
) -> NodeSession:
  """Create a NodeSession for a node within a session.

  Also atomically increments WorldSession.visited_node_count.
  """
  ns = NodeSession(
    PK=NodeSession.pk(session_id),
    SK=NodeSession.sk(node_id),
    node_id=node_id,
    session_id=session_id,
    root_world_id=root_world_id,
    title=title,
    parent_id=parent_id,
    base_choice_states=[BaseChoiceStateMap(is_explored=False) for _ in range(base_choice_count)],
  )
  ns.save()

  try:
    ws = WorldSession(PK=WorldSession.pk(session_id), SK=WorldSession.sk())
    ws.update(
      actions=[WorldSession.visited_node_count.set((WorldSession.visited_node_count | 0) + 1)],
      add_version_condition=False,
    )
  except Exception:
    logger.warning("Failed to increment visited_node_count for session %s (non-fatal)", session_id, exc_info=True)

  return ns


@tracer.capture_method
def get_node_session(session_id: str, node_id: str) -> NodeSession | None:
  """Fetch a NodeSession. Returns None if not found (non-throwing)."""
  try:
    return NodeSession.get(NodeSession.pk(session_id), NodeSession.sk(node_id))
  except NodeSession.DoesNotExist:
    return None


@tracer.capture_method
def list_node_sessions(
  session_id: str,
  limit: int = 100,
  cursor: str | None = None,
) -> tuple[list[NodeSession], str | None]:
  """List all NodeSessions for a session with cursor-based pagination."""
  results = NodeSession.query(
    NodeSession.pk(session_id),
    NodeSession.SK.startswith("NODE#"),
    page_size=limit,
    limit=limit,
    last_evaluated_key=decode_cursor(cursor),
  )
  items = list(results)
  return items, encode_cursor(results.last_evaluated_key)


# =============================================================================
# Progress Operations
# =============================================================================


@tracer.capture_method
def update_session_progress(
  session_id: str,
  root_world_id: str,
  user_id: str,
  node_id: str,
) -> None:
  """Update per-member progress on WorldSession and SessionMembership.

  Uses direct update expressions (no read required).
  """
  now = datetime.now(UTC)

  ws = WorldSession(PK=WorldSession.pk(session_id), SK=WorldSession.sk())
  ws.update(
    actions=[WorldSession.per_member_progress[user_id].set(node_id)],  # type: ignore  # PynamoDB MapAttribute subscript
    add_version_condition=False,
  )

  membership = SessionMembership(
    PK=SessionMembership.pk(user_id),
    SK=SessionMembership.sk(root_world_id, session_id),
  )
  membership.update(
    actions=[
      SessionMembership.last_visited_node_id.set(node_id),
      SessionMembership.visited_node_count.set((SessionMembership.visited_node_count | 0) + 1),
      SessionMembership.last_accessed_at.set(now),
      SessionMembership.GSI2SK.set(SessionMembership.gsi2_sk(now, session_id)),
    ],
    add_version_condition=False,
  )


# =============================================================================
# Metadata Operations
# =============================================================================


@tracer.capture_method
def update_membership_metadata(
  user_id: str,
  root_world_id: str,
  session_id: str,
  world: WorldMeta,
) -> None:
  """Update denormalized world metadata on a user's SessionMembership.

  Called from the worker after world generation and image generation complete.
  """
  actions = []
  if world.title:
    actions.append(SessionMembership.title.set(world.title))
  if world.description:
    actions.append(SessionMembership.description.set(world.description))
  if world.genre:
    actions.append(SessionMembership.genre.set(world.genre))
  if world.world_length:
    actions.append(SessionMembership.world_length.set(world.world_length))
  if world.world_image_url:
    actions.append(SessionMembership.world_image_url.set(world.world_image_url))
  if world.world_image_alt_text:
    actions.append(SessionMembership.world_image_alt_text.set(world.world_image_alt_text))
  if world.image_generation_status:
    actions.append(SessionMembership.image_generation_status.set(world.image_generation_status))
  if world.generation_status:
    actions.append(SessionMembership.generation_status.set(world.generation_status))
  if world.created_at:
    created_at_str = world.created_at.isoformat() if hasattr(world.created_at, "isoformat") else str(world.created_at)
    actions.append(SessionMembership.root_created_at.set(created_at_str))
  if world.root_node_id:
    actions.append(SessionMembership.root_node_id.set(world.root_node_id))
  if world.vocab_level:
    actions.append(SessionMembership.vocab_level.set(world.vocab_level))
  if world.content_filter:
    actions.append(SessionMembership.content_filter.set(world.content_filter))

  if not actions:
    return

  membership = SessionMembership(
    PK=SessionMembership.pk(user_id),
    SK=SessionMembership.sk(root_world_id, session_id),
  )
  membership.update(actions=actions, add_version_condition=False)
  logger.info("Updated session membership metadata for user %s in session %s", user_id, session_id)


# =============================================================================
# Deletion Operations
# =============================================================================


@tracer.capture_method
def delete_session_for_user(
  session_id: str,
  user_id: str,
  root_world_id: str,
) -> bool:
  """Remove a user from a session, cleaning up empty sessions.

  Returns True if the world is now orphaned (zero sessions remaining).
  """
  try:
    SessionMembership(
      PK=SessionMembership.pk(user_id),
      SK=SessionMembership.sk(root_world_id, session_id),
    ).delete()
  except Exception:
    logger.warning("Failed to delete membership for user %s in session %s", user_id, session_id, exc_info=True)

  session = find_session(session_id)
  if session is not None:
    updated_members = [m for m in session.members if str(m) != user_id]
    if not updated_members:
      delete_session(session_id)
    else:
      session.members = updated_members
      progress = dict(session.per_member_progress or {})
      progress.pop(user_id, None)
      session.per_member_progress = progress
      session.save()

  remaining = list(
    WorldSession.GSI3.query(
      hash_key=WorldSession.gsi3_pk(root_world_id),
      range_key_condition=WorldSession.GSI3SK.startswith("SESSION#"),
      limit=1,
    )
  )
  return len(remaining) == 0


@tracer.capture_method
def revoke_unauthorized_sessions(
  root_world_id: str,
  author_id: str,
  shared_with: list[str],
) -> int:
  """Delete sessions for users not in the allowed set.

  Called when a world's visibility changes to private. Returns the
  number of revoked user-session pairs.
  """
  allowed = set(shared_with) | {author_id}
  revoked = 0

  gsi3_results = list(
    WorldSession.GSI3.query(
      hash_key=WorldSession.gsi3_pk(root_world_id),
      range_key_condition=WorldSession.GSI3SK.startswith("SESSION#"),
    )
  )

  for result in gsi3_results:
    session_id = str(result.PK).removeprefix("SESSION#")
    session = find_session(session_id)
    if session is None:
      continue

    members = [str(m) for m in session.members] if session.members else []
    unauthorized = [m for m in members if m not in allowed]

    for uid in unauthorized:
      try:
        SessionMembership(
          PK=SessionMembership.pk(uid),
          SK=SessionMembership.sk(root_world_id, session_id),
        ).delete()
      except Exception:
        logger.warning("Failed to delete membership for user %s in session %s", uid, session_id, exc_info=True)
      revoked += 1

    remaining = [m for m in members if m in allowed]
    if not remaining:
      delete_session(session_id)
    elif len(remaining) < len(members):
      session.members = remaining  # type: ignore[assignment]  # PynamoDB ListAttribute
      progress = dict(session.per_member_progress or {})
      for uid in unauthorized:
        progress.pop(uid, None)
      session.per_member_progress = progress
      session.save()

  if revoked:
    logger.info("Revoked %d session memberships for world %s", revoked, root_world_id)
  return revoked


@tracer.capture_method
def delete_session(session_id: str) -> None:
  """Delete a WorldSession and all its NodeSessions.

  Does NOT delete SessionMembership records (those are under different PKs
  and must be cleaned up by the caller).
  """
  pk = WorldSession.pk(session_id)
  items = list(WorldSession.query(pk))

  if not items:
    return

  with WorldSession.batch_write() as batch:
    for item in items:
      batch.delete(item)

  logger.info("Deleted session %s (%d items)", session_id, len(items))


@tracer.capture_method
def delete_user_memberships(user_id: str) -> None:
  """Delete all SessionMembership records for a user (account deletion cascade)."""
  pk = SessionMembership.pk(user_id)
  items = list(SessionMembership.query(pk, SessionMembership.SK.startswith("SMEMBER#")))

  if not items:
    return

  with SessionMembership.batch_write() as batch:
    for item in items:
      batch.delete(item)

  logger.info("Deleted %d session memberships for user %s", len(items), user_id)


@tracer.capture_method
def delete_sessions_for_world(root_world_id: str) -> None:
  """Delete all sessions associated with a root world.

  Uses GSI3 (ROOTWORLD#{root_world_id} -> SESSION#{session_id}) to
  efficiently find sessions without a full table scan.
  """
  gsi3_pk = WorldSession.gsi3_pk(root_world_id)
  gsi3_results: list[WorldSession] = list(WorldSession.GSI3.query(hash_key=gsi3_pk))

  for result in gsi3_results:
    session_id = str(result.PK).removeprefix("SESSION#")

    try:
      session = get_session(session_id)
      members = [str(m) for m in session.members] if session.members else []
    except SessionNotFoundError:
      members = []

    for member_id in members:
      try:
        membership = SessionMembership(
          PK=SessionMembership.pk(member_id),
          SK=SessionMembership.sk(root_world_id, session_id),
        )
        membership.delete()
      except Exception:
        logger.warning(
          "Failed to delete membership for user %s in session %s (non-fatal)",
          member_id,
          session_id,
          exc_info=True,
        )

    delete_session(session_id)

  if gsi3_results:
    logger.info("Deleted %d sessions for world %s", len(gsi3_results), root_world_id)


# =============================================================================
# Internal Helpers
# =============================================================================


def _create_membership(
  session_id: str,
  root_world_id: str,
  user_id: str,
  role: str,
  joined_at: datetime,
  world: WorldMeta | None = None,
) -> SessionMembership:
  """Create a SessionMembership item, optionally denormalizing world metadata."""
  membership = SessionMembership(
    PK=SessionMembership.pk(user_id),
    SK=SessionMembership.sk(root_world_id, session_id),
    session_id=session_id,
    root_world_id=root_world_id,
    user_id=user_id,
    role=role,
    joined_at=joined_at,
    last_accessed_at=joined_at,
    GSI2PK=SessionMembership.gsi2_pk(user_id),
    GSI2SK=SessionMembership.gsi2_sk(joined_at, session_id),
  )

  if world:
    membership.title = world.title
    membership.description = world.description
    membership.genre = world.genre
    membership.world_length = world.world_length
    membership.world_image_url = world.world_image_url
    membership.world_image_alt_text = world.world_image_alt_text
    if world.created_at:
      membership.root_created_at = (
        world.created_at.isoformat() if hasattr(world.created_at, "isoformat") else str(world.created_at)
      )
    membership.generation_status = world.generation_status
    membership.root_node_id = world.root_node_id
    membership.vocab_level = world.vocab_level
    membership.content_filter = world.content_filter

  membership.save()
  return membership
