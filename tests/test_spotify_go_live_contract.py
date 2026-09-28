"""Spotify go-live contract (#688).

The legacy ``POST /v3/episodes/{id}/publish`` endpoint returns HTTP 404 (W37,
W38, W39). Episodes go live through ``POST /v3/episodes/{id}/update`` with
``isPublished: true`` — the W39 audio episode 126212203 went live at the
metadata ``/update`` call (``publishOn`` equals that call's timestamp) and the
following ``/publish`` 404 was a false failure. Provider ``/overview`` readback
is the only source of truth for the terminal state.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

import podcaster.publish as pub
from podcaster.config import SpotifyPublishConfig

BASE = "https://api-v5.anchor.fm"
VIDEO_ID = 126230829
AUDIO_ID = 126212203


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "SPOTIFY_PUBLISH_ENABLED",
        "SPOTIFY_PUBLISH_DRY_RUN",
        "SPOTIFY_ALLOW_LIVE_PUBLISH",
        "SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH",
        "SPOTIFY_VIDEO_PUBLISH_MODE",
        "SPOTIFY_SHOW_ID",
        "SP_DC",
        "SP_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _json(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    resp.headers = {}
    return resp


def _error(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = "error"
    resp.headers = {}
    resp.raise_for_status.side_effect = requests.HTTPError(f"{status} Error", response=resp)
    return resp


def _overview(*, published: bool, **overrides) -> dict:
    data = {
        "userId": 7,
        "title": "Show | W39",
        "description": "<p>Episode notes</p>",
        "podcastEpisodeType": "full",
        "podcastEpisodeIsExplicit": False,
        "podcastSeasonNumber": 2026,
        "podcastEpisodeNumber": 39,
        "isPublished": published,
        "isDraft": not published,
        "isDeleted": False,
    }
    data.update(overrides)
    return data


def _urls(session: MagicMock) -> list[tuple[str, str]]:
    return [(c.args[0], c.args[1]) for c in session.request.call_args_list]


class TestGoLiveMutation:
    def test_posts_update_with_is_published_and_provider_metadata(self):
        session = MagicMock()
        session.request.return_value = _json({})

        pub._publish_episode_live(session, VIDEO_ID, "7", _overview(published=False))

        session.request.assert_called_once()
        method, url = session.request.call_args.args
        kwargs = session.request.call_args.kwargs
        assert (method, url) == ("POST", f"{BASE}/v3/episodes/{VIDEO_ID}/update")
        assert kwargs["params"] == {"isMumsCompatible": "true"}
        assert kwargs["headers"] == pub._MUTATION_HEADERS
        assert kwargs["json"] == {
            "userId": 7,
            "title": "Show | W39",
            "description": "<p>Episode notes</p>",
            "episodeType": "full",
            "isPublished": True,
            "podcastEpisodeIsExplicit": False,
            "seasonNumber": 2026,
            "episodeNumber": 39,
        }

    def test_update_is_sent_exactly_once_even_on_retryable_error(self):
        session = MagicMock()
        session.request.return_value = _error(503)

        with pytest.raises(pub.SpotifyPublishError):
            pub._publish_episode_live(session, VIDEO_ID, "7", _overview(published=False))

        assert session.request.call_count == 1

    def test_omits_explicitly_null_season_and_episode_numbers(self):
        overview = _overview(published=False, podcastSeasonNumber=None, podcastEpisodeNumber=None)

        payload = pub._go_live_payload_from_overview(VIDEO_ID, "7", overview)

        assert "seasonNumber" not in payload
        assert "episodeNumber" not in payload

    @pytest.mark.parametrize("missing", ["podcastSeasonNumber", "podcastEpisodeNumber"])
    def test_absent_season_or_episode_key_fails_closed(self, missing):
        overview = _overview(published=False)
        del overview[missing]
        session = MagicMock()

        with pytest.raises(pub.SpotifyPublishError, match=missing):
            pub._publish_episode_live(session, VIDEO_ID, "7", overview)

        session.request.assert_not_called()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"title": ""},
            {"title": None},
            {"description": "  "},
            {"description": None},
            {"podcastEpisodeIsExplicit": None},
            {"podcastEpisodeType": ""},
            {"userId": 8},
            {"userId": True},
            {"userId": None},
            {"userId": "7"},
            {"podcastSeasonNumber": "2026"},
            {"podcastEpisodeNumber": True},
        ],
    )
    def test_unreadable_overview_fails_closed_before_mutation(self, overrides):
        session = MagicMock()

        with pytest.raises(pub.SpotifyPublishError):
            pub._publish_episode_live(
                session, VIDEO_ID, "7", _overview(published=False, **overrides)
            )

        session.request.assert_not_called()

    def test_absent_user_id_fails_closed_before_mutation(self):
        overview = _overview(published=False)
        del overview["userId"]
        session = MagicMock()

        with pytest.raises(pub.SpotifyPublishError, match="ownership"):
            pub._publish_episode_live(session, VIDEO_ID, "7", overview)

        session.request.assert_not_called()

    def test_missing_overview_fails_closed_before_mutation(self):
        session = MagicMock()

        with pytest.raises(pub.SpotifyPublishError):
            pub._publish_episode_live(session, VIDEO_ID, "7", None)

        session.request.assert_not_called()


class TestReadbackPublicationState:
    @pytest.mark.parametrize(
        ("episode", "expected"),
        [
            ({"isPublished": True, "isDraft": False}, True),
            ({"isPublished": True}, True),
            ({"status": "published"}, True),
            ({"isPublished": False, "isDraft": True}, False),
            ({"isPublished": False, "isDraft": False}, False),
            ({"isDraft": True}, False),
            ({"status": "scheduled"}, False),
            ({"isDraft": False}, None),
            ({"isPublished": True, "isDraft": True}, None),
            ({"isPublished": True, "status": "scheduled"}, None),
            ({"isPublished": "true"}, None),
            ({"isPublished": True, "isDeleted": True}, None),
            ({"status": "processing"}, None),
            ({}, None),
        ],
    )
    def test_requires_explicit_publication_evidence(self, episode, expected):
        assert pub._readback_publication_state(episode) is expected


class TestPromoteVideoDraftAgainstProvider:
    """Drives promote_spotify_video_draft through the real HTTP helpers."""

    def _run(self, monkeypatch, responses):
        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
        session = MagicMock()
        session.request.side_effect = responses
        monkeypatch.setattr(pub, "_get_credentials", lambda: ("show", "dc", "key"))
        monkeypatch.setattr(pub, "_build_session", MagicMock(return_value=session))
        monkeypatch.setattr(pub, "_resolve_legacy_ids", lambda s, sid: ("99", "7"))
        result = pub.promote_spotify_video_draft(
            VIDEO_ID,
            audio_anchor_id=AUDIO_ID,
            spotify_video_publish_mode="live",
            job_id="job-w39",
            run_id="run-w39",
        )
        return result, session

    def test_go_live_confirmed_by_overview_readback(self, monkeypatch):
        result, session = self._run(
            monkeypatch,
            [
                _json(_overview(published=False)),
                _json({}),
                _json(_overview(published=True)),
            ],
        )

        assert result.terminal_state == "published"
        assert result.outcome == "published"
        assert result.is_published is True
        assert result.details["confirmation_source"] == "spotify_episode_overview"
        assert _urls(session) == [
            ("GET", f"{BASE}/v3/episodes/{VIDEO_ID}/overview"),
            ("POST", f"{BASE}/v3/episodes/{VIDEO_ID}/update"),
            ("GET", f"{BASE}/v3/episodes/{VIDEO_ID}/overview"),
        ]
        assert session.request.call_args_list[1].kwargs["json"]["isPublished"] is True

    def test_never_calls_removed_publish_endpoint(self, monkeypatch):
        _result, session = self._run(
            monkeypatch,
            [
                _json(_overview(published=False)),
                _json({}),
                _json(_overview(published=True)),
            ],
        )

        assert not any("/publish" in url for _method, url in _urls(session))

    @pytest.mark.parametrize("status", [404, 500, 400])
    def test_false_failure_is_classified_published_from_readback(self, monkeypatch, status):
        result, session = self._run(
            monkeypatch,
            [
                _json(_overview(published=False)),
                _error(status),
                _json(_overview(published=True)),
            ],
        )

        assert result.terminal_state == "published"
        assert result.outcome == "published"
        assert result.is_published is True
        assert "mutation_error" in result.details
        posts = [u for m, u in _urls(session) if m == "POST"]
        assert posts == [f"{BASE}/v3/episodes/{VIDEO_ID}/update"]

    def test_transport_failure_is_classified_from_readback(self, monkeypatch):
        result, session = self._run(
            monkeypatch,
            [
                _json(_overview(published=False)),
                requests.ConnectionError("reset"),
                _json(_overview(published=True)),
            ],
        )

        assert result.terminal_state == "published"
        assert [m for m, _u in _urls(session)].count("POST") == 1

    def test_error_with_draft_readback_requires_manual_handoff(self, monkeypatch):
        result, session = self._run(
            monkeypatch,
            [
                _json(_overview(published=False)),
                _error(404),
                _json(_overview(published=False)),
            ],
        )

        assert result.terminal_state == "manual_handoff_required"
        assert result.is_published is False
        assert [m for m, _u in _urls(session)].count("POST") == 1

    def test_success_status_with_draft_readback_is_not_trusted(self, monkeypatch):
        result, _session = self._run(
            monkeypatch,
            [
                _json(_overview(published=False)),
                _json({}),
                _json(_overview(published=False)),
            ],
        )

        assert result.terminal_state == "manual_handoff_required"
        assert result.is_published is False

    def test_error_with_unreadable_readback_stays_unknown(self, monkeypatch):
        result, session = self._run(
            monkeypatch,
            [
                _json(_overview(published=False)),
                _error(404),
                _error(403),
            ],
        )

        assert result.terminal_state == "publication_state_unknown"
        assert result.outcome == "publication_unknown"
        assert result.is_published is None
        assert [m for m, _u in _urls(session)].count("POST") == 1

    def test_credential_expiry_after_mutation_stays_unknown(self, monkeypatch):
        result, _session = self._run(
            monkeypatch,
            [
                _json(_overview(published=False)),
                _json({}),
                _error(401),
            ],
        )

        assert result.terminal_state == "publication_state_unknown"
        assert result.details["credentials_expired"] is True

    def test_unreadable_metadata_fails_closed_without_mutation(self, monkeypatch):
        result, session = self._run(
            monkeypatch,
            [_json(_overview(published=False, description=""))],
        )

        assert result.terminal_state == "manual_handoff_required"
        assert [m for m, _u in _urls(session)] == ["GET"]

    def test_non_draft_non_public_readback_is_not_already_published(self, monkeypatch):
        result, session = self._run(
            monkeypatch,
            [_json(_overview(published=False, isDraft=False))],
        )

        assert result.terminal_state == "publication_state_unknown"
        assert result.details["reason"] == "not_explicit_draft"
        assert [m for m, _u in _urls(session)] == ["GET"]

    def test_non_draft_non_public_final_readback_is_not_published(self, monkeypatch):
        result, _session = self._run(
            monkeypatch,
            [
                _json(_overview(published=False)),
                _error(404),
                _json(_overview(published=False, isDraft=False)),
            ],
        )

        assert result.terminal_state == "manual_handoff_required"
        assert result.is_published is False

    @pytest.mark.parametrize(
        "wrap",
        [
            lambda ep: {"episode": ep},
            lambda ep: {"data": ep},
            lambda ep: {"episodes": [{"episodeId": 1, "isPublished": True}, ep]},
        ],
    )
    def test_wrapped_overview_shapes_promote_from_matched_entry(self, monkeypatch, wrap):
        def entry(published):
            return {"episodeId": VIDEO_ID, **_overview(published=published)}

        result, session = self._run(
            monkeypatch,
            [_json(wrap(entry(False))), _json({}), _json(wrap(entry(True)))],
        )

        assert result.terminal_state == "published"
        assert session.request.call_args_list[1].kwargs["json"]["title"] == "Show | W39"

    def test_already_published_sends_no_mutation(self, monkeypatch):
        result, session = self._run(monkeypatch, [_json(_overview(published=True))])

        assert result.terminal_state == "already_published"
        assert [m for m, _u in _urls(session)] == ["GET"]


_EMPTY_LISTING = {
    "data": {
        "showByShowUri": {
            "episodesV2": {
                "indexStatus": "COMPLETED",
                "items": [],
                "pagination": {"currentPage": 1, "pageSize": 50, "totalItems": 0, "totalPages": 1},
            }
        }
    }
}


class TestAudioGoLive:
    """publish_episode: the metadata ``/update`` IS the go-live call."""

    @pytest.fixture
    def env(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
        monkeypatch.setenv("SPOTIFY_ALLOW_LIVE_PUBLISH", "true")
        monkeypatch.setenv("SPOTIFY_SHOW_ID", "show")
        monkeypatch.setenv("SP_DC", "dc")
        monkeypatch.setenv("SP_KEY", "key")

    def _run(self, tmp_path, monkeypatch, metadata_resp, readback_resp):
        mp3 = tmp_path / "episode.mp3"
        mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)
        session = MagicMock()
        upload = MagicMock()
        upload.headers = {"ETag": '"e1"'}
        upload.raise_for_status = MagicMock()
        session.request.side_effect = [
            _json({"stationId": "1", "userId": "7"}),
            _json(_EMPTY_LISTING),  # #679 audio pre-create snapshot
            _json({"episodeId": AUDIO_ID}),
            _json({"signedUrl": "https://x.example/u", "uploadId": "up1"}),
            upload,
            _json({}),
            _json({"status": "completed"}),
            metadata_resp,
            readback_resp,
        ]
        monkeypatch.setattr(pub, "_build_session", MagicMock(return_value=session))
        result = pub.publish_episode(
            mp3,
            "Show | W39",
            "<p>notes</p>",
            spotify_publish_config=SpotifyPublishConfig(publish_mode="immediate"),
        )
        return result, session

    def test_immediate_publish_uses_update_and_readback_only(self, env, tmp_path, monkeypatch):
        result, session = self._run(
            tmp_path, monkeypatch, _json({}), _json(_overview(published=True))
        )

        assert result.status == "published"
        assert result.outcome == "published"
        urls = _urls(session)
        assert urls[-2] == ("POST", f"{BASE}/v3/episodes/{AUDIO_ID}/update")
        assert urls[-1] == ("GET", f"{BASE}/v3/episodes/{AUDIO_ID}/overview")
        assert session.request.call_args_list[-2].kwargs["json"]["isPublished"] is True
        assert not any("/publish" in url for _m, url in urls)

    def test_w39_false_404_is_reported_published(self, env, tmp_path, monkeypatch):
        result, session = self._run(
            tmp_path, monkeypatch, _error(404), _json(_overview(published=True))
        )

        assert result.status == "published"
        assert result.outcome == "published"
        assert result.error is None
        assert "ambiguous_go_live_response" in result.details
        urls = _urls(session)
        assert urls.count(("POST", f"{BASE}/v3/episodes/{AUDIO_ID}/update")) == 1
        assert not any("/publish" in url for _m, url in urls)

    def test_error_with_draft_readback_fails_as_uploaded(self, env, tmp_path, monkeypatch):
        result, _session = self._run(
            tmp_path, monkeypatch, _error(404), _json(_overview(published=False))
        )

        assert result.status == "failed"
        assert result.outcome == "uploaded"

    def test_error_with_unknown_readback_fails_unknown(self, env, tmp_path, monkeypatch):
        result, _session = self._run(tmp_path, monkeypatch, _error(404), _error(403))

        assert result.status == "failed"
        assert result.outcome == "publication_unknown"
        assert result.details["retry_blocked"] is True

    def test_success_with_draft_readback_requires_manual_handoff(self, env, tmp_path, monkeypatch):
        result, _session = self._run(
            tmp_path, monkeypatch, _json({}), _json(_overview(published=False))
        )

        assert result.outcome == "manual_handoff_required"
        assert result.status == "failed"
        assert "not confirmed" in result.error

    def test_success_with_unknown_readback_is_not_reported_published(
        self, env, tmp_path, monkeypatch
    ):
        result, _session = self._run(tmp_path, monkeypatch, _json({}), _error(403))

        assert result.status == "failed"
        assert result.outcome == "publication_unknown"
        assert result.details["retry_blocked"] is True
