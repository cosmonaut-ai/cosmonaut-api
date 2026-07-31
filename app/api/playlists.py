"""Playlists controller -- read-only access to soundtrack playlists."""

from __future__ import annotations

from fastapi import APIRouter

from app.models.dtos.playlist import PlaylistDTO
from app.services.playlists import get_playlist_dto

router = APIRouter(prefix="/playlists", tags=["playlists"])


@router.get(
  "/{playlist_id}",
  response_model=PlaylistDTO,
  summary="Get a soundtrack playlist by ID",
)
async def get_playlist(playlist_id: str) -> PlaylistDTO:
  """Retrieve a playlist with all denormalized track data."""
  return get_playlist_dto(playlist_id)
