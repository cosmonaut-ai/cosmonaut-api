"""Per-user, per-endpoint rate limiting via DynamoDB.

Uses the existing RateLimitRecord entity with TTL-based cleanup.
Implements a sliding window counter: count requests in the last N seconds,
block if count exceeds the limit.
"""

import time

from aws_lambda_powertools import Logger
from fastapi import HTTPException, status

from app.core.config import settings
from app.models.entities.rate_limit import RateLimitRecord

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)


class RateLimitExceededError(Exception):
  def __init__(self, endpoint: str, limit: int, window_seconds: int):
    self.endpoint = endpoint
    self.limit = limit
    self.window_seconds = window_seconds
    self.retry_after = window_seconds
    super().__init__(f"Rate limit exceeded for {endpoint}: max {limit} requests per {window_seconds}s")


RATE_LIMITS: dict[str, tuple[int, int]] = {
  "generate-text": (10, 60),
  "audio": (5, 60),
  "create-world": (3, 60),
}


def check_rate_limit(user_id: str, endpoint: str) -> None:
  """Check and increment the rate limit counter for a user+endpoint.

  Uses a DynamoDB record with TTL for automatic cleanup.

  Raises RateLimitExceededError if the limit is exceeded.
  """
  if endpoint not in RATE_LIMITS:
    return

  max_requests, window_seconds = RATE_LIMITS[endpoint]
  now = int(time.time())

  pk = f"RATE#{endpoint}#{user_id}"
  sk = "LIMIT"

  try:
    record = RateLimitRecord.get(pk, sk)
    if record.expiration and int(record.expiration) > now:
      count = int(record.count) if record.count else 1
      if count >= max_requests:
        raise RateLimitExceededError(endpoint, max_requests, window_seconds)
      record.count = count + 1
      record.save()
    else:
      record.count = 1
      record.expiration = now + window_seconds
      record.save()
  except RateLimitRecord.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
    record = RateLimitRecord(
      PK=pk,
      SK=sk,
      expiration=now + window_seconds,
      count=1,
    )
    record.save()


def raise_rate_limit_error(e: RateLimitExceededError) -> HTTPException:
  """Convert a RateLimitExceededError to an HTTP 429 response."""
  return HTTPException(
    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    detail=str(e),
    headers={"Retry-After": str(e.retry_after)},
  )
