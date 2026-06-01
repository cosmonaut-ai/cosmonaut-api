"""Soundtrack playlist entity."""

from __future__ import annotations

from pynamodb.attributes import ListAttribute, MapAttribute, NumberAttribute, UnicodeAttribute, UTCDateTimeAttribute

from app.models.dtos.playlist import PlaylistDTO, PlaylistTrackDTO
from app.models.entities.base import BaseCosmonautModel


class PlaylistTrack(MapAttribute[str, UnicodeAttribute]):
  """Denormalized snapshot of a soundtrack within a playlist."""

  soundtrack_id: UnicodeAttribute = UnicodeAttribute()
  title: UnicodeAttribute = UnicodeAttribute(null=True)
  description: UnicodeAttribute = UnicodeAttribute(null=True)
  audio_url: UnicodeAttribute = UnicodeAttribute(null=True)
  duration_seconds: NumberAttribute = NumberAttribute(null=True)
  content_rating: UnicodeAttribute = UnicodeAttribute(null=True)
  loop_strategy: UnicodeAttribute = UnicodeAttribute(null=True)

  def to_dto(self) -> PlaylistTrackDTO:
    return PlaylistTrackDTO(
      soundtrack_id=str(self.soundtrack_id),
      title=str(self.title) if self.title else None,
      description=str(self.description) if self.description else None,
      audio_url=str(self.audio_url) if self.audio_url else None,
      duration_seconds=float(self.duration_seconds) if self.duration_seconds is not None else None,
      content_rating=str(self.content_rating) if self.content_rating else None,
      loop_strategy=str(self.loop_strategy) if self.loop_strategy else None,
    )


class Playlist(BaseCosmonautModel):
  """Soundtrack playlist with denormalized track data.

  Single-table design:
    PK = PLAYLIST#{id}
    SK = META
  """

  id: UnicodeAttribute = UnicodeAttribute()
  description: UnicodeAttribute = UnicodeAttribute(null=True)
  tracks: ListAttribute[PlaylistTrack] = ListAttribute(of=PlaylistTrack, default=list)
  generated_at: UTCDateTimeAttribute = UTCDateTimeAttribute(null=True)

  def to_dto(self) -> PlaylistDTO:
    return PlaylistDTO(
      id=str(self.id),
      description=str(self.description) if self.description else None,
      tracks=[track.to_dto() for track in self.tracks],
      generated_at=self.generated_at.isoformat() if self.generated_at else None,
      created_at=self.created_at.isoformat() if self.created_at else None,
    )

  @classmethod
  def pk(cls, id: str) -> str:
    return f"PLAYLIST#{id}"

  @classmethod
  def sk(cls) -> str:
    return "META"
