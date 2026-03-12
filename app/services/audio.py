"""Audio narration service using ElevenLabs Text-to-Speech.

Generates MP3 audio from story node text via the ElevenLabs API (Flash 2.5
model), uploads the result to S3, and returns the public CDN URL.
"""

from __future__ import annotations

from functools import lru_cache

from httpx import HTTPStatusError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.http import get_http_client
from app.core.observability import MetricUnit, logger, metrics, tracer
from app.services.s3 import upload_file
from app.services.secret_manager import get_secret_value

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_MODEL = "eleven_flash_v2_5"
S3_AUDIO_KEY_TEMPLATE = "audio/{world_id}/{node_id}/{voice_id}.mp3"

_TIMEOUT_S = 15


@lru_cache
def _get_elevenlabs_api_key() -> str:
  """Fetch the ElevenLabs API key from SSM Parameter Store (cached)."""
  return get_secret_value(settings.ELEVENLABS_API_KEY_PARAM)


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
  if isinstance(exc, HTTPStatusError):
    return exc.response.status_code in _RETRYABLE_STATUS_CODES
  return isinstance(exc, ConnectionError | TimeoutError)


@tracer.capture_method
@retry(
  retry=retry_if_exception(_is_retryable),
  stop=stop_after_attempt(3),
  wait=wait_exponential(multiplier=2, max=15),
  reraise=True,
)
async def generate_audio(text: str, elevenlabs_voiceid: str) -> bytes:
  """Call ElevenLabs TTS and return raw MP3 bytes.

  Args:
    text: The story text to synthesise.
    elevenlabs_voiceid: ElevenLabs voice identifier.

  Returns:
    MP3 audio bytes.

  Raises:
    httpx.HTTPStatusError: If the ElevenLabs API returns a non-2xx response.
  """
  api_key = _get_elevenlabs_api_key()
  url = f"{ELEVENLABS_TTS_URL}/{elevenlabs_voiceid}"

  client = get_http_client()
  response = await client.post(
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
    timeout=_TIMEOUT_S,
  )
  response.raise_for_status()

  logger.info(f"Generated audio ({len(response.content)} bytes) with voice {elevenlabs_voiceid}")
  return response.content


def upload_audio(world_id: str, node_id: str, voice_id: str, audio_bytes: bytes) -> str:
  """Upload MP3 audio bytes to S3 and return the CDN URL.

  Args:
    world_id: Identifier for the world.
    node_id: Identifier for the story node.
    voice_id: Internal voice identifier (used in the S3 key).
    audio_bytes: Raw MP3 data.

  Returns:
    The public CDN URL for the uploaded audio file.
  """
  key = S3_AUDIO_KEY_TEMPLATE.format(world_id=world_id, node_id=node_id, voice_id=voice_id)
  return upload_file(key=key, data=audio_bytes, content_type="audio/mpeg")


@tracer.capture_method
async def generate_and_store_audio(
  world_id: str,
  node_id: str,
  text: str,
  voice_id: str,
  elevenlabs_voiceid: str,
) -> str:
  """Generate TTS audio and persist it to S3.

  Orchestrates :func:`generate_audio` and :func:`upload_audio`.

  Args:
    world_id: Identifier for the world.
    node_id: Identifier for the story node.
    text: The story text to synthesise.
    voice_id: Internal voice identifier (used in the S3 key).
    elevenlabs_voiceid: ElevenLabs voice identifier for TTS.

  Returns:
    The public CDN URL of the stored audio file.
  """
  audio_bytes = await generate_audio(text=text, elevenlabs_voiceid=elevenlabs_voiceid)
  cdn_url = upload_audio(world_id=world_id, node_id=node_id, voice_id=voice_id, audio_bytes=audio_bytes)
  metrics.add_metric(name="AudioNarrationGenerated", unit=MetricUnit.Count, value=1)
  logger.info(f"Audio stored for world={world_id} node={node_id} voice={voice_id}: {cdn_url}")
  return cdn_url
