"""Tests for the unlisted-draft -> manual publish workflow (#446)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from podcaster.video.youtube_publish import (
    DEFAULT_DRAFT_PRIVACY,
    PRIVACY_PRIVATE,
    PRIVACY_PUBLIC,
    PRIVACY_UNLISTED,
    VIDEOS_LIST_URL,
    PublishingPacket,
    approve_and_publish,
    build_publishing_packet,
    get_video_snippet,
    publish_video,
    verify_draft_ready,
)


class _FakeTransport:
    """Records the single request it receives and returns a queued status."""

    def __init__(self, status: int = 200, raises: bool = False):
        self._status = status
        self._raises = raises
        self.calls: list[dict] = []

    def request(self, url, *, method="GET", headers=None, data=None):
        self.calls.append({"url": url, "method": method, "headers": headers, "data": data})
        if self._raises:
            raise RuntimeError("boom")
        return self._status, b"{}"


# --- Packet / review gate ----------------------------------------------------


class TestPublishingPacket:
    def test_defaults_to_unlisted_draft_never_public(self):
        packet = build_publishing_packet("vid123", title="Ep")
        assert packet.draft_privacy == DEFAULT_DRAFT_PRIVACY == PRIVACY_UNLISTED
        assert packet.approved is False
        assert packet.is_public_ready is False

    def test_public_draft_privacy_rejected(self):
        with pytest.raises(ValueError):
            build_publishing_packet("vid123", draft_privacy=PRIVACY_PUBLIC)

    def test_missing_video_id_rejected(self):
        with pytest.raises(ValueError):
            PublishingPacket(video_id="")

    def test_review_url_defaulted(self):
        packet = build_publishing_packet("vid123")
        assert "vid123" in packet.review_url
        assert packet.review_url.startswith("https://studio.youtube.com")

    def test_approve_sets_gate(self):
        packet = build_publishing_packet("vid123")
        packet.approve("leela")
        assert packet.approved is True
        assert packet.approved_by == "leela"
        assert packet.is_public_ready is True

    def test_scheduled_flag_and_rfc3339(self):
        when = datetime(2025, 6, 30, 14, 0, 0, tzinfo=timezone.utc)
        packet = build_publishing_packet("vid123", scheduled_publish_at=when)
        assert packet.is_scheduled is True
        assert packet.scheduled_publish_at == "2025-06-30T14:00:00Z"

    def test_naive_datetime_treated_as_utc(self):
        when = datetime(2025, 6, 30, 14, 0, 0)
        packet = build_publishing_packet("vid123", scheduled_publish_at=when)
        assert packet.scheduled_publish_at == "2025-06-30T14:00:00Z"

    def test_to_json_roundtrip(self):
        packet = build_publishing_packet("vid123", title="Ep", locale="es")
        data = json.loads(packet.to_json())
        assert data["video_id"] == "vid123"
        assert data["locale"] == "es"
        assert data["approved"] is False


# --- publish_video -----------------------------------------------------------


class TestPublishVideo:
    def test_flips_to_public(self):
        t = _FakeTransport(status=200)
        res = publish_video("vid123", "tok", transport=t)
        assert res.succeeded is True
        assert res.privacy_status == PRIVACY_PUBLIC
        body = json.loads(t.calls[0]["data"])
        assert body == {
            "id": "vid123",
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }
        assert t.calls[0]["method"] == "PUT"
        assert t.calls[0]["headers"]["Authorization"] == "Bearer tok"

    def test_scheduled_publish_uses_private_plus_publishat(self):
        t = _FakeTransport(status=200)
        when = datetime(2025, 6, 30, 14, 0, 0, tzinfo=timezone.utc)
        res = publish_video("vid123", "tok", publish_at=when, transport=t)
        assert res.succeeded is True
        assert res.privacy_status == PRIVACY_PRIVATE
        assert res.scheduled_publish_at == "2025-06-30T14:00:00Z"
        body = json.loads(t.calls[0]["data"])
        assert body["status"]["privacyStatus"] == "private"
        assert body["status"]["publishAt"] == "2025-06-30T14:00:00Z"

    def test_http_error_returns_failed_result(self):
        t = _FakeTransport(status=403)
        res = publish_video("vid123", "tok", transport=t)
        assert res.succeeded is False
        assert "403" in res.error

    def test_transport_exception_returns_failed_result(self):
        t = _FakeTransport(raises=True)
        res = publish_video("vid123", "tok", transport=t)
        assert res.succeeded is False
        assert res.error

    def test_invalid_privacy_rejected(self):
        with pytest.raises(ValueError):
            publish_video("vid123", "tok", privacy_status="open", transport=_FakeTransport())

    def test_missing_video_id_rejected(self):
        with pytest.raises(ValueError):
            publish_video("", "tok", transport=_FakeTransport())

    def test_token_never_logged(self, caplog):
        t = _FakeTransport(status=200)
        with caplog.at_level("INFO"):
            publish_video("vid123", "supersecret-token", transport=t)
        assert "supersecret-token" not in caplog.text


# --- approve_and_publish gate ------------------------------------------------


class TestApproveAndPublish:
    def test_refuses_unapproved(self):
        t = _FakeTransport(status=200)
        packet = build_publishing_packet("vid123")
        res = approve_and_publish(packet, "tok", transport=t)
        assert res.succeeded is False
        assert "not approved" in res.error
        assert t.calls == []  # never called the API

    def test_approves_inline_then_publishes(self):
        t = _FakeTransport(status=200)
        packet = build_publishing_packet("vid123")
        res = approve_and_publish(packet, "tok", approved_by="leela", transport=t)
        assert res.succeeded is True
        assert packet.approved is True
        assert packet.approved_by == "leela"
        body = json.loads(t.calls[0]["data"])
        assert body["status"]["privacyStatus"] == "public"

    def test_pre_approved_publishes(self):
        t = _FakeTransport(status=200)
        packet = build_publishing_packet("vid123")
        packet.approve("amy")
        res = approve_and_publish(packet, "tok", transport=t)
        assert res.succeeded is True

    def test_scheduled_packet_schedules(self):
        t = _FakeTransport(status=200)
        when = datetime(2025, 6, 30, 14, 0, 0, tzinfo=timezone.utc)
        packet = build_publishing_packet("vid123", scheduled_publish_at=when)
        packet.approve("amy")
        res = approve_and_publish(packet, "tok", transport=t)
        assert res.succeeded is True
        assert res.scheduled_publish_at == "2025-06-30T14:00:00Z"
        body = json.loads(t.calls[0]["data"])
        assert body["status"]["privacyStatus"] == "private"
        assert body["status"]["publishAt"] == "2025-06-30T14:00:00Z"


# --- get_video_snippet and verify_draft_ready --------------------------------


class _SnippetTransport:
    """Fake transport that returns a videos.list response for a given video_id."""

    def __init__(self, *, title: str = "Episode", description: str = "Desc",
                 privacy: str = "unlisted", found: bool = True, status_code: int = 200):
        self.title = title
        self.description = description
        self.privacy = privacy
        self.found = found
        self.status_code = status_code
        self.calls: list[dict] = []

    def request(self, url, *, method="GET", headers=None, data=None):
        self.calls.append({"url": url, "method": method})
        if self.status_code != 200:
            return self.status_code, b"{}"
        if not self.found:
            return 200, json.dumps({"items": []}).encode()
        body = json.dumps({
            "items": [{
                "snippet": {"title": self.title, "description": self.description},
                "status": {"privacyStatus": self.privacy},
            }]
        }).encode()
        return 200, body


class _PlaylistTransport(_SnippetTransport):
    """Fake transport that also handles playlistItems.list (for verify_draft_ready)."""

    def __init__(self, *, playlist_contains: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.playlist_contains = playlist_contains

    def request(self, url, *, method="GET", headers=None, data=None):
        self.calls.append({"url": url, "method": method})
        if "playlistItems" in url:
            items = [{"id": "pi1"}] if self.playlist_contains else []
            return 200, json.dumps({"items": items}).encode()
        # videos.list
        if self.status_code != 200:
            return self.status_code, b"{}"
        if not self.found:
            return 200, json.dumps({"items": []}).encode()
        body = json.dumps({
            "items": [{
                "snippet": {"title": self.title, "description": self.description},
                "status": {"privacyStatus": self.privacy},
            }]
        }).encode()
        return 200, body


class TestGetVideoSnippet:
    def test_returns_combined_snippet_and_status(self):
        t = _SnippetTransport(title="W35", description="Agent skills.", privacy="unlisted")
        result = get_video_snippet("vid1", "tok", transport=t)
        assert result is not None
        assert result["title"] == "W35"
        assert result["description"] == "Agent skills."
        assert result["privacyStatus"] == "unlisted"

    def test_returns_none_when_not_found(self):
        t = _SnippetTransport(found=False)
        result = get_video_snippet("vid1", "tok", transport=t)
        assert result is None

    def test_returns_none_on_http_error(self):
        t = _SnippetTransport(status_code=403)
        result = get_video_snippet("vid1", "tok", transport=t)
        assert result is None

    def test_returns_none_on_transport_exception(self):
        class _Boom:
            def request(self, *a, **kw):
                raise RuntimeError("network down")

        result = get_video_snippet("vid1", "tok", transport=_Boom())
        assert result is None

    def test_uses_videos_list_url(self):
        t = _SnippetTransport()
        get_video_snippet("vid1", "tok", transport=t)
        assert t.calls[0]["url"].startswith(VIDEOS_LIST_URL)

    def test_token_not_in_url(self):
        t = _SnippetTransport()
        get_video_snippet("vid1", "secret-token", transport=t)
        assert "secret-token" not in t.calls[0]["url"]

    def test_missing_video_id_raises(self):
        with pytest.raises(ValueError):
            get_video_snippet("", "tok")


class TestVerifyDraftReady:
    def test_passes_on_good_draft(self):
        t = _PlaylistTransport(title="W35", description="Desc", privacy="unlisted",
                               playlist_contains=True)
        problems = verify_draft_ready("vid1", "tok", playlist_id="PL123", transport=t)
        assert problems == []

    def test_empty_title_is_a_problem(self):
        t = _PlaylistTransport(title="", description="Desc", playlist_contains=True)
        problems = verify_draft_ready("vid1", "tok", playlist_id="PL123", transport=t)
        assert any("title" in p for p in problems)

    def test_empty_description_is_a_problem(self):
        t = _PlaylistTransport(title="W35", description="", playlist_contains=True)
        problems = verify_draft_ready("vid1", "tok", playlist_id="PL123", transport=t)
        assert any("description" in p for p in problems)

    def test_already_public_is_a_problem(self):
        t = _PlaylistTransport(title="W35", description="Desc", privacy="public",
                               playlist_contains=True)
        problems = verify_draft_ready("vid1", "tok", playlist_id="PL123", transport=t)
        assert any("already public" in p for p in problems)

    def test_missing_playlist_membership_is_a_problem(self):
        t = _PlaylistTransport(title="W35", description="Desc", playlist_contains=False)
        problems = verify_draft_ready("vid1", "tok", playlist_id="PL123", transport=t)
        assert any("playlist" in p for p in problems)

    def test_no_playlist_id_skips_playlist_check(self):
        t = _SnippetTransport(title="W35", description="Desc", privacy="unlisted")
        problems = verify_draft_ready("vid1", "tok", transport=t)
        assert problems == []

    def test_metadata_read_failure_returns_single_problem(self):
        t = _SnippetTransport(found=False)
        problems = verify_draft_ready("vid1", "tok", transport=t)
        assert len(problems) == 1
        assert "metadata" in problems[0]
