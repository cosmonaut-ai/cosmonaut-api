"""Story node entity representing narrative content and branching."""

from __future__ import annotations

from functools import cached_property

from pynamodb.attributes import BooleanAttribute, ListAttribute, MapAttribute, UnicodeAttribute
from pynamodb.indexes import GlobalSecondaryIndex, IncludeProjection

from app.models.dtos.story_node import (
  ChoiceDTO,
  GenerationStatus,
  StoryNodeContextDTO,
  StoryNodeDTO,
  StoryNodeProcessingStatus,
)
from app.models.entities.base import BaseCosmonautModel
from app.utils import base52_to_number, number_to_base52


class GSI2Model(GlobalSecondaryIndex):  # type: ignore[type-arg]
  """Global secondary index for story nodes by world ID."""

  GSI2PK: UnicodeAttribute = UnicodeAttribute(hash_key=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(range_key=True)

  class Meta:  # type: ignore[misc]
    projection = IncludeProjection(["node_title", "node_choices", "node_parent_id", "generation_status", "node_id"])


class ChoiceMap(MapAttribute[str, UnicodeAttribute]):
  label: UnicodeAttribute = UnicodeAttribute()
  target: UnicodeAttribute = UnicodeAttribute(null=True)
  is_created: BooleanAttribute = BooleanAttribute(default=False)
  outcome: UnicodeAttribute = UnicodeAttribute(null=True)
  is_custom: BooleanAttribute = BooleanAttribute(default=False)
  creator: UnicodeAttribute = UnicodeAttribute(null=True)

  def to_dto(self) -> ChoiceDTO:
    return ChoiceDTO(
      label=self.label,
      target=self.target,
      is_created=bool(self.is_created),
      outcome=self.outcome,
      is_custom=bool(self.is_custom),
      creator=self.creator,
    )


class StoryNodeContext(MapAttribute[str, UnicodeAttribute]):
  """Context to be provided to the LLM when generating a new story node.

  This is generated asynchronously after a node is created and will be used to generate children.
  """

  world_facts: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list, null=True)
  branch_facts: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list, null=True)
  similar_nodes: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list, null=True)

  def to_dto(self) -> StoryNodeContextDTO:
    return StoryNodeContextDTO(
      world_facts=[str(f) for f in self.world_facts] if self.world_facts else [],
      branch_facts=[str(f) for f in self.branch_facts] if self.branch_facts else [],
      similar_nodes=[str(n) for n in self.similar_nodes] if self.similar_nodes else [],
    )

  @classmethod
  def from_dto(cls, dto: StoryNodeContextDTO) -> StoryNodeContext:
    return StoryNodeContext(
      world_facts=dto.world_facts,
      branch_facts=dto.branch_facts,
      similar_nodes=dto.similar_nodes,
    )


