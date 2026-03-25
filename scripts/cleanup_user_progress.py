"""One-time cleanup: delete all UserProgress records from DynamoDB.

UserProgress items have PK = USER#{user_id}, SK = PROGRESS#{world_id}.
This script scans for all items matching SK begins_with "PROGRESS#"
and batch-deletes them.

Usage:
  python -m scripts.cleanup_user_progress --env dev --dry-run
  python -m scripts.cleanup_user_progress --env dev
  python -m scripts.cleanup_user_progress --env prod

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
  parser = argparse.ArgumentParser(description="Delete all UserProgress records from DynamoDB")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log records found without deleting")
  return parser.parse_args()


def main() -> None:
  args = _parse_args()
  table_name = f"cosmonaut-{args.env}"
  os.environ["DYNAMODB_TABLE_NAME"] = table_name
  os.environ["ENV"] = args.env

  import boto3

  dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
  table = dynamodb.Table(table_name)

  logger.info("Scanning table %s for PROGRESS# records...", table_name)

  scan_kwargs = {
    "FilterExpression": "begins_with(SK, :sk_prefix)",
    "ExpressionAttributeValues": {":sk_prefix": "PROGRESS#"},
    "ProjectionExpression": "PK, SK",
  }

  total_found = 0
  total_deleted = 0
  batch_count = 0

  while True:
    response = table.scan(**scan_kwargs)
    items = response.get("Items", [])
    total_found += len(items)

    if items:
      if args.dry_run:
        for item in items:
          logger.info("[DRY RUN] Would delete PK=%s SK=%s", item["PK"], item["SK"])
      else:
        with table.batch_writer() as batch:
          for item in items:
            batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
            total_deleted += 1
        batch_count += 1
        logger.info("Batch %d: deleted %d records (total: %d)", batch_count, len(items), total_deleted)

    last_key = response.get("LastEvaluatedKey")
    if not last_key:
      break
    scan_kwargs["ExclusiveStartKey"] = last_key
    time.sleep(0.5)

  if args.dry_run:
    logger.info("[DRY RUN] Found %d PROGRESS# records. No deletions performed.", total_found)
  else:
    logger.info("Cleanup complete. Deleted %d PROGRESS# records from %s.", total_deleted, table_name)


if __name__ == "__main__":
  main()
