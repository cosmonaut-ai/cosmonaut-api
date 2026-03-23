"""Post-migration validation for Phase 3 session backfill.

Verifies data integrity across WorldSession, SessionMembership, NodeSession,
UserProgress, and StoryNode entities. Reports errors but makes no writes.

Usage:
  python -m scripts.validate_migration --env dev
  python -m scripts.validate_migration --env prod

IMPORTANT: --env must be parsed and DYNAMODB_TABLE_NAME set before any app.*
imports, because PynamoDB binds Meta.table_name at class-definition time.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field

# ── Parse args and set env BEFORE importing app modules ──────────────────────


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Validate Phase 3 session migration")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  return parser.parse_args()


_cli_args = _parse_args()
os.environ["DYNAMODB_TABLE_NAME"] = f"cosmonaut-{_cli_args.env}"
os.environ["ENV"] = _cli_args.env

from app.models.entities.session_membership import SessionMembership  # noqa: E402
from app.models.entities.story_node import StoryNode  # noqa: E402
from app.models.entities.user_progress import UserProgress  # noqa: E402
from app.models.entities.world_meta import WorldMeta  # noqa: E402
from app.services.sessions import get_node_session, get_session, get_session_for_user  # noqa: E402

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  datefmt="%H:%M:%S",
)
log = logging.getLogger("validate")


@dataclass
class ValidationResult:
  worlds_scanned: int = 0
  worlds_with_sessions: int = 0
  worlds_missing_sessions: int = 0

  memberships_checked: int = 0
  memberships_missing: int = 0

  progress_scanned: int = 0
  progress_matched: int = 0
  progress_missing_membership: int = 0
  progress_node_mismatch: int = 0

  nodes_scanned: int = 0
  nodes_with_index: int = 0
  nodes_missing_index: int = 0
  nodes_with_remaining_customs: int = 0

  source_session_nodes: int = 0
  source_session_valid: int = 0
  source_session_invalid: int = 0

  author_node_sessions_expected: int = 0
  author_node_sessions_found: int = 0
  author_node_sessions_missing: int = 0

  choice_state_checks: int = 0
  choice_state_mismatches: int = 0

  # Phase 4 field checks
  node_sessions_missing_parent_id: int = 0
  memberships_missing_root_node_id: int = 0
  memberships_missing_family_friendly: int = 0

  errors: list[str] = field(default_factory=list)

  @property
  def passed(self) -> bool:
    return (
      self.worlds_missing_sessions == 0
      and self.memberships_missing == 0
      and self.progress_missing_membership == 0
      and self.progress_node_mismatch == 0
      and self.nodes_missing_index == 0
      and self.nodes_with_remaining_customs == 0
      and self.source_session_invalid == 0
      and self.author_node_sessions_missing == 0
      and self.choice_state_mismatches == 0
      and self.node_sessions_missing_parent_id == 0
      and self.memberships_missing_root_node_id == 0
      and self.memberships_missing_family_friendly == 0
      and len(self.errors) == 0
    )


def check_1_world_sessions(result: ValidationResult) -> dict[str, tuple[str, str]]:
  """Check 1: Every completed WorldMeta has a WorldSession.

  Returns a mapping of world_id -> (session_id, author_id) for use by later checks.
  """
  log.info("Check 1: Every completed WorldMeta has a WorldSession")
  world_sessions: dict[str, tuple[str, str]] = {}

  worlds = WorldMeta.scan(
    (WorldMeta.SK == "META") & WorldMeta.PK.startswith("WORLD#") & (WorldMeta.generation_status == "completed"),
  )

  for world in worlds:
    result.worlds_scanned += 1
    world_id = str(world.id)
    author_id = str(world.author_id) if world.author_id else None
    if not author_id:
      continue

    session = get_session_for_user(author_id, world_id)
    if session:
      result.worlds_with_sessions += 1
      world_sessions[world_id] = (str(session.id), author_id)
    else:
      result.worlds_missing_sessions += 1
      result.errors.append(f"World {world_id} (author={author_id}) has no WorldSession")

  log.info(
    "  %d worlds scanned, %d with sessions, %d missing",
    result.worlds_scanned,
    result.worlds_with_sessions,
    result.worlds_missing_sessions,
  )
  return world_sessions


def check_2_membership_consistency(result: ValidationResult, world_sessions: dict[str, tuple[str, str]]) -> None:
  """Check 2: Every WorldSession member has a SessionMembership record."""
  log.info("Check 2: SessionMembership consistency")

  for world_id, (session_id, _author_id) in world_sessions.items():
    try:
      session = get_session(session_id)
      members = [str(m) for m in session.members] if session.members else []

      for member_id in members:
        result.memberships_checked += 1
        try:
          SessionMembership.get(
            SessionMembership.pk(member_id),
            SessionMembership.sk(world_id, session_id),
          )
        except SessionMembership.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
          result.memberships_missing += 1
          result.errors.append(
            f"Missing SessionMembership for user {member_id} in session {session_id} (world {world_id})"
          )
    except Exception as e:
      result.errors.append(f"Error checking memberships for session {session_id}: {e}")

  log.info("  %d memberships checked, %d missing", result.memberships_checked, result.memberships_missing)


def check_3_progress_alignment(result: ValidationResult, world_sessions: dict[str, tuple[str, str]]) -> None:
  """Check 3: Every UserProgress record has a matching SessionMembership."""
  log.info("Check 3: UserProgress -> SessionMembership alignment")

  progress_records = UserProgress.scan(UserProgress.SK.startswith("PROGRESS#"))

  for progress in progress_records:
    result.progress_scanned += 1
    user_id = str(progress.user_id)
    world_id = str(progress.world_id)
    current_node = str(progress.current_node_id)

    if world_id not in world_sessions:
      continue

    session = get_session_for_user(user_id, world_id)
    if not session:
      result.progress_missing_membership += 1
      result.errors.append(f"UserProgress for user {user_id} in world {world_id} has no session")
      continue

    session_id = str(session.id)
    try:
      membership = SessionMembership.get(
        SessionMembership.pk(user_id),
        SessionMembership.sk(world_id, session_id),
      )
      if str(membership.last_visited_node_id) == current_node:
        result.progress_matched += 1
      else:
        result.progress_node_mismatch += 1
        result.errors.append(
          f"Progress mismatch for user {user_id} in world {world_id}: "
          f"UserProgress={current_node}, SessionMembership={membership.last_visited_node_id}"
        )
    except SessionMembership.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
      result.progress_missing_membership += 1
      result.errors.append(f"UserProgress for user {user_id} in world {world_id} has no SessionMembership")

  log.info(
    "  %d records, %d matched, %d missing membership, %d node mismatches",
    result.progress_scanned,
    result.progress_matched,
    result.progress_missing_membership,
    result.progress_node_mismatch,
  )


def check_4_5_6_story_nodes(result: ValidationResult, world_sessions: dict[str, tuple[str, str]]) -> None:
  """Checks 4-6: StoryNode integrity (next_custom_choice_index, no customs, source_session_id)."""
  log.info("Check 4: next_custom_choice_index set on all StoryNodes")
  log.info("Check 5: No custom choices remain on StoryNode")
  log.info("Check 6: source_session_id references valid sessions")

  for world_id in world_sessions:
    nodes = StoryNode.query(StoryNode.pk(world_id), StoryNode.SK.startswith("NODE#"))

    for node in nodes:
      result.nodes_scanned += 1

      # Check 4: next_custom_choice_index
      if node.next_custom_choice_index is not None:
        result.nodes_with_index += 1
      else:
        result.nodes_missing_index += 1
        result.errors.append(f"Node {node.id} in world {world_id} missing next_custom_choice_index")

      # Check 5: no remaining custom choices
      if node.choices:
        customs = [c for c in node.choices if c.is_custom]
        if customs:
          result.nodes_with_remaining_customs += 1
          result.errors.append(f"Node {node.id} in world {world_id} still has {len(customs)} custom choice(s)")

      # Check 6: source_session_id validity
      if node.source_session_id:
        result.source_session_nodes += 1
        try:
          get_session(str(node.source_session_id))
          result.source_session_valid += 1
        except Exception:
          result.source_session_invalid += 1
          result.errors.append(
            f"Node {node.id} has source_session_id={node.source_session_id} referencing non-existent session"
          )

  log.info("  %d nodes scanned", result.nodes_scanned)
  log.info("  Check 4: %d with index, %d missing", result.nodes_with_index, result.nodes_missing_index)
  log.info("  Check 5: %d with remaining customs", result.nodes_with_remaining_customs)
  log.info(
    "  Check 6: %d source_session refs (%d valid, %d invalid)",
    result.source_session_nodes,
    result.source_session_valid,
    result.source_session_invalid,
  )


def check_7_8_node_session_coverage(result: ValidationResult, world_sessions: dict[str, tuple[str, str]]) -> None:
  """Checks 7-8: NodeSession coverage and choice state consistency for authors."""
  log.info("Check 7: Author NodeSession coverage")
  log.info("Check 8: NodeSession choice state consistency")

  for world_id, (session_id, _author_id) in world_sessions.items():
    nodes = list(StoryNode.query(StoryNode.pk(world_id), StoryNode.SK.startswith("NODE#")))

    for node in nodes:
      result.author_node_sessions_expected += 1
      node_id = str(node.id)

      ns = get_node_session(session_id, node_id)
      if not ns:
        result.author_node_sessions_missing += 1
        result.errors.append(
          f"Missing NodeSession for node {node_id} in author session {session_id} (world {world_id})"
        )
        continue
      result.author_node_sessions_found += 1

      # Check 8: base_choice_states count matches base choices on StoryNode
      base_choices = [c for c in (node.choices or []) if not c.is_custom]
      result.choice_state_checks += 1
      if len(ns.base_choice_states) != len(base_choices):
        result.choice_state_mismatches += 1
        result.errors.append(
          f"NodeSession for {node_id} has {len(ns.base_choice_states)} base_choice_states "
          f"but StoryNode has {len(base_choices)} base choices"
        )

      # Check 9: NodeSession.parent_id populated for non-root nodes
      if node_id != "0" and ns.parent_id is None:
        result.node_sessions_missing_parent_id += 1
        result.errors.append(f"NodeSession {node_id} in session {session_id} missing parent_id")

  log.info(
    "  Check 7: %d expected, %d found, %d missing",
    result.author_node_sessions_expected,
    result.author_node_sessions_found,
    result.author_node_sessions_missing,
  )
  log.info(
    "  Check 8: %d checked, %d mismatches",
    result.choice_state_checks,
    result.choice_state_mismatches,
  )
  log.info("  Check 9: %d NodeSessions missing parent_id", result.node_sessions_missing_parent_id)


def check_10_membership_metadata(result: ValidationResult, world_sessions: dict[str, tuple[str, str]]) -> None:
  """Check 10: SessionMembership has Phase 4 denormalized fields populated."""
  log.info("Check 10: SessionMembership Phase 4 metadata (root_node_id, family_friendly)")

  for world_id, (session_id, author_id) in world_sessions.items():
    try:
      membership = SessionMembership.get(
        SessionMembership.pk(author_id),
        SessionMembership.sk(world_id, session_id),
      )
      if membership.root_node_id is None:
        result.memberships_missing_root_node_id += 1
        result.errors.append(f"SessionMembership (user={author_id}, world={world_id}) missing root_node_id")
      if membership.family_friendly is None:
        result.memberships_missing_family_friendly += 1
        result.errors.append(f"SessionMembership (user={author_id}, world={world_id}) missing family_friendly")
    except SessionMembership.DoesNotExist:  # type: ignore[reportGeneralTypeIssues]
      pass  # Already caught by check_2

  log.info(
    "  %d missing root_node_id, %d missing family_friendly",
    result.memberships_missing_root_node_id,
    result.memberships_missing_family_friendly,
  )


def print_report(result: ValidationResult) -> None:
  log.info("")
  log.info("=" * 60)
  log.info("MIGRATION VALIDATION REPORT")
  log.info("=" * 60)
  log.info("Worlds scanned:               %d", result.worlds_scanned)
  log.info("  Sessions verified:           %d", result.worlds_with_sessions)
  log.info("  Sessions missing:            %d", result.worlds_missing_sessions)
  log.info("Memberships checked:           %d", result.memberships_checked)
  log.info("  Missing:                     %d", result.memberships_missing)
  log.info("UserProgress records:          %d", result.progress_scanned)
  log.info("  Matched:                     %d", result.progress_matched)
  log.info("  Missing membership:          %d", result.progress_missing_membership)
  log.info("  Node mismatch:               %d", result.progress_node_mismatch)
  log.info("StoryNodes scanned:            %d", result.nodes_scanned)
  log.info("  next_custom_choice_index:    %d set / %d missing", result.nodes_with_index, result.nodes_missing_index)
  log.info("  Remaining custom choices:    %d", result.nodes_with_remaining_customs)
  log.info(
    "  source_session_id refs:      %d (%d valid / %d invalid)",
    result.source_session_nodes,
    result.source_session_valid,
    result.source_session_invalid,
  )
  log.info(
    "Author NodeSessions:           %d expected / %d found / %d missing",
    result.author_node_sessions_expected,
    result.author_node_sessions_found,
    result.author_node_sessions_missing,
  )
  log.info(
    "Choice state consistency:      %d checked / %d mismatches",
    result.choice_state_checks,
    result.choice_state_mismatches,
  )
  log.info("Phase 4 fields:")
  log.info("  NodeSessions missing parent_id:       %d", result.node_sessions_missing_parent_id)
  log.info("  Memberships missing root_node_id:     %d", result.memberships_missing_root_node_id)
  log.info("  Memberships missing family_friendly:  %d", result.memberships_missing_family_friendly)

  if result.errors:
    log.info("")
    log.error("ERRORS (%d):", len(result.errors))
    for err in result.errors[:50]:
      log.error("  - %s", err)
    if len(result.errors) > 50:
      log.error("  ... and %d more", len(result.errors) - 50)

  log.info("")
  if result.passed:
    log.info("RESULT: PASS")
  else:
    log.error("RESULT: FAIL")
  log.info("=" * 60)


def main() -> None:
  log.info("Validating table: %s", os.environ["DYNAMODB_TABLE_NAME"])

  result = ValidationResult()

  world_sessions = check_1_world_sessions(result)
  check_2_membership_consistency(result, world_sessions)
  check_3_progress_alignment(result, world_sessions)
  check_4_5_6_story_nodes(result, world_sessions)
  check_7_8_node_session_coverage(result, world_sessions)
  check_10_membership_metadata(result, world_sessions)

  print_report(result)

  if not result.passed:
    sys.exit(1)


if __name__ == "__main__":
  main()
