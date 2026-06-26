"""YouTube OAuth2 refresh-token storage & retrieval (#443).

The YouTube uploader (#442) authenticates with a long-lived OAuth2 **refresh
token** that it exchanges for short-lived access tokens on every run. That
refresh token is security-critical: it must be stored in Azure Key Vault (the
same posture as the Spotify cookies), retrieved at runtime, never logged, and —
if Google revokes it (password change, consent withdrawal, 6-month inactivity) —
must raise a clear *re-authenticate* alert instead of silently failing forever.

This module provides:

* :class:`KeyVaultSecretLoader` — fetch a secret from Key Vault over REST using
  the project's managed-identity token flow (no extra SDK dependency).
* :func:`load_youtube_refresh_token` — resolve the refresh token from the
  environment first (Container Apps inject Key Vault *references* as env vars),
  then directly from Key Vault. The value is never logged.
* :func:`refresh_access_token` — exchange the refresh token for an access token,
  detecting ``invalid_grant`` (revocation/expiry) and firing the re-auth alert.

Secrets are returned to callers but never emitted to logs; only their *presence*
and length class are ever logged.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Mapping, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("podcaster.youtube_credentials")

_KEYVAULT_SCOPE = "https://vault.azure.net/.default"
_KEYVAULT_API_VERSION = "7.4"

#: Env var that may hold the refresh token directly (e.g. a Key Vault reference
#: resolved by Azure Container Apps into an env var).
ENV_REFRESH_TOKEN = "VIDEO_YOUTUBE_REFRESH_TOKEN"
#: Env vars describing where to fetch the token from Key Vault at runtime.
ENV_KEYVAULT_URL = "VIDEO_YOUTUBE_KEYVAULT_URL"
ENV_REFRESH_TOKEN_SECRET = "VIDEO_YOUTUBE_REFRESH_TOKEN_SECRET"
DEFAULT_SECRET_NAME = "youtube-oauth-refresh-token"

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class YouTubeCredentialError(RuntimeError):
    """A YouTube credential could not be loaded or used."""


class YouTubeTokenRevokedError(YouTubeCredentialError):
    """The refresh token was revoked/expired — operator must re-authenticate."""


class _TokenCredential(Protocol):
    def get_token(self, *scopes: str) -> str: ...


class _Transport(Protocol):
    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> tuple[int, bytes]: ...


class _UrllibTransport:
    def request(self, url, *, method="GET", headers=None, data=None):
        req = Request(url, data=data, method=method, headers=headers or {})
        with urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()


def _default_credential() -> _TokenCredential:
    # Imported lazily so unit tests need not touch Azure storage internals.
    from podcaster.storage import ManagedIdentityTokenCredential

    return ManagedIdentityTokenCredential()


class KeyVaultSecretLoader:
    """Fetch secrets from Azure Key Vault over REST via managed identity.

    Mirrors the project's existing identity-only access pattern (see
    ``ManagedIdentityTokenCredential``) rather than pulling in the
    ``azure-keyvault-secrets`` SDK.
    """

    def __init__(
        self,
        vault_url: str,
        *,
        credential: _TokenCredential | None = None,
        transport: _Transport | None = None,
    ) -> None:
        if not vault_url:
            raise ValueError("vault_url is required")
        self._vault_url = vault_url.rstrip("/")
        self._credential = credential
        self._transport = transport or _UrllibTransport()

    def _token(self) -> str:
        credential = self._credential or _default_credential()
        return credential.get_token(_KEYVAULT_SCOPE)

    def get_secret(self, name: str) -> str:
        """Return the current value of secret *name* (never logged)."""

        url = f"{self._vault_url}/secrets/{name}?api-version={_KEYVAULT_API_VERSION}"
        status, body = self._transport.request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        if status != 200:
            raise YouTubeCredentialError(
                f"Key Vault secret '{name}' fetch failed: HTTP {status}"
            )
        try:
            value = json.loads(body).get("value", "")
        except (ValueError, AttributeError) as exc:
            raise YouTubeCredentialError(
                f"Key Vault secret '{name}' response was not valid JSON"
            ) from exc
        return (value or "").strip()


def load_youtube_refresh_token(
    *,
    env: Mapping[str, str] | None = None,
    secret_loader: KeyVaultSecretLoader | None = None,
) -> str:
    """Resolve the YouTube OAuth2 refresh token.

    Resolution order:

    1. ``VIDEO_YOUTUBE_REFRESH_TOKEN`` env var (may be a Key Vault reference that
       Container Apps already resolved into an env var).
    2. Key Vault directly: ``VIDEO_YOUTUBE_KEYVAULT_URL`` +
       ``VIDEO_YOUTUBE_REFRESH_TOKEN_SECRET`` (default
       ``youtube-oauth-refresh-token``).

    Returns an empty string when no source is configured. The token value is
    never logged.
    """

    env = os.environ if env is None else env

    direct = (env.get(ENV_REFRESH_TOKEN) or "").strip()
    if direct:
        logger.debug("YouTube refresh token loaded from environment")
        return direct

    vault_url = (env.get(ENV_KEYVAULT_URL) or "").strip()
    if not vault_url:
        return ""

    secret_name = (env.get(ENV_REFRESH_TOKEN_SECRET) or "").strip() or DEFAULT_SECRET_NAME
    loader = secret_loader or KeyVaultSecretLoader(vault_url)
    token = loader.get_secret(secret_name)
    logger.info(
        "YouTube refresh token loaded from Key Vault secret '%s' (present=%s)",
        secret_name,
        bool(token),
    )
    return token


def is_invalid_grant(status: int, body: bytes | str) -> bool:
    """True if a Google token response indicates a revoked/expired grant."""

    if status == 200:
        return False
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else (body or "")
    return "invalid_grant" in text.lower()


def refresh_access_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    transport: _Transport | None = None,
    notify: bool = True,
) -> str:
    """Exchange the refresh token for a short-lived access token.

    Raises :class:`YouTubeTokenRevokedError` (and fires the re-auth alert) when
    Google reports ``invalid_grant``; raises :class:`YouTubeCredentialError` on
    other failures. Secrets are never logged.
    """

    if not refresh_token:
        raise YouTubeCredentialError(
            "No YouTube refresh token configured (set "
            f"{ENV_REFRESH_TOKEN} or {ENV_KEYVAULT_URL})."
        )

    http = transport or _UrllibTransport()
    data = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode()

    status, body = http.request(
        _GOOGLE_TOKEN_URL,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
    )

    if is_invalid_grant(status, body):
        message = "YouTube OAuth refresh token was revoked or expired (invalid_grant)."
        logger.error("%s Operator must re-authenticate.", message)
        if notify:
            _notify_revoked(message)
        raise YouTubeTokenRevokedError(message)

    if status != 200:
        raise YouTubeCredentialError(f"YouTube token refresh failed: HTTP {status}")

    try:
        access_token = json.loads(body).get("access_token")
    except (ValueError, AttributeError) as exc:
        raise YouTubeCredentialError("YouTube token response was not valid JSON") from exc

    if not access_token:
        raise YouTubeCredentialError("YouTube token response missing access_token")
    return access_token


def _notify_revoked(message: str) -> None:
    """Best-effort re-auth alert; never raises into the caller."""

    try:
        from podcaster.credential_expiry import notify_youtube_credential_expiry

        notify_youtube_credential_expiry(message)
    except Exception:  # noqa: BLE001 - alerting must never break the pipeline
        logger.warning("failed to dispatch YouTube re-auth alert", exc_info=True)
