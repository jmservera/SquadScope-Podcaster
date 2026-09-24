"""Regression tests for #698: one resumable session (videos.insert) per upload.

YouTube creates the video resource as soon as a resumable session is opened.
Opening a second session for the same upload leaves an orphan zero-length
video (W39: ``6hYnqVnVRig`` next to the good ``uiP66QFgUfQ``). These tests use
a fake resumable endpoint and a sparse >128 MiB file.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError

import pytest

from podcaster.video import youtube as youtube_mod
from podcaster.video.distribution import (
    VideoDistributionConfig,
    YouTubeDeliveryError,
    distribute_video,
    upload_to_youtube,
)

_LARGE_SIZE = 128 * 1024 * 1024 + 3 * 1024 * 1024 + 17


class FakeResumableYouTube:
    """Fake YouTube upload endpoint that models resumable sessions.

    Every ``uploadType=resumable`` POST creates a video resource, exactly like
    the real API, so a duplicate init shows up as a second video.
    """

    def __init__(
        self,
        *,
        init_responses: list[object] | None = None,
        chunk_failures: dict[int, object] | None = None,
        single_put_failures: list[object] | None = None,
    ) -> None:
        self.init_posts: list[str] = []
        self.sessions: dict[str, dict] = {}
        self.put_urls: list[str] = []
        self.resume_probes: list[str] = []
        self._init_responses = list(init_responses or [])
        self._chunk_failures = dict(chunk_failures or {})
        self._single_put_failures = list(single_put_failures or [])
        self._chunk_calls = 0

    @property
    def videos(self) -> list[str]:
        return [s["video_id"] for s in self.sessions.values()]

    def request(self, url, *, method="GET", headers=None, data=None):
        if "oauth2.googleapis.com/token" in url:
            return 200, json.dumps({"access_token": "tok"}).encode()
        if method == "PUT":
            self.put_urls.append(url)
            session = self.sessions[url]
            if self._single_put_failures:
                failure = self._single_put_failures.pop(0)
                if isinstance(failure, BaseException):
                    raise failure
                return failure, b""
            session["received"] = len(data or b"")
            return 200, json.dumps({"id": session["video_id"]}).encode()
        raise AssertionError(f"unexpected request {method} {url}")

    def request_with_headers(self, url, *, method="GET", headers=None, data=None):
        if method == "POST" and "uploadType=resumable" in url:
            self.init_posts.append(url)
            if self._init_responses:
                response = self._init_responses.pop(0)
                if isinstance(response, BaseException):
                    raise response
                return response
            idx = len(self.sessions) + 1
            session_uri = f"https://upload.example/session-{idx}"
            self.sessions[session_uri] = {
                "video_id": f"vid-{idx}",
                "total": int(headers["X-Upload-Content-Length"]),
                "received": 0,
            }
            return 200, {"location": session_uri}, b""
        if method == "PUT":
            self.put_urls.append(url)
            session = self.sessions[url]
            content_range = headers["Content-Range"]
            if content_range.startswith("bytes */"):
                self.resume_probes.append(url)
                return self._offset_response(session)
            self._chunk_calls += 1
            failure = self._chunk_failures.pop(self._chunk_calls, None)
            if isinstance(failure, BaseException):
                raise failure
            if failure is not None:
                return failure, {}, b""
            start = int(content_range.split(" ", 1)[1].split("-", 1)[0])
            assert start == session["received"], "chunk must resume at acked offset"
            session["received"] = start + len(data)
            return self._offset_response(session)
        raise AssertionError(f"unexpected request {method} {url}")

    @staticmethod
    def _offset_response(session):
        if session["received"] >= session["total"]:
            return 200, {}, json.dumps({"id": session["video_id"]}).encode()
        if session["received"] == 0:
            return 308, {}, b""
        return 308, {"range": f"bytes=0-{session['received'] - 1}"}, b""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(youtube_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr("podcaster.video.distribution.time.sleep", lambda _s: None)


@pytest.fixture
def config() -> VideoDistributionConfig:
    return VideoDistributionConfig(
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


@pytest.fixture
def large_video(tmp_path: Path) -> Path:
    path = tmp_path / "large.mp4"
    with path.open("wb") as fh:
        fh.write(b"\x00" * 4096)
        fh.truncate(_LARGE_SIZE)  # sparse: > 128 MiB without real disk usage
    assert path.stat().st_size > 128 * 1024 * 1024
    return path


@pytest.fixture
def small_video(tmp_path: Path) -> Path:
    path = tmp_path / "small.mp4"
    path.write_bytes(b"\x00" * 4096)
    return path


def test_large_upload_opens_exactly_one_session(large_video, config):
    fake = FakeResumableYouTube()

    video_id, video_url = upload_to_youtube(
        large_video, "t", "d", config, transport=fake, raise_on_failure=True
    )

    assert video_id == "vid-1"
    assert video_url.endswith("vid-1")
    assert len(fake.init_posts) == 1
    assert fake.videos == ["vid-1"]
    assert set(fake.put_urls) == {"https://upload.example/session-1"}
    assert fake.sessions["https://upload.example/session-1"]["received"] == _LARGE_SIZE


@pytest.mark.parametrize(
    "failure",
    [503, ConnectionError("reset"), URLError("temporary failure")],
    ids=["http-503", "connection-reset", "url-error"],
)
def test_transient_chunk_failure_resumes_same_session(large_video, config, failure):
    fake = FakeResumableYouTube(chunk_failures={3: failure})

    video_id, _ = upload_to_youtube(
        large_video, "t", "d", config, transport=fake, raise_on_failure=True
    )

    assert video_id == "vid-1"
    assert len(fake.init_posts) == 1
    assert fake.videos == ["vid-1"]
    assert fake.resume_probes == ["https://upload.example/session-1"]
    assert set(fake.put_urls) == {"https://upload.example/session-1"}


def test_small_upload_put_retry_reuses_same_session(small_video, config):
    fake = FakeResumableYouTube(single_put_failures=[503, ConnectionError("reset")])

    video_id, _ = upload_to_youtube(
        small_video, "t", "d", config, transport=fake, raise_on_failure=True
    )

    assert video_id == "vid-1"
    assert len(fake.init_posts) == 1
    assert fake.put_urls == ["https://upload.example/session-1"] * 3


@pytest.mark.parametrize(
    ("init_response", "code", "retryable"),
    [
        (URLError("dns"), "youtube_upload_init_network_error", True),
        ((503, {}, b""), "youtube_upload_init_http_503", True),
        ((200, {}, b""), "youtube_upload_init_missing_session", False),
    ],
    ids=["network-error", "http-503", "missing-location"],
)
def test_init_failure_never_opens_a_second_session(
    large_video, config, init_response, code, retryable
):
    fake = FakeResumableYouTube(init_responses=[init_response])

    with pytest.raises(YouTubeDeliveryError) as raised:
        upload_to_youtube(large_video, "t", "d", config, transport=fake, raise_on_failure=True)

    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert len(fake.init_posts) == 1
    assert fake.put_urls == []


def test_init_failure_best_effort_mode_opens_one_session(large_video, config):
    fake = FakeResumableYouTube(init_responses=[(200, {}, b"")])

    assert upload_to_youtube(large_video, "t", "d", config, transport=fake) == (None, None)
    assert len(fake.init_posts) == 1
    assert fake.put_urls == []


def test_exhausted_chunk_retries_fail_without_reinit(large_video, config):
    fake = FakeResumableYouTube(chunk_failures={n: 503 for n in range(2, 20)})

    with pytest.raises(YouTubeDeliveryError) as raised:
        upload_to_youtube(large_video, "t", "d", config, transport=fake, raise_on_failure=True)

    assert raised.value.stage == "upload_chunked"
    assert raised.value.code == "youtube_chunked_http_503"
    assert raised.value.retryable is True
    assert len(fake.init_posts) == 1
    assert set(fake.put_urls) == {"https://upload.example/session-1"}


def test_distribute_records_one_video_and_second_run_does_not_insert(large_video, config):
    fake = FakeResumableYouTube()
    published: dict[str, dict] = {}

    first = distribute_video(
        large_video,
        "job-698",
        "t",
        "d",
        365.0,
        config,
        transport=fake,
        published=published,
        on_published=lambda platform, record: published.__setitem__(platform, record),
    )

    assert first.youtube_id == "vid-1"
    assert published["youtube"]["video_id"] == "vid-1"
    assert len(fake.init_posts) == 1

    second = distribute_video(
        large_video,
        "job-698",
        "t",
        "d",
        365.0,
        config,
        transport=fake,
        published=published,
    )

    assert second.youtube_id == "vid-1"
    assert len(fake.init_posts) == 1
    assert fake.videos == ["vid-1"]
