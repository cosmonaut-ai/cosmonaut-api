"""FastAPI entrypoint for the Cosmonaut AI Lambdaolith."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

import nest_asyncio  # type: ignore[import-untyped]
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.story_nodes import router as story_nodes_router
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


app.include_router(worlds_router, dependencies=[Depends(get_current_user)])
app.include_router(story_nodes_router, dependencies=[Depends(get_current_user)])
app.include_router(auth_router, dependencies=[Depends(get_current_user)])

nest_asyncio.apply()
