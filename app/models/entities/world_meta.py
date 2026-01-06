"""World metadata entity."""

from __future__ import annotations

from pynamodb.attributes import ListAttribute, MapAttribute, NumberAttribute, UnicodeAttribute

from app.models.dtos.world_meta import CharacterDTO, GenerationStatus, LocationDTO, WorldMetaDTO
from app.models.entities.base import BaseCosmonautModel
from app.services import llm
from app.utils import coerce_datetime


class Character(MapAttribute):  # type: ignore[type-arg]
  name: UnicodeAttribute = UnicodeAttribute()
  description: UnicodeAttribute = UnicodeAttribute()
  relationships: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)

  def to_dto(self) -> CharacterDTO:
    return CharacterDTO(
      name=self.name,
      description=self.description,
      relationships=self.relationships or [],  # type: ignore[arg-type]
    )

  @classmethod
  def from_dto(cls, dto: CharacterDTO) -> Character:
    return Character(
      name=dto.name,
      description=dto.description,
      relationships=dto.relationships or [],  # type: ignore[arg-type]
    )


class Location(MapAttribute):  # type: ignore[type-arg]
  name: UnicodeAttribute = UnicodeAttribute()
  description: UnicodeAttribute = UnicodeAttribute()
  connections: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)

  def to_dto(self) -> LocationDTO:
    return LocationDTO(
      name=self.name,
      description=self.description,
      connections=self.connections or [],  # type: ignore[arg-type]
    )

  @classmethod
  def from_dto(cls, dto: LocationDTO) -> Location:
    return Location(
      name=dto.name,
      description=dto.description,
      connections=dto.connections or [],  # type: ignore[arg-type]
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
      narrative_context=self.narrative_context,
      characters=[character.to_dto() for character in self.characters],
      locations=[location.to_dto() for location in self.locations],
      potential_endings=self.potential_endings or [],  # type: ignore[arg-type]
      narrator_profile=self.narrator_profile,
      node_text_length=int(self.node_text_length) if self.node_text_length else None,
      world_image_url=self.world_image_url,
      world_image_alt_text=self.world_image_alt_text,
      world_image_width=self.world_image_width,
      world_image_height=self.world_image_height,
      world_image_size=self.world_image_size,
      story_max_nodes=int(self.story_max_nodes),
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
      visibility=dto.visibility,
      world_prompt=dto.world_prompt,
      setting=dto.setting,
      narrative_context=dto.narrative_context,
      characters=[Character.from_dto(character) for character in dto.characters or []],
      locations=[Location.from_dto(location) for location in dto.locations or []],
      potential_endings=dto.potential_endings or [],  # type: ignore[arg-type]
      narrator_profile=dto.narrator_profile,
      node_text_length=dto.node_text_length,
      world_image_url=dto.world_image_url,
      world_image_alt_text=dto.world_image_alt_text,
      world_image_width=dto.world_image_width,
      world_image_height=dto.world_image_height,
      world_image_size=dto.world_image_size,
      updated_at=updated_at_dt,
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

  def to_llm_world_info(self) -> llm.LLMWorldInfo:
    return llm.LLMWorldInfo(
      world_title=self.title or "",
      world_description=self.description or "",
      setting=self.setting or "",
      narrative_context=self.narrative_context or "",
      potential_endings=self.potential_endings or [],  # type: ignore[arg-type]
      characters=[
        llm.LLMCharacter(
          name=character.name or "",
          description=character.description or "",
          relationships=character.relationships or [],  # type: ignore[arg-type]
        )
        for character in self.characters
      ],
      locations=[
        llm.LLMLocation(
          name=location.name or "",
          description=location.description or "",
          connections=location.connections or [],  # type: ignore[arg-type]
        )
        for location in self.locations
      ],
      world_genre=self.genre or "",
    )
