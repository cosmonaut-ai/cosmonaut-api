"""LLM utilities for prompt formatting."""

import json

from pydantic import BaseModel


def format_model_with_descriptions(model: BaseModel) -> str:
  """Format a Pydantic model with field descriptions inline as comments."""
  schema = model.model_json_schema()
  data = model.model_dump()
  properties = schema.get("properties", {})

  lines = ["{"]
  items = list(data.items())
  for i, (key, value) in enumerate(items):
    desc = properties.get(key, {}).get("description", "")

    # Format the value as JSON
    value_json = json.dumps(value, ensure_ascii=False)

    comma = "," if i < len(items) - 1 else ""

    # Add the key-value pair
    lines.append(f'  "{key}": {value_json}{comma}')

    # Add description as a comment on the next line if it exists
    if desc:
      lines.append(f"  // {desc}")

  lines.append("}")
  return "\n".join(lines)
