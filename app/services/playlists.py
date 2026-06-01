"""Playlist generation and retrieval service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from pynamodb.exceptions import GetError

from app.core.errors import NotFoundError
from app.core.observability import logger, tracer
from app.models.dtos.playlist import PlaylistDTO
from app.models.entities.playlist import Playlist, PlaylistTrack
from app.models.entities.soundtrack import Soundtrack
from app.services import pinecone

PLAYLIST_SIZE = 5
PINECONE_FETCH_MULTIPLIER = 2

CONTENT_FILTER_ALLOWED_RATINGS: dict[str, list[str]] = {
  "strict": ["child"],
  "moderate": ["child", "teen"],
  "none": ["child", "teen", "adult"],
}


@tracer.capture_method
def generate_playlist(
  soundtrack_description: str,
  content_filter: str,
) -> Playlist | None:
  """Search for matching soundtracks and create a Playlist entity.

  Returns None if no eligible tracks are found.
  """
  allowed_ratings = CONTENT_FILTER_ALLOWED_RATINGS.get(content_filter, ["child", "teen", "adult"])

  hits = cast(
    list[Any],
    pinecone.search_records(
      query=soundtrack_description,
      top_k=PLAYLIST_SIZE * PINECONE_FETCH_MULTIPLIER,
      filter={
        "entity_type": {"$eq": pinecone.EntityType.SOUNDTRACK},
        "status": {"$eq": "active"},
        "content_rating": {"$in": allowed_ratings},
      },
    ).result.hits,
  )

  if not hits:
    logger.info("No eligible soundtracks found for playlist generation")
    return None

  soundtrack_ids: list[str] = []
  for hit in hits:
    sid = hit.fields.get("soundtrack_id") if hasattr(hit, "fields") else None
    if sid and sid not in soundtrack_ids:
      soundtrack_ids.append(str(sid))
    if len(soundtrack_ids) >= PLAYLIST_SIZE:
      break

  if not soundtrack_ids:
    logger.info("Pinecone hits contained no usable soundtrack_id fields")
    return None

  soundtracks = list(Soundtrack.batch_get([(Soundtrack.pk(sid), Soundtrack.sk()) for sid in soundtrack_ids]))

  if not soundtracks:
    logger.warning("batch_get returned no Soundtrack entities for IDs: %s", soundtrack_ids)
    return None

  id_to_soundtrack = {str(s.id): s for s in soundtracks}
  ordered_tracks = [id_to_soundtrack[sid] for sid in soundtrack_ids if sid in id_to_soundtrack]

  playlist_id = str(uuid4())
  now = datetime.now(UTC)

  playlist = Playlist(
    PK=Playlist.pk(playlist_id),
    SK=Playlist.sk(),
    id=playlist_id,
    description=soundtrack_description,
    generated_at=now,
    tracks=[
      PlaylistTrack(
        soundtrack_id=str(s.id),
        title=str(s.title) if s.title else None,
        description=str(s.description) if s.description else None,
        audio_url=str(s.audio_url) if s.audio_url else None,
        duration_seconds=float(s.duration_seconds) if s.duration_seconds is not None else None,
        content_rating=str(s.content_rating) if s.content_rating else None,
        loop_strategy=str(s.loop_strategy) if s.loop_strategy else None,
      )
      for s in ordered_tracks
    ],
  )
  playlist.save()

  logger.info(
    "Created playlist %s with %d tracks",
    playlist_id,
    len(playlist.tracks),
  )
  return playlist


@tracer.capture_method
def get_playlist(playlist_id: str) -> Playlist:
  """Fetch a playlist by ID. Raises NotFoundError if not found."""
  try:
    return Playlist.get(Playlist.pk(playlist_id), Playlist.sk())
  except (Playlist.DoesNotExist, GetError) as exc:
    raise NotFoundError(f"Playlist {playlist_id} not found") from exc


def get_playlist_dto(playlist_id: str) -> PlaylistDTO:
  """Fetch a playlist and return its public DTO."""
  return get_playlist(playlist_id).to_dto()
