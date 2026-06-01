"""Session controller for user playthrough state, nodes, and audio."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, Depends, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pynamodb.exceptions import UpdateError

import app.services.story_nodes as node_service
import app.services.worlds as world_service
from app.api.dependencies import require_session_read
from app.api.mappers import membership_to_session_summary_dto, node_session_to_list_dto, session_to_dto
from app.core.errors import AppError, BadRequestError, SessionAccessDeniedError, WrongSessionForNodeError
from app.core.llm_telemetry import flush as llm_flush
from app.core.observability import MetricUnit, logger, metrics
from app.core.posthog import capture as ph_capture
from app.core.posthog import flush as ph_flush
from app.core.security import User, get_current_user
from app.models.dtos.base import PaginatedResponse
from app.models.dtos.session import SessionLinkHandoffDTO, WorldSessionDTO, WorldSessionSummaryDTO
from app.models.dtos.story_node import ChooseRequestDTO, NodeGenerationStatus, StoryNodeDTO
from app.models.entities.story_node import StoryNode
from app.models.entities.world_session import WorldSession
from app.models.voices import get_voice_by_id
from app.services.audio import generate_and_store_audio
from app.services.rate_limiter import check_rate_limit
from app.services.sessions import (
  delete_session_for_user,
  find_session,
  get_node_session,
  get_session_membership,
  list_node_sessions,
  list_user_sessions,
  update_session_progress,
)
from app.services.story_nodes import NodeServiceError, merge_choices
from app.services.usage import QuotaExceededError, check_and_increment, release_quota
from app.services.worlds import WorldNotFoundError

router = APIRouter(prefix="/sessions", tags=["sessions"])

MAX_NARRATION_CHARS = 3000


class AudioRequest(BaseModel):
  voice_id: str


class AudioResponse(BaseModel):
  audio_url: str
  timestamps_url: str | None = None


def _assert_node_in_session(node: StoryNode, session_id: str) -> None:
  if node.source_session_id and str(node.source_session_id) != session_id:
    raise WrongSessionForNodeError(f"Node {node.id} belongs to another session")


def _session_context(session_id: str, user: User) -> tuple[WorldSession, str]:
  session, _ = require_session_read(session_id, user)
  return session, str(session.root_world_id)


def _node_context(session_id: str, node_id: str, user: User) -> tuple[WorldSession, str, StoryNode]:
  session, root_world_id = _session_context(session_id, user)
  node = node_service.get_node(root_world_id, node_id)
  _assert_node_in_session(node, session_id)
  return session, root_world_id, node


@router.get(
  "/",
  response_model=PaginatedResponse[WorldSessionSummaryDTO],
  summary="List the current user's playthrough sessions",
)
async def list_sessions(
  user: User = Depends(get_current_user),
  limit: int = Query(50, ge=1, le=200, description="Maximum number of sessions to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[WorldSessionSummaryDTO]:
  """Return dashboard sessions for the authenticated user with cursor-based pagination."""
  memberships, next_cursor = list_user_sessions(user.id, limit, cursor)
  items = [membership_to_session_summary_dto(m) for m in memberships]
  return PaginatedResponse(items=items, next_cursor=next_cursor)


@router.get(
  "/{session_id}",
  response_model=WorldSessionDTO,
  summary="Fetch a session and its root world",
)
async def get_session(
  session_id: str = Path(..., description="Playthrough session identifier"),
  user: User = Depends(get_current_user),
) -> WorldSessionDTO:
  """Return session detail for members, including the full embedded root world."""
  session, world = require_session_read(session_id, user)
  membership = get_session_membership(user.id, str(session.root_world_id), session_id)
  return session_to_dto(session, world, membership, user.id)


@router.get(
  "/{session_id}/handoff",
  response_model=SessionLinkHandoffDTO,
  summary="Resolve a shared session link to its accessible root world",
)
async def get_session_handoff(
  session_id: str = Path(..., description="Playthrough session identifier"),
  user: User = Depends(get_current_user),
) -> SessionLinkHandoffDTO:
  """Return root-world routing data when a viewer can read the world behind a session link."""
  session = find_session(session_id)
  if session is None:
    from app.core.errors import SessionNotFoundError

    raise SessionNotFoundError(f"Session not found: {session_id}")

  world = world_service.get_world_entity(str(session.root_world_id))
  if user.id not in [str(m) for m in (session.members or [])] and not world.can_user_read(user.id):
    raise SessionAccessDeniedError("This private playthrough is not accessible")

  return SessionLinkHandoffDTO(
    root_world_id=str(world.id),
    title=world.title,
    description=world.description,
    world_image_url=world.world_image_url,
    world_image_alt_text=world.world_image_alt_text,
  )


@router.delete(
  "/{session_id}",
  status_code=status.HTTP_204_NO_CONTENT,
  summary="Remove the current user's playthrough session",
)
async def delete_session(
  session_id: str = Path(..., description="Playthrough session identifier"),
  user: User = Depends(get_current_user),
) -> None:
  """Remove the caller's library/session entry and hard-delete orphaned root worlds."""
  session, root_world_id = _session_context(session_id, user)

  is_orphaned = delete_session_for_user(
    session_id=str(session.id),
    user_id=user.id,
    root_world_id=root_world_id,
  )

  if is_orphaned:
    world_service.hard_delete_orphaned_world(root_world_id)

  ph_capture(
    "session_deleted",
    distinct_id=user.id,
    properties={"is_orphaned": is_orphaned, "session_id": session_id, "world_id": root_world_id, "source": "server"},
  )


