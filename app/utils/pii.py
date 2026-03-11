import hashlib


def redact_email(email: str) -> str:
  """Return a deterministic, non-reversible redacted email for safe logging."""
  h = hashlib.sha256(email.lower().strip().encode()).hexdigest()[:8]
  return f"[email:{h}]"
