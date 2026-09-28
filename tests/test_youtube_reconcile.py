"""Regression tests for #678: identity-bound YouTube upload reconciliation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from podcaster.publication_state import PUBLICATION_UNKNOWN, PublicationIdentity
from podcaster.video import job_runner
from podcaster.video.distribution import (
    VideoDistributionConfig,
    distribute_video,
    upload_to_youtube,
)
from podcaster.video.youtube_reconcile import (
    ABSENT,
    AMBIGUOUS,
    CONTRADICTORY,
    ERROR,
    MATCHED,
    is_identity_tag,
    reconcile_youtube_upload,
    tags_with_identity,
    youtube_identity_tag,
)

IDENTITY = PublicationIdentity(
    accepted_job_id="job-2026-W39-en",
    week="2026-W39",
    publish_run_id="run-abc",
    article_sha256="a" * 64,
    manifest_sha256="b" * 64,
)
TAG = youtube_identity_tag(IDENTITY)


class FakeYouTubeReadback:
    """Read-only fake of channels/playlistItems/videos; any mutation fails."""

    def __init__(self, videos: list[dict], *, channels: list[dict] | None = None, pages=None):
        self.videos = videos
        self.channels = (
            channels
            if channels is not None
            else [{"contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]
        )
        self.pages = pages
        self.calls: list[tuple[str, str]] = []
        self.failures: dict[str, int] = {}

    def _page(self, token: str | None) -> dict:
        if self.pages is not None:
            return self.pages[token]
        return {"items": [_item(v["id"], i) for i, v in enumerate(self.videos)]}

    def request(self, url, *, method="GET", headers=None, data=None):
        self.calls.append((method, url))
        if "oauth2.googleapis.com/token" in url:
            return 200, json.dumps({"access_token": "tok"}).encode()
        if method != "GET":
            raise AssertionError(f"reconcile must not mutate: {method} {url}")
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for fragment, status in self.failures.items():
            if fragment in parsed.path:
                return status, b"{}"
        if parsed.path.endswith("/channels"):
            assert query["mine"] == ["true"]
            return 200, json.dumps({"items": self.channels}).encode()
        if parsed.path.endswith("/playlistItems"):
            return 200, json.dumps(self._page(query.get("pageToken", [None])[0])).encode()
        if parsed.path.endswith("/videos"):
            wanted = query["id"][0].split(",")
            items = [v for v in self.videos if v["id"] in wanted]
            return 200, json.dumps({"items": items}).encode()
        raise AssertionError(f"unexpected request {url}")

    def request_with_headers(self, url, *, method="GET", headers=None, data=None):
        raise AssertionError(f"reconcile must not open an upload session: {method} {url}")


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _item(video_id: str, age_minutes: int) -> dict:
    added = (NOW - timedelta(minutes=age_minutes)).isoformat().replace("+00:00", "Z")
    return {"snippet": {"publishedAt": added}, "contentDetails": {"videoId": video_id}}


def _video(video_id: str, tags: list[str], *, privacy="unlisted", upload="processed") -> dict:
    return {
        "id": video_id,
        "snippet": {"title": "Same title", "tags": tags},
        "status": {"privacyStatus": privacy, "uploadStatus": upload},
    }


def _config(**overrides) -> VideoDistributionConfig:
    values = dict(
        youtube_enabled=True,
        youtube_client_id="cid",
        youtube_client_secret="csec",
        youtube_refresh_token="rtok",
        youtube_privacy="unlisted",
        spotify_rss_enabled=False,
        spotify_upload_enabled=False,
        blob_archive_enabled=False,
        dry_run=False,
    )
    values.update(overrides)
    return VideoDistributionConfig(**values)


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    path = tmp_path / "episode.mp4"
    path.write_bytes(b"\x00" * 4096)
    return path


def test_identity_tag_is_deterministic_and_bound_to_identity():
    assert TAG is not None and is_identity_tag(TAG)
    assert youtube_identity_tag(IDENTITY) == TAG
    for field in ("accepted_job_id", "week", "publish_run_id", "article_sha256"):
        changed = PublicationIdentity(**{**IDENTITY.__dict__, field: "other"})
        assert youtube_identity_tag(changed) != TAG
    assert "job-2026" not in TAG and "a" * 16 not in TAG
    assert youtube_identity_tag(None) is None


def test_tags_with_identity_keeps_tag_within_budget():
    tags = tags_with_identity(["x" * 60] * 12, TAG)
    assert tags[-1] == TAG
    assert sum(map(len, tags)) + len(tags) - 1 <= 500
    assert tags_with_identity(["podcast"], None) == ["podcast"]


def test_exact_single_match_binds_video():
    fake = FakeYouTubeReadback([_video("other", ["podcast"]), _video("vid-1", ["podcast", TAG])])
    result = reconcile_youtube_upload(TAG, "tok", fake)
    assert result.status == MATCHED and result.video_id == "vid-1"


def test_title_match_without_identity_tag_is_absent():
    fake = FakeYouTubeReadback([_video("vid-1", ["podcast"])])
    assert reconcile_youtube_upload(TAG, "tok", fake).status == ABSENT


def test_multiple_identity_matches_are_ambiguous():
    fake = FakeYouTubeReadback([_video("v1", [TAG]), _video("v2", [TAG])])
    result = reconcile_youtube_upload(TAG, "tok", fake)
    assert result.status == AMBIGUOUS and result.video_id is None


@pytest.mark.parametrize(
    ("video", "code"),
    [
        (_video("v1", [TAG], privacy="public"), "youtube_reconcile_unexpected_privacy"),
        (_video("v1", [TAG], upload="failed"), "youtube_reconcile_upload_incomplete"),
    ],
)
def test_contradictory_candidate_is_not_bound(video, code):
    result = reconcile_youtube_upload(TAG, "tok", FakeYouTubeReadback([video]))
    assert result.status == CONTRADICTORY and result.code == code and result.video_id is None


def test_repeated_items_across_pages_are_contradictory():
    item = _item("v1", 1)
    fake = FakeYouTubeReadback(
        [_video("v1", [TAG])],
        pages={None: {"items": [item], "nextPageToken": "p2"}, "p2": {"items": [item]}},
    )
    assert reconcile_youtube_upload(TAG, "tok", fake).status == CONTRADICTORY


def test_scan_stops_once_older_than_intent_window():
    fake = FakeYouTubeReadback(
        [_video("v1", []), _video("v2", [TAG])],
        pages={
            None: {"items": [_item("v1", 1)], "nextPageToken": "p2"},
            "p2": {"items": [_item("v2", 5), _item("old", 600)], "nextPageToken": "p3"},
        },
    )
    result = reconcile_youtube_upload(TAG, "tok", fake, not_before=NOW - timedelta(minutes=30))
    assert result.video_id == "v2"
    assert sum("/playlistItems" in url for _, url in fake.calls) == 2


def test_page_bound_without_exhaustion_is_contradictory():
    pages = {
        None if i == 0 else f"p{i}": {"items": [_item(f"v{i}", i)], "nextPageToken": f"p{i + 1}"}
        for i in range(6)
    }
    fake = FakeYouTubeReadback([_video("v0", [TAG])], pages=pages)
    result = reconcile_youtube_upload(TAG, "tok", fake, not_before=NOW - timedelta(days=1))
    assert result.status == CONTRADICTORY
    assert result.code == "youtube_reconcile_window_not_exhausted"
    assert reconcile_youtube_upload(TAG, "tok", fake).status == CONTRADICTORY


@pytest.mark.parametrize(
    ("page", "code"),
    [
        ({"items": [_item("v1", 5), _item("v2", 1)]}, "youtube_reconcile_unordered_uploads"),
        (
            {"items": [_item("v1", 1), _item("old", 600), _item("v2", 2)]},
            "youtube_reconcile_unordered_uploads",
        ),
        ({"items": [{"contentDetails": {"videoId": "v1"}}]}, "youtube_reconcile_malformed_item"),
        ({"items": [_item("v1", 1)], "nextPageToken": 7}, "youtube_reconcile_malformed_page_token"),
    ],
)
def test_unverifiable_listing_is_contradictory(page, code):
    fake = FakeYouTubeReadback([_video("v1", [TAG])], pages={None: page})
    result = reconcile_youtube_upload(TAG, "tok", fake, not_before=NOW - timedelta(minutes=30))
    assert result.status == CONTRADICTORY and result.code == code


def test_channel_and_readback_failures_fail_closed():
    fake = FakeYouTubeReadback([_video("v1", [TAG])], channels=[])
    assert reconcile_youtube_upload(TAG, "tok", fake).status == CONTRADICTORY
    fake = FakeYouTubeReadback([_video("v1", [TAG])])
    fake.failures["/videos"] = 503
    assert reconcile_youtube_upload(TAG, "tok", fake).status == ERROR
    assert reconcile_youtube_upload("not-a-tag", "tok", fake).status == ERROR


def _unknown_record(tag: str | None = TAG) -> dict:
    record = {"status": "published", "outcome": PUBLICATION_UNKNOWN, "publish_run_id": "run-abc"}
    if tag is not None:
        record["identity_tag"] = tag
        record["intent_at"] = (NOW - timedelta(hours=1)).isoformat()
    return record


def _distribute(video_file, fake, record, published_calls):
    return distribute_video(
        video_file,
        "job-2026-W39-en",
        "Same title",
        "desc",
        60.0,
        _config(),
        transport=fake,
        published={"youtube": record},
        on_published=lambda platform, rec: published_calls.append((platform, rec)),
        publish_run_id="run-abc",
        publication_identity_context=IDENTITY,
    )


def test_redelivery_reuses_exact_artifact_without_second_upload(video_file):
    fake = FakeYouTubeReadback([_video("vid-1", ["podcast", TAG])])
    published: list = []
    result = _distribute(video_file, fake, _unknown_record(), published)
    assert result.youtube_id == "vid-1"
    record = result.provider_records["youtube"]
    assert record["verification"] == "provider_readback"
    assert record["evidence_source"] == "youtube_identity_readback"
    assert record["retry_blocked"] is True
    assert published and published[0][0] == "youtube"
    assert published[0][1]["video_id"] == "vid-1"
    assert all(method == "GET" or "oauth2" in url for method, url in fake.calls)


@pytest.mark.parametrize(
    ("videos", "record"),
    [
        ([], _unknown_record()),
        ([_video("v1", [TAG]), _video("v2", [TAG])], _unknown_record()),
        ([_video("v1", [TAG])], _unknown_record(tag=None)),
        ([_video("v1", [TAG])], _unknown_record(tag="sqpub-" + "0" * 32)),
    ],
)
def test_unproven_identity_remains_publication_unknown(video_file, videos, record):
    fake = FakeYouTubeReadback(videos)
    published: list = []
    result = _distribute(video_file, fake, record, published)
    assert result.youtube_id is None
    assert result.provider_outcomes["youtube"] == PUBLICATION_UNKNOWN
    assert published == []


def test_upload_stamps_identity_tag(video_file):
    captured: dict = {}

    class UploadFake:
        def request(self, url, *, method="GET", headers=None, data=None):
            if "oauth2" in url:
                return 200, json.dumps({"access_token": "tok"}).encode()
            return 200, json.dumps({"id": "vid-new"}).encode()

        def request_with_headers(self, url, *, method="GET", headers=None, data=None):
            captured["metadata"] = json.loads(data)
            return 200, {"location": "https://upload.example/s"}, b""

    video_id, _ = upload_to_youtube(
        video_file, "t", "d", _config(), transport=UploadFake(), identity_tag=TAG
    )
    assert video_id == "vid-new"
    assert TAG in captured["metadata"]["snippet"]["tags"]
    assert captured["metadata"]["status"]["privacyStatus"] == "unlisted"


def test_upload_intent_declares_identity_before_mutation():
    details = job_runner._upload_intent_details("youtube", IDENTITY)
    assert details == {"youtube_identity_tag": TAG, "identity_scheme": "youtube-video-v1"}
    assert job_runner._upload_intent_details("spotify_rss", IDENTITY) is None
    assert job_runner._upload_intent_details("youtube", None) is None


def test_incomplete_videos_readback_is_contradictory():
    pages = {None: {"items": [_item("v1", 1), _item("v2", 2)]}}
    fake = FakeYouTubeReadback([_video("v1", [TAG])], pages=pages)
    result = reconcile_youtube_upload(TAG, "tok", fake)
    assert result.status == CONTRADICTORY
    assert result.code == "youtube_reconcile_incomplete_readback"
    assert result.video_id is None


class _TamperedVideosReadback(FakeYouTubeReadback):
    def __init__(self, videos, extra):
        super().__init__(videos)
        self.extra = extra

    def request(self, url, *, method="GET", headers=None, data=None):
        status, body = super().request(url, method=method, headers=headers, data=data)
        if urlparse(url).path.endswith("/videos"):
            payload = json.loads(body)
            payload["items"].extend(self.extra)
            body = json.dumps(payload).encode()
        return status, body


@pytest.mark.parametrize(
    "extra",
    [
        ["not-a-dict"],
        [{"snippet": {"tags": [TAG]}}],
        [_video("unrequested", [])],
        [_video("v1", [])],
    ],
)
def test_malformed_videos_readback_is_contradictory(extra):
    fake = _TamperedVideosReadback([_video("v1", [TAG])], extra)
    result = reconcile_youtube_upload(TAG, "tok", fake)
    assert result.status == CONTRADICTORY
    assert result.code == "youtube_reconcile_malformed_video"
    assert result.video_id is None


def test_reconcile_callback_failure_keeps_binding_without_second_upload(video_file):
    fake = FakeYouTubeReadback([_video("vid-1", [TAG])])

    def failing_callback(platform, record):
        raise RuntimeError("evidence store unavailable")

    result = distribute_video(
        video_file,
        "job-2026-W39-en",
        "Same title",
        "desc",
        60.0,
        _config(),
        transport=fake,
        published={"youtube": _unknown_record()},
        on_published=failing_callback,
        publish_run_id="run-abc",
        publication_identity_context=IDENTITY,
    )
    assert result.youtube_id == "vid-1"
    assert result.provider_records["youtube"]["retry_blocked"] is True
    assert any("YouTube reconcile evidence error" in err for err in result.errors)
    assert not any("evidence store unavailable" in err for err in result.errors)
    assert all(method == "GET" or "oauth2" in url for method, url in fake.calls)


def test_required_reconcile_callback_failure_fails_required_delivery(video_file):
    fake = FakeYouTubeReadback([_video("vid-1", [TAG])])

    def failing_callback(platform, record):
        raise RuntimeError("evidence store unavailable")

    result = distribute_video(
        video_file,
        "job-2026-W39-en",
        "Same title",
        "desc",
        60.0,
        _config(youtube_required=True),
        transport=fake,
        published={"youtube": _unknown_record()},
        on_published=failing_callback,
        publish_run_id="run-abc",
        publication_identity_context=IDENTITY,
    )
    assert result.youtube_id == "vid-1"
    assert result.youtube_required_failed is True
    assert result.status == "failed"
    assert result.youtube_failure_code == "youtube_reconcile_evidence_failed"
    assert result.youtube_failure_retryable is False
    assert result.provider_records["youtube"]["retry_blocked"] is True
    assert all(method == "GET" or "oauth2" in url for method, url in fake.calls)


def test_required_reconcile_success_completes_required_delivery(video_file):
    fake = FakeYouTubeReadback([_video("vid-1", [TAG])])
    published: list = []
    result = distribute_video(
        video_file,
        "job-2026-W39-en",
        "Same title",
        "desc",
        60.0,
        _config(youtube_required=True),
        transport=fake,
        published={"youtube": _unknown_record()},
        on_published=lambda platform, rec: published.append((platform, rec)),
        publish_run_id="run-abc",
        publication_identity_context=IDENTITY,
    )
    assert result.youtube_id == "vid-1"
    assert result.youtube_required_failed is False
    assert result.youtube_failure_code is None
    assert published and published[0][1]["video_id"] == "vid-1"


@pytest.mark.parametrize(
    ("pages", "code"),
    [
        (
            {None: {"items": [_item("v1", 1)], "nextPageToken": ""}},
            "youtube_reconcile_malformed_page_token",
        ),
        (
            {
                None: {"items": [_item("v1", 1)], "nextPageToken": "p2"},
                "p2": {"items": [_item("v2", 2)], "nextPageToken": "p2"},
            },
            "youtube_reconcile_malformed_page_token",
        ),
        (
            {
                None: {"items": [_item("v1", 1)], "nextPageToken": "p2"},
                "p2": {"items": [], "nextPageToken": "p3"},
                "p3": {"items": []},
            },
            "youtube_reconcile_empty_page_with_token",
        ),
    ],
)
def test_untrustworthy_continuation_is_contradictory(pages, code):
    fake = FakeYouTubeReadback([_video("v1", [TAG]), _video("v2", [])], pages=pages)
    result = reconcile_youtube_upload(TAG, "tok", fake)
    assert result.status == CONTRADICTORY
    assert result.code == code
    assert result.video_id is None


class _OAuthFailingReadback(FakeYouTubeReadback):
    def __init__(self, videos, status):
        super().__init__(videos)
        self.failure_status = status

    def request(self, url, *, method="GET", headers=None, data=None):
        if "oauth2.googleapis.com/token" in url:
            self.calls.append((method, url))
            return self.failure_status, b"{}"
        return super().request(url, method=method, headers=headers, data=data)


class _NetworkFailingReadback(FakeYouTubeReadback):
    def __init__(self, videos, exc):
        super().__init__(videos)
        self.exc = exc

    def request(self, url, *, method="GET", headers=None, data=None):
        if urlparse(url).path.endswith("/playlistItems"):
            self.calls.append((method, url))
            raise self.exc
        return super().request(url, method=method, headers=headers, data=data)


def _required_redelivery(video_file, fake):
    return distribute_video(
        video_file,
        "job-2026-W39-en",
        "Same title",
        "desc",
        60.0,
        _config(youtube_required=True),
        transport=fake,
        published={"youtube": _unknown_record()},
        on_published=lambda platform, rec: None,
        publish_run_id="run-abc",
        publication_identity_context=IDENTITY,
    )


def _failure_fields(result):
    return (
        result.youtube_failure_code,
        result.youtube_failure_stage,
        result.youtube_failure_retryable,
    )


def test_required_reconcile_transient_oauth_failure_stays_retryable(video_file):
    fake = _OAuthFailingReadback([_video("vid-1", [TAG])], 503)
    result = _required_redelivery(video_file, fake)
    assert result.youtube_id is None
    assert result.youtube_required_failed is True
    assert _failure_fields(result) == ("youtube_oauth_http_503", "oauth_token", True)
    assert result.youtube_failure_http_status == 503
    assert result.provider_outcomes["youtube"] == PUBLICATION_UNKNOWN
    assert all(method == "GET" or "oauth2" in url for method, url in fake.calls)


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(503, True), (429, True), (404, False)],
)
def test_required_reconcile_readback_http_failure_maps_retryability(video_file, status, retryable):
    fake = FakeYouTubeReadback([_video("vid-1", [TAG])])
    fake.failures["/videos"] = status
    result = _required_redelivery(video_file, fake)
    assert result.youtube_id is None
    assert result.youtube_required_failed is True
    assert _failure_fields(result) == (
        f"youtube_reconcile_videos_http_{status}",
        "reconcile",
        retryable,
    )
    assert result.youtube_failure_http_status == status
    assert all(method == "GET" or "oauth2" in url for method, url in fake.calls)


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (TimeoutError("slow"), "youtube_reconcile_network_error", True),
        (ValueError("bug"), "youtube_reconcile_exception", False),
    ],
)
def test_required_reconcile_transport_exception_maps_retryability(video_file, exc, code, retryable):
    fake = _NetworkFailingReadback([_video("vid-1", [TAG])], exc)
    result = _required_redelivery(video_file, fake)
    assert result.youtube_id is None
    assert result.youtube_required_failed is True
    assert _failure_fields(result) == (code, "reconcile", retryable)
    assert all(method == "GET" or "oauth2" in url for method, url in fake.calls)


def test_unrequired_reconcile_failure_does_not_fail_delivery(video_file):
    fake = FakeYouTubeReadback([_video("vid-1", [TAG])])
    fake.failures["/videos"] = 503
    result = _distribute(video_file, fake, _unknown_record(), [])
    assert result.youtube_id is None
    assert result.youtube_required_failed is False
    assert result.provider_outcomes["youtube"] == PUBLICATION_UNKNOWN
