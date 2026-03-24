"""Phase 3 migration: backfill session entities for all existing data.

Three sequential passes, each idempotent:
  Pass 1 – Create WorldSession + SessionMembership for every completed world's author
  Pass 2 – Migrate UserProgress records into sessions (author progress + non-author sessions)
  Pass 3 – Create NodeSessions, set next_custom_choice_index, migrate & remove custom choices

Usage:
  python -m scripts.migrate_to_sessions --env dev --dry-run
  python -m scripts.migrate_to_sessions --env dev --step all
  python -m scripts.migrate_to_sessions --env prod --resume-from <world_id> --step 3

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
# PynamoDB models read Settings().DYNAMODB_TABLE_NAME at import time.
# The .envrc may already have set it to the dev value, so we must override
# before any app.* import triggers Settings instantiation.


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Migrate existing data to session entities (Phase 3)")
  parser.add_argument("--env", choices=["dev", "prod"], required=True, help="Target environment")
  parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DynamoDB")
  parser.add_argument("--resume-from", type=str, default=None, help="Resume Pass 3 from a specific world_id")
  parser.add_argument(
    "--step",
    choices=["1", "2", "3", "all"],
    default="all",
    help="Run a specific step (1=sessions, 2=progress, 3=nodes) or all",
  )
  return parser.parse_args()


_cli_args = _parse_args()
os.environ["DYNAMODB_TABLE_NAME"] = f"cosmonaut-{_cli_args.env}"
os.environ["ENV"] = _cli_args.env

from app.models.entities.user_progress import UserProgress  # noqa: E402

from app.models.entities.node_session import BaseChoiceStateMap, CustomChoiceMap, NodeSession  # noqa: E402
from app.models.entities.story_node import StoryNode  # noqa: E402
from app.models.entities.world_meta import WorldMeta  # noqa: E402
from app.services.sessions import (  # noqa: E402
  create_session,
  find_or_create_session,
  get_node_session,
  get_session_for_user,
  update_session_progress,
)
from app.utils import base52_to_number  # noqa: E402

logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(levelname)s] %(message)s",
  datefmt="%H:%M:%S",
)
log = logging.getLogger("migrate")


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class WorldInfo:
  world_id: str
  author_id: str
  session_id: str


@dataclass
class UserPathEntry:
  session_id: str
  user_id: str
  explored_choices: dict[str, int]  # node_id -> choice_index explored at that node


@dataclass
class MigrationState:
  worlds: dict[str, WorldInfo] = field(default_factory=dict)  # world_id -> WorldInfo
  user_sessions: dict[str, dict[str, str]] = field(default_factory=dict)  # world_id -> {user_id -> session_id}
  user_paths: dict[str, list[UserPathEntry]] = field(default_factory=dict)  # world_id -> [UserPathEntry]
  dry_run: bool = False

  # counters
  worlds_processed: int = 0
  sessions_created: int = 0
  sessions_skipped: int = 0
  progress_records: int = 0
  progress_authors_updated: int = 0
  progress_members_created: int = 0
  nodes_processed: int = 0
  node_sessions_created: int = 0
  node_sessions_skipped: int = 0
  custom_choices_migrated: int = 0
  source_session_ids_set: int = 0
  errors: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def compute_ancestors(node_id: str) -> list[str]:
  """Reproduce StoryNode.ancestors logic as a standalone function."""
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
  return ancestors


def compute_path_choices(current_node_id: str) -> dict[str, int]:
  """Compute which choice was explored at each node on the path to current_node_id.

  Returns a dict of {node_id: choice_index_explored}. The final node (current)
  has no explored choice and is included with value -1.
  """
  ancestors = compute_ancestors(current_node_id)
  path_choices: dict[str, int] = {}
  for idx in range(len(ancestors) - 1):
    parent_id = ancestors[idx]
    child_id = ancestors[idx + 1]
    suffix = child_id[len(parent_id) :]
    # Strip leading digit prefix for multi-char base52 (e.g. "2aa" -> "aa")
    if suffix and suffix[0].isdigit() and suffix[0] != "0":
      suffix = suffix[1:]
    path_choices[parent_id] = base52_to_number(suffix)
  path_choices[ancestors[-1]] = -1  # current node has no explored choice
  return path_choices


# ──────────────────────────────────────────────────────────────────────────────
# Pass 1: Create author sessions
# ──────────────────────────────────────────────────────────────────────────────


def pass1_create_author_sessions(state: MigrationState) -> None:
  log.info("=" * 60)
  log.info("PASS 1: Creating author sessions for completed worlds")
  log.info("=" * 60)

  worlds = WorldMeta.scan(
    (WorldMeta.SK == "META") & WorldMeta.PK.startswith("WORLD#") & (WorldMeta.generation_status == "completed"),
  )

  for world in worlds:
    world_id = str(world.id)
    author_id = str(world.author_id) if world.author_id else None
    if not author_id:
      log.warning("World %s has no author_id, skipping", world_id)
      continue

    try:
      existing = get_session_for_user(author_id, world_id)
      if existing:
        session_id = str(existing.id)
        state.sessions_skipped += 1
        log.debug("Session already exists for world %s (session=%s)", world_id, session_id)
      else:
        if state.dry_run:
          session_id = f"DRY-{world_id[:8]}"
          log.info("[DRY-RUN] Would create session for world %s (author=%s)", world_id, author_id)
        else:
          session = create_session(root_world_id=world_id, creator_id=author_id, members=[author_id], world=world)
          session_id = str(session.id)
          log.info("Created session %s for world %s", session_id, world_id)
        state.sessions_created += 1

      state.worlds[world_id] = WorldInfo(world_id=world_id, author_id=author_id, session_id=session_id)
      state.user_sessions.setdefault(world_id, {})[author_id] = session_id
      state.worlds_processed += 1

    except Exception as e:
      msg = f"Pass 1 error for world {world_id}: {e}"
      log.exception(msg)
      state.errors.append(msg)

  log.info(
    "Pass 1 complete: %d worlds, %d sessions created, %d skipped",
    state.worlds_processed,
    state.sessions_created,
    state.sessions_skipped,
  )


# ──────────────────────────────────────────────────────────────────────────────
# Pass 2: Migrate UserProgress
# ──────────────────────────────────────────────────────────────────────────────


def pass2_migrate_progress(state: MigrationState) -> None:
  log.info("=" * 60)
  log.info("PASS 2: Migrating UserProgress records")
  log.info("=" * 60)

  progress_records = UserProgress.scan(UserProgress.SK.startswith("PROGRESS#"))

  for progress in progress_records:
    user_id = str(progress.user_id)
    world_id = str(progress.world_id)
    current_node_id = str(progress.current_node_id)
    state.progress_records += 1

    world_info = state.worlds.get(world_id)
    if not world_info:
      log.debug("Progress for world %s not in completed worlds map, skipping", world_id)
      continue

    try:
      is_author = user_id == world_info.author_id

      if is_author:
        if state.dry_run:
          log.info("[DRY-RUN] Would update author progress for world %s (node=%s)", world_id, current_node_id)
        else:
          update_session_progress(
            session_id=world_info.session_id,
            root_world_id=world_id,
            user_id=user_id,
            node_id=current_node_id,
          )
          log.info("Updated author progress for world %s (node=%s)", world_id, current_node_id)
        state.progress_authors_updated += 1
        session_id = world_info.session_id
      else:
        existing_session = get_session_for_user(user_id, world_id)
        if existing_session:
          session_id = str(existing_session.id)
        elif state.dry_run:
          session_id = f"DRY-MEMBER-{world_id[:8]}-{user_id[:8]}"
          log.info("[DRY-RUN] Would create member session for user %s in world %s", user_id, world_id)
        else:
          world = WorldMeta.get(WorldMeta.pk(world_id), WorldMeta.sk())
          new_session = find_or_create_session(world_id, user_id, world=world)
          session_id = str(new_session.id)
          log.info("Created member session %s for user %s in world %s", session_id, user_id, world_id)

        if not state.dry_run:
          update_session_progress(
            session_id=session_id,
            root_world_id=world_id,
            user_id=user_id,
            node_id=current_node_id,
          )
        state.progress_members_created += 1

      state.user_sessions.setdefault(world_id, {})[user_id] = session_id

      path_choices = compute_path_choices(current_node_id)
      state.user_paths.setdefault(world_id, []).append(
        UserPathEntry(session_id=session_id, user_id=user_id, explored_choices=path_choices)
      )

    except Exception as e:
      msg = f"Pass 2 error for user {user_id} in world {world_id}: {e}"
      log.exception(msg)
      state.errors.append(msg)

  log.info(
    "Pass 2 complete: %d records, %d author updates, %d member sessions",
    state.progress_records,
    state.progress_authors_updated,
    state.progress_members_created,
  )


# ──────────────────────────────────────────────────────────────────────────────
# Pass 3: Process StoryNodes (NodeSessions + custom choices)
# ──────────────────────────────────────────────────────────────────────────────


def _create_author_node_session(
  session_id: str,
  node: StoryNode,
  world_id: str,
  base_choices: list,
  custom_choices: list,
  state: MigrationState,
) -> None:
  """Create a NodeSession for the author's session with full choice state."""
  existing = get_node_session(session_id, str(node.id))
  if existing:
    state.node_sessions_skipped += 1
    return

  base_choice_states = [BaseChoiceStateMap(is_explored=bool(c.is_created)) for c in base_choices]
  custom_choice_maps = [
    CustomChoiceMap(
      label=str(c.label),
      target_node_id=str(c.target),
      is_explored=bool(c.is_created),
      creator_id=str(c.creator) if c.creator else "",
    )
    for c in custom_choices
  ]

  if state.dry_run:
    state.node_sessions_created += 1
    return

  ns = NodeSession(
    PK=NodeSession.pk(session_id),
    SK=NodeSession.sk(str(node.id)),
    node_id=str(node.id),
    session_id=session_id,
    root_world_id=world_id,
    title=node.title,
    base_choice_states=base_choice_states,
    custom_choices=custom_choice_maps,
  )
  ns.save()
  state.node_sessions_created += 1


