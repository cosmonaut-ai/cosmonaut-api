"""Centralized application error hierarchy.

All domain exceptions inherit from ``AppError`` and carry an HTTP status code
and machine-readable error code.  A single FastAPI exception handler
(registered in ``main.py``) maps them to structured JSON responses, removing
repetitive ``except X: raise HTTPException(...)`` boilerplate from route
handlers.
"""

from __future__ import annotations


class AppError(Exception):
  """Base class for all application-domain errors."""

  status_code: int = 500
  code: str = "INTERNAL_ERROR"


class NotFoundError(AppError):
  status_code = 404
  code = "NOT_FOUND"


class ForbiddenError(AppError):
  status_code = 403
  code = "FORBIDDEN"


class BadRequestError(AppError):
  status_code = 400
  code = "BAD_REQUEST"


class ConflictError(AppError):
  status_code = 409
  code = "CONFLICT"


class RateLimitError(AppError):
  status_code = 429
  code = "RATE_LIMITED"

  def __init__(self, message: str, retry_after: int):
    super().__init__(message)
    self.retry_after = retry_after


class QuotaError(AppError):
  status_code = 429
  code = "QUOTA_EXCEEDED"


class ExternalServiceError(AppError):
  status_code = 502
  code = "EXTERNAL_SERVICE_ERROR"


class SessionNotFoundError(NotFoundError):
  code = "SESSION_NOT_FOUND"


class SessionAccessDeniedError(ForbiddenError):
  code = "SESSION_ACCESS_DENIED"


class WrongSessionForNodeError(ForbiddenError):
  code = "WRONG_SESSION_FOR_NODE"
