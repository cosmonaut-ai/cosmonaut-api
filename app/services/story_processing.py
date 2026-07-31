"""Story node processing: fact extraction and Pinecone upsert.

Handles post-generation async work: querying Pinecone for context,
extracting facts via LLM, and upserting results back to Pinecone.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, cast

from pynamodb.exceptions import UpdateError

import app.services.llm as llm
import app.services.pinecone as pinecone
from app.core.observability import logger, tracer
from app.models.dtos.story_node import StoryNodeProcessingStatus
from app.models.entities.story_node import StoryNode, StoryNodeContext
from app.services.llm.sanitize import sanitize_llm_output
from app.services.pinecone import PineconeBranchFact
from app.services.sqs import send_node_analysis_message
from app.services.story_nodes import (
  InvalidProcessingStatusError,
  get_node_entity,
)

# =============================================================================
# Pinecone Query Helpers
# =============================================================================


def _get_world_facts(world_id: str, node_text: str, top_k: int = 50) -> list[pinecone.PineconeWorldFact]:
  """Get the world facts for a given node."""
  hits = cast(
    list[Any],
    pinecone.search_records(
      query=node_text,
      top_k=top_k,
      filter={
        "world_id": {"$eq": world_id},
        "entity_type": {"$eq": pinecone.EntityType.WORLD_FACT},
      },
    ).result.hits,
  )
  return [pinecone.PineconeWorldFact.model_validate({**x.fields, "id": x._id}) for x in hits]


def _get_branch_facts(
  world_id: str, node_text: str, ancestors: list[str], top_k: int = 50
) -> list[pinecone.PineconeBranchFact]:
  """Get the branch facts for a given node."""
  hits = cast(
    list[Any],
    pinecone.search_records(
      query=node_text,
      top_k=top_k,
      filter={
        "world_id": {"$eq": world_id},
        "entity_type": {"$eq": pinecone.EntityType.BRANCH_FACT},
        "origin_node_id": {"$in": ancestors},
      },
    ).result.hits,
  )
  results: list[PineconeBranchFact] = [
    pinecone.PineconeBranchFact.model_validate({**x.fields, "id": x._id}) for x in hits
  ]
  return sorted(results, key=lambda x: -ancestors.index(x.origin_node_id))


def _get_similar_nodes(world_id: str, node_text: str, top_k: int = 3) -> list[pinecone.PineconeRecord]:
  """Get the similar story nodes for a given node."""
  hits = cast(
    list[Any],
    pinecone.search_records(
      query=node_text,
      top_k=top_k,
      filter={
        "world_id": {"$eq": world_id},
        "entity_type": {"$eq": pinecone.EntityType.NODE_TEXT},
      },
    ).result.hits,
  )
  return [pinecone.PineconeRecord.model_validate({**x.fields, "id": x._id}) for x in hits]


# =============================================================================
# Node Processing
# =============================================================================


@tracer.capture_method
async def process_node(node: StoryNode) -> None:
  """Process a node: extract facts and upsert to Pinecone."""
  world_id = node.world_id

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

  fact_deps = llm.LLMFactExtractionDeps(
    text=node.text,
    user_choice=node.parent_choice.label if node.parent_choice else None,
  )
  facts = await llm.generate_facts_async(fact_deps)

  facts.world_facts = [sanitize_llm_output(f) for f in facts.world_facts]
  facts.branch_facts = [sanitize_llm_output(f) for f in facts.branch_facts]

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


def retry_processing(world_id: str, node_id: str) -> StoryNode:
  """Re-enqueue a failed node for processing (fact extraction + Pinecone upsert).

  Atomically resets processing_status from FAILED back to PENDING and sends a
  new analysis message to the fast worker queue.

  Returns:
    The updated StoryNode with processing_status=PENDING.

  Raises:
    NodeNotFoundError: If the node does not exist.
    InvalidProcessingStatusError: If the node is not in FAILED processing status.
  """
  node = get_node_entity(world_id, node_id)

  try:
    node.update(
      actions=[StoryNode.processing_status.set(StoryNodeProcessingStatus.PENDING.value)],
      condition=StoryNode.processing_status == StoryNodeProcessingStatus.FAILED.value,
    )
  except UpdateError:
    node = get_node_entity(world_id, node_id)
    raise InvalidProcessingStatusError(node_id, StoryNodeProcessingStatus(node.processing_status)) from None

  send_node_analysis_message(world_id, node_id)
  logger.info(f"Re-enqueued failed node {node_id} in world {world_id} for processing")

  return node
