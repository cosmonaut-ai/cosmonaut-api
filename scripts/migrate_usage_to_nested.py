"""Restructure UserUsage items from flat attributes into a nested ``usage`` map.

Adds ``username`` (null) and ``is_onboarded`` (false) top-level fields, then
moves all usage/subscription attributes into a ``usage`` map attribute.

Idempotent: skips items that already contain a ``usage`` map.

Usage:
  python -m scripts.migrate_usage_to_nested --env dev --dry-run
  python -m scripts.migrate_usage_to_nested --env dev
  python -m scripts.migrate_usage_to_nested --env prod
"""

from __future__ import annotations

import argparse
import logging
import os
from decimal import Decimal

# ── Parse args and set env BEFORE importing app modules ──────────────────────


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Migrate flat UserUsage items to nested usage map")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DynamoDB")
  return parser.parse_args()


_cli_args = _parse_args()
os.environ["DYNAMODB_TABLE_NAME"] = f"cosmonaut-{_cli_args.env}"
os.environ["ENV"] = _cli_args.env

import boto3  # noqa: E402
from boto3.dynamodb.conditions import Key  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

USAGE_FIELDS = [
  "tier",
  "stripe_customer_id",
  "nodes_used",
  "worlds_created",
  "audio_narrations_used",
  "saved_world_count",
  "period_end",
  "subscription_status",
  "pending_cancellation",
  "cancellation_date",
  "pending_tier",
  "pending_tier_date",
]


def run(*, dry_run: bool) -> None:
  table_name = os.environ["DYNAMODB_TABLE_NAME"]
  dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
  table = dynamodb.Table(table_name)

  log.info("Scanning table %s for USER#*/USAGE items ...", table_name)

  scan_kwargs = {
    "FilterExpression": Key("SK").eq("USAGE") & Key("PK").begins_with("USER#"),
  }

  migrated = 0
  skipped = 0
  total = 0

  while True:
    response = table.scan(**scan_kwargs)
    items = response.get("Items", [])
    total += len(items)

    for item in items:
      pk = item["PK"]

      if "usage" in item and isinstance(item["usage"], dict):
        skipped += 1
        log.debug("SKIP %s (already migrated)", pk)
        continue

      usage_map: dict = {}
      remove_attrs: list[str] = []

      for field_name in USAGE_FIELDS:
        if field_name in item:
          usage_map[field_name] = item[field_name]
          remove_attrs.append(field_name)

      if not usage_map:
        usage_map["tier"] = "FREE"
        usage_map["nodes_used"] = Decimal(0)
        usage_map["worlds_created"] = Decimal(0)
        usage_map["audio_narrations_used"] = Decimal(0)
        usage_map["saved_world_count"] = Decimal(0)
        usage_map["pending_cancellation"] = False

      if dry_run:
        log.info("DRY-RUN migrate %s: usage_map=%s, remove=%s", pk, usage_map, remove_attrs)
      else:
        update_expr_parts = [
          "SET #usage_map = :usage_map, #is_onboarded = :is_onboarded, #newsletter = :newsletter",
        ]
        expr_names = {
          "#usage_map": "usage",
          "#is_onboarded": "is_onboarded",
          "#newsletter": "newsletter_opted_in",
        }
        expr_values: dict = {
          ":usage_map": usage_map,
          ":is_onboarded": False,
          ":newsletter": item.get("newsletter_opted_in", False),
        }

        if remove_attrs:
          remove_clause = "REMOVE " + ", ".join(f"#{a}" for a in remove_attrs)
          update_expr_parts.append(remove_clause)
          for a in remove_attrs:
            expr_names[f"#{a}"] = a

        table.update_item(
          Key={"PK": pk, "SK": "USAGE"},
          UpdateExpression=" ".join(update_expr_parts),
          ExpressionAttributeNames=expr_names,
          ExpressionAttributeValues=expr_values,
        )
        log.info("Migrated %s (%d fields moved)", pk, len(remove_attrs))

      migrated += 1

    if "LastEvaluatedKey" not in response:
      break
    scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

  log.info("Done. total=%d  migrated=%d  skipped=%d  dry_run=%s", total, migrated, skipped, dry_run)


if __name__ == "__main__":
  run(dry_run=_cli_args.dry_run)
