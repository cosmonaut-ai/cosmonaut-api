"""Gemini provider and model singletons.

Uses lru_cache to provide lazy initialization without global mutable state.
"""

from functools import lru_cache

from pydantic_ai import ModelSettings
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from app.core.config import settings
from app.services.secret_manager import get_secret_value


@lru_cache
def get_gemini_provider() -> GoogleProvider:
  """Get or create the Gemini provider singleton."""
  return GoogleProvider(api_key=get_secret_value(settings.GEMINI_API_KEY_PARAM))


@lru_cache
def get_gemini_model(model_name: str = settings.GEMINI_MODEL_SMALL) -> GoogleModel:
  """Get or create a Gemini model with the specified model name."""
  return GoogleModel(
    model_name=model_name,
    provider=get_gemini_provider(),
    settings=ModelSettings(temperature=0.95),
  )
