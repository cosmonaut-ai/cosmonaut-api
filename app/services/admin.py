"""Admin service helpers for Cognito and DynamoDB-backed dashboard workflows."""

from __future__ import annotations

from typing import Any, cast

from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.errors import BadRequestError, ExternalServiceError, NotFoundError, SessionNotFoundError
from app.core.observability import logger, tracer
from app.models.dtos.admin import (
  AdminCognitoUserDTO,
  AdminFeaturedOrderItem,
  AdminSessionDTO,
  AdminSessionMemberDTO,
  AdminTier,
  AdminUserUsageDTO,
  AdminWorldMetaDTO,
)
from app.models.entities.playlist import Playlist
from app.models.entities.story_node import StoryNode
from app.models.entities.user import UserRecord
from app.models.entities.world_meta import WorldMeta
from app.models.entities.world_session import WorldSession
from app.services import cognito as cognito_service
from app.services import sessions as sessions_service
from app.services import usage as usage_service
from app.services.worlds import WorldNotFoundError, get_world_entity
from app.utils.pagination import decode_cursor, encode_cursor


def _cursor_payload(cursor: str | None) -> dict[str, Any]:
  payload = decode_cursor(cursor)
  return payload if payload is not None else {}


def _encode_token_cursor(token: str | None) -> str | None:
  if not token:
    return None
  return encode_cursor({"pagination_token": token})


def _safe_datetime(value: Any) -> str | None:
  if value is None:
    return None
  if hasattr(value, "isoformat"):
    return str(value.isoformat())
  return str(value)


def _parse_cognito_user(user: dict[str, Any]) -> AdminCognitoUserDTO:
  raw_attrs = user.get("Attributes", [])
  attrs: dict[str, str] = {}
  if isinstance(raw_attrs, list):
    for attr in cast(list[dict[str, Any]], raw_attrs):
      name = attr.get("Name")
      if isinstance(name, str):
        attrs[name] = str(attr.get("Value", ""))

  return AdminCognitoUserDTO(
    username=attrs.get("custom:username") or None,
    cognito_username=str(user.get("Username") or "") or None,
    sub=attrs.get("sub", ""),
    email=attrs.get("email", ""),
    tier=attrs.get("custom:tier") or "FREE",
    stripe_customer_id=attrs.get("custom:stripe_customer_id") or None,
    email_verified=attrs.get("email_verified") == "true",
    created_at=_safe_datetime(user.get("UserCreateDate")),
    status=str(user.get("UserStatus")) if user.get("UserStatus") else None,
    enabled=bool(user["Enabled"]) if "Enabled" in user else None,
  )


def _email_prefix_filter(email_prefix: str | None) -> str | None:
  if not email_prefix:
    return None
  cleaned = email_prefix.strip()
  if not cleaned:
    return None
  if '"' in cleaned or "\\" in cleaned:
    raise BadRequestError("email_prefix cannot contain quotes or backslashes")
  return f'email ^= "{cleaned}"'


def _external_cognito_error(action: str, exc: ClientError) -> ExternalServiceError:
  code = exc.response.get("Error", {}).get("Code", type(exc).__name__)
  logger.warning("Cognito %s failed: %s", action, code, exc_info=True)
  return ExternalServiceError(f"Cognito {action} failed: {code}")


def _user_record_to_dto(record: UserRecord) -> AdminCognitoUserDTO:
  usage = record.usage
  return AdminCognitoUserDTO(
    username=str(record.username) if record.username else None,
    cognito_username=str(record.cognito_username) if record.cognito_username else None,
    sub=str(record.user_id),
    email=str(record.email) if record.email else "",
    tier=str(usage.tier) if usage.tier else "FREE",
    stripe_customer_id=str(usage.stripe_customer_id) if usage.stripe_customer_id else None,
    email_verified=bool(record.email_verified) if record.email_verified is not None else None,
    created_at=record.created_at.isoformat() if record.created_at else None,
    status=str(record.cognito_status) if record.cognito_status else None,
    enabled=bool(record.enabled) if record.enabled is not None else None,
  )


def _app_user_matches_email_prefix(user: AdminCognitoUserDTO, email_prefix: str | None) -> bool:
  if not email_prefix:
    return True
  cleaned = email_prefix.strip().lower()
  if not cleaned:
    return True
  return user.email.lower().startswith(cleaned)


