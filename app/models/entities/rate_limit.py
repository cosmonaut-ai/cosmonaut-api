"""TTL-based records for rate limiting and idempotency.

Single-table design:
  - Feedback rate limiting: PK=RATE#FEEDBACK#{user_id}, SK=LIMIT
  - Webhook idempotency: PK=WEBHOOK#{event_id}, SK=PROCESSED

Uses the DynamoDB table's TTL on the ``expiration`` attribute for automatic cleanup.
"""

from __future__ import annotations

from pynamodb.attributes import NumberAttribute

from app.models.entities.base import BaseCosmonautModel


class RateLimitRecord(BaseCosmonautModel):
  """TTL-based record for rate limiting and idempotency.

  Used for:
  - Feedback rate limiting: PK=RATE#FEEDBACK#{user_id}, SK=LIMIT
  - Webhook idempotency: PK=WEBHOOK#{event_id}, SK=PROCESSED
  """

  expiration: NumberAttribute = NumberAttribute()

  @classmethod
  def pk(cls, key: str) -> str:
    return key

  @classmethod
  def sk(cls, key: str = "LIMIT") -> str:
    return key
