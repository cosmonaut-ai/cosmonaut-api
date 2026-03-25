"""Backfill GSI2 keys and last_accessed_at on SessionMembership.

After deploying the GSI2 index (Terraform) and the application code that
populates GSI2PK/GSI2SK/last_accessed_at on new writes, existing
SessionMembership items remain invisible to the GSI2-based dashboard
query.  This script scans all SMEMBER# items and sets the three missing
attributes so they appear in chronological order.

Timestamp strategy: ``updated_at`` is the best available proxy for last
user activity.  It also fires on metadata updates (generation completion),
but for most sessions, progress updates dominate.  Falls back to
``joined_at`` when ``updated_at`` is absent.

The operation is idempotent — items that already have GSI2PK are skipped.

Usage:
  python -m scripts.backfill_gsi2_last_accessed --env dev --dry-run
  python -m scripts.backfill_gsi2_last_accessed --env dev
  python -m scripts.backfill_gsi2_last_accessed --env prod

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
  parser = argparse.ArgumentParser(description="Backfill GSI2 keys and last_accessed_at on SessionMembership")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DynamoDB")
  return parser.parse_args()


_cli_args = _parse_args()
os.environ["DYNAMODB_TABLE_NAME"] = f"cosmonaut-{_cli_args.env}"
os.environ["ENV"] = _cli_args.env

from app.models.entities.session_membership import SessionMembership  # noqa: E402

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_gsi2")


@dataclass
class BackfillResult:
  scanned: int = 0
  updated: int = 0
  skipped: int = 0
  no_timestamp: int = 0
  errors: list[str] = field(default_factory=list)


def backfill_gsi2(result: BackfillResult, dry_run: bool) -> None:
  log.info("=" * 60)
  log.info("Backfilling GSI2PK, GSI2SK, last_accessed_at on SessionMembership")
  log.info("=" * 60)

  items = SessionMembership.scan(filter_condition=SessionMembership.SK.startswith("SMEMBER#"))

  for membership in items:
    result.scanned += 1

    if membership.GSI2PK is not None:
      result.skipped += 1
      if result.scanned % 500 == 0:
        log.info("  ...scanned %d SessionMemberships so far", result.scanned)
      continue

    ts = membership.updated_at or membership.joined_at
    if ts is None:
      result.no_timestamp += 1
      log.warning(
        "No usable timestamp for membership (user=%s, session=%s) — skipping",
        membership.user_id,
        membership.session_id,
      )
      continue

    user_id = str(membership.user_id)
    session_id = str(membership.session_id)
    gsi2_pk = SessionMembership.gsi2_pk(user_id)
    gsi2_sk = SessionMembership.gsi2_sk(ts, session_id)

    try:
      if dry_run:
        log.debug(
          "[DRY-RUN] Would set GSI2 keys on membership (user=%s, session=%s, ts=%s)",
          user_id,
          session_id,
          ts.isoformat(),
        )
      else:
        membership.update(
          actions=[
            SessionMembership.last_accessed_at.set(ts),
            SessionMembership.GSI2PK.set(gsi2_pk),
            SessionMembership.GSI2SK.set(gsi2_sk),
          ],
          add_version_condition=False,
        )
      result.updated += 1
    except Exception as e:
      msg = f"Error updating membership (user={user_id}, session={session_id}): {e}"
      log.exception(msg)
      result.errors.append(msg)

    if result.scanned % 500 == 0:
      log.info("  ...scanned %d SessionMemberships so far", result.scanned)

  log.info(
    "Backfill complete: %d scanned, %d updated, %d already populated, %d missing timestamp",
    result.scanned,
    result.updated,
    result.skipped,
    result.no_timestamp,
  )


def print_report(result: BackfillResult, dry_run: bool) -> None:
  log.info("")
  log.info("=" * 60)
  log.info("BACKFILL REPORT%s", " (DRY-RUN)" if dry_run else "")
  log.info("=" * 60)
  log.info("  Scanned:            %d", result.scanned)
  log.info("  Updated:            %d", result.updated)
  log.info("  Already populated:  %d", result.skipped)
  log.info("  Missing timestamp:  %d", result.no_timestamp)

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

  backfill_gsi2(result, args.dry_run)

  elapsed = time.time() - start
  log.info("Total elapsed: %.1fs", elapsed)
  print_report(result, args.dry_run)

  if result.errors:
    sys.exit(1)


if __name__ == "__main__":
  main()
