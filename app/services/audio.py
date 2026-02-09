"""Audio narration service using ElevenLabs Text-to-Speech.

Generates MP3 audio from story node text via the ElevenLabs API (Flash 2.5
model), uploads the result to S3, and returns the public CDN URL.
"""

from __future__ import annotations

from functools import lru_cache

import httpx
from aws_lambda_powertools import Logger

from app.core.config import settings
from app.services.s3 import upload_file
from app.services.secret_manager import get_secret_value

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_MODEL = "eleven_flash_v2_5"
DEFAULT_VOICE_ID = "jfIS2w2yJi0grJZPyEsk"
S3_AUDIO_KEY_TEMPLATE = "audio/{world_id}/{node_id}.mp3"

# ElevenLabs API timeout — Flash 2.5 typically responds in 1-3s.
_TIMEOUT_S = 15


@lru_cache
def _get_elevenlabs_api_key() -> str:
  """Fetch the ElevenLabs API key from SSM Parameter Store (cached)."""
  return get_secret_value(settings.ELEVENLABS_API_KEY_PARAM)


def generate_audio(text: str, voice_id: str = DEFAULT_VOICE_ID) -> bytes:
  """Call ElevenLabs TTS and return raw MP3 bytes.

  Args:
    text: The story text to synthesise.
    voice_id: ElevenLabs voice identifier. Defaults to :data:`DEFAULT_VOICE_ID`.

  Returns:
    MP3 audio bytes.

  Raises:
    httpx.HTTPStatusError: If the ElevenLabs API returns a non-2xx response.
  """
  api_key = _get_elevenlabs_api_key()
  url = f"{ELEVENLABS_TTS_URL}/{voice_id}"

  with httpx.Client(timeout=_TIMEOUT_S) as client:
    response = client.post(
      url,
      headers={
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
      },
      json={
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
          "stability": 0.5,
          "similarity_boost": 0.75,
        },
      },
    )
    response.raise_for_status()

  logger.info(f"Generated audio ({len(response.content)} bytes) with voice {voice_id}")
  return response.content


def upload_audio(world_id: str, node_id: str, audio_bytes: bytes) -> str:
  """Upload MP3 audio bytes to S3 and return the CDN URL.

  Args:
    world_id: Identifier for the world.
    node_id: Identifier for the story node.
    audio_bytes: Raw MP3 data.

  Returns:
    The public CDN URL for the uploaded audio file.
  """
  key = S3_AUDIO_KEY_TEMPLATE.format(world_id=world_id, node_id=node_id)
  return upload_file(key=key, data=audio_bytes, content_type="audio/mpeg")


def generate_and_store_audio(
  world_id: str,
  node_id: str,
  text: str,
  voice_id: str = DEFAULT_VOICE_ID,
) -> str:
  """Generate TTS audio and persist it to S3.

  Orchestrates :func:`generate_audio` and :func:`upload_audio`.

  Args:
    world_id: Identifier for the world.
    node_id: Identifier for the story node.
    text: The story text to synthesise.
    voice_id: ElevenLabs voice identifier.

  Returns:
    The public CDN URL of the stored audio file.
  """
  audio_bytes = generate_audio(text=text, voice_id=voice_id)
  cdn_url = upload_audio(world_id=world_id, node_id=node_id, audio_bytes=audio_bytes)
  logger.info(f"Audio stored for world={world_id} node={node_id}: {cdn_url}")
  return cdn_url
