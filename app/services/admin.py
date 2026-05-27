"""Admin service helpers for Cognito and DynamoDB-backed dashboard workflows."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.errors import BadRequestError, ExternalServiceError, NotFoundError
from app.core.observability import logger, tracer
from app.models.dtos.admin import (
  AdminCognitoUserDTO,
  AdminFeaturedOrderItem,
  AdminTier,
  AdminUserUsageDTO,
)
from app.models.entities.story_node import StoryNode
from app.models.entities.user import UserRecord
from app.models.entities.world_meta import WorldMeta
from app.services import cognito as cognito_service
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

  client = cast(Any, cognito_service._get_cognito_client())
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

  client = cast(Any, cognito_service._get_cognito_client())
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

  client = cast(Any, cognito_service._get_cognito_client())
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

  client = cast(Any, cognito_service._get_cognito_client())
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


def _offset_from_cursor(cursor: str | None) -> int:
  payload = _cursor_payload(cursor)
  offset = payload.get("offset", 0)
  if isinstance(offset, int):
    return max(offset, 0)
  if isinstance(offset, str) and offset.isdigit():
    return int(offset)
  return 0


def _world_matches_search(world: WorldMeta, search: str | None) -> bool:
  if not search:
    return True

  needle = search.strip().lower()
  if not needle:
    return True

  values = [
    world.id,
    world.title,
    world.author_id,
    world.genre,
    world.visibility,
    world.generation_status,
  ]
  return any(needle in str(value).lower() for value in values if value)


@tracer.capture_method
def list_all_worlds(limit: int, cursor: str | None, search: str | None = None) -> tuple[list[WorldMeta], str | None]:
  """List all world metadata records, sorted by created_at descending.

  There is no current all-worlds-by-created-at index, so this preserves the
  former dashboard behavior with a full admin scan and an offset cursor.
  """
  offset = _offset_from_cursor(cursor)
  meta_condition = WorldMeta.SK == WorldMeta.sk()  # noqa: SIM300 - PynamoDB needs the attribute on the left.
  worlds = list(
    WorldMeta.scan(
      filter_condition=(WorldMeta.PK.startswith("WORLD#") & meta_condition),
    )
  )
  fallback = datetime.min.replace(tzinfo=UTC)
  worlds = [world for world in worlds if _world_matches_search(world, search)]
  worlds.sort(key=lambda world: world.created_at or fallback, reverse=True)

  end = offset + limit
  next_cursor = encode_cursor({"offset": end}) if end < len(worlds) else None
  return worlds[offset:end], next_cursor


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
