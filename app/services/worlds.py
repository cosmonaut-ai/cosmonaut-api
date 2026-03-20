"""Worlds service layer.

Keep FastAPI routers thin by centralizing world-related workflows here.
This module should not depend on FastAPI types.

Architecture:
- All methods work with entities (WorldMeta) for efficient service composition
- API layer converts entities to DTOs for external consumption
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from typing import Any

from pynamodb.exceptions import UpdateError
from pynamodb.pagination import ResultIterator

import app.services.llm as llm
import app.services.pinecone as pinecone
from app.core.config import WORLD_LENGTH_MAX_NODES
from app.core.errors import NotFoundError
from app.core.observability import MetricUnit, logger, metrics, tracer
from app.models.dtos.story_node import (
  GenerationStatus as NodeGenerationStatus,
)
from app.models.dtos.story_node import (
  StoryNodeDTO,
  StoryNodeProcessingStatus,
)
from app.models.dtos.world_meta import (
  CharacterDTO,
  GenerationStatus,
  LocationDTO,
  WorldCreateRequest,
  WorldMetaDTO,
  WorldVisibility,
)
from app.models.entities.story_node import StoryNode
from app.models.entities.usage import UserUsage
from app.models.entities.world_meta import Character, Location, WorldMeta
from app.services.llm.sanitize import sanitize_user_input
from app.services.sessions import create_session, delete_sessions_for_world
from app.services.sqs import send_world_generation_message
from app.services.usage import (
  StorageQuotaExceededError,
  check_and_increment,
  check_storage_quota,
  get_or_create_usage,
  release_quota,
)

# =============================================================================
# Entity to LLM Model Conversion
# =============================================================================


def world_meta_to_llm_world_info(world: WorldMeta) -> llm.LLMWorldInfo:
  """Convert a WorldMeta entity to LLMWorldInfo for LLM agents.

  This removes the circular dependency between the entity and the llm module.
  """
  return llm.LLMWorldInfo(
    title=world.title or "",
    description=world.description or "",
    setting=world.setting or "",
    backstory=world.narrative_context or "",
    endings=[str(e) for e in world.potential_endings] if world.potential_endings else [],
    characters=[
      llm.LLMCharacter(
        name=character.name or "",
        description=character.description or "",
        relationships=[str(r) for r in character.relationships] if character.relationships else [],
      )
      for character in world.characters
    ],
    locations=[
      llm.LLMLocation(
        name=location.name or "",
        description=location.description or "",
        connections=[str(c) for c in location.connections] if location.connections else [],
      )
      for location in world.locations
    ],
    genre=world.genre or "",
  )


class WorldServiceError(Exception):
  """Base exception for world service failures."""


class WorldNotFoundError(WorldServiceError, NotFoundError):
  """Raised when a world cannot be found."""

  def __init__(self, world_id: str):
    super().__init__(f"World not found: {world_id}")
    self.world_id = world_id


def _world_keys(world_id: str) -> tuple[str, str]:
  """Generate PynamoDB primary and sort keys for a world ID."""
  return (WorldMeta.pk(world_id), WorldMeta.sk())


@tracer.capture_method
def get_world_entity(world_id: str) -> WorldMeta:
  """Fetch world entity for service composition (public for cross-service use)."""
  pk, sk = _world_keys(world_id)
  try:
    return WorldMeta.get(pk, sk)
  except WorldMeta.DoesNotExist as e:  # type: ignore[reportGeneralTypeIssues]
    raise WorldNotFoundError(world_id) from e


def count_user_worlds(user_id: str) -> int:
  """Count the total number of worlds owned by a user (GSI1 count query)."""
  gsi1_pk = WorldMeta.gsi1_pk(user_id)
  return WorldMeta.GSI1.count(hash_key=gsi1_pk)  # type: ignore[reportUnknownMemberType]


@tracer.capture_method
def list_worlds(
  user_id: str,
  limit: int = 50,
  cursor: str | None = None,
) -> tuple[list[WorldMeta], str | None]:
  """Return worlds for a user with cursor-based pagination, ordered by most recent first.

  Args:
    user_id: Owner identifier.
    limit: Maximum number of worlds to return per page (default 50).
    cursor: Opaque pagination token from a previous response.

  Returns:
    Tuple of (worlds, next_cursor). ``next_cursor`` is None when there are
    no more pages.
  """
  import base64
  import json

  gsi1_pk = WorldMeta.gsi1_pk(user_id)

  last_evaluated_key = None
  if cursor:
    with contextlib.suppress(Exception):
      last_evaluated_key = json.loads(base64.urlsafe_b64decode(cursor))

  worlds: ResultIterator[WorldMeta] = WorldMeta.GSI1.query(  # type: ignore[reportUnknownReturnType]
    hash_key=gsi1_pk,
    scan_index_forward=False,
    page_size=limit,
    limit=limit,
    last_evaluated_key=last_evaluated_key,
  )
  items = list(worlds)

  next_cursor: str | None = None
  if worlds.last_evaluated_key:
    next_cursor = base64.urlsafe_b64encode(json.dumps(worlds.last_evaluated_key).encode()).decode()

  return items, next_cursor


def get_world(world_id: str) -> WorldMeta:
  """Fetch a single world by identifier."""
  return get_world_entity(world_id)


def _increment_world_count(user_id: str) -> None:
  """Atomically increment the saved_world_count with a limit check.

  Uses a DynamoDB conditional update to prevent the race condition where
  two concurrent requests both pass the quota check.

  Raises ``StorageQuotaExceededError`` when the user is at capacity.
  """
  from app.core.config import get_tier_limits

  usage = get_or_create_usage(user_id)
  tier = str(usage.tier) if usage.tier else "FREE"
  saved_worlds_limit: int = get_tier_limits(tier)["saved_worlds"]

  try:
    usage.update(
      actions=[UserUsage.saved_world_count.set((UserUsage.saved_world_count | 0) + 1)],
      condition=(UserUsage.saved_world_count < saved_worlds_limit) | UserUsage.saved_world_count.does_not_exist(),
    )
  except UpdateError:
    count = int(usage.saved_world_count) if usage.saved_world_count else 0
    raise StorageQuotaExceededError("saved_worlds", saved_worlds_limit, count) from None


def _decrement_world_count(user_id: str) -> None:
  """Best-effort decrement of the saved world counter after deletion or failed creation."""
  try:
    usage = get_or_create_usage(user_id)
    usage.update(
      actions=[UserUsage.saved_world_count.set((UserUsage.saved_world_count | 0) - 1)],
      condition=(UserUsage.saved_world_count > 0),
    )
  except Exception:
    logger.warning(f"Failed to decrement world count for user {user_id}")


@tracer.capture_method
def create_world(create_request: WorldCreateRequest, user_id: str) -> WorldMeta:
  """Create a new world metadata record.

  Expects a mapping aligned with the `WorldMeta` attributes.
  Raises ``StorageQuotaExceededError`` if the user has reached their saved-worlds cap.
  Raises ``QuotaExceededError`` if the user has reached their periodic world-creation limit.
  """

  # 1. Optimistic pre-check for fast rejection (non-atomic).
  check_storage_quota(user_id)

  # 2. Atomically increment saved_world_count with a condition check.
  #    This is the actual enforcement that prevents the race condition.
  _increment_world_count(user_id)

  # 3. Atomically reserve a periodic slot (worlds created this billing period).
  #    Released below if the actual creation fails.
  try:
    check_and_increment(user_id, "worlds")
  except Exception:
    _decrement_world_count(user_id)
    raise

  try:
    world_id = str(uuid.uuid4())

    max_nodes = WORLD_LENGTH_MAX_NODES[create_request.world_length.value]
    sanitized_prompt = sanitize_user_input(create_request.world_prompt)

    meta_dto = WorldMetaDTO(
      id=world_id,
      author_id=user_id,
      visibility=create_request.visibility,
      world_prompt=sanitized_prompt,
      generation_status=GenerationStatus.INITIALIZED,
      story_max_nodes=max_nodes,
      world_length=create_request.world_length.value,
      family_friendly=create_request.family_friendly,
    )

    meta = WorldMeta.from_dto(meta_dto)

    meta.save()
    send_world_generation_message(world_id)
  except Exception:
    _decrement_world_count(user_id)
    release_quota(user_id, "worlds")
    raise

  metrics.add_metric(name="WorldCreated", unit=MetricUnit.Count, value=1)

  try:
    create_session(root_world_id=world_id, creator_id=user_id, members=[user_id], world=meta)
  except Exception:
    logger.warning("Failed to create session for world %s (non-fatal)", world_id, exc_info=True)

  return meta


def update_world(world_id: str, payload: WorldMetaDTO) -> WorldMeta:
  """Apply partial updates to an existing world.

  Only fields explicitly provided (non-None) in the payload are updated.
  Immutable fields (id, author_id, created_at, updated_at) are ignored.
  """
  world = get_world_entity(world_id)

  # Fields that should never be updated via this endpoint
  immutable_fields = {"id", "author_id", "created_at", "updated_at"}

  # Field converters: maps DTO field name to a converter function
  def convert_visibility(v: WorldVisibility) -> str:
    return WorldVisibility(v).value

  def convert_generation_status(v: GenerationStatus) -> str:
    return GenerationStatus(v).value

  def convert_characters(v: list[CharacterDTO]) -> list[Character]:
    return [Character.from_dto(c) for c in v]

  def convert_locations(v: list[LocationDTO]) -> list[Location]:
    return [Location.from_dto(loc) for loc in v]

  converters: dict[str, Callable[[Any], Any]] = {
    "visibility": convert_visibility,
    "generation_status": convert_generation_status,
    "characters": convert_characters,
    "locations": convert_locations,
  }

  # Iterate over all payload fields and apply non-None, non-immutable updates
  for field_name in WorldMetaDTO.model_fields:
    if field_name in immutable_fields:
      continue
    value = getattr(payload, field_name)
    if value is not None:
      converter = converters.get(field_name)
      converted_value = converter(value) if converter else value
      setattr(world, field_name, converted_value)

  world.save()
  return world


@tracer.capture_method
def delete_world(world_id: str) -> None:
  """Delete a world and any associated state."""
  world = get_world_entity(world_id)
  author_id = str(world.author_id) if world.author_id else None

  pk, _sk = _world_keys(world_id)
  items = WorldMeta.query(pk)
  with WorldMeta.batch_write() as batch:
    for item in items:
      batch.delete(item)

  try:
    pinecone.delete_records(filter={"world_id": world_id})
  except Exception:
    logger.exception("Failed to delete Pinecone records for world %s (non-fatal)", world_id)

  try:
    delete_sessions_for_world(world_id)
  except Exception:
    logger.warning("Failed to delete sessions for world %s (non-fatal)", world_id, exc_info=True)

  if author_id:
    _decrement_world_count(author_id)


@tracer.capture_method
async def generate_lore(world: WorldMeta) -> WorldMeta:
  """Generate lore for a world via LLM."""

  is_family_friendly = world.family_friendly == "true"
  llm_world_info: llm.LLMWorldInfo = await llm.generate_world_info(
    world.world_prompt, family_friendly=is_family_friendly
  )
  world.title = llm_world_info.title
  world.description = llm_world_info.description
  world.genre = llm_world_info.genre
  world.setting = llm_world_info.setting
  world.narrative_context = llm_world_info.backstory
  world.characters = [
    Character(
      name=character.name,
      description=character.description,
      relationships=character.relationships,
    )
    for character in llm_world_info.characters
  ]
  world.locations = [
    Location(
      name=location.name,
      description=location.description,
      connections=location.connections,
    )
    for location in llm_world_info.locations
  ]
  world.potential_endings = llm_world_info.endings or []  # type: ignore[reportAttributeAccessIssue]  # PynamoDB ListAttribute accepts list[str]

  return world


@tracer.capture_method
async def generate_narrator_profile(world: WorldMeta) -> WorldMeta:
  """Generate a narrator profile for a world."""

  if world.narrator_profile:
    return world
  llm_world_info = world_meta_to_llm_world_info(world)
  is_family_friendly = world.family_friendly == "true"
  narrator_profile = await llm.generate_narrator_profile(llm_world_info, family_friendly=is_family_friendly)
  world.narrator_profile = narrator_profile.narrator_profile
  return world


def initialize_root_node(world: WorldMeta) -> StoryNode:
  """Initialize the root story node for a world without generating text.

  The node is created with generation_status=INITIALIZED. The client
  should call the /generate-text endpoint to stream the story content.
  """
  root_node_id = "0"
  story_node_dto = StoryNodeDTO(
    id=root_node_id,
    world_id=world.id,
    text=None,
    story_summary=None,
    title=None,
    choices=[],
    processing_status=StoryNodeProcessingStatus.PENDING,
    generation_status=NodeGenerationStatus.INITIALIZED,
  )
  story_node = StoryNode.from_dto(story_node_dto)
  story_node.save()

  world.root_node_id = story_node.id
  world.save()
  logger.info(f"Root node {root_node_id} initialized for world {world.id}")

  return story_node
