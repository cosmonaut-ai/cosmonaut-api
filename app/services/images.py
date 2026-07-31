"""Image generation service for world cover images.

Orchestrates prompt crafting via Gemini, image generation via Imagen 3,
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

IMAGEN_MODEL = "imagen-4.0-generate-001"
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
  """Record the Imagen call as a PostHog $ai_generation event.

  Imagen is priced per image, not per token, so cost is computed here
  (IMAGEN_USD_PER_IMAGE). generate_images is outside pydantic-ai, so no
  span is created automatically.
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
  2. Generate an image with Imagen 3.
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

  # 2. Generate image via Imagen 3
  client = _get_genai_client()
  started_at = time.monotonic()
  try:
    response = client.models.generate_images(
      model=IMAGEN_MODEL,
      prompt=image_prompt,
      config=types.GenerateImagesConfig(
        number_of_images=1,
        aspect_ratio="1:1",
      ),
    )
  except Exception as exc:
    _capture_image_generation(world.id, time.monotonic() - started_at, error=str(exc))
    raise
  _capture_image_generation(world.id, time.monotonic() - started_at)

  if not response.generated_images:
    raise RuntimeError(f"Imagen returned no images for world {world.id}")

  generated_image = response.generated_images[0]
  if generated_image.image is None or generated_image.image.image_bytes is None:
    raise RuntimeError(f"Imagen returned an image with no data for world {world.id}")

  image_bytes: bytes = generated_image.image.image_bytes

  # 3. Upload to S3
  s3_key = S3_KEY_TEMPLATE.format(world_id=world.id)
  cdn_url = upload_image(key=s3_key, image_bytes=image_bytes, content_type="image/png")

  # 4. Persist image metadata on the world entity
  world.world_image_url = cdn_url
  world.world_image_alt_text = image_prompt
  world.world_image_width = "1024"
  world.world_image_height = "1024"
  world.world_image_size = str(len(image_bytes))
  world.save()

  logger.info(f"Image saved for world {world.id}: {cdn_url}")
  return world
