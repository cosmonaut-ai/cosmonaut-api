"""Shared PynamoDB base model bound to the Cosmonaut table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from pynamodb.attributes import (
  UnicodeAttribute,
  UTCDateTimeAttribute,
)
from pynamodb.expressions.condition import Condition
from pynamodb.expressions.update import Action
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
  """Base table binding; individual entities supply their own attributes.

  Automatically manages ``created_at`` and ``updated_at`` timestamps:

  - ``save()`` sets ``created_at`` on first persist and always refreshes ``updated_at``.
  - ``update()`` appends an ``updated_at`` SET action so partial updates are timestamped too.

  Subclasses that need to react to timestamp changes (e.g. syncing a GSI sort key)
  can override ``_on_save()``.
  """

  class Meta:  # type: ignore[misc]
    table_name = settings.DYNAMODB_TABLE_NAME
    region = settings.AWS_REGION
    read_capacity_units = 5
    write_capacity_units = 5

  GSI1: BaseGSI1Model = BaseGSI1Model()

  PK: ClassVar[UnicodeAttribute] = UnicodeAttribute(hash_key=True)
  SK: ClassVar[UnicodeAttribute] = UnicodeAttribute(range_key=True)

  created_at: UTCDateTimeAttribute = UTCDateTimeAttribute(default_for_new=lambda: datetime.now(UTC), null=True)
  updated_at: UTCDateTimeAttribute = UTCDateTimeAttribute(default_for_new=lambda: datetime.now(UTC), null=True)

  # -- Timestamp management ---------------------------------------------------

  def save(  # type: ignore[override]
    self,
    condition: Condition | None = None,
    *,
    add_version_condition: bool = True,
  ) -> dict[str, Any]:
    """Persist the item, automatically maintaining timestamps."""
    now = datetime.now(UTC)
    if not self.created_at:
      self.created_at = now
    self.updated_at = now
    self._on_save()
    return super().save(condition=condition, add_version_condition=add_version_condition)

  def update(  # type: ignore[override]
    self,
    actions: list[Action],
    condition: Condition | None = None,
    *,
    add_version_condition: bool = True,
  ) -> Any:
    """Apply an update expression, automatically refreshing ``updated_at``."""
    actions = list(actions)
    actions.append(BaseCosmonautModel.updated_at.set(datetime.now(UTC)))
    return super().update(actions=actions, condition=condition, add_version_condition=add_version_condition)

  def _on_save(self) -> None:
    """Hook called after timestamps are refreshed but before the DynamoDB write.

    Override in subclasses to perform additional pre-save logic that depends
    on the freshly-set ``updated_at`` value (e.g. syncing a GSI sort key).
    """