@tracer.capture_method
def list_cognito_users(
  email_prefix: str | None = None,
  limit: int = 60,
  cursor: str | None = None,
) -> tuple[list[AdminCognitoUserDTO], str | None]:
  """List Cognito users with optional email-prefix filtering."""
  if not settings.COGNITO_USER_POOL_ID:
    raise ExternalServiceError("COGNITO_USER_POOL_ID is not configured")

  payload = _cursor_payload(cursor)
  pagination_token = payload.get("pagination_token")

  params: dict[str, Any] = {
    "UserPoolId": settings.COGNITO_USER_POOL_ID,
    "Limit": limit,
  }
  filter_expr = _email_prefix_filter(email_prefix)
  if filter_expr:
    params["Filter"] = filter_expr
  if isinstance(pagination_token, str) and pagination_token:
    params["PaginationToken"] = pagination_token

  client = cast(Any, cognito_service.get_cognito_client())
  try:
    response = client.list_users(**params)
  except ClientError as exc:
    raise _external_cognito_error("list users", exc) from exc

  users = [_parse_cognito_user(user) for user in cast(list[dict[str, Any]], response.get("Users", []))]
  return users, _encode_token_cursor(response.get("PaginationToken"))


@tracer.capture_method
def list_app_users(
  email_prefix: str | None = None,
  limit: int = 60,
  cursor: str | None = None,
) -> tuple[list[AdminCognitoUserDTO], str | None]:
  """List app-created users from the DynamoDB admin directory, newest first."""
  last_key = decode_cursor(cursor)
  users: list[AdminCognitoUserDTO] = []

  while len(users) < limit:
    results = UserRecord.GSI2.query(
      hash_key=UserRecord.gsi2_pk_user_profiles(),
      scan_index_forward=False,
      page_size=limit,
      limit=limit,
      last_evaluated_key=last_key,
    )
    records = list(results)
    last_key = results.last_evaluated_key

    for record in records:
      user = _user_record_to_dto(record)
      if _app_user_matches_email_prefix(user, email_prefix):
        users.append(user)
        if len(users) >= limit:
          break

    if not last_key:
      break

  return users, encode_cursor(last_key)


@tracer.capture_method
def find_cognito_user_by_sub(user_id: str) -> AdminCognitoUserDTO | None:
  """Return Cognito user metadata for a Cognito ``sub``."""
  if not settings.COGNITO_USER_POOL_ID:
    raise ExternalServiceError("COGNITO_USER_POOL_ID is not configured")

  client = cast(Any, cognito_service.get_cognito_client())
  try:
    response = client.list_users(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Filter=f'sub = "{user_id}"',
      Limit=1,
    )
  except ClientError as exc:
    raise _external_cognito_error("look up user", exc) from exc

  users = cast(list[dict[str, Any]], response.get("Users", []))
  if not users:
    return None
  return _parse_cognito_user(users[0])


def get_cognito_user_by_sub(user_id: str) -> AdminCognitoUserDTO:
  user = find_cognito_user_by_sub(user_id)
  if user is None:
    raise NotFoundError(f"Cognito user not found for sub {user_id}")
  return user


@tracer.capture_method
def get_app_user_by_sub(user_id: str) -> AdminCognitoUserDTO:
  """Return app-user directory metadata for a user identified by Cognito ``sub``."""
  try:
    record = UserRecord.get(UserRecord.pk(user_id), UserRecord.sk())
  except UserRecord.DoesNotExist as exc:
    raise NotFoundError(f"App user not found for sub {user_id}") from exc
  return _user_record_to_dto(record)


@tracer.capture_method
def list_cognito_groups_for_user(user_id: str) -> list[str]:
  """List Cognito group names for a user identified by ``sub``."""
  try:
    app_user = get_app_user_by_sub(user_id)
    username = app_user.cognito_username
  except NotFoundError:
    username = None

  if not username:
    cognito_user = get_cognito_user_by_sub(user_id)
    username = cognito_user.cognito_username
  if not username:
    raise NotFoundError(f"Cognito username not found for sub {user_id}")

  client = cast(Any, cognito_service.get_cognito_client())
  try:
    response = client.admin_list_groups_for_user(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=username,
    )
  except Exception as exc:
    logger.exception("Failed to list Cognito groups for user %s", user_id)
    raise ExternalServiceError("Failed to list Cognito groups") from exc

  groups = cast(list[dict[str, Any]], response.get("Groups", []))
  return [str(group["GroupName"]) for group in groups if group.get("GroupName")]


