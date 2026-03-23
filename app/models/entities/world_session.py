"""World session entity for per-user playthroughs of a world."""

from __future__ import annotations

from pynamodb.attributes import (
  ListAttribute,
  MapAttribute,
  NumberAttribute,
  UnicodeAttribute,
)

from app.models.entities.base import BaseCosmonautModel


class WorldSession(BaseCosmonautModel):
  """Represents a single user's playthrough session for a world.

  Single-table design:
    PK = SESSION#{session_id}
    SK = META

  Shares the SESSION# partition with NodeSession items (SK = NODE#{node_id}),
  enabling efficient queries for all session data within a single partition.

  GSI3 attributes are populated now but the index is deferred to Phase 6.
  """

  id: UnicodeAttribute = UnicodeAttribute()
  root_world_id: UnicodeAttribute = UnicodeAttribute()
  members: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)
  created_by: UnicodeAttribute = UnicodeAttribute()
  per_member_progress: MapAttribute = MapAttribute(default=dict)  # type: ignore[type-arg]
  visited_node_count: NumberAttribute = NumberAttribute(default=0)

  GSI3PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI3PK", null=True)
  GSI3SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI3SK", null=True)

  # ── Key helpers ──────────────────────────────────────────────────────────

  @classmethod
  def pk(cls, session_id: str) -> str:
    return f"SESSION#{session_id}"

  @classmethod
  def sk(cls) -> str:
    return "META"

  @classmethod
  def gsi3_pk(cls, root_world_id: str) -> str:
    return f"ROOTWORLD#{root_world_id}"

  @classmethod
  def gsi3_sk(cls, session_id: str) -> str:
    return f"SESSION#{session_id}"
