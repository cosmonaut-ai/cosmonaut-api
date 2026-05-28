"""Admin-only audio library endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status

from app.core.security import User, get_current_user
from app.models.dtos.base import PaginatedResponse
from app.models.dtos.soundtrack import (
  SoundtrackBulkCreateRequest,
  SoundtrackBulkCreateResponse,
  SoundtrackCreateRequest,
  SoundtrackCreateResponse,
  SoundtrackDTO,
  SoundtrackStatus,
  SoundtrackUpdateRequest,
)
from app.services import soundtracks as soundtrack_service

router = APIRouter(prefix="/admin/audio", tags=["admin-audio"])


@router.post(
  "/soundtracks",
  response_model=SoundtrackCreateResponse,
  status_code=status.HTTP_201_CREATED,
  summary="Create a draft soundtrack and upload URL (admin)",
)
async def admin_create_soundtrack(
  payload: SoundtrackCreateRequest,
  current_user: User = Depends(get_current_user),
) -> SoundtrackCreateResponse:
  """Create a draft soundtrack record and return a presigned S3 upload target."""
  return soundtrack_service.create_soundtrack(payload, created_by=current_user.id)


@router.post(
  "/soundtracks/bulk",
  response_model=SoundtrackBulkCreateResponse,
  status_code=status.HTTP_201_CREATED,
  summary="Create draft soundtrack upload targets in bulk (admin)",
)
async def admin_create_soundtracks_bulk(
  payload: SoundtrackBulkCreateRequest,
  current_user: User = Depends(get_current_user),
) -> SoundtrackBulkCreateResponse:
  """Create multiple draft soundtrack records and presigned S3 upload targets."""
  return soundtrack_service.create_soundtracks_bulk(payload, created_by=current_user.id)


@router.get(
  "/soundtracks",
  response_model=PaginatedResponse[SoundtrackDTO],
  summary="List soundtracks (admin)",
)
async def admin_list_soundtracks(
  status_filter: SoundtrackStatus | None = Query(default=None, alias="status"),
  limit: int = Query(50, ge=1, le=200, description="Maximum number of soundtracks to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[SoundtrackDTO]:
  """List soundtrack library items."""
  soundtracks, next_cursor = soundtrack_service.list_soundtracks(
    status=status_filter,
    limit=limit,
    cursor=cursor,
  )
  return PaginatedResponse(items=soundtracks, next_cursor=next_cursor)


@router.get(
  "/soundtracks/{soundtrack_id}",
  response_model=SoundtrackDTO,
  summary="Get a soundtrack (admin)",
)
async def admin_get_soundtrack(
  soundtrack_id: str = Path(..., description="Soundtrack ID to fetch"),
) -> SoundtrackDTO:
  """Fetch a single soundtrack library item."""
  return soundtrack_service.get_soundtrack(soundtrack_id)


@router.patch(
  "/soundtracks/{soundtrack_id}",
  response_model=SoundtrackDTO,
  summary="Update a soundtrack (admin)",
)
async def admin_update_soundtrack(
  payload: SoundtrackUpdateRequest,
  soundtrack_id: str = Path(..., description="Soundtrack ID to update"),
  current_user: User = Depends(get_current_user),
) -> SoundtrackDTO:
  """Update soundtrack metadata, syncing Pinecone when search metadata changes."""
  return soundtrack_service.update_soundtrack(soundtrack_id, payload, reviewed_by=current_user.id)


@router.delete(
  "/soundtracks/{soundtrack_id}",
  status_code=status.HTTP_204_NO_CONTENT,
  summary="Delete a soundtrack (admin)",
)
async def admin_delete_soundtrack(
  soundtrack_id: str = Path(..., description="Soundtrack ID to delete"),
) -> None:
  """Delete a soundtrack, its Pinecone search record, and its S3 asset."""
  soundtrack_service.delete_soundtrack(soundtrack_id)
