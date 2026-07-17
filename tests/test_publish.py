"""Unit tests for podcaster.publish — Spotify for Creators integration (#182)."""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from unittest.mock import MagicMock, patch

import pytest

from podcaster.config import MAX_SPOTIFY_DESCRIPTION_CHARS, SpotifyPublishConfig, truncate_html
from podcaster.publish import (
    SpotifyPublishError,
    _build_session,
    _is_dry_run,
    _is_enabled,
    _live_publish_allowed,
    _warn_live_publish_downgraded_once,
    publish_episode,
    verify_spotify_auth,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no Spotify env vars leak between tests."""
    for var in (
        "SPOTIFY_PUBLISH_ENABLED",
        "SPOTIFY_PUBLISH_DRY_RUN",
        "SPOTIFY_ALLOW_LIVE_PUBLISH",
        "SPOTIFY_SHOW_ID",
        "SPOTIFY_CLIENT_ID",
        "SP_DC",
        "SP_KEY",
        "PODCASTER_SPOTIFY_RECONCILE",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def spotify_env(monkeypatch):
    """Set up all required Spotify env vars.

    Represents a fully-configured operator that has explicitly accepted the
    unofficial-API risk (#602), so live publishing is allowed here — the
    fail-safe downgrade is exercised separately in ``TestLivePublishGuard``.
    """
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SPOTIFY_ALLOW_LIVE_PUBLISH", "true")
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


@pytest.fixture
def wav_file(tmp_path):
    f = tmp_path / "episode.wav"
    f.write_bytes(b"RIFF" + b"\x00" * 1000)
    return f


class _BalancedHtmlParser(HTMLParser):
    _void_tags = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag not in self._void_tags:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(tag)
            return
        self.stack.pop()


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

    @patch("podcaster.publish._build_session")
    def test_valid_auth(self, mock_build_session, spotify_env):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"stationId": "1", "userId": "2"}
        mock_session.get.return_value = mock_resp
        valid, msg = verify_spotify_auth()
        assert valid
        assert "valid" in msg.lower()

    @patch("podcaster.publish._build_session")
    def test_expired_cookies(self, mock_build_session, spotify_env):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_session.get.return_value = mock_resp
        valid, msg = verify_spotify_auth()
        assert not valid
        assert "expired" in msg.lower()

    @patch("podcaster.publish._build_session")
    def test_missing_ids_is_invalid_auth(self, mock_build_session, spotify_env):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        mock_session.get.return_value = mock_resp
        valid, msg = verify_spotify_auth()
        assert not valid
        assert "missing ids" in msg.lower()

    @patch("podcaster.publish._build_session")
    def test_build_session_error_returns_invalid_auth(self, mock_build_session, spotify_env):
        mock_build_session.side_effect = SpotifyPublishError("bad session")

        valid, msg = verify_spotify_auth()

        assert not valid
        assert msg == "bad session"

    @patch("podcaster.publish._build_session")
    def test_non_json_success_response_is_invalid_auth(self, mock_build_session, spotify_env):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not json")
        mock_session.get.return_value = mock_resp

        valid, msg = verify_spotify_auth()

        assert not valid
        assert "not valid json" in msg.lower()


class TestBuildSession:
    @patch("podcaster.publish.SpotifyConnector")
    def test_build_session_uses_spotifyconnector_bearer(self, mock_connector_cls):
        mock_connector = MagicMock()
        mock_connector._bearer = "test-bearer"
        mock_connector_cls.return_value = mock_connector

        session = _build_session("cookie-dc", "cookie-key", "show-123")

        mock_connector_cls.assert_called_once_with(
            base_url="https://generic.wg.spotify.com/podcasters/v0",
            client_id="05a1371ee5194c27860b3ff3ff3979d2",
            podcast_id="show-123",
            sp_dc="cookie-dc",
            sp_key="cookie-key",
        )
        mock_connector._authenticate.assert_called_once_with()
        assert session.headers["Authorization"] == "Bearer test-bearer"


class TestPublishEpisode:
    def test_spotify_publish_config_resolution(self):
        config = SpotifyPublishConfig.from_payload(
            {
                "spotify_publish": {
                    "title": "2026-W24: Signal",
                    "description": "<p>Summary</p><p>Credits</p>",
                    "season_number": "{year}",
                    "episode_number": "{week}",
                    "publish_mode": "draft",
                    "upload_format": "wav",
                }
            }
        )

        assert config.title == "2026-W24: Signal"
        assert config.description == "<p>Summary</p><p>Credits</p>"
        assert config.resolve_season(2026, 24) == 2026
        assert config.resolve_episode(2026, 24) == 24
        assert config.upload_format == "wav"

    def test_spotify_publish_config_truncates_with_warning(self, caplog):
        caplog.set_level("WARNING")
        config = SpotifyPublishConfig(
            title="T" * 250,
            description="<p><strong>" + ("D" * 5000) + "</strong></p>",
        )

        assert config.title == "T" * 200
        assert len(config.description) <= MAX_SPOTIFY_DESCRIPTION_CHARS
        assert config.description.endswith("</strong></p>")
        assert "Spotify publish title exceeded 200 chars; truncating." in caplog.text
        assert "Spotify publish description exceeded 4000 chars; truncating." in caplog.text

    def test_truncate_html_closes_nested_tags(self):
        truncated = truncate_html("<p><strong>" + ("Signal " * 1000) + "</strong></p>", 120)
        parser = _BalancedHtmlParser()
        parser.feed(truncated)
        parser.close()

        assert len(truncated) <= 120
        assert truncated.endswith("</strong></p>")
        assert parser.errors == []
        assert parser.stack == []

    def test_truncate_html_drops_partial_trailing_tag(self):
        html = "<p>" + ("x" * 3988) + '<a href="https://example.com/really/long/link">link</a></p>'
        truncated = truncate_html(html, MAX_SPOTIFY_DESCRIPTION_CHARS)
        parser = _BalancedHtmlParser()
        parser.feed(truncated)
        parser.close()

        assert len(truncated) <= MAX_SPOTIFY_DESCRIPTION_CHARS
        assert truncated.endswith("</p>")
        assert "<a href" not in truncated
        assert parser.errors == []
        assert parser.stack == []

    def test_returns_failed_when_disabled(self, mp3_file, wav_file):
        result = publish_episode(mp3_file, "Test", "<p>desc</p>", wav_path=wav_file)
        assert result.status == "failed"
        assert "disabled" in result.error

    def test_returns_failed_missing_creds(self, mp3_file, wav_file, monkeypatch):
        monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
        result = publish_episode(mp3_file, "Test", "<p>desc</p>", wav_path=wav_file)
        assert result.status == "failed"
        assert "Missing" in result.error

    def test_dry_run_mode(self, mp3_file, wav_file, spotify_env, monkeypatch):
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        result = publish_episode(mp3_file, "Test Episode", "<p>desc</p>", wav_path=wav_file)
        assert result.status == "published"
        assert result.dry_run is True
        assert result.details["title"] == "Test Episode"
        assert result.details["upload_format"] == "wav"
        assert result.details["upload_path"] == str(wav_file)

    def test_wav_not_found_when_wav_upload_selected(self, spotify_env, tmp_path):
        missing_mp3 = tmp_path / "missing.mp3"
        missing_wav = tmp_path / "missing.wav"
        result = publish_episode(missing_mp3, "Test", "<p>desc</p>", wav_path=missing_wav)
        assert result.status == "failed"
        assert "not found" in result.error

    @patch("podcaster.publish._build_session")
    def test_full_publish_success(self, mock_build, mp3_file, wav_file, spotify_env):
        mock_session = MagicMock()
        mock_build.return_value = mock_session

        # Step 1: resolve IDs
        resolve_resp = MagicMock()
        resolve_resp.json.return_value = {"stationId": "1", "userId": "2"}
        resolve_resp.raise_for_status = MagicMock()

        # Step 2: create episode
        create_resp = MagicMock()
        create_resp.json.return_value = {"episodeId": 12345}
        create_resp.raise_for_status = MagicMock()

        # Step 3: get upload URL
        upload_url_resp = MagicMock()
        upload_url_resp.json.return_value = {
            "signedUrl": "https://gcs.example.com/upload",
            "requestUuid": "upload-abc",
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
        publish_resp = MagicMock()
        publish_resp.raise_for_status = MagicMock()

        # Step 7: publish
        mock_session.request.side_effect = [
            resolve_resp,
            create_resp,
            upload_url_resp,
            upload_resp,
            process_resp,
            poll_resp,
            meta_resp,
            publish_resp,
        ]

        result = publish_episode(
            mp3_file, "Claracle W24", "<p>Episode notes</p>", wav_path=wav_file
        )
        assert result.status == "published"
        assert result.anchor_episode_id == 12345
        assert result.error is None
        create_call = mock_session.request.call_args_list[1]
        assert create_call.kwargs["json"] == {"hourOffset": 0}
        upload_call = mock_session.request.call_args_list[3]
        assert upload_call.kwargs["headers"]["Content-Type"] == "audio/wav"
        assert upload_call.kwargs["data"] == wav_file.read_bytes()
        signed_url_call = mock_session.request.call_args_list[2]
        assert signed_url_call.kwargs["params"]["filename"] == wav_file.name
        assert signed_url_call.kwargs["params"]["type"] == "audio/wav"
        process_call = mock_session.request.call_args_list[4]
        assert process_call.kwargs["json"]["episodeId"] == 12345
        assert process_call.kwargs["json"]["stationId"] == 1
        assert process_call.kwargs["json"]["userId"] == 2
        metadata_call = mock_session.request.call_args_list[-2]
        assert metadata_call.kwargs["json"]["userId"] == 2
        assert metadata_call.kwargs["json"]["isPublished"] is True
        assert metadata_call.kwargs["json"]["podcastEpisodeIsExplicit"] is False
        publish_call = mock_session.request.call_args_list[-1]
        assert publish_call.args[1].endswith("/publish?isMumsCompatible=true")

    @patch("podcaster.publish._build_session")
    def test_scheduled_mode_passes_date(self, mock_build, mp3_file, wav_file, spotify_env):
        mock_session = MagicMock()
        mock_build.return_value = mock_session

        responses = [
            _mock_json_resp({"stationId": "1", "userId": "2"}),
            _mock_json_resp({"episodeId": 999}),
            _mock_json_resp({"signedUrl": "https://x.com/u", "uploadId": "up1"}),
            _mock_resp_with_headers({"ETag": '"e1"'}),
            _mock_json_resp({}),
            _mock_json_resp({"status": "completed"}),
            _mock_json_resp({}),
            _mock_json_resp({}),
        ]
        mock_session.request.side_effect = responses

        publish_config = SpotifyPublishConfig(
            title="2026-W25: Scheduled Ep",
            description="<p>desc</p>",
            season_number="{year}",
            episode_number="{week}",
            publish_mode="2026-06-20T09:00:00Z",
            upload_format="wav",
        )
        result = publish_episode(
            mp3_file,
            "Scheduled Ep",
            "<p>desc</p>",
            spotify_publish_config=publish_config,
            year=2026,
            week=25,
            wav_path=wav_file,
        )
        assert result.status == "scheduled"
        assert result.anchor_episode_id == 999
        metadata_call = mock_session.request.call_args_list[-2]
        assert metadata_call.kwargs["json"]["title"] == "2026-W25: Scheduled Ep"
        assert metadata_call.kwargs["json"]["seasonNumber"] == 2026
        assert metadata_call.kwargs["json"]["episodeNumber"] == 25
        assert metadata_call.kwargs["json"]["isPublished"] is False
        assert metadata_call.kwargs["json"]["publishOn"] == "2026-06-20T09:00:00.000Z"
        assert (
            metadata_call.kwargs["json"]["wizardDraftedToPublishOn"] == "2026-06-20T09:00:00.000Z"
        )
        publish_call = mock_session.request.call_args_list[-1]
        assert publish_call.kwargs["json"]["publishOn"] == "2026-06-20T09:00:00Z"

    @patch("podcaster.publish._build_session")
    def test_draft_mode_does_not_publish(self, mock_build, mp3_file, wav_file, spotify_env):
        mock_session = MagicMock()
        mock_build.return_value = mock_session
        mock_session.request.side_effect = [
            _mock_json_resp({"stationId": "1", "userId": "2"}),
            _mock_json_resp({"episodeId": 999}),
            _mock_json_resp({"signedUrl": "https://x.com/u", "uploadId": "up1"}),
            _mock_resp_with_headers({"ETag": '"e1"'}),
            _mock_json_resp({}),
            _mock_json_resp({"status": "completed"}),
            _mock_json_resp({}),
            _mock_json_resp({}),
        ]

        result = publish_episode(
            mp3_file,
            "Fallback Title",
            "<p>Fallback desc</p>",
            spotify_publish_config=SpotifyPublishConfig(
                title="2026-W24: Signal",
                description="<p>Summary</p><p>Credits</p>",
                season_number="{year}",
                episode_number="{week}",
                publish_mode="draft",
                upload_format="wav",
            ),
            year=2026,
            week=24,
            wav_path=wav_file,
        )

        assert result.status == "draft"
        assert result.anchor_episode_id == 999
        assert mock_session.request.call_count == 7
        metadata_call = mock_session.request.call_args_list[-1]
        assert metadata_call.kwargs["json"]["title"] == "2026-W24: Signal"
        assert metadata_call.kwargs["json"]["description"] == "<p>Summary</p><p>Credits</p>"
        assert metadata_call.kwargs["json"]["seasonNumber"] == 2026
        assert metadata_call.kwargs["json"]["episodeNumber"] == 24
        assert metadata_call.kwargs["json"]["isPublished"] is False

    @patch("podcaster.publish._build_session")
    def test_immediate_downgraded_to_draft_without_opt_in(
        self, mock_build, mp3_file, wav_file, spotify_env, monkeypatch, caplog
    ):
        # #602: publishing is enabled but the live opt-in is NOT set, so an
        # "immediate" request must fail safe to a DRAFT — no publish call and
        # isPublished stays False so nothing is ever silently made public.
        monkeypatch.delenv("SPOTIFY_ALLOW_LIVE_PUBLISH", raising=False)
        _warn_live_publish_downgraded_once.cache_clear()
        assert _live_publish_allowed() is False

        mock_session = MagicMock()
        mock_build.return_value = mock_session
        mock_session.request.side_effect = [
            _mock_json_resp({"stationId": "1", "userId": "2"}),
            _mock_json_resp({"episodeId": 4242}),
            _mock_json_resp({"signedUrl": "https://x.com/u", "uploadId": "up1"}),
            _mock_resp_with_headers({"ETag": '"e1"'}),
            _mock_json_resp({}),
            _mock_json_resp({"status": "completed"}),
            _mock_json_resp({}),
        ]

        with caplog.at_level("WARNING"):
            result = publish_episode(
                mp3_file,
                "Live Attempt",
                "<p>notes</p>",
                spotify_publish_config=SpotifyPublishConfig(publish_mode="immediate"),
                wav_path=wav_file,
            )

        assert result.status == "draft"
        assert result.anchor_episode_id == 4242
        # No publish step: metadata (7th) is the last request, no /publish call.
        assert mock_session.request.call_count == 7
        metadata_call = mock_session.request.call_args_list[-1]
        assert metadata_call.kwargs["json"]["isPublished"] is False
        assert "publishOn" not in metadata_call.kwargs["json"]
        assert "SPOTIFY_ALLOW_LIVE_PUBLISH" in caplog.text

    @patch("podcaster.publish._build_session")
    def test_scheduled_downgraded_to_draft_without_opt_in(
        self, mock_build, mp3_file, wav_file, spotify_env, monkeypatch
    ):
        # #602: a scheduled request is also downgraded to a draft when the live
        # opt-in is absent — the publishOn schedule must not be applied.
        monkeypatch.delenv("SPOTIFY_ALLOW_LIVE_PUBLISH", raising=False)
        _warn_live_publish_downgraded_once.cache_clear()

        mock_session = MagicMock()
        mock_build.return_value = mock_session
        mock_session.request.side_effect = [
            _mock_json_resp({"stationId": "1", "userId": "2"}),
            _mock_json_resp({"episodeId": 555}),
            _mock_json_resp({"signedUrl": "https://x.com/u", "uploadId": "up1"}),
            _mock_resp_with_headers({"ETag": '"e1"'}),
            _mock_json_resp({}),
            _mock_json_resp({"status": "completed"}),
            _mock_json_resp({}),
        ]

        result = publish_episode(
            mp3_file,
            "Scheduled Attempt",
            "<p>notes</p>",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="2026-06-20T09:00:00Z"),
            wav_path=wav_file,
        )

        assert result.status == "draft"
        assert mock_session.request.call_count == 7
        metadata_call = mock_session.request.call_args_list[-1]
        assert metadata_call.kwargs["json"]["isPublished"] is False
        assert "publishOn" not in metadata_call.kwargs["json"]

    def test_dry_run_reports_draft_without_opt_in(
        self, mp3_file, wav_file, spotify_env, monkeypatch
    ):
        # #602: even in dry-run the reported behaviour must reflect the safe
        # downgrade when the live opt-in is absent.
        monkeypatch.delenv("SPOTIFY_ALLOW_LIVE_PUBLISH", raising=False)
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        _warn_live_publish_downgraded_once.cache_clear()

        result = publish_episode(
            mp3_file,
            "Dry Live Attempt",
            "<p>notes</p>",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="immediate"),
            wav_path=wav_file,
        )

        assert result.dry_run is True
        assert result.status == "draft"
        assert result.details["publish_behavior"] == "draft"

    def test_dry_run_reports_published_with_opt_in(
        self, mp3_file, wav_file, spotify_env, monkeypatch
    ):
        # With the opt-in present (via spotify_env) live publishing is allowed,
        # so an immediate request is reported as published.
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        result = publish_episode(
            mp3_file,
            "Dry Live Attempt",
            "<p>notes</p>",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="immediate"),
            wav_path=wav_file,
        )

        assert result.status == "published"
        assert result.details["publish_behavior"] == "immediate"

    @patch("podcaster.publish._build_session")
    def test_missing_config_fallback(self, mock_build, mp3_file, wav_file, spotify_env):
        mock_session = MagicMock()
        mock_build.return_value = mock_session
        mock_session.request.side_effect = [
            _mock_json_resp({"stationId": "1", "userId": "2"}),
            _mock_json_resp({"id": 321}),
            _mock_json_resp({"signedUrl": "https://x.com/u", "uploadId": "up1"}),
            _mock_resp_with_headers({"ETag": '"e1"'}),
            _mock_json_resp({}),
            _mock_json_resp({"status": "completed"}),
            _mock_json_resp({}),
            _mock_json_resp({}),
        ]

        result = publish_episode(
            mp3_file, "Original Title", "<p>Original desc</p>", wav_path=wav_file
        )

        assert result.status == "published"
        metadata_call = mock_session.request.call_args_list[-2]
        assert metadata_call.kwargs["json"]["title"] == "Original Title"
        assert "seasonNumber" not in metadata_call.kwargs["json"]
        assert metadata_call.kwargs["json"]["isPublished"] is True

    @patch("podcaster.publish._build_session")
    def test_missing_config_ignores_publish_on(self, mock_build, mp3_file, wav_file, spotify_env):
        mock_session = MagicMock()
        mock_build.return_value = mock_session
        mock_session.request.side_effect = [
            _mock_json_resp({"stationId": "1", "userId": "2"}),
            _mock_json_resp({"id": 321}),
            _mock_json_resp({"signedUrl": "https://x.com/u", "uploadId": "up1"}),
            _mock_resp_with_headers({"ETag": '"e1"'}),
            _mock_json_resp({}),
            _mock_json_resp({"status": "completed"}),
            _mock_json_resp({}),
            _mock_json_resp({}),
        ]

        result = publish_episode(
            mp3_file,
            "Original Title",
            "<p>Original desc</p>",
            publish_on=datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc),
            wav_path=wav_file,
        )

        assert result.status == "published"
        metadata_call = mock_session.request.call_args_list[-2]
        assert metadata_call.kwargs["json"]["isPublished"] is True
        assert "publishOn" not in metadata_call.kwargs["json"]
        assert "wizardDraftedToPublishOn" not in metadata_call.kwargs["json"]

    @patch("podcaster.publish._build_session")
    def test_appends_timestamps_when_within_limit(
        self, mock_build, mp3_file, wav_file, spotify_env
    ):
        mock_session = MagicMock()
        mock_build.return_value = mock_session
        mock_session.request.side_effect = [
            _mock_json_resp({"stationId": "1", "userId": "2"}),
            _mock_json_resp({"id": 321}),
            _mock_json_resp({"signedUrl": "https://x.com/u", "uploadId": "up1"}),
            _mock_resp_with_headers({"ETag": '"e1"'}),
            _mock_json_resp({}),
            _mock_json_resp({"status": "completed"}),
            _mock_json_resp({}),
            _mock_json_resp({}),
        ]

        description = "<p>Episode notes</p>"
        timestamps_html = "<p>Timestamps:</p><p>00:00 Intro<br/>01:30 Main</p>"

        result = publish_episode(
            mp3_file,
            "Original Title",
            description,
            wav_path=wav_file,
            timestamps_html=timestamps_html,
        )

        assert result.status == "published"
        metadata_call = mock_session.request.call_args_list[-2]
        assert metadata_call.kwargs["json"]["description"] == description + timestamps_html

    @patch("podcaster.publish._build_session")
    def test_api_error_graceful(self, mock_build, mp3_file, wav_file, spotify_env):
        """Publish errors are caught — never raises."""
        mock_session = MagicMock()
        mock_build.return_value = mock_session

        import requests as req

        mock_session.request.side_effect = req.ConnectionError("network down")

        result = publish_episode(mp3_file, "Fail", "<p>x</p>", wav_path=wav_file)
        assert result.status == "failed"
        assert "failed after" in result.error

    @patch("podcaster.publish._build_session")
    def test_mp3_upload_format_is_supported(self, mock_build, mp3_file, wav_file, spotify_env):
        mock_session = MagicMock()
        mock_build.return_value = mock_session
        mock_session.request.side_effect = [
            _mock_json_resp({"stationId": "1", "userId": "2"}),
            _mock_json_resp({"episodeId": 999}),
            _mock_json_resp({"signedUrl": "https://x.com/u", "uploadId": "up1"}),
            _mock_resp_with_headers({"ETag": '"e1"'}),
            _mock_json_resp({}),
            _mock_json_resp({"status": "completed"}),
            _mock_json_resp({}),
            _mock_json_resp({}),
        ]

        result = publish_episode(
            mp3_file,
            "Original Title",
            "<p>Original desc</p>",
            spotify_publish_config=SpotifyPublishConfig(
                publish_mode="immediate", upload_format="mp3"
            ),
            wav_path=wav_file,
        )

        assert result.status == "published"
        upload_call = mock_session.request.call_args_list[3]
        assert upload_call.kwargs["headers"]["Content-Type"] == "audio/mpeg"
        assert upload_call.kwargs["data"] == mp3_file.read_bytes()

    def test_timestamps_html_included_in_dry_run(
        self, mp3_file, wav_file, spotify_env, monkeypatch
    ):
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        result = publish_episode(
            mp3_file,
            "Test",
            "<p>Base desc</p>",
            wav_path=wav_file,
            timestamps_html="<p>Timestamps:</p><p>00:00 Intro</p>",
        )
        assert result.status == "published"
        assert result.dry_run is True


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


class TestSafeUrl:
    """Verify signed URL redaction to prevent token leakage in logs."""

    def test_strips_query_params(self):
        from podcaster.publish import _safe_url

        url = "https://storage.googleapis.com/bucket/ep.mp3?X-Goog-Signature=abc&X-Goog-Credential=xyz"
        result = _safe_url(url)
        assert "X-Goog-Signature" not in result
        assert "[REDACTED]" in result
        assert result.startswith("https://storage.googleapis.com/bucket/ep.mp3")

    def test_plain_url_unchanged(self):
        from podcaster.publish import _safe_url

        url = "https://creators.spotify.com/api/v1/shows"
        assert _safe_url(url) == url


class TestVideoArtifactDetection:
    """Tests for video MP4 detection in publish_episode (#268)."""

    def test_mp4_preferred_over_audio_when_present(self, tmp_path, spotify_env, monkeypatch):
        """When an MP4 exists alongside the MP3, it is preferred for Spotify upload."""
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        mp3_file = tmp_path / "episode.mp3"
        mp3_file.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 1000)
        wav_file = tmp_path / "episode.wav"
        wav_file.write_bytes(b"RIFF" + b"\x00" * 1000)
        mp4_file = tmp_path / "episode.mp4"
        mp4_file.write_bytes(b"\x00\x00\x00\x1cftyp" + b"\x00" * 2000)

        result = publish_episode(mp3_file, "Test", "<p>desc</p>", wav_path=wav_file)
        assert result.dry_run is True
        assert result.status == "published"
        # MP4 is preferred when present
        assert result.details["upload_path"] == str(mp4_file)
        assert result.details["upload_format"] == "mp4"
        assert result.details["content_type"] == "video/mp4"

    def test_no_mp4_uses_audio(self, tmp_path, spotify_env, monkeypatch):
        """Without MP4, normal audio upload path is used."""
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        mp3_file = tmp_path / "episode.mp3"
        mp3_file.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 1000)
        wav_file = tmp_path / "episode.wav"
        wav_file.write_bytes(b"RIFF" + b"\x00" * 1000)

        result = publish_episode(mp3_file, "Test", "<p>desc</p>", wav_path=wav_file)
        assert result.dry_run is True
        assert result.details["upload_format"] == "wav"
        assert result.details["upload_path"] == str(wav_file)
        assert result.details["content_type"] == "audio/wav"


class TestProcessUpload:
    """Tests for _process_upload payload and polling behaviour (#292)."""

    def test_process_upload_audio_payload(self):
        """POST payload uses uploadType=default and isExtractedFromVideo=False for audio."""
        from podcaster.publish import _process_upload

        captured = {}

        session = MagicMock()

        def side_effect(method, url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if method == "POST":
                captured["json"] = kwargs.get("json", {})
                resp.status_code = 200
                resp.json.return_value = {}
                resp.headers = {}
                return resp
            # GET poll: return processed immediately
            resp.status_code = 200
            resp.json.return_value = {"request": {"state": "processed"}}
            return resp

        session.request.side_effect = side_effect

        with patch("podcaster.publish.time.sleep"):
            _process_upload(
                session,
                upload_id="u123",
                anchor_id=42,
                station_id="99",
                user_id="7",
                filename="ep.mp3",
                content_type="audio/mpeg",
            )

        assert captured["json"]["uploadType"] == "default"
        assert captured["json"]["isExtractedFromVideo"] is False
        assert captured["json"]["isMultipartUpload"] is False
        assert "parts" not in captured["json"]

    def test_process_upload_video_payload(self):
        """POST payload uses uploadType=video and isExtractedFromVideo=True for video."""
        from podcaster.publish import _process_upload

        captured = {}

        session = MagicMock()

        def side_effect(method, url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if method == "POST":
                captured["json"] = kwargs.get("json", {})
                resp.status_code = 200
                resp.json.return_value = {}
                resp.headers = {}
                return resp
            resp.status_code = 200
            resp.json.return_value = {"request": {"state": "processed"}}
            return resp

        session.request.side_effect = side_effect

        with patch("podcaster.publish.time.sleep"):
            _process_upload(
                session,
                upload_id="v456",
                anchor_id=10,
                station_id="5",
                user_id="3",
                filename="ep.mp4",
                content_type="video/mp4",
                parts_etags=[{"partNumber": 1, "etag": "etag456"}],
            )

        assert captured["json"]["uploadType"] == "video"
        assert captured["json"]["isExtractedFromVideo"] is False
        assert captured["json"]["isMultipartUpload"] is True
        assert captured["json"]["parts"] == [{"partNumber": 1, "etag": "etag456"}]

    def test_process_upload_tolerates_404_on_poll(self):
        """A 404 during GET polling is treated as 'not ready' and retried."""
        from podcaster.publish import _process_upload

        session = MagicMock()
        call_count = {"n": 0}

        def side_effect(method, url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if method == "POST":
                resp.status_code = 200
                resp.json.return_value = {}
                resp.headers = {}
                return resp
            idx = call_count["n"]
            call_count["n"] += 1
            if idx == 0:
                # First poll: 404 — not ready yet
                resp.status_code = 404
                return resp
            # Second poll: processed
            resp.status_code = 200
            resp.json.return_value = {"request": {"state": "processed"}}
            return resp

        session.request.side_effect = side_effect

        with patch("podcaster.publish.time.sleep"):
            _process_upload(
                session,
                upload_id="x789",
                anchor_id=1,
                station_id="1",
                user_id="1",
                filename="ep.mp3",
                content_type="audio/mpeg",
            )

        # Two GET poll calls: first 404, then 200/processed
        get_calls = [c for c in session.request.call_args_list if c.args[0] == "GET"]
        assert len(get_calls) == 2


class TestSpotifyClientId:
    """Tests for SPOTIFY_CLIENT_ID env-var configurability (#302)."""

    def test_default_client_id_when_env_unset(self, monkeypatch):
        """_SPOTIFY_CLIENT_ID uses the public Spotify web-player default when env var is absent."""
        import importlib

        import podcaster.publish as pub_mod

        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        importlib.reload(pub_mod)
        assert pub_mod._SPOTIFY_CLIENT_ID == "05a1371ee5194c27860b3ff3ff3979d2"

    def test_custom_client_id_from_env(self, monkeypatch):
        """_SPOTIFY_CLIENT_ID reads a custom value from SPOTIFY_CLIENT_ID env var."""
        import importlib

        import podcaster.publish as pub_mod

        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "custom-test-client-id-abc123")
        importlib.reload(pub_mod)
        assert pub_mod._SPOTIFY_CLIENT_ID == "custom-test-client-id-abc123"


class TestUploadVideoToEpisode:
    """Tests for upload_video_to_episode — attaching an MP4 to an existing draft (#337)."""

    def _video(self, tmp_path):
        v = tmp_path / "ep.mp4"
        v.write_bytes(b"\x00" * 4096)
        return v

    def _patch_successful_video_upload(self, monkeypatch, pub, seen):
        def fake_get_url(session, anchor_id, **kwargs):
            seen["upload_anchor"] = anchor_id
            return ([{"partNumber": 1, "url": "https://gcs/part"}], "up1")

        def fake_process(session, upload_id, **kwargs):
            seen["process_anchor"] = kwargs.get("anchor_id")

        def fake_metadata(session, anchor_id, user_id, **kwargs):
            seen["metadata_anchor"] = anchor_id
            seen["metadata_title"] = kwargs.get("title")

        monkeypatch.setattr(pub, "_get_upload_url", fake_get_url)
        monkeypatch.setattr(
            pub,
            "_upload_video_multipart",
            lambda s, parts, data: [{"partNumber": 1, "etag": "e1"}],
        )
        monkeypatch.setattr(pub, "_process_upload", fake_process)
        monkeypatch.setattr(pub, "_set_metadata", fake_metadata)

    def test_dry_run(self, tmp_path, monkeypatch):
        from podcaster.publish import upload_video_to_episode

        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        result = upload_video_to_episode(self._video(tmp_path), 42, title="My Show")
        assert result.dry_run is True
        assert result.status == "draft"
        assert result.anchor_episode_id is None
        assert result.details["title"] == "My Show"
        assert result.details["audio_anchor_id"] == 42

    def test_missing_file(self, tmp_path, monkeypatch):
        from podcaster.publish import upload_video_to_episode

        monkeypatch.delenv("SPOTIFY_PUBLISH_DRY_RUN", raising=False)
        result = upload_video_to_episode(tmp_path / "missing.mp4", 42)
        assert result.status == "failed"
        assert "not found" in result.error

    def test_missing_credentials(self, tmp_path, monkeypatch):
        from podcaster.publish import upload_video_to_episode

        monkeypatch.delenv("SPOTIFY_PUBLISH_DRY_RUN", raising=False)
        monkeypatch.delenv("SPOTIFY_SHOW_ID", raising=False)
        monkeypatch.delenv("SP_DC", raising=False)
        monkeypatch.delenv("SP_KEY", raising=False)
        result = upload_video_to_episode(self._video(tmp_path), 42)
        assert result.status == "failed"
        assert "credentials" in result.error.lower()

    def test_success_creates_new_video_episode(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.delenv("SPOTIFY_PUBLISH_DRY_RUN", raising=False)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        calls = {}
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        # A NEW episode is created for the video (separate from the audio one).
        monkeypatch.setattr(pub, "_create_episode", lambda s, station_id: 777)

        def fake_get_url(session, anchor_id, **kwargs):
            calls["is_video"] = kwargs.get("is_video")
            calls["anchor_id"] = anchor_id
            return ([{"partNumber": 1, "url": "https://gcs/part"}], "up1")

        monkeypatch.setattr(pub, "_get_upload_url", fake_get_url)
        monkeypatch.setattr(
            pub,
            "_upload_video_multipart",
            lambda s, parts, data: [{"partNumber": 1, "etag": "e1"}],
        )

        def fake_process(session, upload_id, **kwargs):
            calls["content_type"] = kwargs.get("content_type")
            calls["parts"] = kwargs.get("parts_etags")
            calls["process_anchor"] = kwargs.get("anchor_id")

        monkeypatch.setattr(pub, "_process_upload", fake_process)

        def fake_metadata(session, anchor_id, user_id, **kwargs):
            calls["metadata_anchor"] = anchor_id
            calls["metadata_title"] = kwargs.get("title")
            calls["publish_behavior"] = kwargs.get("publish_behavior")

        monkeypatch.setattr(pub, "_set_metadata", fake_metadata)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")
        assert result.status == "draft"
        # The NEW episode id is returned, not the audio anchor (555).
        assert result.anchor_episode_id == 777
        assert result.details["audio_anchor_id"] == 555
        assert calls["is_video"] is True
        assert calls["anchor_id"] == 777
        assert calls["process_anchor"] == 777
        assert calls["content_type"] == "video/mp4"
        assert calls["parts"] == [{"partNumber": 1, "etag": "e1"}]
        assert calls["metadata_anchor"] == 777
        assert calls["metadata_title"] == "My Show"
        assert calls["publish_behavior"] == "draft"

    def test_reconcile_reuses_existing_draft_by_title(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_json_resp(
            {"episodes": [{"episodeId": 888, "title": "My Show", "status": "draft"}]}
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)
        seen = {}
        self._patch_successful_video_upload(monkeypatch, pub, seen)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "draft"
        assert result.anchor_episode_id == 888
        create.assert_not_called()
        assert seen["upload_anchor"] == 888
        assert seen["process_anchor"] == 888
        assert seen["metadata_anchor"] == 888
        assert seen["metadata_title"] == "My Show"

    def test_reconcile_never_reuses_audio_anchor_episode(self, tmp_path, monkeypatch):
        """A same-titled audio draft (the anchor_id) must never be reused as the
        video draft — doing so would attach video to the audio episode (#564)."""
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        # The ONLY same-titled draft is the audio anchor episode (id 555).
        session.request.return_value = _mock_json_resp(
            {"episodes": [{"episodeId": 555, "title": "My Show", "status": "draft"}]}
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)
        seen = {}
        self._patch_successful_video_upload(monkeypatch, pub, seen)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "draft"
        # A brand-new video draft is created instead of reusing the audio anchor.
        assert result.anchor_episode_id == 777
        create.assert_called_once_with(session, "99")
        assert seen["upload_anchor"] == 777

    def test_crash_after_create_second_call_reconciles_same_draft(
        self,
        tmp_path,
        monkeypatch,
    ):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        episodes: list[dict] = []
        session = MagicMock()

        def fake_request(method, url, **kwargs):
            return _mock_json_resp({"episodes": list(episodes)})

        session.request.side_effect = fake_request
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        created: list[int] = []

        def fake_create(session, station_id):
            episode_id = 901
            created.append(episode_id)
            episodes.append({"episodeId": episode_id, "title": "My Show", "status": "draft"})
            return episode_id

        monkeypatch.setattr(pub, "_create_episode", fake_create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        first = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")
        second = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert first.anchor_episode_id == 901
        assert second.anchor_episode_id == 901
        assert created == [901]

    def test_reconcile_disabled_falls_back_to_create(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")

        session = MagicMock()
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)
        seen = {}
        self._patch_successful_video_upload(monkeypatch, pub, seen)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "draft"
        assert result.anchor_episode_id == 777
        create.assert_called_once_with(session, "99")
        assert session.request.call_count == 0


class TestPollUploadErrorExtraction:
    """#351: extract Spotify mediaValidation.failureInfo.errorCode on failure."""

    def _session_for(self, data):
        from podcaster import publish as pub

        session = MagicMock()
        process_resp = MagicMock()
        process_resp.raise_for_status = MagicMock()
        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.json.return_value = data
        poll_resp.raise_for_status = MagicMock()
        session.request.side_effect = [process_resp, poll_resp]
        return pub, session

    def test_failed_state_extracts_error_code(self, monkeypatch, caplog):
        import logging

        data = {
            "request": {"state": "failed", "failureReason": ""},
            "mediaValidation": {
                "status": "validation_failure",
                "failures": [],
                "failureInfo": {"errorCode": "INCONSISTENT_COLOR_DETAILS"},
            },
        }
        pub, session = self._session_for(data)
        monkeypatch.setattr(pub.time, "sleep", lambda *a, **k: None)
        with caplog.at_level(logging.DEBUG, logger="podcaster.publish"):
            with pytest.raises(pub.SpotifyPublishError) as exc:
                pub._process_upload(
                    session,
                    "upload-1",
                    anchor_id=1,
                    station_id="2",
                    user_id="3",
                    filename="v.mp4",
                    content_type="video/mp4",
                    parts_etags=[{"partNumber": 1, "etag": "e1"}],
                )
        assert "INCONSISTENT_COLOR_DETAILS" in str(exc.value)
        # Full response JSON logged at debug
        assert any("full response on failure" in r.message for r in caplog.records)

    def test_validation_failure_on_completed_extracts_error_code(self, monkeypatch):
        data = {
            "request": {"state": "processed"},
            "mediaValidation": {
                "status": "validation_failure",
                "failures": [],
                "failureInfo": {"errorCode": "SOME_OTHER_ERROR"},
            },
        }
        pub, session = self._session_for(data)
        monkeypatch.setattr(pub.time, "sleep", lambda *a, **k: None)
        with pytest.raises(pub.SpotifyPublishError) as exc:
            pub._process_upload(
                session,
                "upload-2",
                anchor_id=1,
                station_id="2",
                user_id="3",
                filename="v.mp4",
                content_type="video/mp4",
                parts_etags=[{"partNumber": 1, "etag": "e1"}],
            )
        assert "SOME_OTHER_ERROR" in str(exc.value)


class TestCredentialExpiryDetection:
    """#364: detect Spotify 401/403 as credential expiry and notify."""

    def _http_error(self, status_code):
        import requests

        resp = MagicMock()
        resp.status_code = status_code
        resp.text = "Unauthorized"
        return requests.HTTPError("auth", response=resp)

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_retry_request_raises_credential_expired(self, status_code, monkeypatch):
        from podcaster import publish as pub

        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = self._http_error(status_code)
        session.request.return_value = resp

        monkeypatch.setattr(pub.time, "sleep", lambda *a, **k: None)
        with pytest.raises(pub.SpotifyCredentialExpiredError) as exc:
            pub._retry_request(session, "GET", "https://api-v5.anchor.fm/x")
        assert str(status_code) in str(exc.value)
        # Must not retry on auth failure.
        assert session.request.call_count == 1

    def test_retry_request_still_retries_500(self, monkeypatch):
        from podcaster import publish as pub

        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = self._http_error(500)
        session.request.return_value = resp

        monkeypatch.setattr(pub.time, "sleep", lambda *a, **k: None)
        with pytest.raises(pub.SpotifyPublishError) as exc:
            pub._retry_request(session, "GET", "https://api-v5.anchor.fm/x")
        assert not isinstance(exc.value, pub.SpotifyCredentialExpiredError)

    def test_process_upload_401_raises_credential_expired(self, monkeypatch):
        from podcaster import publish as pub

        session = MagicMock()
        process_resp = MagicMock()
        process_resp.raise_for_status = MagicMock()
        poll_resp = MagicMock()
        poll_resp.status_code = 401
        poll_resp.raise_for_status.side_effect = self._http_error(401)
        session.request.side_effect = [process_resp, poll_resp]

        monkeypatch.setattr(pub.time, "sleep", lambda *a, **k: None)
        with pytest.raises(pub.SpotifyCredentialExpiredError):
            pub._process_upload(
                session,
                "u1",
                anchor_id=1,
                station_id="2",
                user_id="3",
                filename="ep.mp3",
                content_type="audio/mpeg",
            )

    def test_bearer_exchange_login_required_is_credential_expired(self, monkeypatch):
        from podcaster import publish as pub

        connector = MagicMock()
        connector._authenticate.side_effect = Exception("Login required")
        with patch("podcaster.publish.SpotifyConnector", return_value=connector):
            with pytest.raises(pub.SpotifyCredentialExpiredError):
                pub._request_bearer_token("dc", "key", "show")

    def test_publish_episode_notifies_on_credential_expiry(
        self, spotify_env, mp3_file, monkeypatch
    ):
        from podcaster import publish as pub

        monkeypatch.setattr(
            pub,
            "_build_session",
            MagicMock(
                side_effect=pub.SpotifyCredentialExpiredError(
                    "Spotify rejected the request (HTTP 401)"
                )
            ),
        )
        from podcaster.config import SpotifyPublishConfig

        config = SpotifyPublishConfig.from_payload({"spotify_publish": {"upload_format": "mp3"}})
        with patch(
            "podcaster.credential_expiry.notify_credential_expiry",
            return_value=4242,
        ) as notify:
            result = pub.publish_episode(
                mp3_path=mp3_file,
                title="Title",
                description="Desc",
                spotify_publish_config=config,
            )

        notify.assert_called_once()
        assert result.status == "failed"
        assert result.details["credentials_expired"] is True
        assert result.details["notification_issue"] == 4242

    def test_verify_auth_403_reports_expired(self, spotify_env):
        with patch("podcaster.publish._build_session") as mock_build_session:
            mock_session = MagicMock()
            mock_build_session.return_value = mock_session
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_session.get.return_value = mock_resp
            valid, msg = verify_spotify_auth()
        assert valid is False
        assert "refresh" in msg.lower()
