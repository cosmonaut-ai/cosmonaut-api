"""Session membership entity linking users to their world sessions."""

from __future__ import annotations

from pynamodb.attributes import NumberAttribute, UnicodeAttribute, UTCDateTimeAttribute

from app.models.entities.base import BaseCosmonautModel


class SessionMembership(BaseCosmonautModel):
  """Associates a user with a world session, carrying denormalized world metadata.

  Single-table design:
    PK = USER#{user_id}
    SK = SMEMBER#{root_world_id}#{session_id}

  The composite SK enables two query patterns without a GSI:
    1. Find user's session for a specific world:
       query PK=USER#{user_id}, SK begins_with SMEMBER#{root_world_id}#
    2. List all sessions for a user (dashboard):
       query PK=USER#{user_id}, SK begins_with SMEMBER#

  Coexists in the USER# partition alongside UserProgress (SK=PROGRESS#...)
  and UserUsage (SK=USAGE) with no SK prefix collision.
  """

  session_id: UnicodeAttribute = UnicodeAttribute()
  root_world_id: UnicodeAttribute = UnicodeAttribute()
  user_id: UnicodeAttribute = UnicodeAttribute()
  role: UnicodeAttribute = UnicodeAttribute()  # "owner" | "member"
  joined_at: UTCDateTimeAttribute = UTCDateTimeAttribute()

  # Denormalized from WorldMeta (set at creation, updated after generation)
  title: UnicodeAttribute = UnicodeAttribute(null=True)
  description: UnicodeAttribute = UnicodeAttribute(null=True)
  genre: UnicodeAttribute = UnicodeAttribute(null=True)
  world_length: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_url: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_alt_text: UnicodeAttribute = UnicodeAttribute(null=True)
  root_created_at: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)
  generation_status: UnicodeAttribute = UnicodeAttribute(null=True)

  # Live session state
  last_visited_node_id: UnicodeAttribute = UnicodeAttribute(null=True)
  visited_node_count: NumberAttribute = NumberAttribute(default=0)

  # ── Key helpers ──────────────────────────────────────────────────────────

  @classmethod
  def pk(cls, user_id: str) -> str:
    return f"USER#{user_id}"

  @classmethod
  def sk(cls, root_world_id: str, session_id: str) -> str:
    return f"SMEMBER#{root_world_id}#{session_id}"

  @classmethod
  def sk_prefix_for_world(cls, root_world_id: str) -> str:
    return f"SMEMBER#{root_world_id}#"
