"""Tests for podcaster.failure_reporting (#237)."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

from podcaster.failure_reporting import (
    FAILURE_LABEL,
    _build_comment_body,
    _build_issue_body,
    _find_open_issue,
    report_failure,
)


class FakeResponse:
    """Minimal HTTP response context-manager for transport injection."""

    def __init__(self, body: Any, status: int = 200):
        self._data = json.dumps(body).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _make_transport(responses: list[Any]):
    """Return a transport callable that yields *responses* in order."""
    calls: list[dict] = []
    idx = [0]

    def transport(request):
        i = idx[0]
        idx[0] += 1
        calls.append({
            "url": request.full_url,
            "method": request.method,
            "body": json.loads(request.data.decode()) if request.data else None,
            "headers": dict(request.headers),
        })
        return FakeResponse(responses[i])

    return transport, calls


# ---------------------------------------------------------------------------
# _build_issue_body
# ---------------------------------------------------------------------------


def test_build_issue_body_contains_key_fields():
    body = _build_issue_body("synth", "RuntimeError", "kaboom", "2026-06-01T00:00:00Z")
    assert "synth" in body
    assert "RuntimeError" in body
    assert "kaboom" in body
    assert "2026-06-01T00:00:00Z" in body


def test_build_issue_body_with_details():
    body = _build_issue_body("api", "Err", "msg", "ts", details={"job_id": "j1"})
    assert "j1" in body


def test_build_issue_body_truncates_long_messages():
    long_msg = "x" * 3000
    body = _build_issue_body("c", "E", long_msg, "t")
    # Message should be truncated to 2000 chars
    assert len(long_msg) > 2000
    assert "x" * 2000 in body
    assert "x" * 2001 not in body


# ---------------------------------------------------------------------------
# _build_comment_body
# ---------------------------------------------------------------------------


def test_build_comment_body_contains_fields():
    body = _build_comment_body("Err", "boom", "2026-06-01T00:00:00Z")
    assert "Err" in body
    assert "boom" in body
    assert "Recurrence" in body


# ---------------------------------------------------------------------------
# _find_open_issue
# ---------------------------------------------------------------------------


def test_find_open_issue_returns_number_on_match():
    issues = [
        {"number": 42, "title": "[ACA failure] synth: RuntimeError"},
        {"number": 10, "title": "[ACA failure] api: SomeError"},
    ]
    transport, calls = _make_transport([issues])
    result = _find_open_issue("synth", "tok", "owner/repo", transport=transport)
    assert result == 42
    assert "labels=aca-failure" in calls[0]["url"]


def test_find_open_issue_returns_none_when_no_match():
    issues = [{"number": 10, "title": "[ACA failure] api: SomeError"}]
    transport, _ = _make_transport([issues])
    result = _find_open_issue("synth", "tok", "owner/repo", transport=transport)
    assert result is None


def test_find_open_issue_returns_none_on_empty():
    transport, _ = _make_transport([[]])
    result = _find_open_issue("synth", "tok", "owner/repo", transport=transport)
    assert result is None


# ---------------------------------------------------------------------------
# report_failure — no token
# ---------------------------------------------------------------------------


def test_report_failure_skips_without_token():
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("GITHUB_TOKEN", None)
        result = report_failure(container="synth", error_type="Err", error_message="msg")
    assert result is None


# ---------------------------------------------------------------------------
# report_failure — creates new issue
# ---------------------------------------------------------------------------


def test_report_failure_creates_issue_when_none_exists():
    search_resp: list[Any] = []
    create_resp = {"number": 99}
    transport, calls = _make_transport([search_resp, create_resp])

    with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "o/r"}):
        result = report_failure(
            container="synth",
            error_type="RuntimeError",
            error_message="kaboom",
            transport=transport,
        )

    assert result == 99
    assert len(calls) == 2
    # First call: search for existing issues
    assert calls[0]["method"] == "GET"
    # Second call: create issue
    assert calls[1]["method"] == "POST"
    assert "/repos/o/r/issues" in calls[1]["url"]
    body = calls[1]["body"]
    assert body["title"].startswith("[ACA failure] synth:")
    assert FAILURE_LABEL in body["labels"]


# ---------------------------------------------------------------------------
# report_failure — updates existing issue
# ---------------------------------------------------------------------------


def test_report_failure_comments_on_existing_issue():
    search_resp = [{"number": 42, "title": "[ACA failure] synth: RuntimeError"}]
    comment_resp = {"id": 1}
    transport, calls = _make_transport([search_resp, comment_resp])

    with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "o/r"}):
        result = report_failure(
            container="synth",
            error_type="RuntimeError",
            error_message="kaboom again",
            transport=transport,
        )

    assert result == 42
    assert len(calls) == 2
    assert "/issues/42/comments" in calls[1]["url"]
    assert "Recurrence" in calls[1]["body"]["body"]


# ---------------------------------------------------------------------------
# report_failure — from exception
# ---------------------------------------------------------------------------


def test_report_failure_extracts_info_from_exception():
    search_resp: list[Any] = []
    create_resp = {"number": 55}
    transport, calls = _make_transport([search_resp, create_resp])

    try:
        raise ValueError("test error for reporting")
    except ValueError as exc:
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}):
            result = report_failure(container="api", error=exc, transport=transport)

    assert result == 55
    body = calls[1]["body"]
    assert "ValueError" in body["title"]
    assert "test error for reporting" in body["body"]


# ---------------------------------------------------------------------------
# report_failure — network error is swallowed
# ---------------------------------------------------------------------------


def test_report_failure_swallows_transport_errors():
    def bad_transport(req):
        raise OSError("network down")

    with patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}):
        result = report_failure(
            container="synth",
            error_type="Err",
            error_message="msg",
            transport=bad_transport,
        )

    assert result is None


# ---------------------------------------------------------------------------
# report_failure — details passthrough
# ---------------------------------------------------------------------------


def test_report_failure_includes_details_in_new_issue():
    transport, calls = _make_transport([[], {"number": 1}])
    with patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}):
        report_failure(
            container="synth",
            error_type="E",
            error_message="m",
            details={"job_id": "j-123", "dequeue_count": 5},
            transport=transport,
        )
    assert "j-123" in calls[1]["body"]["body"]


def test_report_failure_includes_details_in_comment():
    existing = [{"number": 7, "title": "[ACA failure] synth: E"}]
    transport, calls = _make_transport([existing, {"id": 1}])
    with patch.dict(os.environ, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}):
        report_failure(
            container="synth",
            error_type="E",
            error_message="m",
            details={"job_id": "j-456"},
            transport=transport,
        )
    assert "j-456" in calls[1]["body"]["body"]


# ---------------------------------------------------------------------------
# Default repo fallback
# ---------------------------------------------------------------------------


def test_report_failure_uses_default_repo():
    transport, calls = _make_transport([[], {"number": 1}])
    env = {"GITHUB_TOKEN": "t"}
    # Ensure GITHUB_REPOSITORY is NOT set
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("GITHUB_REPOSITORY", None)
        report_failure(
            container="synth",
            error_type="E",
            error_message="m",
            transport=transport,
        )
    assert "jmservera/SquadScope-Podcaster" in calls[0]["url"]
