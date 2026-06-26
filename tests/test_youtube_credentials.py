"""Tests for YouTube OAuth2 refresh-token storage & retrieval (#443)."""

from __future__ import annotations

import io
import json
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from podcaster.youtube_credentials import (
    _UrllibTransport,
    DEFAULT_SECRET_NAME,
    KeyVaultSecretLoader,
    YouTubeCredentialError,
    YouTubeTokenRevokedError,
    is_invalid_grant,
    load_youtube_refresh_token,
    refresh_access_token,
)


# --- fakes --------------------------------------------------------------------


class _FakeCredential:
    def __init__(self, token="kv-token"):
        self.token = token
        self.scopes = []

    def get_token(self, *scopes):
        self.scopes.extend(scopes)
        return self.token


class _FakeTransport:
    def __init__(self, status, body, *, capture=None):
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.capture = capture if capture is not None else []

    def request(self, url, *, method="GET", headers=None, data=None):
        self.capture.append({"url": url, "method": method, "headers": headers, "data": data})
        return self.status, self.body


# --- KeyVaultSecretLoader -----------------------------------------------------


def test_keyvault_loader_fetches_secret_value():
    cred = _FakeCredential()
    transport = _FakeTransport(200, {"value": "  secret-rt  "})
    loader = KeyVaultSecretLoader(
        "https://v.vault.azure.net/", credential=cred, transport=transport
    )
    assert loader.get_secret("youtube-oauth-refresh-token") == "secret-rt"
    # bearer token used + correct scope requested
    assert transport.capture[0]["headers"]["Authorization"] == "Bearer kv-token"
    assert "vault.azure.net/.default" in cred.scopes[0]
    assert "/secrets/youtube-oauth-refresh-token?api-version=" in transport.capture[0]["url"]


def test_keyvault_loader_requires_url():
    with pytest.raises(ValueError):
        KeyVaultSecretLoader("")


def test_keyvault_loader_http_error():
    loader = KeyVaultSecretLoader(
        "https://v.vault.azure.net", credential=_FakeCredential(),
        transport=_FakeTransport(403, b"forbidden"),
    )
    with pytest.raises(YouTubeCredentialError, match="HTTP 403"):
        loader.get_secret("x")


# --- _UrllibTransport HTTPError handling --------------------------------------


def test_urllib_transport_returns_status_body_on_http_error():
    """urlopen raises HTTPError for non-2xx; transport must catch and return (code, body)."""
    err = HTTPError(
        url="https://example.com",
        code=403,
        msg="Forbidden",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(b"forbidden"),
    )
    with patch("podcaster.youtube_credentials.urlopen", side_effect=err):
        status, body = _UrllibTransport().request("https://example.com")
    assert status == 403
    assert body == b"forbidden"


def test_urllib_transport_returns_status_body_on_invalid_grant():
    """HTTPError with 400 + invalid_grant body is surfaced so callers can detect it."""
    payload = b'{"error":"invalid_grant"}'
    err = HTTPError(
        url="https://oauth2.googleapis.com/token",
        code=400,
        msg="Bad Request",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(payload),
    )
    with patch("podcaster.youtube_credentials.urlopen", side_effect=err):
        status, body = _UrllibTransport().request(
            "https://oauth2.googleapis.com/token", method="POST", data=b"x=y"
        )
    assert status == 400
    assert b"invalid_grant" in body


# --- load_youtube_refresh_token -----------------------------------------------


def test_load_prefers_env():
    token = load_youtube_refresh_token(env={"VIDEO_YOUTUBE_REFRESH_TOKEN": " env-rt "})
    assert token == "env-rt"


def test_load_returns_empty_when_unconfigured():
    assert load_youtube_refresh_token(env={}) == ""


