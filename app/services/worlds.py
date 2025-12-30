"""Worlds service layer.

Keep FastAPI routers thin by centralizing world-related workflows here.
This module should not depend on FastAPI types.

Architecture:
- Internal methods work with entities (WorldMeta) for efficient service composition
- Public methods convert entities to DTOs for API layer consumption
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timezone

from pynamodb.pagination import ResultIterator

import app.services.llm as llm
import app.services.pinecone as pinecone
from app.models.dtos.story_node import ChoiceDTO, StoryNodeDTO, StoryNodeProcessingStatus
from app.models.dtos.world_meta import GenerationStatus, WorldCreateRequest, WorldMetaDTO
from app.models.entities.story_node import StoryNode
from app.models.entities.world_meta import WorldMeta
from app.services.llm import LLMStoryNode
from app.services.sqs import send_world_generation_message


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


def list_worlds(user_id: str) -> list[WorldMetaDTO]:
  """Return a discoverable set of worlds (paged feed TBD)."""
  gsi1_pk = WorldMeta.gsi1_pk(user_id)
  worlds: ResultIterator[WorldMeta] = WorldMeta.GSI1.query(hash_key=gsi1_pk)  # type: ignore[reportUnknownReturnType]
  return [world.to_dto() for world in worlds]


def get_world(world_id: str) -> WorldMetaDTO:
  """Fetch a single world by identifier."""
  world_meta = get_world_entity(world_id)
  return world_meta.to_dto()


def create_world(create_request: WorldCreateRequest, user_id: str) -> WorldMetaDTO:
  """Create a new world metadata record.

  Expects a mapping aligned with the `WorldMeta` attributes.
  """

  world_id = str(uuid.uuid4())

  meta = WorldMetaDTO(
    id=world_id,
    author_id=user_id,
    **create_request.model_dump(exclude_unset=True),
    generation_status=GenerationStatus.GENERATING_LORE,
    created_at=datetime.now(timezone.utc).isoformat(),
    updated_at=datetime.now(timezone.utc).isoformat(),
  )

  meta = WorldMeta.from_dto(meta)

  meta.save()
  send_world_generation_message(world_id)
  return meta.to_dto()


def update_world(world_id: str, payload: Mapping[str, object]) -> WorldMetaDTO:
  """Apply partial updates to an existing world.

  NOTE: Intentionally scaffolded. We'll likely implement this using PynamoDB
  `update()` or an optimistic-write pattern with `updated_at`.
  """

  raise NotImplementedError("World updates are not implemented yet.")


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

  # Reuse internal entity fetch to avoid unnecessary conversions
  if world.generation_status != GenerationStatus.GENERATING_LORE:
    raise WorldServiceError(f"World {world.id} is not in the GENERATING_LORE state")

  world.generation_status = GenerationStatus.GENERATING_NARRATOR_PROFILE
  llm_world_info: llm.LLMWorldInfo = await llm.generate_world_info(world.world_prompt)
  world.title = llm_world_info.story_title
  world.description = llm_world_info.story_description
  world.setting = llm_world_info.setting
  world.potential_endings = llm_world_info.potential_endings or []  # type: ignore[arg-type]
  world.save()

  return world


async def generate_narrator_profile(world: WorldMeta) -> WorldMeta:
  """Generate a narrator profile for a world."""

  if world.generation_status != GenerationStatus.GENERATING_NARRATOR_PROFILE:
    raise WorldServiceError(f"World {world.id} is not in the GENERATING_NARRATOR_PROFILE state")

  if world.narrator_profile:
    return world
  llm_world_info = llm.LLMWorldInfo(
    story_title=world.title,
    story_description=world.description,
    setting=world.setting,
    potential_endings=world.potential_endings or [],  # type: ignore[arg-type]
  )
  narrator_profile = await llm.generate_narrator_profile(llm_world_info)
  world.narrator_profile = narrator_profile.narrator_profile
  world.generation_status = GenerationStatus.GENERATING_START_NODE
  world.save()
  return world


async def generate_start_node(world: WorldMeta) -> WorldMeta:
  """Generate the first story node for a world."""

  if world.generation_status != GenerationStatus.GENERATING_START_NODE:
    raise WorldServiceError(f"World {world.id} is not in the GENERATING_START_NODE state")

  llm_world_info = llm.LLMWorldInfo(
    story_title=world.title,
    story_description=world.description,
    setting=world.setting,
    potential_endings=world.potential_endings or [],  # type: ignore[arg-type]
  )

  deps = llm.LLMRootNodeDeps(
    world_info=llm_world_info,
    narrator_profile=world.narrator_profile or "",
  )

  generated_node: LLMStoryNode = await llm.generate_start_node(deps)
  root_node_id = "0"
  story_node_dto = StoryNodeDTO(
    id=root_node_id,
    world_id=world.id,
    text=generated_node.text,
    story_summary=generated_node.story_summary,
    title=generated_node.title,
    choices=[ChoiceDTO(label=choice, target=None) for choice in generated_node.choices],
    processing_status=StoryNodeProcessingStatus.PENDING,  # type: ignore[arg-type]
  )
  story_node = StoryNode.from_dto(story_node_dto)
  story_node.save()
  world.root_node_id = story_node.id
  world.generation_status = GenerationStatus.COMPLETED
  world.save()

  return world
