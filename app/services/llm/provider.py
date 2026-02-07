"""Gemini provider and model singletons.

Uses lru_cache to provide lazy initialization without global mutable state.
Includes automatic retry with exponential backoff for transient API errors
(429 rate-limit, 502, 503 overloaded, 504 timeout).
"""

from functools import lru_cache

from httpx import AsyncClient, HTTPStatusError
from pydantic_ai import ModelSettings
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.services.secret_manager import get_secret_value

# HTTP status codes that should trigger a retry.
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def _create_retrying_client() -> AsyncClient:
  """Build an httpx AsyncClient whose transport retries transient Gemini errors.

  - 429 (rate-limit): respects Retry-After header when present.
  - 502 / 503 / 504: transient server errors retried with exponential backoff.
  - Up to 5 total attempts, capped at 5 min max wait between retries.
  """

  def _should_retry(response):  # noqa: ANN001
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
  return AsyncClient(transport=transport)


@lru_cache
def get_gemini_provider() -> GoogleProvider:
  """Get or create the Gemini provider singleton.

  The provider is backed by a retrying HTTP client that automatically handles
  429 / 502 / 503 / 504 responses with exponential backoff.
  """
  return GoogleProvider(
    api_key=get_secret_value(settings.GEMINI_API_KEY_PARAM),
    http_client=_create_retrying_client(),
  )


@lru_cache
def get_gemini_model(model_name: str = settings.GEMINI_MODEL_SMALL) -> GoogleModel:
  """Get or create a Gemini model with the specified model name."""
  return GoogleModel(
    model_name=model_name,
    provider=get_gemini_provider(),
    settings=ModelSettings(temperature=0.95),
  )
