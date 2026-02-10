"""Voices controller – lists available TTS voices."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.models.voices import get_all_voices

router = APIRouter(prefix="/voices", tags=["voices"])


class VoiceDTO(BaseModel):
  """Public representation of a voice (no internal ElevenLabs IDs)."""

  id: str
  display_name: str
  description: str
  sample_url: str


@router.get(
  "/",
  response_model=list[VoiceDTO],
  summary="List available TTS voices",
)
async def list_voices() -> list[VoiceDTO]:
  """Return the pre-defined set of voices available for audio narration."""
  return [
    VoiceDTO(id=v.id, display_name=v.display_name, description=v.description, sample_url=v.sample_url)
    for v in get_all_voices()
  ]
