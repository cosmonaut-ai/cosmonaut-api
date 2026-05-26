"""Shared DTOs for user usage and subscription state."""

from __future__ import annotations

from app.models.dtos.base import DTOModel


class UsageStateDTO(DTOModel):
  """Fields common to public and admin usage responses."""

  username: str | None = None
  is_onboarded: bool
  tier: str
  nodes_used: int
  worlds_created: int
  audio_narrations_used: int
  period_end: str | None = None
  pending_cancellation: bool
  cancellation_date: str | None = None
  subscription_status: str | None = None
  pending_tier: str | None = None
  pending_tier_date: str | None = None
  newsletter_opted_in: bool


class UsageResponse(UsageStateDTO):
  """Public current-user usage response, including display and limits."""

  display_name: str
  nodes_limit: int
  worlds_limit: int
  audio_narrations_limit: int
