"""Story nodes controller for fetching and generating story content."""

from __future__ import annotations

from aws_lambda_powertools import Logger
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pynamodb.exceptions import UpdateError

import app.services.story_nodes as node_service
from app.api.dependencies import require_world_read
from app.core.config import settings
from app.core.security import User, get_current_user
from app.models.dtos.story_node import ChooseRequestDTO, GenerationStatus, StoryNodeDTO
from app.models.entities.story_node import StoryNode
from app.models.voices import get_voice_by_id
from app.services.audio import generate_and_store_audio
from app.services.story_nodes import (
  InvalidChoiceError,
  InvalidProcessingStatusError,
  NodeNotFoundError,
  NodeProcessingError,
  NodeServiceError,
)
from app.services.usage import QuotaExceededError, check_and_increment, release_quota
from app.services.worlds import WorldNotFoundError

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

router = APIRouter(prefix="/worlds", tags=["story-nodes"])


class PaginatedNodesResponse(BaseModel):
  nodes: list[StoryNodeDTO]
  next_cursor: str | None = None


@router.get(
  "/{world_id}/nodes/",
  response_model=PaginatedNodesResponse,
  response_model_exclude_none=True,
  summary="List nodes in a world (paginated)",
)
async def list_nodes(
  world_id: str = Path(..., description="Identifier for the world"),
  current_user: User = Depends(get_current_user),
  limit: int = Query(100, ge=1, le=500, description="Maximum number of nodes to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedNodesResponse:
  """Return story nodes for a given world with optional pagination."""
  require_world_read(world_id, current_user)
  nodes, next_cursor = node_service.list_nodes(world_id, limit=limit, cursor=cursor)
  return PaginatedNodesResponse(
    nodes=[node.to_dto() for node in nodes],
    next_cursor=next_cursor,
  )


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
  require_world_read(world_id, current_user)
  try:
    node = node_service.get_node(world_id, node_id)
    return node.to_dto()
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


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
  - `choice_index`: Index of an existing choice (0-based)
  - `custom_choice`: Free text custom choice (max 200 characters)

  Exactly one of these must be provided.

  The endpoint:
  1. Validates the input (choice index bounds or custom choice length)
  2. For custom choices, adds the choice to the parent node's choice list
  3. Creates a new story node with generation_status=INITIALIZED (no text yet)
  4. Returns the initialized node

  To generate the story text, call the /generate-text endpoint.
  """
  # Validate that exactly one of choice_index or custom_choice is provided
  if (request.choice_index is None) == (request.custom_choice is None):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Exactly one of 'choice_index' or 'custom_choice' must be provided",
    )

  require_world_read(world_id, current_user)
  try:
    new_node = await node_service.choose(
      world_id,
      node_id,
      choice_index=request.choice_index,
      custom_choice=request.custom_choice,
      user_id=current_user.id,
    )
    return new_node.to_dto()
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
  except InvalidChoiceError as e:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
  except NodeProcessingError as e:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post(
  "/{world_id}/nodes/{node_id}/generate-text",
  summary="Generate story text for an initialized node (streaming)",
)
async def generate_text(
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
  require_world_read(world_id, current_user)

  # Validate node exists before starting stream.
  # Status validation is intentionally deferred to the service layer which uses
  # an atomic DynamoDB conditional write to prevent race conditions between
  # concurrent requests.
  try:
    node_service.get_node(world_id, node_id)
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

  async def event_generator():
    """Wrap the story node stream in SSE format for better Lambda/Mangum compatibility."""
    try:
      first_chunk = True
      async for chunk in node_service.generate_text(world_id, node_id, user_id=current_user.id):
        # Only strip leading whitespace from the very first chunk to avoid breaking SSE format
        # Preserve all other whitespace including newlines for proper paragraph formatting
        if first_chunk:
          chunk = chunk.lstrip()
          first_chunk = False

        if chunk:  # Only yield non-empty chunks
          # Replace actual newlines with a placeholder to preserve them in SSE format
          # The frontend will need to convert these back to newlines
          chunk_escaped = chunk.replace("\n", "\\n")
          yield f"data: {chunk_escaped}\n\n"
      # Send a done event to signal completion
      yield "data: [DONE]\n\n"
    except QuotaExceededError as e:
      yield f"event: error\ndata: {str(e)}\n\n"
    except NodeServiceError as e:
      # Catches NodeNotFoundError, NodeProcessingError, InvalidChoiceError,
      # InvalidGenerationStatusError, and any future NodeServiceError subclasses.
      yield f"event: error\ndata: {str(e)}\n\n"
    except WorldNotFoundError as e:
      yield f"event: error\ndata: {str(e)}\n\n"
    except Exception as e:
      # Intentional catch-all: unexpected errors during streaming (e.g. LLM metadata
      # parse failure, network timeouts) must emit an SSE error event instead of
      # silently dropping the connection.
      logger.error(f"Unexpected error during text generation for node {node_id}: {e}", exc_info=True)
      yield "event: error\ndata: An unexpected error occurred during generation\n\n"

  return StreamingResponse(
    event_generator(),
    media_type="text/event-stream",
    headers={
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",  # Disable buffering in nginx/proxies
    },
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
  require_world_read(world_id, current_user)
  try:
    node = node_service.retry_processing(world_id, node_id)
    return node.to_dto()
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
  except InvalidProcessingStatusError as e:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Audio narration
# ---------------------------------------------------------------------------


class AudioRequest(BaseModel):
  voice_id: str


class AudioResponse(BaseModel):
  audio_url: str


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
  # -- Validate voice --
  voice = get_voice_by_id(request.voice_id)
  if voice is None:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail=f"Unknown voice_id: {request.voice_id}",
    )

  require_world_read(world_id, current_user)

  # -- Fetch node --
  try:
    node = node_service.get_node(world_id, node_id)
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

  # Node must have completed text generation
  if GenerationStatus(node.generation_status) != GenerationStatus.COMPLETED:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail=f"Node {node_id} text has not been generated yet",
    )

  # -- Idempotency: return existing audio for this voice if present --
  existing_audio: dict[str, str] = dict(node.audio.attribute_values) if node.audio else {}
  if voice.id in existing_audio:
    return AudioResponse(audio_url=existing_audio[voice.id])

  # -- Quota check --
  try:
    check_and_increment(current_user.id, "audio")
  except QuotaExceededError as e:
    raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e

  # -- Generate & upload audio --
  try:
    cdn_url = generate_and_store_audio(
      world_id=world_id,
      node_id=node_id,
      text=str(node.text),
      voice_id=voice.id,
      elevenlabs_voiceid=voice.elevenlabs_voiceid,
    )
  except Exception as e:
    release_quota(current_user.id, "audio")
    logger.error(f"Audio generation failed for node {node_id}: {e}", exc_info=True)
    raise HTTPException(
      status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
      detail="Audio generation failed",
    ) from e

  # -- Persist audio URL on the node (conditional to prevent race conditions) --
  try:
    node.update(
      actions=[
        StoryNode.audio[voice.id].set(cdn_url),  # type: ignore[union-attr]
      ],
      condition=StoryNode.audio[voice.id].does_not_exist() | StoryNode.audio.does_not_exist(),  # type: ignore[union-attr]
    )
  except UpdateError:
    # Another request already set the audio URL for this voice — re-fetch and return.
    node.refresh()
    refreshed_audio: dict[str, str] = dict(node.audio.attribute_values) if node.audio else {}
    if voice.id in refreshed_audio:
      return AudioResponse(audio_url=refreshed_audio[voice.id])

  return AudioResponse(audio_url=cdn_url)