class StoryNode(BaseCosmonautModel):
  """A node in the branching story graph.
  IDs are structured somewhat uniquely. Root node is "0", then each subsequent node is a letter
  corresponding to the index of the choice that led to it. For example, the second choice from the
  root node would be "b", the third choice from the second node would be "c", etc. For choices
  beyond 26, the letters are uppercase. For example, the 27th choice from the root node would be "A"
  and the 28th choice from the root node would be "B". This can essentially be thought of as a
  base-52 number system. For example, the 53rd choice from the root node would be "aa". For any
  choices that require more than one letter, a digit denoting the number of letters is added to the
  front. For example, the 53rd choice from the root node would be "2aa".

  So, the below ID corresponds to the following tree:
  - 0aacda2aaA
  - root (0) -> 1st choice (a) -> 1st (a) -> 3rd (c) -> 4th (d)-> 1st (a) -> 53rd (2aa) -> 53rd (A)
  """

  GSI2: GSI2Model = GSI2Model()

  GSI2PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2PK", null=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2SK", null=True)

  id: UnicodeAttribute = UnicodeAttribute(attr_name="node_id")
  world_id: UnicodeAttribute = UnicodeAttribute()
  text: UnicodeAttribute = UnicodeAttribute(null=True)
  story_summary: UnicodeAttribute = UnicodeAttribute(null=True)
  title: UnicodeAttribute = UnicodeAttribute(null=True, attr_name="node_title")
  choices: ListAttribute[ChoiceMap] = ListAttribute(of=ChoiceMap, default=list, attr_name="node_choices")
  parent_choice: ChoiceMap = ChoiceMap(null=True)
  processing_status: UnicodeAttribute = UnicodeAttribute(default="pending")
  generation_status: UnicodeAttribute = UnicodeAttribute(default="initialized")

  context: StoryNodeContext = StoryNodeContext(null=True)

  audio: MapAttribute[str, str] = MapAttribute(default=dict, null=True)

  @cached_property
  def ancestors(self) -> list[str]:
    """Get the ancestors of the node, in order from root to self."""
    ancestors: list[str] = []
    build_id = ""
    i = 0

    while i < len(self.id):
      char = self.id[i]
      if char.isdigit():
        if char == "0":
          build_id += "0"
        else:
          build_id += self.id[i + 1 : i + int(char) + 1]
        i += int(char) + 1
      else:
        build_id += char
        i += 1
      ancestors.append(build_id)
    return ancestors

  @cached_property
  def depth(self) -> int:
    """Get the depth of the node."""
    return len(self.ancestors)

  @cached_property
  def parent_id(self) -> str | None:
    """Get the parent ID of the node."""
    if len(self.ancestors) > 1:
      return self.ancestors[-2]
    return None

  @cached_property
  def choice_index(self) -> int | None:
    """Get the index of the choice that led to this node."""
    if not self.parent_id:
      return None
    return base52_to_number(self.id[len(self.parent_id) :])

  def get_child_id(self, choice_index: int) -> str:
    """Get the ID of the child node for a given choice index."""
    return StoryNode.get_child_id_static(self.id, choice_index)

  @staticmethod
  def get_child_id_static(parent_id: str, choice_index: int) -> str:
    if choice_index < 0:
      raise ValueError("Choice index must be non-negative")
    base52_index = number_to_base52(choice_index)
    final_string = ""
    if len(base52_index) > 1:
      final_string = f"{len(base52_index)}{base52_index}"
    else:
      final_string = base52_index
    return f"{parent_id}{final_string}"

  def to_dto(self) -> StoryNodeDTO:
    return StoryNodeDTO(
      id=self.id,
      world_id=self.world_id,
      text=self.text,
      story_summary=self.story_summary,
      title=self.title,
      choices=[choice.to_dto() for choice in self.choices],
      parent_choice=self.parent_choice.to_dto() if self.parent_choice else None,
      parent_id=self.parent_id,
      ancestors=self.ancestors,
      context=self.context.to_dto() if self.context else None,
      processing_status=StoryNodeProcessingStatus(self.processing_status),
      generation_status=GenerationStatus(self.generation_status),
      audio=dict(self.audio.attribute_values) if self.audio else {},
      created_at=self.created_at if self.created_at else None,
      updated_at=self.updated_at if self.updated_at else None,
    )

  @classmethod
  def from_dto(cls, dto: StoryNodeDTO) -> StoryNode:
    if dto.world_id is None or dto.id is None:
      raise ValueError("World ID and node ID are required to convert to PynamoDB entity")
    return StoryNode(
      PK=cls.pk(dto.world_id),
      SK=cls.sk(dto.id),
      GSI2PK=cls.gsi2_pk(dto.world_id),
      GSI2SK=cls.gsi2_sk(dto.id),
      id=dto.id,
      world_id=dto.world_id,
      text=dto.text,
      story_summary=dto.story_summary,
      title=dto.title,
      choices=[
        ChoiceMap(
          label=choice.label,
          target=choice.target,
          is_custom=choice.is_custom,
          creator=choice.creator,
        )
        for choice in dto.choices
      ],
      parent_choice=ChoiceMap(
        label=dto.parent_choice.label,
        target=dto.parent_choice.target,
        is_custom=dto.parent_choice.is_custom,
        creator=dto.parent_choice.creator,
        outcome=dto.parent_choice.outcome,
      )
      if dto.parent_choice
      else None,
      processing_status=StoryNodeProcessingStatus(dto.processing_status).value,
      generation_status=GenerationStatus(dto.generation_status).value,
      context=StoryNodeContext.from_dto(dto.context) if dto.context else None,
      audio=dto.audio if dto.audio else None,
    )

  @classmethod
  def pk(cls, world_id: str) -> str:
    return f"WORLD#{world_id}"

  @classmethod
  def sk(cls, node_id: str) -> str:
    return f"NODE#{node_id}"

  @classmethod
  def gsi2_pk(cls, world_id: str) -> str:
    return f"WORLD#{world_id}"

  @classmethod
  def gsi2_sk(cls, node_id: str) -> str:
    return f"NODE#{node_id}"
