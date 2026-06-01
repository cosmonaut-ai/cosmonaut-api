"""Story text generation via LLM streaming.

Handles SSE text streaming for both root and continuation nodes,
including status transitions, quota management, and session updates.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from pynamodb.exceptions import UpdateError

import app.services.llm as llm
from app.core.observability import MetricUnit, logger, metrics, tracer
from app.models.dtos.story_node import NodeGenerationStatus
from app.models.entities.node_session import BaseChoiceStateMap
from app.models.entities.story_node import ChoiceMap, StoryNode
from app.services.sessions import create_node_session, get_node_session
from app.services.sqs import SQSSendError, send_node_analysis_message
from app.services.story_nodes import (
  InvalidGenerationStatusError,
  get_node_entities,
  get_node_entity,
)
from app.services.usage import QuotaExceededError, check_and_increment, release_quota
from app.services.worlds import get_world_entity, world_meta_to_llm_world_info
from app.utils import LLMOutputTruncatedError, extract_xml_block, extract_xml_json
from app.utils.pii import truncate_for_log

if TYPE_CHECKING:
  from app.models.entities.world_meta import WorldMeta


# =============================================================================
# LLM Streaming Helpers
# =============================================================================


def _build_story_summary(
  prev_story_nodes: list[StoryNode],
  selected_choice: ChoiceMap,
) -> str:
  """Build the story summary for context."""
  summary = ""
  for i, current_node in enumerate(prev_story_nodes):
    summary += '<node_summary depth="' + str(i) + '">\n'
    summary += current_node.story_summary
    summary += "\n</node_summary>\n"
    if i < len(prev_story_nodes) - 1:
      next_node = prev_story_nodes[i + 1]
      if next_node.parent_choice:
        summary += '<user_choice custom="' + str(next_node.parent_choice.is_custom) + '">\n'
        summary += next_node.parent_choice.label
        summary += "\n</user_choice>\n"
  return summary or "Nothing to summarize."


def _build_next_node_deps(
  node: StoryNode,
  world_meta: WorldMeta,
  selected_choice: ChoiceMap,
) -> llm.NextNodeDeps:
  """Build the dependencies for next node generation."""
  llm_world_info = world_meta_to_llm_world_info(world_meta)
  prev_story_nodes = get_node_entities(node.world_id, node.ancestors[-10:])
  prev_story_summary = _build_story_summary(prev_story_nodes, selected_choice)

  raw_world_facts = node.context.world_facts if node.context else None
  raw_branch_facts = node.context.branch_facts if node.context else None
  world_facts: list[str] = [str(f) for f in raw_world_facts] if raw_world_facts else []
  branch_facts: list[str] = [str(f) for f in raw_branch_facts] if raw_branch_facts else []

  return llm.NextNodeDeps(
    world_info=llm_world_info,
    prev_story_summary=prev_story_summary,
    story_max_nodes=int(world_meta.story_max_nodes or 10),
    user_choice=selected_choice.label,
    choice_outcome=selected_choice.outcome,
    world_facts=world_facts,
    branch_facts=branch_facts,
    narrator_profile=world_meta.narrator_profile or "",
    story_length=node.depth,
    is_custom_choice=selected_choice.is_custom,
    vocab_level=world_meta.vocab_level,
    content_filter=world_meta.content_filter,
    max_choices=int(world_meta.max_choices) if world_meta.max_choices else None,
  )


async def _stream_node_content(
  run_stream_ctx: AbstractAsyncContextManager[Any],
) -> AsyncGenerator[tuple[str, str, llm.LLMNodeMetadata | None]]:
  """Shared streaming loop: buffer response, extract <story> content, yield metadata at end."""
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
  """Stream next node content via LLM."""
  user_message = llm.build_user_message(deps)
  run_stream_ctx = llm.get_next_node_agent().run_stream(user_message, deps=deps, model_settings={"max_tokens": 16384})
  async for item in _stream_node_content(run_stream_ctx):
    yield item


async def _stream_root_node(
  deps: llm.RootNodeDeps,
) -> AsyncGenerator[tuple[str, str, llm.LLMNodeMetadata | None]]:
  """Stream root node content via LLM."""
  run_stream_ctx = llm.get_root_node_agent().run_stream(
    "Generate the first story node.", deps=deps, model_settings={"max_tokens": 16384}
  )
  async for item in _stream_node_content(run_stream_ctx):
    yield item


# =============================================================================
# Generate Text
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

  Yields:
    Text chunks as they are generated

  Raises:
    NodeNotFoundError: If the node doesn't exist
    InvalidGenerationStatusError: If the node is not in INITIALIZED or FAILED status
    QuotaExceededError: If the user has reached their tier's node limit
  """
  logger.info(f"Generating text for node {node_id}")

  node = get_node_entity(world_id, node_id)

  if NodeGenerationStatus(node.generation_status) == NodeGenerationStatus.COMPLETED and node.text:
    yield node.text
    return

  allowed_statuses = [NodeGenerationStatus.INITIALIZED, NodeGenerationStatus.FAILED]
  try:
    node.update(
      actions=[StoryNode.generation_status.set(NodeGenerationStatus.GENERATING.value)],
      condition=StoryNode.generation_status.is_in(*[s.value for s in allowed_statuses]),
    )
  except UpdateError:
    node = get_node_entity(world_id, node_id)
    current_status = NodeGenerationStatus(node.generation_status)
    if current_status == NodeGenerationStatus.COMPLETED and node.text:
      yield node.text
      return
    raise InvalidGenerationStatusError(node_id, current_status, allowed_statuses) from None

  quota_reserved = False

  try:
    if user_id:
      check_and_increment(user_id, "nodes")
      quota_reserved = True
    if node.parent_id is None:
      logger.info(f"Generating root node text for world {world_id}")
      world_meta = get_world_entity(world_id)
      llm_world_info = world_meta_to_llm_world_info(world_meta)
      root_deps = llm.RootNodeDeps(
        world_info=llm_world_info,
        narrator_profile=world_meta.narrator_profile or "",
        vocab_level=world_meta.vocab_level,
        content_filter=world_meta.content_filter,
        max_choices=int(world_meta.max_choices) if world_meta.max_choices else None,
      )
      stream = _stream_root_node(root_deps)
    else:
      parent_id: str = node.parent_id

      world_meta_result, parent_node = await asyncio.gather(
        asyncio.to_thread(get_world_entity, world_id),
        asyncio.to_thread(get_node_entity, world_id, parent_id),
      )
      world_meta = world_meta_result

      next_deps = _build_next_node_deps(parent_node, world_meta, node.parent_choice)
      stream = _stream_next_node(next_deps, world_id=world_id)

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
      node.generation_status = NodeGenerationStatus.FAILED.value
      node.save()
      raise ValueError("LLM generated text but failed to produce valid metadata (title, choices, summary)")

    node.text = story_text
    node.story_summary = metadata.story_summary
    node.title = metadata.title
    node.choices = [
      ChoiceMap(
        label=choice.label,
        outcome=choice.outcome,
        target=StoryNode.get_child_id_static(node_id, i),
      )
      for i, choice in enumerate(metadata.choices)
    ]
    node.generation_status = NodeGenerationStatus.COMPLETED.value
    node.save()
    metrics.add_metric(name="StoryNodeGenerated", unit=MetricUnit.Count, value=1)

    if session_id:
      try:
        ns = get_node_session(session_id, node_id)
        if ns:
          ns.title = metadata.title
          ns.base_choice_states = [BaseChoiceStateMap(is_explored=False) for _ in metadata.choices]
          ns.save()
        else:
          create_node_session(
            session_id=session_id,
            node_id=node_id,
            root_world_id=world_id,
            title=metadata.title,
            base_choice_count=len(metadata.choices),
            parent_id=node.parent_id,
          )
      except Exception:
        logger.warning(
          "Failed to update/create NodeSession after text generation (session=%s, node=%s)",
          session_id,
          node_id,
          exc_info=True,
        )

    try:
      send_node_analysis_message(world_id, node_id)
    except SQSSendError:
      logger.error(f"Failed to enqueue analysis for node {node_id} -- node will remain in PENDING processing_status")

  except QuotaExceededError:
    logger.info(f"Quota exceeded for nodes for user {user_id}")
    node.generation_status = NodeGenerationStatus.INITIALIZED.value
    node.save()
    raise
  except Exception as e:
    logger.error(f"Error generating text for node {node_id}: {e}")
    if user_id and quota_reserved:
      release_quota(user_id, "nodes")
    node.generation_status = NodeGenerationStatus.FAILED.value
    node.save()
    raise
