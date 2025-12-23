"""Story node entity representing narrative content and branching."""

from __future__ import annotations

from pynamodb.attributes import ListAttribute, MapAttribute, UnicodeAttribute
from pynamodb.indexes import GlobalSecondaryIndex, IncludeProjection

from app.models.dtos.story_node import ChoiceDTO, StoryNodeDTO
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


class StoryNode(BaseCosmonautModel):
  """A node in the branching story graph."""

  GSI2: GSI2Model = GSI2Model()

  GSI2PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2PK", null=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2SK", null=True)

  id: UnicodeAttribute = UnicodeAttribute(attr_name="node_id")
  world_id: UnicodeAttribute = UnicodeAttribute()
  text: UnicodeAttribute = UnicodeAttribute()
  story_summary: UnicodeAttribute = UnicodeAttribute(null=True)
  title: UnicodeAttribute = UnicodeAttribute(null=True, attr_name="node_title")
  choices: ListAttribute[ChoiceMap] = ListAttribute(
    of=ChoiceMap, default=list, attr_name="node_choices"
  )
  parent_id: UnicodeAttribute = UnicodeAttribute(null=True, attr_name="node_parent_id")
  ancestors: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)

  def to_dto(self) -> StoryNodeDTO:
    return StoryNodeDTO(
      id=self.id,
      world_id=self.world_id,
      text=self.text,
      story_summary=self.story_summary,
      title=self.title,
      choices=[choice.to_dto() for choice in self.choices],
      parent_id=self.parent_id,
      ancestors=[str(ancestor) for ancestor in self.ancestors],
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
      parent_id=dto.parent_id,
      ancestors=[ancestor for ancestor in dto.ancestors],
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