def _usage_to_dto(record: UserRecord) -> AdminUserUsageDTO:
  usage = record.usage
  return AdminUserUsageDTO(
    user_id=str(record.user_id),
    username=str(record.username) if record.username else None,
    is_onboarded=bool(record.is_onboarded),
    tier=str(usage.tier) if usage.tier else "FREE",
    stripe_customer_id=str(usage.stripe_customer_id) if usage.stripe_customer_id else None,
    nodes_used=int(usage.nodes_used or 0),
    worlds_created=int(usage.worlds_created or 0),
    audio_narrations_used=int(usage.audio_narrations_used or 0),
    saved_world_count=int(usage.saved_world_count or 0),
    period_end=usage.period_end.isoformat() if usage.period_end else None,
    subscription_status=str(usage.subscription_status) if usage.subscription_status else None,
    pending_cancellation=bool(usage.pending_cancellation),
    cancellation_date=usage.cancellation_date.isoformat() if usage.cancellation_date else None,
    pending_tier=str(usage.pending_tier) if usage.pending_tier else None,
    pending_tier_date=usage.pending_tier_date.isoformat() if usage.pending_tier_date else None,
    newsletter_opted_in=bool(record.newsletter_opted_in),
    created_at=record.created_at.isoformat() if record.created_at else None,
    updated_at=record.updated_at.isoformat() if record.updated_at else None,
  )


@tracer.capture_method
def get_user_usage(user_id: str) -> AdminUserUsageDTO:
  """Read an arbitrary user's usage record without creating one."""
  try:
    record = UserRecord.get(UserRecord.pk(user_id), UserRecord.sk())
  except UserRecord.DoesNotExist as exc:
    raise NotFoundError(f"Usage record not found for user {user_id}") from exc
  return _usage_to_dto(record)


@tracer.capture_method
def update_user_tier(user_id: str, tier: AdminTier) -> str | None:
  """Update DynamoDB tier data, then best-effort sync Cognito claims.

  DynamoDB is the source of truth for quota enforcement. The Cognito claim sync
  is returned as a warning when it fails, matching the former admin dashboard
  behavior while keeping the source-of-truth update successful.
  """
  try:
    UserRecord.get(UserRecord.pk(user_id), UserRecord.sk())
  except UserRecord.DoesNotExist as exc:
    raise NotFoundError(f"Usage record not found for user {user_id}") from exc

  usage_service.update_tier(user_id, tier)

  try:
    _sync_cognito_tier_strict(user_id, tier)
  except Exception as exc:
    logger.warning("DynamoDB tier updated, but Cognito sync failed for user %s", user_id, exc_info=True)
    return f"DynamoDB updated successfully, but Cognito sync failed: {exc}"
  return None


@tracer.capture_method
def update_user_cognito_status(user_id: str, *, enabled: bool, status: str | None = None) -> None:
  """Persist the latest Cognito account status snapshot on the app user record."""
  try:
    record = UserRecord.get(UserRecord.pk(user_id), UserRecord.sk())
  except UserRecord.DoesNotExist:
    logger.warning("Cannot sync Cognito status for missing app user %s", user_id)
    return

  record.enabled = enabled
  record.cognito_status = status
  record.save()


def _sync_cognito_tier_strict(user_id: str, tier: AdminTier) -> None:
  """Synchronize ``custom:tier`` to Cognito and raise on failure."""
  if not settings.COGNITO_USER_POOL_ID:
    raise ExternalServiceError("COGNITO_USER_POOL_ID is not configured")

  client = cast(Any, cognito_service.get_cognito_client())
  username = cognito_service._resolve_cognito_username(client, user_id)
  if not username:
    raise NotFoundError(f"Could not resolve Cognito username for sub {user_id}")

  try:
    client.admin_update_user_attributes(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Username=username,
      UserAttributes=[{"Name": "custom:tier", "Value": tier}],
    )
  except Exception as exc:
    raise ExternalServiceError("Failed to update Cognito tier") from exc


