"""Session membership entity linking users to their world sessions."""

from __future__ import annotations

from datetime import datetime

from pynamodb.attributes import NumberAttribute, UnicodeAttribute, UTCDateTimeAttribute
from pynamodb.indexes import AllProjection, GlobalSecondaryIndex

from app.models.entities.base import BaseCosmonautModel


class GSI2Model(GlobalSecondaryIndex["SessionMembership"]):
  """GSI for listing a user's sessions sorted by last access time (descending)."""

  GSI2PK: UnicodeAttribute = UnicodeAttribute(hash_key=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(range_key=True)

  class Meta:
    projection = AllProjection()


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

  GSI2 (USER#{user_id} -> ACCESSED#{iso_ts}#{session_id}) enables
  chronologically sorted dashboard queries by last access time.

  Coexists in the USER# partition alongside UserRecord (SK=USAGE)
  with no SK prefix collision.
  """

  GSI2: GSI2Model = GSI2Model()

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
  image_generation_status: UnicodeAttribute = UnicodeAttribute(null=True)
  root_node_id: UnicodeAttribute = UnicodeAttribute(null=True)
  root_created_at: UnicodeAttribute = UnicodeAttribute(null=True)
  generation_status: UnicodeAttribute = UnicodeAttribute(null=True)
  vocab_level: UnicodeAttribute = UnicodeAttribute(null=True)
  content_filter: UnicodeAttribute = UnicodeAttribute(null=True)

  # Live session state
  last_visited_node_id: UnicodeAttribute = UnicodeAttribute(null=True)
  visited_node_count: NumberAttribute = NumberAttribute(default=0)

  # GSI2 keys — nullable so pre-existing items deserialize without error
  # and remain invisible to GSI2 queries (sparse index).
  GSI2PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2PK", null=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2SK", null=True)

  last_accessed_at: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

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

  @classmethod
  def gsi2_pk(cls, user_id: str) -> str:
    return f"USER#{user_id}"

  @classmethod
  def gsi2_sk(cls, last_accessed_at: datetime, session_id: str) -> str:
    return f"ACCESSED#{last_accessed_at.isoformat()}#{session_id}"
