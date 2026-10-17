"""
Real authentication primitives: bcrypt password hashing and JWT access
tokens. Signs with settings.API_SECRET_KEY (see backend/SECRETS.md) --
rotating that key invalidates every token already issued.

Replaces the X-User-Id header as the production credential (see
app/api/deps.py::get_current_user). X-User-Id still works in local
dev/test (ENVIRONMENT left at its "development" default) for
convenience and so the existing header-based test fixtures across the
suite don't all need rewriting, but it is a hard 401 whenever
settings.is_production_like() is true.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from app.core.config import settings

ACCESS_TOKEN_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12  # 12 hours
ACCESS_TOKEN_SUBJECT_CLAIM = "sub"


class InvalidAccessToken(Exception):
    """Raised for a missing, malformed, expired, or wrong-signature access token."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed/legacy hash (e.g. a seeded user with no real bcrypt hash) --
        # never a match, never a 500.
        return False


def create_access_token(user_id: uuid.UUID, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {ACCESS_TOKEN_SUBJECT_CLAIM: str(user_id), "exp": expire}
    return jwt.encode(payload, settings.API_SECRET_KEY, algorithm=ACCESS_TOKEN_ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    try:
        payload = jwt.decode(token, settings.API_SECRET_KEY, algorithms=[ACCESS_TOKEN_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidAccessToken(str(exc)) from exc

    subject = payload.get(ACCESS_TOKEN_SUBJECT_CLAIM)
    if not subject:
        raise InvalidAccessToken("Token has no subject claim")

    try:
        return uuid.UUID(subject)
    except ValueError as exc:
        raise InvalidAccessToken("Token subject is not a valid user id") from exc
