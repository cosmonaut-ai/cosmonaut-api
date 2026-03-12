"""Buttondown newsletter API integration.

Handles subscribing and unsubscribing users from the product updates
newsletter.  Failures are logged but never raised — newsletter operations
are non-critical and should not block the calling endpoint.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.http import get_http_client
from app.core.observability import logger
from app.services.secret_manager import get_secret_value
from app.utils.pii import redact_email, truncate_for_log

_BUTTONDOWN_BASE = "https://api.buttondown.com/v1"


def _get_api_key() -> str | None:
  if not settings.BUTTONDOWN_API_KEY_PARAM:
    return None
  try:
    return get_secret_value(settings.BUTTONDOWN_API_KEY_PARAM)
  except Exception:
    logger.exception("Failed to retrieve Buttondown API key")
    return None


async def subscribe(email: str) -> bool:
  """Subscribe an email to the Buttondown newsletter. Returns True on success."""
  api_key = _get_api_key()
  if not api_key:
    logger.warning("Buttondown API key not configured; skipping subscribe for %s", redact_email(email))
    return False

  try:
    client = get_http_client()
    resp = await client.post(
      f"{_BUTTONDOWN_BASE}/subscribers",
      headers={
        "Authorization": f"Token {api_key}",
        "X-Buttondown-Collision-Behavior": "overwrite",
        "X-Buttondown-Bypass-Firewall": "true",
      },
      json={"email_address": email, "type": "regular"},
      timeout=10,
    )
    if resp.status_code in (200, 201):
      logger.info("Subscribed %s to newsletter", redact_email(email))
      return True
    logger.warning("Buttondown subscribe returned %s: %s", resp.status_code, truncate_for_log(resp.text))
    return False
  except Exception:
    logger.exception("Failed to subscribe %s to newsletter", redact_email(email))
    return False


async def unsubscribe(email: str) -> bool:
  """Unsubscribe an email from the Buttondown newsletter. Returns True on success."""
  api_key = _get_api_key()
  if not api_key:
    logger.warning("Buttondown API key not configured; skipping unsubscribe for %s", redact_email(email))
    return False

  try:
    client = get_http_client()
    resp = await client.delete(
      f"{_BUTTONDOWN_BASE}/subscribers/{email}",
      headers={"Authorization": f"Token {api_key}"},
      timeout=10,
    )
    if resp.status_code in (200, 204):
      logger.info("Unsubscribed %s from newsletter", redact_email(email))
      return True
    if resp.status_code == 404:
      logger.info("User %s not found in newsletter (already unsubscribed)", redact_email(email))
      return True
    logger.warning("Buttondown unsubscribe returned %s: %s", resp.status_code, truncate_for_log(resp.text))
    return False
  except Exception:
    logger.exception("Failed to unsubscribe %s from newsletter", redact_email(email))
    return False