@tracer.capture_method
def list_user_worlds(
  user_id: str,
  limit: int,
  cursor: str | None,
) -> tuple[list[WorldMeta], str | None]:
  """List worlds authored by a user, newest first."""
  results = WorldMeta.GSI1.query(
    hash_key=WorldMeta.gsi1_pk(user_id),
    scan_index_forward=False,
    page_size=limit,
    limit=limit,
    last_evaluated_key=decode_cursor(cursor),
  )
  return cast(list[WorldMeta], list(results)), encode_cursor(results.last_evaluated_key)


def _clean_world_lookup(search: str | None) -> str | None:
  if not search:
    return None
  cleaned = search.strip()
  if not cleaned:
    return None
  if cleaned.startswith("WORLD#"):
    return cleaned.removeprefix("WORLD#")
  return cleaned


def _get_world_or_none(world_id: str) -> WorldMeta | None:
  try:
    return get_world(world_id)
  except NotFoundError:
    return None


def _hydrate_worlds_from_index(indexed_worlds: list[WorldMeta]) -> list[WorldMeta]:
  keys = [(str(item.PK), str(item.SK)) for item in indexed_worlds]
  if not keys:
    return []

  hydrated = {(str(world.PK), str(world.SK)): world for world in WorldMeta.batch_get(keys)}
  return [hydrated[key] for key in keys if key in hydrated]


def _playlist_ids_for_worlds(worlds: list[WorldMeta]) -> list[str]:
  playlist_ids: list[str] = []
  seen: set[str] = set()
  for world in worlds:
    if not world.default_playlist_id:
      continue
    playlist_id = str(world.default_playlist_id)
    if playlist_id in seen:
      continue
    seen.add(playlist_id)
    playlist_ids.append(playlist_id)
  return playlist_ids


def _soundtrack_descriptions_for_worlds(worlds: list[WorldMeta]) -> dict[str, str | None]:
  playlist_ids = _playlist_ids_for_worlds(worlds)
  if not playlist_ids:
    return {}

  try:
    playlists = list(Playlist.batch_get([(Playlist.pk(playlist_id), Playlist.sk()) for playlist_id in playlist_ids]))
  except Exception:
    logger.warning("Failed to hydrate soundtrack playlist descriptions for admin worlds", exc_info=True)
    return {}

  return {str(playlist.id): str(playlist.description) if playlist.description else None for playlist in playlists}


def world_to_admin_dto(world: WorldMeta) -> AdminWorldMetaDTO:
  """Convert a world into the admin DTO, including its soundtrack description."""
  return worlds_to_admin_dtos([world])[0]


def worlds_to_admin_dtos(worlds: list[WorldMeta]) -> list[AdminWorldMetaDTO]:
  """Convert worlds into admin DTOs with denormalized playlist descriptions."""
  soundtrack_descriptions = _soundtrack_descriptions_for_worlds(worlds)
  dtos: list[AdminWorldMetaDTO] = []
  for world in worlds:
    playlist_id = str(world.default_playlist_id) if world.default_playlist_id else None
    dto_data = world.to_dto().model_dump()
    dto_data["soundtrack_description"] = soundtrack_descriptions.get(playlist_id) if playlist_id else None
    dtos.append(AdminWorldMetaDTO(**dto_data))
  return dtos


def _hydrate_sessions_from_index(indexed_sessions: list[WorldSession]) -> list[WorldSession]:
  keys = [(str(item.PK), str(item.SK)) for item in indexed_sessions]
  if not keys:
    return []

  hydrated = {(str(session.PK), str(session.SK)): session for session in WorldSession.batch_get(keys)}
  return [hydrated[key] for key in keys if key in hydrated]


def _session_progress_map(session: WorldSession) -> dict[str, str]:
  if not session.per_member_progress:
    return {}
  values = session.per_member_progress.attribute_values
  return {str(user_id): str(node_id) for user_id, node_id in values.items() if node_id}


