"""Base-52 encoding/decoding for deterministic story node IDs."""

from __future__ import annotations


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
