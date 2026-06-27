"""Tests for YouTube playlist management (#449)."""

from __future__ import annotations

import json

import pytest

from podcaster.video.youtube_playlist import (
    add_to_show_playlist,
    add_video_to_playlist,
    playlist_contains_video,
    resolve_playlist_id,
)


class _FakeTransport:
    """Returns queued (status, body) tuples per call; records requests."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, url, *, method="GET", headers=None, data=None):
        self.calls.append({"url": url, "method": method, "headers": headers, "data": data})
        if not self._responses:
            raise AssertionError("unexpected extra request")
        status, body = self._responses.pop(0)
        if isinstance(status, Exception):
            raise status
        return status, body


class _Cfg:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# --- resolve_playlist_id -----------------------------------------------------


class TestResolvePlaylistId:
    def test_env_default_locale(self):
        env = {"VIDEO_YOUTUBE_PLAYLIST_ID": "PLen"}
        assert resolve_playlist_id(None, "en", env=env) == "PLen"

    def test_env_per_locale(self):
        env = {
            "VIDEO_YOUTUBE_PLAYLIST_ID": "PLen",
            "VIDEO_YOUTUBE_PLAYLIST_ID_ES": "PLes",
            "VIDEO_YOUTUBE_PLAYLIST_ID_FR": "PLfr",
        }
        assert resolve_playlist_id(None, "es-419", env=env) == "PLes"
        assert resolve_playlist_id(None, "fr-FR", env=env) == "PLfr"

    def test_config_mapping_wins(self):
        cfg = _Cfg(youtube_playlist_ids={"en": "PLcfg", "es": "PLcfges"})
        assert resolve_playlist_id(cfg, "en", env={}) == "PLcfg"
        assert resolve_playlist_id(cfg, "es", env={}) == "PLcfges"

    def test_config_single_attr_default_only(self):
        cfg = _Cfg(youtube_playlist_id="PLsingle")
        assert resolve_playlist_id(cfg, "en", env={}) == "PLsingle"
        # single attr does not apply to non-default locales
        assert resolve_playlist_id(cfg, "es", env={}) == ""

    def test_unknown_locale_falls_back_to_default(self):
        env = {"VIDEO_YOUTUBE_PLAYLIST_ID": "PLen"}
        assert resolve_playlist_id(None, "de", env=env) == "PLen"

    def test_missing_returns_empty(self):
        assert resolve_playlist_id(None, "en", env={}) == ""


# --- playlist_contains_video -------------------------------------------------


class TestContains:
    def test_true_when_items_present(self):
        t = _FakeTransport([(200, json.dumps({"items": [{"id": "x"}]}).encode())])
        assert playlist_contains_video("PL", "vid", "tok", transport=t) is True
        assert t.calls[0]["method"] == "GET"
        assert "playlistId=PL" in t.calls[0]["url"]
        assert "videoId=vid" in t.calls[0]["url"]

    def test_false_when_empty(self):
        t = _FakeTransport([(200, b'{"items": []}')])
        assert playlist_contains_video("PL", "vid", "tok", transport=t) is False

    def test_false_on_http_error(self):
        t = _FakeTransport([(404, b"{}")])
        assert playlist_contains_video("PL", "vid", "tok", transport=t) is False

    def test_false_on_transport_exception(self):
        t = _FakeTransport([(RuntimeError("boom"), b"")])
        assert playlist_contains_video("PL", "vid", "tok", transport=t) is False

    def test_false_on_blank_args(self):
        assert playlist_contains_video("", "vid", "tok", transport=_FakeTransport([])) is False
        assert playlist_contains_video("PL", "", "tok", transport=_FakeTransport([])) is False


# --- add_video_to_playlist ---------------------------------------------------


class TestAddVideo:
    def test_insert_success(self):
        t = _FakeTransport([(200, json.dumps({"id": "item1"}).encode())])
        res = add_video_to_playlist("PL", "vid", "tok", transport=t)
        assert res.succeeded is True
        assert res.playlist_item_id == "item1"
        body = json.loads(t.calls[0]["data"])
        assert body["snippet"]["playlistId"] == "PL"
        assert body["snippet"]["resourceId"] == {
            "kind": "youtube#video",
            "videoId": "vid",
        }
        assert t.calls[0]["method"] == "POST"

    def test_insert_with_position(self):
        t = _FakeTransport([(201, b'{"id": "item2"}')])
        res = add_video_to_playlist("PL", "vid", "tok", position=0, transport=t)
        assert res.succeeded is True
        body = json.loads(t.calls[0]["data"])
        assert body["snippet"]["position"] == 0

    def test_http_error_returns_failed(self):
        t = _FakeTransport([(403, b"{}")])
        res = add_video_to_playlist("PL", "vid", "tok", transport=t)
        assert res.succeeded is False
        assert "403" in res.error

    def test_transport_exception_returns_failed(self):
        t = _FakeTransport([(RuntimeError("boom"), b"")])
        res = add_video_to_playlist("PL", "vid", "tok", transport=t)
        assert res.succeeded is False
        assert res.error

    def test_blank_args_raise(self):
        with pytest.raises(ValueError):
            add_video_to_playlist("", "vid", "tok", transport=_FakeTransport([]))
        with pytest.raises(ValueError):
            add_video_to_playlist("PL", "", "tok", transport=_FakeTransport([]))

    def test_token_never_logged(self, caplog):
        t = _FakeTransport([(200, b'{"id": "item1"}')])
        with caplog.at_level("INFO"):
            add_video_to_playlist("PL", "vid", "supersecret", transport=t)
        assert "supersecret" not in caplog.text


# --- add_to_show_playlist (idempotent, locale-routed) ------------------------


class TestAddToShowPlaylist:
    def test_skips_when_no_playlist_configured(self, monkeypatch):
        monkeypatch.delenv("VIDEO_YOUTUBE_PLAYLIST_ID", raising=False)
        res = add_to_show_playlist(None, "en", "vid", "tok", transport=_FakeTransport([]))
        assert res.succeeded is True
        assert res.skipped is True
        assert res.playlist_id == ""

    def test_skips_when_already_present(self, monkeypatch):
        monkeypatch.setenv("VIDEO_YOUTUBE_PLAYLIST_ID", "PLen")
        t = _FakeTransport([(200, json.dumps({"items": [{"id": "x"}]}).encode())])
        res = add_to_show_playlist(None, "en", "vid", "tok", transport=t)
        assert res.succeeded is True
        assert res.skipped is True
        assert res.playlist_id == "PLen"
        assert len(t.calls) == 1  # only the list call, no insert

    def test_adds_when_absent(self, monkeypatch):
        monkeypatch.setenv("VIDEO_YOUTUBE_PLAYLIST_ID", "PLen")
        t = _FakeTransport(
            [
                (200, b'{"items": []}'),  # contains -> false
                (200, b'{"id": "item9"}'),  # insert
            ]
        )
        res = add_to_show_playlist(None, "en", "vid", "tok", transport=t)
        assert res.succeeded is True
        assert res.skipped is False
        assert res.playlist_item_id == "item9"
        assert len(t.calls) == 2

    def test_locale_routing(self, monkeypatch):
        monkeypatch.setenv("VIDEO_YOUTUBE_PLAYLIST_ID", "PLen")
        monkeypatch.setenv("VIDEO_YOUTUBE_PLAYLIST_ID_ES", "PLes")
        t = _FakeTransport([(200, b'{"items": []}'), (200, b'{"id": "i"}')])
        res = add_to_show_playlist(None, "es-419", "vid", "tok", transport=t)
        assert res.playlist_id == "PLes"

    def test_blank_video_raises(self):
        with pytest.raises(ValueError):
            add_to_show_playlist(None, "en", "", "tok", transport=_FakeTransport([]))
