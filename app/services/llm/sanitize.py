"""Input and output sanitization for LLM prompts.

Provides defense-in-depth against prompt injection by stripping known
injection patterns and control sequences from user-provided text before
it enters the LLM pipeline.

This is NOT a complete defense -- it's one layer in a multi-layer strategy.
Delimiter isolation and system prompt reinforcement are the primary controls.
"""

import re

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
  re.compile(
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|context)", re.IGNORECASE
  ),
  re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.IGNORECASE),
  re.compile(r"(system|assistant)\s*:", re.IGNORECASE),
  re.compile(r"<\s*/?\s*(system|instruction|prompt|rule|override|admin|command)\s*>", re.IGNORECASE),
  re.compile(r"(new\s+)?(system\s+)?prompt\s*:", re.IGNORECASE),
  re.compile(r"\[\s*INST\s*\]", re.IGNORECASE),
  re.compile(r"```\s*(system|instruction)", re.IGNORECASE),
  re.compile(
    r"(forget|disregard|override|bypass)\s+(everything|all|your|the)\s+(above|previous|instructions?|rules?|guidelines?)",
    re.IGNORECASE,
  ),
]

_XML_TAG_PATTERN = re.compile(r"<\s*/?\s*[a-zA-Z_][\w.-]*\s*(?:[^>]*)?>")


def sanitize_user_input(text: str) -> str:
  """Sanitize user input for safe inclusion in LLM prompts.

  Strips known injection patterns and XML-like tags that could interfere
  with the XML-based output format used by story agents.
  """
  result = text
  for pattern in _INJECTION_PATTERNS:
    result = pattern.sub("", result)
  result = _XML_TAG_PATTERN.sub("", result)
  result = re.sub(r"\s{3,}", "  ", result)
  return result.strip()


def sanitize_llm_output(text: str) -> str:
  """Strip potential injection directives from LLM output before it enters
  downstream prompts. Lighter-touch than input sanitization since LLM
  outputs rarely contain legitimate instruction-like patterns.
  """
  result = text
  for pattern in _INJECTION_PATTERNS:
    result = pattern.sub("", result)
  return result.strip()
