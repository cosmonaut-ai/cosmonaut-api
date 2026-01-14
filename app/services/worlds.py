"""Worlds service layer.

Keep FastAPI routers thin by centralizing world-related workflows here.
This module should not depend on FastAPI types.

Architecture:
- All methods work with entities (WorldMeta) for efficient service composition
- API layer converts entities to DTOs for external consumption
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from aws_lambda_powertools import Logger
from pynamodb.pagination import ResultIterator

import app.services.llm as llm
import app.services.pinecone as pinecone
from app.core.config import settings
from app.models.dtos.story_node import StoryNodeDTO, StoryNodeProcessingStatus
from app.models.dtos.world_meta import (
  CharacterDTO,
  GenerationStatus,
  LocationDTO,
  WorldCreateRequest,
  WorldMetaDTO,
  WorldVisibility,
)
from app.models.entities.story_node import ChoiceMap, StoryNode
from app.models.entities.world_meta import Character, Location, WorldMeta
from app.services.sqs import send_world_generation_message

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)


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
    endings=world.potential_endings or [],  # type: ignore[arg-type]
    characters=[
      llm.LLMCharacter(
        name=character.name or "",
        description=character.description or "",
        relationships=character.relationships or [],  # type: ignore[arg-type]
      )
      for character in world.characters
    ],
    locations=[
      llm.LLMLocation(
        name=location.name or "",
        description=location.description or "",
        connections=location.connections or [],  # type: ignore[arg-type]
      )
      for location in world.locations
    ],
    genre=world.genre or "",
  )


class WorldServiceError(Exception):
  """Base exception for world service failures."""


class WorldNotFoundError(WorldServiceError):
  """Raised when a world cannot be found."""

  def __init__(self, world_id: str):
    super().__init__(f"World not found: {world_id}")
    self.world_id = world_id


def _world_keys(world_id: str) -> tuple[str, str]:
  """Generate PynamoDB primary and sort keys for a world ID."""
  return (WorldMeta.pk(world_id), WorldMeta.sk())


def get_world_entity(world_id: str) -> WorldMeta:
  """Fetch world entity for service composition (public for cross-service use)."""
  pk, sk = _world_keys(world_id)
  try:
    return WorldMeta.get(pk, sk)
  except WorldMeta.DoesNotExist as e:  # type: ignore[reportGeneralTypeIssues]
    raise WorldNotFoundError(world_id) from e


def list_worlds(user_id: str) -> list[WorldMeta]:
  """Return a discoverable set of worlds (paged feed TBD)."""
  gsi1_pk = WorldMeta.gsi1_pk(user_id)
  worlds: ResultIterator[WorldMeta] = WorldMeta.GSI1.query(hash_key=gsi1_pk, scan_index_forward=False)  # type: ignore[reportUnknownReturnType]
  return list(worlds)


def get_world(world_id: str) -> WorldMeta:
  """Fetch a single world by identifier."""
  return get_world_entity(world_id)


def create_world(create_request: WorldCreateRequest, user_id: str) -> WorldMeta:
  """Create a new world metadata record.

  Expects a mapping aligned with the `WorldMeta` attributes.
  """

  world_id = str(uuid.uuid4())

  meta_dto = WorldMetaDTO(
    id=world_id,
    author_id=user_id,
    visibility=create_request.visibility,
    world_prompt=create_request.world_prompt,
    generation_status=GenerationStatus.INITIALIZED,
    created_at=datetime.now(timezone.utc).isoformat(),
    updated_at=datetime.now(timezone.utc).isoformat(),
    story_max_nodes=20,
  )

  meta = WorldMeta.from_dto(meta_dto)

  meta.save()
  send_world_generation_message(world_id)
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
      # PynamoDB ListAttribute accepts regular lists, but type checker doesn't know
      if field_name in ("potential_endings", "shared_with"):
        setattr(world, field_name, converted_value)  # type: ignore[arg-type]
      else:
        setattr(world, field_name, converted_value)

  # Always update timestamp and GSI1SK
  world.updated_at = datetime.now(timezone.utc)
  if world.author_id:
    world.GSI1SK = WorldMeta.gsi1_sk(world.updated_at.isoformat())

  world.save()
  return world


def delete_world(world_id: str) -> None:
  """Delete a world and any associated state (implementation pending)."""

  pk, _sk = _world_keys(world_id)
  items = WorldMeta.query(pk)
  with WorldMeta.batch_write() as batch:
    for item in items:
      batch.delete(item)

  # Delete all records from Pinecone
  pinecone.delete_records(filter={"world_id": world_id})


async def generate_lore(world: WorldMeta) -> WorldMeta:
  """Generate lore for a world (LLM integration TBD)."""

  llm_world_info: llm.LLMWorldInfo = await llm.generate_world_info(world.world_prompt)
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
  world.potential_endings = llm_world_info.endings or []  # type: ignore[arg-type]

  return world


async def generate_narrator_profile(world: WorldMeta) -> WorldMeta:
  """Generate a narrator profile for a world."""

  if world.narrator_profile:
    return world
  llm_world_info = world_meta_to_llm_world_info(world)
  narrator_profile = await llm.generate_narrator_profile(llm_world_info)
  world.narrator_profile = narrator_profile.narrator_profile
  return world


async def generate_start_node(world: WorldMeta) -> StoryNode:
  """Generate the first story node for a world."""

  llm_world_info = world_meta_to_llm_world_info(world)

  deps = llm.RootNodeDeps(
    world_info=llm_world_info,
    narrator_profile=world.narrator_profile or "",
  )

  generated_node = await llm.generate_start_node(deps)
  root_node_id = "0"
  story_node_dto = StoryNodeDTO(
    id=root_node_id,
    world_id=world.id,
    text=generated_node.text,
    story_summary=generated_node.story_summary,
    title=generated_node.title,
    processing_status=StoryNodeProcessingStatus.PENDING,  # type: ignore[arg-type]
  )
  story_node = StoryNode.from_dto(story_node_dto)
  for i, choice in enumerate(generated_node.choices):
    story_node.choices.append(
      ChoiceMap(
        label=choice.label,
        target=story_node.get_child_id(i),
        outcome=choice.outcome,
        is_created=False,
        is_custom=False,
        creator=None,
      )
    )
  story_node.save()
  world.root_node_id = story_node.id
  world.save()
  logger.info(f"Root node {root_node_id} generated for world {world.id}")

  return story_node
