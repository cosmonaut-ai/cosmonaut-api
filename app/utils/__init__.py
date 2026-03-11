"""Shared utilities for services layer."""

import re
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


##################################################################
# X M L   E X T R A C T I O N   U T I L I T I E S
##################################################################


def extract_xml_block(content: str, tag: str, *, streaming: bool = False) -> str | None:
  """Extract content from an XML block.

  Args:
      content: The full text to search in
      tag: The XML tag name (without brackets)
      streaming: If True, returns partial content when closing tag not yet present

  Returns:
      The extracted content (stripped), or None if tag not found
  """
  if f"</{tag}>" in content:
    # Complete block available - extract everything between tags
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, content, re.DOTALL)
    return match.group(1).strip() if match else None
  elif streaming:
    # Streaming mode: return partial content if opening tag found
    pattern = rf"<{tag}>(.*)"
    match = re.search(pattern, content, re.DOTALL)
    return match.group(1) if match else None

  return None


class LLMOutputTruncatedError(ValueError):
  """Raised when LLM output appears to have been truncated by max_tokens."""


def extract_xml_json(content: str, tag: str, model: type[T]) -> T:
  """Extract JSON content from an XML block and parse it into a Pydantic model.

  Args:
      content: The full text to search in
      tag: The XML tag name (without brackets)
      model: The Pydantic model class to parse into

  Returns:
      The parsed Pydantic model instance

  Raises:
      LLMOutputTruncatedError: If the opening tag exists but the closing tag is missing
      ValueError: If the tag is not found or JSON parsing fails
  """
  extracted = extract_xml_block(content, tag, streaming=False)
  if extracted is None:
    if f"<{tag}>" in content and f"</{tag}>" not in content:
      raise LLMOutputTruncatedError(
        f"LLM output appears truncated: <{tag}> opened but never closed. "
        "This likely indicates the response hit the max_tokens limit."
      )
    raise ValueError(f"XML tag <{tag}> not found in content")

  # Clean up potential markdown code blocks
  json_content = re.sub(r"^```(?:json)?\s*", "", extracted, flags=re.IGNORECASE)
  json_content = re.sub(r"\s*```$", "", json_content)

  return model.model_validate_json(json_content)


def has_xml_block(content: str, tag: str) -> bool:
  """Check if a complete XML block exists in the content.

  Args:
      content: The text to search in
      tag: The XML tag name (without brackets)

  Returns:
      True if both opening and closing tags are present
  """
  return f"<{tag}>" in content and f"</{tag}>" in content


def number_to_base52(number: int) -> str:
  """Convert a number to a base-52 string."""
  if number < 0:
    raise ValueError("Number must be positive")
  if number == 0:
    return "a"
  result = ""
  while number > 0:
    if number % 52 > 25:
      result = chr((number) % 52 - 26 + ord("A")) + result
    else:
      result = chr((number) % 52 + ord("a")) + result
    number //= 52
  return result


def base52_to_number(base52_string: str) -> int:
  """Convert a base-52 string to a number."""
  if not base52_string:
    return 0
  number = 0
  for i, c in enumerate(reversed(base52_string)):
    if c.isdigit():
      continue
    base_value = ord(c) - ord("a") if c.islower() else ord(c) - ord("A") + 26
    if base_value < 0 or base_value > 51:
      raise ValueError("Invalid base-52 string")
    number += base_value * 52**i
  return number


##################################################################
# D A T E T I M E   U T I L I T I E S
##################################################################


def coerce_datetime(value: str | datetime | None) -> datetime:
  """Normalize datetime-like inputs into a timezone-aware UTC datetime."""
  if isinstance(value, datetime):
    dt = value
  elif value:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
  else:
    dt = datetime.now(timezone.utc)

  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return dt
