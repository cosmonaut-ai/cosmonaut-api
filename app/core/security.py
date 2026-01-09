from functools import lru_cache
from typing import Any, Dict, List, cast

import httpx
import jwt
from aws_lambda_powertools import Logger
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel

from app.core.config import settings

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)


# --- Data Models ---
class User(BaseModel):
  """Represents an authenticated user."""

  id: str
  email: str
  username: str
  groups: List[str] = []

  @property
  def is_admin(self) -> bool:
    return "Admin" in self.groups or "Owner" in self.groups


# --- JWKS Management ---
@lru_cache()
def get_jwks() -> Dict[str, Any]:
  """
  Fetches and caches the JSON Web Key Set from Cognito.
  """
  if not settings.COGNITO_USER_POOL_ID:
    return {"keys": []}

  url = f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com/{settings.COGNITO_USER_POOL_ID}/.well-known/jwks.json"
  try:
    with httpx.Client(timeout=5.0) as client:
      response = client.get(url)
      response.raise_for_status()
      return response.json()
  except Exception as e:
    logger.error(f"Failed to fetch JWKS: {e}")
    raise HTTPException(status_code=500, detail="Authentication service unavailable")


# --- Validation Logic ---
security = HTTPBearer(auto_error=False)


def get_current_user(request: Request, token: HTTPAuthorizationCredentials | None = Security(security)) -> User:
  # 1. Happy Path: Mock Auth (Dev Only)
  if settings.MOCK_AUTH and settings.ENV in ["local", "dev"]:
    logger.info("Using mock authentication")
    return User(
      id="a1abe550-30e1-70ce-4198-6e782be7e643",
      email="imatson9119@gmail.com",
      username="CosmonautDev",
      groups=["Owner"],
    )

  # 2. Require token for production
  if not token:
    raise HTTPException(status_code=401, detail="Missing authorization token")

  # 2. Production Path: Verify Token
  try:
    token_str = token.credentials

    # A. Decode Header
    unverified_header = jwt.get_unverified_header(token_str)
    jwks = get_jwks()

    rsa_key: Dict[str, Any] = {}

    # Get keys and cast them so strict mode knows they are dicts
    raw_keys = jwks.get("keys", [])
    key_list: List[Dict[str, Any]] = []

    if isinstance(raw_keys, list):
      key_list = cast(List[Dict[str, Any]], raw_keys)

    for key in key_list:
      if key.get("kid") == unverified_header.get("kid"):
        rsa_key = {
          "kty": key.get("kty"),
          "kid": key.get("kid"),
          "use": key.get("use"),
          "n": key.get("n"),
          "e": key.get("e"),
        }
        break

    if not rsa_key:
      raise HTTPException(status_code=401, detail="Invalid token header (kid not found)")

    # B. Verify Signature and Claims
    public_key = cast(RSAPublicKey, RSAAlgorithm.from_jwk(rsa_key))

    # First decode without verification to check token type
    unverified_payload = jwt.decode(token_str, options={"verify_signature": False})
    token_use = unverified_payload.get("token_use")

    decode_options = {
      "verify_at_hash": False,
      "verify_aud": True,
      "verify_iss": True,
    }

    # Cognito Access Tokens use 'client_id' instead of 'aud'
    if token_use == "access":
      decode_options["verify_aud"] = False
      payload = jwt.decode(
        token_str,
        public_key,
        algorithms=["RS256"],
        issuer=f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com/{settings.COGNITO_USER_POOL_ID}",
        options=decode_options,
      )
      # Manually verify client_id for access tokens
      if payload.get("client_id") != settings.COGNITO_CLIENT_ID:
        raise jwt.InvalidTokenError("Token client_id does not match")
    else:
      # ID Tokens use 'aud'
      payload = jwt.decode(
        token_str,
        public_key,
        algorithms=["RS256"],
        audience=settings.COGNITO_CLIENT_ID,
        issuer=f"https://cognito-idp.{settings.AWS_REGION}.amazonaws.com/{settings.COGNITO_USER_POOL_ID}",
        options=decode_options,
      )

    # C. Parse Groups
    raw_groups = payload.get("cognito:groups", [])
    groups: List[str] = []

    if isinstance(raw_groups, list):
      groups = [str(g) for g in cast(List[Any], raw_groups)]

    # REMOVE IN PRODUCTION
    email = payload.get("email", "")
    email_whitelist = [
      "imatson9119@gmail.com",
      "emmarestivo@gmail.com",
      "jsrishere@gmail.com",
      "jsr.is.here@gmail.com",
      "fortex405@gmail.com",
      "andrew24whitman@gmail.com",
      "N0ah.thomas1739@gmail.com",
      "N0ahthomas1739@gmail.com",
      "N0ah.thomas1739@gmail.com",
      "n0ah.thomas1739@gmail.com",
      "n0ahthomas1739@gmail.com",
      "matthew.lee.cochran.jr@gmail.com",
      "zacendermon@gmail.com",
      "Hemani.gulzar@gmail.com",
      "Sujithbaktha2000@gmail.com",
      "palakmathur@gmail.com",
    ]
    if email not in email_whitelist:
      raise HTTPException(status_code=401, detail="Unauthorized")
    return User(
      id=payload["sub"],
      email=payload.get("email", ""),
      username=payload.get("cognito:username", ""),
      groups=groups,
    )

  except jwt.ExpiredSignatureError:
    logger.warning("Token has expired")
    raise HTTPException(status_code=401, detail="Token has expired")
  except jwt.InvalidTokenError as e:
    logger.warning(f"Invalid token: {e}")
    raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
  except Exception:
    logger.exception("Unexpected Auth Error")
    raise HTTPException(status_code=500, detail="Authentication error")
