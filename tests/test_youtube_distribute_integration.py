"""Tests for YouTube integration into distribute_video() (#444)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcaster.video.distribution import (
    VideoDistributionConfig,
    distribute_video,
    upload_to_youtube,
    youtube_enabled_for_language,
)


@pytest.fixture
def video_file(tmp_path) -> Path:
    p = tmp_path / "video.mp4"
    p.write_bytes(b"\x00" * 4096)
    return p


# --- per-locale gating --------------------------------------------------------


def test_gate_disabled_when_youtube_off():
    cfg = VideoDistributionConfig(youtube_enabled=False)
    assert youtube_enabled_for_language(cfg, "en") is False


def test_gate_all_languages_when_unset():
    cfg = VideoDistributionConfig(youtube_enabled=True)
    assert youtube_enabled_for_language(cfg, "en", env={}) is True
    assert youtube_enabled_for_language(cfg, "fr", env={}) is True


def test_gate_allow_list():
    cfg = VideoDistributionConfig(youtube_enabled=True)
    env = {"VIDEO_YOUTUBE_LANGUAGES": "en, fr"}
    assert youtube_enabled_for_language(cfg, "en", env=env) is True
    assert youtube_enabled_for_language(cfg, "fr-FR", env=env) is True
    assert youtube_enabled_for_language(cfg, "es", env=env) is False


# --- distribute_video gating --------------------------------------------------


def test_distribute_video_skips_youtube_for_blocked_language(video_file, monkeypatch):
    monkeypatch.setenv("VIDEO_YOUTUBE_LANGUAGES", "en")
    cfg = VideoDistributionConfig(
        youtube_enabled=True,
        spotify_rss_enabled=False,
        spotify_upload_enabled=False,
        blob_archive_enabled=True,
        dry_run=True,
    )
    result = distribute_video(
        video_file,
        "job1",
        "t",
        "d",
        120.0,
        cfg,
        language="es",
    )
    # YouTube skipped (not attempted) → blob-only success.
    assert result.youtube_id is None
    assert result.blob_path is not None
    assert result.status == "completed"


def test_distribute_video_uploads_youtube_for_allowed_language(video_file, monkeypatch):
    monkeypatch.setenv("VIDEO_YOUTUBE_LANGUAGES", "en,es")
    cfg = VideoDistributionConfig(
        youtube_enabled=True,
        spotify_rss_enabled=False,
        blob_archive_enabled=False,
        dry_run=True,
    )
    result = distribute_video(
        video_file,
        "job1",
        "t",
        "d",
        120.0,
        cfg,
        language="es",
    )
    assert result.youtube_id == "dry-run-id"
    assert result.status == "completed"


def test_distribute_video_default_language_unchanged(video_file):
    # No allow-list set → English uploads as before.
    cfg = VideoDistributionConfig(
        youtube_enabled=True,
        spotify_rss_enabled=False,
        blob_archive_enabled=False,
        dry_run=True,
    )
    result = distribute_video(video_file, "job1", "t", "d", 120.0, cfg)
    assert result.youtube_id == "dry-run-id"


# --- large-file chunked delegation -------------------------------------------


class _InitTransport:
    """Transport that completes the resumable init then is taken over by the
    chunked uploader (which we stub via monkeypatch)."""

    def request_with_headers(self, url, *, method="GET", headers=None, data=None):
        return 200, {"location": "https://upload/session"}, b""

    def request(self, *a, **k):
        return 200, json.dumps({"access_token": "tok"}).encode()


def test_large_file_delegates_to_chunked_uploader(tmp_path, monkeypatch):
    big = tmp_path / "big.mp4"
    big.write_bytes(b"\x00" * 2048)  # size value irrelevant; we fake stat below
    _orig_stat = Path.stat

    def _fake_stat(self, *a, **k):
        if self.name == "big.mp4":
            return type("S", (), {"st_size": 200 * 1024 * 1024})()
        return _orig_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _fake_stat)

    cfg = VideoDistributionConfig(
        youtube_enabled=True,
        youtube_client_id="c",
        youtube_client_secret="s",
        youtube_refresh_token="rt",
    )

    # Stub token exchange and the chunked uploader module.
    monkeypatch.setattr(
        "podcaster.video.distribution._get_youtube_access_token", lambda c, h: "tok"
    )

    import sys
    import types

    fake_mod = types.ModuleType("podcaster.video.youtube")

    class _Result:
        succeeded = True
        video_id = "vid-big"
        video_url = "https://youtube.com/watch?v=vid-big"
        error = None

    fake_mod.upload_video = lambda *a, **k: _Result()
    monkeypatch.setitem(sys.modules, "podcaster.video.youtube", fake_mod)

    vid_id, vid_url = upload_to_youtube(big, "t", "d", cfg, transport=_InitTransport())
    assert vid_id == "vid-big"
    assert vid_url.endswith("vid-big")


def test_large_file_without_chunked_module_returns_none(tmp_path, monkeypatch):
    big = tmp_path / "big.mp4"
    big.write_bytes(b"\x00" * 2048)
    _orig_stat = Path.stat

    def _fake_stat(self, *a, **k):
        if self.name == "big.mp4":
            return type("S", (), {"st_size": 200 * 1024 * 1024})()
        return _orig_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _fake_stat)
    cfg = VideoDistributionConfig(
        youtube_enabled=True,
        youtube_client_id="c",
        youtube_client_secret="s",
        youtube_refresh_token="rt",
    )
    monkeypatch.setattr(
        "podcaster.video.distribution._get_youtube_access_token", lambda c, h: "tok"
    )
    import sys

    monkeypatch.setitem(sys.modules, "podcaster.video.youtube", None)  # ImportError on import

    vid_id, vid_url = upload_to_youtube(big, "t", "d", cfg, transport=_InitTransport())
    assert vid_id is None and vid_url is None
