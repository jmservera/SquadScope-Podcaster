"""Pure OAuth2 helpers for the one-time YouTube refresh-token consent flow (#441).

Service accounts do NOT work with the YouTube Data API for uploads — uploads act
on behalf of a channel-owning Google account, which requires OAuth2 *user*
consent. This module produces the URLs and request payloads for the
authorization-code flow and parses the token response. It contains **no network
calls** so it is unit-testable and safe to import in CI; the interactive
loopback consent flow lives in ``scripts/youtube_oauth_setup.py``.

The minted refresh token is long-lived and is the credential the distribution
path (``podcaster/video/distribution.py``) exchanges for short-lived access
tokens. It MUST be stored as a secret (Azure Key Vault, #443) — never committed,
logged, or printed beyond the operator's one-time setup run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlencode

# The app uploads videos (videos.insert), reads them back to verify
# status/metadata (videos.list), updates their publish status to promote an
# approved draft to public or schedule a future publish (videos.update,
# part=status), and manages the show playlist (playlistItems.list/insert)
# (#649). `youtube.upload` only authorizes videos.insert — every other call
# gets a 403 insufficientPermissions even though the token looks valid.
# `https://www.googleapis.com/auth/youtube` ("Manage your YouTube account") is
# the single scope that covers all of videos.insert/list/update and
# playlistItems.list/insert, so it replaces (rather than adds to)
# youtube.upload — requesting both would be redundant.
# `youtube.force-ssl` grants the same operations and is an equally valid
# choice per Google's docs; we standardize on `youtube` to match the scope
# documented on this app's OAuth consent screen and verification runbook
# going forward (#649) — the app's original scope (#441) was `youtube.upload`
# only, which this change replaces because it could not cover read-back or
# playlist calls.
# Google classifies both `youtube` and `youtube.upload` as *sensitive* (not
# *restricted*) scopes, so this does not change OAuth app verification tier —
# see docs/youtube-oauth-verification.md. Do not request `youtubepartner`;
# it is for content-partner asset management, not this app's use case.
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube"

# Upload-only scope, kept for an explicit opt-in (e.g. `--upload-only` on the
# setup script) for operators who genuinely only need uploads and want the
# narrowest possible grant. This is NOT the default: the app requires
# playlist and read-back access, so `YOUTUBE_SCOPE` above is what
# `build_consent_url` requests unless the caller overrides `scopes=`.
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Loopback redirect for the "Desktop app" / installed-app flow. Google allows
# any port on the loopback IP for desktop clients; the setup script binds an
# ephemeral port and substitutes it here.
DEFAULT_REDIRECT_URI = "http://127.0.0.1:{port}/oauth2callback"


@dataclass(frozen=True)
class OAuthClient:
    """Non-secret-by-itself OAuth2 desktop client identifiers from GCP."""

    client_id: str
    client_secret: str

    def require(self) -> None:
        if not self.client_id or not self.client_secret:
            raise ValueError("client_id and client_secret are required")


def build_consent_url(
    client: OAuthClient,
    redirect_uri: str,
    *,
    scopes: list[str] | None = None,
    state: str,
) -> str:
    """Build the Google consent-screen URL for the authorization-code flow.

    ``access_type=offline`` + ``prompt=consent`` are required so Google returns a
    refresh token (and re-issues one even if the user previously consented).
    """

    client.require()
    if not redirect_uri:
        raise ValueError("redirect_uri is required")
    if not state:
        raise ValueError("state is required (CSRF protection)")

    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes or [YOUTUBE_SCOPE]),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def build_token_exchange_payload(
    client: OAuthClient,
    code: str,
    redirect_uri: str,
) -> bytes:
    """Build the form-encoded body that exchanges an auth code for tokens."""

    client.require()
    if not code:
        raise ValueError("authorization code is required")
    if not redirect_uri:
        raise ValueError("redirect_uri is required")

    return urlencode(
        {
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")


@dataclass(frozen=True)
class TokenResult:
    """Parsed token response. ``refresh_token`` is the durable credential."""

    refresh_token: str
    access_token: str
    expires_in: int
    scope: str
    token_type: str


def parse_token_response(body: str | bytes) -> TokenResult:
    """Parse Google's token JSON, requiring a refresh token to be present.

    A missing ``refresh_token`` almost always means the consent URL omitted
    ``access_type=offline``/``prompt=consent`` or the user previously granted
    consent without forcing re-consent.
    """

    if isinstance(body, bytes):
        body = body.decode("utf-8")
    data = json.loads(body)

    if "error" in data:
        raise RuntimeError(
            f"token endpoint error: {data.get('error')}: {data.get('error_description', '')}"
        )

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "token response missing refresh_token; ensure access_type=offline and "
            "prompt=consent were used in the consent URL"
        )

    return TokenResult(
        refresh_token=refresh_token,
        access_token=data.get("access_token", ""),
        expires_in=int(data.get("expires_in", 0)),
        scope=data.get("scope", ""),
        token_type=data.get("token_type", ""),
    )


def redact_secret(value: str, *, keep: int = 4) -> str:
    """Redact a secret for safe display, revealing only the last ``keep`` chars."""

    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]


def parse_redirect_query(query: dict[str, list[str]], expected_state: str) -> str:
    """Validate the loopback redirect query and return the authorization code.

    Rejects mismatched ``state`` (CSRF) and surfaces an ``error`` returned by
    Google (e.g. ``access_denied``).
    """

    def first(name: str) -> str:
        values = query.get(name) or []
        return values[0] if values else ""

    error = first("error")
    if error:
        raise RuntimeError(f"consent denied or failed: {error}")

    state = first("state")
    if state != expected_state:
        raise RuntimeError("state mismatch — possible CSRF; aborting")

    code = first("code")
    if not code:
        raise RuntimeError("redirect missing authorization code")
    return code
