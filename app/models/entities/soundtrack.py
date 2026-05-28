"""Soundtrack library entity."""

from __future__ import annotations

from pynamodb.attributes import NumberAttribute, UnicodeAttribute, UTCDateTimeAttribute

from app.models.dtos.soundtrack import (
  SoundtrackContentRating,
  SoundtrackDTO,
  SoundtrackLoopStrategy,
  SoundtrackProvider,
  SoundtrackStatus,
)
from app.models.entities.base import BaseCosmonautModel
from app.utils import coerce_datetime


class Soundtrack(BaseCosmonautModel):
  """Reusable ambient soundtrack asset for world assignment.

  Single-table design:
    PK = SOUNDTRACK#{id}
    SK = META

  GSI1 lists the shared soundtrack library by lifecycle status:
    GSI1PK = SOUNDTRACK_LIBRARY
    GSI1SK = STATUS#{status}#CREATED#{created_at}#{id}
  """

  GSI1PK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI1PK", null=True)
  GSI1SK: UnicodeAttribute = UnicodeAttribute(attr_name="GSI1SK", null=True)

  id: UnicodeAttribute = UnicodeAttribute()
  status: UnicodeAttribute = UnicodeAttribute(default=SoundtrackStatus.DRAFT.value)

  title: UnicodeAttribute = UnicodeAttribute(null=True)
  description: UnicodeAttribute = UnicodeAttribute(null=True)
  prompt: UnicodeAttribute = UnicodeAttribute(null=True)

  audio_url: UnicodeAttribute = UnicodeAttribute(null=True)
  s3_key: UnicodeAttribute = UnicodeAttribute(null=True)
  file_size_bytes: NumberAttribute = NumberAttribute(null=True)
  duration_seconds: NumberAttribute = NumberAttribute(null=True)
  content_type: UnicodeAttribute = UnicodeAttribute(null=True)

  content_rating: UnicodeAttribute = UnicodeAttribute(default=SoundtrackContentRating.ADULT.value)
  loop_strategy: UnicodeAttribute = UnicodeAttribute(default=SoundtrackLoopStrategy.CROSSFADE.value)

  provider: UnicodeAttribute = UnicodeAttribute(default=SoundtrackProvider.SUNO.value)
  provider_track_id: UnicodeAttribute = UnicodeAttribute(null=True)
  generated_at: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

  pinecone_record_id: UnicodeAttribute = UnicodeAttribute(null=True)
  pinecone_upserted_at: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

  quality_score: NumberAttribute = NumberAttribute(null=True)
  curation_notes: UnicodeAttribute = UnicodeAttribute(null=True)
  created_by: UnicodeAttribute = UnicodeAttribute(null=True)
  reviewed_by: UnicodeAttribute = UnicodeAttribute(null=True)
  reviewed_at: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

  def to_dto(self) -> SoundtrackDTO:
    return SoundtrackDTO(
      id=self.id,
      status=SoundtrackStatus(self.status),
      title=self.title,
      description=self.description,
      prompt=self.prompt,
      audio_url=self.audio_url,
      s3_key=self.s3_key,
      file_size_bytes=int(self.file_size_bytes) if self.file_size_bytes is not None else None,
      duration_seconds=float(self.duration_seconds) if self.duration_seconds is not None else None,
      content_type=self.content_type,
      content_rating=SoundtrackContentRating(self.content_rating),
      loop_strategy=SoundtrackLoopStrategy(self.loop_strategy),
      provider=SoundtrackProvider(self.provider),
      provider_track_id=self.provider_track_id,
      generated_at=self.generated_at.isoformat() if self.generated_at else None,
      pinecone_record_id=self.pinecone_record_id,
      pinecone_upserted_at=self.pinecone_upserted_at.isoformat() if self.pinecone_upserted_at else None,
      quality_score=float(self.quality_score) if self.quality_score is not None else None,
      curation_notes=self.curation_notes,
      created_by=self.created_by,
      reviewed_by=self.reviewed_by,
      reviewed_at=self.reviewed_at.isoformat() if self.reviewed_at else None,
      created_at=self.created_at.isoformat() if self.created_at else None,
      updated_at=self.updated_at.isoformat() if self.updated_at else None,
    )

  @classmethod
  def from_dto(cls, dto: SoundtrackDTO) -> Soundtrack:
    if dto.id is None:
      raise ValueError("ID is required to convert to PynamoDB entity")

    generated_at = coerce_datetime(dto.generated_at) if dto.generated_at else None
    pinecone_upserted_at = coerce_datetime(dto.pinecone_upserted_at) if dto.pinecone_upserted_at else None
    reviewed_at = coerce_datetime(dto.reviewed_at) if dto.reviewed_at else None

    return Soundtrack(
      PK=cls.pk(dto.id),
      SK=cls.sk(),
      id=dto.id,
      status=SoundtrackStatus(dto.status).value,
      title=dto.title,
      description=dto.description,
      prompt=dto.prompt,
      audio_url=dto.audio_url,
      s3_key=dto.s3_key,
      file_size_bytes=dto.file_size_bytes,
      duration_seconds=dto.duration_seconds,
      content_type=dto.content_type,
      content_rating=SoundtrackContentRating(dto.content_rating).value,
      loop_strategy=SoundtrackLoopStrategy(dto.loop_strategy).value,
      provider=SoundtrackProvider(dto.provider).value,
      provider_track_id=dto.provider_track_id,
      generated_at=generated_at,
      pinecone_record_id=dto.pinecone_record_id,
      pinecone_upserted_at=pinecone_upserted_at,
      quality_score=dto.quality_score,
      curation_notes=dto.curation_notes,
      created_by=dto.created_by,
      reviewed_by=dto.reviewed_by,
      reviewed_at=reviewed_at,
    )

  def _on_save(self) -> None:
    """Keep library listing GSI keys in sync with status and creation time."""
    if self.created_at:
      self.GSI1PK = Soundtrack.gsi1_pk_library()
      self.GSI1SK = Soundtrack.gsi1_sk_status_created(
        status=str(self.status),
        created_at=self.created_at.isoformat(),
        soundtrack_id=str(self.id),
      )

  @classmethod
  def pk(cls, id: str) -> str:
    return f"SOUNDTRACK#{id}"

  @classmethod
  def sk(cls) -> str:
    return "META"

  @classmethod
  def gsi1_pk_library(cls) -> str:
    return "SOUNDTRACK_LIBRARY"

  @classmethod
  def gsi1_sk_status_created(cls, status: str, created_at: str, soundtrack_id: str) -> str:
    return f"STATUS#{status}#CREATED#{created_at}#{soundtrack_id}"

  @classmethod
  def pinecone_record_id_for(cls, id: str) -> str:
    return f"soundtrack:{id}"
