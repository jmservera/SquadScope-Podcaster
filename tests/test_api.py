"""Tests for the HTTP API server module (podcaster.api)."""

from __future__ import annotations

import io
import json
import os
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import pytest

from podcaster.api import GenerateHandler
from podcaster.auth import create_token
from podcaster.orchestration import JobPublishOutcome
from podcaster.publish import PublishResult


class FakeRequest:
    """Minimal request for testing the handler."""

    def __init__(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.method = method
        self.path = path
        self.body = body or b""
        self.headers = headers or {}


class FakeHandler:
    """A minimal mock of BaseHTTPRequestHandler for unit testing."""

    def __init__(
        self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None
    ):
        self.path = path
        self._body = body
        self._headers_dict = headers or {}
        self.response_code: int | None = None
        self.response_headers: dict[str, str] = {}
        self.response_body: bytes = b""
        self._wfile = io.BytesIO()

        # Emulate headers object
        class HeadersProxy:
            def __init__(self, d: dict[str, str]):
                self._d = d

            def get(self, key: str, default: str | None = None) -> str | None:
                for k, v in self._d.items():
                    if k.lower() == key.lower():
                        return v
                return default

            def items(self):
                return self._d.items()

        self.headers = HeadersProxy(self._headers_dict)
        self.rfile = io.BytesIO(self._body)
        self.wfile = self._wfile

    def send_response(self, code: int) -> None:
        self.response_code = code

    def send_header(self, key: str, value: str) -> None:
        self.response_headers[key] = value

    def end_headers(self) -> None:
        pass

    def get_response_json(self) -> dict[str, Any]:
        return json.loads(self._wfile.getvalue())


def make_handler(
    method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None
) -> FakeHandler:
    """Create a FakeHandler and dispatch the request."""
    handler = FakeHandler(method, path, body, headers)
    # Manually invoke the handler method
    if method == "GET":
        GenerateHandler.do_GET(handler)  # type: ignore[arg-type]
    elif method == "POST":
        GenerateHandler.do_POST(handler)  # type: ignore[arg-type]
    elif method == "PUT":
        GenerateHandler.do_PUT(handler)  # type: ignore[arg-type]
    elif method == "DELETE":
        GenerateHandler.do_DELETE(handler)  # type: ignore[arg-type]
    return handler


class TestHealthEndpoint:
    def test_healthz_returns_200(self):
        handler = make_handler("GET", "/healthz")
        assert handler.response_code == HTTPStatus.OK
        assert handler.get_response_json() == {"status": "healthy"}

    def test_unknown_get_returns_404(self):
        handler = make_handler("GET", "/unknown")
        assert handler.response_code == HTTPStatus.NOT_FOUND


class TestAuthCheck:
    def test_missing_api_key_returns_401(self):
        body = json.dumps(
            {"week": "2026-W24", "article_url": "https://example.com/article"}
        ).encode()
        with patch.dict(os.environ, {"PODCASTER_API_KEY": "test-key-123"}):
            handler = make_handler("POST", "/api/generate", body=body)
        assert handler.response_code == HTTPStatus.UNAUTHORIZED

    def test_wrong_api_key_returns_401(self):
        body = json.dumps(
            {"week": "2026-W24", "article_url": "https://example.com/article"}
        ).encode()
        headers = {"x-podcaster-api-key": "wrong-key", "Content-Length": str(len(body))}
        with patch.dict(os.environ, {"PODCASTER_API_KEY": "test-key-123"}):
            handler = make_handler("POST", "/api/generate", body=body, headers=headers)
        assert handler.response_code == HTTPStatus.UNAUTHORIZED

    def test_no_configured_key_returns_401(self):
        body = json.dumps(
            {"week": "2026-W24", "article_url": "https://example.com/article"}
        ).encode()
        headers = {"x-podcaster-api-key": "any-key", "Content-Length": str(len(body))}
        with patch.dict(os.environ, {}, clear=True):
            # Remove PODCASTER_API_KEY if present
            env = {k: v for k, v in os.environ.items() if k != "PODCASTER_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                handler = make_handler("POST", "/api/generate", body=body, headers=headers)
        assert handler.response_code == HTTPStatus.UNAUTHORIZED


class TestAuthLogin:
    def test_login_returns_501_when_not_configured(self):
        body = json.dumps({"username": "any", "password": "any"}).encode()
        headers = {"Content-Length": str(len(body))}
        with patch.dict(os.environ, {}, clear=True):
            handler = make_handler("POST", "/api/auth/login", body=body, headers=headers)
        assert handler.response_code == HTTPStatus.NOT_IMPLEMENTED

    def test_login_valid_credentials(self):
        body = json.dumps({"username": "admin", "password": "hunter2"}).encode()
        headers = {"Content-Length": str(len(body))}
        with patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("POST", "/api/auth/login", body=body, headers=headers)
        assert handler.response_code == HTTPStatus.OK
        response = handler.get_response_json()
        assert response["username"] == "admin"
        assert response["token"]

    def test_login_wrong_password(self):
        body = json.dumps({"username": "admin", "password": "wrong"}).encode()
        headers = {"Content-Length": str(len(body))}
        with patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("POST", "/api/auth/login", body=body, headers=headers)
        assert handler.response_code == HTTPStatus.UNAUTHORIZED

    def test_login_non_json_body(self):
        body = b"not json"
        headers = {"Content-Length": str(len(body))}
        with patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("POST", "/api/auth/login", body=body, headers=headers)
        assert handler.response_code == HTTPStatus.BAD_REQUEST

    def test_login_non_object_body(self):
        body = json.dumps(["admin", "hunter2"]).encode()
        headers = {"Content-Length": str(len(body))}
        with patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("POST", "/api/auth/login", body=body, headers=headers)
        assert handler.response_code == HTTPStatus.BAD_REQUEST

    def test_login_missing_fields(self):
        body = json.dumps({"username": "", "password": ""}).encode()
        headers = {"Content-Length": str(len(body))}
        with patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("POST", "/api/auth/login", body=body, headers=headers)
        assert handler.response_code == HTTPStatus.BAD_REQUEST


class TestAuthMe:
    def test_me_with_valid_bearer_token(self):
        secret = "test-secret-256-bits-long-enough"
        token = create_token("admin", secret)
        headers = {"Authorization": f"Bearer {token}"}
        with patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": secret,
            },
            clear=True,
        ):
            handler = make_handler("GET", "/api/auth/me", headers=headers)
        assert handler.response_code == HTTPStatus.OK
        assert handler.get_response_json() == {"username": "admin"}

    def test_me_with_api_key_no_ui_auth(self):
        headers = {"x-podcaster-api-key": "test-key-123"}
        with patch.dict(os.environ, {"PODCASTER_API_KEY": "test-key-123"}, clear=True):
            handler = make_handler("GET", "/api/auth/me", headers=headers)
        assert handler.response_code == HTTPStatus.OK
        assert handler.get_response_json() == {"username": "api-key-user"}

    def test_me_returns_501_when_nothing_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            handler = make_handler("GET", "/api/auth/me")
        assert handler.response_code == HTTPStatus.NOT_IMPLEMENTED

    def test_me_with_invalid_token(self):
        headers = {"Authorization": "Bearer bad-token"}
        with patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("GET", "/api/auth/me", headers=headers)
        assert handler.response_code == HTTPStatus.UNAUTHORIZED

    def test_me_with_no_auth_header(self):
        with patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("GET", "/api/auth/me")
        assert handler.response_code == HTTPStatus.UNAUTHORIZED


