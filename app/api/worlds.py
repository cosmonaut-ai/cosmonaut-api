"""Worlds controller scaffolding for CRUD and discovery operations."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel

from app.api.dto.world_meta import WorldMetaDTO

router = APIRouter(prefix="/worlds", tags=["worlds"])

_NOT_IMPLEMENTED = HTTPException(
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    detail="Worlds controller is scaffolded only; implement persistence and business logic.",
)


def _raise_not_implemented() -> NoReturn:
    """Consistently signal that the route is not yet implemented."""

    raise _NOT_IMPLEMENTED


class WorldCreateRequest(BaseModel):
    """Payload for creating a new world."""

    title: str
    author_id: str
    root_node_id: str
    description: str | None = None
    genre: str | None = None
    visibility: str = "private"
    world_prompt: str | None = None
    world_info: str | None = None
    narrator_profile: str | None = None
    node_text_length: int | None = None
    world_image_url: str | None = None
    world_image_alt_text: str | None = None
    world_image_width: str | None = None
    world_image_height: str | None = None
    world_image_size: str | None = None


class WorldUpdateRequest(BaseModel):
    """Partial payload for updating an existing world."""

    title: str | None = None
    description: str | None = None
    genre: str | None = None
    visibility: str | None = None
    world_prompt: str | None = None
    world_info: str | None = None
    narrator_profile: str | None = None
    node_text_length: int | None = None
    world_image_url: str | None = None
    world_image_alt_text: str | None = None
    world_image_width: str | None = None
    world_image_height: str | None = None
    world_image_size: str | None = None


@router.get("", response_model=list[WorldMetaDTO], summary="List available worlds")
async def list_worlds() -> list[WorldMetaDTO]:
    """Return a discoverable set of worlds (paged feed TBD)."""

    _raise_not_implemented()


@router.get(
    "/{world_id}",
    response_model=WorldMetaDTO,
    summary="Fetch a single world by identifier",
)
async def get_world(
    world_id: str = Path(..., description="Identifier for the world"),
) -> WorldMetaDTO:
    """Retrieve a single world by its identifier."""

    _raise_not_implemented()


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WorldMetaDTO,
    summary="Create a new world",
)
async def create_world(payload: WorldCreateRequest) -> WorldMetaDTO:
    """Create a world scaffold; actual persistence wiring is pending."""

    _raise_not_implemented()


@router.patch(
    "/{world_id}",
    response_model=WorldMetaDTO,
    summary="Update fields on an existing world",
)
async def update_world(
    payload: WorldUpdateRequest,
    world_id: str = Path(..., description="Identifier for the world"),
) -> WorldMetaDTO:
    """Apply partial updates to an existing world."""

    _raise_not_implemented()


@router.delete(
    "/{world_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a world",
)
async def delete_world(world_id: str = Path(..., description="Identifier for the world")) -> None:
    """Delete a world and any associated state (implementation pending)."""

    _raise_not_implemented()
