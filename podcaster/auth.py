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
    _TOKEN_EXPIRY_SECONDS,
    create_token,
    get_credentials,
    verify_token,
)

__all__ = [
    "_JWT_ALGORITHM",
    "_TOKEN_EXPIRY_SECONDS",
    "LoginRequest",
    "LoginResponse",
    "MeResponse",
    "create_token",
    "get_credentials",
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


def _query_token_allowed(path: str) -> bool:
    """Return True for the browser-native streaming endpoints that must accept
    a ``?token=`` query parameter because the browser primitive loading them
    cannot send an ``Authorization`` header.

    * ``/api/stream/…`` — media proxy loaded via ``<audio>``/``<video>``/``<img>``.
    * ``…/progress/stream`` — the SSE progress endpoint consumed via
      ``EventSource`` (issue #469), which likewise cannot set request headers.

    Every other endpoint rejects query tokens: a token in a URL leaks via
    browser history, access/proxy/CDN logs, and ``Referer`` headers, so
    honouring it on sensitive endpoints would let a leaked URL authorize
    privileged actions (#606).
    """
    return path.startswith("/api/stream/") or path.endswith("/progress/stream")


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

    # --- Try query parameter token (browser media elements only) ---
    query_token = request.query_params.get("token", "")
    # Query-string tokens are honoured *only* for browser-native streaming
    # endpoints (the media proxy and the SSE progress stream) whose loading
    # primitive — ``<audio>``/``<video>``/``<img>`` or ``EventSource`` — cannot
    # send an Authorization header. They are never accepted for credential,
    # generation, review, or config endpoints: a token placed in a URL leaks
    # via browser history, server access logs, proxy/CDN logs, and Referer
    # headers, so honouring it on sensitive endpoints would let a leaked URL
    # authorize privileged actions (#606).
    if query_token and creds is not None and _query_token_allowed(request.url.path):
        _secret = creds[2]
        try:
            verify_token(query_token, _secret)
            return
        except jwt.PyJWTError:
            pass

    raise HTTPException(status_code=401, detail="Invalid or missing credentials")
