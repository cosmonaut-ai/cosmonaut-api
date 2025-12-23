"""Story nodes controller for fetching and generating story content."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, status

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
  response_model=StoryNodeDTO,
  summary="Choose an option and generate the next story node",
)
async def choose_and_generate(
  world_id: str = Path(..., description="Identifier for the world"),
  node_id: str = Path(..., description="Identifier for the current node"),
  choice_index: int = Path(..., description="Index of the choice to select (0-based)", ge=0),
) -> StoryNodeDTO:
  """Select a choice from the current node and generate the next story node.

  This endpoint:
  1. Validates the choice index is within bounds
  2. Generates a new story node based on the selected choice
  3. Updates the parent node's choice to link to the new node
  4. Returns the newly generated node
  """
  try:
    return node_service.choose_and_generate(world_id, node_id, choice_index)
  except NodeNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
  except WorldNotFoundError as e:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
  except InvalidChoiceError as e:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
