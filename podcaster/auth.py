"""Simple username/password authentication for the monitoring UI (#273).

Provides JWT-based auth so operators who cannot create Entra app
registrations can still protect the dashboard.  Credentials are read
from environment variables:

  UI_AUTH_USERNAME  — required login username
  UI_AUTH_PASSWORD  — required login password
  UI_AUTH_SECRET    — HMAC secret used to sign/verify JWTs

When none of the UI_AUTH_* vars are set the auth layer is effectively
disabled (same behaviour as before this change).
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from typing import Any

import jwt
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_TOKEN_EXPIRY_SECONDS = 8 * 60 * 60  # 8 hours
_JWT_ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_credentials() -> tuple[str, str, str] | None:
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
    """Decode and validate a JWT.  Raises on invalid/expired tokens."""
    return jwt.decode(token, secret, algorithms=[_JWT_ALGORITHM])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


class MeResponse(BaseModel):
    username: str


# ---------------------------------------------------------------------------
# FastAPI dependency — replaces the old API-key-only check
# ---------------------------------------------------------------------------


def verify_auth(
    request: Request,
    authorization: str = Header(default=""),
    x_podcaster_api_key: str = Header(default=""),
) -> None:
    """Verify the caller is authorised.

    Accepts either:
    1. A valid Bearer JWT (issued by /api/auth/login), **or**
    2. A valid X-Podcaster-Api-Key header (existing machine-to-machine auth).

    When neither UI_AUTH_* nor MONITORING_API_KEY / PODCASTER_API_KEY are
    configured, all requests are allowed (open mode — mirrors pre-#273
    behaviour).
    """
    creds = _get_credentials()
    configured_api_key = os.environ.get("MONITORING_API_KEY") or os.environ.get(
        "PODCASTER_API_KEY", ""
    )

    # If nothing is configured at all, allow everything (open mode).
    if creds is None and not configured_api_key:
        return

    # --- Try Bearer JWT first ---
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        if creds is not None:
            _secret = creds[2]
            try:
                verify_token(token, _secret)
                return  # valid JWT
            except jwt.PyJWTError:
                pass  # fall through to API-key check

    # --- Try API key ---
    if configured_api_key and x_podcaster_api_key:
        if hmac.compare_digest(x_podcaster_api_key, configured_api_key):
            return

    raise HTTPException(status_code=401, detail="Invalid or missing credentials")
