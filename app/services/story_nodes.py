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

from aws_lambda_powertools import Logger
from pynamodb.pagination import ResultIterator

import app.services.llm as llm
import app.services.pinecone as pinecone
from app.core.config import settings
from app.models.dtos.story_node import ChoiceDTO, StoryNodeDTO
from app.models.entities.story_node import StoryNode
from app.services.pinecone import PineconeBranchFact
from app.services.worlds import get_world_entity

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)


class NodeServiceError(Exception):
  """Base exception for node service failures."""


class NodeNotFoundError(NodeServiceError):
  """Raised when a node cannot be found."""

  def __init__(self, world_id: str, node_id: str):
    super().__init__(f"Node not found: {node_id} in world {world_id}")
    self.world_id = world_id
    self.node_id = node_id


class InvalidChoiceError(NodeServiceError):
  """Raised when a choice index is out of bounds."""

  def __init__(self, node_id: str, choice_index: int, max_index: int):
    super().__init__(
      f"Invalid choice index {choice_index} for node {node_id}. Valid range: 0-{max_index}"
    )
    self.node_id = node_id
    self.choice_index = choice_index
    self.max_index = max_index


def _get_world_facts(
  world_id: str, node_text: str, top_k: int = 10
) -> list[pinecone.PineconeWorldFact]:
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


def _get_similar_nodes(
  world_id: str, node_text: str, top_k: int = 3
) -> list[pinecone.PineconeRecord]:
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


async def choose_and_generate(world_id: str, node_id: str, choice_index: int) -> StoryNodeDTO:
  """Generate a new story node based on a user's choice.

  Workflow:
  1. Fetch parent node and validate choice_index bounds
  2. Fetch world metadata for context
  3. Build LLMNextNodeDeps with world_info, previous node text, user choice
  4. Call llm.generate_next_node to get new node content
  5. Create new StoryNode entity with generated UUID, parent_id, ancestors
  6. Save new node to DynamoDB
  7. Update parent node's choices[choice_index].target to new node ID
  8. Save updated parent node
  9. Return new node as DTO
  """
  # Step 1: Fetch parent node and validate choice index
  parent_node = _get_node_entity(world_id, node_id)

  if not parent_node.choices or choice_index < 0 or choice_index >= len(parent_node.choices):
    max_index = len(parent_node.choices) - 1 if parent_node.choices else -1
    raise InvalidChoiceError(node_id, choice_index, max_index)

  selected_choice = parent_node.choices[choice_index]

  if selected_choice.target:
    return get_node(world_id, selected_choice.target)

  # Step 2: Fetch world metadata for context
  world_meta = get_world_entity(world_id)

  # Step 3: Build LLMNextNodeDeps
  llm_world_info = llm.LLMWorldInfo(
    story_title=world_meta.title or "",
    story_description=world_meta.description or "",
    setting=world_meta.setting or "",
    potential_endings=world_meta.potential_endings or [],  # type: ignore[arg-type]
  )
  world_facts: list[pinecone.PineconeWorldFact] = []
  branch_facts: list[pinecone.PineconeBranchFact] = []
  ancestors: list[str] = [str(ancestor) for ancestor in parent_node.ancestors]
  ancestors.append(parent_node.id)
  try:
    world_facts = _get_world_facts(world_id, parent_node.text)
    branch_facts = _get_branch_facts(world_id, parent_node.text, ancestors)

  except Exception as e:
    logger.error(f"Error searching Pinecone for facts: {e}")

  world_facts_text: list[str] = [fact.text for fact in world_facts]
  branch_facts_text: list[str] = [fact.text for fact in branch_facts]
  print("WORLD FACTS:\n -", "\n - ".join(world_facts_text))
  print("BRANCH FACTS:\n -", "\n - ".join(branch_facts_text))

  similar_nodes = _get_similar_nodes(world_id, parent_node.text)
  similar_nodes_text: list[str] = [node.text for node in similar_nodes]

  deps = llm.LLMNextNodeDeps(
    world_info=llm_world_info,
    previous_node=parent_node.text,
    user_choice=selected_choice.label,
    world_facts=world_facts_text,
    branch_facts=branch_facts_text,
    similar_nodes=similar_nodes_text,
    narrator_profile=world_meta.narrator_profile or "",
  )

  # Step 4: Generate next node content via LLM
  generated_node: llm.LLMStoryNode = llm.generate_next_node(deps)
  # Step 5: Create new StoryNode entity
  new_node_id = str(uuid.uuid4())
  # Build ancestors list: parent's ancestors + parent's id
  new_ancestors: list[str] = []
  if parent_node.ancestors:
    new_ancestors = [str(ancestor) for ancestor in parent_node.ancestors]
  new_ancestors.append(str(parent_node.id))
  new_node_dto = StoryNodeDTO(
    id=new_node_id,
    world_id=world_id,
    text=generated_node.text,
    story_summary=generated_node.story_summary,
    title=generated_node.title,
    choices=[ChoiceDTO(label=choice, target=None) for choice in generated_node.choices],
    parent_id=parent_node.id,
    ancestors=new_ancestors,
  )

  # Kick off a background task to extract facts from the generated node
  fact_deps = llm.LLMFactExtractionDeps(
    text=generated_node.text,
    user_choice=selected_choice.label,
  )
  facts_task = asyncio.create_task(llm.generate_facts_async(fact_deps))

  # Step 6: Save new node
  new_node = StoryNode.from_dto(new_node_dto)
  new_node.save()

  # Step 7: Update parent node's choice target
  parent_node.choices[choice_index].target = new_node_id

  # Step 8: Save updated parent
  parent_node.save()

  # Step 9: Await fact extraction before returning
  facts: llm.LLMFactExtraction = await facts_task

  print("PRODUCED THE BELOW FACTS: \n")
  print("WORLD FACTS: \n -", "\n - ".join(facts.world_facts))
  print("BRANCH FACTS: \n -", "\n - ".join(facts.branch_facts))
  pinecone.upsert_records(
    [
      pinecone.PineconeRecord.model_validate(
        {
          "id": new_node_id,
          "text": generated_node.text,
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
            "origin_node_id": parent_node.id,
          }
        )
        for fact in facts.branch_facts
      ]
    )

  # Step 9: Return new node as DTO
  return new_node.to_dto()
