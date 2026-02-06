"""Image generation service for world cover images.

Orchestrates prompt crafting via Gemini, image generation via Imagen 3,
S3 upload, and WorldMeta persistence.
"""

from __future__ import annotations

from functools import lru_cache

from aws_lambda_powertools import Logger
from google import genai
from google.genai import types

import app.services.llm as llm
from app.core.config import settings
from app.models.entities.world_meta import WorldMeta
from app.services.s3 import upload_image
from app.services.secret_manager import get_secret_value
from app.services.worlds import world_meta_to_llm_world_info

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

IMAGEN_MODEL = "imagen-4.0-generate-001"
IMAGE_SIZE = "1024x1024"
S3_KEY_TEMPLATE = "worlds/{world_id}/cover.png"


@lru_cache
def _get_genai_client() -> genai.Client:
  """Lazy singleton for the Google GenAI client."""
  return genai.Client(api_key=get_secret_value(settings.GEMINI_API_KEY_PARAM))


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
  logger.info(f"Crafted image prompt for world {world.id}", extra={"image_prompt": image_prompt})

  # 2. Generate image via Imagen 3
  client = _get_genai_client()
  response = client.models.generate_images(
    model=IMAGEN_MODEL,
    prompt=image_prompt,
    config=types.GenerateImagesConfig(
      number_of_images=1,
      aspect_ratio="1:1",
    ),
  )

  if not response.generated_images:
    raise RuntimeError(f"Imagen returned no images for world {world.id}")

  image_bytes: bytes = response.generated_images[0].image.image_bytes

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
