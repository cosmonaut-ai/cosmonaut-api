"""Story node entity representing narrative content and branching."""

from __future__ import annotations

from functools import cached_property

from pynamodb.attributes import ListAttribute, MapAttribute, UnicodeAttribute
from pynamodb.indexes import GlobalSecondaryIndex, IncludeProjection

from app.models.dtos.story_node import (
  ChoiceDTO,
  StoryNodeContextDTO,
  StoryNodeDTO,
  StoryNodeProcessingStatus,
)
from app.models.entities.base import BaseCosmonautModel


class GSI2Model(GlobalSecondaryIndex):  # type: ignore[type-arg]
  """Global secondary index for story nodes by world ID."""

  GSI2PK: UnicodeAttribute = UnicodeAttribute(hash_key=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(range_key=True)

  class Meta:  # type: ignore[misc]
    projection = IncludeProjection(["node_title", "node_choices", "node_parent_id"])


class ChoiceMap(MapAttribute[str, UnicodeAttribute]):
  label: UnicodeAttribute = UnicodeAttribute()
  target: UnicodeAttribute = UnicodeAttribute(null=True)

  def to_dto(self) -> ChoiceDTO:
    return ChoiceDTO(label=self.label, target=self.target)


class StoryNodeContext(MapAttribute[str, UnicodeAttribute]):
  """Context to be provided to the LLM when generating a new story node.

  This is generated asynchronously after a node is created and will be used to generate children.
  """

  world_facts: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list, null=True)
  branch_facts: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list, null=True)
  similar_nodes: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list, null=True)

  def to_dto(self) -> StoryNodeContextDTO:
    return StoryNodeContextDTO(
      world_facts=self.world_facts,  # type: ignore[arg-type]
      branch_facts=self.branch_facts,  # type: ignore[arg-type]
      similar_nodes=self.similar_nodes,  # type: ignore[arg-type]
    )

  @classmethod
  def from_dto(cls, dto: StoryNodeContextDTO) -> StoryNodeContext:
    return StoryNodeContext(
      world_facts=dto.world_facts,  # type: ignore[arg-type]
      branch_facts=dto.branch_facts,  # type: ignore[arg-type]
      similar_nodes=dto.similar_nodes,  # type: ignore[arg-type]
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
  text: UnicodeAttribute = UnicodeAttribute()
  story_summary: UnicodeAttribute = UnicodeAttribute(null=True)
  title: UnicodeAttribute = UnicodeAttribute(null=True, attr_name="node_title")
  choices: ListAttribute[ChoiceMap] = ListAttribute(of=ChoiceMap, default=list, attr_name="node_choices")
  processing_status: UnicodeAttribute = UnicodeAttribute(default="pending")

  context: StoryNodeContext = StoryNodeContext(null=True)

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
    return self._base52_to_number(self.id[len(self.parent_id) :])

  @staticmethod
  def _number_to_base52(number: int) -> str:
    """Convert a number to a base-52 string."""
    if number < 0:
      raise ValueError("Number must be positive")
    if number == 0:
      return "a"
    result = ""
    while number > 0:
      if number % 52 > 25:
        result = chr((number) % 52 - 26 + ord("A")) + result
      else:
        result = chr((number) % 52 + ord("a")) + result
      number //= 52
    return result

  @staticmethod
  def _base52_to_number(base52_string: str) -> int:
    """Convert a base-52 string to a number."""
    if not base52_string:
      return 0
    number = 0
    for i, c in enumerate(reversed(base52_string)):
      if c.isdigit():
        continue

      base_value = ord(c) - ord("a") if c.islower() else ord(c) - ord("A") + 26
      if base_value < 0 or base_value > 51:
        raise ValueError("Invalid base-52 string")
      number += base_value * 52**i
    return number

  def get_child_id(self, choice_index: int) -> str:
    """Get the ID of the child node for a given choice index."""
    if choice_index < 0:
      raise ValueError("Choice index must be non-negative")
    base52_index = self._number_to_base52(choice_index)
    final_string = ""
    if len(base52_index) > 1:
      final_string = f"{len(base52_index)}{base52_index}"
    else:
      final_string = base52_index
    return f"{self.id}{final_string}"

  def to_dto(self) -> StoryNodeDTO:
    return StoryNodeDTO(
      id=self.id,
      world_id=self.world_id,
      text=self.text,
      story_summary=self.story_summary,
      title=self.title,
      choices=[choice.to_dto() for choice in self.choices],
      parent_id=self.parent_id,
      ancestors=self.ancestors,
      context=self.context.to_dto() if self.context else None,
      processing_status=StoryNodeProcessingStatus(self.processing_status),
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
      choices=[ChoiceMap(label=choice.label, target=choice.target) for choice in dto.choices],
      processing_status=StoryNodeProcessingStatus(dto.processing_status).value,
      context=StoryNodeContext.from_dto(dto.context) if dto.context else None,
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
