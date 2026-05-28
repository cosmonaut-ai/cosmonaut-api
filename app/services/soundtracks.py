"""Soundtrack library service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from app.core.errors import BadRequestError, NotFoundError
from app.core.observability import logger, tracer
from app.models.dtos.soundtrack import (
  SoundtrackBulkCreateRequest,
  SoundtrackBulkCreateResponse,
  SoundtrackContentRating,
  SoundtrackCreateRequest,
  SoundtrackCreateResponse,
  SoundtrackDTO,
  SoundtrackLoopStrategy,
  SoundtrackProvider,
  SoundtrackStatus,
  SoundtrackUpdateRequest,
  SoundtrackUploadDTO,
)
from app.models.entities.soundtrack import Soundtrack
from app.services import pinecone
from app.services.s3 import cdn_url_for_key, create_presigned_upload_url, delete_file, file_exists
from app.utils import coerce_datetime
from app.utils.pagination import decode_cursor, encode_cursor

UPLOAD_EXPIRES_IN_SECONDS = 3600
UPLOAD_CACHE_CONTROL = "public, max-age=31536000, immutable"
S3_SOUNDTRACK_KEY_TEMPLATE = "soundtracks/{soundtrack_id}/source.{extension}"

_ALLOWED_CONTENT_TYPES = {
  "audio/mpeg": "mp3",
  "audio/mp3": "mp3",
  "audio/wav": "wav",
  "audio/x-wav": "wav",
}


def _normalize_content_type(content_type: str) -> str:
  normalized = content_type.strip().lower()
  if normalized not in _ALLOWED_CONTENT_TYPES:
    allowed = ", ".join(sorted(_ALLOWED_CONTENT_TYPES))
    raise BadRequestError(f"Unsupported soundtrack content type. Allowed values: {allowed}")
  return normalized


def _soundtrack_s3_key(soundtrack_id: str, content_type: str) -> str:
  extension = _ALLOWED_CONTENT_TYPES[_normalize_content_type(content_type)]
  return S3_SOUNDTRACK_KEY_TEMPLATE.format(soundtrack_id=soundtrack_id, extension=extension)


def _upload_headers(content_type: str) -> dict[str, str]:
  return {
    "Content-Type": content_type,
    "Cache-Control": UPLOAD_CACHE_CONTROL,
  }


def _description_for_search(soundtrack: Soundtrack) -> str | None:
  description = str(soundtrack.description).strip() if soundtrack.description else ""
  return description or None


def _pinecone_record_id(soundtrack: Soundtrack) -> str:
  return (
    str(soundtrack.pinecone_record_id)
    if soundtrack.pinecone_record_id
    else Soundtrack.pinecone_record_id_for(str(soundtrack.id))
  )


@tracer.capture_method
def _sync_pinecone(soundtrack: Soundtrack) -> Soundtrack:
  """Sync active soundtrack search metadata to Pinecone."""
  record_id = _pinecone_record_id(soundtrack)
  description = _description_for_search(soundtrack)

  if soundtrack.status == SoundtrackStatus.ACTIVE.value and description:
    pinecone.upsert_records(
      [
        pinecone.PineconeRecord.model_validate(
          {
            "id": record_id,
            "text": description,
            "entity_type": pinecone.EntityType.SOUNDTRACK,
            "soundtrack_id": str(soundtrack.id),
            "status": str(soundtrack.status),
            "content_rating": str(soundtrack.content_rating),
            "title": str(soundtrack.title) if soundtrack.title else "",
            "audio_url": str(soundtrack.audio_url) if soundtrack.audio_url else "",
          }
        )
      ]
    )
    soundtrack.pinecone_record_id = record_id
    soundtrack.pinecone_upserted_at = datetime.now(UTC)
    soundtrack.save()
    return soundtrack

  if soundtrack.pinecone_record_id and soundtrack.pinecone_upserted_at:
    pinecone.delete_records(ids=[str(soundtrack.pinecone_record_id)])
    soundtrack.pinecone_upserted_at = None
    soundtrack.save()
  return soundtrack


def _delete_pinecone_record(soundtrack: Soundtrack) -> None:
  if soundtrack.pinecone_record_id and soundtrack.pinecone_upserted_at:
    pinecone.delete_records(ids=[str(soundtrack.pinecone_record_id)])


def _validate_active_soundtrack(soundtrack: Soundtrack) -> None:
  if soundtrack.status != SoundtrackStatus.ACTIVE.value:
    return
  if not _description_for_search(soundtrack):
    raise BadRequestError("Active soundtracks require a description for vector search")
  if not soundtrack.audio_url or not soundtrack.s3_key:
    raise BadRequestError("Active soundtracks require an uploaded audio asset")
  if not file_exists(str(soundtrack.s3_key)):
    raise BadRequestError("Soundtrack audio upload must complete before activation")


def get_soundtrack_entity(soundtrack_id: str) -> Soundtrack:
  """Fetch a soundtrack entity by ID."""
  try:
    return Soundtrack.get(Soundtrack.pk(soundtrack_id), Soundtrack.sk())
  except Soundtrack.DoesNotExist as exc:
    raise NotFoundError(f"Soundtrack not found: {soundtrack_id}") from exc


@tracer.capture_method
def create_soundtrack(payload: SoundtrackCreateRequest, *, created_by: str) -> SoundtrackCreateResponse:
  """Create a draft soundtrack record and return a presigned upload target."""
  soundtrack_id = str(uuid4())
  content_type = _normalize_content_type(payload.content_type)
  s3_key = _soundtrack_s3_key(soundtrack_id, content_type)
  upload_url = create_presigned_upload_url(
    key=s3_key,
    content_type=content_type,
    cache_control=UPLOAD_CACHE_CONTROL,
    expires_in_seconds=UPLOAD_EXPIRES_IN_SECONDS,
  )

  generated_at = coerce_datetime(payload.generated_at) if payload.generated_at else None
  soundtrack = Soundtrack(
    PK=Soundtrack.pk(soundtrack_id),
    SK=Soundtrack.sk(),
    id=soundtrack_id,
    status=SoundtrackStatus.DRAFT.value,
    title=payload.title,
    description=payload.description,
    prompt=payload.prompt,
    audio_url=cdn_url_for_key(s3_key),
    s3_key=s3_key,
    file_size_bytes=payload.file_size_bytes,
    duration_seconds=payload.duration_seconds,
    content_type=content_type,
    content_rating=SoundtrackContentRating(payload.content_rating).value,
    loop_strategy=SoundtrackLoopStrategy(payload.loop_strategy).value,
    provider=SoundtrackProvider(payload.provider).value,
    provider_track_id=payload.provider_track_id,
    generated_at=generated_at,
    quality_score=payload.quality_score,
    curation_notes=payload.curation_notes,
    created_by=created_by,
  )
  soundtrack.save()

  return SoundtrackCreateResponse(
    soundtrack=soundtrack.to_dto(),
    upload=SoundtrackUploadDTO(
      upload_url=upload_url,
      headers=_upload_headers(content_type),
      expires_in_seconds=UPLOAD_EXPIRES_IN_SECONDS,
    ),
  )


@tracer.capture_method
def create_soundtracks_bulk(payload: SoundtrackBulkCreateRequest, *, created_by: str) -> SoundtrackBulkCreateResponse:
  """Create multiple draft soundtrack records and upload targets."""
  for item in payload.items:
    _normalize_content_type(item.content_type)

  return SoundtrackBulkCreateResponse(
    items=[create_soundtrack(item, created_by=created_by) for item in payload.items],
  )


@tracer.capture_method
def list_soundtracks(
  *,
  status: SoundtrackStatus | None = None,
  limit: int,
  cursor: str | None,
) -> tuple[list[SoundtrackDTO], str | None]:
  """List soundtrack library items."""
  range_key_condition = None
  if status is not None:
    range_key_condition = Soundtrack.GSI1SK.startswith(f"STATUS#{status.value}#CREATED#")

  results = Soundtrack.GSI1.query(
    hash_key=Soundtrack.gsi1_pk_library(),
    range_key_condition=range_key_condition,
    scan_index_forward=False,
    page_size=limit,
    limit=limit,
    last_evaluated_key=decode_cursor(cursor),
  )
  soundtracks = cast(list[Soundtrack], list(results))
  return [soundtrack.to_dto() for soundtrack in soundtracks], encode_cursor(results.last_evaluated_key)


@tracer.capture_method
def get_soundtrack(soundtrack_id: str) -> SoundtrackDTO:
  """Fetch a soundtrack by ID."""
  return get_soundtrack_entity(soundtrack_id).to_dto()


def _set_optional_attr(soundtrack: Soundtrack, field_name: str, value: object) -> None:
  if field_name == "status" and value is not None:
    soundtrack.status = SoundtrackStatus(value).value
  elif field_name == "content_rating" and value is not None:
    soundtrack.content_rating = SoundtrackContentRating(value).value
  elif field_name == "loop_strategy" and value is not None:
    soundtrack.loop_strategy = SoundtrackLoopStrategy(value).value
  elif field_name == "provider" and value is not None:
    soundtrack.provider = SoundtrackProvider(value).value
  elif field_name == "generated_at":
    generated_at = value if isinstance(value, str) else None
    soundtrack.generated_at = coerce_datetime(generated_at) if generated_at else None
  elif field_name == "content_type" and value is not None:
    soundtrack.content_type = _normalize_content_type(str(value))
  else:
    setattr(soundtrack, field_name, value)


@tracer.capture_method
def update_soundtrack(soundtrack_id: str, payload: SoundtrackUpdateRequest, *, reviewed_by: str) -> SoundtrackDTO:
  """Partially update a soundtrack and sync Pinecone if search metadata changed."""
  soundtrack = get_soundtrack_entity(soundtrack_id)
  fields = payload.model_fields_set
  if not fields:
    return soundtrack.to_dto()

  sync_fields = {"status", "description", "content_rating", "title"}
  review_fields = {"status", "quality_score", "curation_notes"}
  updatable_fields = {
    "status",
    "title",
    "description",
    "prompt",
    "file_size_bytes",
    "duration_seconds",
    "content_type",
    "content_rating",
    "loop_strategy",
    "provider",
    "provider_track_id",
    "generated_at",
    "quality_score",
    "curation_notes",
  }

  for field_name in fields:
    if field_name not in updatable_fields:
      continue
    _set_optional_attr(soundtrack, field_name, getattr(payload, field_name))

  if fields & review_fields:
    soundtrack.reviewed_by = reviewed_by
    soundtrack.reviewed_at = datetime.now(UTC)

  _validate_active_soundtrack(soundtrack)
  soundtrack.save()

  if fields & sync_fields:
    soundtrack = _sync_pinecone(soundtrack)

  return soundtrack.to_dto()


@tracer.capture_method
def delete_soundtrack(soundtrack_id: str) -> None:
  """Delete a soundtrack, its search record, and its S3 asset."""
  soundtrack = get_soundtrack_entity(soundtrack_id)
  _delete_pinecone_record(soundtrack)
  if soundtrack.s3_key:
    delete_file(str(soundtrack.s3_key))
  soundtrack.delete()
  logger.info("Deleted soundtrack %s", soundtrack_id)
