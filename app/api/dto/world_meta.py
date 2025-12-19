"""DTOs for world metadata responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.api.dto.base import DTOModel


class WorldMetaDTO(DTOModel):
    """World metadata response DTO."""

    pk: str
    sk: str
    title: str
    description: str | None = None
    genre: str | None = Field(default=None, alias="GSI1PK")
    score: str | None = Field(default=None, alias="GSI1SK")
    author_id: str
    root_node_id: str
    visibility: str
    world_prompt: str | None = None
    world_info: str | None = None
    narrator_profile: str | None = None
    node_text_length: int | None = None
    world_image_url: str | None = None
    world_image_alt_text: str | None = None
    world_image_width: str | None = None
    world_image_height: str | None = None
    world_image_size: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
