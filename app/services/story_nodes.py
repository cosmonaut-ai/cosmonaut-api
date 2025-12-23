"""Story nodes service layer.

Keep FastAPI routers thin by centralizing story node workflows here.
This module should not depend on FastAPI types.

Architecture:
- Internal methods work with entities (StoryNode) for efficient service composition
- Public methods convert entities to DTOs for API layer consumption
"""

from __future__ import annotations

import uuid

from pynamodb.pagination import ResultIterator

import app.services.llm as llm
from app.models.dtos.story_node import ChoiceDTO, StoryNodeDTO
from app.models.entities.story_node import StoryNode
from app.services.worlds import get_world_entity


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
  print(node.attribute_values)
  return node.to_dto()


def choose_and_generate(world_id: str, node_id: str, choice_index: int) -> StoryNodeDTO:
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
  print("RETURNED PARENT NODE: ", parent_node.attribute_values)

  if not parent_node.choices or choice_index < 0 or choice_index >= len(parent_node.choices):
    max_index = len(parent_node.choices) - 1 if parent_node.choices else -1
    raise InvalidChoiceError(node_id, choice_index, max_index)

  selected_choice = parent_node.choices[choice_index]

  if selected_choice.target:
    return get_node(world_id, selected_choice.target)

  # Step 2: Fetch world metadata for context
  world_meta = get_world_entity(world_id)
  print("RETURNED WORLD META: ", world_meta.attribute_values)

  # Step 3: Build LLMNextNodeDeps
  llm_world_info = llm.LLMWorldInfo(
    story_title=world_meta.title or "",
    story_description=world_meta.description or "",
    setting=world_meta.setting or "",
    characters=world_meta.characters or [],  # type: ignore[arg-type]
    potential_endings=world_meta.potential_endings or [],  # type: ignore[arg-type]
    story_background=world_meta.story_background or "",
  )

  deps = llm.LLMNextNodeDeps(
    world_info=llm_world_info,
    previous_node=parent_node.text,
    user_choice=selected_choice.label,
    world_facts=[],  # Skip fact extraction per user decision
    branch_facts=[],  # Skip fact extraction per user decision
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

  # Step 6: Save new node
  new_node = StoryNode.from_dto(new_node_dto)
  new_node.save()

  # Step 7: Update parent node's choice target
  parent_node.choices[choice_index].target = new_node_id

  # Step 8: Save updated parent
  parent_node.save()

  # Step 9: Return new node as DTO
  return new_node.to_dto()