@router.get(
  "/{session_id}/nodes/",
  response_model=PaginatedResponse[StoryNodeDTO],
  response_model_exclude_none=True,
  summary="List nodes visited in a session",
)
async def list_nodes(
  session_id: str = Path(..., description="Playthrough session identifier"),
  current_user: User = Depends(get_current_user),
  limit: int = Query(100, ge=1, le=500, description="Maximum number of nodes to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[StoryNodeDTO]:
  """Return graph-compatible node overlays for a given session."""
  _, root_world_id = _session_context(session_id, current_user)
  node_sessions, next_cursor = list_node_sessions(session_id, limit=limit, cursor=cursor)
  dtos = [node_session_to_list_dto(ns, root_world_id) for ns in node_sessions]
  return PaginatedResponse[StoryNodeDTO](items=dtos, next_cursor=next_cursor)


@router.get(
  "/{session_id}/nodes/{node_id}",
  response_model=StoryNodeDTO,
  summary="Fetch a single story node for a session",
)
async def get_node(
  session_id: str = Path(..., description="Playthrough session identifier"),
  node_id: str = Path(..., description="Identifier for the node"),
  current_user: User = Depends(get_current_user),
) -> StoryNodeDTO:
  """Retrieve a story node and merge in the caller's session overlay."""
  _, _, node = _node_context(session_id, node_id, current_user)
  dto = node.to_dto()
  ns = get_node_session(session_id, node_id)
  dto.choices = merge_choices(
    node.choices,
    ns.base_choice_states if ns else [],
    ns.custom_choices if ns else [],
  )
  return dto


@router.post(
  "/{session_id}/nodes/{node_id}/choose",
  response_model=StoryNodeDTO,
  response_model_exclude_none=True,
  status_code=status.HTTP_201_CREATED,
  summary="Choose an option and initialize the next story node",
)
async def choose(
  session_id: str = Path(..., description="Playthrough session identifier"),
  node_id: str = Path(..., description="Identifier for the current node"),
  request: ChooseRequestDTO = Body(...),
  current_user: User = Depends(get_current_user),
) -> StoryNodeDTO:
  """Select a choice from the current node and initialize the next story node."""
  if (request.target_id is None) == (request.custom_choice is None):
    raise BadRequestError("Exactly one of 'target_id' or 'custom_choice' must be provided")

  _, root_world_id, _ = _node_context(session_id, node_id, current_user)
  node_existed_before = request.target_id is not None and get_node_session(session_id, request.target_id) is not None
  new_node = await node_service.choose_with_session(
    root_world_id=root_world_id,
    session_id=session_id,
    node_id=node_id,
    target_id=request.target_id,
    custom_choice=request.custom_choice,
    user_id=current_user.id,
  )
  update_session_progress(
    session_id,
    root_world_id,
    current_user.id,
    str(new_node.id),
    is_new_node=not node_existed_before,
  )
  dto = new_node.to_dto()
  ns = get_node_session(session_id, str(new_node.id))
  dto.choices = merge_choices(
    new_node.choices,
    ns.base_choice_states if ns else [],
    ns.custom_choices if ns else [],
  )
  ph_capture(
    "story_choice_made",
    distinct_id=current_user.id,
    properties={
      "is_custom_choice": request.custom_choice is not None,
      "world_id": root_world_id,
      "session_id": session_id,
      "source": "server",
    },
  )
  return dto


@router.post(
  "/{session_id}/nodes/{node_id}/generate-text",
  summary="Generate story text for an initialized node (streaming)",
)
async def generate_text(
  request: Request,
  session_id: str = Path(..., description="Playthrough session identifier"),
  node_id: str = Path(..., description="Identifier for the node to generate text for"),
  current_user: User = Depends(get_current_user),
) -> StreamingResponse:
  """Generate story text for a node with INITIALIZED or FAILED generation status."""
  _, root_world_id, _ = _node_context(session_id, node_id, current_user)
  check_rate_limit(current_user.id, "generate-text")
  metrics.add_metric(name="StoryNodeStreamStarted", unit=MetricUnit.Count, value=1)

  queue: asyncio.Queue[str | Exception | None] = asyncio.Queue()

  async def _run_generation() -> None:
    try:
      async for chunk in node_service.generate_text(
        root_world_id, node_id, user_id=current_user.id, session_id=session_id
      ):
        await queue.put(chunk)
      ph_capture(
        "story_text_generated",
        distinct_id=current_user.id,
        properties={"world_id": root_world_id, "session_id": session_id, "node_id": node_id, "source": "server"},
      )
      await queue.put(None)
    except Exception as exc:
      ph_capture(
        "story_text_generation_failed",
        distinct_id=current_user.id,
        properties={
          "error_type": type(exc).__name__,
          "world_id": root_world_id,
          "session_id": session_id,
          "node_id": node_id,
          "source": "server",
        },
      )
      await queue.put(exc)
    finally:
      llm_flush()
      ph_flush()

  generation_task = asyncio.create_task(_run_generation())

  async def event_generator():
    try:
      first_chunk = True
      while True:
        try:
          item = await asyncio.wait_for(queue.get(), timeout=5.0)
        except TimeoutError:
          if await request.is_disconnected():
            logger.info("Client disconnected during text generation for node %s", node_id)
            return
          continue

        if item is None:
          yield "data: [DONE]\n\n"
          return

        if isinstance(item, Exception):
          if isinstance(item, QuotaExceededError | NodeServiceError | WorldNotFoundError):
            yield f"event: error\ndata: {item!s}\n\n"
          else:
            logger.error(f"Unexpected error during text generation for node {node_id}: {item}", exc_info=True)
            yield "event: error\ndata: An unexpected error occurred during generation\n\n"
          return

        chunk: str = item
        if first_chunk:
          chunk = chunk.lstrip()
          first_chunk = False
        if chunk:
          chunk_escaped = chunk.replace("\n", "\\n")
          yield f"data: {chunk_escaped}\n\n"
    finally:
      if generation_task.done():
        exc = generation_task.exception()
        if exc:
          logger.error("Generation task failed for node %s: %s", node_id, exc, exc_info=exc)
      else:
        logger.info("SSE response ended for node %s; generation continues in background", node_id)

  return StreamingResponse(
    event_generator(),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
  )


@router.post(
  "/{session_id}/nodes/{node_id}/retry-processing",
  response_model=StoryNodeDTO,
  summary="Retry processing for a failed node",
)
async def retry_processing(
  session_id: str = Path(..., description="Playthrough session identifier"),
  node_id: str = Path(..., description="Identifier for the node to retry processing for"),
  current_user: User = Depends(get_current_user),
) -> StoryNodeDTO:
  """Re-enqueue a node whose processing (fact extraction) failed."""
  _, root_world_id, _ = _node_context(session_id, node_id, current_user)
  node = node_service.retry_processing(root_world_id, node_id)
  return node.to_dto()


@router.post(
  "/{session_id}/nodes/{node_id}/audio",
  response_model=AudioResponse,
  summary="Generate TTS audio narration for a story node",
)
async def generate_node_audio(
  session_id: str = Path(..., description="Playthrough session identifier"),
  node_id: str = Path(..., description="Identifier for the story node"),
  request: AudioRequest = Body(...),
  current_user: User = Depends(get_current_user),
) -> AudioResponse:
  """Generate audio narration for a completed story node using the chosen voice."""
  voice = get_voice_by_id(request.voice_id)
  if voice is None:
    raise BadRequestError(f"Unknown voice_id: {request.voice_id}")

  _, root_world_id, node = _node_context(session_id, node_id, current_user)
  check_rate_limit(current_user.id, "audio")

  if NodeGenerationStatus(node.generation_status) != NodeGenerationStatus.COMPLETED:
    raise BadRequestError(f"Node {node_id} text has not been generated yet")

  text = str(node.text)
  if len(text) > MAX_NARRATION_CHARS:
    raise BadRequestError(f"Node text exceeds the maximum of {MAX_NARRATION_CHARS:,} characters for audio narration")

  existing_audio = dict(node.audio.attribute_values) if node.audio else {}
  if voice.id in existing_audio:
    entry = existing_audio[voice.id]
    audio_url = entry["audio_url"] if isinstance(entry, dict) else str(entry)
    timestamps_url = entry.get("timestamps_url") if isinstance(entry, dict) else None
    return AudioResponse(audio_url=audio_url, timestamps_url=timestamps_url)

  check_and_increment(current_user.id, "audio", email=current_user.email)

  try:
    audio_cdn_url, timestamps_cdn_url = await generate_and_store_audio(
      world_id=root_world_id,
      node_id=node_id,
      text=str(node.text),
      voice_id=voice.id,
      elevenlabs_voiceid=voice.elevenlabs_voiceid,
    )
  except Exception as e:
    release_quota(current_user.id, "audio")
    logger.error(f"Audio generation failed for node {node_id}: {e}", exc_info=True)
    raise AppError("Audio generation failed") from e

  audio_entry = {"audio_url": audio_cdn_url, "timestamps_url": timestamps_cdn_url}
  try:
    node.update(
      actions=[
        StoryNode.audio[voice.id].set(audio_entry),  # type: ignore  # PynamoDB MapAttribute subscript
      ],
      condition=StoryNode.audio[voice.id].does_not_exist() | StoryNode.audio.does_not_exist(),  # type: ignore  # PynamoDB condition expression
    )
  except UpdateError:
    release_quota(current_user.id, "audio")
    node.refresh()
    refreshed_audio = dict(node.audio.attribute_values) if node.audio else {}
    if voice.id in refreshed_audio:
      entry = refreshed_audio[voice.id]
      audio_url = entry["audio_url"] if isinstance(entry, dict) else str(entry)
      timestamps_url = entry.get("timestamps_url") if isinstance(entry, dict) else None
      return AudioResponse(audio_url=audio_url, timestamps_url=timestamps_url)

  ph_capture(
    "audio_narration_generated",
    distinct_id=current_user.id,
    properties={
      "voice_id": voice.id,
      "world_id": root_world_id,
      "session_id": session_id,
      "node_id": node_id,
      "text_length": len(text),
      "source": "server",
    },
  )
  return AudioResponse(audio_url=audio_cdn_url, timestamps_url=timestamps_cdn_url)
