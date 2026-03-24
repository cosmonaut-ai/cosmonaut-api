"""Invite token entity for sharing private worlds via time-limited links."""

from __future__ import annotations

from pynamodb.attributes import NumberAttribute, UnicodeAttribute
from pynamodb.indexes import GlobalSecondaryIndex, KeysOnlyProjection

from app.models.entities.base import BaseCosmonautModel


class InviteTokenGSI3(GlobalSecondaryIndex["WorldInviteToken"]):
  """Lookup all invite tokens for a root world via GSI3.

  Shares the table-level GSI3 with WorldSession. Token items use
  ``INVITE#{token}`` as the range key, distinguishing them from
  ``SESSION#{session_id}`` entries.
  """

  GSI3PK: UnicodeAttribute = UnicodeAttribute(hash_key=True)
  GSI3SK: UnicodeAttribute = UnicodeAttribute(range_key=True)

  class Meta:
    projection = KeysOnlyProjection()


class WorldInviteToken(BaseCosmonautModel):
  """A time-limited invite token for sharing access to a world.

  Single-table design:
    PK = INVITE#{token}
    SK = META

  GSI3 (ROOTWORLD#{root_world_id} -> INVITE#{token}) enables lookup
  of the active token for a given world.

  One active token per world at any time, enforced by the service layer.
  Tokens expire after 24 hours and are auto-deleted by DynamoDB TTL
  on the ``expiration`` attribute.
  """

  GSI3: InviteTokenGSI3 = InviteTokenGSI3()

  token: UnicodeAttribute = UnicodeAttribute()
  root_world_id: UnicodeAttribute = UnicodeAttribute()
  created_by: UnicodeAttribute = UnicodeAttribute()
  expires_at: UnicodeAttribute = UnicodeAttribute()
  use_count: NumberAttribute = NumberAttribute(default=0)

  # DynamoDB TTL attribute — epoch seconds matching expires_at.
  expiration: NumberAttribute = NumberAttribute()

  GSI3PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI3PK", null=True)
  GSI3SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI3SK", null=True)

  # ── Key helpers ──────────────────────────────────────────────────────────

  @classmethod
  def pk(cls, token: str) -> str:
    return f"INVITE#{token}"

  @classmethod
  def sk(cls) -> str:
    return "META"

  @classmethod
  def gsi3_pk(cls, root_world_id: str) -> str:
    return f"ROOTWORLD#{root_world_id}"

  @classmethod
  def gsi3_sk(cls, token: str) -> str:
    return f"INVITE#{token}"
