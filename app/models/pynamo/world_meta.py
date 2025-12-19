"""World metadata entity."""

from __future__ import annotations

from pynamodb.attributes import NumberAttribute, UnicodeAttribute

from app.models.pynamo.base import BaseCosmonautModel


class WorldMeta(BaseCosmonautModel):
    """Container for world-level metadata."""

    title: UnicodeAttribute = UnicodeAttribute()
    description: UnicodeAttribute = UnicodeAttribute(null=True)
    genre: UnicodeAttribute = UnicodeAttribute(attr_name="GSI1PK", null=True)
    score: UnicodeAttribute = UnicodeAttribute(attr_name="GSI1SK", null=True)
    author_id: UnicodeAttribute = UnicodeAttribute()
    root_node_id: UnicodeAttribute = UnicodeAttribute()
    visibility: UnicodeAttribute = UnicodeAttribute(default="private")

    # Prompt defining the world's backstory and setting - what the user is entering when they create
    # a new world.
    world_prompt: UnicodeAttribute = UnicodeAttribute(null=True)

    # Additional information about the story - what the main storyline is, main characters, etc. LLM
    # generated.
    world_info: UnicodeAttribute = UnicodeAttribute(null=True)

    # Prompt defining the narrator's personality and style.
    narrator_profile: UnicodeAttribute = UnicodeAttribute(null=True)

    # User setting defining how long (typically) the text of a node should be.
    node_text_length: NumberAttribute = NumberAttribute(null=True)

    world_image_url: UnicodeAttribute = UnicodeAttribute(null=True)
    world_image_alt_text: UnicodeAttribute = UnicodeAttribute(null=True)
    world_image_width: UnicodeAttribute = UnicodeAttribute(null=True)
    world_image_height: UnicodeAttribute = UnicodeAttribute(null=True)
    world_image_size: UnicodeAttribute = UnicodeAttribute(null=True)
