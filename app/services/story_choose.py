"""Choose flow for story nodes.

Handles the session-first choose workflow: resolving target IDs to base
or custom choices, creating child nodes, and managing session overlays.
"""

from __future__ import annotations

from pynamodb.exceptions import UpdateError

from app.core.observability import logger, tracer
from app.models.dtos.story_node import ChoiceDTO, NodeGenerationStatus, StoryNodeDTO, StoryNodeProcessingStatus
from app.models.entities.node_session import BaseChoiceStateMap, CustomChoiceMap
from app.models.entities.story_node import StoryNode
from app.services.llm.sanitize import sanitize_user_input
from app.services.sessions import create_node_session, get_node_session
from app.services.story_nodes import (
  ABSOLUTE_MAX_DEPTH,
  DepthLimitReachedError,
  InvalidTargetError,
  get_node_entity,
)
from app.services.usage import check_quota
from app.utils import base52_to_number


@tracer.capture_method
async def choose_with_session(
  root_world_id: str,
  session_id: str,
  node_id: str,
  target_id: str | None = None,
  custom_choice: str | None = None,
  user_id: str | None = None,
) -> StoryNode:
  """Session-first choose: resolve a target_id to a base or custom choice, or create a new custom choice."""
  node = get_node_entity(root_world_id, node_id)
  if node.depth >= ABSOLUTE_MAX_DEPTH:
    raise DepthLimitReachedError(root_world_id, node_id, node.depth)

  if custom_choice is not None:
    return await _choose_custom_with_session(node, root_world_id, session_id, custom_choice, user_id)
  if target_id is None:
    raise InvalidTargetError(node_id, "", "target_id is required when custom_choice is not provided")

  parent_id = str(node.id)
  if not target_id.startswith(parent_id) or len(target_id) <= len(parent_id):
    raise InvalidTargetError(parent_id, target_id, "not a child of this node")

  suffix = target_id[len(parent_id) :]
  choice_index = base52_to_number(suffix)

  if choice_index < len(node.choices):
    return await _choose_base_with_session(node, root_world_id, session_id, choice_index, target_id, user_id)

  return await _choose_existing_custom_with_session(node, root_world_id, session_id, target_id, user_id)


async def _choose_custom_with_session(
  node: StoryNode,
  root_world_id: str,
  session_id: str,
  custom_choice: str,
  user_id: str | None,
) -> StoryNode:
  """Create a custom-choice child node scoped to this session."""
  if user_id:
    check_quota(user_id, "nodes")

  custom_choice = sanitize_user_input(custom_choice)

  # Atomically claim the next custom choice index on the StoryNode.
  # This ensures unique child IDs across concurrent sessions.
  try:
    if node.next_custom_choice_index is None:
      node.update(
        actions=[StoryNode.next_custom_choice_index.set(len(node.choices))],
        condition=StoryNode.next_custom_choice_index.does_not_exist(),
      )
      node.refresh()
  except UpdateError:
    node.refresh()

  node.update(
    actions=[StoryNode.next_custom_choice_index.set(StoryNode.next_custom_choice_index + 1)],
  )
  node.refresh()
  actual_index = int(node.next_custom_choice_index) - 1
  child_id = StoryNode.get_child_id_static(str(node.id), actual_index)

  parent_choice_dto = ChoiceDTO(label=custom_choice, is_custom=True, creator=user_id)
  child_dto = StoryNodeDTO(
    id=child_id,
    world_id=root_world_id,
    parent_choice=parent_choice_dto,
    source_session_id=session_id,
    generation_status=NodeGenerationStatus.INITIALIZED,
    processing_status=StoryNodeProcessingStatus.PENDING,
  )
  child = StoryNode.from_dto(child_dto)
  child.source_session_id = session_id
  child.save()

  create_node_session(session_id, child_id, root_world_id, parent_id=str(node.id))

  parent_ns = get_node_session(session_id, str(node.id))
  if not parent_ns:
    parent_ns = create_node_session(
      session_id=session_id,
      node_id=str(node.id),
      root_world_id=root_world_id,
      title=node.title,
      base_choice_count=len(node.choices),
      parent_id=node.parent_id,
    )
  cc_map = CustomChoiceMap(
    label=custom_choice,
    target_node_id=child_id,
    is_explored=True,
    creator_id=user_id or "",
  )
  parent_ns.custom_choices.append(cc_map)
  parent_ns.save()

  logger.info("Created custom-choice node %s in session %s", child_id, session_id)
  return child


async def _choose_existing_custom_with_session(
  node: StoryNode,
  root_world_id: str,
  session_id: str,
  target_id: str,
  user_id: str | None,
) -> StoryNode:
  """Navigate to an already-created custom-choice child node by its target_id."""
  parent_ns = get_node_session(session_id, str(node.id))
  if not parent_ns:
    raise InvalidTargetError(str(node.id), target_id, "no session state for this node")

  if not any(str(cc.target_node_id) == target_id for cc in parent_ns.custom_choices):
    raise InvalidTargetError(str(node.id), target_id, "custom choice not found in this session")

  child = get_node_entity(root_world_id, target_id)
  if user_id and str(child.generation_status) == NodeGenerationStatus.INITIALIZED.value:
    check_quota(user_id, "nodes")

  if not get_node_session(session_id, target_id):
    create_node_session(
      session_id,
      target_id,
      root_world_id,
      parent_id=str(node.id),
      title=child.title,
      base_choice_count=len(child.choices),
    )

  return child


async def _choose_base_with_session(
  node: StoryNode,
  root_world_id: str,
  session_id: str,
  choice_index: int,
  target_id: str,
  user_id: str | None,
) -> StoryNode:
  """Select a base choice, creating the child StoryNode if needed, and ensuring NodeSession state."""
  if not node.choices or choice_index < 0 or choice_index >= len(node.choices):
    raise InvalidTargetError(str(node.id), target_id, "choice index out of range")

  selected = node.choices[choice_index]

  if selected.is_created:
    child = get_node_entity(root_world_id, target_id)
    if user_id and str(child.generation_status) == NodeGenerationStatus.INITIALIZED.value:
      check_quota(user_id, "nodes")
  else:
    if user_id:
      check_quota(user_id, "nodes")
    parent_choice_dto = ChoiceDTO(
      label=selected.label,
      outcome=selected.outcome,
      target=target_id,
    )
    child_dto = StoryNodeDTO(
      id=target_id,
      world_id=root_world_id,
      parent_choice=parent_choice_dto,
      generation_status=NodeGenerationStatus.INITIALIZED,
      processing_status=StoryNodeProcessingStatus.PENDING,
    )
    child = StoryNode.from_dto(child_dto)
    child.save()
    node.choices[choice_index].is_created = True
    node.save()

  if not get_node_session(session_id, target_id):
    create_node_session(
      session_id,
      target_id,
      root_world_id,
      parent_id=str(node.id),
      title=child.title,
      base_choice_count=len(child.choices),
    )

  parent_ns = get_node_session(session_id, str(node.id))
  if not parent_ns:
    parent_ns = create_node_session(
      session_id=session_id,
      node_id=str(node.id),
      root_world_id=root_world_id,
      title=node.title,
      base_choice_count=len(node.choices),
      parent_id=node.parent_id,
    )

  expected = len(node.choices)
  if len(parent_ns.base_choice_states) < expected:
    parent_ns.base_choice_states.extend(
      BaseChoiceStateMap(is_explored=False) for _ in range(expected - len(parent_ns.base_choice_states))
    )

  parent_ns.base_choice_states[choice_index].is_explored = True
  parent_ns.save()

  return child
