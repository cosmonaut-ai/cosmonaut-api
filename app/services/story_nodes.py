"""Story nodes service layer.

This module is the public facade for story node operations. It defines
shared errors, constants, and node CRUD, then re-exports the higher-level
workflows from focused submodules so that existing consumers can continue
to ``import app.services.story_nodes``.

Submodules:
  story_choose      – session-first choose flow
  story_generation   – LLM streaming text generation
  story_processing   – fact extraction + Pinecone upsert
"""

from __future__ import annotations

from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.core.observability import tracer
from app.models.dtos.story_node import ChoiceDTO, NodeGenerationStatus, StoryNodeProcessingStatus
from app.models.entities.node_session import BaseChoiceStateMap, CustomChoiceMap
from app.models.entities.story_node import ChoiceMap, StoryNode

# =============================================================================
# Errors
# =============================================================================


class NodeServiceError(Exception):
  """Base exception for node service failures."""


class NodeProcessingError(NodeServiceError, ConflictError):
  """Raised when a node is not in the COMPLETED state."""

  def __init__(self, node_id: str):
    super().__init__(f"Node {node_id} is not in the COMPLETED state")
    self.node_id = node_id


class NodeNotFoundError(NodeServiceError, NotFoundError):
  """Raised when a node cannot be found."""

  def __init__(self, world_id: str, node_id: str):
    super().__init__(f"Node not found: {node_id} in world {world_id}")
    self.world_id = world_id
    self.node_id = node_id


class InvalidTargetError(NodeServiceError, BadRequestError):
  """Raised when a target_id is not a valid child of the given node."""

  def __init__(self, node_id: str, target_id: str, reason: str = ""):
    detail = f"Invalid target '{target_id}' for node {node_id}"
    if reason:
      detail += f": {reason}"
    super().__init__(detail)
    self.node_id = node_id
    self.target_id = target_id


class InvalidGenerationStatusError(NodeServiceError, ConflictError):
  """Raised when trying to generate text for a node with invalid generation status."""

  def __init__(self, node_id: str, current_status: NodeGenerationStatus, allowed_statuses: list[NodeGenerationStatus]):
    allowed = ", ".join(s.value for s in allowed_statuses)
    super().__init__(f"Cannot generate text for node {node_id} with status {current_status.value}. Allowed: {allowed}")
    self.node_id = node_id
    self.current_status = current_status
    self.allowed_statuses = allowed_statuses


class InvalidProcessingStatusError(NodeServiceError, BadRequestError):
  """Raised when trying to retry processing for a node that is not in FAILED status."""

  def __init__(self, node_id: str, current_status: StoryNodeProcessingStatus):
    super().__init__(
      f"Cannot retry processing for node {node_id} with status {current_status.value}. "
      f"Only nodes with status '{StoryNodeProcessingStatus.FAILED.value}' can be retried."
    )
    self.node_id = node_id
    self.current_status = current_status


ABSOLUTE_MAX_DEPTH = 100


class DepthLimitReachedError(NodeServiceError, ForbiddenError):
  """Raised when a node has reached the absolute maximum story depth."""

  def __init__(self, world_id: str, node_id: str, depth: int):
    super().__init__(
      f"Story depth limit reached: node {node_id} is at depth {depth} (maximum is {ABSOLUTE_MAX_DEPTH})."
    )
    self.world_id = world_id
    self.node_id = node_id
    self.depth = depth


# =============================================================================
# Session Choice Merging
# =============================================================================


def merge_choices(
  base_choices: list[ChoiceMap],
  base_choice_states: list[BaseChoiceStateMap],
  custom_choices: list[CustomChoiceMap],
) -> list[ChoiceDTO]:
  """Merge root StoryNode base choices with NodeSession overlay."""
  result: list[ChoiceDTO] = []
  for i, choice in enumerate(base_choices):
    explored = base_choice_states[i].is_explored if i < len(base_choice_states) else False
    result.append(
      ChoiceDTO(
        label=choice.label,
        outcome=choice.outcome,
        target=choice.target,
        is_created=bool(choice.is_created),
        is_explored=bool(explored),
      )
    )
  for cc in custom_choices:
    result.append(
      ChoiceDTO(
        label=cc.label,
        target=cc.target_node_id,
        is_created=True,
        is_explored=bool(cc.is_explored),
        is_custom=True,
        creator=cc.creator_id,
        creator_email=cc.creator_email,
        creator_display_name=cc.creator_display_name,
      )
    )
  return result


# =============================================================================
# Node CRUD Operations
# =============================================================================


def _node_keys(world_id: str, node_id: str) -> tuple[str, str]:
  """Generate PynamoDB primary and sort keys for a node ID."""
  return (StoryNode.pk(world_id), StoryNode.sk(node_id))


@tracer.capture_method
def get_node_entity(world_id: str, node_id: str) -> StoryNode:
  """Internal: fetch node entity for service composition."""
  pk, sk = _node_keys(world_id, node_id)
  try:
    return StoryNode.get(pk, sk)
  except StoryNode.DoesNotExist as e:
    raise NodeNotFoundError(world_id, node_id) from e


def get_node(world_id: str, node_id: str) -> StoryNode:
  """Fetch a single node by identifier."""
  return get_node_entity(world_id, node_id)


def get_node_entities(world_id: str, node_ids: list[str]) -> list[StoryNode]:
  """Fetch multiple nodes by identifier."""
  nodes = StoryNode.batch_get([(StoryNode.pk(world_id), StoryNode.sk(node_id)) for node_id in node_ids])
  nodes_map = {node.id: node for node in nodes}
  return [nodes_map[node_id] for node_id in node_ids if node_id in nodes_map]


# =============================================================================
# Re-exports from focused submodules
# =============================================================================

from app.services.story_choose import choose_with_session  # noqa: E402
from app.services.story_generation import generate_text  # noqa: E402
from app.services.story_processing import process_node, retry_processing  # noqa: E402

__all__ = [
  "ABSOLUTE_MAX_DEPTH",
  "DepthLimitReachedError",
  "InvalidGenerationStatusError",
  "InvalidProcessingStatusError",
  "InvalidTargetError",
  "NodeNotFoundError",
  "NodeProcessingError",
  "NodeServiceError",
  "choose_with_session",
  "generate_text",
  "get_node",
  "get_node_entities",
  "get_node_entity",
  "merge_choices",
  "process_node",
  "retry_processing",
]
