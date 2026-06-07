"""Admin-only endpoints for moderation and dashboard data.

The router is secured at registration time in ``app.main``.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status

from app.api.mappers import membership_to_session_summary_dto, node_session_to_list_dto
from app.core.errors import NotFoundError
from app.core.observability import logger
from app.core.security import User, get_current_user
from app.models.dtos.admin import (
  AdminCognitoUserDTO,
  AdminFeaturedOrderUpdateRequest,
  AdminFeaturedUpdateRequest,
  AdminSessionDTO,
  AdminSoundtrackMatchRequest,
  AdminSoundtrackMatchResponse,
  AdminTierUpdateRequest,
  AdminTierUpdateResponse,
  AdminUserGroupsResponse,
  AdminUserUsageDTO,
  AdminWorldContextPreviewRequest,
  AdminWorldContextPreviewResponse,
  AdminWorldMetaDTO,
)
from app.models.dtos.base import PaginatedResponse
from app.models.dtos.playlist import PlaylistDTO
from app.models.dtos.session import WorldSessionSummaryDTO
from app.models.dtos.story_node import StoryNodeDTO
from app.services import admin as admin_service
from app.services import admin_diagnostics as admin_diagnostics_service
from app.services import cognito as cognito_service
from app.services import sessions as sessions_service
from app.services.account import delete_account
from app.services.sessions import delete_sessions_for_world
from app.services.worlds import hard_delete_orphaned_world

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
  "/users",
  response_model=PaginatedResponse[AdminCognitoUserDTO],
  summary="List app users (admin)",
)
async def admin_list_users(
  email_prefix: str | None = Query(None, description="Optional app-user email prefix filter"),
  limit: int = Query(60, ge=1, le=60, description="Maximum number of users to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[AdminCognitoUserDTO]:
  """List app-created users for the admin user table."""
  users, next_cursor = admin_service.list_app_users(email_prefix=email_prefix, limit=limit, cursor=cursor)
  return PaginatedResponse(items=users, next_cursor=next_cursor)


@router.get(
  "/users/{user_id}",
  response_model=AdminCognitoUserDTO,
  summary="Get app user metadata (admin)",
)
async def admin_get_user(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user"),
) -> AdminCognitoUserDTO:
  """Return app-user directory metadata for a user identified by sub."""
  return admin_service.get_app_user_by_sub(user_id)


@router.get(
  "/users/{user_id}/usage",
  response_model=AdminUserUsageDTO,
  summary="Get user usage record (admin)",
)
async def admin_get_user_usage(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user"),
) -> AdminUserUsageDTO:
  """Return DynamoDB usage/subscription data for an arbitrary user."""
  return admin_service.get_user_usage(user_id)


@router.get(
  "/users/{user_id}/groups",
  response_model=AdminUserGroupsResponse,
  summary="List user Cognito groups (admin)",
)
async def admin_get_user_groups(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user"),
) -> AdminUserGroupsResponse:
  """Return Cognito group names for a user."""
  return AdminUserGroupsResponse(groups=admin_service.list_cognito_groups_for_user(user_id))


@router.get(
  "/users/{user_id}/worlds",
  response_model=PaginatedResponse[AdminWorldMetaDTO],
  summary="List worlds authored by a user (admin)",
)
async def admin_list_user_worlds(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user"),
  limit: int = Query(50, ge=1, le=200, description="Maximum number of worlds to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[AdminWorldMetaDTO]:
  """List worlds authored by a user, independent of session membership."""
  worlds, next_cursor = admin_service.list_user_worlds(user_id, limit=limit, cursor=cursor)
  return PaginatedResponse(items=admin_service.worlds_to_admin_dtos(worlds), next_cursor=next_cursor)


@router.get(
  "/users/{user_id}/sessions",
  response_model=PaginatedResponse[WorldSessionSummaryDTO],
  summary="List sessions for a user (admin)",
)
async def admin_list_user_sessions(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user"),
  limit: int = Query(50, ge=1, le=100, description="Maximum number of sessions to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[WorldSessionSummaryDTO]:
  """List playthrough sessions for a user through the user-session index."""
  memberships, next_cursor = sessions_service.list_user_sessions(user_id, limit=limit, cursor=cursor)
  return PaginatedResponse(
    items=[membership_to_session_summary_dto(membership) for membership in memberships],
    next_cursor=next_cursor,
  )


@router.patch(
  "/users/{user_id}/tier",
  response_model=AdminTierUpdateResponse,
  summary="Update a user's subscription tier (admin)",
)
async def admin_update_user_tier(
  payload: AdminTierUpdateRequest = Body(...),
  user_id: str = Path(..., description="Cognito sub (UUID) of the user"),
  current_user: User = Depends(get_current_user),
) -> AdminTierUpdateResponse:
  """Update DynamoDB tier state and best-effort sync Cognito custom:tier."""
  logger.info("Admin %s updating tier for user %s to %s", current_user.id, user_id, payload.tier)
  warning = admin_service.update_user_tier(user_id, payload.tier)
  return AdminTierUpdateResponse(success=True, tier=payload.tier, warning=warning)


@router.get(
  "/worlds",
  response_model=PaginatedResponse[AdminWorldMetaDTO],
  summary="List recent worlds (admin)",
)
async def admin_list_worlds(
  limit: int = Query(50, ge=1, le=200, description="Maximum number of worlds to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
  search: str | None = Query(None, description="Optional exact world ID or author ID lookup"),
) -> PaginatedResponse[AdminWorldMetaDTO]:
  """List recent indexed root worlds for the admin worlds table."""
  worlds, next_cursor = admin_service.list_all_worlds(limit=limit, cursor=cursor, search=search)
  return PaginatedResponse(items=admin_service.worlds_to_admin_dtos(worlds), next_cursor=next_cursor)


@router.get(
  "/worlds/{world_id}",
  response_model=AdminWorldMetaDTO,
  summary="Get world metadata (admin)",
)
async def admin_get_world(
  world_id: str = Path(..., description="World ID to fetch"),
) -> AdminWorldMetaDTO:
  """Fetch world metadata without user/session access checks."""
  return admin_service.world_to_admin_dto(admin_service.get_world(world_id))


@router.get(
  "/worlds/{world_id}/nodes",
  response_model=PaginatedResponse[StoryNodeDTO],
  summary="List world nodes (admin)",
)
async def admin_list_world_nodes(
  world_id: str = Path(..., description="World ID to inspect"),
  limit: int = Query(100, ge=1, le=500, description="Maximum number of nodes to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[StoryNodeDTO]:
  """List root story nodes for a world without session filtering."""
  nodes, next_cursor = admin_service.list_world_nodes(world_id, limit=limit, cursor=cursor)
  return PaginatedResponse(items=[node.to_dto() for node in nodes], next_cursor=next_cursor)


@router.get(
  "/worlds/{world_id}/sessions",
  response_model=PaginatedResponse[AdminSessionDTO],
  summary="List sessions for a world (admin)",
)
async def admin_list_world_sessions(
  world_id: str = Path(..., description="World ID to inspect"),
  limit: int = Query(50, ge=1, le=100, description="Maximum number of sessions to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[AdminSessionDTO]:
  """List playthrough sessions for a world through the world-session index."""
  world = admin_service.get_world(world_id)
  sessions, next_cursor = admin_service.list_world_sessions(world_id, limit=limit, cursor=cursor)
  return PaginatedResponse(items=admin_service.sessions_to_admin_dtos(sessions, world=world), next_cursor=next_cursor)


@router.get(
  "/playlists/{playlist_id}",
  response_model=PlaylistDTO,
  summary="Get soundtrack playlist metadata (admin)",
)
async def admin_get_playlist(
  playlist_id: str = Path(..., description="Playlist ID to inspect"),
) -> PlaylistDTO:
  """Fetch a playlist by exact ID for admin inspection."""
  return admin_service.get_playlist(playlist_id).to_dto()


@router.get(
  "/sessions/{session_id}/nodes",
  response_model=PaginatedResponse[StoryNodeDTO],
  summary="List session node overlays (admin)",
)
async def admin_list_session_nodes(
  session_id: str = Path(..., description="Session ID to inspect"),
  limit: int = Query(100, ge=1, le=500, description="Maximum number of node overlays to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[StoryNodeDTO]:
  """List per-session node overlays for a playthrough."""
  session = admin_service.get_session(session_id)
  nodes, next_cursor = sessions_service.list_node_sessions(session_id, limit=limit, cursor=cursor)
  return PaginatedResponse(
    items=[node_session_to_list_dto(node, str(session.root_world_id)) for node in nodes],
    next_cursor=next_cursor,
  )


@router.get(
  "/sessions/{session_id}",
  response_model=AdminSessionDTO,
  summary="Get world session metadata (admin)",
)
async def admin_get_session(
  session_id: str = Path(..., description="Session ID to inspect"),
) -> AdminSessionDTO:
  """Fetch a world session by exact ID for admin inspection."""
  return admin_service.session_to_admin_dto(admin_service.get_session(session_id))


@router.get(
  "/featured",
  response_model=PaginatedResponse[AdminWorldMetaDTO],
  summary="List featured worlds (admin)",
)
async def admin_list_featured_worlds(
  limit: int = Query(50, ge=1, le=200, description="Maximum number of featured worlds to return"),
  cursor: str | None = Query(None, description="Opaque pagination cursor from a previous response"),
) -> PaginatedResponse[AdminWorldMetaDTO]:
  """List featured worlds for admin management, including non-public worlds."""
  worlds, next_cursor = admin_service.list_featured_worlds(limit=limit, cursor=cursor)
  return PaginatedResponse(items=admin_service.worlds_to_admin_dtos(worlds), next_cursor=next_cursor)


@router.post(
  "/worlds/{world_id}/featured",
  response_model=AdminWorldMetaDTO,
  summary="Promote a world to featured (admin)",
)
async def admin_promote_world_to_featured(
  payload: AdminFeaturedUpdateRequest = Body(default=AdminFeaturedUpdateRequest()),
  world_id: str = Path(..., description="World ID to promote"),
  current_user: User = Depends(get_current_user),
) -> AdminWorldMetaDTO:
  """Promote a world to the featured list, appending by default."""
  logger.info("Admin %s promoting world %s to featured", current_user.id, world_id)
  return admin_service.world_to_admin_dto(admin_service.promote_world_to_featured(world_id, order=payload.order))


@router.delete(
  "/worlds/{world_id}/featured",
  response_model=AdminWorldMetaDTO,
  summary="Remove a world from featured (admin)",
)
async def admin_remove_world_from_featured(
  world_id: str = Path(..., description="World ID to remove from featured"),
  current_user: User = Depends(get_current_user),
) -> AdminWorldMetaDTO:
  """Remove a world from the featured list."""
  logger.info("Admin %s removing world %s from featured", current_user.id, world_id)
  return admin_service.world_to_admin_dto(admin_service.remove_world_from_featured(world_id))


@router.patch(
  "/featured/order",
  response_model=list[AdminWorldMetaDTO],
  summary="Update featured world ordering (admin)",
)
async def admin_update_featured_order(
  payload: AdminFeaturedOrderUpdateRequest = Body(...),
  current_user: User = Depends(get_current_user),
) -> list[AdminWorldMetaDTO]:
  """Update ordering for one or more featured worlds."""
  logger.info("Admin %s updating featured order for %d worlds", current_user.id, len(payload.items))
  worlds = admin_service.update_featured_orders(payload.items)
  return admin_service.worlds_to_admin_dtos(worlds)


@router.post(
  "/diagnostics/soundtrack-matches",
  response_model=AdminSoundtrackMatchResponse,
  summary="Preview playlist soundtrack matching (admin)",
)
async def admin_preview_soundtrack_matches(
  payload: AdminSoundtrackMatchRequest = Body(...),
  current_user: User = Depends(get_current_user),
) -> AdminSoundtrackMatchResponse:
  """Preview active soundtrack matches for arbitrary query text."""
  logger.info("Admin %s previewing soundtrack matches", current_user.id)
  return admin_diagnostics_service.preview_soundtrack_matches(payload)


@router.post(
  "/diagnostics/world-context",
  response_model=AdminWorldContextPreviewResponse,
  summary="Preview world context retrieval (admin)",
)
async def admin_preview_world_context(
  payload: AdminWorldContextPreviewRequest = Body(...),
  current_user: User = Depends(get_current_user),
) -> AdminWorldContextPreviewResponse:
  """Preview the world facts, branch facts, and similar nodes used for generation context."""
  logger.info(
    "Admin %s previewing world context for world %s node %s",
    current_user.id,
    payload.world_id,
    payload.node_id,
  )
  return admin_diagnostics_service.preview_world_context(payload)


@router.delete(
  "/worlds/{world_id}",
  status_code=status.HTTP_204_NO_CONTENT,
  summary="Force-delete a world (admin)",
)
async def admin_delete_world(
  world_id: str = Path(..., description="World ID to permanently delete"),
  current_user: User = Depends(get_current_user),
) -> None:
  """Hard-delete a world regardless of active sessions.

  Removes all sessions first, then deletes the world metadata, story nodes,
  Pinecone vectors, and S3 objects.
  """
  logger.info("Admin %s deleting world %s", current_user.id, world_id)
  delete_sessions_for_world(world_id)
  hard_delete_orphaned_world(world_id)


@router.delete(
  "/users/{user_id}",
  status_code=status.HTTP_200_OK,
  summary="Permanently delete a user account (admin)",
)
async def admin_delete_account(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user to delete"),
  current_user: User = Depends(get_current_user),
) -> dict[str, str]:
  """Full account deletion cascade: Stripe, worlds, sessions, Cognito.

  Reuses the same ``delete_account`` service that powers the self-service
  DELETE /auth/account endpoint.
  """
  logger.info("Admin %s deleting account for user %s", current_user.id, user_id)

  try:
    app_user = admin_service.get_app_user_by_sub(user_id)
    email = app_user.email
    cognito_username = app_user.cognito_username
  except NotFoundError:
    email = ""
    cognito_username = None

  if not email:
    email, _name = cognito_service.get_user_contact_info(user_id)
  if not email:
    logger.warning("Could not resolve email for user %s; proceeding without tombstone", user_id)

  # Resolve the Cognito username needed by the deletion cascade.
  if not cognito_username:
    client = cognito_service.get_cognito_client()
    cognito_username = cognito_service._resolve_cognito_username(client, user_id)
  if not cognito_username:
    raise HTTPException(status_code=404, detail=f"Cognito user not found for sub {user_id}")

  await delete_account(user_id=user_id, cognito_username=cognito_username, email=email or None)
  return {"status": "deleted"}


@router.post(
  "/users/{user_id}/ban",
  status_code=status.HTTP_200_OK,
  summary="Ban a user (disable Cognito account)",
)
async def admin_ban_user(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user to ban"),
  current_user: User = Depends(get_current_user),
) -> dict[str, str | bool]:
  """Disable the user in Cognito and invalidate all active sessions."""
  logger.info("Admin %s banning user %s", current_user.id, user_id)
  try:
    sessions_revoked = cognito_service.ban_user(user_id)
  except ValueError as e:
    raise HTTPException(status_code=404, detail=str(e)) from None
  except RuntimeError as e:
    raise HTTPException(status_code=502, detail=str(e)) from None
  admin_service.update_user_cognito_status(user_id, enabled=False, status="DISABLED")
  return {"status": "banned", "sessions_revoked": sessions_revoked}


@router.post(
  "/users/{user_id}/unban",
  status_code=status.HTTP_200_OK,
  summary="Unban a user (re-enable Cognito account)",
)
async def admin_unban_user(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user to unban"),
  current_user: User = Depends(get_current_user),
) -> dict[str, str]:
  """Re-enable a previously disabled Cognito user."""
  logger.info("Admin %s unbanning user %s", current_user.id, user_id)
  try:
    cognito_service.unban_user(user_id)
  except ValueError as e:
    raise HTTPException(status_code=404, detail=str(e)) from None
  except RuntimeError as e:
    raise HTTPException(status_code=502, detail=str(e)) from None
  admin_service.update_user_cognito_status(user_id, enabled=True, status=None)
  return {"status": "unbanned"}
