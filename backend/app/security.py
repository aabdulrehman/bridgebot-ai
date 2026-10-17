"""
Password hashing (bcrypt), JWT issuing/verification and connector API keys.

Connector API keys are high-entropy random strings, so a fast SHA-256 digest
is sufficient (and allows O(1) indexed lookup); user passwords use bcrypt.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from .config import get_settings

settings = get_settings()
API_KEY_PREFIX = "bbk_"


def hash_password(password: str) -> str:
    """bcrypt-hash a password (bcrypt only uses the first 72 bytes)."""
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time bcrypt verification; never raises on malformed hashes."""
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: uuid.UUID, email: str) -> str:
    """Issue a signed JWT for the dashboard."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[uuid.UUID]:
    """Return the user id inside a valid token, or None."""
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return uuid.UUID(data["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def generate_api_key() -> str:
    """Create a new raw connector API key (shown to the user exactly once)."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest used for storage and lookup."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