def _session_membership_detail(session: WorldSession, user_id: str) -> AdminSessionMemberDTO:
  root_world_id = str(session.root_world_id)
  session_id = str(session.id)
  membership = sessions_service.get_session_membership(user_id, root_world_id, session_id)
  progress_map = _session_progress_map(session)

  if membership is None:
    return AdminSessionMemberDTO(
      user_id=user_id,
      role="owner" if user_id == str(session.created_by) else None,
      last_visited_node_id=progress_map.get(user_id),
    )

  return AdminSessionMemberDTO(
    user_id=user_id,
    role=str(membership.role) if membership.role else None,
    last_visited_node_id=str(membership.last_visited_node_id)
    if membership.last_visited_node_id
    else progress_map.get(user_id),
    visited_node_count=int(membership.visited_node_count or 0),
    joined_at=membership.joined_at.isoformat() if membership.joined_at else None,
    last_accessed_at=membership.last_accessed_at.isoformat() if membership.last_accessed_at else None,
  )


def session_to_admin_dto(session: WorldSession, world: WorldMeta | None = None) -> AdminSessionDTO:
  """Convert a world session into an admin DTO with member progress."""
  if world is None:
    try:
      world = get_world(str(session.root_world_id))
    except NotFoundError:
      world = None

  members = [str(member) for member in session.members]
  return AdminSessionDTO(
    id=str(session.id),
    root_world_id=str(session.root_world_id),
    created_by=str(session.created_by) if session.created_by else None,
    members=members,
    member_details=[_session_membership_detail(session, member_id) for member_id in members],
    per_member_progress=_session_progress_map(session),
    visited_node_count=int(session.visited_node_count or 0),
    soundtrack_playlist_id=str(session.soundtrack_playlist_id) if session.soundtrack_playlist_id else None,
    created_at=session.created_at.isoformat() if session.created_at else None,
    updated_at=session.updated_at.isoformat() if session.updated_at else None,
    world=world_to_admin_dto(world) if world else None,
  )


def sessions_to_admin_dtos(sessions: list[WorldSession], world: WorldMeta | None = None) -> list[AdminSessionDTO]:
  """Convert world sessions into admin DTOs."""
  return [session_to_admin_dto(session, world=world) for session in sessions]


@tracer.capture_method
def get_session(session_id: str) -> WorldSession:
  """Fetch a world session by exact session ID for admin inspection."""
  try:
    return sessions_service.get_session(session_id)
  except SessionNotFoundError as exc:
    raise NotFoundError(f"Session not found: {session_id}") from exc


@tracer.capture_method
def list_world_sessions(world_id: str, limit: int, cursor: str | None) -> tuple[list[WorldSession], str | None]:
  """List sessions for a world through the world-session GSI."""
  get_world(world_id)
  last_key = decode_cursor(cursor)
  sessions: list[WorldSession] = []

  while len(sessions) < limit:
    page_limit = limit - len(sessions)
    results = WorldSession.GSI3.query(
      hash_key=WorldSession.gsi3_pk(world_id),
      range_key_condition=WorldSession.GSI3SK.startswith("SESSION#"),
      scan_index_forward=False,
      page_size=page_limit,
      limit=page_limit,
      last_evaluated_key=last_key,
    )
    indexed_sessions = list(results)
    sessions.extend(_hydrate_sessions_from_index(indexed_sessions))
    last_key = results.last_evaluated_key
    if not last_key:
      break

  sessions.sort(key=lambda session: str(session.created_at or session.updated_at or ""), reverse=True)
  return sessions, encode_cursor(last_key)


@tracer.capture_method
def list_all_worlds(limit: int, cursor: str | None, search: str | None = None) -> tuple[list[WorldMeta], str | None]:
  """List recent indexed world metadata records, newest first.

  Search is intentionally exact-ID based to keep the admin endpoint bounded:
  a lookup first tries a world ID, then treats the value as an author ID and
  uses the author-worlds index.
  """
  lookup = _clean_world_lookup(search)
  if lookup:
    world = _get_world_or_none(lookup)
    if world:
      return [world], None
    return list_user_worlds(lookup, limit=limit, cursor=cursor)

  last_key = decode_cursor(cursor)
  worlds: list[WorldMeta] = []

  while len(worlds) < limit:
    results = WorldMeta.GSI3.query(
      hash_key=WorldMeta.gsi3_pk_world_directory(),
      scan_index_forward=False,
      page_size=limit,
      limit=limit - len(worlds),
      last_evaluated_key=last_key,
    )
    indexed_worlds = list(results)
    worlds.extend(_hydrate_worlds_from_index(indexed_worlds))
    last_key = results.last_evaluated_key
    if not last_key:
      break

  return worlds, encode_cursor(last_key)


