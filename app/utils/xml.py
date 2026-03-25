"""XML extraction utilities for parsing LLM outputs."""

from __future__ import annotations

import re

from pydantic import BaseModel


class LLMOutputTruncatedError(ValueError):
  """Raised when LLM output appears to have been truncated by max_tokens."""


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
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, content, re.DOTALL)
    return match.group(1).strip() if match else None
  elif streaming:
    pattern = rf"<{tag}>(.*)"
    match = re.search(pattern, content, re.DOTALL)
    return match.group(1) if match else None

  return None


def extract_xml_json[T: BaseModel](content: str, tag: str, model: type[T]) -> T:
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

  json_content = re.sub(r"^```(?:json)?\s*", "", extracted, flags=re.IGNORECASE)
  json_content = re.sub(r"\s*```$", "", json_content)

  return model.model_validate_json(json_content)
