"""One-time migration: convert shared_with entries from email strings to Cognito user IDs.

Usage:
  python -m scripts.migrate_shared_with [--dry-run]

For each WorldMeta with a non-empty shared_with list, resolves each email
address to a Cognito sub via list_users.  Entries that cannot be resolved
(no matching Cognito account) are dropped and logged for audit.
"""

from __future__ import annotations

import argparse
import time

import boto3

from app.core.config import settings
from app.models.entities.world_meta import WorldMeta


def _resolve_email_to_sub(cognito_client, email: str) -> str | None:  # type: ignore[type-arg]
  """Look up a Cognito user by email and return their sub, or None."""
  try:
    resp = cognito_client.list_users(
      UserPoolId=settings.COGNITO_USER_POOL_ID,
      Filter=f'email = "{email}"',
      Limit=1,
    )
    users = resp.get("Users", [])
    if not users:
      return None
    attrs = {a["Name"]: a.get("Value", "") for a in users[0].get("Attributes", [])}
    return attrs.get("sub") or None
  except Exception as exc:
    print(f"  [ERROR] Cognito lookup failed for {email}: {exc}")
    return None


def main() -> None:
  parser = argparse.ArgumentParser(description="Migrate shared_with from emails to Cognito user IDs")
  parser.add_argument("--dry-run", action="store_true", help="Print changes without writing to DynamoDB")
  args = parser.parse_args()

  cognito = boto3.client("cognito-idp", region_name=settings.AWS_REGION)

  migrated = 0
  skipped = 0
  dropped_entries: list[tuple[str, str]] = []

  for world in WorldMeta.query(
    WorldMeta.pk(""),
    WorldMeta.SK.begins_with("META"),
    scan_index_forward=False,
  ):
    pass

  print("Scanning all WorldMeta items via full table scan...")
  for world in WorldMeta.scan():
    if world.SK != "META":
      continue
    shared = list(world.shared_with or [])
    if not shared:
      continue

    looks_like_email = any("@" in str(entry) for entry in shared)
    if not looks_like_email:
      skipped += 1
      continue

    world_id = str(world.id)
    print(f"\nWorld {world_id}: {len(shared)} shared_with entries")

    new_shared: list[str] = []
    for entry in shared:
      entry_str = str(entry)
      if "@" not in entry_str:
        new_shared.append(entry_str)
        continue

      sub = _resolve_email_to_sub(cognito, entry_str)
      if sub:
        print(f"  {entry_str} -> {sub}")
        new_shared.append(sub)
      else:
        print(f"  {entry_str} -> DROPPED (no Cognito account)")
        dropped_entries.append((world_id, entry_str))

      time.sleep(0.05)

    if new_shared != [str(e) for e in shared]:
      if args.dry_run:
        print(f"  [DRY RUN] Would update shared_with to {new_shared}")
      else:
        world.shared_with = new_shared
        world.save()
        print(f"  Updated shared_with -> {new_shared}")
      migrated += 1
    else:
      skipped += 1

  print(f"\n{'=' * 60}")
  print(f"Migration complete: {migrated} worlds updated, {skipped} skipped")
  if dropped_entries:
    print(f"\nDropped entries ({len(dropped_entries)}):")
    for wid, email in dropped_entries:
      print(f"  World {wid}: {email}")


if __name__ == "__main__":
  main()
