"""User progress entity for tracking last-visited story node per world."""

from __future__ import annotations

from pynamodb.attributes import UnicodeAttribute

from app.models.entities.base import BaseCosmonautModel


class UserProgress(BaseCosmonautModel):
  """Tracks the last story node a user visited in each world.

  Single-table design:
    PK = USER#{user_id}
    SK = PROGRESS#{world_id}
  """

  user_id: UnicodeAttribute = UnicodeAttribute()
  world_id: UnicodeAttribute = UnicodeAttribute()
  current_node_id: UnicodeAttribute = UnicodeAttribute()

  # ── Key helpers ──────────────────────────────────────────────────────────

  @classmethod
  def pk(cls, user_id: str) -> str:
    return f"USER#{user_id}"

  @classmethod
  def sk(cls, world_id: str) -> str:
    return f"PROGRESS#{world_id}"
