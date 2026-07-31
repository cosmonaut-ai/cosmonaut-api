"""World metadata entity."""

from __future__ import annotations

from pynamodb.attributes import ListAttribute, MapAttribute, NumberAttribute, UnicodeAttribute
from pynamodb.indexes import AllProjection, GlobalSecondaryIndex, KeysOnlyProjection

from app.models.dtos.world_meta import (
  CharacterDTO,
  ContentFilter,
  ImageGenerationStatus,
  LocationDTO,
  VocabLevel,
  WorldGenerationStatus,
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


class WorldMetaGSI2Model(GlobalSecondaryIndex["WorldMeta"]):
  """Sparse GSI for featured world discovery (GSI2PK = 'FEATURED')."""

  GSI2PK: UnicodeAttribute = UnicodeAttribute(hash_key=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(range_key=True)

  class Meta:
    projection = AllProjection()


class WorldMetaGSI3Model(GlobalSecondaryIndex["WorldMeta"]):
  """Keys-only GSI for the admin recent-world directory."""

  GSI3PK: UnicodeAttribute = UnicodeAttribute(hash_key=True)
  GSI3SK: UnicodeAttribute = UnicodeAttribute(range_key=True)

  class Meta:
    projection = KeysOnlyProjection()


class WorldMeta(BaseCosmonautModel):
  """Container for world-level metadata."""

  GSI1PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI1PK", null=True)
  GSI1SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI1SK", null=True)

  GSI2: WorldMetaGSI2Model = WorldMetaGSI2Model()
  GSI2PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2PK", null=True)
  GSI2SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI2SK", null=True)
  featured_order: NumberAttribute = NumberAttribute(null=True)

  GSI3: WorldMetaGSI3Model = WorldMetaGSI3Model()
  GSI3PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI3PK", null=True)
  GSI3SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI3SK", null=True)

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
  # Vocabulary complexity: "child", "teen", or "adult".
  vocab_level: UnicodeAttribute = UnicodeAttribute(default=VocabLevel.ADULT.value)
  # Content filter strictness: "none", "moderate", or "strict".
  content_filter: UnicodeAttribute = UnicodeAttribute(default=ContentFilter.NONE.value)
  # Optional fixed choice count per node (e.g. 2 for YT Shorts).
  max_choices: NumberAttribute = NumberAttribute(null=True)

  shared_with: ListAttribute[UnicodeAttribute] = ListAttribute(of=UnicodeAttribute, default=list)

  world_image_url: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_alt_text: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_width: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_height: UnicodeAttribute = UnicodeAttribute(null=True)
  world_image_size: UnicodeAttribute = UnicodeAttribute(null=True)
  image_generation_status: UnicodeAttribute = UnicodeAttribute(null=True)

  default_playlist_id: UnicodeAttribute = UnicodeAttribute(null=True)

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
      generation_status=WorldGenerationStatus(self.generation_status),
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
      image_generation_status=ImageGenerationStatus(self.image_generation_status)
      if self.image_generation_status
      else None,
      story_max_nodes=int(self.story_max_nodes),
      world_length=self.world_length,
      vocab_level=self.vocab_level,
      content_filter=self.content_filter,
      max_choices=int(self.max_choices) if self.max_choices else None,
      featured_order=int(self.featured_order) if self.featured_order is not None else None,
      default_playlist_id=self.default_playlist_id,
      created_at=self.created_at.isoformat() if self.created_at else None,
      updated_at=self.updated_at.isoformat() if self.updated_at else None,
    )

  @classmethod
  def from_dto(cls, dto: WorldMetaDTO) -> WorldMeta:
    if dto.id is None:
      raise ValueError("ID is required to convert to PynamoDB entity")
    created_at_dt = coerce_datetime(dto.created_at) if dto.created_at else None
    updated_at_dt = coerce_datetime(dto.updated_at)
    return WorldMeta(
      PK=cls.pk(dto.id),
      SK=cls.sk(),
      GSI1PK=cls.gsi1_pk(dto.author_id) if dto.author_id else None,
      GSI1SK=cls.gsi1_sk(updated_at_dt.isoformat()),
      GSI3PK=cls.gsi3_pk_world_directory() if created_at_dt else None,
      GSI3SK=cls.gsi3_sk_created(created_at_dt.isoformat(), dto.id) if created_at_dt else None,
      id=dto.id,
      generation_status=WorldGenerationStatus(dto.generation_status).value,
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
      vocab_level=dto.vocab_level or VocabLevel.ADULT.value,
      content_filter=dto.content_filter or ContentFilter.NONE.value,
      max_choices=dto.max_choices,
      shared_with=dto.shared_with or [],
      world_image_url=dto.world_image_url,
      world_image_alt_text=dto.world_image_alt_text,
      world_image_width=dto.world_image_width,
      world_image_height=dto.world_image_height,
      world_image_size=dto.world_image_size,
      image_generation_status=ImageGenerationStatus(dto.image_generation_status).value
      if dto.image_generation_status
      else None,
      default_playlist_id=dto.default_playlist_id,
      featured_order=dto.featured_order,
      GSI2PK=cls.gsi2_pk_featured() if dto.featured_order is not None else None,
      GSI2SK=cls.gsi2_sk_order(dto.featured_order) if dto.featured_order is not None else None,
      created_at=created_at_dt,
      updated_at=updated_at_dt,
    )

  def _on_save(self) -> None:
    """Keep GSI sort keys in sync with their source fields."""
    if self.author_id and self.updated_at:
      self.GSI1SK = WorldMeta.gsi1_sk(self.updated_at.isoformat())
    if self.featured_order is not None:
      self.GSI2SK = WorldMeta.gsi2_sk_order(int(self.featured_order))
    if self.created_at:
      self.GSI3PK = WorldMeta.gsi3_pk_world_directory()
      self.GSI3SK = WorldMeta.gsi3_sk_created(self.created_at.isoformat(), str(self.id))

  def can_user_read(self, user_id: str) -> bool:
    if self.visibility in (WorldVisibility.PUBLIC, WorldVisibility.UNLISTED) or self.author_id == user_id:
      return True
    return user_id in (self.shared_with or [])

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

  @classmethod
  def gsi2_pk_featured(cls) -> str:
    return "FEATURED"

  @classmethod
  def gsi2_sk_order(cls, order: int) -> str:
    return f"ORDER#{str(order).zfill(5)}"

  @classmethod
  def gsi3_pk_world_directory(cls) -> str:
    return "WORLDS"

  @classmethod
  def gsi3_sk_created(cls, created_at: str, world_id: str) -> str:
    return f"CREATED#{created_at}#{world_id}"