def test_load_from_keyvault_when_env_absent():
    loader = KeyVaultSecretLoader(
        "https://v.vault.azure.net", credential=_FakeCredential(),
        transport=_FakeTransport(200, {"value": "kv-rt"}),
    )
    token = load_youtube_refresh_token(
        env={"VIDEO_YOUTUBE_KEYVAULT_URL": "https://v.vault.azure.net"},
        secret_loader=loader,
    )
    assert token == "kv-rt"


def test_load_uses_default_secret_name():
    captured = []
    loader = KeyVaultSecretLoader(
        "https://v.vault.azure.net", credential=_FakeCredential(),
        transport=_FakeTransport(200, {"value": "kv-rt"}, capture=captured),
    )
    load_youtube_refresh_token(
        env={"VIDEO_YOUTUBE_KEYVAULT_URL": "https://v.vault.azure.net"},
        secret_loader=loader,
    )
    assert f"/secrets/{DEFAULT_SECRET_NAME}?" in captured[0]["url"]


# --- is_invalid_grant ---------------------------------------------------------


def test_is_invalid_grant_detection():
    assert is_invalid_grant(400, b'{"error":"invalid_grant"}') is True
    assert is_invalid_grant(400, '{"error":"invalid_grant"}') is True
    assert is_invalid_grant(401, b"INVALID_GRANT") is True
    assert is_invalid_grant(200, b'{"access_token":"x"}') is False
    assert is_invalid_grant(500, b"server error") is False


# --- refresh_access_token -----------------------------------------------------


def test_refresh_access_token_success():
    transport = _FakeTransport(200, {"access_token": "at-123"})
    token = refresh_access_token(
        client_id="c", client_secret="s", refresh_token="rt", transport=transport
    )
    assert token == "at-123"
    # secret payload posted as urlencoded form, not logged
    assert transport.capture[0]["method"] == "POST"


def test_refresh_access_token_missing_token_raises():
    with pytest.raises(YouTubeCredentialError, match="No YouTube refresh token"):
        refresh_access_token(client_id="c", client_secret="s", refresh_token="")


def test_refresh_access_token_invalid_grant_alerts(monkeypatch):
    alerts = []
    monkeypatch.setattr(
        "podcaster.credential_expiry.notify_youtube_credential_expiry",
        lambda msg: alerts.append(msg),
    )
    transport = _FakeTransport(400, {"error": "invalid_grant"})
    with pytest.raises(YouTubeTokenRevokedError):
        refresh_access_token(
            client_id="c", client_secret="s", refresh_token="rt", transport=transport
        )
    assert alerts and "invalid_grant" in alerts[0]


def test_refresh_access_token_invalid_grant_no_notify(monkeypatch):
    called = []
    monkeypatch.setattr(
        "podcaster.credential_expiry.notify_youtube_credential_expiry",
        lambda msg: called.append(msg),
    )
    transport = _FakeTransport(400, {"error": "invalid_grant"})
    with pytest.raises(YouTubeTokenRevokedError):
        refresh_access_token(
            client_id="c", client_secret="s", refresh_token="rt",
            transport=transport, notify=False,
        )
    assert called == []


def test_refresh_access_token_other_error():
    transport = _FakeTransport(500, b"boom")
    with pytest.raises(YouTubeCredentialError, match="HTTP 500"):
        refresh_access_token(
            client_id="c", client_secret="s", refresh_token="rt", transport=transport
        )


# --- credential_expiry YouTube notifier ---------------------------------------


def test_build_youtube_issue_body_has_reauth_steps():
    from podcaster.credential_expiry import build_youtube_issue_body

    body = build_youtube_issue_body("invalid_grant", timestamp="2025-01-01T00:00:00Z")
    assert "re-authenticate" in body.lower()
    assert "youtube-oauth-refresh-token" in body
    assert "2025-01-01T00:00:00Z" in body


def test_notify_youtube_disabled(monkeypatch):
    from podcaster import credential_expiry

    monkeypatch.setenv("CREDENTIAL_EXPIRY_NOTIFY_DISABLED", "true")
    assert credential_expiry.notify_youtube_credential_expiry("x") is None
