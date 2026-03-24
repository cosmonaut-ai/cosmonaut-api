"""One-time cleanup: remove legacy custom choices from StoryNode.choices.

The pre-sessions choose() function dual-wrote custom choices into both
StoryNode.node_choices (shared entity) and NodeSession.custom_choices
(session-scoped). After the sessions migration, only NodeSession is the
source of truth. This script strips the legacy is_custom=True entries
from node_choices so they no longer leak across sessions.

Only the node_choices list attribute is modified. The parent_choice map
attribute (which legitimately carries is_custom for LLM context) is
left untouched.

Usage:
  python -m scripts.cleanup_custom_choices --env dev --dry-run
  python -m scripts.cleanup_custom_choices --env dev
  python -m scripts.cleanup_custom_choices --env prod

IMPORTANT: --env must be parsed and DYNAMODB_TABLE_NAME set before any app.*
imports, because PynamoDB binds Meta.table_name at class-definition time.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Remove legacy custom choices from StoryNode.choices")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log affected records without modifying")
  return parser.parse_args()


def main() -> None:
  args = _parse_args()
  table_name = f"cosmonaut-{args.env}"
  os.environ["DYNAMODB_TABLE_NAME"] = table_name
  os.environ["ENV"] = args.env

  import boto3

  dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
  table = dynamodb.Table(table_name)

  logger.info("Scanning table %s for NODE# records with custom choices in node_choices...", table_name)

  scan_kwargs = {
    "FilterExpression": "begins_with(SK, :sk_prefix)",
    "ExpressionAttributeValues": {":sk_prefix": "NODE#"},
    "ProjectionExpression": "PK, SK, node_choices",
  }

  total_scanned = 0
  total_affected = 0
  total_choices_removed = 0

  while True:
    response = table.scan(**scan_kwargs)
    items = response.get("Items", [])
    total_scanned += len(items)

    for item in items:
      choices = item.get("node_choices")
      if not choices or not isinstance(choices, list):
        continue

      filtered = [c for c in choices if not c.get("is_custom", False)]
      removed_count = len(choices) - len(filtered)

      if removed_count == 0:
        continue

      total_affected += 1
      total_choices_removed += removed_count
      pk, sk = item["PK"], item["SK"]

      if args.dry_run:
        removed_labels = [c.get("label", "?") for c in choices if c.get("is_custom", False)]
        logger.info(
          "[DRY RUN] %s %s — would remove %d custom choice(s): %s",
          pk,
          sk,
          removed_count,
          removed_labels,
        )
      else:
        table.update_item(
          Key={"PK": pk, "SK": sk},
          UpdateExpression="SET node_choices = :filtered",
          ExpressionAttributeValues={":filtered": filtered},
        )
        logger.info("Updated %s %s — removed %d custom choice(s)", pk, sk, removed_count)

    last_key = response.get("LastEvaluatedKey")
    if not last_key:
      break
    scan_kwargs["ExclusiveStartKey"] = last_key
    time.sleep(0.5)

  logger.info(
    "%s complete. Scanned %d nodes, %d affected, %d custom choices %s.",
    "DRY RUN" if args.dry_run else "Cleanup",
    total_scanned,
    total_affected,
    total_choices_removed,
    "would be removed" if args.dry_run else "removed",
  )


if __name__ == "__main__":
  main()
