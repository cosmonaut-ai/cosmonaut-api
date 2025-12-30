from aws_lambda_powertools import Logger
from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.cloudfront import create_signed_cookies
from app.core.config import settings
from app.core.security import User, get_current_user
from app.services.secret_manager import get_secret_value

router = APIRouter(prefix="/auth", tags=["auth"])

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)


@router.post("/session")
async def create_session(response: Response, current_user: User = Depends(get_current_user)):
  try:
    private_key = get_secret_value(settings.CLOUDFRONT_PRIVATE_KEY_PARAM)
  except Exception:
    logger.error("Could not retrieve signing key", exc_info=True)
    raise HTTPException(status_code=500, detail="Could not retrieve signing key")

  resource_url = f"https://*{settings.COOKIE_DOMAIN}/*"
  cookies = create_signed_cookies(resource_url, settings.CLOUDFRONT_KEY_PAIR_ID, private_key)

  for key, value in cookies.items():
    response.set_cookie(
      key=key, value=value, httponly=True, secure=True, samesite="none", domain=settings.COOKIE_DOMAIN
    )

  return {"status": "session_created"}
