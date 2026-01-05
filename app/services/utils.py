"""Shared utilities for services layer."""

import re
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


def extract_xml_json(content: str, tag: str, model: type[T]) -> T:
  """Extract JSON content from an XML block and parse it into a Pydantic model.

  Args:
      content: The full text to search in
      tag: The XML tag name (without brackets)
      model: The Pydantic model class to parse into

  Returns:
      The parsed Pydantic model instance

  Raises:
      ValueError: If the tag is not found or JSON parsing fails
  """
  extracted = extract_xml_block(content, tag, streaming=False)
  if extracted is None:
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

