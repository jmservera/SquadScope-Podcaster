"""Tests for simple auth endpoints and middleware (#273)."""

from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

from podcaster.auth import create_token, verify_token
from podcaster.monitoring import app, set_storage

# ---------------------------------------------------------------------------
# Minimal storage so the app can start
# ---------------------------------------------------------------------------


class _MinimalStorage:
    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        return []

    def get_bytes(self, path: str) -> bytes | None:
        return None

    def blob_exists(self, path: str) -> bool:
        return False


@pytest.fixture
def storage():
    backend = _MinimalStorage()
    set_storage(backend)
    yield backend
    set_storage(None)


@pytest.fixture
def client(storage):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

_SECRET = "test-secret-256-bits-long-enough"
_USERNAME = "admin"
_PASSWORD = "hunter2"


def _configure_auth(monkeypatch):
    monkeypatch.setenv("UI_AUTH_USERNAME", _USERNAME)
    monkeypatch.setenv("UI_AUTH_PASSWORD", _PASSWORD)
    monkeypatch.setenv("UI_AUTH_SECRET", _SECRET)


# ---------------------------------------------------------------------------
# Tests: token helpers
# ---------------------------------------------------------------------------


class TestTokenHelpers:
    def test_create_and_verify(self):
        token = create_token("alice", _SECRET)
        payload = verify_token(token, _SECRET)
        assert payload["sub"] == "alice"
        assert "exp" in payload

    def test_expired_token_rejected(self):
        payload = {"sub": "alice", "iat": 0, "exp": 1}
        token = jwt.encode(payload, _SECRET, algorithm="HS256")
        with pytest.raises(jwt.ExpiredSignatureError):
            verify_token(token, _SECRET)

    def test_wrong_secret_rejected(self):
        token = create_token("alice", _SECRET)
        with pytest.raises(jwt.InvalidSignatureError):
            verify_token(token, "wrong-secret")


# ---------------------------------------------------------------------------
# Tests: POST /api/auth/login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_returns_501_when_not_configured(self, client):
        resp = client.post(
            "/api/auth/login",
            json={"username": "any", "password": "any"},
        )
        assert resp.status_code == 501

    def test_valid_credentials(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        resp = client.post(
            "/api/auth/login",
            json={"username": _USERNAME, "password": _PASSWORD},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == _USERNAME
        assert "token" in data
        # Token must be valid
        payload = verify_token(data["token"], _SECRET)
        assert payload["sub"] == _USERNAME

    def test_wrong_password(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        resp = client.post(
            "/api/auth/login",
            json={"username": _USERNAME, "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_wrong_username(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        resp = client.post(
            "/api/auth/login",
            json={"username": "wrong", "password": _PASSWORD},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: GET /api/auth/me
# ---------------------------------------------------------------------------


class TestMe:
    def test_returns_501_when_not_configured(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 501

    def test_valid_token(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        token = create_token(_USERNAME, _SECRET)
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == _USERNAME

    def test_missing_token(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_invalid_token(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer bad-token"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: auth middleware (verify_auth)
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    def test_fails_closed_when_nothing_configured(self, client, monkeypatch):
        # #604: with no auth env vars the API must reject requests rather than
        # silently exposing every endpoint (fail closed, not fail open).
        monkeypatch.delenv("MONITORING_AUTH_DISABLED", raising=False)
        resp = client.get("/api/jobs")
        assert resp.status_code == 401

    def test_open_mode_requires_explicit_opt_in(self, client, monkeypatch):
        # #604: unauthenticated access is only allowed when an operator
        # explicitly opts in via MONITORING_AUTH_DISABLED (local dev only).
        monkeypatch.delenv("UI_AUTH_USERNAME", raising=False)
        monkeypatch.delenv("MONITORING_API_KEY", raising=False)
        monkeypatch.delenv("PODCASTER_API_KEY", raising=False)
        monkeypatch.setenv("MONITORING_AUTH_DISABLED", "true")
        resp = client.get("/api/jobs")
        assert resp.status_code == 200

    def test_disable_flag_ignored_when_auth_configured(self, client, monkeypatch):
        # The opt-out only applies when nothing is configured; if credentials
        # ARE configured, requests without them are still rejected (#604).
        _configure_auth(monkeypatch)
        monkeypatch.setenv("MONITORING_AUTH_DISABLED", "true")
        resp = client.get("/api/jobs")
        assert resp.status_code == 401

    def test_bearer_jwt_accepted(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        token = create_token(_USERNAME, _SECRET)
        resp = client.get(
            "/api/jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_api_key_still_accepted(self, client, monkeypatch):
        monkeypatch.setenv("MONITORING_API_KEY", "my-key")
        resp = client.get(
            "/api/jobs",
            headers={"x-podcaster-api-key": "my-key"},
        )
        assert resp.status_code == 200

    def test_rejects_when_auth_configured_and_no_creds(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        resp = client.get("/api/jobs")
        assert resp.status_code == 401

    def test_rejects_invalid_bearer(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        resp = client.get(
            "/api/jobs",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401

    def test_api_key_works_alongside_jwt_auth(self, client, monkeypatch):
        _configure_auth(monkeypatch)
        monkeypatch.setenv("MONITORING_API_KEY", "machine-key")
        # JWT works
        token = create_token(_USERNAME, _SECRET)
        resp1 = client.get(
            "/api/jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 200
        # API key works
        resp2 = client.get(
            "/api/jobs",
            headers={"x-podcaster-api-key": "machine-key"},
        )
        assert resp2.status_code == 200

    def test_query_token_accepted_on_stream_path(self, client, monkeypatch):
        # Browser media elements authenticate the streaming proxy via ?token=.
        _configure_auth(monkeypatch)
        token = create_token(_USERNAME, _SECRET)
        resp = client.get(f"/api/stream/jobs/job-1/episode.mp3?token={token}")
        # Auth succeeds; the blob is absent so the proxy returns 404 (not 401).
        assert resp.status_code == 404

    def test_query_token_accepted_on_progress_stream_path(self, client, monkeypatch):
        # The SSE progress stream is consumed via EventSource (#469), which
        # cannot send an Authorization header, so it must accept ?token= (#606).
        _configure_auth(monkeypatch)
        token = create_token(_USERNAME, _SECRET)
        resp = client.get(f"/api/jobs/job-1/progress/stream?token={token}")
        # Auth succeeds; the job is absent so the endpoint returns 404 (not 401).
        assert resp.status_code == 404

    def test_query_token_rejected_on_sensitive_path(self, client, monkeypatch):
        # A token leaked in a URL must not authorize credential/config/generation
        # endpoints via the query string (#606).
        _configure_auth(monkeypatch)
        token = create_token(_USERNAME, _SECRET)
        resp = client.get(f"/api/credentials?token={token}")
        assert resp.status_code == 401
        # The same token as a Bearer header is still accepted.
        ok = client.get("/api/credentials", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200

    def test_referrer_policy_header_on_every_response(self, client):
        # Streaming URLs carry a ?token=; Referrer-Policy: no-referrer stops it
        # leaking via the Referer header on outbound navigation (#606).
        resp = client.get("/api/jobs")
        assert resp.headers["referrer-policy"] == "no-referrer"
