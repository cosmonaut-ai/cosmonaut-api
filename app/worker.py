import asyncio
from typing import Any, Callable, Dict, Iterable, List, cast

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.data_classes import SQSEvent, event_source  # type: ignore[import-untyped]
from pydantic import TypeAdapter

from app.core.config import settings
from app.models.dtos.sqs_payloads import AnalyzeNodePayload, GenerateWorldImagePayload, GenerateWorldPayload, SQSPayload
from app.services import story_nodes, worlds

# Concurrency cap for processing messages in parallel within a Lambda invocation.
BATCH_CONCURRENCY = 5

# Initialize Powertools
logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)
tracer = Tracer(service=settings.POWERTOOLS_SERVICE_NAME)
sqs_payload_adapter: TypeAdapter[SQSPayload] = TypeAdapter(SQSPayload)

# Provide a typed wrapper for the event source decorator to satisfy type checkers.
HandlerReturn = Dict[str, Any]
HandlerFunc = Callable[[SQSEvent, Any], HandlerReturn]
EventDecorator = Callable[[HandlerFunc], HandlerFunc]
_event_source: Callable[..., Any] = cast(Callable[..., Any], event_source)
sqs_event_source: EventDecorator = cast(EventDecorator, _event_source(data_class=SQSEvent))


@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
@sqs_event_source
def handler(event: SQSEvent, context: Any) -> HandlerReturn:
  """
  Main Entrypoint for SQS Background Workers.
  """
  # Create a new event loop for this Lambda execution context
  # This is required because we are calling async service methods from a sync handler
  loop = asyncio.get_event_loop()
  if loop.is_closed():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

  failed_message_ids: List[str] = loop.run_until_complete(_process_batch(event.records))

  return {
    "statusCode": 200,
    "body": "Batch processed",
    "failed_message_ids": failed_message_ids,
  }


async def _process_batch(records: Iterable[Any]) -> List[str]:
  """
  Process SQS records concurrently with a bounded semaphore to avoid resource saturation.
  Returns message_ids that failed (for partial batch failure).
  """
  semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)

  async def _process_record(record: Any) -> str | None:
    async with semaphore:
      try:
        payload: SQSPayload = sqs_payload_adapter.validate_json(record.body)
        logger.info(f"Processing task: {payload.task_type}", extra={"payload": payload})
        await _process_task(payload)
        return None
      except Exception:
        logger.exception(f"Failed to process message {record.message_id}")
        return record.message_id

  results = await asyncio.gather(*(_process_record(record) for record in records))
  return [message_id for message_id in results if message_id]


async def _process_task(payload: SQSPayload):
  """
  Router for specific task logic.
  """
  match payload:
    case AnalyzeNodePayload():
      await _analyze_node(payload)

    case GenerateWorldPayload():
      await _generate_world(payload)

    case GenerateWorldImagePayload():
      world_id = payload.world_id
      # SLOW LANE TASK (~15s)
      # Placeholder for DALL-E generation
      logger.info(f"Generating image for world {world_id} (Not Implemented)")
      # await images.generate_image(node_id)


async def _analyze_node(payload: AnalyzeNodePayload):
  world_id = payload.world_id
  node_id = payload.node_id
  # FAST LANE TASK (~3s)
  # Extracts facts from a generated node and upserts to Pinecone
  if not world_id or not node_id:
    raise ValueError("Missing world_id or node_id for analyze_node")

  await story_nodes.process_node(world_id, node_id)
  logger.info(f"Node {node_id} analysis complete.")


async def _generate_world(payload: GenerateWorldPayload):
  world_id = payload.world_id
  # SLOW LANE TASK (~60s)
  # Generates Lore -> Narrator -> Start Node
  if not world_id:
    raise ValueError("Missing world_id for generate_world")

  # 1. Generate Lore (Sync)
  logger.info("Generating Lore...")
  worlds.generate_lore(world_id)

  # 2. Generate Narrator Profile (Sync)
  logger.info("Generating Narrator...")
  worlds.generate_narrator_profile(world_id)

  # 3. Generate Start Node (Async)
  logger.info("Generating Start Node...")
  await worlds.generate_start_node(world_id)

  logger.info(f"World {world_id} generation complete.")
