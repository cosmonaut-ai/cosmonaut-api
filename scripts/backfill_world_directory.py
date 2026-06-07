"""Backfill admin recent-world directory keys on WorldMeta.

Existing ``WorldMeta`` items can predate the admin directory keys used by
``/admin/worlds``. This script scans world metadata rows and writes
``GSI3PK/GSI3SK`` so they appear in the indexed recent-world query.

Usage:
  python -m scripts.backfill_world_directory --env dev --dry-run
  python -m scripts.backfill_world_directory --env dev
  python -m scripts.backfill_world_directory --env prod --dry-run

IMPORTANT: --env must be parsed and DYNAMODB_TABLE_NAME set before any app.*
imports, because PynamoDB binds Meta.table_name at class-definition time.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Backfill admin recent-world directory GSI keys on WorldMeta")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DynamoDB")
  return parser.parse_args()


_cli_args = _parse_args()
os.environ["DYNAMODB_TABLE_NAME"] = f"cosmonaut-{_cli_args.env}"
os.environ["ENV"] = _cli_args.env

from app.models.entities.world_meta import WorldMeta  # noqa: E402

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_world_directory")


@dataclass
class BackfillResult:
  scanned: int = 0
  updated: int = 0
  unchanged: int = 0
  no_timestamp: int = 0
  no_world_id: int = 0
  errors: list[str] = field(default_factory=list)


def _world_id(record: WorldMeta) -> str | None:
  raw_id = getattr(record, "id", None)
  if raw_id:
    return str(raw_id)

  pk = str(record.PK) if record.PK else ""
  if pk.startswith("WORLD#"):
    return pk.removeprefix("WORLD#")
  return None


def _record_actions(record: WorldMeta) -> list[Any]:
  world_id = _world_id(record)
  timestamp = record.created_at or record.updated_at
  if not world_id or timestamp is None:
    return []

  gsi3_pk = WorldMeta.gsi3_pk_world_directory()
  gsi3_sk = WorldMeta.gsi3_sk_created(timestamp.isoformat(), world_id)
  actions: list[Any] = []

  if gsi3_pk != record.GSI3PK:
    actions.append(WorldMeta.GSI3PK.set(gsi3_pk))
  if gsi3_sk != record.GSI3SK:
    actions.append(WorldMeta.GSI3SK.set(gsi3_sk))
  return actions


def backfill(result: BackfillResult, *, dry_run: bool) -> None:
  meta_condition = WorldMeta.SK == WorldMeta.sk()  # noqa: SIM300 - PynamoDB needs the attribute on the left.
  items = WorldMeta.scan(filter_condition=WorldMeta.PK.startswith("WORLD#") & meta_condition)

  for record in items:
    result.scanned += 1
    world_id = _world_id(record)

    if not world_id:
      result.no_world_id += 1
      log.warning("No usable world ID for PK=%s; skipping", record.PK)
      continue
    if not (record.created_at or record.updated_at):
      result.no_timestamp += 1
      log.warning("No usable timestamp for world %s; skipping", world_id)
      continue

    actions = _record_actions(record)
    if not actions:
      result.unchanged += 1
      continue

    try:
      if dry_run:
        log.debug("[DRY-RUN] Would update world %s with %d action(s)", world_id, len(actions))
      else:
        record.update(actions=actions, add_version_condition=False)
      result.updated += 1
    except Exception as exc:
      msg = f"Error updating world {world_id}: {exc}"
      log.exception(msg)
      result.errors.append(msg)

    if result.scanned % 500 == 0:
      log.info("  ...scanned %d WorldMeta rows so far", result.scanned)


def print_report(result: BackfillResult, dry_run: bool) -> None:
  log.info("")
  log.info("=" * 60)
  log.info("WORLD DIRECTORY BACKFILL REPORT%s", " (DRY-RUN)" if dry_run else "")
  log.info("=" * 60)
  log.info("  Scanned:           %d", result.scanned)
  log.info("  Updated:           %d", result.updated)
  log.info("  Unchanged:         %d", result.unchanged)
  log.info("  Missing timestamp: %d", result.no_timestamp)
  log.info("  Missing world ID:  %d", result.no_world_id)
  log.info("  Errors:            %d", len(result.errors))
  for err in result.errors[:50]:
    log.error("  - %s", err)
  if len(result.errors) > 50:
    log.error("  ... and %d more", len(result.errors) - 50)
  log.info("=" * 60)


def main() -> None:
  args = _cli_args
  log.info("Target table: %s", os.environ["DYNAMODB_TABLE_NAME"])
  if args.dry_run:
    log.info("*** DRY-RUN MODE: no writes will be performed ***")

  result = BackfillResult()
  start = time.time()
  backfill(result, dry_run=args.dry_run)
  log.info("Total elapsed: %.1fs", time.time() - start)
  print_report(result, args.dry_run)

  if result.errors:
    sys.exit(1)


if __name__ == "__main__":
  main()
