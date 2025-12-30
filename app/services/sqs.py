import json
import logging
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from mypy_boto3_sqs.client import SQSClient

from app.core.config import settings
from app.models.dtos.sqs_payloads import AnalyzeNodePayload, GenerateWorldPayload

logger = logging.getLogger(__name__)


sqs: SQSClient | None = None


def get_sqs_client() -> SQSClient:
  global sqs
  if sqs is None:
    sqs = boto3.client("sqs", region_name=settings.AWS_REGION)
  return sqs


def send_message(queue_url: str, message: Dict[str, Any], delay_seconds: int = 0) -> Optional[str]:
  """
  Sends a JSON-serializable dict to SQS.
  Returns MessageId on success, None on failure.
  """
  try:
    response = get_sqs_client().send_message(
      QueueUrl=queue_url, MessageBody=json.dumps(message), DelaySeconds=delay_seconds
    )
    return response.get("MessageId")
  except ClientError as e:
    logger.error(f"Failed to send message to SQS: {e}")
    return None


def send_node_analysis_message(world_id: str, node_id: str):
  send_message(settings.FAST_WORKER_QUEUE_URL, AnalyzeNodePayload(world_id=world_id, node_id=node_id).model_dump())
  logger.info(f"Sent node analysis message for {node_id} in world {world_id}")


def send_world_generation_message(world_id: str):
  send_message(settings.SLOW_WORKER_QUEUE_URL, GenerateWorldPayload(world_id=world_id).model_dump())
  logger.info(f"Sent world generation message for {world_id}")
