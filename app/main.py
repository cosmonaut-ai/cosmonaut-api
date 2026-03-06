"""FastAPI entrypoint for the Cosmonaut AI Lambdaolith."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import nest_asyncio  # type: ignore[import-untyped]
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.meta import router as meta_router
from app.api.story_nodes import router as story_nodes_router
from app.api.voices import router as voices_router
from app.api.webhooks import router as webhooks_router
from app.api.worlds import router as worlds_router
from app.core.config import settings
from app.core.security import get_current_user

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)
tracer = Tracer(service=settings.POWERTOOLS_SERVICE_NAME)
metrics = Metrics(namespace=settings.POWERTOOLS_SERVICE_NAME)

app = FastAPI(title="Cosmonaut AI API", version="0.1.0")

# Configure CORS
app.add_middleware(
  CORSMiddleware,
  allow_origins=settings.CORS_ORIGINS,
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
  expose_headers=["X-New-Node-Id"],
)


@app.middleware("http")
async def inject_logger_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
  """Attach a request identifier to structured logs for correlation."""

  request_id = request.headers.get("x-request-id") or str(uuid4())
  logger.append_keys(request_id=request_id)
  try:
    response: Response = await call_next(request)
  finally:
    logger.remove_keys("request_id")
  return response


@app.get("/health")
@tracer.capture_method
async def health():
  """Lightweight health probe for uptime checks."""

  metrics.add_metric(name="HealthCheck", unit=MetricUnit.Count, value=1)
  return {"status": "ok"}


@app.get("/debug/wif")
async def debug_wif():
  """Temporary diagnostic endpoint for Workload Identity Federation auth."""
  import json
  import os

  results: dict[str, object] = {}

  cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
  results["config_path"] = cred_path
  results["config_exists"] = os.path.exists(cred_path)

  if results["config_exists"]:
    with open(cred_path) as f:
      config = json.load(f)
    results["audience"] = config.get("audience", "MISSING")
    results["service_account_url"] = config.get("service_account_impersonation_url", "MISSING")
    results["type"] = config.get("type", "MISSING")

  results["aws_access_key"] = bool(os.environ.get("AWS_ACCESS_KEY_ID"))
  results["aws_secret_key"] = bool(os.environ.get("AWS_SECRET_ACCESS_KEY"))
  results["aws_session_token"] = bool(os.environ.get("AWS_SESSION_TOKEN"))
  results["aws_region"] = os.environ.get("AWS_REGION", "MISSING")

  try:
    import google.auth

    creds: Any
    creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])  # type: ignore[reportUnknownVariableType]
    results["cred_type"] = type(creds).__name__
    results["project"] = project
  except Exception as e:
    results["cred_load_error"] = f"{type(e).__name__}: {e}"
    return results

  try:
    from google.auth.transport.requests import Request as AuthRequest

    creds.refresh(AuthRequest())
    results["refresh"] = "SUCCESS"
    results["token_preview"] = creds.token[:20] + "..." if creds.token else "NO TOKEN"
  except Exception as e:
    results["refresh_error"] = f"{type(e).__name__}: {e}"

  return results


app.include_router(worlds_router, dependencies=[Depends(get_current_user)])
app.include_router(story_nodes_router, dependencies=[Depends(get_current_user)])
app.include_router(auth_router, dependencies=[Depends(get_current_user)])

# Meta router – public (bots can't authenticate)
app.include_router(meta_router)

# Voices router – public (no auth required)
app.include_router(voices_router)

# Webhook router – no auth (Stripe signature verification instead)
app.include_router(webhooks_router)

nest_asyncio.apply()
