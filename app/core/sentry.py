"""Centralized Sentry SDK initialization.

Provides a single ``init_sentry()`` entry-point used by both the API
(``main.py``) and the SQS worker (``worker.py``) so configuration stays
consistent and is easy to evolve in one place.
"""

from __future__ import annotations

import logging

import sentry_sdk
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from app.core.config import settings


def init_sentry() -> None:
  """Configure and initialize the Sentry SDK (no-op when ENV is ``local``)."""
  if settings.ENV == "local":
    return

  sentry_sdk.init(
    dsn=settings.SENTRY_DSN,
    environment=settings.ENV,
    release=settings.SENTRY_RELEASE or None,
    send_default_pii=True,
    debug=settings.ENV == "dev",
    shutdown_timeout=10,
    # --- Performance / Profiling ---
    traces_sample_rate=0.1,
    profile_session_sample_rate=0.1,
    profile_lifecycle="trace",
    # --- Logs ---
    enable_logs=True,
    # --- Integrations ---
    integrations=[
      AwsLambdaIntegration(timeout_warning=True),
      LoggingIntegration(
        level=logging.INFO,
        event_level=logging.ERROR,
        sentry_logs_level=logging.INFO,
      ),
    ],
  )