def _create_member_node_session(
  session_id: str,
  node_id: str,
  world_id: str,
  node: StoryNode,
  explored_choice_index: int,
  base_choices: list,
  state: MigrationState,
) -> None:
  """Create a NodeSession for a non-author member at a node on their path."""
  existing = get_node_session(session_id, node_id)
  if existing:
    state.node_sessions_skipped += 1
    return

  base_choice_states = [BaseChoiceStateMap(is_explored=False) for _ in base_choices]
  if 0 <= explored_choice_index < len(base_choice_states):
    base_choice_states[explored_choice_index].is_explored = True

  if state.dry_run:
    state.node_sessions_created += 1
    return

  ns = NodeSession(
    PK=NodeSession.pk(session_id),
    SK=NodeSession.sk(node_id),
    node_id=node_id,
    session_id=session_id,
    root_world_id=world_id,
    title=node.title,
    base_choice_states=base_choice_states,
  )
  ns.save()
  state.node_sessions_created += 1


def _migrate_custom_choices(
  node: StoryNode,
  custom_choices: list,
  world_id: str,
  state: MigrationState,
) -> None:
  """Set source_session_id on child nodes created via custom choices."""
  for choice in custom_choices:
    target_id = str(choice.target) if choice.target else None
    creator_id = str(choice.creator) if choice.creator else None
    if not target_id:
      continue

    try:
      child = StoryNode.get(StoryNode.pk(world_id), StoryNode.sk(target_id))
      if child.source_session_id:
        continue  # already migrated

      session_id = None
      if creator_id:
        session_id = state.user_sessions.get(world_id, {}).get(creator_id)
        if not session_id and not state.dry_run:
          world = WorldMeta.get(WorldMeta.pk(world_id), WorldMeta.sk())
          new_session = find_or_create_session(world_id, creator_id, world=world)
          session_id = str(new_session.id)
          state.user_sessions.setdefault(world_id, {})[creator_id] = session_id
          log.info("Created session %s for custom-choice creator %s in world %s", session_id, creator_id, world_id)

      if session_id and not state.dry_run:
        child.source_session_id = session_id
        child.save()
        state.source_session_ids_set += 1
      elif state.dry_run:
        state.source_session_ids_set += 1

    except StoryNode.DoesNotExist:
      log.warning("Custom choice target node %s not found in world %s", target_id, world_id)
    except Exception as e:
      msg = f"Error setting source_session_id on {target_id} in world {world_id}: {e}"
      log.exception(msg)
      state.errors.append(msg)


