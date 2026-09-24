"""Unit tests for podcaster.publish — Spotify for Creators integration (#182)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from podcaster.config import MAX_SPOTIFY_DESCRIPTION_CHARS, SpotifyPublishConfig, truncate_html
from podcaster.publication_state import PublicationIdentity
from podcaster.publish import (
    SpotifyPublishError,
    _build_session,
    _is_dry_run,
    _is_enabled,
    _live_publish_allowed,
    _spotify_video_allow_live_publish,
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
        "SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH",
        "SPOTIFY_VIDEO_PUBLISH_MODE",
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
    def test_spotify_unknown_evidence_blocks_redelivery_mutation(
        self, monkeypatch, mp3_file, wav_file, spotify_env
    ):
        import podcaster.publish as pub

        build = MagicMock()
        monkeypatch.setattr(pub, "_build_session", build)
        monkeypatch.setattr(
            pub,
            "read_evidence",
            lambda *args: {
                "records": [
                    {
                        "platform": "spotify",
                        "media_kind": "audio",
                        "outcome": "publication_unknown",
                        "retry_blocked": True,
                    }
                ]
            },
        )
        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            wav_path=wav_file,
            publication_storage=object(),
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )
        assert result.outcome == "publication_unknown"
        assert result.details["retry_blocked"] is True
        build.assert_not_called()

    def test_spotify_evidence_failure_before_mutation_prevents_provider_call(
        self, monkeypatch, mp3_file, wav_file, spotify_env
    ):
        import podcaster.publish as pub

        build = MagicMock()
        monkeypatch.setattr(pub, "_build_session", build)
        monkeypatch.setattr(pub, "read_evidence", lambda *args: None)
        monkeypatch.setattr(
            pub,
            "claim_evidence",
            MagicMock(side_effect=RuntimeError("storage unavailable")),
        )
        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            wav_path=wav_file,
            publication_storage=object(),
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )
        assert result.outcome == "publication_unknown"
        build.assert_not_called()

    def test_spotify_existing_mutation_claim_prevents_provider_call(
        self, monkeypatch, mp3_file, wav_file, spotify_env
    ):
        import podcaster.publish as pub

        build = MagicMock()
        monkeypatch.setattr(pub, "_build_session", build)
        monkeypatch.setattr(pub, "read_evidence", lambda *args: None)
        monkeypatch.setattr(pub, "claim_evidence", MagicMock(return_value=None))

        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            wav_path=wav_file,
            publication_storage=object(),
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.outcome == "publication_unknown"
        assert result.details["code"] == "mutation_claim_exists"
        build.assert_not_called()

    def test_spotify_deterministic_create_rejection_stays_retry_blocked(
        self, monkeypatch, mp3_file, spotify_env
    ):
        import podcaster.publish as pub

        monkeypatch.setattr(pub, "read_evidence", lambda *args: None)
        monkeypatch.setattr(pub, "append_evidence", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(pub, "claim_evidence", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(pub, "emit_publication_signal", MagicMock())
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: (1, 2))
        monkeypatch.setattr(
            pub,
            "_create_episode",
            MagicMock(side_effect=pub.SpotifyPublishError("request rejected")),
        )

        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=object(),
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.outcome == "publication_unknown"
        assert result.details["retry_blocked"] is True

    def test_spotify_evidence_read_failure_blocks_even_when_write_would_succeed(
        self, monkeypatch, mp3_file, wav_file, spotify_env
    ):
        import podcaster.publish as pub

        build = MagicMock()
        append = MagicMock()
        monkeypatch.setattr(pub, "_build_session", build)
        monkeypatch.setattr(
            pub,
            "read_evidence",
            MagicMock(side_effect=RuntimeError("storage read unavailable")),
        )
        monkeypatch.setattr(pub, "append_evidence", append)

        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            wav_path=wav_file,
            publication_storage=object(),
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.outcome == "publication_unknown"
        assert result.details["retry_blocked"] is True
        append.assert_not_called()
        build.assert_not_called()

    def test_spotify_dry_run_writes_no_evidence_or_signal(
        self, monkeypatch, mp3_file, wav_file, spotify_env
    ):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
        append = MagicMock()
        signal = MagicMock()
        monkeypatch.setattr(pub, "append_evidence", append)
        monkeypatch.setattr(pub, "emit_publication_signal", signal)
        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            wav_path=wav_file,
            publication_storage=object(),
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )
        assert result.dry_run is True
        append.assert_not_called()
        signal.assert_not_called()

    def test_spotify_signal_failure_preserves_persisted_provider_outcome(
        self, monkeypatch, mp3_file, spotify_env, caplog
    ):
        import podcaster.publish as pub

        monkeypatch.setattr(pub, "read_evidence", lambda *args: None)
        append = MagicMock()
        monkeypatch.setattr(pub, "append_evidence", append)
        monkeypatch.setattr(pub, "claim_evidence", append)
        monkeypatch.setattr(
            pub,
            "emit_publication_signal",
            MagicMock(side_effect=RuntimeError("signal unavailable")),
        )
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("station", "user"))
        monkeypatch.setattr(pub, "_create_episode", lambda *args: 123)
        monkeypatch.setattr(pub, "_get_upload_url", lambda *args, **kwargs: ("signed", "up-1"))
        monkeypatch.setattr(pub, "_upload_audio", lambda *args, **kwargs: "etag")
        monkeypatch.setattr(pub, "_process_upload", lambda *args, **kwargs: None)
        monkeypatch.setattr(pub, "_set_metadata", lambda *args, **kwargs: None)

        with caplog.at_level("WARNING"):
            result = publish_episode(
                mp3_file,
                "Title",
                "Description",
                spotify_publish_config=SpotifyPublishConfig(
                    publish_mode="draft", upload_format="mp3"
                ),
                publication_storage=object(),
                publication_identity_context=PublicationIdentity(
                    "job-1", "2026-W37", "1", "a" * 64, "b" * 64
                ),
            )

        assert result.status == "draft"
        assert result.outcome == "draft_created"
        assert result.error is None
        assert append.call_count == 3
        assert "publication signal failed" in caplog.text

    def test_spotify_create_id_is_persisted_before_upload_work(
        self, monkeypatch, mp3_file, spotify_env
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("station-1", "user-1"))
        monkeypatch.setattr(pub, "_create_episode", lambda *args: 12345)
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            MagicMock(side_effect=pub.SpotifyPublishError("signed URL failed")),
        )

        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=storage,
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.status == "failed"
        assert result.anchor_episode_id == 12345
        records = _evidence_records(storage)
        assert [record["operation"] for record in records[:2]] == [
            "create_episode_intent",
            "create_episode",
        ]
        assert records[1]["provider_artifact_id"] == "12345"
        assert records[1]["retry_blocked"] is True

    def test_spotify_mp4_evidence_is_classified_as_video(self, monkeypatch, mp3_file, spotify_env):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        mp4_file = mp3_file.with_suffix(".mp4")
        mp4_file.write_bytes(b"video")
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("station-1", "user-1"))
        monkeypatch.setattr(pub, "_create_episode", lambda *args: 12345)
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            MagicMock(side_effect=pub.SpotifyPublishError("signed URL failed")),
        )

        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert result.status == "failed"
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == [
            "create_episode_intent",
            "create_episode",
            "provider_mutation_failure",
        ]
        assert all(record["media_kind"] == "video" for record in records)

    def test_spotify_unparseable_create_response_leaves_intent_marker(
        self, monkeypatch, mp3_file, spotify_env
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("station-1", "user-1"))
        monkeypatch.setattr(
            pub,
            "_create_episode",
            MagicMock(
                side_effect=pub.SpotifyDraftCreateAmbiguousError(
                    "created but response was unparseable"
                )
            ),
        )

        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=storage,
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.status == "failed"
        assert result.outcome == "publication_unknown"
        records = _evidence_records(storage)
        assert records[0]["operation"] == "create_episode_intent"
        assert records[0].get("provider_artifact_id") is None
        assert records[0]["details"]["show_id"] == "test-show-123"

    def test_spotify_create_failure_keeps_latest_evidence_retry_blocking(
        self, monkeypatch, mp3_file, spotify_env
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        create = MagicMock(side_effect=pub.SpotifyPublishError("create rejected"))
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("station-1", "user-1"))
        monkeypatch.setattr(pub, "_create_episode", create)

        first = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=storage,
            publication_identity_context=identity,
        )
        second = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.outcome == "publication_unknown"
        assert first.details["retry_blocked"] is True
        assert second.details["retry_blocked"] is True
        create.assert_called_once()
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == [
            "create_episode_intent",
            "provider_mutation_failure",
        ]
        assert records[-1]["mutation_attempted"] is True
        assert records[-1]["retry_blocked"] is True

    def test_spotify_create_credential_rejection_unblocks_corrected_retry(
        self, monkeypatch, mp3_file, spotify_env
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        create = MagicMock(
            side_effect=[
                pub.SpotifyCredentialExpiredError("credentials expired"),
                12345,
            ]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("station-1", "user-1"))
        monkeypatch.setattr(pub, "_create_episode", create)
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            MagicMock(side_effect=pub.SpotifyPublishError("signed URL failed")),
        )

        with patch(
            "podcaster.credential_expiry.notify_credential_expiry",
            return_value=4242,
        ):
            first = publish_episode(
                mp3_file,
                "Title",
                "Description",
                spotify_publish_config=SpotifyPublishConfig(
                    publish_mode="draft", upload_format="mp3"
                ),
                publication_storage=storage,
                publication_identity_context=identity,
            )
        second = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.outcome == "manual_handoff_required"
        assert first.details["retry_blocked"] is False
        assert first.details["code"] == "credentials_expired"
        assert second.anchor_episode_id == 12345
        assert create.call_count == 2
        records = _evidence_records(storage)
        assert [record["operation"] for record in records[:2]] == [
            "create_episode_intent",
            "credential_failure",
        ]
        assert records[1]["mutation_attempted"] is False
        assert records[1]["retry_blocked"] is False

    def test_spotify_evidence_failure_after_create_surfaces_with_anchor_id(
        self, monkeypatch, mp3_file, spotify_env
    ):
        import podcaster.publish as pub

        storage = MemoryStorage(fail_on_update=2)
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("station-1", "user-1"))
        monkeypatch.setattr(pub, "_create_episode", lambda *args: 12345)
        upload = MagicMock()
        monkeypatch.setattr(pub, "_get_upload_url", upload)

        result = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=storage,
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.status == "failed"
        assert result.anchor_episode_id == 12345
        assert result.outcome == "publication_unknown"
        assert result.details["code"] == "create_evidence_persistence_failed"
        upload.assert_not_called()
        assert _evidence_records(storage)[0]["operation"] == "create_episode_intent"

    def test_spotify_rerun_after_create_marker_does_not_duplicate_or_mutate(
        self, monkeypatch, mp3_file, spotify_env
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        create = MagicMock(return_value=12345)
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("station-1", "user-1"))
        monkeypatch.setattr(pub, "_create_episode", create)
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            MagicMock(side_effect=pub.SpotifyPublishError("signed URL failed")),
        )
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)

        first = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=storage,
            publication_identity_context=identity,
        )
        second = publish_episode(
            mp3_file,
            "Title",
            "Description",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="draft", upload_format="mp3"),
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.anchor_episode_id == 12345
        assert second.anchor_episode_id is None
        assert second.details["retry_blocked"] is True
        assert create.call_count == 1
        operations = [record["operation"] for record in _evidence_records(storage)]
        assert operations == [
            "create_episode_intent",
            "create_episode",
            "provider_mutation_failure",
        ]

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

        # Step 7: provider readback (the /update above is the go-live call)
        mock_session.request.side_effect = [
            resolve_resp,
            create_resp,
            upload_url_resp,
            upload_resp,
            process_resp,
            poll_resp,
            meta_resp,
            _mock_json_resp({"isPublished": True}),
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
        assert metadata_call.args[1].endswith("/v3/episodes/12345/update")
        readback_call = mock_session.request.call_args_list[-1]
        assert readback_call.args[:2] == (
            "GET",
            "https://api-v5.anchor.fm/v3/episodes/12345/overview",
        )
        assert result.outcome == "published"
        assert not any("/publish" in c.args[1] for c in mock_session.request.call_args_list)

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
            _mock_json_resp({"isPublished": False, "isDraft": True}),
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
        assert result.outcome == "draft_created"
        assert not any("/publish" in c.args[1] for c in mock_session.request.call_args_list)

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
            _mock_json_resp({"isPublished": True}),
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
            _mock_json_resp({"isPublished": True}),
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
            _mock_json_resp({"isPublished": True}),
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
            _mock_json_resp({"isPublished": True}),
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
            _mock_json_resp({"isPublished": True}),
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


class MemoryStorage:
    def __init__(self, *, fail_on_update: int | None = None):
        self.data = {}
        self.fail_on_update = fail_on_update
        self.update_count = 0

    def get_bytes(self, path):
        return self.data.get(path)

    def update_bytes(self, path, content_type, update):
        self.update_count += 1
        if self.fail_on_update is not None and self.update_count == self.fail_on_update:
            raise RuntimeError("storage unavailable")
        self.data[path] = update(self.data.get(path))
        return None


def _evidence_records(storage: MemoryStorage, job_id: str = "job-1") -> list[dict]:
    raw = storage.get_bytes(f"publication-evidence/{job_id}.json")
    assert raw is not None
    return json.loads(raw.decode())["records"]


def _mock_json_resp(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _graphql_listing_payload(episodes=None, **listing):
    items = [] if episodes is None else episodes
    had_has_more = "hasMore" in listing or "hasNextPage" in listing
    had_cursor = "nextPageToken" in listing or "nextPage" in listing
    current_page = listing.pop("currentPage", 1)
    has_more = listing.pop("hasMore", listing.pop("hasNextPage", False))
    listing.pop("nextPageToken", None)
    listing.pop("nextPage", None)
    total_pages = listing.pop("totalPages", current_page + 1 if has_more is True else current_page)
    page_size = listing.pop("pageSize", 50)
    if had_has_more and not isinstance(has_more, bool):
        page_size = has_more
    elif had_cursor and not had_has_more:
        page_size = "invalid-cursor-contract"
    total_items = listing.pop("totalItems", len(items) if isinstance(items, list) else 0)
    index_status = listing.pop("indexStatus", "COMPLETED")
    return {
        "data": {
            "showByShowUri": {
                "episodesV2": {
                    "indexStatus": index_status,
                    "items": items,
                    "pagination": {
                        "currentPage": current_page,
                        "pageSize": page_size,
                        "totalItems": total_items,
                        "totalPages": total_pages,
                    },
                    **listing,
                }
            }
        }
    }


def _mock_graphql_listing_resp(episodes=None, **listing) -> MagicMock:
    return _mock_json_resp(_graphql_listing_payload(episodes, **listing))


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

    @pytest.fixture(autouse=True)
    def _enable_verified_listing(self, monkeypatch):
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "true")

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

    @pytest.mark.parametrize("title", [None, "", " \t\n"])
    def test_missing_identity_title_aborts_before_any_spotify_mutation(
        self, tmp_path, monkeypatch, title
    ):
        import podcaster.publish as pub

        build_session = MagicMock()
        create = MagicMock()
        upload = MagicMock()
        publish = MagicMock()
        monkeypatch.setattr(pub, "_build_session", build_session)
        monkeypatch.setattr(pub, "_create_episode", create)
        monkeypatch.setattr(pub, "_get_upload_url", upload)
        monkeypatch.setattr(pub, "_publish_episode_live", publish)

        result = pub.upload_video_to_episode(self._video(tmp_path), 42, title=title)

        assert result.status == "failed"
        assert "non-blank title" in result.error
        build_session.assert_not_called()
        create.assert_not_called()
        upload.assert_not_called()
        publish.assert_not_called()

    def test_missing_file(self, tmp_path, monkeypatch):
        from podcaster.publish import upload_video_to_episode

        monkeypatch.delenv("SPOTIFY_PUBLISH_DRY_RUN", raising=False)
        result = upload_video_to_episode(tmp_path / "missing.mp4", 42, title="My Show")
        assert result.status == "failed"
        assert "not found" in result.error

    def test_missing_credentials(self, tmp_path, monkeypatch):
        from podcaster.publish import upload_video_to_episode

        monkeypatch.delenv("SPOTIFY_PUBLISH_DRY_RUN", raising=False)
        monkeypatch.delenv("SPOTIFY_SHOW_ID", raising=False)
        monkeypatch.delenv("SP_DC", raising=False)
        monkeypatch.delenv("SP_KEY", raising=False)
        result = upload_video_to_episode(self._video(tmp_path), 42, title="My Show")
        assert result.status == "failed"
        assert "credentials" in result.error.lower()

    def test_create_retry_authorization_requires_exact_publication_identity(self):
        import podcaster.publish as pub

        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        record = {
            "job_id": identity.accepted_job_id,
            "week": identity.week,
            "publish_run_id": identity.publish_run_id,
            "article_sha256": identity.article_sha256,
            "manifest_sha256": "c" * 64,
            "platform": "spotify",
            "media_kind": "video",
            "operation": "create_episode_failure",
            "retry_blocked": False,
        }

        assert not pub._spotify_video_create_retry_authorized({"records": [record]}, identity)
        record["manifest_sha256"] = identity.manifest_sha256
        assert pub._spotify_video_create_retry_authorized({"records": [record]}, identity)

    def test_default_reconcile_fails_closed_on_unreadable_listing(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.delenv("PODCASTER_SPOTIFY_RECONCILE", raising=False)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_json_resp([])
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        create = MagicMock()
        upload = MagicMock()
        monkeypatch.setattr(pub, "_create_episode", create)
        monkeypatch.setattr(pub, "_get_upload_url", upload)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "failed"
        assert "duplicate draft" in result.error
        session.request.assert_called()
        create.assert_not_called()
        upload.assert_not_called()

    def test_success_creates_new_video_episode(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.delenv("SPOTIFY_PUBLISH_DRY_RUN", raising=False)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        calls = {}
        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp()
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
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

    def test_video_create_id_is_persisted_before_upload_work(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        storage = MemoryStorage()
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")
        session = MagicMock()
        session.request.return_value = _mock_json_resp({"episodes": []})
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        monkeypatch.setattr(pub, "_create_episode", lambda *args: 777)
        monkeypatch.setattr(pub, "_set_metadata", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            MagicMock(side_effect=pub.SpotifyPublishError("signed URL failed")),
        )

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.status == "failed"
        assert result.anchor_episode_id == 777
        assert result.outcome == "publication_unknown"
        assert result.details == {"retry_blocked": True, "code": "post_create_failure"}
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == [
            "create_episode_intent",
            "create_episode",
        ]
        assert records[1]["provider_artifact_id"] == "777"
        assert records[1]["media_kind"] == "video"
        assert records[1]["mutation_attempted"] is True
        assert records[1]["code"] == "provider_artifact_created"

    def test_titled_ambiguous_recovery_reconciles_concurrent_draft(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        session, calls = _scripted_session(
            [
                _mock_graphql_listing_resp(),
                _mock_error_resp(504, "gateway timeout"),
                _mock_graphql_listing_resp(
                    [{"episodeId": 901, "title": "My Show", "status": "draft"}]
                ),
            ]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            MagicMock(side_effect=pub.SpotifyPublishError("signed URL failed")),
        )

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert result.status == "failed"
        assert result.anchor_episode_id == 901
        assert result.outcome == "publication_unknown"
        assert len(_create_posts(calls)) == 1
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == [
            "create_episode_intent",
            "reconcile_episode",
        ]
        assert records[1]["provider_artifact_id"] == "901"
        assert records[1]["mutation_attempted"] is False
        assert records[1]["code"] == "provider_artifact_reconciled"

    def test_listing_failure_before_create_leaves_retryable_evidence(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setattr(pub.time, "sleep", lambda _seconds: None)
        failed_session = MagicMock()
        failed_session.request.return_value = _mock_error_resp(503, "listing unavailable")
        successful_session = MagicMock()
        successful_session.request.return_value = _mock_graphql_listing_resp()
        monkeypatch.setattr(
            pub,
            "_build_session",
            MagicMock(side_effect=[failed_session, successful_session]),
        )
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        first = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )
        evidence_path = "publication-evidence/job-1.json"

        assert first.status == "failed"
        assert first.anchor_episode_id is None
        assert failed_session.request.call_count == pub._MAX_RETRIES
        assert create.call_count == 0
        assert storage.get_bytes(evidence_path) is None

        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert second.status == "draft"
        assert second.anchor_episode_id == 777
        create.assert_called_once()
        assert [record["operation"] for record in _evidence_records(storage)] == [
            "create_episode_intent",
            "create_episode",
        ]

    def test_video_unparseable_create_response_leaves_intent_marker(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        storage = MemoryStorage()
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")
        session = MagicMock()
        session.request.return_value = _mock_json_resp({"episodes": []})
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        monkeypatch.setattr(
            pub,
            "_create_episode",
            MagicMock(
                side_effect=pub.SpotifyDraftCreateAmbiguousError(
                    "created but response was unparseable"
                )
            ),
        )

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.status == "failed"
        assert result.outcome == "publication_unknown"
        assert result.details == {"retry_blocked": True, "code": "ambiguous_create"}
        records = _evidence_records(storage)
        assert records[0]["operation"] == "create_episode_intent"
        assert records[0].get("provider_artifact_id") is None
        assert records[0]["retry_blocked"] is True
        assert records[0]["details"]["show_id"] == "show1"
        assert records[0]["details"]["pre_create_episode_ids"] == []
        assert records[0]["details"]["pre_create_snapshot_complete"] is False

    def test_reconcile_disabled_blocks_unresolved_create_intent(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        pub.append_evidence(
            storage,
            identity,
            platform="spotify",
            media_kind="video",
            operation="create_episode_intent",
            outcome=pub.PUBLICATION_UNKNOWN,
            mutation_attempted=False,
            retry_blocked=False,
            code="mutation_intent",
            details={
                "show_id": "show1",
                "station_id": "99",
                "pre_create_episode_ids": [111222],
                "pre_create_snapshot_complete": True,
            },
        )
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(return_value=777001)
        monkeypatch.setattr(pub, "_create_episode", create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert result.status == "failed"
        assert result.anchor_episode_id is None
        assert result.outcome == pub.PUBLICATION_UNKNOWN
        assert result.details == {"retry_blocked": True, "code": "unresolved_create_intent"}
        assert "refusing provider access or mutation" in result.error
        create.assert_not_called()
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == ["create_episode_intent"]
        assert records[0]["details"]["pre_create_episode_ids"] == [111222]
        assert records[0]["details"]["pre_create_snapshot_complete"] is True

    def test_large_snapshot_does_not_adopt_preexisting_untitled_draft(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        preexisting_ids = list(range(1, 151))
        pub.append_evidence(
            storage,
            identity,
            platform="spotify",
            media_kind="video",
            operation="create_episode_intent",
            outcome=pub.PUBLICATION_UNKNOWN,
            mutation_attempted=False,
            retry_blocked=True,
            code="mutation_intent",
            details={
                "show_id": "show1",
                "station_id": "99",
                "pre_create_episode_ids": preexisting_ids,
                "pre_create_snapshot_complete": True,
            },
        )
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp(
            [{"episodeId": 150, "title": None, "status": "draft"}]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(return_value=777001)
        monkeypatch.setattr(pub, "_create_episode", create)
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            lambda *args, **kwargs: pytest.fail("must not upload"),
        )

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert result.status == "failed"
        assert result.anchor_episode_id is None
        assert result.details == {"retry_blocked": True, "code": "unresolved_create_intent"}
        assert "refusing provider access or mutation" in result.error
        assert session.request.call_count == 0
        create.assert_not_called()
        record = _evidence_records(storage)[0]
        assert record["details"]["pre_create_episode_ids"] == preexisting_ids
        assert record["details"]["pre_create_snapshot_complete"] is True

    def test_credential_expiry_during_create_does_not_adopt_unrelated_draft(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        first_session = MagicMock()
        first_session.request.return_value = _mock_graphql_listing_resp()
        second_session = MagicMock()
        second_session.request.return_value = _mock_graphql_listing_resp(
            [{"episodeId": 777, "title": None, "status": "draft"}]
        )
        monkeypatch.setattr(
            pub, "_build_session", MagicMock(side_effect=[first_session, second_session])
        )
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(
            side_effect=[
                pub.SpotifyCredentialExpiredError("credentials expired"),
                888,
            ]
        )
        monkeypatch.setattr(pub, "_create_episode", create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        with patch(
            "podcaster.credential_expiry.notify_credential_expiry",
            return_value=4242,
        ):
            first = pub.upload_video_to_episode(
                self._video(tmp_path),
                555,
                title="My Show",
                publication_storage=storage,
                publication_identity_context=identity,
            )

        assert first.status == "failed"
        assert first.outcome == "manual_handoff_required"
        assert first.anchor_episode_id is None
        assert first.details["credentials_expired"] is True
        assert first.details["retry_blocked"] is False
        assert first.details["code"] == "credentials_expired"
        records = _evidence_records(storage)
        assert records[0]["operation"] == "create_episode_intent"
        assert records[0]["retry_blocked"] is True
        assert records[0]["details"]["pre_create_episode_ids"] == [555]
        assert records[0]["details"]["pre_create_snapshot_complete"] is True
        assert records[1]["operation"] == "create_episode_failure"
        assert records[1]["retry_blocked"] is False

        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert second.status == "draft"
        assert second.anchor_episode_id == 888
        assert create.call_count == 2
        records = _evidence_records(storage)
        assert [
            (record["operation"], record.get("provider_artifact_id")) for record in records
        ] == [
            ("create_episode_intent", None),
            ("create_episode_failure", None),
            ("create_episode_intent", None),
            ("create_episode", "888"),
        ]
        assert records[2]["details"]["pre_create_episode_ids"] == [555, 777]
        assert records[2]["details"]["pre_create_snapshot_complete"] is True

    @pytest.mark.parametrize(
        ("operation", "outcome", "provider_artifact_id"),
        [
            ("create_episode", "publication_unknown", "777"),
            ("distribution", "draft_created", "777"),
            ("distribution", "published", "777"),
        ],
    )
    def test_video_provider_evidence_prevents_second_create(
        self,
        tmp_path,
        monkeypatch,
        operation,
        outcome,
        provider_artifact_id,
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        pub.append_evidence(
            storage,
            identity,
            platform="spotify",
            media_kind="video",
            operation=operation,
            outcome=outcome,
            provider_artifact_id=provider_artifact_id,
            retry_blocked=True,
        )
        pub.append_evidence(
            storage,
            identity,
            platform="spotify",
            media_kind="video",
            operation="upload_intent",
            outcome="publication_unknown",
            mutation_attempted=False,
            retry_blocked=True,
            code="mutation_intent",
        )
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock()
        monkeypatch.setattr(pub, "_create_episode", create)

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert result.status == "failed"
        assert result.outcome == "publication_unknown"
        assert result.details == {"retry_blocked": True}
        create.assert_not_called()
        assert len(_evidence_records(storage)) == 2

    def test_video_evidence_failure_after_create_surfaces_with_anchor_id(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage(fail_on_update=2)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")
        session = MagicMock()
        session.request.return_value = _mock_json_resp({"episodes": []})
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)
        upload = MagicMock()
        monkeypatch.setattr(pub, "_get_upload_url", upload)
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert result.status == "failed"
        assert result.anchor_episode_id == 777
        assert result.outcome == "publication_unknown"
        assert result.details["code"] == "create_evidence_persistence_failed"
        create.assert_called_once()
        upload.assert_not_called()
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == ["create_episode_intent"]
        assert records[0]["details"]["pre_create_snapshot_complete"] is False

        create.reset_mock()
        retry = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert retry.status == "failed"
        assert retry.anchor_episode_id is None
        assert retry.details == {"retry_blocked": True}
        create.assert_not_called()

    def test_recovered_titled_draft_is_reconciled_before_upload(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        storage = MemoryStorage()
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        session = MagicMock()
        session.request.side_effect = [
            _mock_graphql_listing_resp([]),
            _mock_graphql_listing_resp([{"episodeId": 888, "title": "My Show", "status": "draft"}]),
        ]
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(
            side_effect=pub.SpotifyDraftCreateAmbiguousError("created but response was unparseable")
        )
        monkeypatch.setattr(pub, "_create_episode", create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.status == "draft"
        assert result.anchor_episode_id == 888
        create.assert_called_once()
        records = _evidence_records(storage)
        assert [
            (record["operation"], record.get("provider_artifact_id")) for record in records
        ] == [
            ("create_episode_intent", None),
            ("reconcile_episode", "888"),
        ]
        assert records[1]["mutation_attempted"] is False
        assert records[1]["code"] == "provider_artifact_reconciled"

    def test_recovered_untitled_draft_persists_provider_id_before_upload(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        session = MagicMock()
        session.request.side_effect = [
            _mock_graphql_listing_resp([]),
            _mock_graphql_listing_resp([{"episodeId": 889, "title": None, "status": "draft"}]),
        ]
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        monkeypatch.setattr(
            pub,
            "_create_episode",
            MagicMock(
                side_effect=pub.SpotifyDraftCreateAmbiguousError(
                    "created but response was unparseable"
                )
            ),
        )
        self._patch_successful_video_upload(monkeypatch, pub, {})

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.status == "draft"
        assert result.anchor_episode_id == 889
        records = _evidence_records(storage)
        assert records[1]["operation"] == "create_episode"
        assert records[1]["provider_artifact_id"] == "889"

    def test_reconcile_reuses_existing_draft_by_title(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp(
            [{"episodeId": 888, "title": "My Show", "status": "draft"}]
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

    def test_reconciled_titled_draft_blocks_blind_create_retry(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp(
            [{"episodeId": 888, "title": "My Show", "status": "draft"}]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            MagicMock(side_effect=pub.SpotifyPublishError("signed URL failed")),
        )

        first = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.status == "failed"
        assert first.anchor_episode_id == 888
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == ["reconcile_episode"]
        assert records[0]["provider_artifact_id"] == "888"
        assert records[0]["mutation_attempted"] is False
        assert records[0]["retry_blocked"] is True
        create.assert_not_called()

        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")
        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert second.status == "failed"
        assert second.outcome == "publication_unknown"
        assert second.details == {"retry_blocked": True}
        create.assert_not_called()

    def test_reconcile_rejects_multiple_exact_title_drafts_before_mutation(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp(
            [
                {"episodeId": 888, "title": "My Show", "status": "draft"},
                {"episodeId": 889, "title": "My Show", "status": "draft"},
            ]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        mutations = {
            name: MagicMock()
            for name in (
                "_create_episode",
                "_get_upload_url",
                "_upload_video_multipart",
                "_process_upload",
                "_set_metadata",
                "_publish_episode_live",
            )
        }
        for name, mutation in mutations.items():
            monkeypatch.setattr(pub, name, mutation)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "failed"
        assert result.anchor_episode_id is None
        assert "multiple reusable drafts" in result.error
        assert "888" in result.error
        assert "889" in result.error
        for mutation in mutations.values():
            mutation.assert_not_called()

    def test_reconcile_never_reuses_audio_anchor_episode(self, tmp_path, monkeypatch):
        """A same-titled audio draft (the anchor_id) must never be reused as the
        video draft — doing so would attach video to the audio episode (#564)."""
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        # The ONLY same-titled draft is the audio anchor episode (id 555).
        session.request.return_value = _mock_graphql_listing_resp(
            [{"episodeId": 555, "title": "My Show", "status": "draft"}]
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
        """Production-realistic idempotency: a created draft starts **untitled**.

        ``_create_episode`` posts ``{"hourOffset": 0}`` — Spotify returns a draft
        with no title, and ``_find_existing_draft`` matches on title. Only the
        immediate title claim makes the new draft reconcilable, so this test
        models the real server (untitled on create, titled by the metadata call)
        and fails the upload repeatedly. Exactly one draft may ever be created.
        """
        import itertools

        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        # Server-side state: episodes are created untitled, exactly as Spotify does.
        episodes: list[dict] = []
        ids = itertools.count(901)
        session = MagicMock()
        session.request.side_effect = lambda method, url, **kwargs: _mock_graphql_listing_resp(
            [dict(episode) for episode in episodes]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        created: list[int] = []

        def fake_create(session, station_id):
            episode_id = next(ids)
            created.append(episode_id)
            episodes.append({"episodeId": episode_id, "title": None, "status": "draft"})
            return episode_id

        def fake_metadata(session, anchor_id, user_id, **kwargs):
            for episode in episodes:
                if episode["episodeId"] == anchor_id:
                    episode["title"] = kwargs.get("title")

        # The upload leg fails on the first two attempts, mid-flight.
        attempts = {"n": 0}

        def fake_get_url(session, anchor_id, **kwargs):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise pub.SpotifyPublishError("signed URL request failed")
            return ([{"partNumber": 1, "url": "https://gcs/part"}], "up1")

        monkeypatch.setattr(pub, "_create_episode", fake_create)
        monkeypatch.setattr(pub, "_set_metadata", fake_metadata)
        monkeypatch.setattr(pub, "_get_upload_url", fake_get_url)
        monkeypatch.setattr(
            pub,
            "_upload_video_multipart",
            lambda s, parts, data: [{"partNumber": 1, "etag": "e1"}],
        )
        monkeypatch.setattr(pub, "_process_upload", lambda s, upload_id, **kwargs: None)

        video = self._video(tmp_path)
        first = pub.upload_video_to_episode(video, 555, title="My Show")
        second = pub.upload_video_to_episode(video, 555, title="My Show")
        third = pub.upload_video_to_episode(video, 555, title="My Show")

        assert first.status == "failed"
        assert second.status == "failed"
        assert third.status == "draft"
        assert third.anchor_episode_id == 901
        # One draft, reused across every retry — never a second one.
        assert created == [901]
        assert len(episodes) == 1

    def test_new_draft_is_titled_before_any_upload(self, tmp_path, monkeypatch):
        """The title claim must precede the signed-URL request, not follow it."""
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp()
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        monkeypatch.setattr(pub, "_create_episode", lambda s, station_id: 777)

        order: list[str] = []

        def fake_metadata(session, anchor_id, user_id, **kwargs):
            order.append(f"metadata:{anchor_id}:{kwargs.get('title')}")

        def fake_get_url(session, anchor_id, **kwargs):
            order.append(f"upload_url:{anchor_id}")
            return ([{"partNumber": 1, "url": "https://gcs/part"}], "up1")

        monkeypatch.setattr(pub, "_set_metadata", fake_metadata)
        monkeypatch.setattr(pub, "_get_upload_url", fake_get_url)
        monkeypatch.setattr(
            pub,
            "_upload_video_multipart",
            lambda s, parts, data: [{"partNumber": 1, "etag": "e1"}],
        )
        monkeypatch.setattr(pub, "_process_upload", lambda s, upload_id, **kwargs: None)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "draft"
        assert order == [
            "metadata:777:My Show",
            "upload_url:777",
            "metadata:777:My Show",
        ]

    def test_reused_draft_is_not_re_titled(self, tmp_path, monkeypatch):
        """A reconciled draft already carries the title — no extra claim call."""
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp(
            [{"episodeId": 888, "title": "My Show", "status": "draft"}]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)

        metadata_calls: list[int] = []
        monkeypatch.setattr(
            pub,
            "_set_metadata",
            lambda s, anchor_id, user_id, **kwargs: metadata_calls.append(anchor_id),
        )
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            lambda s, anchor_id, **kwargs: ([{"partNumber": 1, "url": "https://gcs/p"}], "up1"),
        )
        monkeypatch.setattr(
            pub,
            "_upload_video_multipart",
            lambda s, parts, data: [{"partNumber": 1, "etag": "e1"}],
        )
        monkeypatch.setattr(pub, "_process_upload", lambda s, upload_id, **kwargs: None)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.anchor_episode_id == 888
        create.assert_not_called()
        assert metadata_calls == [888]

    def test_title_claim_failure_aborts_before_upload(self, tmp_path, monkeypatch):
        """If the new draft cannot be titled, abort — do not upload, do not re-create."""
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp()
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)

        def boom(*args, **kwargs):
            raise pub.SpotifyPublishError("update rejected: secret-token-abc")

        monkeypatch.setattr(pub, "_set_metadata", boom)
        upload = MagicMock()
        monkeypatch.setattr(pub, "_get_upload_url", upload)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "failed"
        create.assert_called_once()
        upload.assert_not_called()
        assert "777" in result.error
        assert "secret-token-abc" not in result.error

    def test_reconcile_disabled_skips_title_claim(self, tmp_path, monkeypatch):
        """The escape hatch restores the exact pre-#656 blind-create behaviour."""
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")

        session = MagicMock()
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        monkeypatch.setattr(pub, "_create_episode", lambda s, station_id: 777)

        metadata_calls: list[int] = []
        monkeypatch.setattr(
            pub,
            "_set_metadata",
            lambda s, anchor_id, user_id, **kwargs: metadata_calls.append(anchor_id),
        )
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            lambda s, anchor_id, **kwargs: ([{"partNumber": 1, "url": "https://gcs/p"}], "up1"),
        )
        monkeypatch.setattr(
            pub,
            "_upload_video_multipart",
            lambda s, parts, data: [{"partNumber": 1, "etag": "e1"}],
        )
        monkeypatch.setattr(pub, "_process_upload", lambda s, upload_id, **kwargs: None)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "draft"
        assert metadata_calls == [777]
        assert session.request.call_count == 0

    def test_credential_expiry_opens_notification(self, tmp_path, monkeypatch):
        """#656 follow-up: the video path must notify operators, like the audio path."""
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("CREDENTIAL_EXPIRY_NOTIFY_DISABLED", "true")

        session = MagicMock()
        session.request.return_value = _mock_error_resp(401, "unauthorized")
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)

        with patch(
            "podcaster.credential_expiry.notify_credential_expiry",
            return_value=4242,
        ) as notify:
            result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        notify.assert_called_once()
        assert result.status == "failed"
        assert result.details["credentials_expired"] is True
        assert result.details["notification_issue"] == 4242
        create.assert_not_called()

    def test_credential_expiry_after_create_preserves_provider_identity(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        monkeypatch.setattr(pub, "_create_episode", lambda *args: 777)
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            MagicMock(side_effect=pub.SpotifyCredentialExpiredError("credentials expired")),
        )

        with patch(
            "podcaster.credential_expiry.notify_credential_expiry",
            return_value=4242,
        ):
            result = pub.upload_video_to_episode(
                self._video(tmp_path),
                555,
                title="My Show",
                publication_storage=storage,
                publication_identity_context=identity,
            )

        assert result.status == "failed"
        assert result.anchor_episode_id == 777
        assert result.outcome == "publication_unknown"
        assert result.details == {
            "credentials_expired": True,
            "notification_issue": 4242,
            "audio_anchor_id": 555,
            "retry_blocked": True,
            "code": "post_create_failure",
        }
        assert _evidence_records(storage)[1]["provider_artifact_id"] == "777"

    def test_create_rejection_keeps_video_intent_retry_blocking(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(side_effect=pub.SpotifyPublishError("create rejected"))
        monkeypatch.setattr(pub, "_create_episode", create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        first = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )
        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.outcome == "publication_unknown"
        assert first.details["retry_blocked"] is True
        assert first.details["code"] == "create_rejected"
        assert second.outcome == "publication_unknown"
        assert second.details["retry_blocked"] is True
        create.assert_called_once()
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == [
            "create_episode_intent",
            "create_episode_failure",
        ]
        assert records[0]["mutation_attempted"] is False
        assert records[0]["transport_status"] == "not_attempted"
        assert records[1]["mutation_attempted"] is True
        assert records[1]["transport_status"] == "mutation_attempted"
        assert records[-1]["retry_blocked"] is True
        assert records[-1]["code"] == "create_rejected"

    def test_create_rejection_storage_append_failure_persists_fail_closed_fence(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage(fail_on_update=2)
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        first_session = MagicMock()
        first_session.request.return_value = _mock_graphql_listing_resp()
        second_session = MagicMock()
        monkeypatch.setattr(
            pub, "_build_session", MagicMock(side_effect=[first_session, second_session])
        )
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(side_effect=pub.SpotifyPublishError("create rejected"))
        monkeypatch.setattr(pub, "_create_episode", create)

        first = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )
        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.outcome == "publication_unknown"
        assert first.details == {
            "retry_blocked": True,
            "code": "create_evidence_persistence_failed",
        }
        assert second.details == {"retry_blocked": True}
        create.assert_called_once()
        assert second_session.request.call_count == 0
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == [
            "create_episode_intent",
            "create_episode_failure_fence",
        ]
        assert records[-1]["mutation_attempted"] is True
        assert records[-1]["retry_blocked"] is True
        assert records[-1]["code"] == "create_evidence_persistence_failed"

    def test_create_rejection_double_storage_failure_blocks_later_provider_access(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        original_update = storage.update_bytes

        def fail_failure_evidence(path, content_type, update):
            if storage.update_count >= 1:
                storage.update_count += 1
                raise RuntimeError("storage unavailable")
            return original_update(path, content_type, update)

        storage.update_bytes = fail_failure_evidence
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        first_session = MagicMock()
        first_session.request.side_effect = [
            _mock_json_resp({"stationId": "99", "userId": "7"}),
            _mock_graphql_listing_resp(),
        ]
        build_session = MagicMock(return_value=first_session)
        monkeypatch.setattr(pub, "_build_session", build_session)
        create = MagicMock(side_effect=pub.SpotifyPublishError("create rejected"))
        monkeypatch.setattr(pub, "_create_episode", create)
        upload = MagicMock()
        monkeypatch.setattr(pub, "_get_upload_url", upload)

        first = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )
        provider_access = {
            "_get_credentials": MagicMock(side_effect=AssertionError("credentials requested")),
            "_request_bearer_token": MagicMock(
                side_effect=AssertionError("bearer token requested")
            ),
            "_build_session": MagicMock(side_effect=AssertionError("session built")),
            "_resolve_legacy_ids": MagicMock(side_effect=AssertionError("legacyIds requested")),
            "_reconcile_or_create_draft": MagicMock(
                side_effect=AssertionError("draft listing or reconciliation attempted")
            ),
            "_create_episode": MagicMock(side_effect=AssertionError("draft create attempted")),
            "_get_upload_url": MagicMock(side_effect=AssertionError("upload attempted")),
        }
        for name, boundary in provider_access.items():
            monkeypatch.setattr(pub, name, boundary)
        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.details == {
            "retry_blocked": True,
            "code": "create_evidence_persistence_failed",
        }
        assert second.details == {
            "retry_blocked": True,
            "code": "unresolved_create_intent",
        }
        build_session.assert_called_once_with("dc", "key", "show1")
        assert first_session.request.call_count == 2
        for boundary in provider_access.values():
            boundary.assert_not_called()
        create.assert_called_once()
        upload.assert_not_called()
        assert [record["operation"] for record in _evidence_records(storage)] == [
            "create_episode_intent"
        ]

    def test_deterministic_create_failure_never_recovers_untitled_draft(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        first_session = MagicMock()
        first_session.request.return_value = _mock_graphql_listing_resp()
        second_session = MagicMock()
        second_session.request.return_value = _mock_graphql_listing_resp(
            [{"episodeId": 777, "title": None, "status": "draft"}]
        )
        monkeypatch.setattr(
            pub, "_build_session", MagicMock(side_effect=[first_session, second_session])
        )
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(side_effect=pub.SpotifyPublishError("create rejected"))
        monkeypatch.setattr(pub, "_create_episode", create)

        first = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )
        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.details == {"retry_blocked": True, "code": "create_rejected"}
        assert second.details == {"retry_blocked": True}
        create.assert_called_once()
        assert second_session.request.call_count == 0

    def test_credential_expiry_during_ambiguous_recovery_remains_blocked(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        session, _calls = _scripted_session(
            [
                _mock_graphql_listing_resp(),
                _mock_error_resp(504, "gateway timeout"),
                _mock_error_resp(401, "credentials expired"),
            ]
        )
        retry_session = MagicMock()
        monkeypatch.setattr(pub, "_build_session", MagicMock(side_effect=[session, retry_session]))
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))

        with patch(
            "podcaster.credential_expiry.notify_credential_expiry",
            return_value=4242,
        ):
            first = pub.upload_video_to_episode(
                self._video(tmp_path),
                555,
                title="My Show",
                publication_storage=storage,
                publication_identity_context=identity,
            )
        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.outcome == "publication_unknown"
        assert first.details["retry_blocked"] is True
        assert first.details["code"] == "post_create_failure"
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == [
            "create_episode_intent",
            "create_episode_failure",
        ]
        assert records[-1]["code"] == "ambiguous_recovery_credentials_expired"
        assert records[-1]["mutation_attempted"] is True
        assert records[-1]["transport_status"] == "mutation_attempted"
        assert records[-1]["retry_blocked"] is True
        assert second.details == {"retry_blocked": True}
        assert retry_session.request.call_count == 0

    def test_create_credential_rejection_replaces_video_intent_with_retryable_evidence(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "0")
        monkeypatch.setattr(pub, "_build_session", lambda *args: MagicMock())
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(
            side_effect=[
                pub.SpotifyCredentialExpiredError("credentials expired"),
                777,
            ]
        )
        monkeypatch.setattr(pub, "_create_episode", create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        with patch(
            "podcaster.credential_expiry.notify_credential_expiry",
            return_value=4242,
        ):
            first = pub.upload_video_to_episode(
                self._video(tmp_path),
                555,
                title="My Show",
                publication_storage=storage,
                publication_identity_context=identity,
            )
        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.outcome == "manual_handoff_required"
        assert first.details["retry_blocked"] is False
        assert first.details["code"] == "credentials_expired"
        assert second.status == "draft"
        assert second.anchor_episode_id == 777
        assert create.call_count == 2
        records = _evidence_records(storage)
        assert [record["operation"] for record in records[:2]] == [
            "create_episode_intent",
            "create_episode_failure",
        ]
        assert records[0]["mutation_attempted"] is False
        assert records[0]["transport_status"] == "not_attempted"
        assert records[1]["mutation_attempted"] is True
        assert records[1]["transport_status"] == "mutation_attempted"
        assert records[1]["retry_blocked"] is False

    @pytest.mark.parametrize("reconcile_enabled", [True, False])
    def test_unresolved_create_snapshot_isolated_by_publication_identity(
        self, tmp_path, monkeypatch, reconcile_enabled
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        prior_identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        current_identity = PublicationIdentity("job-1", "2026-W37", "2", "c" * 64, "d" * 64)
        pub.claim_evidence(
            storage,
            prior_identity,
            platform="spotify",
            media_kind="video",
            operation="create_episode_intent",
            details={
                "show_id": "show1",
                "station_id": "99",
                "pre_create_episode_ids": [555],
                "pre_create_snapshot_complete": True,
            },
        )
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "1" if reconcile_enabled else "0")
        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp(
            [{"episodeId": 777, "title": None, "status": "draft"}]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(return_value=888)
        monkeypatch.setattr(pub, "_create_episode", create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=current_identity,
        )

        assert result.status == "draft"
        assert result.anchor_episode_id == 888
        create.assert_called_once_with(session, "99")
        assert session.request.call_count == (1 if reconcile_enabled else 0)

    def test_create_credential_rejection_retries_after_empty_reconciliation(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        identity = PublicationIdentity("job-1", "2026-W37", "1", "a" * 64, "b" * 64)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp()
        monkeypatch.setattr(pub, "_build_session", lambda *args: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda *args: ("99", "7"))
        create = MagicMock(
            side_effect=[
                pub.SpotifyCredentialExpiredError("credentials expired"),
                777,
            ]
        )
        monkeypatch.setattr(pub, "_create_episode", create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        with patch(
            "podcaster.credential_expiry.notify_credential_expiry",
            return_value=4242,
        ):
            first = pub.upload_video_to_episode(
                self._video(tmp_path),
                555,
                title="My Show",
                publication_storage=storage,
                publication_identity_context=identity,
            )
        second = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=identity,
        )

        assert first.outcome == "manual_handoff_required"
        assert first.details["retry_blocked"] is False
        assert second.status == "draft"
        assert second.anchor_episode_id == 777
        assert session.request.call_count == 2
        assert create.call_count == 2
        records = _evidence_records(storage)
        assert [record["operation"] for record in records] == [
            "create_episode_intent",
            "create_episode_failure",
            "create_episode_intent",
            "create_episode",
        ]
        assert records[2]["details"]["pre_create_episode_ids"] == [555]
        assert records[2]["details"]["pre_create_snapshot_complete"] is True

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

    def test_reconcile_uses_resolved_show_id_for_listing(self, tmp_path, monkeypatch):
        """The episode listing must carry the resolved show id for GraphQL."""
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp()
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        monkeypatch.setattr(pub, "_create_episode", lambda s, station_id: 777)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.anchor_episode_id == 777
        _, kwargs = session.request.call_args
        assert kwargs["json"]["variables"]["showUri"] == "spotify:show:show1"

    def test_reconcile_lookup_failure_does_not_create_duplicate(self, tmp_path, monkeypatch):
        """A failed lookup must fail the publish, never blind-create a duplicate."""
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_error_resp(
            400, '{"property":"query.userId","message":"is required"}'
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)
        self._patch_successful_video_upload(monkeypatch, pub, {})

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "failed"
        assert result.anchor_episode_id is None
        create.assert_not_called()
        assert "reconcile" in result.error.lower()

    def test_reconcile_lookup_failure_with_storage_leaves_no_create_intent(
        self, tmp_path, monkeypatch
    ):
        import podcaster.publish as pub

        storage = MemoryStorage()
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

        session = MagicMock()
        session.request.return_value = _mock_error_resp(
            400, '{"property":"query.userId","message":"is required"}'
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        create = MagicMock(return_value=777)
        monkeypatch.setattr(pub, "_create_episode", create)

        result = pub.upload_video_to_episode(
            self._video(tmp_path),
            555,
            title="My Show",
            publication_storage=storage,
            publication_identity_context=PublicationIdentity(
                "job-1", "2026-W37", "1", "a" * 64, "b" * 64
            ),
        )

        assert result.status == "failed"
        assert result.anchor_episode_id is None
        create.assert_not_called()
        assert storage.get_bytes("publication-evidence/job-1.json") is None


class TestVideoLivePublishGuard:
    def test_video_gate_disabled_by_default(self):
        assert _spotify_video_allow_live_publish() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_video_gate_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", value)
        assert _spotify_video_allow_live_publish() is True


class TestPromoteSpotifyVideoDraft:
    VIDEO_ANCHOR_ID = 321
    AUDIO_ANCHOR_ID = 111
    W35_PROTECTED_IDS = (124658107, 124658398, 124662333)
    OVERVIEW = {
        "userId": 7,
        "title": "Show | W39",
        "description": "<p>Notes</p>",
        "podcastEpisodeType": "full",
        "podcastEpisodeIsExplicit": False,
        "podcastSeasonNumber": 2026,
        "podcastEpisodeNumber": 39,
        "isPublished": False,
        "isDraft": True,
    }

    def _patch_dependencies(self, monkeypatch, pub, *, states=None, publish_side_effect=None):
        session = MagicMock(name="spotify-session")
        monkeypatch.setattr(pub, "_get_credentials", lambda: ("show-id", "sp_dc", "sp_key"))
        monkeypatch.setattr(pub, "_build_session", MagicMock(return_value=session))
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        publish_live = MagicMock()
        if publish_side_effect is not None:
            publish_live.side_effect = publish_side_effect
        monkeypatch.setattr(pub, "_publish_episode_live", publish_live)
        state_reader = MagicMock(
            side_effect=[
                (state, dict(self.OVERVIEW))
                for state in (states if states is not None else [False, True])
            ]
        )
        monkeypatch.setattr(pub, "_read_episode_overview", state_reader)
        return session, publish_live, state_reader

    def test_denies_non_live_mode_without_spotify_calls(self, monkeypatch):
        import podcaster.publish as pub

        build = MagicMock()
        monkeypatch.setattr(pub, "_build_session", build)

        result = pub.promote_spotify_video_draft(self.VIDEO_ANCHOR_ID)

        assert result.terminal_state == "draft_gate_denied"
        assert result.outcome == "draft_created"
        assert result.authorized is False
        build.assert_not_called()

    def test_denies_when_operator_gate_is_disabled(self, monkeypatch):
        import podcaster.publish as pub

        build = MagicMock()
        monkeypatch.setattr(pub, "_build_session", build)

        result = pub.promote_spotify_video_draft(
            self.VIDEO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "draft_gate_denied"
        assert result.authorized is False
        build.assert_not_called()

    @pytest.mark.parametrize("protected_anchor_id", W35_PROTECTED_IDS)
    def test_blocks_protected_anchor_before_session_creation(
        self, monkeypatch, protected_anchor_id
    ):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        build = MagicMock()
        monkeypatch.setattr(pub, "_build_session", build)

        result = pub.promote_spotify_video_draft(
            protected_anchor_id,
            audio_anchor_id=self.AUDIO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "blocked_protected_historical_draft"
        build.assert_not_called()

    def test_rejects_audio_video_anchor_collision(self, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        build = MagicMock()
        monkeypatch.setattr(pub, "_build_session", build)

        result = pub.promote_spotify_video_draft(
            self.AUDIO_ANCHOR_ID,
            audio_anchor_id=self.AUDIO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "draft_gate_denied"
        build.assert_not_called()

    def test_already_published_skips_post(self, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        _session, publish_live, state_reader = self._patch_dependencies(
            monkeypatch, pub, states=[True]
        )

        result = pub.promote_spotify_video_draft(
            self.VIDEO_ANCHOR_ID,
            audio_anchor_id=self.AUDIO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "already_published"
        assert result.is_published is True
        assert result.spotify_episode_url is None
        publish_live.assert_not_called()
        state_reader.assert_called_once()

    def test_spotify_confirmed_publish_reports_published(self, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        session, publish_live, state_reader = self._patch_dependencies(
            monkeypatch, pub, states=[False, True]
        )

        result = pub.promote_spotify_video_draft(
            self.VIDEO_ANCHOR_ID,
            audio_anchor_id=self.AUDIO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "published"
        assert result.outcome == "published"
        assert result.is_published is True
        publish_live.assert_called_once_with(session, self.VIDEO_ANCHOR_ID, "7", self.OVERVIEW)
        assert state_reader.call_count == 2
        assert state_reader.call_args_list == [
            call(session, self.VIDEO_ANCHOR_ID, user_id="7"),
            call(session, self.VIDEO_ANCHOR_ID, user_id="7"),
        ]

    def test_spotify_deterministic_publish_rejection_requires_manual_handoff(self, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        _session, publish_live, _state_reader = self._patch_dependencies(
            monkeypatch, pub, states=[False, False]
        )

        result = pub.promote_spotify_video_draft(
            self.VIDEO_ANCHOR_ID,
            audio_anchor_id=self.AUDIO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "manual_handoff_required"
        assert result.outcome == "manual_handoff_required"
        assert result.is_published is False
        publish_live.assert_called_once()

    def test_unknown_readback_state_is_terminally_unknown(self, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        _session, publish_live, _state_reader = self._patch_dependencies(
            monkeypatch, pub, states=[None]
        )

        result = pub.promote_spotify_video_draft(
            self.VIDEO_ANCHOR_ID,
            audio_anchor_id=self.AUDIO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "publication_state_unknown"
        assert result.outcome == "publication_unknown"
        assert result.is_published is None
        publish_live.assert_not_called()

    def test_overview_403_aborts_without_publish_mutation(self, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        session = MagicMock()
        session.request.return_value = _mock_error_resp(403, "forbidden")
        monkeypatch.setattr(pub, "_get_credentials", lambda: ("show-id", "sp_dc", "sp_key"))
        monkeypatch.setattr(pub, "_build_session", MagicMock(return_value=session))
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        publish_live = MagicMock()
        monkeypatch.setattr(pub, "_publish_episode_live", publish_live)

        result = pub.promote_spotify_video_draft(
            self.VIDEO_ANCHOR_ID,
            audio_anchor_id=self.AUDIO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "publication_state_unknown"
        assert result.outcome == "publication_unknown"
        assert result.is_published is None
        publish_live.assert_not_called()

    def test_spotify_ambiguous_publish_reports_publication_unknown_without_retry(self, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        session, publish_live, state_reader = self._patch_dependencies(
            monkeypatch, pub, states=[False, None]
        )
        result = pub.promote_spotify_video_draft(
            self.VIDEO_ANCHOR_ID,
            audio_anchor_id=self.AUDIO_ANCHOR_ID,
            spotify_video_publish_mode="live",
            job_id="job-1",
            run_id="run-1",
        )
        assert result.terminal_state == "publication_state_unknown"
        assert result.outcome == "publication_unknown"
        assert result.publish_run_id == "run-1"
        publish_live.assert_called_once_with(session, self.VIDEO_ANCHOR_ID, "7", self.OVERVIEW)
        assert state_reader.call_count == 2

    def test_publish_error_requires_manual_handoff(self, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        _session, publish_live, _state_reader = self._patch_dependencies(
            monkeypatch,
            pub,
            states=[False, False],
            publish_side_effect=pub.SpotifyPublishError("publish rejected"),
        )

        result = pub.promote_spotify_video_draft(
            self.VIDEO_ANCHOR_ID,
            audio_anchor_id=self.AUDIO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "manual_handoff_required"
        assert result.is_published is False
        assert result.details["mutation_error"] == "publish rejected"
        publish_live.assert_called_once()

    def test_dry_run_denies_promotion_after_authorization(self, monkeypatch):
        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")

        result = pub.promote_spotify_video_draft(
            self.VIDEO_ANCHOR_ID,
            spotify_video_publish_mode="live",
        )

        assert result.terminal_state == "draft_gate_denied"
        assert result.authorized is True
        assert result.dry_run is True

    def test_terminal_telemetry_emitted_once_per_call(self, monkeypatch, caplog):
        import logging

        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        self._patch_dependencies(monkeypatch, pub, states=[False, True, True])

        with caplog.at_level(logging.INFO, logger="podcaster.publish"):
            denied = pub.promote_spotify_video_draft(self.VIDEO_ANCHOR_ID)
            published = pub.promote_spotify_video_draft(
                self.VIDEO_ANCHOR_ID,
                audio_anchor_id=self.AUDIO_ANCHOR_ID,
                spotify_video_publish_mode="live",
                job_id="job-664",
                run_id="run-664",
            )
            already = pub.promote_spotify_video_draft(
                self.VIDEO_ANCHOR_ID,
                audio_anchor_id=self.AUDIO_ANCHOR_ID,
                spotify_video_publish_mode="live",
                job_id="job-664",
                run_id="run-664-retry",
            )

        terminal_logs = [
            record
            for record in caplog.records
            if "spotify_video_publication_terminal" in record.getMessage()
        ]
        assert denied.terminal_state == "draft_gate_denied"
        assert published.terminal_state == "published"
        assert already.terminal_state == "already_published"
        assert len(terminal_logs) == 3
        denied_message, published_message, already_message = [
            record.getMessage() for record in terminal_logs
        ]
        assert "video_auth_granted=False" in denied_message
        assert "w35_check=not_run" in denied_message
        assert "video_auth_granted=True" in published_message
        assert "w35_check=pass" in published_message
        assert "video_auth_granted=True" in already_message
        assert "w35_check=pass" in already_message

    def test_terminal_telemetry_marks_blocked_w35_check(self, monkeypatch, caplog):
        import logging

        import podcaster.publish as pub

        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")

        with caplog.at_level(logging.INFO, logger="podcaster.publish"):
            result = pub.promote_spotify_video_draft(
                self.W35_PROTECTED_IDS[0],
                audio_anchor_id=self.AUDIO_ANCHOR_ID,
                spotify_video_publish_mode="live",
                job_id="job-665",
            )

        terminal_logs = [
            record.getMessage()
            for record in caplog.records
            if "spotify_video_publication_terminal" in record.getMessage()
        ]
        assert result.terminal_state == "blocked_protected_historical_draft"
        assert len(terminal_logs) == 1
        assert "video_auth_granted=True" in terminal_logs[0]
        assert "w35_check=blocked" in terminal_logs[0]


class TestGetEpisodePublicationState:
    ANCHOR_ID = 125401976

    def test_extract_state_handles_episodes_list_response_published(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp(
            {
                "episodes": [
                    {"id": self.ANCHOR_ID, "isPublished": True, "isDraft": False},
                ],
                "audios": [],
                "users": [],
            }
        )

        assert pub._get_episode_publication_state(session, self.ANCHOR_ID, user_id="7") is True

    def test_extract_state_matches_episodeId_field(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp(
            {
                "episodes": [{"episodeId": self.ANCHOR_ID, "isPublished": False}],
                "audios": [],
                "users": [],
            }
        )

        assert pub._get_episode_publication_state(session, self.ANCHOR_ID, user_id="7") is False

    def test_extract_state_null_isPublished_unknown(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp(
            {
                "episodes": [{"episodeId": self.ANCHOR_ID, "isPublished": None}],
                "audios": [],
                "users": [],
            }
        )

        assert pub._get_episode_publication_state(session, self.ANCHOR_ID, user_id="7") is None

    def test_extract_state_episodeId_match_already_published(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp(
            {
                "episodes": [{"episodeId": self.ANCHOR_ID, "isPublished": True}],
                "audios": [],
                "users": [],
            }
        )

        assert pub._get_episode_publication_state(session, self.ANCHOR_ID, user_id="7") is True

    def test_extract_state_no_episodeId_match_unknown(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp(
            {
                "episodes": [{"episodeId": 999999, "isPublished": False}],
                "audios": [],
                "users": [],
            }
        )

        assert pub._get_episode_publication_state(session, self.ANCHOR_ID, user_id="7") is None

    def test_overview_route_and_403_is_unknown_not_expired(self):
        """A readback 403 is unknown state, not draft evidence or credential expiry."""
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_error_resp(403, "forbidden")

        assert pub._get_episode_publication_state(session, self.ANCHOR_ID, user_id="7") is None
        args, kwargs = session.request.call_args
        assert args[0] == "GET"
        assert args[1].endswith(f"/v3/episodes/{self.ANCHOR_ID}/overview")
        assert kwargs["params"] == {"returnWebIds": "true", "isMumsCompatible": "true"}

    def test_overview_route_retries_transient_failures(self, monkeypatch):
        from podcaster import publish as pub

        monkeypatch.setattr(pub.time, "sleep", lambda *args, **kwargs: None)
        session = MagicMock()
        session.request.side_effect = [
            _mock_error_resp(500, "provider error"),
            _mock_json_resp(
                {
                    "episodes": [
                        {"episodeId": self.ANCHOR_ID, "isPublished": True},
                    ],
                    "audios": [],
                    "users": [],
                }
            ),
        ]

        assert pub._get_episode_publication_state(session, self.ANCHOR_ID, user_id="7") is True
        assert session.request.call_count == 2


def _mock_error_resp(status_code: int, body: str) -> MagicMock:
    """A response whose raise_for_status raises an HTTPError, like requests does."""
    import requests

    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    resp.headers = {}
    resp.raise_for_status.side_effect = requests.HTTPError(
        f"{status_code} Client Error", response=resp
    )
    return resp


class TestFindExistingDraft:
    """#656: reconcile-before-create must send userId and never mask failures."""

    def _session(self, payload):
        session = MagicMock()
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("data"), dict)
            and set(payload["data"]) == {"webGetIndexedEpisodeList"}
        ):
            legacy = payload["data"]["webGetIndexedEpisodeList"]
            if (
                isinstance(legacy, dict)
                and isinstance(legacy.get("episodes"), list)
                and isinstance(legacy.get("hasNextPage"), bool)
            ):
                payload = _graphql_listing_payload(
                    legacy["episodes"],
                    hasNextPage=legacy["hasNextPage"],
                )
        if isinstance(payload, dict) and (
            any(key in payload for key in ("episodes", "items", "results"))
            or isinstance(payload.get("data"), list)
        ):
            payload = dict(payload)
            episodes = payload.pop("episodes", payload.pop("items", payload.pop("results", [])))
            payload = _graphql_listing_payload(episodes, **payload)
        session.request.return_value = _mock_json_resp(payload)
        return session

    def test_uses_graphql_episode_index_contract(self):
        from podcaster import publish as pub

        session = self._session({"episodes": []})
        assert (
            pub._find_existing_draft(
                session, "99", "My Show", user_id="7", show_id="show1", exclude_id=None
            )
            is None
        )

        args, kwargs = session.request.call_args
        assert args[0] == "POST"
        assert args[1] == pub._SPOTIFY_CREATORS_GRAPHQL_URL
        assert kwargs["json"]["operationName"] == "WebGetIndexedEpisodeList"
        assert kwargs["json"]["variables"] == {
            "showUri": "spotify:show:show1",
            "currentPage": 1,
            "pageSize": pub._EPISODE_LIST_PAGE_SIZE,
            "sortOrder": None,
            "sortBy": None,
            "search": None,
            "filter": "DRAFT_EPISODES",
            "includeMembershipTiers": False,
            "analyticsWindow": "WINDOW_ALL_TIME",
        }
        assert kwargs["json"]["extensions"]["persistedQuery"]["sha256Hash"]
        assert "query" not in kwargs["json"]
        assert kwargs["headers"]["x-creator-client"] == "public-website"

    def test_returns_matching_draft(self):
        from podcaster import publish as pub

        session = self._session(
            {
                "episodes": [
                    {"episodeId": 111, "title": "Other", "status": "draft"},
                    {"episodeId": 888, "title": "  My Show  ", "status": "draft"},
                ]
            }
        )
        assert (
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1") == 888
        )

    def test_multiple_matching_draft_ids_fail_closed(self):
        from podcaster import publish as pub

        session = self._session(
            {
                "episodes": [
                    {"episodeId": 888, "title": "My Show", "status": "draft"},
                    {"episodeId": 889, "title": " My Show ", "status": "draft"},
                ]
            }
        )
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert "multiple reusable drafts" in str(exc.value)
        assert "888" in str(exc.value)
        assert "889" in str(exc.value)

    def test_exclude_id_is_never_returned(self):
        from podcaster import publish as pub

        session = self._session(
            {"episodes": [{"episodeId": 555, "title": "My Show", "status": "draft"}]}
        )
        assert (
            pub._find_existing_draft(
                session, "99", "My Show", user_id="7", show_id="show1", exclude_id=555
            )
            is None
        )

    def test_published_episode_is_not_reused(self):
        from podcaster import publish as pub

        session = self._session(
            {"episodes": [{"episodeId": 888, "title": "My Show", "status": "published"}]}
        )
        assert (
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1") is None
        )

    def test_empty_listing_returns_none(self):
        from podcaster import publish as pub

        session = self._session({"episodes": []})
        assert (
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1") is None
        )

    def test_empty_listing_is_distinct_from_graphql_error(self):
        from podcaster import publish as pub

        empty = self._session(
            {
                "data": {
                    "webGetIndexedEpisodeList": {
                        "episodes": [],
                        "hasNextPage": False,
                    }
                }
            }
        )
        assert (
            pub._find_existing_draft(empty, "99", "My Show", user_id="7", show_id="show1") is None
        )

        errored = self._session({"errors": [{"message": "boom"}]})
        with pytest.raises(pub.SpotifyDraftReconcileError):
            pub._find_existing_draft(errored, "99", "My Show", user_id="7", show_id="show1")

    @pytest.mark.parametrize("payload", [[], {"episodes": []}, {"data": []}])
    def test_non_graphql_listing_shapes_fail_closed(self, payload):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp(payload)
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert "GraphQL" in str(exc.value)
        assert "duplicate draft" in str(exc.value)

    def test_missing_user_id_is_explicit_and_makes_no_request(self):
        from podcaster import publish as pub

        session = MagicMock()
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="  ")
        assert "userId" in str(exc.value)
        session.request.assert_not_called()

    def test_missing_show_id_is_explicit_and_makes_no_request(self, monkeypatch):
        from podcaster import publish as pub

        session = MagicMock()
        create = MagicMock()
        monkeypatch.setattr(pub, "_create_episode", create)
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._reconcile_or_create_draft(session, "99", user_id="7", title="My Show")
        assert "Spotify show id" in str(exc.value)
        assert "station id" in str(exc.value)
        session.request.assert_not_called()
        create.assert_not_called()

    def test_http_error_raises_instead_of_reporting_no_draft(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_error_resp(
            400, '{"property":"query.userId","message":"is required"}'
        )
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        message = str(exc.value)
        assert "Refusing to create a new draft" in message
        # Sanitized: no response body, cookies or tokens echoed into the message.
        assert "query.userId" not in message

    @pytest.mark.parametrize("status_code", [400, 403, 500])
    def test_listing_http_errors_fail_closed_and_are_not_empty(self, monkeypatch, status_code):
        from podcaster import publish as pub

        monkeypatch.setattr(pub.time, "sleep", lambda *args, **kwargs: None)
        session = MagicMock()
        session.request.return_value = _mock_error_resp(status_code, "provider error")
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert f"HTTP {status_code}" in str(exc.value)
        assert session.request.call_count == (3 if status_code == 500 else 1)

    def test_listing_403_is_not_classified_as_expired_credentials(self):
        """jmservera/SquadScope-Podcaster#685: 403 readback/listing can be state/permission."""
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_error_resp(403, "forbidden")
        with pytest.raises(pub.SpotifyDraftReconcileError):
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")

    def test_malformed_json_raises(self):
        from podcaster import publish as pub

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("not json")
        session = MagicMock()
        session.request.return_value = resp

        with pytest.raises(pub.SpotifyDraftReconcileError):
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")

    def test_truncated_listing_raises_instead_of_returning_partial_absence(self):
        from podcaster import publish as pub

        session = self._session({"episodes": [], "hasMore": True})
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert "pagination" in str(exc.value)

    def test_drifted_pagination_flag_raises_instead_of_returning_absence(self):
        from podcaster import publish as pub

        session = self._session({"episodes": [], "hasMore": "true"})
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = pub._find_existing_draft(
                session, "99", "My Show", user_id="7", show_id="show1"
            )
            pytest.fail(f"drifted pagination flag returned partial result: {result!r}")
        assert "pagination" in str(exc.value)

    @pytest.mark.parametrize("cursor_key", ["nextPageToken", "nextPage"])
    def test_null_top_level_pagination_cursor_raises_without_false_flag(self, cursor_key):
        from podcaster import publish as pub

        session = self._session({"episodes": [], "hasMore": True, cursor_key: None})
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = pub._find_existing_draft(
                session, "99", "My Show", user_id="7", show_id="show1"
            )
            pytest.fail(f"null {cursor_key} cursor returned partial result: {result!r}")
        assert "pagination" in str(exc.value)

    def test_graphql_cursor_without_explicit_has_next_page_raises(self):
        from podcaster import publish as pub

        session = self._session(
            {
                "data": {
                    "webGetIndexedEpisodeList": {
                        "episodes": {
                            "nodes": [],
                            "pageInfo": {"endCursor": None},
                        }
                    }
                }
            }
        )
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = pub._find_existing_draft(
                session, "99", "My Show", user_id="7", show_id="show1"
            )
            pytest.fail(f"null GraphQL endCursor returned partial result: {result!r}")
        assert "GraphQL" in str(exc.value)

    @pytest.mark.parametrize("cursor_key", ["nextPageToken", "nextPage"])
    def test_top_level_cursor_without_explicit_true_flag_raises(self, cursor_key):
        from podcaster import publish as pub

        session = self._session({"episodes": [], cursor_key: "cursor-2"})
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert "pagination" in str(exc.value)
        session.request.assert_called_once()

    def test_missing_pagination_metadata_raises(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp(
            {"data": {"webGetIndexedEpisodeList": {"episodes": []}}}
        )
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert "GraphQL" in str(exc.value)

    def test_nested_multiple_episode_arrays_raise(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp(
            {
                "data": {
                    "webGetIndexedEpisodeList": {
                        "episodes": {
                            "nodes": [],
                            "items": [],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        )
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert "GraphQL" in str(exc.value)

    @pytest.mark.parametrize(
        ("label", "cursor_value"),
        [
            ("spaces", "   "),
            ("tab", "\t"),
            ("newline", "\n"),
            ("non-breaking space", "\u00a0"),
            ("zero-width space", "\u200b"),
            ("zero-width non-joiner", "\u200c"),
            ("zero-width joiner", "\u200d"),
            ("byte-order mark", "\ufeff"),
            ("mixed whitespace", " \t\n\u00a0\u200b\u200c\u200d\ufeff "),
        ],
    )
    @pytest.mark.parametrize("cursor_key", ["nextPageToken", "nextPage"])
    def test_whitespace_only_top_level_pagination_cursor_raises(
        self, cursor_key, label, cursor_value
    ):
        from podcaster import publish as pub

        session = self._session({"episodes": [], "hasMore": True, cursor_key: cursor_value})
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = pub._find_existing_draft(
                session, "99", "My Show", user_id="7", show_id="show1"
            )
            pytest.fail(
                f"{label} {cursor_key} cursor returned partial result instead of raising: "
                f"{result!r}"
            )
        assert "pagination" in str(exc.value)

    @pytest.mark.parametrize(
        ("label", "cursor_value"),
        [
            ("spaces", "   "),
            ("tab", "\t"),
            ("newline", "\n"),
            ("non-breaking space", "\u00a0"),
            ("zero-width space", "\u200b"),
            ("zero-width non-joiner", "\u200c"),
            ("zero-width joiner", "\u200d"),
            ("byte-order mark", "\ufeff"),
            ("mixed whitespace", " \t\n\u00a0\u200b\u200c\u200d\ufeff "),
        ],
    )
    def test_whitespace_only_graphql_end_cursor_raises(self, label, cursor_value):
        from podcaster import publish as pub

        session = self._session(
            {
                "data": {
                    "webGetIndexedEpisodeList": {
                        "episodes": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": True, "endCursor": cursor_value},
                        }
                    }
                }
            }
        )
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = pub._find_existing_draft(
                session, "99", "My Show", user_id="7", show_id="show1"
            )
            pytest.fail(
                f"{label} GraphQL endCursor returned partial result instead of raising: {result!r}"
            )
        assert "GraphQL" in str(exc.value)

    def test_numbered_pagination_fetches_second_page(self):
        from podcaster import publish as pub

        first_page = [
            {"episodeId": 1000 + index, "title": "Other", "status": "draft"}
            for index in range(pub._EPISODE_LIST_PAGE_SIZE)
        ]
        session = MagicMock()
        session.request.side_effect = [
            _mock_graphql_listing_resp(first_page, totalItems=51, totalPages=2),
            _mock_graphql_listing_resp(
                [{"episodeId": 888, "title": "My Show", "status": "draft"}],
                currentPage=2,
                totalItems=51,
                totalPages=2,
            ),
        ]

        assert (
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1") == 888
        )
        second_call_vars = session.request.call_args_list[1].kwargs["json"]["variables"]
        assert second_call_vars["currentPage"] == 2

    @pytest.mark.parametrize("cursor_key", ["nextPageToken", "nextPage"])
    def test_null_top_level_cursor_with_explicit_false_flag_is_terminal(self, cursor_key):
        from podcaster import publish as pub

        session = self._session({"episodes": [], "hasMore": False, cursor_key: None})
        assert (
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1") is None
        )

    def test_paginated_listing_fetches_all_pages_before_absence(self):
        from podcaster import publish as pub

        first_page = [
            {"episodeId": 1000 + index, "title": "Other", "status": "draft"}
            for index in range(pub._EPISODE_LIST_PAGE_SIZE)
        ]
        session = MagicMock()
        session.request.side_effect = [
            _mock_graphql_listing_resp(first_page, totalItems=51, totalPages=2),
            _mock_graphql_listing_resp(
                [{"episodeId": 888, "title": "My Show", "status": "draft"}],
                currentPage=2,
                totalItems=51,
                totalPages=2,
            ),
        ]
        assert (
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1") == 888
        )
        second_call_vars = session.request.call_args_list[1].kwargs["json"]["variables"]
        assert second_call_vars["currentPage"] == 2

    @pytest.mark.parametrize(
        ("final_episode", "message"),
        [
            pytest.param(
                {"anchorId": "1000", "title": "Other", "status": "draft"},
                "repeated canonical episode id 1000",
                id="repeated-canonical-identity",
            ),
            pytest.param(
                {"title": "Other", "status": "draft"},
                "exactly one unambiguous canonical episode id",
                id="omitted-canonical-identity",
            ),
        ],
    )
    def test_numerically_complete_pages_with_repeated_or_omitted_identity_fail_closed(
        self, monkeypatch, final_episode, message
    ):
        from podcaster import publish as pub

        first_page = [
            {"episodeId": 1000 + index, "title": "Other", "status": "draft"}
            for index in range(pub._EPISODE_LIST_PAGE_SIZE)
        ]
        session = MagicMock()
        session.request.side_effect = [
            _mock_graphql_listing_resp(first_page, totalItems=51, totalPages=2),
            _mock_graphql_listing_resp(
                [final_episode],
                currentPage=2,
                totalItems=51,
                totalPages=2,
            ),
        ]
        create = MagicMock()
        monkeypatch.setattr(pub, "_create_episode", create)

        with pytest.raises(
            pub.SpotifyDraftReconcileError,
            match=message,
        ):
            pub._reconcile_or_create_draft(
                session,
                "99",
                user_id="7",
                show_id="show1",
                title="Missing Target",
            )
        create.assert_not_called()

    def test_paginated_listing_second_page_failure_raises_without_partial_absence(
        self, monkeypatch
    ):
        from podcaster import publish as pub

        monkeypatch.setattr(pub.time, "sleep", lambda *args, **kwargs: None)
        session = MagicMock()
        provider_error = _mock_error_resp(500, "provider error")
        first_page = [
            {"episodeId": 1000 + index, "title": "Other", "status": "draft"}
            for index in range(pub._EPISODE_LIST_PAGE_SIZE)
        ]
        session.request.side_effect = [
            _mock_graphql_listing_resp(first_page, totalItems=51, totalPages=2),
            provider_error,
            provider_error,
            provider_error,
        ]

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = pub._find_existing_draft(
                session, "99", "My Show", user_id="7", show_id="show1"
            )
            pytest.fail(f"truncated paginated listing returned partial result: {result!r}")

        assert "HTTP 500" in str(exc.value)
        assert len(session.request.call_args_list) == 4
        second_call_vars = session.request.call_args_list[1].kwargs["json"]["variables"]
        assert second_call_vars["currentPage"] == 2

    def test_paginated_listing_still_returns_match_on_first_page(self):
        from podcaster import publish as pub

        first_page = [
            {"episodeId": 888, "title": "My Show", "status": "draft"},
            *[
                {"episodeId": 1000 + index, "title": "Other", "status": "draft"}
                for index in range(pub._EPISODE_LIST_PAGE_SIZE - 1)
            ],
        ]
        session = MagicMock()
        session.request.side_effect = [
            _mock_graphql_listing_resp(
                first_page,
                totalItems=51,
                totalPages=2,
            ),
            _mock_graphql_listing_resp(
                [{"episodeId": 2000, "title": "Other", "status": "draft"}],
                currentPage=2,
                totalItems=51,
                totalPages=2,
            ),
        ]
        assert (
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1") == 888
        )

    def test_terminal_graphql_page_retaining_end_cursor_is_not_followed(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp()

        assert (
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1") is None
        )
        session.request.assert_called_once()

    def test_present_non_object_page_info_fails_closed(self):
        from podcaster import publish as pub

        session = self._session(
            {
                "data": {
                    "webGetIndexedEpisodeList": {
                        "episodes": {"nodes": [], "pageInfo": "not-an-object"}
                    }
                }
            }
        )
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert "GraphQL" in str(exc.value)

    def test_transport_error_raises(self):
        import requests

        from podcaster import publish as pub

        session = MagicMock()
        session.request.side_effect = requests.ConnectionError("dns")
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert "dns" not in str(exc.value)

    def test_credential_expiry_propagates(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_error_resp(401, "unauthorized")
        with pytest.raises(pub.SpotifyCredentialExpiredError):
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")


class TestEpisodeListingSchema:
    """Only the provider-confirmed GraphQL path can prove draft absence."""

    def _session(self, payload):
        session = MagicMock()
        session.request.return_value = _mock_json_resp(payload)
        return session

    def _lookup(self, payload, **kwargs):
        from podcaster import publish as pub

        return pub._find_existing_draft(
            self._session(payload), "99", "My Show", user_id="7", show_id="show1", **kwargs
        )

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("unknown container", {"stationEpisodes": []}),
            ("error body", {"property": "query.userId", "message": "is required"}),
            ("nested object under known key", {"episodes": {"0": {"title": "My Show"}}}),
            ("primitive payload", 42),
            ("string payload", "episodes"),
            ("null payload", None),
            ("boolean payload", True),
            ("renamed item fields", {"episodes": [{"episode_id": 1, "episode_title": "My Show"}]}),
            ("non-object entry", {"episodes": ["My Show"]}),
            ("nested list entry", {"episodes": [[{"title": "My Show", "status": "draft"}]]}),
            ("non-string title", {"episodes": [{"title": {"text": "My Show"}, "id": 1}]}),
            ("match without usable id", {"episodes": [{"title": "My Show", "status": "draft"}]}),
        ],
    )
    def test_unreadable_listing_fails_closed(self, label, payload):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup(payload)
        assert "duplicate draft" in str(exc.value), label

    def test_error_body_values_are_not_echoed(self):
        from podcaster import publish as pub

        payload = {"property": "query.userId", "message": "is required", "token": "sekret"}
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup(payload)
        message = str(exc.value)
        # Key *names* aid schema diagnosis; values may carry tokens and never leak.
        assert "token" in message
        assert "sekret" not in message
        assert "is required" not in message

    def test_confirmed_graphql_empty_listing_is_a_legitimate_no_match(self):
        assert self._lookup(_graphql_listing_payload([])) is None

    @pytest.mark.parametrize(
        "episode",
        [
            pytest.param(
                {"title": "Other", "status": "draft"},
                id="missing-identity",
            ),
            pytest.param(
                {"episodeId": "not-a-number", "title": "Other", "status": "draft"},
                id="malformed-identity",
            ),
            pytest.param(
                {
                    "episodeId": 101,
                    "anchorId": 202,
                    "title": "Other",
                    "status": "draft",
                },
                id="conflicting-identity-aliases",
            ),
        ],
    )
    def test_numeric_page_with_ambiguous_identity_never_authorizes_create(
        self, monkeypatch, episode
    ):
        from podcaster import publish as pub

        session = self._session(_graphql_listing_payload([episode], totalItems=1, totalPages=1))
        create = MagicMock()
        monkeypatch.setattr(pub, "_create_episode", create)

        with pytest.raises(
            pub.SpotifyDraftReconcileError,
            match="exactly one unambiguous canonical episode id",
        ):
            pub._reconcile_or_create_draft(
                session,
                "99",
                user_id="7",
                show_id="show1",
                title="Missing Target",
            )
        create.assert_not_called()

    @pytest.mark.parametrize(
        ("episodes", "pagination"),
        [
            ([], {"totalItems": 1, "totalPages": 0}),
            ([], {"totalItems": 1, "totalPages": 1}),
            ([], {"totalItems": 0, "totalPages": 2}),
            (
                [{"episodeId": 1, "title": "Other", "status": "draft"}],
                {"totalItems": 0, "totalPages": 1},
            ),
            (
                [
                    {"episodeId": 1000 + index, "title": "Other", "status": "draft"}
                    for index in range(50)
                ],
                {"totalItems": 50, "totalPages": 2},
            ),
            (
                [
                    {"episodeId": 1000 + index, "title": "Other", "status": "draft"}
                    for index in range(49)
                ],
                {"totalItems": 51, "totalPages": 2},
            ),
        ],
    )
    def test_inconsistent_count_metadata_never_authorizes_create(
        self, monkeypatch, episodes, pagination
    ):
        from podcaster import publish as pub

        payload = _graphql_listing_payload(episodes, **pagination)
        session = self._session(payload)
        create = MagicMock()
        monkeypatch.setattr(pub, "_create_episode", create)

        with pytest.raises(pub.SpotifyDraftReconcileError, match="pagination is inconsistent"):
            pub._reconcile_or_create_draft(
                session,
                "99",
                user_id="7",
                show_id="show1",
                title="My Show",
            )
        create.assert_not_called()

    @pytest.mark.parametrize("page_size", [0, 1, 49, 51])
    def test_response_page_size_must_match_numeric_request_contract(self, monkeypatch, page_size):
        from podcaster import publish as pub

        session = self._session(
            _graphql_listing_payload([], pageSize=page_size, totalItems=0, totalPages=1)
        )
        create = MagicMock()
        monkeypatch.setattr(pub, "_create_episode", create)

        with pytest.raises(pub.SpotifyDraftReconcileError, match="pagination is inconsistent"):
            pub._reconcile_or_create_draft(
                session,
                "99",
                user_id="7",
                show_id="show1",
                title="My Show",
            )
        create.assert_not_called()

    def test_final_page_item_count_mismatch_never_authorizes_create(self, monkeypatch):
        from podcaster import publish as pub

        first_page = [
            {"episodeId": 1000 + index, "title": "Other", "status": "draft"}
            for index in range(pub._EPISODE_LIST_PAGE_SIZE)
        ]
        session = MagicMock()
        session.request.side_effect = [
            _mock_graphql_listing_resp(first_page, totalItems=51, totalPages=2),
            _mock_graphql_listing_resp(
                [],
                currentPage=2,
                totalItems=51,
                totalPages=2,
            ),
        ]
        create = MagicMock()
        monkeypatch.setattr(pub, "_create_episode", create)

        with pytest.raises(pub.SpotifyDraftReconcileError, match="pagination is inconsistent"):
            pub._reconcile_or_create_draft(
                session,
                "99",
                user_id="7",
                show_id="show1",
                title="My Show",
            )
        create.assert_not_called()

    @pytest.mark.parametrize("alias", ["episodes", "items", "data", "results"])
    def test_alternate_empty_listing_aliases_fail_closed(self, alias):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError):
            self._lookup({alias: []})

    def test_bare_list_container_is_rejected_at_graphql_boundary(self):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError):
            self._lookup([{"episodeId": 888, "title": "My Show", "status": "draft"}])

    def test_untitled_draft_is_understood_as_no_match(self):
        """The orphan shape this PR is about: a created-but-never-titled draft."""
        payload = _graphql_listing_payload(
            [
                {"episodeId": 901, "title": None, "status": "draft"},
                {"episodeId": 902, "title": "", "status": "draft"},
            ]
        )
        assert self._lookup(payload) is None

    def test_exclude_id_still_applies_under_strict_parsing(self):
        payload = _graphql_listing_payload(
            [
                {"episodeId": 555, "title": "My Show", "status": "draft"},
                {"episodeId": 888, "title": "My Show", "status": "draft"},
            ]
        )
        assert self._lookup(payload, exclude_id=555) == 888

    @pytest.mark.parametrize("alias", ["episodes", "data", "results", "nodes"])
    def test_extra_listing_aliases_fail_closed(self, alias):
        from podcaster import publish as pub

        payload = _graphql_listing_payload([])
        payload["data"]["showByShowUri"]["episodesV2"][alias] = []
        with pytest.raises(pub.SpotifyDraftReconcileError, match="episodesV2 fields changed"):
            self._lookup(payload)

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("empty top-level cursor", {"episodes": [], "nextPageToken": ""}),
            ("falsey numeric pagination flag", {"episodes": [], "hasMore": 0}),
        ],
    )
    def test_final_pagination_hint_drift_fails_closed(self, label, payload):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = pub._match_existing_draft(payload, "99", "My Show")
            pytest.fail(f"{label} returned partial result instead of raising: {result!r}")
        assert "duplicate draft" in str(exc.value), label

    def test_multiple_graphql_episode_containers_fail_closed(self):
        from podcaster import publish as pub

        payload = {
            "data": {
                "webGetIndexedEpisodeList": {
                    "episodes": [],
                    "hasNextPage": False,
                },
                "shadowEpisodeList": {
                    "episodes": [{"episodeId": 888, "title": "My Show", "status": "draft"}],
                    "hasNextPage": False,
                },
            }
        }
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = self._lookup(payload)
            pytest.fail(f"multiple GraphQL containers returned partial result: {result!r}")
        assert "GraphQL" in str(exc.value)

    def test_unexpected_episode_like_graphql_field_does_not_prove_absence(self, monkeypatch):
        from podcaster import publish as pub

        payload = {"data": {"viewer": {"items": [], "hasNextPage": False}}}
        session = self._session(payload)
        create = MagicMock()
        monkeypatch.setattr(pub, "_create_episode", create)
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = pub._reconcile_or_create_draft(
                session,
                "99",
                user_id="7",
                show_id="show1",
                title="My Show",
            )
            pytest.fail(f"unexpected GraphQL container returned absence: {result!r}")
        assert "GraphQL" in str(exc.value)
        create.assert_not_called()

    def test_multiple_graphql_list_fields_fail_closed(self):
        from podcaster import publish as pub

        payload = {
            "data": {
                "webGetIndexedEpisodeList": {
                    "episodes": [],
                    "items": [{"episodeId": 888, "title": "My Show", "status": "draft"}],
                }
            }
        }
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            result = self._lookup(payload)
            pytest.fail(f"multiple GraphQL list fields returned partial result: {result!r}")
        assert "GraphQL" in str(exc.value)


_LIVE_SORTABLE = [
    "TITLE",
    "PUBLISHED_ON",
    "CONTENT_TYPE",
    "DURATION_MS",
    "AD_COUNT",
    "CREATED_ON",
    "PLAY_COUNT",
    "START_COUNT",
    "LISTENERS",
    "PLAYS_AND_DOWNLOADS",
]


def _live_draft_item(episode_id, title):
    """An item in the shape the live ``WebGetIndexedEpisodeList`` returned (2026-09)."""
    return {
        "episodeId": episode_id,
        "title": title,
        "uri": f"spotify:episode:{episode_id}",
        "contentType": "EPISODE_CONTENT_TYPE_VIDEO",
        "episodeType": "EPISODE_TYPE_FULL",
        "createdOn": {"seconds": "1790197835"},
        "publishedOn": None,
        "asset": {"downloadUrl": None, "lengthMs": "364691", "mediaFiles": []},
        "clips": {"clips": []},
        "isSpotifyExclusive": False,
        "paywall": {"isPaywallContent": False},
    }


def _live_listing_payload(
    items, *, current_page=1, total_items=None, total_pages=None, index_status="COMPLETED"
):
    """``episodesV2`` in the live shape: ``indexStatus, items, pagination, sortable``."""
    if total_items is None:
        total_items = len(items)
    if total_pages is None:
        total_pages = (total_items + 49) // 50
    return {
        "data": {
            "showByShowUri": {
                "episodesV2": {
                    "indexStatus": index_status,
                    "items": items,
                    "pagination": {
                        "currentPage": current_page,
                        "pageSize": 50,
                        "totalItems": total_items,
                        "totalPages": total_pages,
                    },
                    "sortable": list(_LIVE_SORTABLE),
                }
            }
        }
    }


class TestLiveEpisodeListingShape:
    """The 2026-09 listing adds ``sortable`` and reports an empty listing as 0 pages."""

    TITLE = "Target Video | W39"

    def _session(self, *payloads):
        session = MagicMock()
        session.request.side_effect = [_mock_json_resp(payload) for payload in payloads]
        return session

    def _reconcile(self, monkeypatch, session, create_id=None):
        from podcaster import publish as pub

        create = MagicMock(return_value=create_id)
        monkeypatch.setattr(pub, "_create_episode", create)
        result = pub._reconcile_or_create_draft(
            session, "99", user_id="7", show_id="show1", title=self.TITLE
        )
        return result, create

    def test_observed_empty_draft_listing_proves_absence(self, monkeypatch):
        # Exact pagination the live DRAFT_EPISODES read returned for W39.
        session = self._session(_live_listing_payload([], total_items=0, total_pages=0))
        result, create = self._reconcile(monkeypatch, session, create_id=4242)
        assert result == (4242, True)
        create.assert_called_once()
        assert session.request.call_count == 1

    def test_single_page_without_match_proves_absence(self, monkeypatch):
        items = [_live_draft_item(1000 + i, f"Other {i}") for i in range(17)]
        session = self._session(_live_listing_payload(items))
        result, create = self._reconcile(monkeypatch, session, create_id=4242)
        assert result == (4242, True)
        create.assert_called_once()

    def test_existing_matching_draft_is_adopted_without_create(self, monkeypatch):
        items = [_live_draft_item(1001, "Other"), _live_draft_item(126212999, self.TITLE)]
        session = self._session(_live_listing_payload(items))
        result, create = self._reconcile(monkeypatch, session)
        assert result == (126212999, False)
        create.assert_not_called()

    def test_multiple_pages_are_followed_to_find_match_on_last_page(self, monkeypatch):
        first = [_live_draft_item(1000 + i, f"Other {i}") for i in range(50)]
        second = [_live_draft_item(2000, self.TITLE)]
        session = self._session(
            _live_listing_payload(first, total_items=51),
            _live_listing_payload(second, current_page=2, total_items=51),
        )
        result, create = self._reconcile(monkeypatch, session)
        assert result == (2000, False)
        create.assert_not_called()
        pages = [
            call.kwargs["json"]["variables"]["currentPage"]
            for call in session.request.call_args_list
        ]
        assert pages == [1, 2]

    def test_multiple_pages_without_match_prove_absence_only_after_last_page(self, monkeypatch):
        first = [_live_draft_item(1000 + i, f"Other {i}") for i in range(50)]
        second = [_live_draft_item(2000 + i, f"More {i}") for i in range(3)]
        session = self._session(
            _live_listing_payload(first, total_items=53),
            _live_listing_payload(second, current_page=2, total_items=53),
        )
        result, create = self._reconcile(monkeypatch, session, create_id=4242)
        assert result == (4242, True)
        create.assert_called_once()
        assert session.request.call_count == 2

    @pytest.mark.parametrize(
        "index_status",
        ["INDEXING", "IN_PROGRESS", "PENDING", "PARTIAL", "NOT_INDEXED", "completed", "", None, 1],
    )
    def test_not_fully_indexed_listing_never_authorizes_create(self, monkeypatch, index_status):
        from podcaster import publish as pub

        session = self._session(
            _live_listing_payload([], total_items=0, total_pages=0, index_status=index_status)
        )
        with pytest.raises(pub.SpotifyDraftReconcileError, match="index is not complete"):
            self._reconcile(monkeypatch, session, create_id=4242)
        assert pub._create_episode.call_count == 0

    def test_later_page_not_fully_indexed_never_authorizes_create(self, monkeypatch):
        from podcaster import publish as pub

        first = [_live_draft_item(1000 + i, f"Other {i}") for i in range(50)]
        session = self._session(
            _live_listing_payload(first, total_items=51),
            _live_listing_payload(
                [_live_draft_item(2000, "More")],
                current_page=2,
                total_items=51,
                index_status="INDEXING",
            ),
        )
        with pytest.raises(pub.SpotifyDraftReconcileError, match="index is not complete"):
            self._reconcile(monkeypatch, session, create_id=4242)
        assert pub._create_episode.call_count == 0

    @pytest.mark.parametrize(
        ("pages", "match"),
        [
            pytest.param(
                [_live_listing_payload([], total_items=1, total_pages=0)],
                "pagination is inconsistent",
                id="zero-pages-but-items-counted",
            ),
            pytest.param(
                [
                    _live_listing_payload(
                        [_live_draft_item(1, "Other")], total_items=0, total_pages=0
                    )
                ],
                "pagination is inconsistent",
                id="zero-pages-but-items-returned",
            ),
            pytest.param(
                [
                    _live_listing_payload(
                        [_live_draft_item(1000 + i, "Other") for i in range(49)],
                        total_items=51,
                    )
                ],
                "pagination is inconsistent",
                id="short-first-page",
            ),
            pytest.param(
                [
                    _live_listing_payload(
                        [_live_draft_item(1000 + i, "Other") for i in range(50)],
                        total_items=51,
                    ),
                    _live_listing_payload([], current_page=2, total_items=51),
                ],
                "pagination is inconsistent",
                id="truncated-last-page",
            ),
            pytest.param(
                [
                    _live_listing_payload(
                        [_live_draft_item(1000 + i, "Other") for i in range(50)],
                        total_items=51,
                    ),
                    _live_listing_payload(
                        [_live_draft_item(2000 + i, "More") for i in range(50)],
                        current_page=2,
                        total_items=100,
                    ),
                ],
                "count metadata changed",
                id="count-changed-between-pages",
            ),
            pytest.param(
                [
                    _live_listing_payload(
                        [_live_draft_item(1000 + i, "Other") for i in range(50)],
                        total_items=51,
                    ),
                    _live_listing_payload(
                        [_live_draft_item(1000, "Other")], current_page=2, total_items=51
                    ),
                ],
                "repeated canonical episode id",
                id="repeated-item-across-pages",
            ),
        ],
    )
    def test_truncated_or_inconsistent_listing_never_authorizes_create(
        self, monkeypatch, pages, match
    ):
        from podcaster import publish as pub

        session = self._session(*pages)
        with pytest.raises(pub.SpotifyDraftReconcileError, match=match):
            self._reconcile(monkeypatch, session, create_id=4242)
        assert pub._create_episode.call_count == 0

    @pytest.mark.parametrize("sortable", [None, "TITLE", {"TITLE": True}, ["TITLE", 1]])
    def test_malformed_sortable_never_authorizes_create(self, monkeypatch, sortable):
        from podcaster import publish as pub

        payload = _live_listing_payload([], total_items=0, total_pages=0)
        payload["data"]["showByShowUri"]["episodesV2"]["sortable"] = sortable
        session = self._session(payload)
        with pytest.raises(pub.SpotifyDraftReconcileError, match="sortable"):
            self._reconcile(monkeypatch, session, create_id=4242)
        assert pub._create_episode.call_count == 0

    @pytest.mark.parametrize("extra", ["cursor", "hasMore", "nextPageToken", "totalCount"])
    def test_unknown_listing_field_alongside_sortable_fails_closed(self, monkeypatch, extra):
        from podcaster import publish as pub

        payload = _live_listing_payload([], total_items=0, total_pages=0)
        payload["data"]["showByShowUri"]["episodesV2"][extra] = None
        session = self._session(payload)
        with pytest.raises(pub.SpotifyDraftReconcileError, match="episodesV2 fields changed"):
            self._reconcile(monkeypatch, session, create_id=4242)
        assert pub._create_episode.call_count == 0

    @pytest.mark.parametrize("extra", ["cursor", "hasNextPage", "offset"])
    def test_unknown_pagination_field_fails_closed(self, monkeypatch, extra):
        from podcaster import publish as pub

        payload = _live_listing_payload([], total_items=0, total_pages=0)
        payload["data"]["showByShowUri"]["episodesV2"]["pagination"][extra] = None
        session = self._session(payload)
        with pytest.raises(pub.SpotifyDraftReconcileError, match="pagination fields changed"):
            self._reconcile(monkeypatch, session, create_id=4242)
        assert pub._create_episode.call_count == 0


class TestEpisodeDraftState:
    """#656 review: draft/published state must come from evidence, never truthiness.

    ``bool("false")`` is ``True``: truth-testing a JSON string would have made a
    *published* episode look like a draft and got it overwritten, and an
    unknown status token silently meant "not a draft", which creates a
    duplicate. Both are now schema drift and fail closed.
    """

    def _lookup(self, episode, **kwargs):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp([episode])
        return pub._find_existing_draft(
            session, "99", "My Show", user_id="7", show_id="show1", **kwargs
        )

    @pytest.mark.parametrize(
        ("label", "episode"),
        [
            ("isDraft string false", {"episodeId": 1, "title": "My Show", "isDraft": "false"}),
            ("isDraft string true", {"episodeId": 1, "title": "My Show", "isDraft": "true"}),
            ("isDraft empty string", {"episodeId": 1, "title": "My Show", "isDraft": ""}),
            ("isDraft numeric 1", {"episodeId": 1, "title": "My Show", "isDraft": 1}),
            ("isDraft numeric 0", {"episodeId": 1, "title": "My Show", "isDraft": 0}),
            ("isDraft float", {"episodeId": 1, "title": "My Show", "isDraft": 1.0}),
            ("isDraft object", {"episodeId": 1, "title": "My Show", "isDraft": {"v": True}}),
            ("isDraft list", {"episodeId": 1, "title": "My Show", "isDraft": []}),
            ("isPublished string", {"episodeId": 1, "title": "My Show", "isPublished": "false"}),
        ],
    )
    def test_non_boolean_boolean_state_fails_closed(self, label, episode):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup(episode)
        message = str(exc.value)
        assert "boolean" in message, label
        assert "duplicate draft" in message, label

    @pytest.mark.parametrize(
        "token",
        ["processing", "publishing", "archived", "error", "DRAFTED"],
    )
    def test_unknown_status_token_fails_closed(self, token):
        """An unrecognised state is an error, not an implied 'not a draft'."""
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup({"episodeId": 1, "title": "My Show", "status": token})
        message = str(exc.value)
        assert "unrecognised" in message
        assert "duplicate draft" in message

    def test_unknown_status_token_is_reported_for_diagnosis(self):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup({"episodeId": 1, "title": "My Show", "state": "processing"})
        assert "processing" in str(exc.value)
        assert "'state'" in str(exc.value)

    def test_unprintable_status_token_is_not_echoed(self):
        """A token that is not identifier-shaped may carry data — never echo it."""
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup({"episodeId": 1, "title": "My Show", "status": "user@example.com wins"})
        message = str(exc.value)
        assert "user@example.com" not in message
        assert "<unprintable>" in message

    @pytest.mark.parametrize(
        ("label", "episode"),
        [
            ("numeric status", {"episodeId": 1, "title": "My Show", "status": 3}),
            ("boolean status", {"episodeId": 1, "title": "My Show", "status": True}),
            ("object state", {"episodeId": 1, "title": "My Show", "state": {"name": "draft"}}),
            (
                "list publishStatus",
                {"episodeId": 1, "title": "My Show", "publishStatus": ["draft"]},
            ),
        ],
    )
    def test_non_string_status_fails_closed(self, label, episode):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup(episode)
        assert "duplicate draft" in str(exc.value), label

    @pytest.mark.parametrize(
        ("label", "episode"),
        [
            ("status published", {"episodeId": 1, "title": "My Show", "status": "published"}),
            ("state published", {"episodeId": 1, "title": "My Show", "state": "PUBLISHED"}),
            (
                "publishState padded",
                {"episodeId": 1, "title": "My Show", "publishState": " draft "},
            ),
            ("isDraft false", {"episodeId": 1, "title": "My Show", "isDraft": False}),
            ("isPublished true", {"episodeId": 1, "title": "My Show", "isPublished": True}),
        ],
    )
    def test_known_non_draft_states_are_understood(self, label, episode):
        """A known non-draft is a *match-free* answer, not an error."""
        expected = 1 if "draft" in str(episode.get("publishState", "")) else None
        assert self._lookup(episode) == expected, label

    @pytest.mark.parametrize(
        ("label", "episode"),
        [
            ("status draft", {"episodeId": 1, "title": "My Show", "status": "draft"}),
            ("isDraft true", {"episodeId": 1, "title": "My Show", "isDraft": True}),
            (
                "isPublished false corroborated by isDraft",
                {"episodeId": 1, "title": "My Show", "isPublished": False, "isDraft": True},
            ),
            (
                "isPublished false corroborated by status",
                {"episodeId": 1, "title": "My Show", "isPublished": False, "status": "draft"},
            ),
            (
                "agreeing fields",
                {"episodeId": 1, "title": "My Show", "isDraft": True, "status": "draft"},
            ),
            (
                "null isDraft with draft status",
                {"episodeId": 1, "title": "My Show", "isDraft": None, "status": "draft"},
            ),
        ],
    )
    def test_known_draft_states_match(self, label, episode):
        assert self._lookup(episode) == 1, label

    @pytest.mark.parametrize(
        ("label", "episode"),
        [
            ("status scheduled", {"episodeId": 1, "title": "My Show", "status": "scheduled"}),
            ("state unpublished", {"episodeId": 1, "title": "My Show", "state": "unpublished"}),
            ("isPublished false", {"episodeId": 1, "title": "My Show", "isPublished": False}),
        ],
    )
    def test_non_public_state_without_explicit_draft_evidence_fails_closed(self, label, episode):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup(episode)
        assert "not draft evidence" in str(exc.value), label

    def test_contradictory_state_fails_closed(self):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup(
                {"episodeId": 1, "title": "My Show", "isDraft": True, "status": "published"}
            )
        assert "contradictory" in str(exc.value)

    def test_null_only_state_fails_closed(self):
        """An explicit null carries no state, so the entry is not understood."""
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup({"episodeId": 1, "title": "My Show", "isDraft": None, "status": None})
        assert "no recognised draft/published state" in str(exc.value)

    def test_non_matching_titles_never_need_state(self):
        """Only the title-matching entry has to be classified."""
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp(
            [
                {"episodeId": 1, "title": "Another Show", "status": "unheard-of"},
                {"episodeId": 2, "title": "My Show", "status": "draft"},
            ]
        )
        assert pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1") == 2


class TestIsPublishedIsAsymmetricEvidence:
    """``isPublished: false`` is non-public, not reusable-draft evidence."""

    def _lookup(self, episode, **kwargs):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp([episode])
        return pub._find_existing_draft(
            session, "99", "My Show", user_id="7", show_id="show1", **kwargs
        )

    def test_is_published_false_alone_fails_closed(self):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError):
            self._lookup({"episodeId": 1, "title": "My Show", "isPublished": False})

    @pytest.mark.parametrize(
        "corroboration",
        [{"isDraft": True}, {"status": "draft"}, {"publishState": "draft"}],
    )
    def test_is_published_false_with_a_draft_signal_matches(self, corroboration):
        episode = {"episodeId": 1, "title": "My Show", "isPublished": False, **corroboration}
        assert self._lookup(episode) == 1

    def test_is_published_true_alone_establishes_non_draft(self):
        """``true`` needs no corroboration and is a clean match-free answer."""
        assert self._lookup({"episodeId": 1, "title": "My Show", "isPublished": True}) is None

    def test_is_published_true_contradicting_is_draft_fails_closed(self):
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._lookup({"episodeId": 1, "title": "My Show", "isPublished": True, "isDraft": True})
        assert "contradictory" in str(exc.value)

    def test_is_published_false_with_published_status_is_non_draft(self):
        assert (
            self._lookup(
                {"episodeId": 1, "title": "My Show", "isPublished": False, "status": "published"}
            )
            is None
        )


class TestExcludedEntriesAreSkippedBeforeClassification:
    """#656 review: the audio anchor's own state must never fail the lookup."""

    def _lookup(self, payload, **kwargs):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp(payload["episodes"])
        return pub._find_existing_draft(
            session, "99", "My Show", user_id="7", show_id="show1", **kwargs
        )

    @pytest.mark.parametrize(
        ("label", "excluded"),
        [
            (
                "scheduled audio anchor",
                {"episodeId": 555, "title": "My Show", "status": "scheduled"},
            ),
            ("published audio anchor", {"episodeId": 555, "title": "My Show", "isPublished": True}),
            ("state-less audio anchor", {"episodeId": 555, "title": "My Show"}),
            (
                "unpublished-only audio anchor",
                {"id": 555, "title": "My Show", "isPublished": False},
            ),
        ],
    )
    def test_excluded_entry_state_is_never_classified(self, label, excluded):
        payload = {"episodes": [excluded, {"episodeId": 888, "title": "My Show", "isDraft": True}]}
        assert self._lookup(payload, exclude_id=555) == 888, label

    def test_excluded_entry_alone_is_a_clean_no_match(self):
        payload = {"episodes": [{"episodeId": 555, "title": "My Show", "status": "scheduled"}]}
        assert self._lookup(payload, exclude_id=555) is None

    def test_a_non_excluded_scheduled_state_fails_closed(self):
        """Skipping is scoped to ``exclude_id``; other entries still need draft evidence."""

        payload = {"episodes": [{"episodeId": 777, "title": "My Show", "status": "scheduled"}]}
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError):
            self._lookup(payload, exclude_id=555)


class TestEpisodeAnchorId:
    """#656 review: id parsing considers every key and fails closed on conflict."""

    def _id(self, episode):
        from podcaster import publish as pub

        return pub._episode_anchor_id(episode)

    def test_no_id_keys_yields_none(self):
        assert self._id({"title": "My Show"}) is None

    @pytest.mark.parametrize(
        ("label", "episode"),
        [
            ("episodeId int", {"episodeId": 42}),
            ("episodeId numeric string", {"episodeId": "42"}),
            ("id fallback", {"id": 42}),
            ("anchorId fallback", {"anchorId": "42"}),
            ("null episodeId falls through", {"episodeId": None, "id": 42}),
            ("agreeing keys", {"episodeId": 42, "id": "42", "anchorId": 42.0}),
        ],
    )
    def test_single_agreed_id_is_returned(self, label, episode):
        assert self._id(episode) == 42, label

    @pytest.mark.parametrize(
        ("label", "episode"),
        [
            ("malformed episodeId hides valid id", {"episodeId": "abc", "id": 42}),
            ("malformed later key", {"episodeId": 42, "anchorId": "not-a-number"}),
            ("object id", {"id": {"value": 42}}),
            ("list id", {"id": [42]}),
            ("fractional float id", {"id": 42.5}),
            ("boolean alias hides valid id", {"episodeId": True, "anchorId": 42}),
        ],
    )
    def test_malformed_id_fields_fail_closed(self, label, episode):
        """A partial identity is never trusted — but every key is still read."""
        assert self._id(episode) is None, label

    def test_disagreeing_ids_fail_closed(self):
        assert self._id({"episodeId": 42, "id": 43}) is None

    def test_malformed_id_is_logged_not_silent(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="podcaster.publish"):
            assert self._id({"episodeId": "abc", "id": 42}) is None
        assert any("unreadable id field" in r.message for r in caplog.records)

    def test_title_match_with_unusable_id_fails_closed(self):
        """The fail-closed contract the callers rely on is unchanged."""
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_graphql_listing_resp(
            [{"episodeId": "abc", "id": 42, "title": "My Show", "isDraft": True}]
        )
        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            pub._find_existing_draft(session, "99", "My Show", user_id="7", show_id="show1")
        assert "exactly one unambiguous canonical episode id" in str(exc.value)


def _scripted_session(steps):
    """A session whose successive ``request()`` calls follow *steps*.

    Each step is a response mock, or an exception instance to raise. Returns
    the session and the live list of ``(method, url)`` calls, so tests can
    assert the *exact* number of state-mutating create POSTs.
    """
    calls: list[tuple[str, str]] = []
    remaining = list(steps)

    def _request(method, url, **kwargs):
        calls.append((method, url))
        if not remaining:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        step = remaining.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    session = MagicMock()
    session.request.side_effect = _request
    return session, calls


def _create_posts(calls):
    return [url for method, url in calls if method == "POST" and url.endswith("/episodes")]


class TestCreateEpisodeIsNeverRetriedBlindly:
    """#656 review: the create POST is state-mutating and has no idempotency key.

    A 408/429/5xx/timeout is indistinguishable from "the draft was created and
    the response was lost", so the generic retry (up to three POSTs) could
    orphan two extra *untitled* drafts that title-based reconcile can never
    find. Exactly one POST may leave this function.
    """

    @pytest.mark.parametrize(
        ("label", "step"),
        [
            ("timeout", requests.Timeout("timed out")),
            ("connection reset", requests.ConnectionError("reset")),
            ("http 408", _mock_error_resp(408, "request timeout")),
            ("http 429", _mock_error_resp(429, "slow down")),
            ("http 500", _mock_error_resp(500, "boom")),
            ("http 502", _mock_error_resp(502, "bad gateway")),
            ("http 503", _mock_error_resp(503, "unavailable")),
            ("http 504", _mock_error_resp(504, "gateway timeout")),
        ],
    )
    def test_transient_failure_sends_one_post_and_is_ambiguous(self, label, step):
        from podcaster import publish as pub

        session, calls = _scripted_session([step])
        with pytest.raises(pub.SpotifyDraftCreateAmbiguousError) as exc:
            pub._create_episode(session, "99")

        assert len(_create_posts(calls)) == 1, label
        assert "may or may not exist" in str(exc.value)
        assert "Not retrying blindly" in str(exc.value)

    def test_transient_failure_does_not_sleep(self, monkeypatch):
        """No backoff is spent on a request that will not be repeated."""
        from podcaster import publish as pub

        monkeypatch.setattr(
            pub.time, "sleep", lambda _s: pytest.fail("create must not back off and retry")
        )
        session, calls = _scripted_session([_mock_error_resp(503, "unavailable")])
        with pytest.raises(pub.SpotifyDraftCreateAmbiguousError):
            pub._create_episode(session, "99")
        assert len(_create_posts(calls)) == 1

    def test_deterministic_client_error_is_not_ambiguous(self):
        """A 400 provably created nothing — the caller may fail plainly."""
        from podcaster import publish as pub

        session, calls = _scripted_session([_mock_error_resp(400, "bad request")])
        with pytest.raises(pub.SpotifyPublishError) as exc:
            pub._create_episode(session, "99")

        assert not isinstance(exc.value, pub.SpotifyDraftCreateAmbiguousError)
        assert len(_create_posts(calls)) == 1

    def test_credential_expiry_still_propagates(self):
        from podcaster import publish as pub

        session, calls = _scripted_session([_mock_error_resp(401, "unauthorized")])
        with pytest.raises(pub.SpotifyCredentialExpiredError):
            pub._create_episode(session, "99")
        assert len(_create_posts(calls)) == 1

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("no id field", {"created": True}),
            ("null id", {"episodeId": None, "id": None}),
            ("non-numeric id", {"episodeId": "not-a-number"}),
            ("non-object body", ["ok"]),
        ],
    )
    def test_accepted_create_with_unreadable_body_is_ambiguous(self, label, payload):
        """The draft exists; only its id was lost. Retrying would duplicate it."""
        from podcaster import publish as pub

        session, calls = _scripted_session([_mock_json_resp(payload)])
        with pytest.raises(pub.SpotifyDraftCreateAmbiguousError) as exc:
            pub._create_episode(session, "99")
        assert "id is unknown" in str(exc.value)
        assert len(_create_posts(calls)) == 1

    def test_successful_create_is_unchanged(self):
        from podcaster import publish as pub

        session, calls = _scripted_session([_mock_json_resp({"episodeId": 777})])
        assert pub._create_episode(session, "99") == 777
        assert len(_create_posts(calls)) == 1

    def test_error_message_never_echoes_the_response_body(self):
        from podcaster import publish as pub

        session, _ = _scripted_session([_mock_error_resp(503, "token=sekret backend down")])
        with pytest.raises(pub.SpotifyDraftCreateAmbiguousError) as exc:
            pub._create_episode(session, "99")
        assert "sekret" not in str(exc.value)


class TestAmbiguousCreateRecovery:
    """#656 review: after an ambiguous create, act on evidence, never on a guess."""

    @pytest.fixture(autouse=True)
    def _no_settle_delay(self, monkeypatch):
        """The bounded settling delay is real behaviour; the wall clock is not."""
        from podcaster import publish as pub

        monkeypatch.setattr(pub, "_AMBIGUOUS_CREATE_SETTLE_SECONDS", 0.0)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")

    def _listing(self, *episodes, **extra):
        return _mock_graphql_listing_resp(list(episodes), **extra)

    def _reconcile(self, steps, **kwargs):
        from podcaster import publish as pub

        session, calls = _scripted_session(steps)
        kwargs.setdefault("user_id", "7")
        kwargs.setdefault("show_id", "show1")
        kwargs.setdefault("title", "My Show")
        return pub._reconcile_or_create_draft(session, "99", **kwargs), calls

    def test_new_untitled_draft_is_adopted_without_a_second_create(self):
        """The created draft is identified by the id that was not there before."""
        (anchor_id, created), calls = self._reconcile(
            [
                self._listing(),
                _mock_error_resp(504, "gateway timeout"),
                self._listing({"episodeId": 901, "title": None, "status": "draft"}),
            ]
        )
        assert (anchor_id, created) == (901, True)
        assert len(_create_posts(calls)) == 1

    def test_timeout_after_creation_is_recovered(self):
        """A lost response, not an error response — same evidence, same answer."""
        (anchor_id, created), calls = self._reconcile(
            [
                self._listing(),
                requests.Timeout("timed out"),
                self._listing({"episodeId": 901, "title": "", "isDraft": True}),
            ]
        )
        assert (anchor_id, created) == (901, True)
        assert len(_create_posts(calls)) == 1

    def test_pre_existing_untitled_orphans_are_not_adopted(self):
        """Only an id absent from the pre-create snapshot proves *this* create."""
        (anchor_id, _created), calls = self._reconcile(
            [
                self._listing({"episodeId": 800, "title": None, "status": "draft"}),
                _mock_error_resp(504, "gateway timeout"),
                self._listing(
                    {"episodeId": 800, "title": None, "status": "draft"},
                    {"episodeId": 901, "title": None, "status": "draft"},
                ),
            ]
        )
        assert anchor_id == 901
        assert len(_create_posts(calls)) == 1

    def test_titled_draft_appearing_after_the_failure_is_reused(self):
        """An already-titled draft is reused as-is and must not be re-titled."""
        (anchor_id, needs_title), calls = self._reconcile(
            [
                self._listing(),
                _mock_error_resp(504, "gateway timeout"),
                self._listing({"episodeId": 901, "title": "My Show", "status": "draft"}),
            ]
        )
        assert (anchor_id, needs_title) == (901, False)
        assert len(_create_posts(calls)) == 1

    def test_proven_non_creation_retries_exactly_once(self):
        """No new entry across two settled reads ⇒ the create did nothing."""
        (anchor_id, created), calls = self._reconcile(
            [
                self._listing(),
                _mock_error_resp(503, "unavailable"),
                self._listing(),
                self._listing(),
                _mock_json_resp({"episodeId": 902}),
            ]
        )
        assert (anchor_id, created) == (902, True)
        assert len(_create_posts(calls)) == 2

    def test_immediately_unchanged_listing_is_not_proof_on_its_own(self):
        """A draft that only shows up on the settled re-read is adopted, not duplicated."""
        (anchor_id, created), calls = self._reconcile(
            [
                self._listing(),
                requests.Timeout("timed out"),
                self._listing(),
                self._listing({"episodeId": 901, "title": None, "status": "draft"}),
            ]
        )
        assert (anchor_id, created) == (901, True)
        assert len(_create_posts(calls)) == 1

    def test_settling_re_read_can_surface_a_titled_draft(self):
        (anchor_id, needs_title), calls = self._reconcile(
            [
                self._listing(),
                requests.Timeout("timed out"),
                self._listing(),
                self._listing({"episodeId": 901, "title": "My Show", "isDraft": True}),
            ]
        )
        assert (anchor_id, needs_title) == (901, False)
        assert len(_create_posts(calls)) == 1

    def test_settling_read_is_bounded_to_one_extra_listing(self):
        """Exactly two listing reads before the second create — never a loop."""
        from podcaster import publish as pub

        session, calls = _scripted_session(
            [
                self._listing(),
                _mock_error_resp(503, "unavailable"),
                self._listing(),
                self._listing(),
                _mock_json_resp({"episodeId": 902}),
            ]
        )
        pub._reconcile_or_create_draft(session, "99", user_id="7", show_id="show1", title="My Show")
        listings = [url for _m, url in calls if url == pub._SPOTIFY_CREATORS_GRAPHQL_URL]
        assert len(listings) == pub._AMBIGUOUS_CREATE_READS + 1

    def test_ambiguous_evidence_skips_the_settling_read(self):
        """Settling cannot disambiguate two candidates — fail closed immediately."""
        from podcaster import publish as pub

        session, calls = _scripted_session(
            [
                self._listing(),
                _mock_error_resp(504, "gateway timeout"),
                self._listing(
                    {"episodeId": 901, "title": None, "status": "draft"},
                    {"episodeId": 902, "title": None, "status": "draft"},
                ),
            ]
        )
        with pytest.raises(pub.SpotifyDraftReconcileError):
            pub._reconcile_or_create_draft(
                session, "99", user_id="7", show_id="show1", title="My Show"
            )
        assert len([url for _m, url in calls if url == pub._SPOTIFY_CREATORS_GRAPHQL_URL]) == 2

    def test_second_create_is_never_retried_either(self):
        """Two ambiguous creates in a row stop at two POSTs, not four."""
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftCreateAmbiguousError):
            self._reconcile(
                [
                    self._listing(),
                    _mock_error_resp(503, "unavailable"),
                    self._listing(),
                    self._listing(),
                    _mock_error_resp(503, "unavailable"),
                ]
            )

    def test_second_create_post_count_is_capped_at_two(self):
        from podcaster import publish as pub

        session, calls = _scripted_session(
            [
                self._listing(),
                _mock_error_resp(503, "unavailable"),
                self._listing(),
                self._listing(),
                _mock_error_resp(503, "unavailable"),
            ]
        )
        with pytest.raises(pub.SpotifyPublishError):
            pub._reconcile_or_create_draft(
                session, "99", user_id="7", show_id="show1", title="My Show"
            )
        assert len(_create_posts(calls)) == 2

    def test_several_candidate_drafts_fail_closed(self):
        """A concurrent publisher makes the diff ambiguous — do not guess."""
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._reconcile(
                [
                    self._listing(),
                    _mock_error_resp(504, "gateway timeout"),
                    self._listing(
                        {"episodeId": 901, "title": None, "status": "draft"},
                        {"episodeId": 902, "title": None, "status": "draft"},
                    ),
                ]
            )
        message = str(exc.value)
        assert "901" in message and "902" in message
        assert "creator UI" in message

    def test_unclassifiable_new_entry_fails_closed(self):
        """An entry that might *be* the created draft is not evidence of absence."""
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyDraftReconcileError) as exc:
            self._reconcile(
                [
                    self._listing(),
                    _mock_error_resp(504, "gateway timeout"),
                    self._listing({"episodeId": 901, "title": None, "status": "who-knows"}),
                ]
            )
        assert "unclassifiable entries: 1" in str(exc.value)

    def test_incomplete_snapshot_blocks_create_before_retry(self):
        """A pre-create listing without every id cannot establish absence."""
        from podcaster import publish as pub

        session, calls = _scripted_session(
            [
                self._listing({"title": "Some other show", "status": "draft"}),
                _mock_error_resp(504, "gateway timeout"),
                self._listing({"title": "Some other show", "status": "draft"}),
            ]
        )
        with pytest.raises(
            pub.SpotifyDraftReconcileError,
            match="exactly one unambiguous canonical episode id",
        ):
            pub._reconcile_or_create_draft(
                session,
                "99",
                user_id="7",
                show_id="show1",
                title="My Show",
            )
        assert not _create_posts(calls)

    def test_unreadable_recovery_listing_fails_closed(self):
        from podcaster import publish as pub

        session, calls = _scripted_session(
            [
                self._listing(),
                _mock_error_resp(504, "gateway timeout"),
                _mock_error_resp(400, "still broken"),
            ]
        )
        with pytest.raises(pub.SpotifyDraftReconcileError):
            pub._reconcile_or_create_draft(
                session, "99", user_id="7", show_id="show1", title="My Show"
            )
        assert len(_create_posts(calls)) == 1

    def test_deterministic_create_failure_is_not_recovered(self):
        """A 400 created nothing, so no re-list and no second POST."""
        from podcaster import publish as pub

        session, calls = _scripted_session([self._listing(), _mock_error_resp(400, "bad request")])
        with pytest.raises(pub.SpotifyPublishError):
            pub._reconcile_or_create_draft(
                session, "99", user_id="7", show_id="show1", title="My Show"
            )
        assert len(_create_posts(calls)) == 1

    def test_reconciled_draft_skips_the_create_entirely(self):
        (anchor_id, created), calls = self._reconcile(
            [self._listing({"episodeId": 888, "title": "My Show", "status": "draft"})]
        )
        assert (anchor_id, created) == (888, False)
        assert _create_posts(calls) == []

    def test_audio_anchor_is_never_adopted_as_the_new_draft(self):
        """``exclude_id`` holds even when it is absent from the listing (#564).

        The audio anchor pre-dates this create, so it is not evidence that the
        create took effect: it must not be adopted, and the second POST is only
        sent because nothing else appeared.
        """
        (anchor_id, created), calls = self._reconcile(
            [
                self._listing(),
                _mock_error_resp(504, "gateway timeout"),
                self._listing({"episodeId": 555, "title": None, "status": "draft"}),
                self._listing({"episodeId": 555, "title": None, "status": "draft"}),
                _mock_json_resp({"episodeId": 902}),
            ],
            exclude_id=555,
        )
        assert (anchor_id, created) == (902, True)
        assert len(_create_posts(calls)) == 2


class TestAmbiguousCreateAtCallerLevel:
    """The video publish path must surface, not multiply, an ambiguous create."""

    def _video(self, tmp_path):
        video = tmp_path / "ep.mp4"
        video.write_bytes(b"video-bytes")
        return video

    def _env(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "true")

    def test_unrecoverable_ambiguous_create_fails_after_one_post(self, tmp_path, monkeypatch):
        import podcaster.publish as pub

        self._env(monkeypatch)
        session, calls = _scripted_session(
            [
                _mock_graphql_listing_resp(),
                _mock_error_resp(504, "gateway timeout"),
                _mock_graphql_listing_resp(
                    [
                        {"episodeId": 901, "title": None, "status": "draft"},
                        {"episodeId": 902, "title": None, "status": "draft"},
                    ]
                ),
            ]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        monkeypatch.setattr(pub, "_get_upload_url", lambda *a, **k: pytest.fail("must not upload"))

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "failed"
        assert len(_create_posts(calls)) == 1

    def test_recovered_draft_is_titled_before_upload(self, tmp_path, monkeypatch):
        """An adopted draft is untitled — it must be claimed like a created one."""
        import podcaster.publish as pub

        self._env(monkeypatch)
        session, calls = _scripted_session(
            [
                _mock_graphql_listing_resp(),
                requests.Timeout("timed out"),
                _mock_graphql_listing_resp([{"episodeId": 901, "title": None, "isDraft": True}]),
            ]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        order: list[str] = []
        monkeypatch.setattr(
            pub,
            "_set_metadata",
            lambda s, anchor_id, user_id, **kw: order.append(f"metadata:{anchor_id}"),
        )
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            lambda s, anchor_id, **kw: (
                order.append(f"upload_url:{anchor_id}"),
                ([{"partNumber": 1, "url": "https://gcs/part"}], "up1"),
            )[1],
        )
        monkeypatch.setattr(
            pub,
            "_upload_video_multipart",
            lambda s, parts, data: [{"partNumber": 1, "etag": "e1"}],
        )
        monkeypatch.setattr(pub, "_process_upload", lambda s, upload_id, **kw: None)

        result = pub.upload_video_to_episode(self._video(tmp_path), 555, title="My Show")

        assert result.status == "draft"
        assert result.anchor_episode_id == 901
        assert order == ["metadata:901", "upload_url:901", "metadata:901"]
        assert len(_create_posts(calls)) == 1

    def test_audio_path_create_is_also_single_shot(self, tmp_path, monkeypatch):
        """``publish_episode`` never reconciles, so it must not retry the create."""
        import podcaster.publish as pub

        self._env(monkeypatch)
        monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
        session, calls = _scripted_session([_mock_error_resp(503, "unavailable")])
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        mp3 = tmp_path / "ep.mp3"
        mp3.write_bytes(b"audio")
        config = SpotifyPublishConfig.from_payload({"spotify_publish": {"upload_format": "mp3"}})
        result = pub.publish_episode(
            mp3_path=mp3,
            title="My Show",
            description="d",
            spotify_publish_config=config,
        )

        assert result.status == "failed"
        assert len(_create_posts(calls)) == 1


class TestResolveLegacyIds:
    """#656: identity resolution must fail loudly instead of yielding blank ids."""

    def test_returns_string_ids(self):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp({"stationId": 99, "userId": 7})
        assert pub._resolve_legacy_ids(session, "show1") == ("99", "7")

    @pytest.mark.parametrize(
        "payload",
        [
            {"stationId": "99"},
            {"stationId": "99", "userId": None},
            {"stationId": "99", "userId": "   "},
        ],
    )
    def test_missing_user_id_raises(self, payload):
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp(payload)
        with pytest.raises(pub.SpotifyPublishError) as exc:
            pub._resolve_legacy_ids(session, "show1")
        assert "userId" in str(exc.value)

    def test_malformed_json_raises(self):
        from podcaster import publish as pub

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("not json")
        session = MagicMock()
        session.request.return_value = resp

        with pytest.raises(pub.SpotifyPublishError) as exc:
            pub._resolve_legacy_ids(session, "show1")
        assert "not valid JSON" in str(exc.value)

    @pytest.mark.parametrize(
        ("label", "raw"),
        [
            ("dict", {"id": 7}),
            ("list", [7]),
            ("tuple-ish nested", {"nested": {"id": 7}}),
            ("bool true", True),
            ("bool false", False),
            ("float", 7.0),
        ],
    )
    def test_non_scalar_identity_is_rejected(self, label, raw):
        """#656 review: only str/int may be interpolated into URLs and params."""
        from podcaster import publish as pub

        session = MagicMock()
        session.request.return_value = _mock_json_resp({"stationId": "99", "userId": raw})
        with pytest.raises(pub.SpotifyPublishError) as exc:
            pub._resolve_legacy_ids(session, "show1")
        assert "userId" in str(exc.value), label

    def test_non_scalar_identity_error_names_the_type_not_the_value(self):
        """The error must be type-safe: legacyIds payloads can carry account data."""
        from podcaster import publish as pub

        with pytest.raises(pub.SpotifyPublishError) as exc:
            pub._require_identity({"secretHandle": "sekret"}, "userId", "show1")
        message = str(exc.value)
        assert "dict" in message
        assert "sekret" not in message
        assert "secretHandle" not in message
        assert "non-scalar" in message

    def test_none_and_blank_stay_missing_not_non_scalar(self):
        from podcaster import publish as pub

        for raw in (None, "", "   "):
            with pytest.raises(pub.SpotifyPublishError) as exc:
                pub._require_identity(raw, "userId", "show1")
            assert "is missing userId" in str(exc.value)

    @pytest.mark.parametrize(("raw", "expected"), [(7, "7"), ("7", "7"), (" 7 ", "7")])
    def test_scalar_identities_are_accepted(self, raw, expected):
        from podcaster import publish as pub

        assert pub._require_identity(raw, "userId", "show1") == expected


class TestRetryRequestLogging:
    """#656 review: failure logs carry metadata only, never response bodies."""

    def test_final_failure_log_never_echoes_the_response_body(self, caplog):
        """Spotify error bodies can carry account data or tokens."""
        import logging

        from podcaster import publish as pub

        session, _calls = _scripted_session([_mock_error_resp(400, "token=sekret bad request")])
        with caplog.at_level(logging.ERROR, logger="podcaster.publish"):
            with pytest.raises(pub.SpotifyPublishError):
                pub._retry_request(session, "GET", "https://api-v5.anchor.fm/v3/x", max_attempts=1)
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "sekret" not in logged
        assert "bad request" not in logged
        assert "HTTP 400" in logged

    def test_forbidden_failure_logs_never_echo_auth_secrets(self, caplog):
        """A 403 body may repeat every credential presented by the browser."""
        import logging

        from podcaster import publish as pub

        sentinels = (
            "response-body-sentinel",
            "cookie-sentinel",
            "sp-dc-sentinel",
            "sp-key-sentinel",
            "authorization-sentinel",
        )
        body = " ".join(sentinels)
        session, _calls = _scripted_session([_mock_error_resp(403, body)])

        with caplog.at_level(logging.ERROR, logger="podcaster.publish"):
            with pytest.raises(pub.SpotifyPublishError):
                pub._retry_request(
                    session,
                    "GET",
                    "https://api-v5.anchor.fm/v3/x?token=response-body-sentinel",
                    max_attempts=1,
                    headers={
                        "Authorization": "Bearer authorization-sentinel",
                        "Cookie": "sp_dc=sp-dc-sentinel; sp_key=sp-key-sentinel",
                    },
                )

        logged = " ".join(record.getMessage() for record in caplog.records)
        assert "HTTP 403" in logged
        assert all(secret not in logged for secret in sentinels)


class TestClaimDraftTitleIsNonDestructive:
    """#656 review: the early title claim must never clear metadata."""

    @pytest.fixture(autouse=True)
    def _enable_verified_listing(self, monkeypatch):
        monkeypatch.setenv("PODCASTER_SPOTIFY_RECONCILE", "true")

    def test_claim_sends_the_real_metadata_not_an_empty_description(self, monkeypatch):
        from podcaster import publish as pub

        seen: dict[str, object] = {}
        monkeypatch.setattr(
            pub,
            "_set_metadata",
            lambda s, anchor_id, user_id, **kw: seen.update(kw),
        )
        pub._claim_draft_title(
            MagicMock(),
            901,
            user_id="7",
            title="My Show",
            description="real description",
            season_number=2,
            episode_number=5,
        )
        assert seen["title"] == "My Show"
        assert seen["description"] == "real description"
        assert seen["season_number"] == 2
        assert seen["episode_number"] == 5
        assert seen["publish_behavior"] == "draft"

    def test_video_path_claims_with_the_description_it_will_publish(self, tmp_path, monkeypatch):
        """End-to-end: the claim carries the caller's description, never ``""``."""
        from podcaster import publish as pub

        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        session, _calls = _scripted_session(
            [
                _mock_graphql_listing_resp(),
                _mock_json_resp({"episodeId": 901}),
            ]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        metadata_calls: list[dict] = []
        monkeypatch.setattr(
            pub,
            "_set_metadata",
            lambda s, anchor_id, user_id, **kw: metadata_calls.append(kw),
        )
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            lambda s, anchor_id, **kw: ([{"partNumber": 1, "url": "https://gcs/p"}], "up1"),
        )
        monkeypatch.setattr(
            pub,
            "_upload_video_multipart",
            lambda s, parts, data: [{"partNumber": 1, "etag": "e1"}],
        )
        monkeypatch.setattr(pub, "_process_upload", lambda s, upload_id, **kw: None)

        video = tmp_path / "ep.mp4"
        video.write_bytes(b"video-bytes")
        result = pub.upload_video_to_episode(
            video,
            555,
            title="My Show",
            description="real description",
            season_number=1,
            episode_number=4,
        )

        assert result.status == "draft"
        assert len(metadata_calls) == 2
        assert all(call["description"] == "real description" for call in metadata_calls)
        assert all(call["episode_number"] == 4 for call in metadata_calls)

    def test_already_titled_adopted_draft_is_never_re_claimed(self, tmp_path, monkeypatch):
        """A recovered draft that already carries the title keeps its metadata."""
        from podcaster import publish as pub

        monkeypatch.setattr(pub, "_AMBIGUOUS_CREATE_SETTLE_SECONDS", 0.0)
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show1")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")
        session, calls = _scripted_session(
            [
                _mock_graphql_listing_resp(),
                requests.Timeout("timed out"),
                _mock_graphql_listing_resp(
                    [{"episodeId": 901, "title": "My Show", "isDraft": True}]
                ),
            ]
        )
        monkeypatch.setattr(pub, "_build_session", lambda *a, **k: session)
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))

        metadata_calls: list[dict] = []
        monkeypatch.setattr(
            pub,
            "_set_metadata",
            lambda s, anchor_id, user_id, **kw: metadata_calls.append(kw),
        )
        monkeypatch.setattr(
            pub,
            "_get_upload_url",
            lambda s, anchor_id, **kw: ([{"partNumber": 1, "url": "https://gcs/p"}], "up1"),
        )
        monkeypatch.setattr(
            pub,
            "_upload_video_multipart",
            lambda s, parts, data: [{"partNumber": 1, "etag": "e1"}],
        )
        monkeypatch.setattr(pub, "_process_upload", lambda s, upload_id, **kw: None)

        video = tmp_path / "ep.mp4"
        video.write_bytes(b"video-bytes")
        result = pub.upload_video_to_episode(video, 555, title="My Show", description="desc")

        assert result.anchor_episode_id == 901
        # Only the final metadata call — the adopted draft is already titled.
        assert len(metadata_calls) == 1
        assert metadata_calls[0]["description"] == "desc"
        assert len(_create_posts(calls)) == 1


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
    """#364/#685: distinguish credential expiry from ordinary forbidden responses."""

    def _http_error(self, status_code):
        import requests

        resp = MagicMock()
        resp.status_code = status_code
        resp.text = "Unauthorized"
        return requests.HTTPError("auth", response=resp)

    def test_retry_request_401_raises_credential_expired_without_retry(self, monkeypatch):
        from podcaster import publish as pub

        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = self._http_error(401)
        session.request.return_value = resp

        monkeypatch.setattr(pub.time, "sleep", lambda *a, **k: None)
        with pytest.raises(pub.SpotifyCredentialExpiredError) as exc:
            pub._retry_request(session, "GET", "https://api-v5.anchor.fm/x")
        assert "401" in str(exc.value)
        # Must not retry on auth failure.
        assert session.request.call_count == 1

    def test_retry_request_generic_403_is_ordinary_http_failure_without_retry(self, monkeypatch):
        from podcaster import publish as pub

        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = self._http_error(403)
        session.request.return_value = resp

        monkeypatch.setattr(pub.time, "sleep", lambda *a, **k: None)
        with pytest.raises(pub.SpotifyPublishError) as exc:
            pub._retry_request(session, "GET", "https://api-v5.anchor.fm/x")
        assert not isinstance(exc.value, pub.SpotifyCredentialExpiredError)
        assert session.request.call_count == 1

    def test_retry_request_auth_check_403_is_credential_expired_without_retry(self, monkeypatch):
        from podcaster import publish as pub

        session = MagicMock()
        resp = MagicMock()
        resp.raise_for_status.side_effect = self._http_error(403)
        session.request.return_value = resp

        monkeypatch.setattr(pub.time, "sleep", lambda *a, **k: None)
        with pytest.raises(pub.SpotifyCredentialExpiredError) as exc:
            pub._retry_request(
                session,
                "GET",
                "https://api-v5.anchor.fm/v3/shows/show/legacyIds",
                request_context="auth_check",
            )
        assert "403" in str(exc.value)
        assert session.request.call_count == 1

    def test_draft_readback_403_returns_unknown_on_overview_route(self, caplog):
        import logging

        from podcaster import publish as pub

        secrets = (
            "readback-body-sentinel",
            "cookie-sentinel",
            "sp-dc-sentinel",
            "sp-key-sentinel",
            "authorization-sentinel",
        )
        session = MagicMock()
        session.headers = {
            "Authorization": "Bearer authorization-sentinel",
            "Cookie": "sp_dc=sp-dc-sentinel; sp_key=sp-key-sentinel",
        }
        session.request.return_value = _mock_error_resp(403, " ".join(secrets))

        with caplog.at_level(logging.INFO, logger="podcaster.publish"):
            state = pub._get_episode_publication_state(session, 12345, user_id="7")

        assert state is None
        session.request.assert_called_once()
        args, kwargs = session.request.call_args
        assert args == ("GET", "https://api-v5.anchor.fm/v3/episodes/12345/overview")
        assert kwargs["params"] == {"returnWebIds": "true", "isMumsCompatible": "true"}
        logged = " ".join(record.getMessage() for record in caplog.records)
        assert all(secret not in logged for secret in secrets)

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
