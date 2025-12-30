"""Story nodes service layer.

Keep FastAPI routers thin by centralizing story node workflows here.
This module should not depend on FastAPI types.

Architecture:
- Internal methods work with entities (StoryNode) for efficient service composition
- Public methods convert entities to DTOs for API layer consumption
"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator

from aws_lambda_powertools import Logger
from pynamodb.pagination import ResultIterator

import app.services.llm as llm
import app.services.pinecone as pinecone
from app.core.config import settings
from app.models.dtos.story_node import ChoiceDTO, StoryNodeDTO, StoryNodeProcessingStatus
from app.models.entities.story_node import StoryNode, StoryNodeContext
from app.services.llm import LLMFactExtractionDeps
from app.services.pinecone import PineconeBranchFact
from app.services.sqs import send_node_analysis_message
from app.services.worlds import get_world_entity

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)


class NodeServiceError(Exception):
  """Base exception for node service failures."""


class NodeProcessingError(NodeServiceError):
  """Raised when a node is not in the COMPLETED state."""

  def __init__(self, node_id: str):
    super().__init__(f"Node {node_id} is not in the COMPLETED state")
    self.node_id = node_id


class NodeNotFoundError(NodeServiceError):
  """Raised when a node cannot be found."""

  def __init__(self, world_id: str, node_id: str):
    super().__init__(f"Node not found: {node_id} in world {world_id}")
    self.world_id = world_id
    self.node_id = node_id


class InvalidChoiceError(NodeServiceError):
  """Raised when a choice index is out of bounds."""

  def __init__(self, node_id: str, choice_index: int, max_index: int):
    super().__init__(f"Invalid choice index {choice_index} for node {node_id}. Valid range: 0-{max_index}")
    self.node_id = node_id
    self.choice_index = choice_index
    self.max_index = max_index


def _get_world_facts(world_id: str, node_text: str, top_k: int = 10) -> list[pinecone.PineconeWorldFact]:
  """Get the world facts for a given node."""
  return [
    pinecone.PineconeWorldFact.model_validate({**x.fields, "id": x._id})
    for x in pinecone.search_records(  # type: ignore[reportUnknownReturnType]
      query=node_text,
      top_k=top_k,
      filter={
        "world_id": {"$eq": world_id},
        "entity_type": {"$eq": pinecone.EntityType.WORLD_FACT},
      },
    ).result.hits
  ]


def _get_branch_facts(
  world_id: str, node_text: str, ancestors: list[str], top_k: int = 10
) -> list[pinecone.PineconeBranchFact]:
  """Get the branch facts for a given node."""

  results: list[PineconeBranchFact] = [
    pinecone.PineconeBranchFact.model_validate({**x.fields, "id": x._id})
    for x in pinecone.search_records(  # type: ignore[reportUnknownReturnType]
      query=node_text,
      top_k=top_k,
      filter={
        "world_id": {"$eq": world_id},
        "entity_type": {"$eq": pinecone.EntityType.BRANCH_FACT},
        "origin_node_id": {"$in": ancestors},
      },
    ).result.hits
  ]

  return sorted(results, key=lambda x: -ancestors.index(x.origin_node_id))


def _get_similar_nodes(world_id: str, node_text: str, top_k: int = 3) -> list[pinecone.PineconeRecord]:
  """Get the similar story nodes for a given node."""
  return [
    pinecone.PineconeRecord.model_validate({**x.fields, "id": x._id})
    for x in pinecone.search_records(  # type: ignore[reportUnknownReturnType]
      query=node_text,
      top_k=top_k,
      filter={
        "world_id": {"$eq": world_id},
        "entity_type": {"$eq": pinecone.EntityType.NODE_TEXT},
      },
    ).result.hits
  ]


def _node_keys(world_id: str, node_id: str) -> tuple[str, str]:
  """Generate PynamoDB primary and sort keys for a node ID."""
  return (StoryNode.pk(world_id), StoryNode.sk(node_id))


def _get_node_entity(world_id: str, node_id: str) -> StoryNode:
  """Internal: fetch node entity for service composition."""
  pk, sk = _node_keys(world_id, node_id)
  try:
    return StoryNode.get(pk, sk)
  except StoryNode.DoesNotExist as e:  # type: ignore[reportGeneralTypeIssues]
    raise NodeNotFoundError(world_id, node_id) from e


def list_nodes(world_id: str) -> list[StoryNodeDTO]:
  """Return all nodes for a given world."""
  pk = StoryNode.gsi2_pk(world_id)
  nodes: ResultIterator[StoryNode] = StoryNode.GSI2.query(hash_key=pk, page_size=100)  # type: ignore[reportUnknownReturnType]
  return [node.to_dto() for node in nodes]


def get_node(world_id: str, node_id: str) -> StoryNodeDTO:
  """Fetch a single node by identifier."""
  node = _get_node_entity(world_id, node_id)
  return node.to_dto()


async def choose(world_id: str, node_id: str, choice_index: int) -> AsyncGenerator[str, None]:
  """Choose a story node based on a user's choice and stream the text.

  Workflow:
  1. Fetch parent node and validate choice_index bounds
  2. Fetch world metadata for context
  3. Build LLMNextNodeDeps
  4. Call llm.get_next_node_agent().run_stream to stream new node content
  5. Create and save new StoryNode entity after stream finishes
  6. Update parent node and send analysis message
  """
  # Step 1: Fetch parent node and validate choice index
  node = _get_node_entity(world_id, node_id)

  if not node.choices or choice_index < 0 or choice_index >= len(node.choices):
    max_index = len(node.choices) - 1 if node.choices else -1
    raise InvalidChoiceError(node_id, choice_index, max_index)

  if node.processing_status != StoryNodeProcessingStatus.COMPLETED:
    # Wait for node to be completed for 5 seconds prior to raising an error
    await asyncio.sleep(5)
    node = _get_node_entity(world_id, node_id)
    if node.processing_status != StoryNodeProcessingStatus.COMPLETED:
      raise NodeProcessingError(node_id)

  selected_choice = node.choices[choice_index]

  if selected_choice.target:
    target_node = _get_node_entity(world_id, selected_choice.target)
    yield target_node.text
    return

  # Step 2: Fetch world metadata for context
  world_meta = get_world_entity(world_id)

  # Step 3: Build LLMNextNodeDeps
  llm_world_info = llm.LLMWorldInfo(
    story_title=world_meta.title or "",
    story_description=world_meta.description or "",
    setting=world_meta.setting or "",
    potential_endings=world_meta.potential_endings or [],  # type: ignore[arg-type]
  )

  deps = llm.LLMNextNodeDeps(
    world_info=llm_world_info,
    previous_text=node.text,
    user_choice=selected_choice.label,
    world_facts=node.context.world_facts or [],  # type: ignore[arg-type]
    branch_facts=node.context.branch_facts or [],  # type: ignore[arg-type]
    similar_nodes=node.context.similar_nodes or [],  # type: ignore[arg-type]
    narrator_profile=world_meta.narrator_profile or "",
  )

  # Step 4: Stream next node content via LLM
  new_node_id = node.get_child_id(choice_index)
  generated_node: llm.LLMStoryNode | None = None

  async with llm.get_next_node_agent().run_stream(llm.build_next_node_prompt(deps), deps=deps) as result:
    last_text = ""
    async for partial in result.stream_output(debounce_by=None):
      if partial.text and len(partial.text) > len(last_text):
        new_text = partial.text[len(last_text) :]
        yield new_text
        last_text = partial.text
      generated_node = partial

  if not generated_node:
    # Should not happen in normal circumstances with pydantic-ai
    return

  # Step 5: Create new StoryNode entity
  new_node_dto = StoryNodeDTO(
    id=new_node_id,
    world_id=world_id,
    text=generated_node.text,
    story_summary=generated_node.story_summary,
    title=generated_node.title,
    choices=[ChoiceDTO(label=choice, target=None) for choice in generated_node.choices],
    processing_status=StoryNodeProcessingStatus.PENDING,
  )

  # Step 6: Save new node
  new_node = StoryNode.from_dto(new_node_dto)
  new_node.save()

  # Step 7: Update parent node's choice target
  node.choices[choice_index].target = new_node_id

  # Step 8: Save updated parent
  node.save()

  # Step 9: Send node analysis message
  send_node_analysis_message(world_id, new_node_id)


async def process_node(world_id: str, node_id: str):
  node: StoryNode = _get_node_entity(world_id, node_id)
  if node.processing_status == StoryNodeProcessingStatus.COMPLETED:
    return
  try:
    node.processing_status = StoryNodeProcessingStatus.PROCESSING
    node.save()
    parent_node: StoryNode | None = _get_node_entity(world_id, node.parent_id) if node.parent_id else None
    world_facts: list[pinecone.PineconeWorldFact] = []
    branch_facts: list[pinecone.PineconeBranchFact] = []
    similar_nodes: list[pinecone.PineconeRecord] = []
    try:
      world_facts = _get_world_facts(world_id, node.text)
      branch_facts = _get_branch_facts(world_id, node.text, node.ancestors)
      similar_nodes = _get_similar_nodes(world_id, node.text)

    except Exception as e:
      logger.error(f"Error searching Pinecone for facts: {e}")

    world_facts_text: list[str] = [fact.text for fact in world_facts]
    branch_facts_text: list[str] = [fact.text for fact in branch_facts]
    similar_nodes_text: list[str] = [node.text for node in similar_nodes]

    node_context: StoryNodeContext = StoryNodeContext(
      world_facts=world_facts_text,
      branch_facts=branch_facts_text,
      similar_nodes=similar_nodes_text,
    )

    node.context = node_context

    fact_deps: LLMFactExtractionDeps = llm.LLMFactExtractionDeps(
      text=node.text,
      user_choice=parent_node.choices[node.choice_index].label if parent_node and node.choice_index else None,
    )

    facts = await llm.generate_facts_async(fact_deps)
    pinecone.upsert_records(
      [
        pinecone.PineconeRecord.model_validate(
          {
            "id": node.id,
            "text": node.text,
            "entity_type": pinecone.EntityType.NODE_TEXT,
            "world_id": world_id,
          }
        )
      ]
    )
    if facts.world_facts or facts.branch_facts:
      pinecone.upsert_records(
        [
          pinecone.PineconeRecord.model_validate(
            {
              "id": str(uuid.uuid4()),
              "text": fact,
              "entity_type": pinecone.EntityType.WORLD_FACT,
              "world_id": world_id,
            }
          )
          for fact in facts.world_facts
        ]
        + [
          pinecone.PineconeRecord.model_validate(
            {
              "id": str(uuid.uuid4()),
              "text": fact,
              "entity_type": pinecone.EntityType.BRANCH_FACT,
              "world_id": world_id,
              "origin_node_id": node.id,
            }
          )
          for fact in facts.branch_facts
        ]
      )

    node.processing_status = StoryNodeProcessingStatus.COMPLETED
    node.save()
  except Exception as e:
    logger.error(f"Error processing node: {e}")
    node.processing_status = StoryNodeProcessingStatus.FAILED
    node.save()
    raise e
