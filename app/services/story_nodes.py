"""Story nodes service layer.

Keep FastAPI routers thin by centralizing story node workflows here.
This module should not depend on FastAPI types.

Architecture:
- Internal methods work with entities (StoryNode) for efficient service composition
- Public methods convert entities to DTOs for API layer consumption
"""

from __future__ import annotations

import asyncio
import re
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


def _get_world_facts(world_id: str, node_text: str, top_k: int = 20) -> list[pinecone.PineconeWorldFact]:
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
  world_id: str, node_text: str, ancestors: list[str], top_k: int = 20
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


def get_node_entity(world_id: str, node_id: str) -> StoryNode:
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
  node = get_node_entity(world_id, node_id)
  return node.to_dto()


def get_node_entities(world_id: str, node_ids: list[str]) -> list[StoryNode]:
  """Fetch multiple nodes by identifier."""
  nodes = StoryNode.batch_get([(StoryNode.pk(world_id), StoryNode.sk(node_id)) for node_id in node_ids])
  nodes_map = {node.id: node for node in nodes}
  return [nodes_map[node_id] for node_id in node_ids if node_id in nodes_map]


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
  logger.info(f"Choosing node {node_id} with choice index {choice_index}")
  # Step 1: Fetch parent node and validate choice index
  node = get_node_entity(world_id, node_id)
  if not node.choices or choice_index < 0 or choice_index >= len(node.choices):
    max_index = len(node.choices) - 1 if node.choices else -1
    raise InvalidChoiceError(node_id, choice_index, max_index)

  if node.processing_status != StoryNodeProcessingStatus.COMPLETED:
    # Wait for node to be completed for 5 seconds prior to raising an error
    await asyncio.sleep(5)
    node = get_node_entity(world_id, node_id)
    if node.processing_status != StoryNodeProcessingStatus.COMPLETED:
      raise NodeProcessingError(node_id)

  selected_choice = node.choices[choice_index]

  if selected_choice.target:
    logger.info(f"Returning cached node {selected_choice.target} for choice {choice_index}")
    target_node = get_node_entity(world_id, selected_choice.target)
    logger.info(f"Cached node text length: {len(target_node.text)}, preview: {target_node.text[:100]}...")
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

  prev_story_nodes = get_node_entities(world_id, node.ancestors[-5:])
  prev_story_nodes_text = ""
  for i, current_node in enumerate(prev_story_nodes):
    prev_story_nodes_text += current_node.text
    if i < len(prev_story_nodes) - 1:
      # The choice that led to the next node in our list
      next_node = prev_story_nodes[i + 1]
      choice_idx = next_node.choice_index
      if choice_idx is not None and choice_idx < len(current_node.choices):
        choice_text = current_node.choices[choice_idx].label
        prev_story_nodes_text += f'\n\nUser chose: "{choice_text}"\n\n'
    else:
      # For the last node in our list (which is the current node), the choice is the one just selected
      prev_story_nodes_text += f'\n\nUser chose: "{selected_choice.label}"\n\n'

  deps = llm.LLMNextNodeDeps(
    world_info=llm_world_info,
    story_summary=node.story_summary or "The story begins.",
    previous_text=prev_story_nodes_text,
    user_choice=selected_choice.label,
    world_facts=node.context.world_facts or [],  # type: ignore[arg-type]
    branch_facts=node.context.branch_facts or [],  # type: ignore[arg-type]
    narrator_profile=world_meta.narrator_profile or "",
    story_length=node.depth,
  )

  # Step 4: Stream next node content via LLM
  new_node_id = node.get_child_id(choice_index)
  full_response_buffer = ""
  last_emitted_index = 0
  story_started = False
  total_chunks_yielded = 0

  async with llm.get_next_node_agent().run_stream(llm.build_next_node_prompt(deps), deps=deps) as result:
    async for chunk in result.stream_text(delta=True):
      full_response_buffer += chunk

      # Look for content between <story> and </story> (or end of buffer if </story> not yet present)
      # Match everything from <story> to </story> (if present) or end of buffer
      # We want to match up to </story> if it exists, otherwise to the end
      if "</story>" in full_response_buffer:
        story_match = re.search(r"<story>(.*?)</story>", full_response_buffer, re.DOTALL)
      else:
        story_match = re.search(r"<story>(.*)", full_response_buffer, re.DOTALL)
      if story_match:
        if not story_started:
          story_started = True

        full_story_so_far = story_match.group(1)
        if len(full_story_so_far) > last_emitted_index:
          new_text = full_story_so_far[last_emitted_index:]
          total_chunks_yielded += 1
          yield new_text
          last_emitted_index = len(full_story_so_far)

  # Step 5: Parse metadata from the full buffer using Regex
  try:
    meta_match = re.search(r"<metadata>(.*?)</metadata>", full_response_buffer, re.DOTALL)
    if not meta_match:
      logger.error(f"LLM failed to output metadata tags. Full response: {full_response_buffer}")
      raise ValueError("LLM failed to output metadata tags")

    metadata_json = meta_match.group(1).strip()
    # Clean up potential markdown code blocks in metadata
    metadata_json = re.sub(r"^```(?:json)?\s*", "", metadata_json, flags=re.IGNORECASE)
    metadata_json = re.sub(r"\s*```$", "", metadata_json)

    metadata = llm.LLMNodeMetadata.model_validate_json(metadata_json)

    # Extract the story text from the buffer as well
    story_match = re.search(r"<story>(.*?)</story>", full_response_buffer, re.DOTALL)
    story_text = story_match.group(1).strip() if story_match else ""

  except Exception as e:
    logger.error(f"Failed to parse node metadata: {e}")
    # Fallback or Error handling - we might want to still save what we can or raise
    return

  # Step 6: Create new StoryNode entity
  new_node_dto = StoryNodeDTO(
    id=new_node_id,
    world_id=world_id,
    text=story_text,
    story_summary=metadata.story_summary,
    title=metadata.title,
    choices=[ChoiceDTO(label=choice, target=None) for choice in metadata.choices],
    processing_status=StoryNodeProcessingStatus.PENDING,
  )

  # Step 7: Save new node
  new_node = StoryNode.from_dto(new_node_dto)
  new_node.save()

  # Step 8: Update parent node's choice target
  node.choices[choice_index].target = new_node_id

  # Step 9: Save updated parent
  node.save()

  # Step 10: Send node analysis message
  send_node_analysis_message(world_id, new_node_id)


async def process_node(node: StoryNode):
  world_id = node.world_id
  parent_node: StoryNode | None = get_node_entity(world_id, node.parent_id) if node.parent_id else None
  world_facts: list[pinecone.PineconeWorldFact] = []
  branch_facts: list[pinecone.PineconeBranchFact] = []
  similar_nodes: list[pinecone.PineconeRecord] = []
  try:
    world_facts = _get_world_facts(world_id, node.text)
    branch_facts = _get_branch_facts(world_id, node.text, node.ancestors)
    similar_nodes = _get_similar_nodes(world_id, node.text)
  except Exception as e:
    logger.error(f"Error getting facts for node {node.id}: {e}")

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
