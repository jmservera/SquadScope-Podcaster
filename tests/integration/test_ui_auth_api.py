"""Integration test: UI MSAL auth config + API auth enforcement.

Verifies that:
1. The UI's MSAL auth configuration is structurally valid.
2. The API rejects unauthenticated requests (enforces auth gate).
"""

from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from http.server import HTTPServer

import pytest

from podcaster.api import GenerateHandler

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH_CONFIG_PATH = REPO_ROOT / "ui" / "src" / "authConfig.ts"

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not AUTH_CONFIG_PATH.exists(),
    reason="ui/src/authConfig.ts not found; UI source required",
)
def test_msal_auth_config_contains_required_fields() -> None:
    """MSAL config must export msalConfig with clientId, authority, and login scopes."""
    content = AUTH_CONFIG_PATH.read_text()

    assert "msalConfig" in content, "Missing msalConfig export"
    assert "clientId" in content, "Missing clientId in msalConfig"
    assert "authority" in content, "Missing authority in msalConfig"
    assert "loginRequest" in content, "Missing loginRequest export"
    assert "scopes" in content, "Missing scopes definition"
    assert "apiConfig" in content, "Missing apiConfig export for API token acquisition"


def test_api_rejects_unauthenticated_generate_request(monkeypatch) -> None:
    """POST /api/generate without x-podcaster-api-key returns 401."""
    monkeypatch.setenv("PODCASTER_API_KEY", "test-secret-key")

    server = HTTPServer(("127.0.0.1", 0), GenerateHandler)
    port = server.server_address[1]
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        body = json.dumps({"week": "2026-W25"}).encode()
        conn.request("POST", "/api/generate", body=body, headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        })
        resp = conn.getresponse()
        assert resp.status == 401
        data = json.loads(resp.read())
        assert "unauthorized" in data.get("error", "").lower()
        conn.close()
    finally:
        server.server_close()
        thread.join(timeout=5)


def test_api_rejects_wrong_api_key(monkeypatch) -> None:
    """POST /api/generate with wrong key returns 401."""
    monkeypatch.setenv("PODCASTER_API_KEY", "correct-key")

    server = HTTPServer(("127.0.0.1", 0), GenerateHandler)
    port = server.server_address[1]
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()

    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        body = json.dumps({"week": "2026-W25"}).encode()
        conn.request("POST", "/api/generate", body=body, headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "x-podcaster-api-key": "wrong-key",
        })
        resp = conn.getresponse()
        assert resp.status == 401
        conn.close()
    finally:
        server.server_close()
        thread.join(timeout=5)