class TestRequestValidation:
    @pytest.fixture(autouse=True)
    def _set_api_key(self):
        with patch.dict(os.environ, {"PODCASTER_API_KEY": "test-key-123"}):
            yield

    def _headers(self, body: bytes) -> dict[str, str]:
        return {"x-podcaster-api-key": "test-key-123", "Content-Length": str(len(body))}

    def test_invalid_json_returns_400(self):
        body = b"not json"
        handler = make_handler("POST", "/api/generate", body=body, headers=self._headers(body))
        assert handler.response_code == HTTPStatus.BAD_REQUEST
        resp = handler.get_response_json()
        assert "request body must be valid JSON" in resp["errors"]

    def test_missing_week_returns_400(self):
        body = json.dumps({"article_url": "https://example.com/a"}).encode()
        handler = make_handler("POST", "/api/generate", body=body, headers=self._headers(body))
        assert handler.response_code == HTTPStatus.BAD_REQUEST
        resp = handler.get_response_json()
        assert "week is required" in resp["errors"]

    def test_missing_article_url_returns_400(self):
        body = json.dumps({"week": "2026-W24"}).encode()
        handler = make_handler("POST", "/api/generate", body=body, headers=self._headers(body))
        assert handler.response_code == HTTPStatus.BAD_REQUEST
        resp = handler.get_response_json()
        assert "article_url is required" in resp["errors"]


