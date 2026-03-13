import asyncio
from collections.abc import Callable, Iterable
from typing import Any, cast

import sentry_sdk
from aws_lambda_powertools.utilities.data_classes import SQSEvent, event_source  # type: ignore[import-untyped]
from pydantic import TypeAdapter
from pynamodb.exceptions import UpdateError
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration

from app.core.config import settings
from app.core.observability import logger, tracer

if settings.ENV != "local":
  sentry_sdk.init(
    dsn=settings.SENTRY_DSN,
    environment=settings.ENV,
    release=settings.SENTRY_RELEASE or None,
    send_default_pii=True,
    traces_sample_rate=0.1,
    enable_logs=True,
    integrations=[AwsLambdaIntegration(timeout_warning=True)],
  )
from app.models.dtos.sqs_payloads import AnalyzeNodePayload, GenerateWorldImagePayload, GenerateWorldPayload, SQSPayload
from app.models.dtos.story_node import StoryNodeProcessingStatus
from app.models.dtos.world_meta import GenerationStatus
from app.models.entities.story_node import StoryNode
from app.models.entities.world_meta import WorldMeta
from app.services import images, story_nodes, worlds
from app.services.sqs import SQSSendError, send_world_image_generation_message

# Concurrency cap for processing messages in parallel within a Lambda invocation.
BATCH_CONCURRENCY = 5

sqs_payload_adapter: TypeAdapter[SQSPayload] = TypeAdapter(SQSPayload)

# Provide a typed wrapper for the event source decorator to satisfy type checkers.
HandlerReturn = dict[str, Any]
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

  failed_message_ids: list[str] = loop.run_until_complete(_process_batch(event.records))

  # Return the AWS-standard partial batch failure response so that only failed
  # messages are retried.  The previous format ("failed_message_ids") was not
  # recognized by the Lambda/SQS integration, causing failed messages to be
  # silently dropped instead of retried.
  return {
    "batchItemFailures": [{"itemIdentifier": mid} for mid in failed_message_ids],
  }


async def _process_batch(records: Iterable[Any]) -> list[str]:
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
      await _generate_world_image(payload)


async def _analyze_node(payload: AnalyzeNodePayload):
  world_id = payload.world_id
  node_id = payload.node_id
  # FAST LANE TASK (~3s)
  # Extracts facts from a generated node and upserts to Pinecone
  if not world_id or not node_id:
    raise ValueError("Missing world_id or node_id for analyze_node")

  node: StoryNode = story_nodes.get_node_entity(world_id, node_id)

  # Skip if already completed or failed (idempotent for duplicate messages).
  current_status = StoryNodeProcessingStatus(node.processing_status)
  if current_status in (StoryNodeProcessingStatus.COMPLETED, StoryNodeProcessingStatus.FAILED):
    logger.info(f"Node {node_id} already {current_status.value}, skipping.")
    return

  # Atomically transition: PENDING | PROCESSING -> PROCESSING.
  # Accepting PROCESSING in addition to PENDING allows a retry to re-claim
  # a node whose previous worker crashed after the conditional update but
  # before the final save (which would leave it stuck in PROCESSING forever).
  # Pinecone upserts are idempotent by record ID, so concurrent processing
  # from a rare SQS duplicate delivery is safe — at worst it wastes compute.
  try:
    node.update(
      actions=[StoryNode.processing_status.set(StoryNodeProcessingStatus.PROCESSING.value)],
      condition=StoryNode.processing_status.is_in(
        StoryNodeProcessingStatus.PENDING.value,
        StoryNodeProcessingStatus.PROCESSING.value,
      ),
    )
  except UpdateError:
    logger.info(f"Node {node_id} already completed or failed, skipping.")
    return

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

  # Allow retries from intermediate states (GENERATING_LORE, GENERATING_NARRATOR_PROFILE)
  # in addition to INITIALIZED and FAILED.  A worker crash during lore/narrator generation
  # would otherwise leave the world permanently stuck.  Retries are safe because:
  # - generate_lore() overwrites previous lore
  # - generate_narrator_profile() is idempotent (returns early if already set)
  # - initialize_root_node() overwrites the root node (same deterministic ID "0")
  if world.generation_status == GenerationStatus.COMPLETED:
    logger.info(f"World {world_id} already completed, skipping.")
    return

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

    # 3. Initialize Root Node (without text generation)
    # Text will be generated when the client calls /generate-text
    logger.info("Initializing Root Node...")
    worlds.initialize_root_node(world)

    world.generation_status = GenerationStatus.COMPLETED
    logger.info(f"World {world_id} generation complete.")

    # 4. Enqueue image generation (fire-and-forget, non-blocking).
    # An SQS failure here must not undo the successful world generation.
    try:
      send_world_image_generation_message(world_id)
    except SQSSendError:
      logger.error(f"Failed to enqueue image generation for world {world_id} -- world will lack a cover image")

  except Exception as e:
    logger.exception(f"Error generating world {world_id}: {e}")
    world.generation_status = GenerationStatus.FAILED
    raise
  finally:
    world.save()


async def _generate_world_image(payload: GenerateWorldImagePayload):
  world_id = payload.world_id
  if not world_id:
    raise ValueError("Missing world_id for generate_world_image")

  world: WorldMeta = worlds.get_world_entity(world_id)

  # Idempotency: skip if image already exists
  if world.world_image_url:
    logger.info(f"World {world_id} already has an image, skipping.")
    return

  try:
    await images.generate_world_image(world)
    logger.info(f"World {world_id} image generation complete.")
  except Exception as e:
    logger.exception(f"Error generating image for world {world_id}: {e}")
    raise
