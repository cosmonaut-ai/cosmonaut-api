"""Story nodes controller for fetching and generating story content."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse

import app.services.story_nodes as node_service
from app.core.security import User, get_current_user
from app.models.dtos.story_node import ChooseRequestDTO, StoryNodeDTO
from app.services.story_nodes import InvalidChoiceError, NodeNotFoundError
from app.services.worlds import WorldNotFoundError, get_world_entity

router = APIRouter(prefix="/worlds", tags=["story-nodes"])


@router.get(
  "/{world_id}/nodes/",
  response_model=list[StoryNodeDTO],
  response_model_exclude_none=True,
  summary="List all nodes in a world",
)
async def list_nodes(
  world_id: str = Path(..., description="Identifier for the world"),
  current_user: User = Depends(get_current_user),
) -> list[StoryNodeDTO]:
  """Return all story nodes for a given world."""
  # Check authorization
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

  if not world.can_user_read(current_user.id):
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail=f"You are not authorized to access world {world_id}",
    )

  nodes = node_service.list_nodes(world_id)
  return [node.to_dto() for node in nodes]


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
  # Check authorization
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

  if not world.can_user_read(current_user.id):
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail=f"You are not authorized to access world {world_id}",
    )

  try:
    node = node_service.get_node(world_id, node_id)
    return node.to_dto()
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post(
  "/{world_id}/nodes/{node_id}/choose",
  status_code=status.HTTP_201_CREATED,
  summary="Choose an option and generate the next story node (streaming)",
)
async def choose(
  world_id: str = Path(..., description="Identifier for the world"),
  node_id: str = Path(..., description="Identifier for the current node"),
  request: ChooseRequestDTO = Body(...),
  current_user: User = Depends(get_current_user),
) -> StreamingResponse:
  """Select a choice from the current node and stream the generated text of the next story node.

  This endpoint accepts either:
  - `choice_index`: Index of an existing choice (0-based)
  - `custom_choice`: Free text custom choice (max 200 characters)

  Exactly one of these must be provided.

  The endpoint:
  1. Validates the input (choice index bounds or custom choice length)
  2. For custom choices, adds the choice to the node's choice list
  3. Generates a new story node text stream based on the selected/custom choice
  4. Updates the parent node's choice to link to the new node (after stream finishes)
  5. Returns a stream of the generated text using Server-Sent Events (SSE) format
  """
  # Validate that exactly one of choice_index or custom_choice is provided
  if (request.choice_index is None) == (request.custom_choice is None):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Exactly one of 'choice_index' or 'custom_choice' must be provided",
    )

  # Check authorization
  try:
    world = get_world_entity(world_id)
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

  if not world.can_user_read(current_user.id):
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail=f"You are not authorized to access world {world_id}",
    )

  try:
    new_node_id, stream = await node_service.choose(
      world_id,
      node_id,
      choice_index=request.choice_index,
      custom_choice=request.custom_choice,
      user_id=current_user.id,
    )
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
  except InvalidChoiceError as e:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

  async def event_generator():
    """Wrap the story node stream in SSE format for better Lambda/Mangum compatibility."""
    try:
      first_chunk = True
      async for chunk in stream:
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
    except NodeNotFoundError as e:
      yield f"event: error\ndata: {str(e)}\n\n"
    except WorldNotFoundError as e:
      yield f"event: error\ndata: {str(e)}\n\n"
    except InvalidChoiceError as e:
      yield f"event: error\ndata: {str(e)}\n\n"

  return StreamingResponse(
    event_generator(),
    media_type="text/event-stream",
    headers={
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",  # Disable buffering in nginx/proxies
      "X-New-Node-Id": new_node_id,
      "Access-Control-Expose-Headers": "X-New-Node-Id",
    },
  )
