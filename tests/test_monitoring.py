"""Tests for the job monitoring API (podcaster.monitoring)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from podcaster.monitoring import app, set_storage, JobSummary, JobDetailResponse


# ---------------------------------------------------------------------------
# In-memory storage backend for tests
# ---------------------------------------------------------------------------


class MemoryStorageBackend:
    """Minimal in-memory storage for testing the monitoring API."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put_bytes(self, path: str, content: bytes, content_type: str) -> Any:
        self._blobs[path] = content
        return type("SA", (), {"path": path, "url": f"mem://{path}", "size_bytes": len(content), "content_type": content_type})()

    def get_bytes(self, path: str) -> bytes | None:
        return self._blobs.get(path)

    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> Any:
        current = self._blobs.get(path)
        updated = update(current)
        self._blobs[path] = updated
        return self.put_bytes(path, updated, content_type)

    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        matches = sorted(k for k in self._blobs if k.startswith(prefix))
        return matches[:limit]

    def generate_download_url(self, path: str, *, expiry: datetime) -> Any:
        return type("URL", (), {"path": path, "url": f"mem://{path}", "expires_at": "", "method": "local", "signed": False, "https_only": False, "account_key_used": False})()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_manifest(
    job_id: str,
    *,
    status: str = "accepted",
    week: str = "2026-W24",
    article_title: str = "Test Article",
    created_at: str = "2026-06-15T12:00:00Z",
) -> dict[str, Any]:
    return {
        "schema_version": "squadscope-podcaster-job-v1",
        "job_id": job_id,
        "status": status,
        "created_at": created_at,
        "expires_at": "2026-06-22T12:00:00Z",
        "request": {
            "week": week,
            "article_url": "https://example.com/article",
            "article_title": article_title,
        },
        "lifecycle": {
            "status": status,
            "revision": 1,
            "transitions": [
                {"at": created_at, "to": status, "reason": "initial_staging"},
            ],
        },
        "generation": {
            "engine": "llm-script-gen",
            "deterministic": False,
            "audio_mode": "placeholder",
            "tts_provider": None,
            "tts_voice": None,
            "tts_synthesis": {"status": "queued", "allowed": True, "blocked_by": []},
            "audio_validation": {"status": "placeholder"},
            "synthesis_queue": {"status": "enqueued", "enqueued_at": created_at, "detail": None},
        },
        "publishing": {
            "mode": "review_gate",
            "auto_publish_enabled": False,
            "packet_ready": False,
            "eligible": False,
            "blocked_by": ["human_review", "synthesis_not_completed"],
            "readiness_checks": {},
        },
        "warnings": [],
    }


@pytest.fixture
def storage():
    """Provide a fresh in-memory storage backend."""
    backend = MemoryStorageBackend()
    set_storage(backend)
    yield backend
    set_storage(None)


