"""Admin-only endpoints for moderation: world deletion, account deletion, banning.

These endpoints are secured by a shared API key (X-Admin-Key header) rather
than JWT auth, since the admin portal is a server-side tool without user login.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.core.config import settings
from app.core.observability import logger
from app.services import cognito as cognito_service
from app.services.account import delete_account
from app.services.secret_manager import get_secret_value
from app.services.sessions import delete_sessions_for_world
from app.services.worlds import hard_delete_orphaned_world

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def require_admin(request: Request) -> None:
  """Verify the request carries a valid admin API key (resolved from SSM).

  Uses constant-time comparison to prevent timing side-channel attacks.
  """
  if not settings.ADMIN_API_KEY_PARAM:
    raise HTTPException(status_code=503, detail="Admin API key not configured")

  expected_key = get_secret_value(settings.ADMIN_API_KEY_PARAM)
  api_key = request.headers.get("x-admin-key") or ""
  if not hmac.compare_digest(api_key, expected_key):
    raise HTTPException(status_code=403, detail="Invalid or missing admin API key")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.delete(
  "/worlds/{world_id}",
  status_code=status.HTTP_204_NO_CONTENT,
  summary="Force-delete a world (admin)",
)
async def admin_delete_world(
  world_id: str = Path(..., description="World ID to permanently delete"),
) -> None:
  """Hard-delete a world regardless of active sessions.

  Removes all sessions first, then deletes the world metadata, story nodes,
  Pinecone vectors, and S3 objects.
  """
  logger.info("Admin: deleting world %s", world_id)
  delete_sessions_for_world(world_id)
  hard_delete_orphaned_world(world_id)


@router.delete(
  "/users/{user_id}",
  status_code=status.HTTP_200_OK,
  summary="Permanently delete a user account (admin)",
)
async def admin_delete_account(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user to delete"),
) -> dict[str, str]:
  """Full account deletion cascade: Stripe, worlds, sessions, Cognito.

  Reuses the same ``delete_account`` service that powers the self-service
  DELETE /auth/account endpoint.
  """
  logger.info("Admin: deleting account for user %s", user_id)

  email, _name = cognito_service.get_user_contact_info(user_id)
  if not email:
    logger.warning("Could not resolve email for user %s; proceeding without tombstone", user_id)

  # Resolve the Cognito username needed by the deletion cascade.
  client = cognito_service._get_cognito_client()
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
) -> dict[str, str]:
  """Disable the user in Cognito and invalidate all active sessions."""
  logger.info("Admin: banning user %s", user_id)
  cognito_service.ban_user(user_id)
  return {"status": "banned"}


@router.post(
  "/users/{user_id}/unban",
  status_code=status.HTTP_200_OK,
  summary="Unban a user (re-enable Cognito account)",
)
async def admin_unban_user(
  user_id: str = Path(..., description="Cognito sub (UUID) of the user to unban"),
) -> dict[str, str]:
  """Re-enable a previously disabled Cognito user."""
  logger.info("Admin: unbanning user %s", user_id)
  cognito_service.unban_user(user_id)
  return {"status": "unbanned"}
