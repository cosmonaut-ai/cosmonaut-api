"""User progress entity tracking save state within a world."""

from __future__ import annotations

from pynamodb.attributes import UnicodeAttribute, UTCDateTimeAttribute

from app.models.pynamo.base import BaseCosmonautModel


class UserProgress(BaseCosmonautModel):
    """Save state per user per world."""

    current_node_id: UnicodeAttribute = UnicodeAttribute()
    last_played: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)
    world_title: UnicodeAttribute = UnicodeAttribute(null=True)
