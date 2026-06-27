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
    PublishingPacket,
    approve_and_publish,
    build_publishing_packet,
    publish_video,
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