def pass3_process_nodes(state: MigrationState, resume_from: str | None = None) -> None:
  log.info("=" * 60)
  log.info("PASS 3: Processing StoryNodes (NodeSessions + custom choices)")
  log.info("=" * 60)

  sorted_world_ids = sorted(state.worlds.keys())
  resuming = resume_from is not None

  for world_id in sorted_world_ids:
    if resuming:
      if world_id < resume_from:  # type: ignore  # str comparison
        continue
      resuming = False

    world_info = state.worlds[world_id]
    author_session_id = world_info.session_id
    member_paths = state.user_paths.get(world_id, [])
    non_author_paths = [p for p in member_paths if p.user_id != world_info.author_id]

    try:
      nodes = list(StoryNode.query(StoryNode.pk(world_id), StoryNode.SK.startswith("NODE#")))
      log.info("Processing world %s: %d nodes, %d member paths", world_id, len(nodes), len(non_author_paths))

      for node in nodes:
        node_id = str(node.id)
        all_choices = list(node.choices) if node.choices else []
        base_choices = [c for c in all_choices if not c.is_custom]
        custom_choices = [c for c in all_choices if c.is_custom]

        # Step 5: Set next_custom_choice_index (BEFORE removal)
        needs_save = False
        if node.next_custom_choice_index is None:
          node.next_custom_choice_index = len(all_choices)
          needs_save = True

        # Step 4: Create author NodeSession
        if not state.dry_run:
          _create_author_node_session(author_session_id, node, world_id, base_choices, custom_choices, state)
        else:
          state.node_sessions_created += 1

        # Step 4: Create member NodeSessions (only for nodes on their path)
        for path_entry in non_author_paths:
          if node_id in path_entry.explored_choices:
            explored_idx = path_entry.explored_choices[node_id]
            _create_member_node_session(
              path_entry.session_id, node_id, world_id, node, explored_idx, base_choices, state
            )

        # Step 6: Migrate custom choices
        if custom_choices:
          _migrate_custom_choices(node, custom_choices, world_id, state)
          state.custom_choices_migrated += len(custom_choices)
          node.choices = base_choices
          needs_save = True

        if needs_save and not state.dry_run:
          node.save()

        state.nodes_processed += 1

      log.info("World %s complete: %d nodes processed", world_id, len(nodes))

    except Exception as e:
      msg = f"Pass 3 error for world {world_id}: {e}"
      log.exception(msg)
      state.errors.append(msg)

  log.info(
    "Pass 3 complete: %d nodes, %d NodeSessions created (%d skipped), "
    "%d custom choices migrated, %d source_session_ids set",
    state.nodes_processed,
    state.node_sessions_created,
    state.node_sessions_skipped,
    state.custom_choices_migrated,
    state.source_session_ids_set,
  )


