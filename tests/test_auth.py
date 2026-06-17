"""Tests for simple auth endpoints and middleware (#273)."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from podcaster.auth import _TOKEN_EXPIRY_SECONDS, create_token, verify_token
from podcaster.monitoring import app, set_storage


# ---------------------------------------------------------------------------
# Minimal storage so the app can start
# ---------------------------------------------------------------------------


class _MinimalStorage:
    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        return []

    def get_bytes(self, path: str) -> bytes | None:
        return None


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
    def test_open_mode_when_nothing_configured(self, client):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200

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
