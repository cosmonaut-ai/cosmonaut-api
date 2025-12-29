"""World metadata entity."""

from __future__ import annotations

from datetime import datetime, timezone

from pynamodb.attributes import ListAttribute, NumberAttribute, UnicodeAttribute

from app.models.dtos.world_meta import GenerationStatus, WorldMetaDTO
from app.models.entities.base import BaseCosmonautModel


class WorldMeta(BaseCosmonautModel):
  """Container for world-level metadata."""

  GSI1PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI1PK", null=True)
  GSI1SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI1SK", null=True)

  id: UnicodeAttribute = UnicodeAttribute()
  title: UnicodeAttribute = UnicodeAttribute(null=True)
  description: UnicodeAttribute = UnicodeAttribute(null=True)
  genre: UnicodeAttribute = UnicodeAttribute(null=True)
  score: UnicodeAttribute = UnicodeAttribute(null=True)
  author_id: UnicodeAttribute = UnicodeAttribute(null=True)
  root_node_id: UnicodeAttribute = UnicodeAttribute(null=True)
  visibility: UnicodeAttribute = UnicodeAttribute(default="private")
  generation_status: UnicodeAttribute = UnicodeAttribute(default="initialized")

  # Prompt defining the world's backstory and setting - what the user is entering when they create
  # a new world.
  world_prompt: UnicodeAttribute = UnicodeAttribute(null=True)

  # Additional information about the story - what the main storyline is, main characters, etc. LLM
  # generated.
  setting: UnicodeAttribute = UnicodeAttribute(null=True)
  potential_endings: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)

  # Prompt defining the narrator's personality and style.
  narrator_profile: UnicodeAttribute = UnicodeAttribute(null=True)

  # User setting defining how long (typically) the text of a node should be.
  node_text_length: NumberAttribute = NumberAttribute(null=True)

  world_image_url: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_alt_text: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_width: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_height: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_size: UnicodeAttribute = UnicodeAttribute(null=True)

  def to_dto(self) -> WorldMetaDTO:
    return WorldMetaDTO(
      id=self.id,
      title=self.title,
      description=self.description,
      genre=self.genre,
      score=self.score,
      author_id=self.author_id,
      root_node_id=self.root_node_id,
      visibility=self.visibility,
      generation_status=GenerationStatus(self.generation_status),
      world_prompt=self.world_prompt,
      setting=self.setting,
      potential_endings=self.potential_endings or [],  # type: ignore[arg-type]
      narrator_profile=self.narrator_profile,
      node_text_length=int(self.node_text_length) if self.node_text_length else None,
      world_image_url=self.world_image_url,
      world_image_alt_text=self.world_image_alt_text,
      world_image_width=self.world_image_width,
      world_image_height=self.world_image_height,
      world_image_size=self.world_image_size,
    )

  @classmethod
  def from_dto(cls, dto: WorldMetaDTO) -> WorldMeta:
    if dto.id is None:
      raise ValueError("ID is required to convert to PynamoDB entity")
    return WorldMeta(
      PK=cls.pk(dto.id),
      SK=cls.sk(),
      GSI1PK=cls.gsi1_pk(dto.author_id) if dto.author_id else None,
      GSI1SK=cls.gsi1_sk(dto.updated_at) if dto.updated_at else datetime.now(timezone.utc).isoformat(),
      id=dto.id,
      generation_status=GenerationStatus(dto.generation_status).value,
      title=dto.title,
      description=dto.description,
      genre=dto.genre,
      score=dto.score,
      author_id=dto.author_id,
      root_node_id=dto.root_node_id,
      visibility=dto.visibility,
      world_prompt=dto.world_prompt,
      setting=dto.setting,
      potential_endings=dto.potential_endings or [],  # type: ignore[arg-type]
      narrator_profile=dto.narrator_profile,
      node_text_length=dto.node_text_length,
      world_image_url=dto.world_image_url,
      world_image_alt_text=dto.world_image_alt_text,
      world_image_width=dto.world_image_width,
      world_image_height=dto.world_image_height,
      world_image_size=dto.world_image_size,
    )

  @classmethod
  def pk(cls, id: str) -> str:
    return f"WORLD#{id}"

  @classmethod
  def sk(
    cls,
  ) -> str:
    return "META"

  @classmethod
  def gsi1_pk(cls, author_id: str) -> str:
    return f"AUTHOR#{author_id}"

  @classmethod
  def gsi1_sk(cls, updated_at: str) -> str:
    return f"WORLD#{updated_at}"
