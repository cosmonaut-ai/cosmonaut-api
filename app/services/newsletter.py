"""Buttondown newsletter API integration.

Handles subscribing and unsubscribing users from the product updates
newsletter.  Failures are logged but never raised — newsletter operations
are non-critical and should not block the calling endpoint.
"""

from __future__ import annotations

import httpx
from aws_lambda_powertools import Logger

from app.core.config import settings
from app.services.secret_manager import get_secret_value

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

_BUTTONDOWN_BASE = "https://api.buttondown.com/v1"


def _get_api_key() -> str | None:
  if not settings.BUTTONDOWN_API_KEY_PARAM:
    return None
  try:
    return get_secret_value(settings.BUTTONDOWN_API_KEY_PARAM)
  except Exception:
    logger.exception("Failed to retrieve Buttondown API key")
    return None


def subscribe(email: str) -> bool:
  """Subscribe an email to the Buttondown newsletter. Returns True on success."""
  api_key = _get_api_key()
  if not api_key:
    logger.warning("Buttondown API key not configured; skipping subscribe for %s", email)
    return False

  try:
    resp = httpx.post(
      f"{_BUTTONDOWN_BASE}/subscribers",
      headers={"Authorization": f"Token {api_key}"},
      json={"email": email, "type": "regular"},
      timeout=10,
    )
    if resp.status_code in (200, 201):
      logger.info("Subscribed %s to newsletter", email)
      return True
    if resp.status_code == 409:
      logger.info("User %s already subscribed", email)
      return True
    logger.warning("Buttondown subscribe returned %s: %s", resp.status_code, resp.text)
    return False
  except Exception:
    logger.exception("Failed to subscribe %s to newsletter", email)
    return False


def unsubscribe(email: str) -> bool:
  """Unsubscribe an email from the Buttondown newsletter. Returns True on success."""
  api_key = _get_api_key()
  if not api_key:
    logger.warning("Buttondown API key not configured; skipping unsubscribe for %s", email)
    return False

  try:
    resp = httpx.delete(
      f"{_BUTTONDOWN_BASE}/subscribers/{email}",
      headers={"Authorization": f"Token {api_key}"},
      timeout=10,
    )
    if resp.status_code in (200, 204):
      logger.info("Unsubscribed %s from newsletter", email)
      return True
    if resp.status_code == 404:
      logger.info("User %s not found in newsletter (already unsubscribed)", email)
      return True
    logger.warning("Buttondown unsubscribe returned %s: %s", resp.status_code, resp.text)
    return False
  except Exception:
    logger.exception("Failed to unsubscribe %s from newsletter", email)
    return False
