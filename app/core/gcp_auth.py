"""GCP credential loading for Vertex AI via Workload Identity Federation."""

from __future__ import annotations

import os
from functools import lru_cache

import google.auth
from aws_lambda_powertools import Logger
from google.auth.credentials import Credentials

from app.core.config import settings

logger = Logger(service=settings.POWERTOOLS_SERVICE_NAME)

_VERTEX_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


@lru_cache
def get_gcp_credentials() -> Credentials:
  """Load and return GCP credentials for Vertex AI.

  Uses Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS -> WIF config).
  Logs diagnostic information on failure to aid troubleshooting.
  """
  cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
  logger.info(
    "Loading GCP credentials",
    extra={
      "config_path": cred_path,
      "config_exists": os.path.exists(cred_path) if cred_path else False,
      "aws_region": os.environ.get("AWS_REGION", "UNSET"),
      "has_aws_key": bool(os.environ.get("AWS_ACCESS_KEY_ID")),
    },
  )

  try:
    # google.auth.default() returns a (credentials, project) tuple whose exact
    # credential type depends on the environment (WIF, ADC, service account, etc.).
    # The google-auth stubs under-specify the return type, so we cast to Credentials.
    result: tuple[Credentials, str | None] = google.auth.default(scopes=_VERTEX_SCOPES)  # type: ignore[reportUnknownVariableType]
    credentials, project = result
    logger.info(
      "GCP credentials loaded",
      extra={
        "credential_type": type(credentials).__name__,
        "project": project,
      },
    )
    return credentials
  except Exception:
    logger.exception("Failed to load GCP credentials")
    raise
