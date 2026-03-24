"""Datetime normalization utilities."""

from __future__ import annotations

from datetime import UTC, datetime


def coerce_datetime(value: str | datetime | None) -> datetime:
  """Normalize datetime-like inputs into a timezone-aware UTC datetime."""
  if isinstance(value, datetime):
    dt = value
  elif value:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
  else:
    dt = datetime.now(UTC)

  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=UTC)
  return dt