@pytest.fixture
def client(storage):
    """FastAPI test client with injected storage."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests: GET /api/jobs
# ---------------------------------------------------------------------------


class TestListJobs:
    def test_empty(self, client, storage):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["jobs"] == []
        assert data["total"] == 0

    def test_returns_jobs(self, client, storage):
        m1 = _make_manifest("podcast-2026-W24-abc123", created_at="2026-06-15T12:00:00Z")
        m2 = _make_manifest("podcast-2026-W23-def456", week="2026-W23", created_at="2026-06-08T12:00:00Z")
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m1).encode(), "application/json")
        storage.put_bytes("jobs/podcast-2026-W23-def456/manifest.json", json.dumps(m2).encode(), "application/json")

        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["jobs"]) == 2
        # Most recent first
        assert data["jobs"][0]["job_id"] == "podcast-2026-W24-abc123"
        assert data["jobs"][1]["job_id"] == "podcast-2026-W23-def456"

    def test_pagination(self, client, storage):
        for i in range(5):
            m = _make_manifest(f"podcast-2026-W{20+i:02d}-x{i}", created_at=f"2026-05-{10+i:02d}T12:00:00Z")
            storage.put_bytes(f"jobs/podcast-2026-W{20+i:02d}-x{i}/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/jobs?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["jobs"]) == 2

    def test_limit_bounds(self, client, storage):
        resp = client.get("/api/jobs?limit=0")
        assert resp.status_code == 422  # validation error

        resp = client.get("/api/jobs?limit=101")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: GET /api/jobs/{id}
# ---------------------------------------------------------------------------


class TestGetJob:
    def test_not_found(self, client, storage):
        resp = client.get("/api/jobs/nonexistent-job")
        assert resp.status_code == 404

    def test_returns_detail(self, client, storage):
        m = _make_manifest("podcast-2026-W24-abc123", status="synthesized_publish_ready")
        m["generation"]["audio_validation"] = {"status": "passed"}
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/jobs/podcast-2026-W24-abc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "podcast-2026-W24-abc123"
        assert data["status"] == "synthesized_publish_ready"
        assert data["week"] == "2026-W24"
        assert data["article_title"] == "Test Article"
        assert data["quality_score"] == 1.0
        assert data["generation"] is not None
        assert data["publishing"] is not None

    def test_quality_score_placeholder(self, client, storage):
        m = _make_manifest("podcast-2026-W24-abc123")
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/jobs/podcast-2026-W24-abc123")
        data = resp.json()
        assert data["quality_score"] == 0.0

    def test_corrupt_manifest(self, client, storage):
        storage.put_bytes("jobs/bad-job/manifest.json", b"not json", "application/json")
        resp = client.get("/api/jobs/bad-job")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Tests: GET /api/jobs/{id}/logs
# ---------------------------------------------------------------------------


class TestGetJobLogs:
    def test_not_found(self, client, storage):
        resp = client.get("/api/jobs/nonexistent/logs")
        assert resp.status_code == 404

    def test_returns_lifecycle_transitions(self, client, storage):
        m = _make_manifest("podcast-2026-W24-abc123")
        m["lifecycle"]["transitions"] = [
            {"at": "2026-06-15T12:00:00Z", "to": "accepted", "reason": "initial_staging"},
            {"at": "2026-06-15T12:05:00Z", "to": "synthesized_publish_ready", "reason": "audio_synthesized_validation_passed"},
        ]
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/jobs/podcast-2026-W24-abc123/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "podcast-2026-W24-abc123"
        assert len(data["logs"]) >= 2
        events = [log["event"] for log in data["logs"]]
        assert "transition:accepted" in events
        assert "transition:synthesized_publish_ready" in events

    def test_includes_synthesis_runner_state(self, client, storage):
        m = _make_manifest("podcast-2026-W24-abc123")
        m["generation"]["synthesis_runner"] = {
            "schema_version": "squadscope-podcaster-synthesis-runner-v1",
            "status": "completed",
            "completed_at": "2026-06-15T12:10:00Z",
            "job_id": "podcast-2026-W24-abc123",
        }
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/jobs/podcast-2026-W24-abc123/logs")
        data = resp.json()
        events = [log["event"] for log in data["logs"]]
        assert "synthesis:completed" in events

    def test_includes_queue_state(self, client, storage):
        m = _make_manifest("podcast-2026-W24-abc123")
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/jobs/podcast-2026-W24-abc123/logs")
        data = resp.json()
        events = [log["event"] for log in data["logs"]]
        assert "queue:enqueued" in events

    def test_logs_sorted_by_timestamp(self, client, storage):
        m = _make_manifest("podcast-2026-W24-abc123")
        m["lifecycle"]["transitions"] = [
            {"at": "2026-06-15T12:05:00Z", "to": "synthesized", "reason": "done"},
            {"at": "2026-06-15T12:00:00Z", "to": "accepted", "reason": "initial"},
        ]
        m["generation"]["synthesis_queue"] = {"status": "enqueued", "enqueued_at": "2026-06-15T12:01:00Z", "detail": None}
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/jobs/podcast-2026-W24-abc123/logs")
        data = resp.json()
        timestamps = [log["timestamp"] for log in data["logs"] if log["timestamp"]]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# Tests: GET /healthz
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_healthy(self, client, storage):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}
