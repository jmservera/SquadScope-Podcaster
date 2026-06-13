"""Unit tests for podcaster.publish — Spotify for Creators integration (#182)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcaster.publish import (
    PublishResult,
    SpotifyPublishError,
    _is_dry_run,
    _is_enabled,
    publish_episode,
    verify_spotify_auth,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no Spotify env vars leak between tests."""
    for var in (
        "SPOTIFY_PUBLISH_ENABLED",
        "SPOTIFY_PUBLISH_DRY_RUN",
        "SPOTIFY_SHOW_ID",
        "SP_DC",
        "SP_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def spotify_env(monkeypatch):
    """Set up all required Spotify env vars."""
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SPOTIFY_SHOW_ID", "test-show-123")
    monkeypatch.setenv("SP_DC", "test-sp-dc-cookie")
    monkeypatch.setenv("SP_KEY", "test-sp-key-cookie")


@pytest.fixture
def mp3_file(tmp_path):
    """Create a dummy MP3 file."""
    f = tmp_path / "episode.mp3"
    # Minimal MP3 header (not valid audio, but good for testing upload)
    f.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 1000)
    return f


class TestEnabled:
    def test_disabled_by_default(self):
        assert not _is_enabled()

    def test_enabled_when_set(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
        assert _is_enabled()

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "True")
        assert _is_enabled()


class TestDryRun:
    def test_not_dry_run_by_default(self):
        assert not _is_dry_run()

    def test_dry_run_when_set(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        assert _is_dry_run()


class TestVerifyAuth:
    def test_missing_credentials(self):
        valid, msg = verify_spotify_auth()
        assert not valid
        assert "Missing Spotify credentials" in msg

    def test_dry_run_skips_live_check(self, spotify_env, monkeypatch):
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        valid, msg = verify_spotify_auth()
        assert valid
        assert "Dry-run" in msg

    @patch("podcaster.publish.requests.Session")
    def test_valid_auth(self, mock_session_cls, spotify_env):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.get.return_value = mock_resp
        valid, msg = verify_spotify_auth()
        assert valid
        assert "valid" in msg.lower()

    @patch("podcaster.publish.requests.Session")
    def test_expired_cookies(self, mock_session_cls, spotify_env):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_session.get.return_value = mock_resp
        valid, msg = verify_spotify_auth()
        assert not valid
        assert "expired" in msg.lower()


class TestPublishEpisode:
    def test_returns_failed_when_disabled(self, mp3_file):
        result = publish_episode(mp3_file, "Test", "<p>desc</p>")
        assert result.status == "failed"
        assert "disabled" in result.error

    def test_returns_failed_missing_creds(self, mp3_file, monkeypatch):
        monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
        result = publish_episode(mp3_file, "Test", "<p>desc</p>")
        assert result.status == "failed"
        assert "Missing" in result.error

    def test_dry_run_mode(self, mp3_file, spotify_env, monkeypatch):
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        result = publish_episode(mp3_file, "Test Episode", "<p>desc</p>")
        assert result.status == "draft"
        assert result.dry_run is True
        assert result.details["title"] == "Test Episode"

    def test_mp3_not_found(self, spotify_env, tmp_path):
        missing = tmp_path / "missing.mp3"
        result = publish_episode(missing, "Test", "<p>desc</p>")
        assert result.status == "failed"
        assert "not found" in result.error

    @patch("podcaster.publish._build_session")
    def test_full_publish_success(self, mock_build, mp3_file, spotify_env):
        mock_session = MagicMock()
        mock_build.return_value = mock_session

        # Step 1: resolve IDs
        resolve_resp = MagicMock()
        resolve_resp.json.return_value = {"stationId": "station-1", "userId": "user-1"}
        resolve_resp.raise_for_status = MagicMock()

        # Step 2: create episode
        create_resp = MagicMock()
        create_resp.json.return_value = {"id": 12345}
        create_resp.raise_for_status = MagicMock()

        # Step 3: get upload URL
        upload_url_resp = MagicMock()
        upload_url_resp.json.return_value = {
            "signedUrl": "https://gcs.example.com/upload",
            "uploadId": "upload-abc",
        }
        upload_url_resp.raise_for_status = MagicMock()

        # Step 4: upload audio
        upload_resp = MagicMock()
        upload_resp.headers = {"ETag": '"etag-123"'}
        upload_resp.raise_for_status = MagicMock()

        # Step 5: process + poll
        process_resp = MagicMock()
        process_resp.raise_for_status = MagicMock()

        poll_resp = MagicMock()
        poll_resp.json.return_value = {"status": "completed"}
        poll_resp.raise_for_status = MagicMock()

        # Step 6: metadata
        meta_resp = MagicMock()
        meta_resp.raise_for_status = MagicMock()

        # Wire up responses in order
        mock_session.request.side_effect = [
            resolve_resp,
            create_resp,
            upload_url_resp,
            upload_resp,
            process_resp,
            poll_resp,
            meta_resp,
        ]

        result = publish_episode(mp3_file, "Claracle W24", "<p>Episode notes</p>")
        assert result.status == "published"
        assert result.anchor_episode_id == 12345
        assert result.error is None

    @patch("podcaster.publish._build_session")
    def test_scheduled_publish(self, mock_build, mp3_file, spotify_env):
        mock_session = MagicMock()
        mock_build.return_value = mock_session

        responses = [
            _mock_json_resp({"stationId": "s1", "userId": "u1"}),
            _mock_json_resp({"id": 999}),
            _mock_json_resp({"signedUrl": "https://x.com/u", "uploadId": "up1"}),
            _mock_resp_with_headers({"ETag": '"e1"'}),
            _mock_json_resp({}),
            _mock_json_resp({"status": "completed"}),
            _mock_json_resp({}),
        ]
        mock_session.request.side_effect = responses

        publish_time = datetime(2026, 6, 20, 9, 0, 0, tzinfo=timezone.utc)
        result = publish_episode(
            mp3_file, "Scheduled Ep", "<p>desc</p>", publish_on=publish_time
        )
        assert result.status == "scheduled"
        assert result.anchor_episode_id == 999

    @patch("podcaster.publish._build_session")
    def test_api_error_graceful(self, mock_build, mp3_file, spotify_env):
        """Publish errors are caught — never raises."""
        mock_session = MagicMock()
        mock_build.return_value = mock_session

        import requests as req

        mock_session.request.side_effect = req.ConnectionError("network down")

        result = publish_episode(mp3_file, "Fail", "<p>x</p>")
        assert result.status == "failed"
        assert "failed after" in result.error


# -- Helpers --


def _mock_json_resp(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _mock_resp_with_headers(headers: dict) -> MagicMock:
    resp = MagicMock()
    resp.headers = headers
    resp.raise_for_status = MagicMock()
    return resp
