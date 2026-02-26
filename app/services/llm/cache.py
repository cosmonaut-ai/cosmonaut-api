"""Per-world Gemini context caching for reduced time-to-first-token.

Creates and manages CachedContent resources via the google-genai SDK.
Each cache stores the static system prompt and world-specific context
(world info + narrator profile) so that subsequent node generation
requests only need to send per-node dynamic context.

Caches are keyed by ``(world_id, family_friendly)`` and stored in an
in-memory registry (per Lambda instance).  Expired or invalid caches
are handled gracefully -- callers fall back to the non-cached agent path.
"""

from __future__ import annotations

from functools import lru_cache

from aws_lambda_powertools import Logger
from google.genai import Client as GenAIClient
from google.genai.types import CreateCachedContentConfig

from app.core.config import settings

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

# In-memory registry: {cache_key -> cache_name}
# Scoped to the lifetime of a single Lambda instance.
_cache_registry: dict[str, str] = {}

# Default TTL for cached content (1 hour).
_CACHE_TTL = "3600s"


@lru_cache
def _get_genai_client() -> GenAIClient:
  """Lazy singleton for the Vertex AI GenAI client used by the cache service."""
  return GenAIClient(
    vertexai=True,
    project=settings.GCP_PROJECT_ID,
    location=settings.GCP_LOCATION,
  )


def get_or_create_world_cache(
  world_id: str,
  static_system_prompt: str,
  world_context: str,
  *,
  family_friendly: bool = False,
) -> str | None:
  """Get or create a Gemini cached content resource for a world.

  The cache stores the static system prompt as ``system_instruction`` and the
  formatted world info + narrator profile as cached ``contents``.  Subsequent
  generation requests reference the cache name and only send per-node dynamic
  context (story summary, facts, previous text, choice) as the user message.

  Args:
    world_id: The world identifier.
    static_system_prompt: The full static system prompt text
      (SYSTEM_PROMPT + optional FAMILY_FRIENDLY_INSTRUCTIONS).
    world_context: Pre-formatted world info and narrator profile text.
    family_friendly: Whether family-friendly mode is active (affects cache key
      since it changes the system prompt).

  Returns:
    The cache ``name`` string suitable for ``GoogleModelSettings.google_cached_content``,
    or ``None`` if cache creation fails.
  """
  cache_key = f"{world_id}:{family_friendly}"

  if cache_key in _cache_registry:
    return _cache_registry[cache_key]

  try:
    client = _get_genai_client()
    cache = client.caches.create(
      model=settings.MODEL_SMALL,
      config=CreateCachedContentConfig(
        display_name=f"cosmonaut-world-{world_id}",
        system_instruction=static_system_prompt,
        contents=[world_context],
        ttl=_CACHE_TTL,
      ),
    )
    cache_name: str = cache.name  # type: ignore[assignment]
    _cache_registry[cache_key] = cache_name
    logger.info(
      f"Created Gemini cache for world {world_id}",
      extra={"cache_name": cache_name, "family_friendly": family_friendly},
    )
    return cache_name
  except Exception:
    logger.warning(
      f"Failed to create Gemini cache for world {world_id} -- falling back to non-cached path",
      exc_info=True,
    )
    return None


def evict_world_cache(world_id: str, *, family_friendly: bool = False) -> None:
  """Remove a stale cache entry so the next call to ``get_or_create_world_cache`` creates a fresh one."""
  cache_key = f"{world_id}:{family_friendly}"
  removed = _cache_registry.pop(cache_key, None)
  if removed:
    logger.info(f"Evicted stale Gemini cache for world {world_id}", extra={"cache_name": removed})
