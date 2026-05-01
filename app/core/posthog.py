"""PostHog analytics client for server-side event tracking.

Provides a lazily-initialized PostHog client that is safe to import in any
environment. The client is only created when ``init_posthog()`` is called
(typically at application startup), and all public capture helpers silently
no-op when PostHog is disabled (e.g. local development, missing token).
"""

from __future__ import annotations

import logging

from posthog import Posthog

from app.core.config import settings

log = logging.getLogger(__name__)

_client: Posthog | None = None


def init_posthog() -> None:
  """Initialize the PostHog client.

  No-ops when ``ENV`` is ``local`` or when the project token is empty,
  making it safe to call unconditionally at startup.
  """
  global _client

  if settings.ENV == "local" or not settings.POSTHOG_PROJECT_TOKEN:
    log.info("PostHog disabled (env=%s, token=%s)", settings.ENV, "set" if settings.POSTHOG_PROJECT_TOKEN else "empty")
    return

  _client = Posthog(
    project_api_key=settings.POSTHOG_PROJECT_TOKEN,
    host=settings.POSTHOG_HOST,
  )
  log.info("PostHog initialized (host=%s)", settings.POSTHOG_HOST)


def get_client() -> Posthog | None:
  """Return the PostHog client, or ``None`` if not initialized."""
  return _client


def capture(event: str, distinct_id: str, properties: dict | None = None) -> None:
  """Safely capture an analytics event (no-op when PostHog is disabled)."""
  if _client is not None:
    _client.capture(event, distinct_id=distinct_id, properties=properties or {})


def capture_exception(exception: Exception, distinct_id: str | None = None) -> None:
  """Forward an exception to PostHog for error tracking (no-op when disabled)."""
  if _client is not None:
    _client.capture_exception(exception, distinct_id=distinct_id or "server")


def identify(distinct_id: str, properties: dict | None = None) -> None:
  """Set person properties for a user (no-op when disabled)."""
  if _client is not None:
    _client.set(distinct_id=distinct_id, properties=properties or {})


def flush() -> None:
  """Flush pending events to PostHog (no-op when disabled).

  Call this at the end of each request in Lambda environments where
  ``atexit`` handlers are unreliable.
  """
  if _client is not None:
    _client.flush()


def shutdown() -> None:
  """Flush remaining events and shut down the client."""
  if _client is not None:
    _client.shutdown()
