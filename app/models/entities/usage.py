"""User usage / subscription entity for quota tracking."""

from __future__ import annotations

from pynamodb.attributes import BooleanAttribute, NumberAttribute, UnicodeAttribute, UTCDateTimeAttribute

from app.models.entities.base import BaseCosmonautModel


class UserUsage(BaseCosmonautModel):
  """Tracks per-user subscription tier and usage counters.

  Single-table design:
    PK = USER#{user_id}
    SK = USAGE
  """

  user_id: UnicodeAttribute = UnicodeAttribute()
  tier: UnicodeAttribute = UnicodeAttribute(default="FREE")
  stripe_customer_id: UnicodeAttribute = UnicodeAttribute(null=True)

  nodes_used: NumberAttribute = NumberAttribute(default=0)
  worlds_created: NumberAttribute = NumberAttribute(default=0)
  audio_narrations_used: NumberAttribute = NumberAttribute(default=0)
  saved_world_count: NumberAttribute = NumberAttribute(default=0)

  period_end: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

  # Stripe subscription status (e.g. "active", "past_due", "unpaid", "paused")
  subscription_status: UnicodeAttribute = UnicodeAttribute(null=True)

  # Pending cancellation state (set when cancel_at_period_end or cancel_at on Stripe)
  pending_cancellation: BooleanAttribute = BooleanAttribute(default=False)
  cancellation_date: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

  # Pending plan change (e.g. scheduled downgrade from COSMONAUT to EXPLORER at period end)
  pending_tier: UnicodeAttribute = UnicodeAttribute(null=True)
  pending_tier_date: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

  # Newsletter opt-in (Buttondown)
  newsletter_opted_in: BooleanAttribute = BooleanAttribute(default=False)

  # ── Key helpers ──────────────────────────────────────────────────────────

  @classmethod
  def pk(cls, user_id: str) -> str:
    return f"USER#{user_id}"

  @classmethod
  def sk(cls) -> str:
    return "USAGE"


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
