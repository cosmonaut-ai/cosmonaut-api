"""S3 upload service for generated images.

Provides a lazy-initialized S3 client singleton and helpers for uploading
image bytes to the configured bucket, returning the public CDN URL.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
  from mypy_boto3_s3.client import S3Client

logger = logging.getLogger(__name__)

s3: S3Client | None = None


def get_s3_client() -> S3Client:
  global s3
  if s3 is None:
    import boto3

    s3 = boto3.client("s3", region_name=settings.AWS_REGION)
  return s3


def upload_image(key: str, image_bytes: bytes, content_type: str = "image/png") -> str:
  """Upload image bytes to S3 and return the public CDN URL.

  Args:
    key: The S3 object key (e.g. ``worlds/{world_id}/cover.png``).
    image_bytes: Raw image data.
    content_type: MIME type for the object. Defaults to ``image/png``.

  Returns:
    The full CDN URL for the uploaded image.
  """
  get_s3_client().put_object(
    Bucket=settings.IMAGES_S3_BUCKET,
    Key=key,
    Body=image_bytes,
    ContentType=content_type,
    CacheControl="public, max-age=31536000, immutable",
  )
  logger.info(f"Uploaded image to s3://{settings.IMAGES_S3_BUCKET}/{key}")
  return f"https://{settings.IMAGES_CDN_DOMAIN}/{key}"
