"""Worlds service layer.

Keep FastAPI routers thin by centralizing world-related workflows here.
This module should not depend on FastAPI types.

Architecture:
- All methods work with entities (WorldMeta) for efficient service composition
- API layer converts entities to DTOs for external consumption
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from pynamodb.transactions import TransactWrite

import app.services.llm as llm
import app.services.pinecone as pinecone
from app.core.config import WORLD_LENGTH_MAX_NODES
from app.core.errors import NotFoundError
from app.core.observability import MetricUnit, logger, metrics, tracer
from app.models.dtos.story_node import (
  NodeGenerationStatus,
  StoryNodeDTO,
  StoryNodeProcessingStatus,
)
from app.models.dtos.world_meta import (
  ImageGenerationStatus,
  WorldCreateRequest,
  WorldGenerationStatus,
  WorldMetaDTO,
  WorldVisibility,
)
from app.models.entities.session_membership import SessionMembership
from app.models.entities.story_node import StoryNode
from app.models.entities.user import UserRecord
from app.models.entities.world_meta import Character, Location, WorldMeta
from app.models.entities.world_session import WorldSession
from app.services import playlists
from app.services.llm.sanitize import sanitize_user_input
from app.services.s3 import delete_objects_by_prefix
from app.services.sessions import build_session_items, delete_session
from app.services.sqs import send_world_generation_message
from app.services.usage import (
  check_and_increment,
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
  except WorldMeta.DoesNotExist as e:
    raise WorldNotFoundError(world_id) from e


def _increment_world_count(user_id: str) -> None:
  """Atomically increment the saved_world_count tracker."""
  record = get_or_create_usage(user_id)
  swc = UserRecord.usage.saved_world_count
  record.update(
    actions=[swc.set((swc | 0) + 1)],  # type: ignore  # PynamoDB expression builder
  )


def _decrement_world_count(user_id: str) -> None:
  """Best-effort decrement of the saved world counter after deletion or failed creation."""
  try:
    record = get_or_create_usage(user_id)
    swc = UserRecord.usage.saved_world_count
    record.update(
      actions=[swc.set((swc | 0) - 1)],  # type: ignore  # PynamoDB expression builder
      condition=(swc > 0),  # type: ignore  # PynamoDB condition expression
    )
  except Exception:
    logger.warning(f"Failed to decrement world count for user {user_id}")


@tracer.capture_method
def create_world(create_request: WorldCreateRequest, user_id: str) -> tuple[WorldMeta, WorldSession]:
  """Create a new world metadata record and owner session.

  Expects a mapping aligned with the ``WorldMeta`` attributes.
  Raises ``QuotaExceededError`` if the user has reached their periodic world-creation limit.
  """

  # 1. Track the saved world count (no limit enforced).
  _increment_world_count(user_id)

  # 2. Atomically reserve a periodic slot (worlds created this billing period).
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
      generation_status=WorldGenerationStatus.INITIALIZED,
      story_max_nodes=max_nodes,
      world_length=create_request.world_length.value,
      vocab_level=create_request.vocab_level.value,
      content_filter=create_request.content_filter.value,
      max_choices=create_request.max_choices,
    )

    meta = WorldMeta.from_dto(meta_dto)
    meta._prepare_for_save()
    session, memberships = build_session_items(world_id, user_id, [user_id], meta)
    session._prepare_for_save()
    for membership in memberships:
      membership._prepare_for_save()

    with TransactWrite(connection=WorldMeta._get_connection()) as transaction:
      transaction.save(meta, condition=WorldMeta.PK.does_not_exist())
      transaction.save(session, condition=WorldSession.PK.does_not_exist())
      for membership in memberships:
        transaction.save(membership, condition=SessionMembership.PK.does_not_exist())
  except Exception:
    _decrement_world_count(user_id)
    release_quota(user_id, "worlds")
    raise

  try:
    send_world_generation_message(world_id)
  except Exception:
    logger.exception("Failed to enqueue generation for world %s; cleaning up created world/session", world_id)
    try:
      for membership in memberships:
        membership.delete()
      delete_session(str(session.id))
      hard_delete_orphaned_world(world_id)
    except Exception:
      logger.exception("Failed to clean up world %s after enqueue failure", world_id)
    release_quota(user_id, "worlds")
    raise

  metrics.add_metric(name="WorldCreated", unit=MetricUnit.Count, value=1)

  return meta, session


def update_world(world_id: str, payload: BaseModel) -> WorldMeta:
  """Apply partial updates to an existing world.

  Accepts either a ``WorldUpdateRequest`` (user-facing, restricted field set)
  or a ``WorldMetaDTO`` (internal/admin, full field set).  Only fields
  present on the *payload model* and non-None are applied; immutable fields
  are always skipped.
  """
  world = get_world_entity(world_id)

  immutable_fields = {"id", "author_id", "created_at", "updated_at", "visibility"}

  converters: dict[str, Callable[[Any], Any]] = {
    "visibility": lambda v: WorldVisibility(v).value,
    "generation_status": lambda v: WorldGenerationStatus(v).value,
    "image_generation_status": lambda v: ImageGenerationStatus(v).value,
    "characters": lambda v: [Character.from_dto(c) for c in v],
    "locations": lambda v: [Location.from_dto(loc) for loc in v],
  }

  for field_name in payload.model_fields:
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
def hard_delete_orphaned_world(world_id: str) -> None:
  """Hard-delete a world that has zero remaining sessions.

  Called after orphan detection (all sessions removed) and during
  account deletion (GDPR right to erasure).  All cleanup operations
  are idempotent, so concurrent calls are safe.
  """
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError:
    logger.info("World %s already deleted (idempotent)", world_id)
    return

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
    delete_objects_by_prefix(f"worlds/{world_id}/")
    delete_objects_by_prefix(f"audio/{world_id}/")
  except Exception:
    logger.exception("Failed to delete S3 objects for world %s (non-fatal)", world_id)

  if author_id:
    _decrement_world_count(author_id)


@tracer.capture_method
async def generate_lore(world: WorldMeta) -> WorldMeta:
  """Generate lore for a world via LLM."""

  llm_world_info: llm.LLMWorldInfo = await llm.generate_world_info(
    world.world_prompt, vocab_level=world.vocab_level, content_filter=world.content_filter
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
  world.potential_endings = llm_world_info.endings or []  # type: ignore  # PynamoDB ListAttribute accepts list[str]

  try:
    playlist = playlists.generate_playlist(
      soundtrack_description=llm_world_info.soundtrack_description,
      content_filter=world.content_filter,
    )
    if playlist:
      world.default_playlist_id = str(playlist.id)
  except Exception:
    logger.warning("Soundtrack playlist generation failed for world %s (non-fatal)", world.id, exc_info=True)

  return world


@tracer.capture_method
async def generate_narrator_profile(world: WorldMeta) -> WorldMeta:
  """Generate a narrator profile for a world."""

  if world.narrator_profile:
    return world
  llm_world_info = world_meta_to_llm_world_info(world)
  narrator_profile = await llm.generate_narrator_profile(
    llm_world_info, vocab_level=world.vocab_level, content_filter=world.content_filter
  )
  world.narrator_profile = narrator_profile.narrator_profile
  return world


@tracer.capture_method
def list_featured_worlds() -> list[WorldMeta]:
  """Return featured worlds with public visibility, ordered by featured_order ascending."""
  results = list(
    WorldMeta.GSI2.query(
      hash_key=WorldMeta.gsi2_pk_featured(),
      scan_index_forward=True,
    )
  )
  return [w for w in results if w.visibility == WorldVisibility.PUBLIC.value]


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
