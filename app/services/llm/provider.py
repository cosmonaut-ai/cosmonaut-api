"""Vertex AI provider and model singletons.

Uses lru_cache to provide lazy initialization without global mutable state.
Includes automatic retry with exponential backoff for transient API errors
(429 rate-limit, 500 internal, 502, 503 overloaded, 504 timeout).

Purpose-based factory functions centralize model selection so agent code
never needs to know which provider or caching strategy is in use.
"""

from functools import lru_cache
from typing import Literal

from anthropic import AsyncAnthropicVertex
from httpx import AsyncClient, HTTPStatusError, Response, Timeout
from pydantic_ai import ModelSettings
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.gcp_auth import get_gcp_credentials

# HTTP status codes that should trigger a retry.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Anthropic requires a minimum token count for prompt caching to activate.
# Below this threshold the request is processed normally (no error, no cache).
ANTHROPIC_CACHE_MIN_TOKENS: dict[str, int] = {
  "claude-haiku-4-5": 4096,
  "claude-haiku-4-5@20251001": 4096,
  "claude-3-5-haiku": 4096,
}
ANTHROPIC_CACHE_MIN_TOKENS_DEFAULT = 4096


def _create_retrying_client() -> AsyncClient:
  """Build an httpx AsyncClient whose transport retries transient API errors.

  - 429 (rate-limit): respects Retry-After header when present.
  - 500 / 502 / 503 / 504: transient server errors retried with exponential backoff.
  - Up to 5 total attempts, capped at 5 min max wait between retries.
  """

  def _should_retry(response: Response) -> None:
    """Raise for retryable status codes so tenacity can intercept them."""
    if response.status_code in _RETRYABLE_STATUS_CODES:
      response.raise_for_status()

  transport = AsyncTenacityTransport(
    config=RetryConfig(
      retry=retry_if_exception_type((HTTPStatusError, ConnectionError)),
      wait=wait_retry_after(
        fallback_strategy=wait_exponential(multiplier=1, max=60),
        max_wait=300,
      ),
      stop=stop_after_attempt(5),
      reraise=True,
    ),
    validate_response=_should_retry,
  )
  return AsyncClient(transport=transport, timeout=Timeout(settings.VERTEX_TIMEOUT_S))


# =============================================================================
# Google / Gemini
# =============================================================================


@lru_cache
def get_vertex_google_provider() -> GoogleProvider:
  """Get or create the Vertex AI Google provider singleton.

  The provider is backed by a retrying HTTP client that automatically handles
  429 / 500 / 502 / 503 / 504 responses with exponential backoff.
  Uses Vertex AI via Workload Identity Federation (Lambda) or ADC (local).
  """
  return GoogleProvider(  # type: ignore[reportCallIssue]
    vertexai=True,
    credentials=get_gcp_credentials(),
    project=settings.GCP_PROJECT_ID,
    location=settings.GCP_LOCATION,
    http_client=_create_retrying_client(),
  )


@lru_cache
def get_vertex_google_model(model_name: str = settings.MODEL_SMALL) -> GoogleModel:
  """Get or create a Vertex AI Google model with the specified model name."""
  return GoogleModel(
    model_name=model_name,
    provider=get_vertex_google_provider(),
    settings=ModelSettings(temperature=0.95),
  )


# =============================================================================
# Anthropic (via Vertex AI)
# =============================================================================


@lru_cache
def get_vertex_anthropic_provider() -> AnthropicProvider:
  """Get or create the Vertex AI Anthropic provider singleton."""
  vertex_client = AsyncAnthropicVertex(
    region=settings.GCP_LOCATION,
    project_id=settings.GCP_PROJECT_ID,
    credentials=get_gcp_credentials(),
    http_client=_create_retrying_client(),
  )
  return AnthropicProvider(anthropic_client=vertex_client)


@lru_cache
def get_vertex_anthropic_model(
  model_name: str = settings.MODEL_SMALL_ANTHROPIC,
  cache_instructions: Literal["5m", "1h"] | None = "1h",
) -> AnthropicModel:
  """Get or create a Vertex AI Anthropic model.

  Prompt caching is configured at the model level so agents and callers are
  completely unaware of caching mechanics.  Anthropic caches the system prompt
  prefix automatically when ``cache_control`` breakpoints are present.

  If the system prompt is below the model's minimum token threshold (e.g. 4096
  for Haiku 4.5), Anthropic silently processes the request without caching --
  no error occurs, the request simply costs standard input token rates.

  Args:
    model_name: The Anthropic model name on Vertex AI.
    cache_instructions: TTL for system prompt caching. ``'1h'`` gives 90%
      discount on cache reads at 2x write cost. ``None`` disables caching.
  """
  model_settings: AnthropicModelSettings = AnthropicModelSettings(temperature=0.95)
  if cache_instructions is not None:
    model_settings["anthropic_cache_instructions"] = cache_instructions
  return AnthropicModel(
    model_name,
    provider=get_vertex_anthropic_provider(),
    settings=model_settings,
  )


# =============================================================================
# Purpose-based factory functions
# =============================================================================


def get_storytelling_model() -> AnthropicModel:
  """Model for story narration (root_node, next_node).

  Uses Claude Haiku via Vertex AI with 1-hour prompt caching enabled.
  The system prompt (static instructions + world context + narrator profile)
  is cached automatically by Anthropic -- agents are cache-unaware.
  """
  return get_vertex_anthropic_model()


def get_utility_model() -> GoogleModel:
  """Model for utility tasks (fact extraction, narrator profile, image prompt)."""
  return get_vertex_google_model()


def get_world_building_model() -> GoogleModel:
  """Model for world building (world_info generation)."""
  return get_vertex_google_model(settings.MODEL_LARGE)
