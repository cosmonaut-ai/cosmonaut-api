"""Username validation, reservation, and lookup service.

Provides atomic, race-condition-free username claiming via a DynamoDB
conditional write on the ``UsernameReservation`` sentinel item.
"""

from __future__ import annotations

import re

from better_profanity import profanity
from pynamodb.exceptions import PutError

from app.core.errors import BadRequestError, ConflictError
from app.core.observability import logger, tracer
from app.models.entities.user import UsernameReservation
from app.services.usage import get_or_create_usage

_USERNAME_RE = re.compile(r"^[A-Za-z0-9]+$")
_MIN_LENGTH = 3
_MAX_LENGTH = 30


_RESERVED_WORDS: frozenset[str] = frozenset(
  {
    "admin",
    "administrator",
    "cosmonaut",
    "mod",
    "moderator",
    "support",
    "system",
    "help",
    "root",
    "staff",
    "official",
    "null",
    "undefined",
  }
)

# Load the profanity filter
profanity.load_censor_words()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_username(username: str) -> str:
  """Validate format and return the cleaned username.

  Raises ``BadRequestError`` on invalid input.
  """
  if not username:
    raise BadRequestError("Username is required")

  username = username.strip()

  if len(username) < _MIN_LENGTH:
    raise BadRequestError(f"Username must be at least {_MIN_LENGTH} characters")
  if len(username) > _MAX_LENGTH:
    raise BadRequestError(f"Username must be at most {_MAX_LENGTH} characters")
  if not _USERNAME_RE.fullmatch(username):
    raise BadRequestError("Username must contain only letters and numbers")
  if username.lower() in _RESERVED_WORDS or profanity.contains_profanity(username):
    raise BadRequestError("That username is not available")

  return username


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------


@tracer.capture_method
def check_availability(username: str) -> bool:
  """Return ``True`` if *username* is available (case-insensitive)."""
  username = validate_username(username)
  try:
    UsernameReservation.get(UsernameReservation.pk(username), UsernameReservation.sk())
    return False
  except UsernameReservation.DoesNotExist:
    return True


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------


@tracer.capture_method
def reserve_username(user_id: str, username: str) -> str:
  """Atomically claim *username* for *user_id*.

  Returns the stored username (original casing preserved).

  Raises:
    BadRequestError  -- invalid format or user already has a username.
    ConflictError    -- username is already taken.
  """
  username = validate_username(username)

  record = get_or_create_usage(user_id)
  if record.username:
    raise BadRequestError("Username has already been set and cannot be changed")

  sentinel = UsernameReservation(
    PK=UsernameReservation.pk(username),
    SK=UsernameReservation.sk(),
    user_id=user_id,
  )
  try:
    sentinel.save(
      condition=UsernameReservation.PK.does_not_exist(),
      add_version_condition=False,
    )
  except PutError:
    raise ConflictError("That username is already taken") from None

  record.username = username
  record.is_onboarded = True
  record.save()
  logger.info("Reserved username '%s' for user %s", username, user_id)
  return username


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


@tracer.capture_method
def get_user_id_by_username(username: str) -> str | None:
  """O(1) lookup: return the ``user_id`` that owns *username*, or ``None``."""
  try:
    sentinel = UsernameReservation.get(
      UsernameReservation.pk(username),
      UsernameReservation.sk(),
    )
    return str(sentinel.user_id)
  except UsernameReservation.DoesNotExist:
    return None
