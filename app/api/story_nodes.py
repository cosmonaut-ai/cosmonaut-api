"""Story nodes controller for fetching and generating story content."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, status
from fastapi.responses import StreamingResponse

import app.services.story_nodes as node_service
from app.models.dtos.story_node import StoryNodeDTO
from app.services.story_nodes import InvalidChoiceError, NodeNotFoundError
from app.services.worlds import WorldNotFoundError

router = APIRouter(prefix="/worlds", tags=["story-nodes"])


@router.get(
  "/{world_id}/nodes/",
  response_model=list[StoryNodeDTO],
  response_model_exclude_none=True,
  summary="List all nodes in a world",
)
async def list_nodes(
  world_id: str = Path(..., description="Identifier for the world"),
) -> list[StoryNodeDTO]:
  """Return all story nodes for a given world."""
  return node_service.list_nodes(world_id)


@router.get(
  "/{world_id}/nodes/{node_id}",
  response_model=StoryNodeDTO,
  summary="Fetch a single story node",
)
async def get_node(
  world_id: str = Path(..., description="Identifier for the world"),
  node_id: str = Path(..., description="Identifier for the node"),
) -> StoryNodeDTO:
  """Retrieve a single story node by its identifier."""
  try:
    return node_service.get_node(world_id, node_id)
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post(
  "/{world_id}/nodes/{node_id}/choose/{choice_index}",
  status_code=status.HTTP_201_CREATED,
  summary="Choose an option and generate the next story node (streaming)",
)
async def choose(
  world_id: str = Path(..., description="Identifier for the world"),
  node_id: str = Path(..., description="Identifier for the current node"),
  choice_index: int = Path(..., description="Index of the choice to select (0-based)", ge=0),
) -> StreamingResponse:
  """Select a choice from the current node and stream the generated text of the next story node.

  This endpoint:
  1. Validates the choice index is within bounds
  2. Generates a new story node text stream based on the selected choice
  3. Updates the parent node's choice to link to the new node (after stream finishes)
  4. Returns a stream of the generated text using Server-Sent Events (SSE) format
  """

  async def event_generator():
    """Wrap the story node stream in SSE format for better Lambda/Mangum compatibility."""
    try:
      first_chunk = True
      async for chunk in node_service.choose(world_id, node_id, choice_index):
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

  try:
    # Validate early to catch errors before streaming starts
    node_service.get_node_entity(world_id, node_id)
    return StreamingResponse(
      event_generator(),
      media_type="text/event-stream",
      headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # Disable buffering in nginx/proxies
      },
    )
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
  except InvalidChoiceError as e:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
