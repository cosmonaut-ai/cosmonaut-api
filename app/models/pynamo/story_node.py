"""Story node entity representing narrative content and branching."""

from __future__ import annotations

from pynamodb.attributes import ListAttribute, MapAttribute, UnicodeAttribute

from app.models.pynamo.base import BaseCosmonautModel


class ChoiceMap(MapAttribute[str, str]):
    label: UnicodeAttribute = UnicodeAttribute()
    target: UnicodeAttribute = UnicodeAttribute()


class StoryNode(BaseCosmonautModel):
    """A node in the branching story graph."""

    text: UnicodeAttribute = UnicodeAttribute()
    story_summary: UnicodeAttribute = UnicodeAttribute(null=True)
    title: UnicodeAttribute = UnicodeAttribute(null=True)
    choices: ListAttribute[ChoiceMap] = ListAttribute(of=ChoiceMap, default=list)
    parent_id: UnicodeAttribute = UnicodeAttribute(null=True)
    ancestors: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)
