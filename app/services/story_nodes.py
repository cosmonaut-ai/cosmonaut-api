"""Story nodes service layer.

Keep FastAPI routers thin by centralizing story node workflows here.
This module should not depend on FastAPI types.

Architecture:
- All methods work with entities (StoryNode) for efficient service composition
- API layer converts entities to DTOs for external consumption
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from pynamodb.exceptions import UpdateError
from pynamodb.pagination import ResultIterator

import app.services.llm as llm
import app.services.pinecone as pinecone
from app.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.core.observability import MetricUnit, logger, metrics, tracer
from app.models.dtos.story_node import ChoiceDTO, GenerationStatus, StoryNodeDTO, StoryNodeProcessingStatus
from app.models.entities.story_node import ChoiceMap, StoryNode, StoryNodeContext
from app.services.llm.sanitize import sanitize_llm_output, sanitize_user_input
from app.services.pinecone import PineconeBranchFact
from app.services.sqs import SQSSendError, send_node_analysis_message
from app.services.usage import check_and_increment, release_quota
from app.services.worlds import get_world_entity, world_meta_to_llm_world_info
from app.utils import LLMOutputTruncatedError, extract_xml_block, extract_xml_json
from app.utils.pii import truncate_for_log

if TYPE_CHECKING:
  from app.models.entities.world_meta import WorldMeta


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


class InvalidChoiceError(NodeServiceError, BadRequestError):
  """Raised when a choice index is out of bounds."""

  def __init__(self, node_id: str, choice_index: int, max_index: int):
    super().__init__(f"Invalid choice index {choice_index} for node {node_id}. Valid range: 0-{max_index}")
    self.node_id = node_id
    self.choice_index = choice_index
    self.max_index = max_index


class InvalidGenerationStatusError(NodeServiceError, ConflictError):
  """Raised when trying to generate text for a node with invalid generation status."""

  def __init__(self, node_id: str, current_status: GenerationStatus, allowed_statuses: list[GenerationStatus]):
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
# Pinecone Query Helpers
# =============================================================================


def _get_world_facts(world_id: str, node_text: str, top_k: int = 50) -> list[pinecone.PineconeWorldFact]:
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
  world_id: str, node_text: str, ancestors: list[str], top_k: int = 50
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
  except StoryNode.DoesNotExist as e:  # type: ignore[reportGeneralTypeIssues]
    raise NodeNotFoundError(world_id, node_id) from e


@tracer.capture_method
def list_nodes(
  world_id: str,
  limit: int = 100,
  cursor: str | None = None,
) -> tuple[list[StoryNode], str | None]:
  """Return nodes for a given world with cursor-based pagination.

  Args:
    world_id: The world identifier.
    limit: Maximum number of nodes to return (default 100).
    cursor: Opaque pagination token from a previous response.

  Returns:
    Tuple of (nodes, next_cursor).  ``next_cursor`` is None when there
    are no more pages.
  """
  import base64
  import json

  pk = StoryNode.gsi2_pk(world_id)

  last_evaluated_key = None
  if cursor:
    with contextlib.suppress(Exception):
      last_evaluated_key = json.loads(base64.urlsafe_b64decode(cursor))

  results: ResultIterator[StoryNode] = StoryNode.GSI2.query(  # type: ignore[reportUnknownReturnType]
    hash_key=pk,
    page_size=limit,
    limit=limit,
    last_evaluated_key=last_evaluated_key,
  )
  nodes = list(results)
  next_cursor: str | None = None
  if results.last_evaluated_key:
    next_cursor = base64.urlsafe_b64encode(json.dumps(results.last_evaluated_key).encode()).decode()
  return nodes, next_cursor


def get_node(world_id: str, node_id: str) -> StoryNode:
  """Fetch a single node by identifier."""
  return get_node_entity(world_id, node_id)


def get_node_entities(world_id: str, node_ids: list[str]) -> list[StoryNode]:
  """Fetch multiple nodes by identifier."""
  nodes = StoryNode.batch_get([(StoryNode.pk(world_id), StoryNode.sk(node_id)) for node_id in node_ids])
  nodes_map = {node.id: node for node in nodes}
  return [nodes_map[node_id] for node_id in node_ids if node_id in nodes_map]


# =============================================================================
# Choose Flow - Helper Functions
# =============================================================================


async def _validate_and_prepare_choice(
  node: StoryNode,
  node_id: str,
  choice_index: int | None,
  custom_choice: str | None,
  user_id: str | None,
) -> int:
  """Validate choice and prepare node for selection.

  Returns:
    Tuple of (choice_index, is_custom_choice)
  """

  # The choose flow only needs the node to exist and have valid choices.
  # Processing status (fact extraction) is not required here -- the
  # generate-text path will use whatever context is available at that time.

  if custom_choice is not None:
    custom_choice = sanitize_user_input(custom_choice)
    choice_index = len(node.choices)
    new_choice = ChoiceMap(
      label=custom_choice,
      target=node.get_child_id(choice_index),
      is_custom=True,
      creator=user_id,
    )
    node.choices.append(new_choice)
    node.save()
    logger.info("Added custom choice at index %s: %s", choice_index, truncate_for_log(custom_choice))
  else:
    if choice_index is None:
      raise InvalidChoiceError(node_id, -1, len(node.choices) - 1 if node.choices else -1)
    if not node.choices or choice_index < 0 or choice_index >= len(node.choices):
      max_index = len(node.choices) - 1 if node.choices else -1
      raise InvalidChoiceError(node_id, choice_index, max_index)
    # No save() needed here — the node is not modified when selecting an
    # existing choice by index.  The is_created flag is set later in choose().
    logger.info(f"Selected existing choice at index {choice_index}")

  return choice_index


def _build_previous_text(
  prev_story_nodes: list[StoryNode],
  selected_choice_label: str,
) -> str:
  """Build the previous story text for context."""
  prev_story_nodes_text = ""
  for i, current_node in enumerate(prev_story_nodes):
    prev_story_nodes_text += current_node.text
    if i < len(prev_story_nodes) - 1:
      next_node = prev_story_nodes[i + 1]
      choice_idx = next_node.choice_index
      if choice_idx is not None and choice_idx < len(current_node.choices):
        choice_text = current_node.choices[choice_idx].label
        prev_story_nodes_text += f'\n\nUser chose: "{choice_text}"'
        if current_node.choices[choice_idx].is_custom:
          prev_story_nodes_text += " *(custom choice)*"
        prev_story_nodes_text += "\n\n"
    else:
      prev_story_nodes_text += f'\n\nUser chose: "{selected_choice_label}"\n\n'
  return prev_story_nodes_text


def _build_next_node_deps(
  node: StoryNode,
  world_meta: WorldMeta,
  selected_choice: ChoiceMap,
) -> llm.NextNodeDeps:
  """Build the dependencies for next node generation."""
  llm_world_info = world_meta_to_llm_world_info(world_meta)
  prev_story_nodes = get_node_entities(node.world_id, node.ancestors[-10:])
  prev_story_nodes_text = _build_previous_text(prev_story_nodes, selected_choice.label)

  # Safely access context attributes; context may be None if the parent node
  # hasn't been processed yet (e.g., SQS worker hasn't completed analysis).
  raw_world_facts = node.context.world_facts if node.context else None
  raw_branch_facts = node.context.branch_facts if node.context else None
  world_facts: list[str] = [str(f) for f in raw_world_facts] if raw_world_facts else []
  branch_facts: list[str] = [str(f) for f in raw_branch_facts] if raw_branch_facts else []

  return llm.NextNodeDeps(
    world_info=llm_world_info,
    story_summary=node.story_summary or "The story begins.",
    story_max_nodes=int(world_meta.story_max_nodes or 10),
    previous_text=prev_story_nodes_text,
    user_choice=selected_choice.label,
    choice_outcome=selected_choice.outcome,
    world_facts=world_facts,
    branch_facts=branch_facts,
    narrator_profile=world_meta.narrator_profile or "",
    story_length=node.depth,
    is_custom_choice=selected_choice.is_custom,
    family_friendly=world_meta.family_friendly == "true",
  )


async def _stream_node_content(
  run_stream_ctx: AbstractAsyncContextManager[Any],
) -> AsyncGenerator[tuple[str, str, llm.LLMNodeMetadata | None]]:
  """Shared streaming loop: buffer response, extract <story> content, yield metadata at end.

  Yields:
    (new_text, full_buffer, None) during streaming; ("", full_buffer, metadata) at completion.
  """
  full_response_buffer = ""
  last_emitted_index = 0
  story_started = False

  async with run_stream_ctx as result:
    async for chunk in result.stream_text(delta=True):
      full_response_buffer += chunk

      story_content = extract_xml_block(full_response_buffer, "story", streaming=True)
      if story_content is not None:
        if not story_started:
          story_started = True

        if len(story_content) > last_emitted_index:
          new_text = story_content[last_emitted_index:]
          yield new_text, full_response_buffer, None
          last_emitted_index = len(story_content)

  # Parse metadata after stream completes
  try:
    metadata = extract_xml_json(full_response_buffer, "metadata", llm.LLMNodeMetadata)
    yield "", full_response_buffer, metadata
  except LLMOutputTruncatedError:
    logger.warning("LLM output truncated by max_tokens -- extracting partial story text")
    story_text = extract_xml_block(full_response_buffer, "story", streaming=False)
    if story_text:
      fallback_metadata = llm.LLMNodeMetadata(
        choices=[],
        story_summary="[Story truncated due to length limit]",
        title="Continued...",
      )
      yield "", full_response_buffer, fallback_metadata
    else:
      raise ValueError("LLM output truncated before any story content was generated") from None
  except ValueError as e:
    logger.error("LLM failed to output expected XML tags. Full response: %s", truncate_for_log(full_response_buffer))
    raise ValueError("LLM failed to output metadata tags") from e


async def _stream_next_node(
  deps: llm.NextNodeDeps,
  world_id: str,
) -> AsyncGenerator[tuple[str, str, llm.LLMNodeMetadata | None]]:
  """Stream next node content via LLM.

  The agent's system prompt contains only static content (instructions + world
  context + narrator profile).  Dynamic per-node context is passed as the user
  message.  Prompt caching is handled transparently at the model level.

  Yields:
    Tuple of (chunk_text, full_buffer, metadata_if_complete)
  """
  user_message = llm.build_user_message(deps)
  run_stream_ctx = llm.get_next_node_agent().run_stream(user_message, deps=deps, model_settings={"max_tokens": 8192})
  async for item in _stream_node_content(run_stream_ctx):
    yield item


async def _stream_root_node(
  deps: llm.RootNodeDeps,
) -> AsyncGenerator[tuple[str, str, llm.LLMNodeMetadata | None]]:
  """Stream root node content via LLM.

  Similar to _stream_next_node but uses the root node agent with different prompts.

  Yields:
    Tuple of (chunk_text, full_buffer, metadata_if_complete)
  """
  run_stream_ctx = llm.get_root_node_agent().run_stream(
    "Generate the first story node.", deps=deps, model_settings={"max_tokens": 8192}
  )
  async for item in _stream_node_content(run_stream_ctx):
    yield item


# =============================================================================
# Main Choose Function
# =============================================================================


@tracer.capture_method
async def choose(
  world_id: str,
  node_id: str,
  choice_index: int | None = None,
  custom_choice: str | None = None,
  user_id: str | None = None,
  session_id: str | None = None,
) -> StoryNode:
  """Choose a story node option and initialize the next node without generating text.

  If the node already exists, returns the existing node.
  If the node doesn't exist, creates a new node with INITIALIZED generation status.

  Args:
    world_id: The world identifier
    node_id: The current node identifier
    choice_index: Index of an existing choice (mutually exclusive with custom_choice)
    custom_choice: Free text custom choice (mutually exclusive with choice_index)
    user_id: User ID for tracking custom choice creator

  Returns:
    The initialized or existing story node
  """
  logger.info(
    "Choosing node %s with choice_index=%s, custom_choice=%s",
    node_id,
    choice_index,
    truncate_for_log(custom_choice) if custom_choice else None,
  )

  # Validate and prepare choice
  node = get_node_entity(world_id, node_id)

  if node.depth >= ABSOLUTE_MAX_DEPTH:
    raise DepthLimitReachedError(world_id, node_id, node.depth)

  choice_index = await _validate_and_prepare_choice(node, node_id, choice_index, custom_choice, user_id)

  selected_choice = node.choices[choice_index]
  new_node_id = selected_choice.target or node.get_child_id(choice_index)

  # Return existing node if already created
  if selected_choice.is_created:
    logger.info(f"Returning existing node {new_node_id} for choice {choice_index}")
    try:
      return get_node_entity(world_id, new_node_id)
    except NodeNotFoundError:
      logger.error(f"New node {new_node_id} not found even though parent choice is created. Continuing.")

  # Create new initialized node (without text).
  # NOTE: These two writes are intentionally non-transactional.  If the process
  # crashes between them, the child node exists but ``is_created`` on the parent
  # remains False.  A retry will overwrite the child (same deterministic ID,
  # still INITIALIZED with no content) and then set ``is_created``.  This is safe
  # and avoids the added latency/complexity of DynamoDB TransactWriteItems.
  parent_choice_dto = ChoiceDTO(
    label=selected_choice.label,
    outcome=selected_choice.outcome,
    target=selected_choice.target,
    is_created=bool(selected_choice.is_created),
    is_custom=bool(selected_choice.is_custom),
    creator=selected_choice.creator,
  )
  new_node_dto = StoryNodeDTO(
    id=new_node_id,
    world_id=world_id,
    text=None,
    story_summary=None,
    title=None,
    choices=[],
    parent_choice=parent_choice_dto,
    processing_status=StoryNodeProcessingStatus.PENDING,
    generation_status=GenerationStatus.INITIALIZED,
  )
  new_node = StoryNode.from_dto(new_node_dto)
  new_node.save()
  node.choices[choice_index].is_created = True
  node.save()

  logger.info(f"Initialized new node {new_node_id} with generation_status=INITIALIZED")

  if session_id:
    try:
      from app.services.sessions import create_node_session, get_node_session

      create_node_session(
        session_id=session_id,
        node_id=new_node_id,
        root_world_id=world_id,
        title=None,
        base_choice_count=0,
      )

      parent_ns = get_node_session(session_id, node_id)
      if parent_ns:
        if custom_choice is not None:
          from app.models.entities.node_session import CustomChoiceMap

          custom_map = CustomChoiceMap(
            label=custom_choice,
            target_node_id=new_node_id,
            is_explored=True,
            creator_id=user_id or "",
          )
          parent_ns.custom_choices.append(custom_map)
          parent_ns.save()
        elif choice_index < len(parent_ns.base_choice_states):
          parent_ns.base_choice_states[choice_index].is_explored = True
          parent_ns.save()
    except Exception:
      logger.warning(
        "Failed to dual-write session data for choose (session=%s, node=%s)",
        session_id,
        node_id,
        exc_info=True,
      )

  return new_node


# =============================================================================
# Generate Text Function
# =============================================================================


@tracer.capture_method
async def generate_text(
  world_id: str,
  node_id: str,
  user_id: str | None = None,
  session_id: str | None = None,
) -> AsyncGenerator[str]:
  """Generate story text for an initialized or failed node.

  For root nodes (node_id == "0"), uses the root node agent.
  For child nodes, uses the next node agent with parent context.

  Args:
    world_id: The world identifier
    node_id: The node identifier to generate text for
    user_id: The authenticated user's ID (used for node quota enforcement)

  Yields:
    Text chunks as they are generated

  Raises:
    NodeNotFoundError: If the node doesn't exist
    InvalidGenerationStatusError: If the node is not in INITIALIZED or FAILED status
    QuotaExceededError: If the user has reached their tier's node limit
  """
  logger.info(f"Generating text for node {node_id}")

  node = get_node_entity(world_id, node_id)

  # If already completed and has text, return it idempotently
  if GenerationStatus(node.generation_status) == GenerationStatus.COMPLETED and node.text:
    yield node.text
    return

  # Atomically transition: INITIALIZED|FAILED -> GENERATING.
  # DynamoDB conditional write ensures only one concurrent request can win this
  # transition, preventing duplicate generation and race conditions.
  # This MUST happen before the quota check so that concurrent / retried requests
  # do not drain quota without performing actual generation.
  allowed_statuses = [GenerationStatus.INITIALIZED, GenerationStatus.FAILED]
  try:
    node.update(
      actions=[StoryNode.generation_status.set(GenerationStatus.GENERATING.value)],
      condition=StoryNode.generation_status.is_in(*[s.value for s in allowed_statuses]),
    )
  except UpdateError:
    # Another request already transitioned the status; re-read to determine state
    node = get_node_entity(world_id, node_id)
    current_status = GenerationStatus(node.generation_status)
    if current_status == GenerationStatus.COMPLETED and node.text:
      yield node.text
      return
    raise InvalidGenerationStatusError(node_id, current_status, allowed_statuses) from None

  # Enforce node quota after winning the status transition so that losing
  # concurrent requests (or retries after a network error) do not waste quota.
  if user_id:
    check_and_increment(user_id, "nodes")

  try:
    # Select appropriate stream generator based on node type
    if node.parent_id is None:
      # Root node: use root node agent
      logger.info(f"Generating root node text for world {world_id}")
      world_meta = get_world_entity(world_id)
      llm_world_info = world_meta_to_llm_world_info(world_meta)
      root_deps = llm.RootNodeDeps(
        world_info=llm_world_info,
        narrator_profile=world_meta.narrator_profile or "",
        family_friendly=world_meta.family_friendly == "true",
      )
      stream = _stream_root_node(root_deps)
    else:
      # Child node: use next node agent with parent context.
      # Fetch world meta and parent node concurrently. We do NOT wait for
      # parent processing (fact extraction) to complete -- whatever context
      # is available at this moment is used. This avoids up to 2s of
      # blocking latency at the cost of potentially degraded context for
      # the first few nodes in rapid succession.
      parent_id: str = node.parent_id  # narrowed from str | None by the if-branch above

      world_meta_result, parent_node = await asyncio.gather(
        asyncio.to_thread(get_world_entity, world_id),
        asyncio.to_thread(get_node_entity, world_id, parent_id),
      )
      world_meta = world_meta_result

      if node.choice_index is None or node.choice_index >= len(parent_node.choices):
        raise InvalidChoiceError(node_id, node.choice_index or -1, len(parent_node.choices) - 1)

      selected_choice = parent_node.choices[node.choice_index]
      next_deps = _build_next_node_deps(parent_node, world_meta, selected_choice)
      stream = _stream_next_node(next_deps, world_id=world_id)

    # Stream content and collect metadata
    story_text = ""
    metadata: llm.LLMNodeMetadata | None = None

    async for chunk, full_buffer, meta in stream:
      if chunk:
        yield chunk
      if meta:
        metadata = meta
        story_text = extract_xml_block(full_buffer, "story") or ""

    if metadata is None:
      logger.error("Failed to parse node metadata")
      node.generation_status = GenerationStatus.FAILED.value
      node.save()
      raise ValueError("LLM generated text but failed to produce valid metadata (title, choices, summary)")

    # Update node with generated content
    node.text = story_text
    node.story_summary = metadata.story_summary
    node.title = metadata.title
    node.choices = [
      ChoiceMap(
        label=choice.label,
        outcome=choice.outcome,
        is_custom=False,
        target=StoryNode.get_child_id_static(node_id, i),
      )
      for i, choice in enumerate(metadata.choices)
    ]
    node.generation_status = GenerationStatus.COMPLETED.value
    node.save()
    metrics.add_metric(name="StoryNodeGenerated", unit=MetricUnit.Count, value=1)

    if session_id:
      try:
        from app.services.sessions import get_node_session

        ns = get_node_session(session_id, node_id)
        if ns:
          from app.models.entities.node_session import BaseChoiceStateMap

          ns.title = metadata.title
          ns.base_choice_states = [BaseChoiceStateMap(is_explored=False) for _ in metadata.choices]
          ns.save()
      except Exception:
        logger.warning(
          "Failed to update NodeSession after text generation (session=%s, node=%s)",
          session_id,
          node_id,
          exc_info=True,
        )

    # Enqueue async fact extraction.  This is non-critical: a failure leaves
    # the node in PENDING processing_status which can be retried via the
    # /retry-processing endpoint.  We must not let an SQS failure undo the
    # successful generation.
    try:
      send_node_analysis_message(world_id, node_id)
    except SQSSendError:
      logger.error(f"Failed to enqueue analysis for node {node_id} -- node will remain in PENDING processing_status")

  except Exception as e:
    logger.error(f"Error generating text for node {node_id}: {e}")
    if user_id:
      release_quota(user_id, "nodes")
    node.generation_status = GenerationStatus.FAILED.value
    node.save()
    raise


# =============================================================================
# Node Processing
# =============================================================================


@tracer.capture_method
async def process_node(node: StoryNode) -> None:
  """Process a node: extract facts and upsert to Pinecone."""
  world_id = node.world_id
  parent_node: StoryNode | None = get_node_entity(world_id, node.parent_id) if node.parent_id else None

  # Fetch facts from Pinecone.  A failure here is non-fatal: the node will be
  # processed with empty/partial context, degrading story quality but not
  # blocking the pipeline.
  pinecone_degraded = False
  world_facts: list[pinecone.PineconeWorldFact] = []
  branch_facts: list[pinecone.PineconeBranchFact] = []
  similar_nodes: list[pinecone.PineconeRecord] = []
  try:
    world_facts, branch_facts, similar_nodes = await asyncio.gather(
      asyncio.to_thread(_get_world_facts, world_id, node.text),
      asyncio.to_thread(_get_branch_facts, world_id, node.text, node.ancestors),
      asyncio.to_thread(_get_similar_nodes, world_id, node.text),
    )
  except Exception as e:
    pinecone_degraded = True
    logger.warning(
      f"Pinecone query failed for node {node.id} -- proceeding with degraded context",
      extra={"error": str(e), "world_id": world_id, "node_id": node.id},
      exc_info=True,
    )

  # Update node context
  world_facts_text: list[str] = [fact.text for fact in world_facts]
  branch_facts_text: list[str] = [fact.text for fact in branch_facts]
  similar_nodes_text: list[str] = [n.text for n in similar_nodes]
  node.context = StoryNodeContext(
    world_facts=world_facts_text,
    branch_facts=branch_facts_text,
    similar_nodes=similar_nodes_text,
  )

  if pinecone_degraded:
    logger.warning(
      f"Node {node.id} context populated with degraded Pinecone data: "
      f"world_facts={len(world_facts_text)}, branch_facts={len(branch_facts_text)}, "
      f"similar_nodes={len(similar_nodes_text)}",
    )

  # Extract new facts via LLM
  fact_deps = llm.LLMFactExtractionDeps(
    text=node.text,
    user_choice=parent_node.choices[node.choice_index].label if parent_node and node.choice_index else None,
  )
  facts = await llm.generate_facts_async(fact_deps)

  facts.world_facts = [sanitize_llm_output(f) for f in facts.world_facts]
  facts.branch_facts = [sanitize_llm_output(f) for f in facts.branch_facts]

  # Upsert node text to Pinecone
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

  # Upsert extracted facts
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


def retry_processing(world_id: str, node_id: str) -> StoryNode:
  """Re-enqueue a failed node for processing (fact extraction + Pinecone upsert).

  Atomically resets processing_status from FAILED back to PENDING and sends a
  new analysis message to the fast worker queue.

  Args:
    world_id: The world identifier.
    node_id: The node identifier to retry.

  Returns:
    The updated StoryNode with processing_status=PENDING.

  Raises:
    NodeNotFoundError: If the node does not exist.
    InvalidProcessingStatusError: If the node is not in FAILED processing status.
  """
  node = get_node_entity(world_id, node_id)

  # Use a conditional update to atomically transition FAILED -> PENDING.
  # This prevents duplicate SQS messages from concurrent retry requests.
  try:
    node.update(
      actions=[StoryNode.processing_status.set(StoryNodeProcessingStatus.PENDING.value)],
      condition=StoryNode.processing_status == StoryNodeProcessingStatus.FAILED.value,
    )
  except UpdateError:
    # Re-read to get the actual status for the error message
    node = get_node_entity(world_id, node_id)
    raise InvalidProcessingStatusError(node_id, StoryNodeProcessingStatus(node.processing_status)) from None

  send_node_analysis_message(world_id, node_id)
  logger.info(f"Re-enqueued failed node {node_id} in world {world_id} for processing")

  return node
