"""Node session entity for per-session exploration state of story nodes."""

from __future__ import annotations

from pynamodb.attributes import (
  BooleanAttribute,
  ListAttribute,
  MapAttribute,
  UnicodeAttribute,
)

from app.models.entities.base import BaseCosmonautModel


class BaseChoiceStateMap(MapAttribute):  # type: ignore[type-arg]
  """Tracks whether a base (LLM-generated) choice has been explored in this session."""

  is_explored: BooleanAttribute = BooleanAttribute(default=False)


class CustomChoiceMap(MapAttribute):  # type: ignore[type-arg]
  """A session-scoped custom choice added by a user, with creator attribution."""

  label: UnicodeAttribute = UnicodeAttribute()
  target_node_id: UnicodeAttribute = UnicodeAttribute()
  is_explored: BooleanAttribute = BooleanAttribute(default=False)
  creator_id: UnicodeAttribute = UnicodeAttribute()
  creator_email: UnicodeAttribute = UnicodeAttribute(null=True)
  creator_display_name: UnicodeAttribute = UnicodeAttribute(null=True)


class NodeSession(BaseCosmonautModel):
  """Per-session overlay for a story node, tracking exploration state and custom choices.

  Single-table design:
    PK = SESSION#{session_id}
    SK = NODE#{node_id}

  Lives in the same partition as the parent WorldSession (SK = META).

  ``base_choice_states`` mirrors the root StoryNode's choices list by index,
  tracking only ``is_explored`` per session. ``custom_choices`` are
  session-scoped additions not present on the root StoryNode.
  """

  node_id: UnicodeAttribute = UnicodeAttribute()
  session_id: UnicodeAttribute = UnicodeAttribute()
  root_world_id: UnicodeAttribute = UnicodeAttribute()
  title: UnicodeAttribute = UnicodeAttribute(null=True)
  base_choice_states: ListAttribute[BaseChoiceStateMap] = ListAttribute(of=BaseChoiceStateMap, default=list)
  custom_choices: ListAttribute[CustomChoiceMap] = ListAttribute(of=CustomChoiceMap, default=list)

  # ── Key helpers ──────────────────────────────────────────────────────────

  @classmethod
  def pk(cls, session_id: str) -> str:
    return f"SESSION#{session_id}"

  @classmethod
  def sk(cls, node_id: str) -> str:
    return f"NODE#{node_id}"
