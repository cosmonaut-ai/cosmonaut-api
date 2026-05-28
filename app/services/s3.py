"""S3 upload service for static content (images, audio, etc.).

Provides a lazy-initialized S3 client singleton and helpers for uploading
file bytes to the configured bucket, returning the public CDN URL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.observability import logger, tracer

if TYPE_CHECKING:
  from mypy_boto3_s3.client import S3Client

s3: S3Client | None = None


def get_s3_client() -> S3Client:
  global s3
  if s3 is None:
    import boto3

    s3 = boto3.client("s3", region_name=settings.AWS_REGION)
  return s3


@tracer.capture_method
def upload_file(
  key: str, data: bytes, content_type: str, cache_control: str = "public, max-age=31536000, immutable"
) -> str:
  """Upload bytes to S3 and return the public CDN URL.

  Args:
    key: The S3 object key (e.g. ``worlds/{world_id}/cover.png``).
    data: Raw file data.
    content_type: MIME type for the object (e.g. ``image/png``, ``audio/mpeg``).
    cache_control: Cache-Control header value. Defaults to long-lived immutable.

  Returns:
    The full CDN URL for the uploaded file.
  """
  get_s3_client().put_object(
    Bucket=settings.STATIC_CONTENT_S3_BUCKET,
    Key=key,
    Body=data,
    ContentType=content_type,
    CacheControl=cache_control,
  )
  logger.info(f"Uploaded file to s3://{settings.STATIC_CONTENT_S3_BUCKET}/{key}")
  return cdn_url_for_key(key)


def cdn_url_for_key(key: str) -> str:
  """Return the public CDN URL for a static-content S3 key."""
  return f"https://{settings.STATIC_CONTENT_CDN_DOMAIN}/{key}"


def create_presigned_upload_url(
  key: str,
  content_type: str,
  *,
  cache_control: str = "public, max-age=31536000, immutable",
  expires_in_seconds: int = 3600,
) -> str:
  """Create a presigned PUT URL for direct S3 upload."""
  return str(
    get_s3_client().generate_presigned_url(
      "put_object",
      Params={
        "Bucket": settings.STATIC_CONTENT_S3_BUCKET,
        "Key": key,
        "ContentType": content_type,
        "CacheControl": cache_control,
      },
      ExpiresIn=expires_in_seconds,
    )
  )


def upload_image(key: str, image_bytes: bytes, content_type: str = "image/png") -> str:
  """Upload image bytes to S3 and return the public CDN URL.

  Convenience wrapper around :func:`upload_file` for images.

  Args:
    key: The S3 object key (e.g. ``worlds/{world_id}/cover.png``).
    image_bytes: Raw image data.
    content_type: MIME type for the object. Defaults to ``image/png``.

  Returns:
    The full CDN URL for the uploaded image.
  """
  return upload_file(key=key, data=image_bytes, content_type=content_type)


@tracer.capture_method
def delete_file(key: str) -> None:
  """Delete a single static-content object by key."""
  get_s3_client().delete_object(Bucket=settings.STATIC_CONTENT_S3_BUCKET, Key=key)
  logger.info(f"Deleted file from s3://{settings.STATIC_CONTENT_S3_BUCKET}/{key}")


@tracer.capture_method
def file_exists(key: str) -> bool:
  """Return whether a static-content object exists by exact key."""
  bucket = settings.STATIC_CONTENT_S3_BUCKET
  if not bucket:
    return False
  response = get_s3_client().list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
  return any(obj.get("Key") == key for obj in response.get("Contents", []))


@tracer.capture_method
def delete_objects_by_prefix(prefix: str) -> int:
  """Delete all S3 objects matching the given prefix. Returns count deleted."""
  client = get_s3_client()
  bucket = settings.STATIC_CONTENT_S3_BUCKET
  if not bucket:
    return 0
  deleted = 0
  response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
  while True:
    objects = response.get("Contents", [])
    if objects:
      client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": obj["Key"]} for obj in objects if "Key" in obj]},
      )
      deleted += len(objects)
    if not response.get("IsTruncated"):
      break
    response = client.list_objects_v2(
      Bucket=bucket,
      Prefix=prefix,
      ContinuationToken=response["NextContinuationToken"],
    )
  return deleted
