"""Story nodes controller for fetching and generating story content."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, Depends, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pynamodb.exceptions import UpdateError

import app.services.story_nodes as node_service
from app.api.dependencies import node_session_to_list_dto, require_session_read, require_world_read
from app.core.errors import AppError, BadRequestError, WrongSessionForNodeError
from app.core.llm_telemetry import flush as llm_flush
from app.core.observability import MetricUnit, logger, metrics
from app.core.posthog import capture as ph_capture
from app.core.security import User, get_current_user
from app.models.dtos.base import PaginatedResponse
from app.models.dtos.story_node import ChooseRequestDTO, GenerationStatus, StoryNodeDTO
from app.models.entities.story_node import StoryNode
from app.models.voices import get_voice_by_id
from app.services.audio import generate_and_store_audio
from app.services.rate_limiter import check_rate_limit
from app.services.sessions import get_node_session, list_node_sessions, update_session_progress
from app.services.story_nodes import NodeServiceError, merge_choices
from app.services.usage import QuotaExceededError, check_and_increment, release_quota
from app.services.worlds import WorldNotFoundError

router = APIRouter(prefix="/worlds", tags=["story-nodes"])

MAX_NARRATION_CHARS = 3000


@router.get(
  "/{world_id}/nodes/",
  response_model=PaginatedResponse[StoryNodeDTO],
  response_model_exclude_none=True,
  summary="List nodes in a world (paginated)",
)
async def list_nodes(
  world_id: str = Path(..., description="Identifier for the world"),
  current_user: User = Depends(get_current_user),
  limit: int = Query(100, ge=1, le=500, description="Maximum number of nodes to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[StoryNodeDTO]:
  """Return story nodes for a given world with optional pagination."""
  session, _ = require_session_read(world_id, current_user)
  session_id = str(session.id)
  root_world_id = str(session.root_world_id)
  node_sessions, next_cursor = list_node_sessions(session_id, limit=limit, cursor=cursor)
  dtos = [node_session_to_list_dto(ns, root_world_id) for ns in node_sessions]
  return PaginatedResponse[StoryNodeDTO](items=dtos, next_cursor=next_cursor)


@router.get(
  "/{world_id}/nodes/{node_id}",
  response_model=StoryNodeDTO,
  summary="Fetch a single story node",
)
async def get_node(
  world_id: str = Path(..., description="Identifier for the world"),
  node_id: str = Path(..., description="Identifier for the node"),
  current_user: User = Depends(get_current_user),
) -> StoryNodeDTO:
  """Retrieve a single story node by its identifier."""
  session, _ = require_session_read(world_id, current_user)
  root_world_id = str(session.root_world_id)
  session_id = str(session.id)
  node = node_service.get_node(root_world_id, node_id)
  if node.source_session_id and str(node.source_session_id) != session_id:
    raise WrongSessionForNodeError(f"Node {node_id} belongs to another session")
  dto = node.to_dto()
  ns = get_node_session(session_id, node_id)
  dto.choices = merge_choices(
    node.choices,
    ns.base_choice_states if ns else [],
    ns.custom_choices if ns else [],
  )
  return dto


class ProgressResponse(BaseModel):
  current_node_id: str | None = None


@router.get(
  "/{world_id}/progress",
  response_model=ProgressResponse,
  summary="Get the user's last-visited node in a world",
)
async def get_progress(
  world_id: str = Path(..., description="Identifier for the world"),
  current_user: User = Depends(get_current_user),
) -> ProgressResponse:
  """Return the last story node the authenticated user visited in this world."""
  session, _ = require_world_read(world_id, current_user)
  if session is None:
    return ProgressResponse(current_node_id=None)
  progress_map = session.per_member_progress.attribute_values if session.per_member_progress else {}
  node_id_val = progress_map.get(current_user.id)
  return ProgressResponse(current_node_id=str(node_id_val) if node_id_val else None)


@router.post(
  "/{world_id}/nodes/{node_id}/choose",
  response_model=StoryNodeDTO,
  response_model_exclude_none=True,
  status_code=status.HTTP_201_CREATED,
  summary="Choose an option and initialize the next story node",
)
async def choose(
  world_id: str = Path(..., description="Identifier for the world"),
  node_id: str = Path(..., description="Identifier for the current node"),
  request: ChooseRequestDTO = Body(...),
  current_user: User = Depends(get_current_user),
) -> StoryNodeDTO:
  """Select a choice from the current node and initialize the next story node.

  This endpoint accepts either:
  - `target_id`: Deterministic child node ID for an existing choice (base or custom)
  - `custom_choice`: Free text custom choice (max 200 characters)

  Exactly one of these must be provided.

  The endpoint:
  1. Validates the target is a child of the current node (or creates one for custom choices)
  2. Creates a new story node with generation_status=INITIALIZED if one does not exist
  3. Returns the child node (new or existing)

  To generate the story text, call the /generate-text endpoint.
  """
  if (request.target_id is None) == (request.custom_choice is None):
    raise BadRequestError("Exactly one of 'target_id' or 'custom_choice' must be provided")

  session, _ = require_session_read(world_id, current_user)
  root_world_id = str(session.root_world_id)
  session_id = str(session.id)
  new_node = await node_service.choose_with_session(
    root_world_id=root_world_id,
    session_id=session_id,
    node_id=node_id,
    target_id=request.target_id,
    custom_choice=request.custom_choice,
    user_id=current_user.id,
  )
  update_session_progress(session_id, root_world_id, current_user.id, str(new_node.id))
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
      "source": "server",
    },
  )
  return dto


@router.post(
  "/{world_id}/nodes/{node_id}/generate-text",
  summary="Generate story text for an initialized node (streaming)",
)
async def generate_text(
  request: Request,
  world_id: str = Path(..., description="Identifier for the world"),
  node_id: str = Path(..., description="Identifier for the node to generate text for"),
  current_user: User = Depends(get_current_user),
) -> StreamingResponse:
  """Generate story text for a node with INITIALIZED or FAILED generation status.

  This endpoint streams the generated text using Server-Sent Events (SSE) format.
  The node must have been previously created via the /choose endpoint.

  The endpoint:
  1. Validates the node exists and has INITIALIZED or FAILED generation_status
  2. Updates generation_status to GENERATING
  3. Streams the generated story text
  4. Updates the node with generated text, title, choices, and sets generation_status to COMPLETED
  5. On error, sets generation_status to FAILED
  """
  session, _ = require_session_read(world_id, current_user)
  root_world_id = str(session.root_world_id)
  session_id = str(session.id)
  node = node_service.get_node(root_world_id, node_id)
  if node.source_session_id and str(node.source_session_id) != session_id:
    raise WrongSessionForNodeError(f"Node {node_id} belongs to another session")
  check_rate_limit(current_user.id, "generate-text")
  metrics.add_metric(name="StoryNodeStreamStarted", unit=MetricUnit.Count, value=1)

  # Decouple LLM generation from the SSE response so that a client
  # disconnect does not kill the in-flight generation.  The service
  # generator runs in its own task and feeds chunks through a queue;
  # if the client drops, the task still runs to completion and the
  # node is properly saved as COMPLETED (or FAILED).
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
        properties={"world_id": root_world_id, "node_id": node_id, "source": "server"},
      )
      await queue.put(None)
    except Exception as exc:
      ph_capture(
        "story_text_generation_failed",
        distinct_id=current_user.id,
        properties={
          "error_type": type(exc).__name__,
          "world_id": root_world_id,
          "node_id": node_id,
          "source": "server",
        },
      )
      await queue.put(exc)
    finally:
      # Flush OTel LLM spans here rather than in the request middleware: the
      # middleware's finally block fires when StreamingResponse is returned,
      # before this generation task runs and before the Claude span exists.
      llm_flush()

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
      # If the client disconnected, log but let the task finish in the
      # background.  If the generator ended normally, await the task to
      # propagate any unexpected errors.
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
  "/{world_id}/nodes/{node_id}/retry-processing",
  response_model=StoryNodeDTO,
  summary="Retry processing for a failed node",
)
async def retry_processing(
  world_id: str = Path(..., description="Identifier for the world"),
  node_id: str = Path(..., description="Identifier for the node to retry processing for"),
  current_user: User = Depends(get_current_user),
) -> StoryNodeDTO:
  """Re-enqueue a node whose processing (fact extraction) failed.

  Resets processing_status from FAILED back to PENDING and sends a new
  analysis message to the worker queue. Only nodes with
  processing_status=FAILED can be retried.
  """
  session, _ = require_session_read(world_id, current_user)
  node = node_service.retry_processing(str(session.root_world_id), node_id)
  return node.to_dto()


# ---------------------------------------------------------------------------
# Audio narration
# ---------------------------------------------------------------------------


class AudioRequest(BaseModel):
  voice_id: str


class AudioResponse(BaseModel):
  audio_url: str
  timestamps_url: str | None = None


@router.post(
  "/{world_id}/nodes/{node_id}/audio",
  response_model=AudioResponse,
  summary="Generate TTS audio narration for a story node",
)
async def generate_node_audio(
  world_id: str = Path(..., description="Identifier for the world"),
  node_id: str = Path(..., description="Identifier for the story node"),
  request: AudioRequest = Body(...),
  current_user: User = Depends(get_current_user),
) -> AudioResponse:
  """Generate audio narration for a completed story node using the chosen voice.

  The endpoint is **idempotent per voice**: if audio has already been generated
  for this node with the requested voice, the existing URL is returned
  immediately without consuming quota.

  Workflow:
    1. Validate the ``voice_id`` against the voice registry.
    2. Verify the node exists and has completed text generation.
    3. Return the existing audio URL for the voice if present.
    4. Check the user's audio narration quota.
    5. Generate audio via ElevenLabs TTS (Flash 2.5).
    6. Upload the MP3 to S3 and persist the URL on the node.
    7. Return the CDN URL.
  """
  voice = get_voice_by_id(request.voice_id)
  if voice is None:
    raise BadRequestError(f"Unknown voice_id: {request.voice_id}")

  session, _ = require_session_read(world_id, current_user)
  resolved_world_id = str(session.root_world_id)
  session_id = str(session.id)
  check_rate_limit(current_user.id, "audio")
  node = node_service.get_node(resolved_world_id, node_id)
  if node.source_session_id and str(node.source_session_id) != session_id:
    raise WrongSessionForNodeError(f"Node {node_id} belongs to another session")

  if GenerationStatus(node.generation_status) != GenerationStatus.COMPLETED:
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
      world_id=resolved_world_id,
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
      "world_id": resolved_world_id,
      "node_id": node_id,
      "text_length": len(text),
      "source": "server",
    },
  )
  return AudioResponse(audio_url=audio_cdn_url, timestamps_url=timestamps_cdn_url)
