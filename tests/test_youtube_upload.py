"""Tests for YouTube resumable chunked upload (#442)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from podcaster.video.distribution import VideoDistributionConfig
from podcaster.video.youtube import (
    RESUMABLE_CHUNK_SIZE,
    align_chunk_size,
    build_video_metadata,
    initiate_resumable_session,
    parse_range_end,
    upload_chunked,
    upload_video,
)

_GRANULE = 256 * 1024


# --- metadata builders --------------------------------------------------------


def test_build_video_metadata_defaults_to_unlisted_draft():
    meta = build_video_metadata("Title", "Desc")
    assert meta["status"]["privacyStatus"] == "unlisted"
    assert meta["status"]["selfDeclaredMadeForKids"] is False
    assert meta["snippet"]["categoryId"] == "28"
    assert meta["snippet"]["tags"]


def test_build_video_metadata_truncates_and_overrides():
    meta = build_video_metadata(
        "x" * 200, "y" * 6000, tags=["a"], category_id="22", privacy_status="private"
    )
    assert len(meta["snippet"]["title"]) == 100
    assert len(meta["snippet"]["description"]) == 5000
    assert meta["snippet"]["tags"] == ["a"]
    assert meta["status"]["privacyStatus"] == "private"


# --- chunk helpers ------------------------------------------------------------


def test_align_chunk_size_rounds_to_granule():
    assert align_chunk_size(_GRANULE) == _GRANULE
    assert align_chunk_size(_GRANULE + 5) == _GRANULE
    assert align_chunk_size(3 * _GRANULE + 10) == 3 * _GRANULE
    assert align_chunk_size(100) == _GRANULE  # minimum one granule
    assert RESUMABLE_CHUNK_SIZE % _GRANULE == 0


def test_parse_range_end():
    assert parse_range_end("bytes=0-262143") == 262143
    assert parse_range_end("0-100") == 100
    assert parse_range_end(None) is None
    assert parse_range_end("garbage") is None


# --- fake transport -----------------------------------------------------------


@dataclass
class _FakeTransport:
    """Simulates the YouTube resumable endpoint, chunk by chunk."""

    total: int
    chunk: int
    session_uri: str = "https://upload.example/session-123"
    video_id: str = "vid-OK"
    fail_at_offset: int | None = None  # inject one transient 503 at this offset
    received: int = 0
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    _failed_once: bool = False

    def request_with_headers(self, url, *, method="GET", headers=None, data=None):
        headers = headers or {}
        self.requests.append((method, headers))

        # Initiate resumable session.
        if "uploadType=resumable" in url:
            return 200, {"location": self.session_uri}, b""

        crange = headers.get("Content-Range", "")

        # Status query: "bytes */{total}".
        if crange == f"bytes */{self.total}":
            if self.received >= self.total:
                return 200, {}, json.dumps({"id": self.video_id}).encode()
            # Only include Range header when at least one byte has been received;
            # "bytes=0--1" is invalid per the resumable-upload spec.
            if self.received > 0:
                return 308, {"range": f"bytes=0-{self.received - 1}"}, b""
            return 308, {}, b""

        # A data chunk: "bytes {start}-{end}/{total}".
        prefix, rng = crange.split(" ", 1)
        span, total = rng.split("/")
        start_s, end_s = span.split("-")
        start, end = int(start_s), int(end_s)

        # Inject a single transient failure at a given offset.
        if (
            self.fail_at_offset is not None
            and start == self.fail_at_offset
            and not self._failed_once
        ):
            self._failed_once = True
            return 503, {}, b""

        self.received = end + 1
        if self.received >= self.total:
            return 200, {}, json.dumps({"id": self.video_id}).encode()
        return 308, {"range": f"bytes=0-{end}"}, b""

    def request(self, *a, **k):  # pragma: no cover - unused
        raise AssertionError("request() should not be called")


def _make_file(tmp_path: Path, size: int) -> Path:
    p = tmp_path / "video.mp4"
    p.write_bytes(b"\x00" * size)
    return p


# --- initiate -----------------------------------------------------------------


def test_initiate_resumable_session_returns_uri():
    t = _FakeTransport(total=1000, chunk=512)
    uri = initiate_resumable_session(t, "tok", {"snippet": {}}, file_size=1000)
    assert uri == t.session_uri


def test_initiate_resumable_session_raises_without_location():
    class _NoLoc:
        def request_with_headers(self, *a, **k):
            return 200, {}, b""

    with pytest.raises(RuntimeError, match="no session URI"):
        initiate_resumable_session(_NoLoc(), "tok", {}, file_size=10)


# --- chunked upload happy path ------------------------------------------------


def test_upload_chunked_multiple_chunks(tmp_path):
    total = 5 * _GRANULE  # 5 chunks at 1-granule chunk size
    t = _FakeTransport(total=total, chunk=_GRANULE)
    path = _make_file(tmp_path, total)
    result = upload_chunked(
        t, t.session_uri, "tok", path, total, chunk_size=_GRANULE, sleep=lambda s: None
    )
    assert result.succeeded
    assert result.status == "completed"
    assert result.video_id == "vid-OK"
    assert result.bytes_uploaded == total
    # 5 PUT chunk requests issued (init not counted here).
    assert len([r for r in t.requests if r[0] == "PUT"]) == 5


def test_upload_chunked_resumes_after_transient_failure(tmp_path):
    total = 4 * _GRANULE
    # Fail once when the chunk starting at offset 2*GRANULE is first attempted.
    t = _FakeTransport(total=total, chunk=_GRANULE, fail_at_offset=2 * _GRANULE)
    path = _make_file(tmp_path, total)
    result = upload_chunked(
        t, t.session_uri, "tok", path, total, chunk_size=_GRANULE, sleep=lambda s: None
    )
    assert result.succeeded
    assert result.bytes_uploaded == total
    # The status-query ("bytes */total") must have been used to resume.
    assert any(r[1].get("Content-Range") == f"bytes */{total}" for r in t.requests)


def test_upload_chunked_308_without_range_header_re_queries_offset(tmp_path):
    """A 308 with no Range header must re-query the server offset, not advance blindly."""

    total = 3 * _GRANULE
    path = _make_file(tmp_path, total)

    class _NoRangeTransport:
        """Returns 308 without Range on the first chunk, then normal 308/200."""

        def __init__(self):
            self.received = 0
            self.calls = 0
            self.session_uri = "https://upload.example/no-range-session"

        def request_with_headers(self, url, *, method="GET", headers=None, data=None):
            headers = headers or {}
            crange = headers.get("Content-Range", "")

            if "uploadType=resumable" in url:
                return 200, {"location": self.session_uri}, b""

            # Status query
            if crange == f"bytes */{total}":
                if self.received > 0:
                    return 308, {"range": f"bytes=0-{self.received - 1}"}, b""
                return 308, {}, b""

            # Data chunk
            span = crange.split(" ", 1)[1].split("/")[0]
            start, end = (int(x) for x in span.split("-"))
            self.calls += 1

            # First chunk: return 308 WITHOUT a Range header
            if self.calls == 1:
                return 308, {}, b""

            # Subsequent chunks: accept normally
            self.received = end + 1
            if self.received >= total:
                import json as _json

                return 200, {}, _json.dumps({"id": "vid-norange"}).encode()
            return 308, {"range": f"bytes=0-{end}"}, b""

    t = _NoRangeTransport()
    result = upload_chunked(
        t, t.session_uri, "tok", path, total, chunk_size=_GRANULE, sleep=lambda s: None
    )
    # Upload must still complete successfully; the missing-Range 308 must not
    # cause bytes to be silently skipped.
    assert result.succeeded
    assert result.video_id == "vid-norange"
    assert result.bytes_uploaded == total


def test_upload_chunked_non_retryable_fails(tmp_path):
    class _Forbidden:
        def request_with_headers(self, url, *, method="GET", headers=None, data=None):
            return 403, {}, b""

    path = _make_file(tmp_path, _GRANULE)
    result = upload_chunked(
        _Forbidden(), "uri", "tok", path, _GRANULE, chunk_size=_GRANULE, sleep=lambda s: None
    )
    assert result.status == "failed"
    assert "403" in result.error


# --- upload_video top-level ---------------------------------------------------


def _config(**kw) -> VideoDistributionConfig:
    base = dict(
        youtube_enabled=True,
        youtube_client_id="cid",
        youtube_client_secret="sec",
        youtube_refresh_token="rt",
    )
    base.update(kw)
    return VideoDistributionConfig(**base)


def test_upload_video_disabled():
    res = upload_video(Path("/nope.mp4"), "t", "d", _config(youtube_enabled=False))
    assert res.status == "disabled"


def test_upload_video_dry_run():
    res = upload_video(Path("/nope.mp4"), "t", "d", _config(dry_run=True))
    assert res.status == "dry_run"
    assert res.video_id == "dry-run-id"


def test_upload_video_too_small(tmp_path):
    small = tmp_path / "tiny.mp4"
    small.write_bytes(b"\x00" * 10)
    with pytest.raises(ValueError, match="too small"):
        upload_video(small, "t", "d", _config())


def test_upload_video_full_flow(tmp_path, monkeypatch):
    total = 3 * _GRANULE
    path = _make_file(tmp_path, total)
    t = _FakeTransport(total=total, chunk=_GRANULE)

    monkeypatch.setattr("podcaster.video.youtube._get_youtube_access_token", lambda c, h: "tok")
    res = upload_video(
        path,
        "Title",
        "Desc",
        _config(),
        transport=t,
        chunk_size=_GRANULE,
        sleep=lambda s: None,
    )
    assert res.succeeded
    assert res.video_id == "vid-OK"
    assert res.bytes_uploaded == total
