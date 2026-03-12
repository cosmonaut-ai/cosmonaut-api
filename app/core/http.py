"""Shared httpx.AsyncClient with lazy initialization for Lambda container reuse.

The client is created on first access and reused across requests within the
same Lambda container.  This avoids the overhead of creating a new TCP
connection pool per request while remaining safe for Lambda's execution model
(no lifespan hook needed).
"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
  """Return the shared async HTTP client, creating it on first call."""
  global _client
  if _client is None:
    _client = httpx.AsyncClient(timeout=15.0)
  return _client
