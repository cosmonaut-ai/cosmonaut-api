"""DTOs for admin-only API responses and requests."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.models.dtos.base import DTOModel
from app.models.dtos.usage import UsageStateDTO

AdminTier = Literal["FREE", "EXPLORER", "COSMONAUT"]


class AdminCognitoUserDTO(DTOModel):
  """Cognito metadata surfaced in the admin UI."""

  username: str
  sub: str
  email: str
  tier: str
  stripe_customer_id: str | None = None
  email_verified: bool
  created_at: str | None = None
  status: str | None = None
  enabled: bool | None = None


class AdminUserUsageDTO(UsageStateDTO):
  """DynamoDB usage/subscription record for an arbitrary user."""

  user_id: str
  stripe_customer_id: str | None = None
  saved_world_count: int
  created_at: str | None = None
  updated_at: str | None = None


class AdminUserGroupsResponse(DTOModel):
  groups: list[str]


class AdminTierUpdateRequest(DTOModel):
  tier: AdminTier


class AdminTierUpdateResponse(DTOModel):
  success: bool
  tier: AdminTier
  warning: str | None = None


class AdminFeaturedUpdateRequest(DTOModel):
  """Promote a world to featured, optionally at a specific order."""

  order: int | None = Field(default=None, ge=1)


class AdminFeaturedOrderItem(DTOModel):
  world_id: str = Field(..., min_length=1)
  featured_order: int = Field(..., ge=1)


class AdminFeaturedOrderUpdateRequest(DTOModel):
  items: list[AdminFeaturedOrderItem] = Field(..., min_length=1)
