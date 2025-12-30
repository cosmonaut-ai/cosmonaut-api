import asyncio
from typing import Any, Callable, Dict, Iterable, List, cast

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.data_classes import SQSEvent, event_source  # type: ignore[import-untyped]
from pydantic import TypeAdapter

from app.core.config import settings
from app.models.dtos.sqs_payloads import AnalyzeNodePayload, GenerateWorldImagePayload, GenerateWorldPayload, SQSPayload
from app.models.dtos.story_node import StoryNodeProcessingStatus
from app.models.dtos.world_meta import GenerationStatus
from app.models.entities.story_node import StoryNode
from app.models.entities.world_meta import WorldMeta
from app.services import sqs, story_nodes, worlds

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

  node: StoryNode = story_nodes.get_node_entity(world_id, node_id)
  if node.processing_status in [StoryNodeProcessingStatus.COMPLETED, StoryNodeProcessingStatus.PROCESSING]:
    return
  node.processing_status = StoryNodeProcessingStatus.PROCESSING
  node.save()
  try:
    await story_nodes.process_node(node)
    node.processing_status = StoryNodeProcessingStatus.COMPLETED
    logger.info(f"Node {node_id} analysis complete.")
  except Exception as e:
    logger.exception(f"Error analyzing node {node_id}: {e}")
    node.processing_status = StoryNodeProcessingStatus.FAILED
    raise
  finally:
    node.save()


async def _generate_world(payload: GenerateWorldPayload):
  world_id = payload.world_id
  if not world_id:
    raise ValueError("Missing world_id for generate_world")

  world: WorldMeta = worlds.get_world_entity(world_id)

  if world.generation_status not in [GenerationStatus.INITIALIZED, GenerationStatus.FAILED]:
    raise ValueError(f"World {world_id} is not in the INITIALIZED or FAILED state")

  try:
    # 1. Generate Lore
    logger.info("Generating Lore...")
    world.generation_status = GenerationStatus.GENERATING_LORE
    world.save()
    await worlds.generate_lore(world)

    # 2. Generate Narrator Profile
    logger.info("Generating Narrator...")
    world.generation_status = GenerationStatus.GENERATING_NARRATOR_PROFILE
    world.save()
    await worlds.generate_narrator_profile(world)

    # 3. Generate Start Node
    logger.info("Generating Start Node...")
    world.generation_status = GenerationStatus.GENERATING_START_NODE
    world.save()
    await worlds.generate_start_node(world)

    if world.root_node_id:
      sqs.send_node_analysis_message(world.id, world.root_node_id)
    world.generation_status = GenerationStatus.COMPLETED
    logger.info(f"World {world_id} generation complete.")

  except Exception as e:
    logger.exception(f"Error generating world {world_id}: {e}")
    world.generation_status = GenerationStatus.FAILED
    raise
  finally:
    world.save()
