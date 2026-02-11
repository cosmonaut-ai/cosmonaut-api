from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict

from botocore.exceptions import ClientError

if TYPE_CHECKING:
  from mypy_boto3_sqs.client import SQSClient

from app.core.config import settings
from app.models.dtos.sqs_payloads import AnalyzeNodePayload, GenerateWorldImagePayload, GenerateWorldPayload

logger = logging.getLogger(__name__)


class SQSSendError(Exception):
  """Raised when an SQS message fails to send."""

  def __init__(self, queue_url: str, cause: Exception | None = None):
    super().__init__(f"Failed to send SQS message to {queue_url}")
    self.queue_url = queue_url
    self.__cause__ = cause


sqs: SQSClient | None = None


def get_sqs_client() -> SQSClient:
  global sqs
  if sqs is None:
    import boto3

    sqs = boto3.client("sqs", region_name=settings.AWS_REGION)
  return sqs


def send_message(queue_url: str, message: Dict[str, Any], delay_seconds: int = 0) -> str:
  """Send a JSON-serializable dict to SQS.

  Returns:
    The SQS MessageId on success.

  Raises:
    SQSSendError: If the message cannot be sent.
  """
  try:
    response = get_sqs_client().send_message(
      QueueUrl=queue_url, MessageBody=json.dumps(message), DelaySeconds=delay_seconds
    )
    message_id = response.get("MessageId")
    if not message_id:
      raise SQSSendError(queue_url)
    return message_id
  except ClientError as e:
    logger.error(f"Failed to send message to SQS: {e}")
    raise SQSSendError(queue_url, cause=e) from e


def send_node_analysis_message(world_id: str, node_id: str) -> None:
  """Send a node analysis message to the fast worker queue.

  Raises ``SQSSendError`` on failure so callers can handle or propagate.
  """
  send_message(settings.FAST_WORKER_QUEUE_URL, AnalyzeNodePayload(world_id=world_id, node_id=node_id).model_dump())
  logger.info(f"Sent node analysis message for {node_id} in world {world_id}")


def send_world_generation_message(world_id: str) -> None:
  """Send a world generation message to the slow worker queue.

  Raises ``SQSSendError`` on failure so callers can handle or propagate.
  """
  send_message(settings.SLOW_WORKER_QUEUE_URL, GenerateWorldPayload(world_id=world_id).model_dump())
  logger.info(f"Sent world generation message for {world_id}")


def send_world_image_generation_message(world_id: str) -> None:
  """Send a world image generation message to the slow worker queue.

  Raises ``SQSSendError`` on failure so callers can handle or propagate.
  """
  send_message(settings.SLOW_WORKER_QUEUE_URL, GenerateWorldImagePayload(world_id=world_id).model_dump())
  logger.info(f"Sent world image generation message for {world_id}")