@tracer.capture_method
def get_world(world_id: str) -> WorldMeta:
  """Fetch world metadata without user/session access checks."""
  try:
    return get_world_entity(world_id)
  except WorldNotFoundError as exc:
    raise NotFoundError(f"World not found: {world_id}") from exc


@tracer.capture_method
def list_world_nodes(world_id: str, limit: int, cursor: str | None) -> tuple[list[StoryNode], str | None]:
  """List root story nodes for a world independent of user sessions."""
  get_world(world_id)
  results = StoryNode.query(
    StoryNode.pk(world_id),
    StoryNode.SK.startswith("NODE#"),
    page_size=limit,
    limit=limit,
    last_evaluated_key=decode_cursor(cursor),
  )
  return list(results), encode_cursor(results.last_evaluated_key)


@tracer.capture_method
def get_playlist(playlist_id: str) -> Playlist:
  """Fetch a soundtrack playlist by exact ID for admin inspection."""
  try:
    return Playlist.get(Playlist.pk(playlist_id), Playlist.sk())
  except Playlist.DoesNotExist as exc:
    raise NotFoundError(f"Playlist not found: {playlist_id}") from exc


@tracer.capture_method
def list_featured_worlds(limit: int, cursor: str | None) -> tuple[list[WorldMeta], str | None]:
  """List featured worlds for admin management, including non-public worlds."""
  results = WorldMeta.GSI2.query(
    hash_key=WorldMeta.gsi2_pk_featured(),
    scan_index_forward=True,
    page_size=limit,
    limit=limit,
    last_evaluated_key=decode_cursor(cursor),
  )
  return list(results), encode_cursor(results.last_evaluated_key)


def _max_featured_order() -> int:
  results = WorldMeta.GSI2.query(
    hash_key=WorldMeta.gsi2_pk_featured(),
    scan_index_forward=False,
    limit=1,
  )
  items = list(results)
  if not items:
    return 0
  return int(items[0].featured_order or 0)


def _set_featured_order(world: WorldMeta, order: int) -> WorldMeta:
  world.GSI2PK = WorldMeta.gsi2_pk_featured()
  world.GSI2SK = WorldMeta.gsi2_sk_order(order)
  world.featured_order = order
  world.save()
  return world


@tracer.capture_method
def promote_world_to_featured(world_id: str, order: int | None = None) -> WorldMeta:
  """Promote a world to the featured list."""
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError as exc:
    raise NotFoundError(f"World not found: {world_id}") from exc

  next_order = order if order is not None else _max_featured_order() + 1
  return _set_featured_order(world, next_order)


@tracer.capture_method
def remove_world_from_featured(world_id: str) -> WorldMeta:
  """Remove a world from the featured list."""
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError as exc:
    raise NotFoundError(f"World not found: {world_id}") from exc

  world.update(
    actions=[
      WorldMeta.GSI2PK.remove(),
      WorldMeta.GSI2SK.remove(),
      WorldMeta.featured_order.remove(),
    ]
  )
  return get_world_entity(world_id)


@tracer.capture_method
def update_featured_orders(items: list[AdminFeaturedOrderItem]) -> list[WorldMeta]:
  """Update featured ordering for one or more worlds."""
  world_ids = [item.world_id for item in items]
  if len(set(world_ids)) != len(world_ids):
    raise BadRequestError("Featured order update contains duplicate world IDs")

  orders = [item.featured_order for item in items]
  if len(set(orders)) != len(orders):
    raise BadRequestError("Featured order update contains duplicate order values")

  updated: list[WorldMeta] = []
  for item in items:
    try:
      world = get_world_entity(item.world_id)
    except WorldNotFoundError as exc:
      raise NotFoundError(f"World not found: {item.world_id}") from exc
    if not world.GSI2PK:
      raise BadRequestError(f"World is not featured: {item.world_id}")
    updated.append(_set_featured_order(world, item.featured_order))

  updated.sort(key=lambda world: int(world.featured_order or 0))
  return updated
