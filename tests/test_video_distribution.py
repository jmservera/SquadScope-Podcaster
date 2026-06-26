"""Tests for podcaster.video.distribution (#242)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcaster.video.distribution import (
    DistributionResult,
    VideoDistributionConfig,
    _create_rss_feed,
    _escape_xml,
    archive_to_blob,
    distribute_video,
    update_spotify_rss,
    upload_to_spotify_episode,
    upload_to_youtube,
)


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    """Create a minimal valid video file for testing."""
    video = tmp_path / "test.mp4"
    # Write enough bytes to pass the minimum size check
    video.write_bytes(b"\x00" * 2048)
    return video


@pytest.fixture
def dry_run_config() -> VideoDistributionConfig:
    return VideoDistributionConfig(
        youtube_enabled=True,
        spotify_rss_enabled=True,
        blob_archive_enabled=True,
        dry_run=True,
    )


@pytest.fixture
def youtube_config() -> VideoDistributionConfig:
    return VideoDistributionConfig(
        youtube_enabled=True,
        youtube_client_id="test-client-id",
        youtube_client_secret="test-secret",
        youtube_refresh_token="test-refresh-token",
        youtube_category_id="28",
        youtube_privacy="unlisted",
        dry_run=False,
    )


class FakeTransport:
    """Fake HTTP transport for testing."""

    def __init__(self, responses: list[tuple[int, bytes]] | None = None):
        self.requests: list[dict] = []
        self._responses = list(responses or [])
        self._call_idx = 0

    def request(self, url, *, method="GET", headers=None, data=None):
        self.requests.append({"url": url, "method": method, "headers": headers, "data": data})
        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
            self._call_idx += 1
            return resp
        return (200, b'{"id": "test-video-id", "access_token": "fake-token"}')

    def request_with_headers(self, url, *, method="GET", headers=None, data=None):
        self.requests.append({"url": url, "method": method, "headers": headers, "data": data})
        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
            self._call_idx += 1
            return (resp[0], {"location": f"{url}/resumable-session"}, resp[1])
        return (200, {"location": f"{url}/resumable-session"}, b'{"id": "test-video-id", "access_token": "fake-token"}')


class FakeStorage:
    """Fake storage uploader for testing."""

    def __init__(self):
        self.uploads: list[tuple[str, bytes, str]] = []
        self._data: dict[str, bytes] = {}

    def upload(self, path: str, content: bytes, content_type: str) -> str:
        self.uploads.append((path, content, content_type))
        self._data[path] = content
        return f"https://storage.blob.core.windows.net/{path}"

    def get_bytes(self, path: str) -> bytes | None:
        return self._data.get(path)


# --- VideoDistributionConfig Tests ---


class TestVideoDistributionConfig:
    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("VIDEO_YOUTUBE_ENABLED", "true")
        monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "cid")
        monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "csec")
        monkeypatch.setenv("VIDEO_YOUTUBE_REFRESH_TOKEN", "rtok")
        monkeypatch.setenv("VIDEO_YOUTUBE_CATEGORY_ID", "22")
        monkeypatch.setenv("VIDEO_YOUTUBE_PRIVACY", "private")
        monkeypatch.setenv("VIDEO_SPOTIFY_RSS_ENABLED", "true")
        monkeypatch.setenv("VIDEO_SPOTIFY_RSS_FEED_PATH", "feeds/video.xml")
        monkeypatch.setenv("VIDEO_BLOB_ARCHIVE_ENABLED", "false")
        monkeypatch.setenv("VIDEO_DISTRIBUTE_DRY_RUN", "true")

        config = VideoDistributionConfig.from_env()
        assert config.youtube_enabled is True
        assert config.youtube_client_id == "cid"
        assert config.youtube_client_secret == "csec"
        assert config.youtube_refresh_token == "rtok"
        assert config.youtube_category_id == "22"
        assert config.youtube_privacy == "private"
        assert config.spotify_rss_enabled is True
        assert config.spotify_rss_feed_path == "feeds/video.xml"
        assert config.blob_archive_enabled is False
        assert config.dry_run is True

    def test_from_payload(self):
        payload = {
            "youtube_enabled": True,
            "youtube_category_id": "22",
            "youtube_privacy": "public",
            "spotify_rss_enabled": True,
            "spotify_rss_feed_path": "feeds/test.xml",
            "blob_archive_enabled": False,
            "dry_run": True,
        }
        config = VideoDistributionConfig.from_payload(payload)
        assert config.youtube_enabled is True
        assert config.youtube_category_id == "22"
        assert config.youtube_privacy == "public"
        assert config.spotify_rss_enabled is True
        assert config.blob_archive_enabled is False
        assert config.dry_run is True

    def test_defaults(self):
        config = VideoDistributionConfig()
        assert config.youtube_enabled is False
        assert config.spotify_rss_enabled is False
        assert config.blob_archive_enabled is True
        assert config.dry_run is False


# --- YouTube Upload Tests ---


class TestUploadToYouTube:
    def test_disabled(self, video_file):
        config = VideoDistributionConfig(youtube_enabled=False)
        vid_id, vid_url = upload_to_youtube(video_file, "title", "desc", config)
        assert vid_id is None
        assert vid_url is None

    def test_dry_run(self, video_file):
        config = VideoDistributionConfig(youtube_enabled=True, dry_run=True)
        vid_id, vid_url = upload_to_youtube(video_file, "title", "desc", config)
        assert vid_id == "dry-run-id"
        assert "dry-run-id" in vid_url

    def test_successful_upload(self, video_file, youtube_config):
        transport = FakeTransport(responses=[
            (200, json.dumps({"access_token": "tok123"}).encode()),
            (200, b'{}'),  # resumable init
            (200, json.dumps({"id": "yt-abc123"}).encode()),
        ])
        vid_id, vid_url = upload_to_youtube(
            video_file, "Test Title", "Test Desc", youtube_config,
            transport=transport,
        )
        assert vid_id == "yt-abc123"
        assert "yt-abc123" in vid_url

    def test_file_not_found(self, youtube_config):
        with pytest.raises(FileNotFoundError):
            upload_to_youtube(
                Path("/nonexistent.mp4"), "title", "desc", youtube_config,
                transport=FakeTransport(),
            )

    def test_file_too_small(self, tmp_path, youtube_config):
        small_file = tmp_path / "tiny.mp4"
        small_file.write_bytes(b"\x00" * 10)
        with pytest.raises(ValueError, match="too small"):
            upload_to_youtube(
                small_file, "title", "desc", youtube_config,
                transport=FakeTransport(),
            )

    def test_upload_failure_returns_none(self, video_file, youtube_config):
        transport = FakeTransport(responses=[
            (200, json.dumps({"access_token": "tok"}).encode()),
            (500, b"error"),  # init fails
        ])
        vid_id, vid_url = upload_to_youtube(
            video_file, "title", "desc", youtube_config,
            transport=transport,
        )
        assert vid_id is None
        assert vid_url is None


# --- Spotify RSS Tests ---


class TestUpdateSpotifyRss:
    def test_disabled(self):
        config = VideoDistributionConfig(spotify_rss_enabled=False)
        assert update_spotify_rss("url", "title", "desc", 120.0, config) is False

    def test_dry_run(self):
        config = VideoDistributionConfig(spotify_rss_enabled=True, dry_run=True)
        assert update_spotify_rss("url", "title", "desc", 120.0, config) is True

    def test_no_feed_path(self):
        config = VideoDistributionConfig(spotify_rss_enabled=True, spotify_rss_feed_path="")
        assert update_spotify_rss("url", "title", "desc", 120.0, config) is False

    def test_creates_new_feed(self):
        config = VideoDistributionConfig(
            spotify_rss_enabled=True,
            spotify_rss_feed_path="feeds/video.xml",
        )
        storage = FakeStorage()
        result = update_spotify_rss(
            "https://youtube.com/watch?v=abc",
            "Episode 1",
            "Test episode",
            300.0,
            config,
            storage=storage,
        )
        assert result is True
        assert len(storage.uploads) == 1
        path, content, ct = storage.uploads[0]
        assert path == "feeds/video.xml"
        assert b"Episode 1" in content
        assert b"video/mp4" in content
        assert ct == "application/rss+xml"

    def test_appends_to_existing_feed(self):
        config = VideoDistributionConfig(
            spotify_rss_enabled=True,
            spotify_rss_feed_path="feeds/video.xml",
        )
        storage = FakeStorage()
        existing_feed = (
            '<?xml version="1.0"?>\n<rss version="2.0">\n<channel>\n'
            "  <title>Test</title>\n</channel>\n</rss>\n"
        )
        storage._data["feeds/video.xml"] = existing_feed.encode()

        result = update_spotify_rss(
            "https://example.com/vid.mp4",
            "Episode 2",
            "Description",
            180.0,
            config,
            storage=storage,
        )
        assert result is True
        uploaded = storage.uploads[0][1].decode()
        assert "Episode 2" in uploaded
        assert "</channel>" in uploaded
        assert "<title>Test</title>" in uploaded


# --- Blob Archive Tests ---


class TestArchiveToBlob:
    def test_disabled(self, video_file):
        config = VideoDistributionConfig(blob_archive_enabled=False)
        assert archive_to_blob(video_file, "job1", config=config) is None

    def test_dry_run(self, video_file):
        config = VideoDistributionConfig(dry_run=True)
        result = archive_to_blob(video_file, "job1", config=config)
        assert result == "https://dry-run.blob.core.windows.net/jobs/job1/video/job1.mp4"

    def test_successful_upload(self, video_file):
        storage = FakeStorage()
        result = archive_to_blob(video_file, "job1", storage=storage)
        assert result == "https://storage.blob.core.windows.net/jobs/job1/video/job1.mp4"
        assert len(storage.uploads) == 1
        assert storage.uploads[0][2] == "video/mp4"

    def test_no_storage(self, video_file):
        result = archive_to_blob(video_file, "job1")
        assert result is None

    def test_file_not_found(self, tmp_path):
        storage = FakeStorage()
        result = archive_to_blob(tmp_path / "missing.mp4", "job1", storage=storage)
        assert result is None


# --- Distribute Video Tests ---


class TestDistributeVideo:
    def test_file_not_found(self, tmp_path, dry_run_config):
        result = distribute_video(
            tmp_path / "missing.mp4", "job1", "title", "desc", 120.0, dry_run_config,
        )
        assert result.status == "failed"
        assert "not found" in result.errors[0]

    def test_file_too_small(self, tmp_path, dry_run_config):
        small = tmp_path / "small.mp4"
        small.write_bytes(b"\x00" * 10)
        result = distribute_video(small, "job1", "title", "desc", 120.0, dry_run_config)
        assert result.status == "failed"
        assert "too small" in result.errors[0]

    def test_dry_run_all_targets(self, video_file, dry_run_config):
        result = distribute_video(
            video_file, "job1", "Test Video", "A test", 120.0, dry_run_config,
        )
        assert result.status == "completed"
        assert result.youtube_id == "dry-run-id"
        assert result.spotify_rss_updated is True
        assert result.blob_path is not None

    def test_youtube_only(self, video_file):
        config = VideoDistributionConfig(
            youtube_enabled=True, spotify_rss_enabled=False,
            blob_archive_enabled=False, dry_run=True,
        )
        result = distribute_video(
            video_file, "job1", "title", "desc", 120.0, config,
        )
        assert result.youtube_id == "dry-run-id"
        assert result.spotify_rss_updated is False
        assert result.status == "completed"

    def test_partial_failure(self, video_file):
        config = VideoDistributionConfig(
            youtube_enabled=True,
            spotify_rss_enabled=True,
            spotify_rss_feed_path="",  # Will cause RSS failure
            blob_archive_enabled=False,
            dry_run=False,
        )
        # YouTube in dry-run-like mode won't work without credentials
        # but RSS will fail due to empty path → partial
        transport = FakeTransport(responses=[
            (200, json.dumps({"access_token": "tok"}).encode()),
            (500, b"error"),
            (500, b"error"),
            (500, b"error"),
        ])
        result = distribute_video(
            video_file, "job1", "title", "desc", 120.0, config,
            transport=transport,
        )
        assert result.status == "failed"


# --- Helper Tests ---


class TestHelpers:
    def test_escape_xml(self):
        assert _escape_xml("<test>&'\"") == "&lt;test&gt;&amp;&apos;&quot;"

    def test_create_rss_feed(self):
        feed = _create_rss_feed("  <item><title>Test</title></item>\n")
        assert '<?xml version="1.0"' in feed
        assert "<channel>" in feed
        assert "<title>Test</title>" in feed
        assert "</channel>" in feed

    def test_distribution_result_succeeded(self):
        r = DistributionResult(status="completed")
        assert r.succeeded is True
        r2 = DistributionResult(status="partial")
        assert r2.succeeded is True
        r3 = DistributionResult(status="failed")
        assert r3.succeeded is False

    def test_blob_archive_alone_is_sufficient(self, tmp_path):
        """Blob archive alone is a sufficient distribution target (#337)."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 2048)
        config = VideoDistributionConfig(
            youtube_enabled=False,
            spotify_rss_enabled=False,
            spotify_upload_enabled=False,
            blob_archive_enabled=True,
            dry_run=True,
        )
        result = distribute_video(video_file, "job1", "title", "desc", 120.0, config)
        assert result.status == "completed"
        assert result.blob_path is not None

    def test_no_target_at_all_fails(self, tmp_path):
        """Distribution fails only if no target whatsoever is enabled (#337)."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 2048)
        config = VideoDistributionConfig(
            youtube_enabled=False,
            spotify_rss_enabled=False,
            spotify_upload_enabled=False,
            blob_archive_enabled=False,
            dry_run=False,
        )
        result = distribute_video(video_file, "job1", "title", "desc", 120.0, config)
        assert result.status == "failed"
        assert "No distribution target configured" in result.errors[0]


class TestSpotifyEpisodeUpload:
    """Tests for the Spotify episode video-upload target (#337)."""

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("VIDEO_SPOTIFY_UPLOAD_ENABLED", "true")
        config = VideoDistributionConfig.from_env()
        assert config.spotify_upload_enabled is True

    def test_from_payload(self):
        config = VideoDistributionConfig.from_payload({"spotify_upload_enabled": True})
        assert config.spotify_upload_enabled is True

    def test_no_anchor_id_still_uploads(self, video_file, monkeypatch):
        """Video upload proceeds even without an audio anchor_id (#340)."""
        from podcaster.publish import PublishResult

        def fake_upload(path, anchor_id, *, title=None, description=None, content_type="video/mp4", season_number=None, episode_number=None):
            return PublishResult(
                anchor_episode_id=999,
                status="draft",
                dry_run=False,
                details={},
            )

        monkeypatch.setattr("podcaster.publish.upload_video_to_episode", fake_upload)
        config = VideoDistributionConfig(spotify_upload_enabled=True)
        assert upload_to_spotify_episode(video_file, None, config) is True

    def test_dry_run_returns_true(self, video_file):
        config = VideoDistributionConfig(spotify_upload_enabled=True, dry_run=True)
        assert upload_to_spotify_episode(video_file, 42, config) is True

    def test_delegates_to_publish(self, video_file, monkeypatch):
        captured = {}

        def fake_upload(path, anchor_id, *, title=None, description=None, content_type="video/mp4", season_number=None, episode_number=None):
            captured["path"] = path
            captured["anchor_id"] = anchor_id
            captured["content_type"] = content_type
            captured["title"] = title
            captured["description"] = description
            captured["season_number"] = season_number
            captured["episode_number"] = episode_number
            from podcaster.publish import PublishResult

            return PublishResult(anchor_episode_id=12345, status="draft")

        monkeypatch.setattr("podcaster.publish.upload_video_to_episode", fake_upload)
        config = VideoDistributionConfig(spotify_upload_enabled=True)
        assert upload_to_spotify_episode(
            video_file, 99, config, title="My Show", description="desc",
            season_number=2026, episode_number=24,
        ) is True
        assert captured["anchor_id"] == 99
        assert captured["content_type"] == "video/mp4"
        assert captured["title"] == "My Show"
        assert captured["season_number"] == 2026
        assert captured["episode_number"] == 24

    def test_publish_failure_returns_false(self, video_file, monkeypatch):
        def fake_upload(path, anchor_id, *, title=None, description=None, content_type="video/mp4", season_number=None, episode_number=None):
            from podcaster.publish import PublishResult

            return PublishResult(status="failed", error="boom")

        monkeypatch.setattr("podcaster.publish.upload_video_to_episode", fake_upload)
        config = VideoDistributionConfig(spotify_upload_enabled=True)
        assert upload_to_spotify_episode(video_file, 99, config) is False

    def test_distribute_video_spotify_upload_dry_run(self, video_file):
        config = VideoDistributionConfig(
            spotify_upload_enabled=True,
            blob_archive_enabled=False,
            dry_run=True,
        )
        result = distribute_video(
            video_file, "job1", "title", "desc", 120.0, config,
            spotify_anchor_id=123,
        )
        assert result.spotify_upload_updated is True
        assert result.status == "completed"


class TestPlaylistIntegration:
    """distribute_video calls add_to_show_playlist after successful YouTube upload (#449)."""

    def test_playlist_add_called_on_youtube_success(self, video_file, youtube_config, monkeypatch):
        """add_to_show_playlist is invoked with correct args after a real upload."""
        calls: list[dict] = []

        def fake_add(config, locale, video_id, token, *, transport=None, position=None):
            calls.append({"locale": locale, "video_id": video_id})
            from podcaster.video.youtube_playlist import PlaylistAddResult
            return PlaylistAddResult(video_id=video_id, playlist_id="PLen", succeeded=True)

        monkeypatch.setattr("podcaster.video.distribution._add_to_show_playlist", fake_add)

        transport = FakeTransport(responses=[
            (200, json.dumps({"access_token": "tok"}).encode()),  # token (upload)
        ])
        # upload_to_youtube: token, init, upload; then token again for playlist
        transport._responses = [
            (200, json.dumps({"access_token": "tok"}).encode()),  # token for upload
        ]

        # dry_run skips playlist call — use a monkeypatched upload_to_youtube instead
        captured_upload: list = []

        def fake_upload(path, title, desc, cfg, *, tags=None, transport=None):
            captured_upload.append(True)
            return "yt-vid-001", "https://youtube.com/watch?v=yt-vid-001"

        monkeypatch.setattr("podcaster.video.distribution.upload_to_youtube", fake_upload)

        # Also patch _get_youtube_access_token so playlist token fetch doesn't need network
        monkeypatch.setattr(
            "podcaster.video.distribution._get_youtube_access_token",
            lambda cfg, http: "fake-access-token",
        )

        config_real = VideoDistributionConfig(
            youtube_enabled=True,
            youtube_client_id="id", youtube_client_secret="sec",
            youtube_refresh_token="ref",
            blob_archive_enabled=False,
            dry_run=False,
        )
        result = distribute_video(
            video_file, "job1", "title", "desc", 120.0, config_real,
            locale="es",
        )
        assert result.youtube_id == "yt-vid-001"
        assert len(calls) == 1
        assert calls[0]["video_id"] == "yt-vid-001"
        assert calls[0]["locale"] == "es"

    def test_playlist_skipped_on_dry_run(self, video_file, monkeypatch):
        """Playlist add is not called when dry_run=True."""
        calls: list = []
        monkeypatch.setattr(
            "podcaster.video.distribution._add_to_show_playlist",
            lambda *a, **kw: calls.append(True),
        )
        config = VideoDistributionConfig(
            youtube_enabled=True, blob_archive_enabled=False, dry_run=True,
        )
        distribute_video(video_file, "job1", "title", "desc", 120.0, config)
        assert calls == []

    def test_playlist_failure_does_not_abort_distribution(self, video_file, monkeypatch):
        """A playlist error is logged but does not fail the distribution result."""

        def fake_upload(path, title, desc, cfg, *, tags=None, transport=None):
            return "yt-vid-002", "https://youtube.com/watch?v=yt-vid-002"

        monkeypatch.setattr("podcaster.video.distribution.upload_to_youtube", fake_upload)
        monkeypatch.setattr(
            "podcaster.video.distribution._get_youtube_access_token",
            lambda cfg, http: "tok",
        )
        monkeypatch.setattr(
            "podcaster.video.distribution._add_to_show_playlist",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("network down")),
        )
        config = VideoDistributionConfig(
            youtube_enabled=True, blob_archive_enabled=False, dry_run=False,
        )
        result = distribute_video(video_file, "job1", "title", "desc", 120.0, config)
        assert result.youtube_id == "yt-vid-002"
        assert result.status == "completed"
