"""One-time backfill: sync DynamoDB usernames to Cognito custom:username.

Usage:
  python -m scripts.backfill_cognito_username [--dry-run]

Scans all UserRecord items with a non-null username and sets the
custom:username attribute in Cognito so the app-level handle appears
in subsequent ID tokens.  Idempotent — re-running sets the same value.
"""

from __future__ import annotations

import argparse
import time

from app.models.entities.user import UserRecord
from app.services.cognito import update_user_username


def main() -> None:
  parser = argparse.ArgumentParser(description="Backfill Cognito custom:username from DynamoDB")
  parser.add_argument("--dry-run", action="store_true", help="Print changes without writing to Cognito")
  args = parser.parse_args()

  synced = 0
  skipped = 0
  errors = 0

  print("Scanning UserRecord items for non-null usernames...")

  for record in UserRecord.scan():
    if str(record.SK) != "USAGE":
      continue

    username = str(record.username) if record.username else None
    if not username:
      skipped += 1
      continue

    user_id = str(record.user_id)
    print(f"  {user_id} -> {username}")

    if args.dry_run:
      print(f"    [DRY RUN] Would set custom:username={username}")
      synced += 1
    else:
      try:
        update_user_username(user_id, username)
        synced += 1
      except Exception as exc:
        print(f"    [ERROR] {exc}")
        errors += 1

    time.sleep(0.05)

  print(f"\n{'=' * 60}")
  print(f"Backfill complete: {synced} synced, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
  main()
