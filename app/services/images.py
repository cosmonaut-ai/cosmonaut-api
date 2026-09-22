"""Image generation service for world cover images.

Orchestrates prompt crafting via Gemini, image generation via Gemini,
S3 upload, and WorldMeta persistence.
"""

from __future__ import annotations

import time
from functools import lru_cache

from google.genai import Client as GenAIClient
from google.genai import types

import app.services.llm as llm
from app.core.config import settings
from app.core.gcp_auth import get_gcp_credentials
from app.core.llm_telemetry import current_ai_trace_id, get_llm_context
from app.core.observability import logger, tracer
from app.core.posthog import capture as ph_capture
from app.models.entities.world_meta import WorldMeta
from app.services.s3 import upload_image
from app.services.worlds import world_meta_to_llm_world_info
from app.utils.pii import truncate_for_log

IMAGEN_MODEL = "gemini-3.1-flash-lite-image"
IMAGE_SIZE = "1024x1024"
S3_KEY_TEMPLATE = "worlds/{world_id}/cover.png"


@lru_cache
def _get_genai_client() -> GenAIClient:
  """Lazy singleton for the Vertex AI GenAI client."""
  return GenAIClient(
    vertexai=True,
    credentials=get_gcp_credentials(),
    project=settings.GCP_PROJECT_ID,
    location=settings.GCP_LOCATION,
  )


def _capture_image_generation(world_id: str, latency_s: float, error: str | None = None) -> None:
  """Record the Gemini image call as a PostHog $ai_generation event.

  Gemini is token-billed; IMAGEN_USD_PER_IMAGE is a configurable estimate
  for a Standard-tier 1K image, not an exact bill. This direct SDK call
  is outside pydantic-ai, so no span is created automatically.
  """
  context = get_llm_context()
  distinct_id = context.get("distinct_id") or context.get("user_id")
  if not distinct_id:
    return
  properties: dict[str, object] = {
    "$ai_provider": "google",
    "$ai_model": IMAGEN_MODEL,
    "$ai_span_name": "image_generation",
    "$ai_latency": latency_s,
    "$ai_trace_id": current_ai_trace_id(),
    "$ai_total_cost_usd": settings.IMAGEN_USD_PER_IMAGE,
    "world_id": world_id,
  }
  if error is not None:
    properties["$ai_is_error"] = True
    properties["$ai_error"] = error
  ph_capture("$ai_generation", distinct_id=str(distinct_id), properties=properties)


@tracer.capture_method
async def generate_world_image(world: WorldMeta) -> WorldMeta:
  """Generate a cover image for a world and persist it.

  1. Craft an optimised image prompt via the Gemini LLM agent.
  2. Generate an image with Gemini.
  3. Upload the image to S3.
  4. Update the WorldMeta entity with image metadata.

  Args:
    world: The world entity to generate an image for.  Must already have
      lore fields populated (title, genre, setting, etc.).

  Returns:
    The updated WorldMeta entity with image fields set.
  """
  # 1. Build an LLM-friendly representation and craft the image prompt
  world_info = world_meta_to_llm_world_info(world)
  image_prompt = await llm.generate_image_prompt(world_info)
  logger.info(
    "Crafted image prompt for world %s", world.id, extra={"image_prompt_preview": truncate_for_log(image_prompt)}
  )

  # 2. Generate image via Gemini
  client = _get_genai_client()
  started_at = time.monotonic()
  try:
    response = client.models.generate_content(
      model=IMAGEN_MODEL,
      contents=[image_prompt],
      config=types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio="1:1", output_mime_type="image/png"),
      ),
    )
    image_data = next(
      (
        part.inline_data
        for part in response.parts or []
        if not part.thought
        and part.inline_data is not None
        and part.inline_data.mime_type == "image/png"
        and part.inline_data.data
      ),
      None,
    )
    if image_data is None or not image_data.data:
      logger.warning(
        "Gemini returned no PNG image data",
        extra={
          "world_id": world.id,
          "model": IMAGEN_MODEL,
          "inline_mime_types": [part.inline_data.mime_type for part in response.parts or [] if part.inline_data],
          "finish_reasons": [str(candidate.finish_reason) for candidate in response.candidates or []],
          "block_reason": str(response.prompt_feedback.block_reason) if response.prompt_feedback else None,
        },
      )
      raise RuntimeError(f"Gemini returned no PNG image data for world {world.id}")
    image_bytes: bytes = image_data.data
  except Exception as exc:
    _capture_image_generation(world.id, time.monotonic() - started_at, error=str(exc))
    raise
  _capture_image_generation(world.id, time.monotonic() - started_at)

  # 3. Upload to S3
  s3_key = S3_KEY_TEMPLATE.format(world_id=world.id)
  cdn_url = upload_image(key=s3_key, image_bytes=image_bytes)

  # 4. Persist image metadata on the world entity
  world.world_image_url = cdn_url
  world.world_image_alt_text = image_prompt
  world.world_image_width = "1024"
  world.world_image_height = "1024"
  world.world_image_size = str(len(image_bytes))
  world.save()

  logger.info(f"Image saved for world {world.id}: {cdn_url}")
  return world
