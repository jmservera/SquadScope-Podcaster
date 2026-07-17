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
import os

import jwt
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel

from podcaster.auth_core import (
    _JWT_ALGORITHM,
    _STREAM_TOKEN_EXPIRY_SECONDS,
    _TOKEN_EXPIRY_SECONDS,
    create_scoped_token,
    create_token,
    get_credentials,
    verify_scoped_token,
    verify_token,
)

__all__ = [
    "_JWT_ALGORITHM",
    "_STREAM_TOKEN_EXPIRY_SECONDS",
    "_TOKEN_EXPIRY_SECONDS",
    "LoginRequest",
    "LoginResponse",
    "MeResponse",
    "create_scoped_token",
    "create_token",
    "get_credentials",
    "verify_scoped_query_access",
    "verify_scoped_token",
    "verify_auth",
    "verify_token",
]


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


def _full_auth_is_valid(
    *,
    creds: tuple[str, str, str] | None,
    configured_api_key: str,
    authorization: str,
    x_podcaster_api_key: str,
) -> bool:
    """Return true when a full bearer JWT or API key authorizes the request."""
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        if creds is not None:
            _secret = creds[2]
            try:
                verify_token(token, _secret)
                return True
            except jwt.PyJWTError:
                pass

    if configured_api_key and x_podcaster_api_key:
        if hmac.compare_digest(x_podcaster_api_key, configured_api_key):
            return True

    return False


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
    creds = get_credentials()
    configured_api_key = os.environ.get("MONITORING_API_KEY") or os.environ.get(
        "PODCASTER_API_KEY", ""
    )

    # If nothing is configured at all, allow everything (open mode).
    if creds is None and not configured_api_key:
        return

    if _full_auth_is_valid(
        creds=creds,
        configured_api_key=configured_api_key,
        authorization=authorization,
        x_podcaster_api_key=x_podcaster_api_key,
    ):
        return

    raise HTTPException(status_code=401, detail="Invalid or missing credentials")


def verify_scoped_query_access(request: Request, scope: str, resource: str) -> None:
    """Authorize browser URL access using full headers or a scoped query token."""
    creds = get_credentials()
    configured_api_key = os.environ.get("MONITORING_API_KEY") or os.environ.get(
        "PODCASTER_API_KEY", ""
    )

    if creds is None and not configured_api_key:
        return

    if _full_auth_is_valid(
        creds=creds,
        configured_api_key=configured_api_key,
        authorization=request.headers.get("Authorization", ""),
        x_podcaster_api_key=request.headers.get("x-podcaster-api-key", ""),
    ):
        return

    query_token = request.query_params.get("token", "")
    if query_token and creds is not None:
        _secret = creds[2]
        try:
            verify_scoped_token(query_token, _secret, scope=scope, resource=resource)
            return
        except jwt.PyJWTError:
            pass

    raise HTTPException(status_code=401, detail="Invalid or missing credentials")
