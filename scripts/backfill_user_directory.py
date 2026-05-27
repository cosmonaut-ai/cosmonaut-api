"""Backfill the DynamoDB-backed admin user directory.

Existing ``UserRecord`` items predate the admin-directory GSI keys. This script
scans app-created users and writes ``GSI2PK/GSI2SK`` so they appear in the new
``GSI2PK = USER_PROFILES`` query used by the admin dashboard.

By default this does not enumerate Cognito users. With ``--sync-cognito`` it
does a per-record lookup by ``sub`` to copy identity display fields onto the
existing app user record (email, verification status, Cognito internal
username, status, and enabled flag).

Usage:
  python -m scripts.backfill_user_directory --env dev --dry-run
  python -m scripts.backfill_user_directory --env dev --sync-cognito
  python -m scripts.backfill_user_directory --env prod --sync-cognito --dry-run

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

_COGNITO_USER_POOL_IDS = {
  "dev": "us-east-2_GWLKBPNKF",
  "prod": "us-east-2_NE7ZsAjT9",
}


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Backfill app-user directory GSI keys on UserRecord")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DynamoDB")
  parser.add_argument(
    "--sync-cognito",
    action="store_true",
    help="Also resolve each existing app user in Cognito by sub and copy identity fields",
  )
  parser.add_argument(
    "--user-pool-id",
    help="Override the Cognito user pool ID selected from --env",
  )
  return parser.parse_args()


_cli_args = _parse_args()
os.environ["DYNAMODB_TABLE_NAME"] = f"cosmonaut-{_cli_args.env}"
os.environ["ENV"] = _cli_args.env
os.environ["COGNITO_USER_POOL_ID"] = _cli_args.user_pool_id or _COGNITO_USER_POOL_IDS[_cli_args.env]

from app.models.entities.user import UserRecord  # noqa: E402
from app.services import cognito as cognito_service  # noqa: E402

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_user_directory")


@dataclass
class CognitoSnapshot:
  email: str | None = None
  email_verified: bool | None = None
  app_username: str | None = None
  cognito_username: str | None = None
  status: str | None = None
  enabled: bool | None = None


@dataclass
class BackfillResult:
  scanned: int = 0
  updated: int = 0
  unchanged: int = 0
  no_timestamp: int = 0
  cognito_misses: int = 0
  errors: list[str] = field(default_factory=list)


def _clean(value: Any) -> str | None:
  if value is None:
    return None
  cleaned = str(value).strip()
  return cleaned or None


def _cognito_attrs(user: dict[str, Any]) -> dict[str, str]:
  attrs: dict[str, str] = {}
  raw_attrs = user.get("Attributes", [])
  if isinstance(raw_attrs, list):
    for attr in raw_attrs:
      if not isinstance(attr, dict):
        continue
      name = attr.get("Name")
      if isinstance(name, str):
        attrs[name] = str(attr.get("Value", ""))
  return attrs


def _resolve_cognito_snapshot(client: Any, user_pool_id: str, user_id: str) -> CognitoSnapshot | None:
  response = client.list_users(
    UserPoolId=user_pool_id,
    Filter=f'sub = "{user_id}"',
    Limit=1,
  )
  users = response.get("Users", [])
  if not users:
    return None

  user = users[0]
  attrs = _cognito_attrs(user)
  return CognitoSnapshot(
    email=_clean(attrs.get("email")),
    email_verified=attrs.get("email_verified") == "true" if "email_verified" in attrs else None,
    app_username=_clean(attrs.get("custom:username")),
    cognito_username=_clean(user.get("Username")),
    status=_clean(user.get("UserStatus")),
    enabled=bool(user["Enabled"]) if "Enabled" in user else None,
  )


def _record_actions(record: UserRecord, snapshot: CognitoSnapshot | None) -> list[Any]:
  ts = record.created_at or record.updated_at
  if ts is None:
    return []

  user_id = str(record.user_id)
  actions: list[Any] = []
  gsi2_pk = UserRecord.gsi2_pk_user_profiles()
  gsi2_sk = UserRecord.gsi2_sk_created(ts.isoformat(), user_id)

  if gsi2_pk != record.GSI2PK:
    actions.append(UserRecord.GSI2PK.set(gsi2_pk))
  if gsi2_sk != record.GSI2SK:
    actions.append(UserRecord.GSI2SK.set(gsi2_sk))

  if snapshot is None:
    return actions

  if snapshot.email is not None and record.email != snapshot.email:
    actions.append(UserRecord.email.set(snapshot.email))
  if snapshot.email_verified is not None and record.email_verified != snapshot.email_verified:
    actions.append(UserRecord.email_verified.set(snapshot.email_verified))
  if snapshot.app_username is not None and not record.username:
    actions.append(UserRecord.username.set(snapshot.app_username))
  if snapshot.cognito_username is not None and record.cognito_username != snapshot.cognito_username:
    actions.append(UserRecord.cognito_username.set(snapshot.cognito_username))
  if snapshot.status is not None and record.cognito_status != snapshot.status:
    actions.append(UserRecord.cognito_status.set(snapshot.status))
  if snapshot.enabled is not None and record.enabled != snapshot.enabled:
    actions.append(UserRecord.enabled.set(snapshot.enabled))

  return actions


def backfill(result: BackfillResult, *, dry_run: bool, sync_cognito: bool) -> None:
  user_pool_id = os.environ.get("COGNITO_USER_POOL_ID", "")
  if sync_cognito and not user_pool_id:
    raise RuntimeError("--sync-cognito requires COGNITO_USER_POOL_ID or --user-pool-id")

  client = cognito_service._get_cognito_client() if sync_cognito else None
  usage_record_filter = UserRecord.PK.startswith("USER#") & (UserRecord.sk() == UserRecord.SK)
  items = UserRecord.scan(filter_condition=usage_record_filter)

  for record in items:
    result.scanned += 1
    user_id = str(record.user_id)
    if not (record.created_at or record.updated_at):
      result.no_timestamp += 1
      log.warning("No usable timestamp for user %s; skipping", user_id)
      continue

    snapshot = None
    if sync_cognito:
      try:
        snapshot = _resolve_cognito_snapshot(client, user_pool_id, user_id)
      except Exception as exc:
        msg = f"Error resolving Cognito user {user_id}: {exc}"
        log.exception(msg)
        result.errors.append(msg)
        continue
      if snapshot is None:
        result.cognito_misses += 1

    actions = _record_actions(record, snapshot)
    if not actions:
      result.unchanged += 1
      continue

    try:
      if dry_run:
        log.debug("[DRY-RUN] Would update user %s with %d action(s)", user_id, len(actions))
      else:
        record.update(actions=actions, add_version_condition=False)
      result.updated += 1
    except Exception as exc:
      msg = f"Error updating user {user_id}: {exc}"
      log.exception(msg)
      result.errors.append(msg)

    if result.scanned % 500 == 0:
      log.info("  ...scanned %d UserRecords so far", result.scanned)


def print_report(result: BackfillResult, dry_run: bool) -> None:
  log.info("")
  log.info("=" * 60)
  log.info("USER DIRECTORY BACKFILL REPORT%s", " (DRY-RUN)" if dry_run else "")
  log.info("=" * 60)
  log.info("  Scanned:           %d", result.scanned)
  log.info("  Updated:           %d", result.updated)
  log.info("  Unchanged:         %d", result.unchanged)
  log.info("  Missing timestamp: %d", result.no_timestamp)
  log.info("  Cognito misses:    %d", result.cognito_misses)
  log.info("  Errors:            %d", len(result.errors))
  for err in result.errors[:50]:
    log.error("  - %s", err)
  if len(result.errors) > 50:
    log.error("  ... and %d more", len(result.errors) - 50)
  log.info("=" * 60)


def main() -> None:
  args = _cli_args
  log.info("Target table: %s", os.environ["DYNAMODB_TABLE_NAME"])
  log.info("Target Cognito user pool: %s", os.environ["COGNITO_USER_POOL_ID"])
  if args.sync_cognito:
    log.info("Cognito identity sync enabled for existing app users")
  if args.dry_run:
    log.info("*** DRY-RUN MODE: no writes will be performed ***")

  result = BackfillResult()
  start = time.time()
  backfill(result, dry_run=args.dry_run, sync_cognito=args.sync_cognito)
  log.info("Total elapsed: %.1fs", time.time() - start)
  print_report(result, args.dry_run)

  if result.errors:
    sys.exit(1)


if __name__ == "__main__":
  main()
