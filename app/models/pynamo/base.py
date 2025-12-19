# pyright: ignore-all
"""Shared PynamoDB base model bound to the Cosmonaut table."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import ClassVar

from pynamodb.attributes import (
    UnicodeAttribute,
    UTCDateTimeAttribute,
)
from pynamodb.models import Model

from app.core.config import settings


class BaseCosmonautModel(Model):
    """Base table binding; individual entities supply their own attributes."""

    pk: ClassVar[UnicodeAttribute] = UnicodeAttribute(hash_key=True)
    sk: ClassVar[UnicodeAttribute] = UnicodeAttribute(range_key=True)

    created_at: UTCDateTimeAttribute = UTCDateTimeAttribute(
        default_for_new=datetime.now(timezone.utc), null=True
    )
    updated_at: UTCDateTimeAttribute = UTCDateTimeAttribute(
        default_for_new=datetime.now(timezone.utc), null=True
    )


BaseCosmonautModel.Meta.table_name = settings.DYNAMODB_TABLE_NAME
BaseCosmonautModel.Meta.region = os.getenv("AWS_REGION", "us-east-1")
BaseCosmonautModel.Meta.host = os.getenv("DYNAMODB_ENDPOINT")
BaseCosmonautModel.Meta.read_capacity_units = 5
BaseCosmonautModel.Meta.write_capacity_units = 5
