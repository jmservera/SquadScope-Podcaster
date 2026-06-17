"""Framework-free JWT auth helpers for API and monitoring auth flows.

This module intentionally depends only on PyJWT and the Python standard
library so the lightweight API container can import token helpers without
requiring FastAPI or Pydantic to be installed.
"""

from __future__ import annotations

import os
import time
from typing import Any

import jwt

_TOKEN_EXPIRY_SECONDS = 8 * 60 * 60  # 8 hours
_JWT_ALGORITHM = "HS256"


def get_credentials() -> tuple[str, str, str] | None:
    """Return (username, password, secret) or None if auth is not configured."""
    username = os.environ.get("UI_AUTH_USERNAME", "").strip()
    password = os.environ.get("UI_AUTH_PASSWORD", "").strip()
    secret = os.environ.get("UI_AUTH_SECRET", "").strip()
    if username and password and secret:
        return username, password, secret
    return None


def create_token(username: str, secret: str) -> str:
    """Issue a signed JWT for *username*."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": username,
        "iat": now,
        "exp": now + _TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, secret, algorithm=_JWT_ALGORITHM)


def verify_token(token: str, secret: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises on invalid/expired tokens."""
    return jwt.decode(token, secret, algorithms=[_JWT_ALGORITHM])
