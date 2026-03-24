"""World metadata entity."""

from __future__ import annotations

from pynamodb.attributes import ListAttribute, MapAttribute, NumberAttribute, UnicodeAttribute

from app.models.dtos.world_meta import (
  CharacterDTO,
  GenerationStatus,
  LocationDTO,
  WorldMetaDTO,
  WorldVisibility,
)
from app.models.entities.base import BaseCosmonautModel
from app.utils import coerce_datetime


class Character(MapAttribute[str, UnicodeAttribute]):
  name: UnicodeAttribute = UnicodeAttribute()
  description: UnicodeAttribute = UnicodeAttribute()
  relationships: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)

  def to_dto(self) -> CharacterDTO:
    return CharacterDTO(
      name=self.name,
      description=self.description,
      relationships=[str(r) for r in self.relationships] if self.relationships else [],
    )

  @classmethod
  def from_dto(cls, dto: CharacterDTO) -> Character:
    return Character(
      name=dto.name,
      description=dto.description,
      relationships=dto.relationships or [],
    )


class Location(MapAttribute[str, UnicodeAttribute]):
  name: UnicodeAttribute = UnicodeAttribute()
  description: UnicodeAttribute = UnicodeAttribute()
  connections: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)

  def to_dto(self) -> LocationDTO:
    return LocationDTO(
      name=self.name,
      description=self.description,
      connections=[str(c) for c in self.connections] if self.connections else [],
    )

  @classmethod
  def from_dto(cls, dto: LocationDTO) -> Location:
    return Location(
      name=dto.name,
      description=dto.description,
      connections=dto.connections or [],
    )


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
  narrative_context: UnicodeAttribute = UnicodeAttribute(null=True)
  characters: ListAttribute[Character] = ListAttribute(of=Character, default=list)
  locations: ListAttribute[Location] = ListAttribute(of=Location, default=list)
  potential_endings: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)

  # Prompt defining the narrator's personality and style.
  narrator_profile: UnicodeAttribute = UnicodeAttribute(null=True)

  # User setting defining how long (typically) the text of a node should be.
  node_text_length: NumberAttribute = NumberAttribute(null=True)
  story_max_nodes: NumberAttribute = NumberAttribute(default=10)

  # World length preset ("short", "medium", "long") chosen at creation time.
  world_length: UnicodeAttribute = UnicodeAttribute(null=True)
  # When true, LLM prompts are augmented with child-safe content guidelines.
  family_friendly: UnicodeAttribute = UnicodeAttribute(default="false")

  shared_with: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)

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
      visibility=WorldVisibility(self.visibility),
      generation_status=GenerationStatus(self.generation_status),
      world_prompt=self.world_prompt,
      setting=self.setting,
      narrative_context=self.narrative_context,
      characters=[character.to_dto() for character in self.characters],
      locations=[location.to_dto() for location in self.locations],
      potential_endings=[str(e) for e in self.potential_endings] if self.potential_endings else [],
      narrator_profile=self.narrator_profile,
      node_text_length=int(self.node_text_length) if self.node_text_length else None,
      shared_with=[str(s) for s in self.shared_with] if self.shared_with else [],
      world_image_url=self.world_image_url,
      world_image_alt_text=self.world_image_alt_text,
      world_image_width=self.world_image_width,
      world_image_height=self.world_image_height,
      world_image_size=self.world_image_size,
      story_max_nodes=int(self.story_max_nodes),
      world_length=self.world_length,
      family_friendly=self.family_friendly == "true",
      created_at=self.created_at.isoformat() if self.created_at else None,
      updated_at=self.updated_at.isoformat() if self.updated_at else None,
    )

  @classmethod
  def from_dto(cls, dto: WorldMetaDTO) -> WorldMeta:
    if dto.id is None:
      raise ValueError("ID is required to convert to PynamoDB entity")
    updated_at_dt = coerce_datetime(dto.updated_at)
    return WorldMeta(
      PK=cls.pk(dto.id),
      SK=cls.sk(),
      GSI1PK=cls.gsi1_pk(dto.author_id) if dto.author_id else None,
      GSI1SK=cls.gsi1_sk(updated_at_dt.isoformat()),
      id=dto.id,
      generation_status=GenerationStatus(dto.generation_status).value,
      title=dto.title,
      description=dto.description,
      genre=dto.genre,
      score=dto.score,
      author_id=dto.author_id,
      root_node_id=dto.root_node_id,
      visibility=WorldVisibility(dto.visibility).value,
      world_prompt=dto.world_prompt,
      setting=dto.setting,
      narrative_context=dto.narrative_context,
      characters=[Character.from_dto(character) for character in dto.characters or []],
      locations=[Location.from_dto(location) for location in dto.locations or []],
      potential_endings=dto.potential_endings or [],
      narrator_profile=dto.narrator_profile,
      node_text_length=dto.node_text_length,
      story_max_nodes=dto.story_max_nodes,
      world_length=dto.world_length,
      family_friendly="true" if dto.family_friendly else "false",
      shared_with=dto.shared_with or [],
      world_image_url=dto.world_image_url,
      world_image_alt_text=dto.world_image_alt_text,
      world_image_width=dto.world_image_width,
      world_image_height=dto.world_image_height,
      world_image_size=dto.world_image_size,
      updated_at=updated_at_dt,
    )

  def _on_save(self) -> None:
    """Keep GSI1SK in sync with ``updated_at`` for chronological ordering."""
    if self.author_id and self.updated_at:
      self.GSI1SK = WorldMeta.gsi1_sk(self.updated_at.isoformat())

  def can_user_read(self, user_id: str, user_email: str | None = None) -> bool:
    if self.visibility == WorldVisibility.PUBLIC or self.author_id == user_id:
      return True
    return bool(user_email and user_email in (self.shared_with or []))

  def can_user_write(self, user_id: str) -> bool:
    return self.author_id == user_id

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
