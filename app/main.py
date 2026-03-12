"""FastAPI entrypoint for the Cosmonaut AI Lambdaolith."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

import nest_asyncio  # type: ignore[import-untyped]
import sentry_sdk
from aws_lambda_powertools.metrics import MetricUnit
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.meta import router as meta_router
from app.api.story_nodes import router as story_nodes_router
from app.api.voices import router as voices_router
from app.api.webhooks import router as webhooks_router
from app.api.worlds import router as worlds_router
from app.core.config import settings
from app.core.errors import AppError, RateLimitError
from app.core.observability import logger, metrics, tracer
from app.core.security import get_current_user

if settings.ENV == "prod":
  sentry_sdk.init(
    dsn=settings.SENTRY_DSN,
    environment=settings.ENV,
    send_default_pii=True,
    traces_sample_rate=0.1,
  )

app = FastAPI(title="Cosmonaut AI API", version="0.1.0")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
  """Map domain exceptions to structured JSON error responses."""
  headers: dict[str, str] = {}
  if isinstance(exc, RateLimitError):
    headers["Retry-After"] = str(exc.retry_after)
  return JSONResponse(
    status_code=exc.status_code,
    content={"error": {"code": exc.code, "message": str(exc)}},
    headers=headers or None,
  )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
  """Return generic validation errors to avoid exposing internal field names."""
  logger.warning("Validation error on %s %s", request.method, request.url.path)
  error_obj: dict[str, object] = {
    "code": "VALIDATION_ERROR",
    "message": "Invalid request. Please check your input and try again.",
  }
  if settings.ENV != "prod":
    error_obj["errors"] = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
  content: dict[str, object] = {"error": error_obj}
  return JSONResponse(status_code=422, content=content)


# Configure CORS
app.add_middleware(
  CORSMiddleware,
  allow_origins=settings.CORS_ORIGINS,
  allow_credentials=True,
  allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
  allow_headers=["Authorization", "Content-Type"],
  expose_headers=["X-New-Node-Id"],
)


@app.middleware("http")
async def inject_logger_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
  """Attach a request identifier to structured logs for correlation."""

  request_id = request.headers.get("x-request-id") or str(uuid4())
  logger.append_keys(request_id=request_id, request_path=request.url.path)
  try:
    response: Response = await call_next(request)
  finally:
    logger.remove_keys(["request_id", "request_path", "user_id"])
  return response


@app.get("/health")
@tracer.capture_method
async def health():
  """Lightweight health probe for uptime checks."""

  metrics.add_metric(name="HealthCheck", unit=MetricUnit.Count, value=1)
  return {"status": "ok"}


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
