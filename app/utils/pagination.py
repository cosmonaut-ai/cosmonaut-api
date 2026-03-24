"""Cursor-based pagination helpers for DynamoDB queries."""

from __future__ import annotations

import base64
import contextlib
import json
from typing import Any


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
  """Decode an opaque pagination cursor into a DynamoDB last_evaluated_key.

  Returns None if the cursor is absent or malformed (silently treated as
  "start from the beginning").
  """
  if not cursor:
    return None
  result = None
  with contextlib.suppress(Exception):
    result = json.loads(base64.urlsafe_b64decode(cursor))
  return result


def encode_cursor(last_evaluated_key: dict[str, Any] | None) -> str | None:
  """Encode a DynamoDB last_evaluated_key into an opaque pagination cursor.

  Returns None when there are no more pages.
  """
  if not last_evaluated_key:
    return None
  return base64.urlsafe_b64encode(json.dumps(last_evaluated_key).encode()).decode()
