"""Shared PynamoDB base model bound to the Cosmonaut table."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from pynamodb.attributes import (
  UnicodeAttribute,
  UTCDateTimeAttribute,
)
from pynamodb.indexes import AllProjection, GlobalSecondaryIndex
from pynamodb.models import Model

from app.core.config import settings


class BaseGSI1Model(GlobalSecondaryIndex):  # type: ignore[type-arg]
  """Base table binding for GSI1; individual entities supply their own attributes."""

  GSI1PK: ClassVar[UnicodeAttribute] = UnicodeAttribute(hash_key=True)
  GSI1SK: ClassVar[UnicodeAttribute] = UnicodeAttribute(range_key=True)

  class Meta:  # type: ignore[misc]
    table_name = settings.DYNAMODB_TABLE_NAME
    region = settings.AWS_REGION
    projection = AllProjection()


class BaseCosmonautModel(Model):
  """Base table binding; individual entities supply their own attributes."""

  class Meta:  # type: ignore[misc]
    table_name = settings.DYNAMODB_TABLE_NAME
    region = settings.AWS_REGION
    read_capacity_units = 5
    write_capacity_units = 5

  GSI1: BaseGSI1Model = BaseGSI1Model()

  PK: ClassVar[UnicodeAttribute] = UnicodeAttribute(hash_key=True)
  SK: ClassVar[UnicodeAttribute] = UnicodeAttribute(range_key=True)

  created_at: UTCDateTimeAttribute = UTCDateTimeAttribute(
    default_for_new=datetime.now(timezone.utc), null=True
  )
  updated_at: UTCDateTimeAttribute = UTCDateTimeAttribute(
    default_for_new=datetime.now(timezone.utc), null=True
  )
