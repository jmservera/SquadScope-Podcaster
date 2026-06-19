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


class TestPodcastConfigApi:
    def _headers(self, body: bytes = b"") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {create_token('admin', 'test-secret-256-bits-long-enough')}",
            "Content-Length": str(len(body)),
        }

    def test_get_podcast_config_returns_defaults(self):
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
            handler = make_handler("GET", "/api/podcast-config", headers=self._headers())
        assert handler.response_code == 200
        assert handler.get_response_json() == {
            "name": "",
            "intro_music_url": None,
            "outro_music_url": None,
            "publish_targets": [],
            "auto_publish": False,
            "schedule": None,
        }

    def test_post_podcast_config_saves_and_reads_back(self):
        storage = MemoryStorageBackend()
        body = json.dumps(
            {
                "name": "Daily Signal",
                "intro_music_url": "https://example.com/intro.mp3",
                "outro_music_url": None,
                "publish_targets": [
                    {"type": "spotify", "config": {"show_id": "show-123"}},
                    {"type": "youtube", "config": {"channel_id": "channel-456"}},
                ],
                "auto_publish": True,
                "schedule": "0 9 * * 1-5",
            }
        ).encode()
        with patch("podcaster.api.create_storage_backend", return_value=storage), patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            post_handler = make_handler("POST", "/api/podcast-config", body=body, headers=self._headers(body))
            assert post_handler.response_code == 200
            saved = post_handler.get_response_json()
            assert saved["name"] == "Daily Signal"
            assert saved["auto_publish"] is True
            assert saved["publish_targets"][0]["type"] == "spotify"

            raw_blob = storage.get_bytes("config/podcast-config.json")
            assert raw_blob is not None
            assert json.loads(raw_blob.decode("utf-8")) == saved

            get_handler = make_handler("GET", "/api/podcast-config", headers=self._headers())
            assert get_handler.response_code == 200
            assert get_handler.get_response_json() == saved

    def test_podcast_config_accepts_api_key_auth(self):
        storage = MemoryStorageBackend()
        with patch("podcaster.api.create_storage_backend", return_value=storage), patch.dict(
            os.environ,
            {
                "PODCASTER_API_KEY": "machine-key",
            },
            clear=True,
        ):
            handler = make_handler("GET", "/api/podcast-config", headers={"x-podcaster-api-key": "machine-key"})
        assert handler.response_code == 200

    def test_podcast_config_rejects_invalid_payload(self):
        storage = MemoryStorageBackend()
        body = json.dumps({"name": "", "publish_targets": {}, "auto_publish": "yes"}).encode()
        with patch("podcaster.api.create_storage_backend", return_value=storage), patch.dict(
            os.environ,
            {
                "UI_AUTH_USERNAME": "admin",
                "UI_AUTH_PASSWORD": "hunter2",
                "UI_AUTH_SECRET": "test-secret-256-bits-long-enough",
            },
            clear=True,
        ):
            handler = make_handler("POST", "/api/podcast-config", body=body, headers=self._headers(body))
        assert handler.response_code == 400
        assert handler.get_response_json()["error"] == "name is required"
