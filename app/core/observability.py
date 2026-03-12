"""Shared observability singletons for Lambda Powertools.

Import ``logger``, ``tracer``, and ``metrics`` from this module instead of
constructing new instances in every file.  This ensures a single Logger /
Tracer / Metrics object is reused across the application, which is required
for correlation IDs, active segments, and metric flushing to work correctly.
"""

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

from app.core.config import settings

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)
tracer = Tracer(service=settings.POWERTOOLS_SERVICE_NAME)
metrics = Metrics(namespace=settings.POWERTOOLS_SERVICE_NAME)

__all__ = ["MetricUnit", "logger", "metrics", "tracer"]
