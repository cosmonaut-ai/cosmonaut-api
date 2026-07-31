"""Admin-only diagnostic helpers for search and generation context previews."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from app.core.errors import BadRequestError
from app.models.dtos.admin import (
  AdminSoundtrackMatchHit,
  AdminSoundtrackMatchRequest,
  AdminSoundtrackMatchResponse,
  AdminWorldContextFactHit,
  AdminWorldContextPreviewRequest,
  AdminWorldContextPreviewResponse,
  AdminWorldContextSimilarNodeHit,
)
from app.models.entities.soundtrack import Soundtrack
from app.services import pinecone
from app.services.playlists import CONTENT_FILTER_ALLOWED_RATINGS
from app.services.story_nodes import get_node_entity
from app.services.story_processing import _get_branch_facts, _get_similar_nodes, _get_world_facts


def _hit_fields(hit: Any) -> dict[str, Any]:
  fields = getattr(hit, "fields", None)
  if isinstance(fields, Mapping):
    return dict(fields)
  return {}


def _hit_id(hit: Any) -> str | None:
  for attr in ("_id", "id"):
    value = getattr(hit, attr, None)
    if value is not None:
      return str(value)
  return None


def _hit_score(hit: Any) -> float | None:
  for attr in ("_score", "score"):
    value = getattr(hit, attr, None)
    if isinstance(value, int | float):
      return float(value)
  fields = _hit_fields(hit)
  value = fields.get("_score") or fields.get("score")
  if isinstance(value, int | float):
    return float(value)
  return None


def preview_soundtrack_matches(payload: AdminSoundtrackMatchRequest) -> AdminSoundtrackMatchResponse:
  """Preview playlist soundtrack search without creating a Playlist entity."""
  query = payload.query.strip()
  if not query:
    raise BadRequestError("Query text is required")

  allowed_ratings = CONTENT_FILTER_ALLOWED_RATINGS.get(payload.content_filter, CONTENT_FILTER_ALLOWED_RATINGS["none"])
  hits = cast(
    list[Any],
    pinecone.search_records(
      query=query,
      top_k=payload.top_k,
      filter={
        "entity_type": {"$eq": pinecone.EntityType.SOUNDTRACK},
        "status": {"$eq": "active"},
        "content_rating": {"$in": allowed_ratings},
      },
    ).result.hits,
  )

  ordered_candidates: list[tuple[str | None, str | None, float | None, str | None]] = []
  seen: set[str] = set()
  for hit in hits:
    fields = _hit_fields(hit)
    soundtrack_id = fields.get("soundtrack_id")
    soundtrack_id_str = str(soundtrack_id) if soundtrack_id else None
    pinecone_record_id = _hit_id(hit)
    dedupe_key = soundtrack_id_str or pinecone_record_id or f"hit:{len(ordered_candidates)}"
    if dedupe_key in seen:
      continue
    seen.add(dedupe_key)
    matched_text = fields.get("text")
    ordered_candidates.append(
      (
        soundtrack_id_str,
        pinecone_record_id,
        _hit_score(hit),
        str(matched_text) if matched_text else None,
      )
    )

  soundtrack_ids = [candidate[0] for candidate in ordered_candidates if candidate[0] is not None]
  soundtracks = list(
    Soundtrack.batch_get([(Soundtrack.pk(soundtrack_id), Soundtrack.sk()) for soundtrack_id in soundtrack_ids])
  )
  id_to_soundtrack = {str(soundtrack.id): soundtrack for soundtrack in soundtracks}

  matches: list[AdminSoundtrackMatchHit] = []
  for index, (soundtrack_id, pinecone_record_id, score, matched_text) in enumerate(ordered_candidates, start=1):
    soundtrack = id_to_soundtrack.get(soundtrack_id) if soundtrack_id is not None else None
    matches.append(
      AdminSoundtrackMatchHit(
        rank=index,
        soundtrack_id=soundtrack_id,
        pinecone_record_id=pinecone_record_id,
        score=score,
        matched_text=matched_text,
        missing_soundtrack=soundtrack_id is not None and soundtrack is None,
        soundtrack=soundtrack.to_dto() if soundtrack else None,
      )
    )

  return AdminSoundtrackMatchResponse(
    query=query,
    content_filter=payload.content_filter,
    allowed_ratings=allowed_ratings,
    matches=matches,
  )


def preview_world_context(payload: AdminWorldContextPreviewRequest) -> AdminWorldContextPreviewResponse:
  """Preview the Pinecone context buckets used around a world node."""
  node = get_node_entity(payload.world_id, payload.node_id) if payload.node_id else None
  provided_text = payload.text.strip() if payload.text else ""
  query_text = provided_text
  if not query_text and node is not None:
    query_text = str(node.text or node.story_summary or "").strip()
  if not query_text:
    raise BadRequestError("Provide text, or provide a node_id for a node with text")

  ancestor_node_ids = [ancestor.strip() for ancestor in payload.ancestor_node_ids if ancestor.strip()]
  if node is not None and not ancestor_node_ids:
    ancestor_node_ids = [str(ancestor) for ancestor in node.ancestors]

  world_facts = (
    _get_world_facts(payload.world_id, query_text, payload.world_facts_top_k) if payload.world_facts_top_k > 0 else []
  )
  branch_facts = (
    _get_branch_facts(payload.world_id, query_text, ancestor_node_ids, payload.branch_facts_top_k)
    if payload.branch_facts_top_k > 0 and ancestor_node_ids
    else []
  )
  similar_nodes = (
    _get_similar_nodes(payload.world_id, query_text, payload.similar_nodes_top_k)
    if payload.similar_nodes_top_k > 0
    else []
  )

  return AdminWorldContextPreviewResponse(
    world_id=payload.world_id,
    source_node_id=payload.node_id,
    query_text=query_text,
    ancestor_node_ids=ancestor_node_ids,
    stored_context=node.context.to_dto() if node is not None and node.context else None,
    world_facts=[AdminWorldContextFactHit(id=fact.id, text=fact.text, origin_node_id=None) for fact in world_facts],
    branch_facts=[
      AdminWorldContextFactHit(id=fact.id, text=fact.text, origin_node_id=fact.origin_node_id) for fact in branch_facts
    ],
    similar_nodes=[
      AdminWorldContextSimilarNodeHit(
        id=record.id,
        text=record.text,
        world_id=str((record.model_extra or {}).get("world_id"))
        if (record.model_extra or {}).get("world_id")
        else None,
      )
      for record in similar_nodes
    ],
  )
