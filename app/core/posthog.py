"""PostHog analytics client for server-side event tracking."""

from __future__ import annotations

import atexit

from posthog import Posthog

from app.core.config import settings
from app.services.secret_manager import get_secret_value

posthog_client = Posthog(
  project_api_key=get_secret_value(settings.POSTHOG_PROJECT_TOKEN_PARAM),
  host=settings.POSTHOG_HOST,
  enable_exception_autocapture=True,
)

atexit.register(posthog_client.shutdown)
