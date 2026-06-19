from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Callable
from unittest.mock import patch

from podcaster.auth_core import create_token
from tests.test_api import make_handler


class MemoryStorageBackend:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put_bytes(self, path: str, content: bytes, content_type: str) -> Any:
        self._blobs[path] = content
        return type("SA", (), {"path": path, "url": f"mem://{path}", "size_bytes": len(content), "content_type": content_type})()

    def get_bytes(self, path: str) -> bytes | None:
        return self._blobs.get(path)

    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> Any:
        current = self._blobs.get(path)
        updated = update(current)
        self._blobs[path] = updated
        return self.put_bytes(path, updated, content_type)

    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        return sorted(path for path in self._blobs if path.startswith(prefix))[:limit]

    def generate_download_url(self, path: str, *, expiry: datetime) -> Any:
        return type("URL", (), {"path": path, "url": f"mem://{path}", "expires_at": expiry.isoformat(), "method": "local", "signed": False, "https_only": False, "account_key_used": False})()


class TestCredentialsApi:
    def _headers(self, body: bytes = b"") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {create_token('admin', 'test-secret-256-bits-long-enough')}",
            "Content-Length": str(len(body)),
        }

    def test_get_credentials_returns_empty_list(self):
        storage = MemoryStorageBackend()
        with patch("podcaster.api.create_storage_backend", return_value=storage), patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("GET", "/api/credentials", headers=self._headers())
        assert handler.response_code == 200
        assert handler.get_response_json() == {"credentials": []}

    def test_post_put_delete_credentials_crud_without_returning_values(self):
        storage = MemoryStorageBackend()
        create_body = json.dumps(
            {
                "type": "spotify",
                "label": "Main show",
                "values": {"show_id": "show-123", "sp_dc": "cookie"},
            }
        ).encode()
        update_body = json.dumps(
            {
                "type": "youtube",
                "label": "Backup channel",
                "values": {"channel_id": "chan-456"},
            }
        ).encode()
        env = {
            "UI_AUTH_USERNAME": "admin",
            "UI_AUTH_PASSWORD": "hunter2",
            "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
        }
        with patch("podcaster.api.create_storage_backend", return_value=storage), patch.dict(os.environ, env, clear=True):
            create_handler = make_handler("POST", "/api/credentials", body=create_body, headers=self._headers(create_body))
            assert create_handler.response_code == 200
            created = create_handler.get_response_json()
            assert set(created) == {"id", "type", "label", "is_set", "created_at", "updated_at"}
            assert created["type"] == "spotify"
            assert created["label"] == "Main show"
            assert created["is_set"] is True
            assert "values" not in created

            encrypted_blob = storage.get_bytes("config/credentials.json")
            assert encrypted_blob is not None
            assert b"show-123" not in encrypted_blob
            assert b"cookie" not in encrypted_blob

            get_handler = make_handler("GET", "/api/credentials", headers=self._headers())
            assert get_handler.response_code == 200
            assert get_handler.get_response_json() == {"credentials": [created]}

            credential_id = created["id"]
            update_handler = make_handler(
                "PUT",
                f"/api/credentials/{credential_id}",
                body=update_body,
                headers=self._headers(update_body),
            )
            assert update_handler.response_code == 200
            updated = update_handler.get_response_json()
            assert updated["id"] == credential_id
            assert updated["type"] == "youtube"
            assert updated["label"] == "Backup channel"
            assert updated["created_at"] == created["created_at"]
            assert updated["updated_at"] >= created["updated_at"]

            delete_handler = make_handler("DELETE", f"/api/credentials/{credential_id}", headers=self._headers())
            assert delete_handler.response_code == 204
            assert delete_handler.response_body == b""

            final_handler = make_handler("GET", "/api/credentials", headers=self._headers())
            assert final_handler.get_response_json() == {"credentials": []}

    def test_credentials_accept_api_key_auth(self):
        storage = MemoryStorageBackend()
        body = json.dumps({"type": "api_key", "label": "Service", "values": {"key": "secret"}}).encode()
        headers = {"x-podcaster-api-key": "machine-key", "Content-Length": str(len(body))}
        with patch("podcaster.api.create_storage_backend", return_value=storage), patch.dict(
            os.environ,
            {
                "PODCASTER_API_KEY": "machine-key",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("POST", "/api/credentials", body=body, headers=headers)
        assert handler.response_code == 200
        assert handler.get_response_json()["type"] == "api_key"

    def test_credentials_require_ui_auth_secret_for_encryption(self):
        storage = MemoryStorageBackend()
        with patch("podcaster.api.create_storage_backend", return_value=storage), patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "",
                "PODCASTER_API_KEY": "machine-key",
            },
            clear=True,
        ):
            handler = make_handler("GET", "/api/credentials", headers={"x-podcaster-api-key": "machine-key"})
        assert handler.response_code == 501
        assert "UI_AUTH_SECRET" in handler.get_response_json()["error"]

    def test_credentials_reject_invalid_payload(self):
        storage = MemoryStorageBackend()
        body = json.dumps({"type": "bad", "label": "", "values": []}).encode()
        with patch("podcaster.api.create_storage_backend", return_value=storage), patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("POST", "/api/credentials", body=body, headers=self._headers(body))
        assert handler.response_code == 400
        assert handler.get_response_json()["error"] == "type must be one of: spotify, youtube, api_key"