# ──────────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────────


def print_report(state: MigrationState) -> None:
  log.info("")
  log.info("=" * 60)
  log.info("MIGRATION REPORT%s", " (DRY-RUN)" if state.dry_run else "")
  log.info("=" * 60)
  log.info("Pass 1 - Author Sessions:")
  log.info("  Worlds processed:      %d", state.worlds_processed)
  log.info("  Sessions created:      %d", state.sessions_created)
  log.info("  Sessions skipped:      %d", state.sessions_skipped)
  log.info("Pass 2 - UserProgress:")
  log.info("  Records scanned:       %d", state.progress_records)
  log.info("  Author updates:        %d", state.progress_authors_updated)
  log.info("  Member sessions:       %d", state.progress_members_created)
  log.info("Pass 3 - StoryNodes:")
  log.info("  Nodes processed:       %d", state.nodes_processed)
  log.info("  NodeSessions created:  %d", state.node_sessions_created)
  log.info("  NodeSessions skipped:  %d", state.node_sessions_skipped)
  log.info("  Custom choices moved:  %d", state.custom_choices_migrated)
  log.info("  source_session_ids:    %d", state.source_session_ids_set)
  if state.errors:
    log.error("ERRORS: %d", len(state.errors))
    for err in state.errors:
      log.error("  - %s", err)
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

  state = MigrationState(dry_run=args.dry_run)
  start = time.time()

  if args.step in ("1", "all"):
    pass1_create_author_sessions(state)

  if args.step in ("2", "all"):
    if args.step == "2" and not state.worlds:
      log.info("Pass 2 requires Pass 1 data; running Pass 1 first...")
      pass1_create_author_sessions(state)
    pass2_migrate_progress(state)

  if args.step in ("3", "all"):
    if args.step == "3" and not state.worlds:
      log.info("Pass 3 requires Pass 1+2 data; running Passes 1-2 first...")
      pass1_create_author_sessions(state)
      pass2_migrate_progress(state)
    pass3_process_nodes(state, resume_from=args.resume_from)

  elapsed = time.time() - start
  log.info("Total elapsed: %.1fs", elapsed)
  print_report(state)

  if state.errors:
    sys.exit(1)


if __name__ == "__main__":
  main()