class TestSuccessfulGeneration:
    @pytest.fixture(autouse=True)
    def _set_api_key(self):
        with patch.dict(os.environ, {"PODCASTER_API_KEY": "test-key-123"}):
            yield

    def _headers(self, body: bytes) -> dict[str, str]:
        return {"x-podcaster-api-key": "test-key-123", "Content-Length": str(len(body))}

    def test_valid_request_returns_202(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PODCASTER_ARTIFACT_BASE_URL", "https://test.example")
        monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(tmp_path))
        body = json.dumps(
            {
                "week": "2026-W24",
                "article_url": "https://example.com/article",
            }
        ).encode()
        handler = make_handler("POST", "/api/generate", body=body, headers=self._headers(body))
        assert handler.response_code == HTTPStatus.ACCEPTED
        resp = handler.get_response_json()
        assert resp["job_id"] is not None
        assert resp["status"] == "accepted"
        assert resp["manifest_url"] is not None
        assert resp["errors"] == []

    def test_dry_run_returns_200(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PODCASTER_ARTIFACT_BASE_URL", "https://test.example")
        monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(tmp_path))
        body = json.dumps(
            {
                "week": "2026-W24",
                "article_url": "https://example.com/article",
                "dry_run": True,
            }
        ).encode()
        handler = make_handler("POST", "/api/generate", body=body, headers=self._headers(body))
        assert handler.response_code == HTTPStatus.OK
        resp = handler.get_response_json()
        assert resp["status"] == "dry_run"
        assert resp["errors"] == []

    def test_replay_collision_returns_409(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PODCASTER_ARTIFACT_BASE_URL", "https://test.example")
        monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(tmp_path))
        body = json.dumps(
            {
                "week": "2026-W24",
                "article_url": "https://example.com/article",
            }
        ).encode()

        first = make_handler("POST", "/api/generate", body=body, headers=self._headers(body))
        second = make_handler("POST", "/api/generate", body=body, headers=self._headers(body))

        assert first.response_code == HTTPStatus.ACCEPTED
        assert second.response_code == HTTPStatus.CONFLICT
        assert second.get_response_json()["errors"] == [
            "replay collision: existing outputs are not overwritten"
        ]

    def test_wrong_path_returns_404(self):
        body = json.dumps({"week": "2026-W24", "article_url": "https://example.com/a"}).encode()
        handler = make_handler("POST", "/api/wrong", body=body, headers=self._headers(body))
        assert handler.response_code == HTTPStatus.NOT_FOUND


class TestResponseShape:
    """Verify the response matches the integration contract."""

    @pytest.fixture(autouse=True)
    def _set_api_key(self):
        with patch.dict(os.environ, {"PODCASTER_API_KEY": "test-key-123"}):
            yield

    def _headers(self, body: bytes) -> dict[str, str]:
        return {"x-podcaster-api-key": "test-key-123", "Content-Length": str(len(body))}

    def test_response_has_all_contract_fields(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PODCASTER_ARTIFACT_BASE_URL", "https://test.example")
        monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(tmp_path))
        body = json.dumps(
            {
                "week": "2026-W24",
                "article_url": "https://example.com/article",
            }
        ).encode()
        handler = make_handler("POST", "/api/generate", body=body, headers=self._headers(body))
        resp = handler.get_response_json()
        expected_keys = {
            "job_id",
            "status",
            "manifest_url",
            "mp3_url",
            "wav_url",
            "transcript_url",
            "show_notes_url",
            "publishing_packet_url",
            "expires_at",
            "warnings",
            "errors",
        }
        assert set(resp.keys()) == expected_keys

    def test_error_response_has_contract_fields(self):
        body = json.dumps({}).encode()
        handler = make_handler("POST", "/api/generate", body=body, headers=self._headers(body))
        resp = handler.get_response_json()
        expected_keys = {
            "job_id",
            "status",
            "manifest_url",
            "mp3_url",
            "wav_url",
            "transcript_url",
            "show_notes_url",
            "publishing_packet_url",
            "expires_at",
            "warnings",
            "errors",
        }
        assert set(resp.keys()) == expected_keys
        assert resp["errors"]  # Should have validation errors


class TestReviewEndpoint:
    @pytest.fixture(autouse=True)
    def _set_api_key(self):
        with patch.dict(os.environ, {"PODCASTER_API_KEY": "test-key-123"}):
            yield

    def _headers(self, body: bytes) -> dict[str, str]:
        return {"x-podcaster-api-key": "test-key-123", "Content-Length": str(len(body))}

    def test_review_endpoint_requires_job_id_and_decision(self):
        body = json.dumps({"reviewer": "leela"}).encode()
        handler = make_handler("POST", "/api/review", body=body, headers=self._headers(body))
        assert handler.response_code == HTTPStatus.BAD_REQUEST
        assert "job_id is required" in handler.get_response_json()["errors"]

    @patch("podcaster.api.process_review_decision")
    def test_review_endpoint_returns_manifest_and_publish_status(self, mock_process):
        mock_process.return_value = JobPublishOutcome(
            manifest={"job_id": "podcast-1", "status": "published", "review_status": "approved"},
            publish_result=PublishResult(status="published"),
        )
        body = json.dumps(
            {"job_id": "podcast-1", "reviewer": "leela", "decision": "approved"}
        ).encode()
        handler = make_handler("POST", "/api/review", body=body, headers=self._headers(body))
        assert handler.response_code == HTTPStatus.OK
        response = handler.get_response_json()
        assert response["job_id"] == "podcast-1"
        assert response["publish_status"] == "published"
        assert response["manifest"]["status"] == "published"
