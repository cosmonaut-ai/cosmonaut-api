"""Backfill ChoiceMap.target on StoryNode choices.

Legacy nodes (created before deterministic target assignment was added to
generate_text) have choices with target=None.  The target_id-based choose
endpoint requires every choice to carry a non-null target.

This script computes the deterministic target for each choice using
StoryNode.get_child_id_static(node_id, index) and persists it.  The
operation is idempotent — choices that already have a target are skipped.

Usage:
  python -m scripts.backfill_choice_targets --env dev --dry-run
  python -m scripts.backfill_choice_targets --env dev
  python -m scripts.backfill_choice_targets --env prod

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

# ── Parse args and set env BEFORE importing app modules ──────────────────────


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Backfill ChoiceMap.target on StoryNode choices")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DynamoDB")
  return parser.parse_args()


_cli_args = _parse_args()
os.environ["DYNAMODB_TABLE_NAME"] = f"cosmonaut-{_cli_args.env}"
os.environ["ENV"] = _cli_args.env

from app.models.entities.story_node import StoryNode  # noqa: E402

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_targets")


@dataclass
class BackfillResult:
  nodes_scanned: int = 0
  nodes_updated: int = 0
  nodes_skipped: int = 0
  choices_backfilled: int = 0
  errors: list[str] = field(default_factory=list)


def backfill_choice_targets(result: BackfillResult, dry_run: bool) -> None:
  log.info("=" * 60)
  log.info("Backfilling ChoiceMap.target on StoryNode choices")
  log.info("=" * 60)

  scan_kwargs: dict = {"filter_condition": StoryNode.SK.startswith("NODE#")}
  items = StoryNode.scan(**scan_kwargs)

  for node in items:
    result.nodes_scanned += 1
    node_id = str(node.id)

    if not node.choices:
      result.nodes_skipped += 1
      continue

    needs_update = False
    backfilled_count = 0

    for i, choice in enumerate(node.choices):
      if choice.target is not None:
        continue

      expected_target = StoryNode.get_child_id_static(node_id, i)
      choice.target = expected_target
      needs_update = True
      backfilled_count += 1

    if not needs_update:
      result.nodes_skipped += 1
      continue

    try:
      if dry_run:
        log.info(
          "[DRY-RUN] Would backfill %d choice target(s) on node %s (world=%s)",
          backfilled_count,
          node_id,
          node.world_id,
        )
      else:
        node.save(add_version_condition=False)
        log.info(
          "Backfilled %d choice target(s) on node %s (world=%s)",
          backfilled_count,
          node_id,
          node.world_id,
        )
      result.nodes_updated += 1
      result.choices_backfilled += backfilled_count
    except Exception as e:
      msg = f"Error updating node {node_id} (world={node.world_id}): {e}"
      log.exception(msg)
      result.errors.append(msg)

    if result.nodes_scanned % 500 == 0:
      log.info("  ...scanned %d StoryNodes so far", result.nodes_scanned)

  log.info(
    "Backfill complete: %d scanned, %d updated, %d already complete",
    result.nodes_scanned,
    result.nodes_updated,
    result.nodes_skipped,
  )


def print_report(result: BackfillResult, dry_run: bool) -> None:
  log.info("")
  log.info("=" * 60)
  log.info("BACKFILL REPORT%s", " (DRY-RUN)" if dry_run else "")
  log.info("=" * 60)
  log.info("  Nodes scanned:          %d", result.nodes_scanned)
  log.info("  Nodes updated:          %d", result.nodes_updated)
  log.info("  Nodes already complete: %d", result.nodes_skipped)
  log.info("  Choices backfilled:     %d", result.choices_backfilled)

  if result.errors:
    log.error("ERRORS: %d", len(result.errors))
    for err in result.errors[:50]:
      log.error("  - %s", err)
    if len(result.errors) > 50:
      log.error("  ... and %d more", len(result.errors) - 50)
  else:
    log.info("  Errors: 0")
  log.info("=" * 60)


def main() -> None:
  args = _cli_args
  log.info("Target table: %s", os.environ["DYNAMODB_TABLE_NAME"])
  if args.dry_run:
    log.info("*** DRY-RUN MODE — no writes will be performed ***")

  result = BackfillResult()
  start = time.time()

  backfill_choice_targets(result, args.dry_run)

  elapsed = time.time() - start
  log.info("Total elapsed: %.1fs", elapsed)
  print_report(result, args.dry_run)

  if result.errors:
    sys.exit(1)


if __name__ == "__main__":
  main()
