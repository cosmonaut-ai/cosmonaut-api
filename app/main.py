"""FastAPI entrypoint for the Cosmonaut AI Lambdaolith."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from fastapi import FastAPI, Request, Response
from mangum import Mangum

from app.core.config import settings

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)
tracer = Tracer(service=settings.POWERTOOLS_SERVICE_NAME)
metrics = Metrics(namespace=settings.POWERTOOLS_SERVICE_NAME)

app = FastAPI(title="Cosmonaut AI API", version="0.1.0")


@app.middleware("http")
async def inject_logger_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
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


handler = Mangum(app)
