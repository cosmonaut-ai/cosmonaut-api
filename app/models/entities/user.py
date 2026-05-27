"""User profile, usage tracking, and username reservation entities.

``UserRecord`` is the primary per-user item, containing profile fields
(``username``, ``is_onboarded``) alongside a nested ``UsageData`` map that
holds subscription tier and quota counters.  The DynamoDB key layout is
unchanged from the legacy ``UserUsage`` entity:

  PK = USER#{user_id}
  SK = USAGE

``UsernameReservation`` is a uniqueness sentinel that guarantees no two users
can claim the same username (case-insensitive):

  PK = USERNAME#{username_lower}
  SK = UNIQUE
"""

from __future__ import annotations

from pynamodb.attributes import (
  BooleanAttribute,
  MapAttribute,
  NumberAttribute,
  UnicodeAttribute,
  UTCDateTimeAttribute,
)
from pynamodb.indexes import AllProjection, GlobalSecondaryIndex

from app.models.entities.base import BaseCosmonautModel

# ---------------------------------------------------------------------------
# Nested usage map
# ---------------------------------------------------------------------------


class UsageData(MapAttribute[str, UnicodeAttribute]):
  """Subscription tier and quota counters stored as a DynamoDB map attribute."""

  tier: UnicodeAttribute = UnicodeAttribute(default="FREE")
  stripe_customer_id: UnicodeAttribute = UnicodeAttribute(null=True)

  nodes_used: NumberAttribute = NumberAttribute(default=0)
  worlds_created: NumberAttribute = NumberAttribute(default=0)
  audio_narrations_used: NumberAttribute = NumberAttribute(default=0)
  saved_world_count: NumberAttribute = NumberAttribute(default=0)

  period_end: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

  subscription_status: UnicodeAttribute = UnicodeAttribute(null=True)

  pending_cancellation: BooleanAttribute = BooleanAttribute(default=False)
  cancellation_date: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

  pending_tier: UnicodeAttribute = UnicodeAttribute(null=True)
  pending_tier_date: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)


# ---------------------------------------------------------------------------
# Primary user record
# ---------------------------------------------------------------------------


class UserRecordGSI2Model(GlobalSecondaryIndex["UserRecord"]):
  """Sparse GSI for the admin user directory."""

  GSI2PK: UnicodeAttribute = UnicodeAttribute(hash_key=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(range_key=True)

  class Meta:
    projection = AllProjection()


class UserRecord(BaseCosmonautModel):
  """Per-user profile and usage record.

  Single-table design:
    PK = USER#{user_id}
    SK = USAGE
  """

  user_id: UnicodeAttribute = UnicodeAttribute()
  username: UnicodeAttribute = UnicodeAttribute(null=True)
  email: UnicodeAttribute = UnicodeAttribute(null=True)
  cognito_username: UnicodeAttribute = UnicodeAttribute(null=True)
  cognito_status: UnicodeAttribute = UnicodeAttribute(null=True)
  email_verified: BooleanAttribute = BooleanAttribute(null=True)
  enabled: BooleanAttribute = BooleanAttribute(null=True)
  is_onboarded: BooleanAttribute = BooleanAttribute(default=False)
  newsletter_opted_in: BooleanAttribute = BooleanAttribute(default=False)

  usage: UsageData = UsageData(default_for_new=UsageData)

  # Sparse admin directory index. Populated only for real app users.
  GSI2: UserRecordGSI2Model = UserRecordGSI2Model()
  GSI2PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2PK", null=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2SK", null=True)

  # ── Key helpers ──────────────────────────────────────────────────────────

  @classmethod
  def pk(cls, user_id: str) -> str:
    return f"USER#{user_id}"

  @classmethod
  def sk(cls) -> str:
    return "USAGE"

  @classmethod
  def gsi2_pk_user_profiles(cls) -> str:
    return "USER_PROFILES"

  @classmethod
  def gsi2_sk_created(cls, created_at: str, user_id: str) -> str:
    return f"CREATED#{created_at}#{user_id}"

  def _on_save(self) -> None:
    """Keep admin-directory GSI keys in sync with the record creation time."""
    if self.created_at:
      self.GSI2PK = UserRecord.gsi2_pk_user_profiles()
      self.GSI2SK = UserRecord.gsi2_sk_created(self.created_at.isoformat(), str(self.user_id))


# ---------------------------------------------------------------------------
# Username uniqueness sentinel
# ---------------------------------------------------------------------------


class UsernameReservation(BaseCosmonautModel):
  """Atomic uniqueness lock for usernames (case-insensitive).

  Single-table design:
    PK = USERNAME#{username_lower}
    SK = UNIQUE
  """

  user_id: UnicodeAttribute = UnicodeAttribute()

  @classmethod
  def pk(cls, username: str) -> str:
    return f"USERNAME#{username.lower()}"

  @classmethod
  def sk(cls) -> str:
    return "UNIQUE"


# ---------------------------------------------------------------------------
# Tombstone (account deletion)
# ---------------------------------------------------------------------------


class UsageTombstone(BaseCosmonautModel):
  """Tombstone preserving usage counters after account deletion.

  Keyed by SHA-256 of the user's email. Used during re-registration to
  carry forward unexpired quota usage, preventing free-tier abuse.
  TTL is 1 year from deletion -- long enough to preserve the lifetime
  audio narration cap across re-registration cycles.
  """

  email_hash: UnicodeAttribute = UnicodeAttribute()
  worlds_created: NumberAttribute = NumberAttribute(default=0)
  nodes_used: NumberAttribute = NumberAttribute(default=0)
  audio_narrations_used: NumberAttribute = NumberAttribute(default=0)
  period_end: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)
  deleted_at: UTCDateTimeAttribute = UTCDateTimeAttribute()
  expiration: NumberAttribute = NumberAttribute()

  @classmethod
  def pk(cls, email_hash: str) -> str:
    return f"TOMBSTONE#{email_hash}"

  @classmethod
  def sk(cls) -> str:
    return "USAGE"
