import hashlib

# Max chars to log for potentially sensitive content (API responses, LLM output, user input).
LOG_SAFE_MAX_LEN = 200


def truncate_for_log(value: str, max_len: int = LOG_SAFE_MAX_LEN) -> str:
  """Truncate a string for safe logging to avoid exposing PII, secrets, or large payloads."""
  if not value:
    return ""
  if len(value) <= max_len:
    return value
  return value[:max_len] + f"... [truncated, {len(value)} chars total]"


def redact_email(email: str) -> str:
  """Return a deterministic, non-reversible redacted email for safe logging."""
  h = hashlib.sha256(email.lower().strip().encode()).hexdigest()[:8]
  return f"[email:{h}]"
