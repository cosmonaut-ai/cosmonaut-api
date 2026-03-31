"""Migrate family_friendly boolean to vocab_level + content_filter.

Replaces the legacy ``family_friendly`` attribute on WorldMeta and
SessionMembership items with the new ``vocab_level`` and ``content_filter``
attributes using raw boto3 operations (the PynamoDB models no longer
define family_friendly).

Mapping:
  family_friendly == "true"  -> vocab_level="teen",  content_filter="strict"
  family_friendly == "false" -> vocab_level="adult", content_filter="none"
  family_friendly is missing -> vocab_level="adult", content_filter="none"

The old ``family_friendly`` attribute is removed after migration.
The operation is idempotent — items that already have ``vocab_level`` set
are skipped.

Usage:
  python -m scripts.migrate_family_friendly --env dev --dry-run
  python -m scripts.migrate_family_friendly --env dev
  python -m scripts.migrate_family_friendly --env prod
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field

import boto3

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate_family_friendly")


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Migrate family_friendly to vocab_level + content_filter")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DynamoDB")
  parser.add_argument("--region", default="us-east-2", help="AWS region (default: us-east-2)")
  return parser.parse_args()


@dataclass
class MigrationResult:
  scanned: int = 0
  updated: int = 0
  skipped: int = 0
  errors: list[str] = field(default_factory=list)


def _resolve_settings(family_friendly: str | None) -> tuple[str, str]:
  """Map legacy family_friendly value to (vocab_level, content_filter)."""
  if family_friendly == "true":
    return ("teen", "strict")
  return ("adult", "none")


def _scan_items(table, sk_prefix: str):
  """Scan for items with a given SK prefix, paginating through all results."""
  last_key = None
  while True:
    kwargs = {
      "FilterExpression": "begins_with(SK, :prefix)",
      "ExpressionAttributeValues": {":prefix": sk_prefix},
    }
    if last_key:
      kwargs["ExclusiveStartKey"] = last_key
    response = table.scan(**kwargs)
    yield from response.get("Items", [])
    last_key = response.get("LastEvaluatedKey")
    if not last_key:
      break


def migrate_items(table, sk_prefix: str, label: str, result: MigrationResult, dry_run: bool) -> None:
  log.info("--- Migrating %s items ---", label)

  for item in _scan_items(table, sk_prefix):
    result.scanned += 1

    if item.get("vocab_level"):
      result.skipped += 1
      if result.scanned % 500 == 0:
        log.info("  ...scanned %d %s items so far", result.scanned, label)
      continue

    pk = item["PK"]
    sk = item["SK"]
    ff_value = item.get("family_friendly")
    vocab_level, content_filter = _resolve_settings(ff_value)

    try:
      if dry_run:
        log.debug(
          "[DRY-RUN] %s PK=%s: family_friendly=%s -> vocab_level=%s, content_filter=%s",
          label,
          pk,
          ff_value,
          vocab_level,
          content_filter,
        )
      else:
        update_expr = "SET vocab_level = :vl, content_filter = :cf REMOVE family_friendly"
        table.update_item(
          Key={"PK": pk, "SK": sk},
          UpdateExpression=update_expr,
          ExpressionAttributeValues={
            ":vl": vocab_level,
            ":cf": content_filter,
          },
        )
      result.updated += 1
    except Exception as e:
      msg = f"Error updating {label} PK={pk} SK={sk}: {e}"
      log.exception(msg)
      result.errors.append(msg)

    if result.scanned % 500 == 0:
      log.info("  ...scanned %d %s items so far", result.scanned, label)

  log.info(
    "%s migration: %d scanned, %d updated, %d already migrated",
    label,
    result.scanned,
    result.updated,
    result.skipped,
  )


def print_report(world_result: MigrationResult, membership_result: MigrationResult, dry_run: bool) -> None:
  log.info("")
  log.info("=" * 60)
  log.info("MIGRATION REPORT%s", " (DRY-RUN)" if dry_run else "")
  log.info("=" * 60)
  log.info("WorldMeta:")
  log.info("  Scanned:           %d", world_result.scanned)
  log.info("  Updated:           %d", world_result.updated)
  log.info("  Already migrated:  %d", world_result.skipped)
  log.info("SessionMembership:")
  log.info("  Scanned:           %d", membership_result.scanned)
  log.info("  Updated:           %d", membership_result.updated)
  log.info("  Already migrated:  %d", membership_result.skipped)

  all_errors = world_result.errors + membership_result.errors
  if all_errors:
    log.error("ERRORS: %d", len(all_errors))
    for err in all_errors[:50]:
      log.error("  - %s", err)
    if len(all_errors) > 50:
      log.error("  ... and %d more", len(all_errors) - 50)
  else:
    log.info("  Errors: 0")
  log.info("=" * 60)


def main() -> None:
  args = _parse_args()
  table_name = f"cosmonaut-{args.env}"
  log.info("Target table: %s (region: %s)", table_name, args.region)
  if args.dry_run:
    log.info("*** DRY-RUN MODE — no writes will be performed ***")

  dynamodb = boto3.resource("dynamodb", region_name=args.region)
  table = dynamodb.Table(table_name)

  world_result = MigrationResult()
  membership_result = MigrationResult()
  start = time.time()

  migrate_items(table, "META", "WorldMeta", world_result, args.dry_run)
  migrate_items(table, "SMEMBER#", "SessionMembership", membership_result, args.dry_run)

  elapsed = time.time() - start
  log.info("Total elapsed: %.1fs", elapsed)
  print_report(world_result, membership_result, args.dry_run)

  if world_result.errors or membership_result.errors:
    sys.exit(1)


if __name__ == "__main__":
  main()
