"""Phase 4 backfill: populate fields added after the Phase 3 migration.

Two passes, each idempotent:
  Pass A – Set parent_id on every NodeSession (derived from node_id encoding)
  Pass B – Set root_node_id, family_friendly, root_created_at on every
           SessionMembership (denormalized from WorldMeta)

Usage:
  python -m scripts.backfill_phase4_fields --env dev --dry-run
  python -m scripts.backfill_phase4_fields --env dev --pass all
  python -m scripts.backfill_phase4_fields --env prod --pass B

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
  parser = argparse.ArgumentParser(description="Backfill Phase 4 fields on NodeSession and SessionMembership")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DynamoDB")
  parser.add_argument(
    "--pass",
    dest="run_pass",
    choices=["A", "B", "all"],
    default="all",
    help="Run a specific pass (A=NodeSession parent_id, B=SessionMembership metadata) or all",
  )
  return parser.parse_args()


_cli_args = _parse_args()
os.environ["DYNAMODB_TABLE_NAME"] = f"cosmonaut-{_cli_args.env}"
os.environ["ENV"] = _cli_args.env

from app.models.entities.node_session import NodeSession  # noqa: E402
from app.models.entities.session_membership import SessionMembership  # noqa: E402
from app.models.entities.world_meta import WorldMeta  # noqa: E402


def derive_parent_id(node_id: str) -> str | None:
  """Derive the parent node ID from a StoryNode ID using the base-52 encoding scheme."""
  ancestors: list[str] = []
  build_id = ""
  i = 0
  while i < len(node_id):
    char = node_id[i]
    if char.isdigit():
      if char == "0":
        build_id += "0"
      else:
        build_id += node_id[i + 1 : i + int(char) + 1]
      i += int(char) + 1
    else:
      build_id += char
      i += 1
    ancestors.append(build_id)
  if len(ancestors) > 1:
    return ancestors[-2]
  return None


logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_p4")


@dataclass
class BackfillResult:
  # Pass A
  node_sessions_scanned: int = 0
  parent_ids_set: int = 0
  parent_ids_skipped: int = 0
  root_nodes_skipped: int = 0

  # Pass B
  memberships_scanned: int = 0
  memberships_updated: int = 0
  memberships_skipped: int = 0
  worlds_not_found: int = 0

  errors: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Pass A: Backfill NodeSession.parent_id
# ──────────────────────────────────────────────────────────────────────────────


def pass_a_backfill_parent_ids(result: BackfillResult, dry_run: bool) -> None:
  log.info("=" * 60)
  log.info("PASS A: Backfilling NodeSession.parent_id")
  log.info("=" * 60)

  scan_kwargs: dict = {"filter_condition": NodeSession.SK.startswith("NODE#")}
  items = NodeSession.scan(**scan_kwargs)

  for ns in items:
    result.node_sessions_scanned += 1
    node_id = str(ns.node_id)

    if ns.parent_id is not None:
      result.parent_ids_skipped += 1
      continue

    computed_parent = derive_parent_id(node_id)
    if computed_parent is None:
      result.root_nodes_skipped += 1
      continue

    try:
      if dry_run:
        log.debug(
          "[DRY-RUN] Would set parent_id=%s on NodeSession (session=%s, node=%s)",
          computed_parent,
          ns.session_id,
          node_id,
        )
      else:
        ns.update(
          actions=[NodeSession.parent_id.set(computed_parent)],
          add_version_condition=False,
        )
      result.parent_ids_set += 1
    except Exception as e:
      msg = f"Error setting parent_id on NodeSession (session={ns.session_id}, node={node_id}): {e}"
      log.exception(msg)
      result.errors.append(msg)

    if result.node_sessions_scanned % 500 == 0:
      log.info("  ...scanned %d NodeSessions so far", result.node_sessions_scanned)

  log.info(
    "Pass A complete: %d scanned, %d parent_ids set, %d already populated, %d root nodes (no parent)",
    result.node_sessions_scanned,
    result.parent_ids_set,
    result.parent_ids_skipped,
    result.root_nodes_skipped,
  )


# ──────────────────────────────────────────────────────────────────────────────
# Pass B: Backfill SessionMembership metadata
# ──────────────────────────────────────────────────────────────────────────────


def pass_b_backfill_membership_metadata(result: BackfillResult, dry_run: bool) -> None:
  log.info("=" * 60)
  log.info("PASS B: Backfilling SessionMembership root_node_id, family_friendly, root_created_at")
  log.info("=" * 60)

  world_cache: dict[str, WorldMeta | None] = {}

  scan_kwargs: dict = {"filter_condition": SessionMembership.SK.startswith("SMEMBER#")}
  items = SessionMembership.scan(**scan_kwargs)

  for membership in items:
    result.memberships_scanned += 1
    root_world_id = str(membership.root_world_id)

    needs_root_node_id = membership.root_node_id is None
    needs_family_friendly = membership.family_friendly is None
    needs_created_at = membership.root_created_at is None

    if not needs_root_node_id and not needs_family_friendly and not needs_created_at:
      result.memberships_skipped += 1
      continue

    if root_world_id not in world_cache:
      try:
        world_cache[root_world_id] = WorldMeta.get(WorldMeta.pk(root_world_id), WorldMeta.sk())
      except WorldMeta.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
        world_cache[root_world_id] = None
        log.warning("WorldMeta not found for root_world_id=%s", root_world_id)

    world = world_cache[root_world_id]
    if world is None:
      result.worlds_not_found += 1
      continue

    actions = []
    updates_desc = []

    if needs_root_node_id and world.root_node_id:
      actions.append(SessionMembership.root_node_id.set(world.root_node_id))
      updates_desc.append(f"root_node_id={world.root_node_id}")

    if needs_family_friendly and world.family_friendly is not None:
      actions.append(SessionMembership.family_friendly.set(world.family_friendly))
      updates_desc.append(f"family_friendly={world.family_friendly}")

    if needs_created_at and world.created_at:
      created_at_str = world.created_at.isoformat() if hasattr(world.created_at, "isoformat") else str(world.created_at)
      actions.append(SessionMembership.root_created_at.set(created_at_str))
      updates_desc.append("root_created_at")

    if not actions:
      result.memberships_skipped += 1
      continue

    try:
      if dry_run:
        log.debug(
          "[DRY-RUN] Would update membership (user=%s, world=%s): %s",
          membership.user_id,
          root_world_id,
          ", ".join(updates_desc),
        )
      else:
        membership.update(actions=actions, add_version_condition=False)
      result.memberships_updated += 1
    except Exception as e:
      msg = f"Error updating membership (user={membership.user_id}, world={root_world_id}): {e}"
      log.exception(msg)
      result.errors.append(msg)

    if result.memberships_scanned % 500 == 0:
      log.info("  ...scanned %d SessionMemberships so far", result.memberships_scanned)

  log.info(
    "Pass B complete: %d scanned, %d updated, %d already complete, %d worlds not found",
    result.memberships_scanned,
    result.memberships_updated,
    result.memberships_skipped,
    result.worlds_not_found,
  )


# ──────────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────────


def print_report(result: BackfillResult, dry_run: bool) -> None:
  log.info("")
  log.info("=" * 60)
  log.info("BACKFILL REPORT%s", " (DRY-RUN)" if dry_run else "")
  log.info("=" * 60)
  log.info("Pass A - NodeSession.parent_id:")
  log.info("  Scanned:               %d", result.node_sessions_scanned)
  log.info("  parent_ids set:        %d", result.parent_ids_set)
  log.info("  Already populated:     %d", result.parent_ids_skipped)
  log.info("  Root nodes (no parent):%d", result.root_nodes_skipped)
  log.info("Pass B - SessionMembership metadata:")
  log.info("  Scanned:               %d", result.memberships_scanned)
  log.info("  Updated:               %d", result.memberships_updated)
  log.info("  Already complete:      %d", result.memberships_skipped)
  log.info("  Worlds not found:      %d", result.worlds_not_found)

  if result.errors:
    log.error("ERRORS: %d", len(result.errors))
    for err in result.errors[:50]:
      log.error("  - %s", err)
    if len(result.errors) > 50:
      log.error("  ... and %d more", len(result.errors) - 50)
  else:
    log.info("Errors: 0")
  log.info("=" * 60)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────


def main() -> None:
  args = _cli_args
  log.info("Target table: %s", os.environ["DYNAMODB_TABLE_NAME"])
  if args.dry_run:
    log.info("*** DRY-RUN MODE — no writes will be performed ***")

  result = BackfillResult()
  start = time.time()

  if args.run_pass in ("A", "all"):
    pass_a_backfill_parent_ids(result, args.dry_run)

  if args.run_pass in ("B", "all"):
    pass_b_backfill_membership_metadata(result, args.dry_run)

  elapsed = time.time() - start
  log.info("Total elapsed: %.1fs", elapsed)
  print_report(result, args.dry_run)

  if result.errors:
    sys.exit(1)


if __name__ == "__main__":
  main()
