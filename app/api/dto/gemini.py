"""DTOs for Gemini chat interactions."""

from __future__ import annotations

from pydantic import Field

from app.api.dto.base import DTOModel


class GeminiChatRequest(DTOModel):
    """Request payload for a single-turn Gemini chat/completion."""

    prompt: str = Field(..., description="User prompt sent to Gemini.")
    system: str | None = Field(default=None, description="Optional system instruction.")
    context: list[str] | None = Field(
        default=None, description="Optional prior messages to provide additional context."
    )
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=512, ge=1, description="Max tokens to generate.")


class GeminiChatResponse(DTOModel):
    """Normalized Gemini response."""

    text: str = Field(default="", description="Primary text response.")
    finish_reason: str | None = Field(default=None, description="Model finish reason.")
    input_tokens: int | None = Field(default=None, description="Token count for the prompt.")
    output_tokens: int | None = Field(default=None, description="Token count for the response.")
    total_tokens: int | None = Field(default=None, description="Total token count for the call.")

