"""DTOs and conversion helpers for API responses."""

from __future__ import annotations

from app.api.dto.base import DTOModel, to_dto
from app.api.dto.gemini import GeminiChatRequest, GeminiChatResponse
from app.api.dto.story_node import ChoiceDTO, StoryNodeDTO
from app.api.dto.user_progress import UserProgressDTO
from app.api.dto.vector import (
    VectorDeleteRequest,
    VectorDeleteResponse,
    VectorMatch,
    VectorQueryRequest,
    VectorQueryResponse,
    VectorUpsertItem,
    VectorUpsertRequest,
    VectorUpsertResponse,
)
from app.api.dto.world_meta import WorldMetaDTO

__all__ = [
    "DTOModel",
    "to_dto",
    "GeminiChatRequest",
    "GeminiChatResponse",
    "ChoiceDTO",
    "StoryNodeDTO",
    "UserProgressDTO",
    "VectorDeleteRequest",
    "VectorDeleteResponse",
    "VectorMatch",
    "VectorQueryRequest",
    "VectorQueryResponse",
    "VectorUpsertItem",
    "VectorUpsertRequest",
    "VectorUpsertResponse",
    "WorldMetaDTO",
]
