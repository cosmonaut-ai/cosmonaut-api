"""Migrate flat audio map to nested AudioEntryMap format.

Converts legacy ``audio`` attribute values from flat strings
(``{voice_id: "url"}``) to nested maps
(``{voice_id: {"audio_url": "url"}}``).

Items already in the new format (value is a map with ``audio_url`` key) are
skipped.  No ``timestamps_url`` is set for migrated items since these predate
the /with-timestamps feature.

The operation is idempotent — running it multiple times is safe.

Usage:
  python -m scripts.migrate_audio_entries --env dev --dry-run
  python -m scripts.migrate_audio_entries --env dev
  python -m scripts.migrate_audio_entries --env prod
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
log = logging.getLogger("migrate_audio_entries")


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Migrate flat audio map to nested AudioEntryMap format")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DynamoDB")
  parser.add_argument("--region", default="us-east-2", help="AWS region (default: us-east-2)")
  return parser.parse_args()


@dataclass
class MigrationResult:
  scanned: int = 0
  migrated: int = 0
  skipped: int = 0
  no_audio: int = 0
  errors: list[str] = field(default_factory=list)


def _scan_nodes(table):
  """Scan for all StoryNode items (SK begins with NODE#), paginating."""
  last_key = None
  while True:
    kwargs = {
      "FilterExpression": "begins_with(SK, :prefix)",
      "ExpressionAttributeValues": {":prefix": "NODE#"},
    }
    if last_key:
      kwargs["ExclusiveStartKey"] = last_key
    response = table.scan(**kwargs)
    yield from response.get("Items", [])
    last_key = response.get("LastEvaluatedKey")
    if not last_key:
      break


def _needs_migration(audio_map: dict) -> bool:
  """Return True if any value in the audio map is a plain string (old format)."""
  return any(isinstance(value, str) for value in audio_map.values())


def _convert_audio_map(audio_map: dict) -> dict:
  """Convert old-format entries to new nested format, leaving new-format entries intact."""
  converted = {}
  for voice_id, value in audio_map.items():
    if isinstance(value, str):
      converted[voice_id] = {"audio_url": value}
    else:
      converted[voice_id] = value
  return converted


def migrate(table, result: MigrationResult, dry_run: bool) -> None:
  log.info("--- Scanning StoryNode items for audio migration ---")

  for item in _scan_nodes(table):
    result.scanned += 1

    audio_map = item.get("audio")
    if not audio_map or not isinstance(audio_map, dict):
      result.no_audio += 1
      if result.scanned % 1000 == 0:
        log.info(
          "  ...scanned %d items so far (migrated=%d, skipped=%d)", result.scanned, result.migrated, result.skipped
        )
      continue

    if not _needs_migration(audio_map):
      result.skipped += 1
      if result.scanned % 1000 == 0:
        log.info(
          "  ...scanned %d items so far (migrated=%d, skipped=%d)", result.scanned, result.migrated, result.skipped
        )
      continue

    pk = item["PK"]
    sk = item["SK"]
    new_audio = _convert_audio_map(audio_map)

    try:
      if dry_run:
        log.debug("[DRY-RUN] PK=%s SK=%s: audio %s -> %s", pk, sk, audio_map, new_audio)
      else:
        table.update_item(
          Key={"PK": pk, "SK": sk},
          UpdateExpression="SET audio = :new_audio",
          ExpressionAttributeValues={":new_audio": new_audio},
        )
      result.migrated += 1
    except Exception as e:
      msg = f"Error updating PK={pk} SK={sk}: {e}"
      log.exception(msg)
      result.errors.append(msg)

    if result.scanned % 1000 == 0:
      log.info(
        "  ...scanned %d items so far (migrated=%d, skipped=%d)", result.scanned, result.migrated, result.skipped
      )


def print_report(result: MigrationResult, dry_run: bool) -> None:
  log.info("")
  log.info("=" * 60)
  log.info("AUDIO MIGRATION REPORT%s", " (DRY-RUN)" if dry_run else "")
  log.info("=" * 60)
  log.info("  Scanned:            %d", result.scanned)
  log.info("  Migrated:           %d", result.migrated)
  log.info("  Already up-to-date: %d", result.skipped)
  log.info("  No audio attr:      %d", result.no_audio)
  if result.errors:
    log.error("  Errors:             %d", len(result.errors))
    for err in result.errors[:50]:
      log.error("    - %s", err)
    if len(result.errors) > 50:
      log.error("    ... and %d more", len(result.errors) - 50)
  else:
    log.info("  Errors:             0")
  log.info("=" * 60)


def main() -> None:
  args = _parse_args()
  table_name = f"cosmonaut-{args.env}"
  log.info("Target table: %s (region: %s)", table_name, args.region)
  if args.dry_run:
    log.info("*** DRY-RUN MODE — no writes will be performed ***")

  dynamodb = boto3.resource("dynamodb", region_name=args.region)
  table = dynamodb.Table(table_name)

  result = MigrationResult()
  start = time.time()
  migrate(table, result, args.dry_run)
  elapsed = time.time() - start

  log.info("Total elapsed: %.1fs", elapsed)
  print_report(result, args.dry_run)

  if result.errors:
    sys.exit(1)


if __name__ == "__main__":
  main()
