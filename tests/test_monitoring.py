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

    def test_skips_corrupt_manifests_in_total(self, client, storage):
        valid_manifest = _make_manifest("podcast-2026-W24-valid")
        storage.put_bytes("jobs/podcast-2026-W24-valid/manifest.json", json.dumps(valid_manifest).encode(), "application/json")
        storage.put_bytes("jobs/podcast-2026-W23-corrupt/manifest.json", b"not json", "application/json")

        resp = client.get("/api/jobs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert [job["job_id"] for job in data["jobs"]] == ["podcast-2026-W24-valid"]

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


class TestMonitoringAuth:
    def test_allows_requests_without_configured_key(self, client, storage):
        resp = client.get("/api/jobs")

        assert resp.status_code == 200

    def test_rejects_missing_or_invalid_api_key_when_configured(self, client, storage, monkeypatch):
        monkeypatch.setenv("MONITORING_API_KEY", "monitor-key")

        missing = client.get("/api/jobs")
        wrong = client.get("/api/jobs", headers={"x-podcaster-api-key": "wrong"})

        assert missing.status_code == 401
        assert wrong.status_code == 401

    def test_allows_valid_api_key_when_configured(self, client, storage, monkeypatch):
        monkeypatch.setenv("MONITORING_API_KEY", "monitor-key")

        resp = client.get("/api/jobs", headers={"x-podcaster-api-key": "monitor-key"})

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: GET /api/stream/{blob_path}
# ---------------------------------------------------------------------------


class TestStreamBlob:
    def test_streams_mp3(self, client, storage):
        audio_data = b"\xff\xfb\x90\x04" + b"\x01" * 100
        storage.put_bytes("jobs/test-job/episode.mp3", audio_data, "audio/mpeg")

        resp = client.get("/api/stream/jobs/test-job/episode.mp3")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.headers["content-length"] == str(len(audio_data))
        assert resp.content == audio_data

    def test_streams_image(self, client, storage):
        image_data = b"\x89PNG\r\n\x1a\n" + b"\x01" * 50
        storage.put_bytes("jobs/test-job/cover.png", image_data, "image/png")

        resp = client.get("/api/stream/jobs/test-job/cover.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == image_data

    def test_streams_video(self, client, storage):
        video_data = b"\x01\x02\x03\x04" + b"\x05" * 100
        storage.put_bytes("jobs/test-job/episode.mp4", video_data, "video/mp4")

        resp = client.get("/api/stream/jobs/test-job/episode.mp4")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"

    def test_rejects_non_media_content_type(self, client, storage):
        storage.put_bytes("jobs/test-job/data.json", b"{}", "application/json")

        resp = client.get("/api/stream/jobs/test-job/data.json")
        assert resp.status_code == 403
        assert "not streamable" in resp.json()["detail"]

    def test_returns_404_for_missing_blob(self, client, storage):
        resp = client.get("/api/stream/jobs/test-job/missing.mp3")
        assert resp.status_code == 404

    def test_cache_control_header(self, client, storage):
        storage.put_bytes("jobs/test-job/episode.mp3", b"\xff" * 10, "audio/mpeg")

        resp = client.get("/api/stream/jobs/test-job/episode.mp3")
        assert "max-age=3600" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# Tests: GET /api/episodes
# ---------------------------------------------------------------------------


class TestListEpisodes:
    def _manifest_with_audio(self, job_id: str, *, audio_path: str = "jobs/test/episode.mp3", **kwargs) -> dict[str, Any]:
        m = _make_manifest(job_id, **kwargs)
        m["generation"]["artifacts"] = {"audio": {"path": audio_path}}
        m["generation"]["audio_validation"] = {"status": "passed"}
        return m

    def test_empty_when_no_audio(self, client, storage):
        m = _make_manifest("test-job")
        storage.put_bytes("jobs/test-job/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["episodes"] == []
        assert data["total"] == 0

    def test_returns_episodes_with_audio(self, client, storage):
        m = self._manifest_with_audio("podcast-W24-abc", audio_path="jobs/podcast-W24-abc/episode.mp3")
        storage.put_bytes("jobs/podcast-W24-abc/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        ep = data["episodes"][0]
        assert ep["job_id"] == "podcast-W24-abc"
        assert ep["audio_path"] == "jobs/podcast-W24-abc/episode.mp3"
        assert ep["audio_url"] == "/api/stream/jobs/podcast-W24-abc/episode.mp3"
        assert ep["quality_score"] == 1.0
        assert ep["title"] == "Test Article"

    def test_episodes_sorted_by_date(self, client, storage):
        m1 = self._manifest_with_audio("job-old", created_at="2026-06-01T12:00:00Z", audio_path="jobs/job-old/ep.mp3")
        m2 = self._manifest_with_audio("job-new", created_at="2026-06-10T12:00:00Z", audio_path="jobs/job-new/ep.mp3")
        storage.put_bytes("jobs/job-old/manifest.json", json.dumps(m1).encode(), "application/json")
        storage.put_bytes("jobs/job-new/manifest.json", json.dumps(m2).encode(), "application/json")

        resp = client.get("/api/episodes")
        data = resp.json()
        assert data["episodes"][0]["job_id"] == "job-new"
        assert data["episodes"][1]["job_id"] == "job-old"

    def test_episodes_pagination(self, client, storage):
        for i in range(3):
            m = self._manifest_with_audio(f"job-{i}", created_at=f"2026-06-{10+i:02d}T12:00:00Z", audio_path=f"jobs/job-{i}/ep.mp3")
            storage.put_bytes(f"jobs/job-{i}/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes?limit=2&offset=0")
        data = resp.json()
        assert data["total"] == 3
        assert len(data["episodes"]) == 2

    def test_episode_with_string_audio_artifact(self, client, storage):
        m = _make_manifest("job-str")
        m["generation"]["artifacts"] = {"audio": "jobs/job-str/audio.mp3"}
        storage.put_bytes("jobs/job-str/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        data = resp.json()
        assert data["total"] == 1
        assert data["episodes"][0]["audio_path"] == "jobs/job-str/audio.mp3"

    def test_episode_with_audio_file_fallback(self, client, storage):
        m = _make_manifest("job-fallback")
        m["generation"]["audio_file"] = "jobs/job-fallback/out.mp3"
        storage.put_bytes("jobs/job-fallback/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        data = resp.json()
        assert data["total"] == 1
        assert data["episodes"][0]["audio_path"] == "jobs/job-fallback/out.mp3"

    def test_publish_status_from_manifest(self, client, storage):
        m = self._manifest_with_audio("job-pub", audio_path="jobs/job-pub/ep.mp3")
        m["publishing"]["status"] = "published"
        storage.put_bytes("jobs/job-pub/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        data = resp.json()
        assert data["episodes"][0]["publish_status"] == "published"

    def test_episode_from_synthesis_runner_audio_path(self, client, storage):
        """Manifests produced by the synthesis runner record audio under
        generation.synthesis_runner.audio.path."""
        m = _make_manifest("job-synth")
        m["generation"]["synthesis_runner"] = {
            "status": "completed",
            "audio": {
                "path": "jobs/job-synth/episode.mp3",
                "sha256": "abc123",
                "size_bytes": 12345,
                "artifacts": {
                    "mp3": {"path": "jobs/job-synth/episode.mp3", "sha256": "abc123", "size_bytes": 12345},
                    "wav": {"path": "jobs/job-synth/episode.wav", "sha256": "def456", "size_bytes": 99999},
                },
            },
        }
        storage.put_bytes("jobs/job-synth/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        data = resp.json()
        assert data["total"] == 1
        assert data["episodes"][0]["job_id"] == "job-synth"
        assert data["episodes"][0]["audio_path"] == "jobs/job-synth/episode.mp3"
        assert data["episodes"][0]["audio_url"] == "/api/stream/jobs/job-synth/episode.mp3"

    def test_episode_from_synthesis_runner_artifacts_mp3(self, client, storage):
        """Falls back to synthesis_runner.audio.artifacts.mp3.path when audio.path is absent."""
        m = _make_manifest("job-synth2")
        m["generation"]["synthesis_runner"] = {
            "status": "completed",
            "audio": {
                "artifacts": {
                    "mp3": {"path": "jobs/job-synth2/episode.mp3", "sha256": "abc", "size_bytes": 100},
                },
            },
        }
        storage.put_bytes("jobs/job-synth2/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        data = resp.json()
        assert data["total"] == 1
        assert data["episodes"][0]["audio_path"] == "jobs/job-synth2/episode.mp3"


# ---------------------------------------------------------------------------
# Tests: GET /api/articles/{path}
# ---------------------------------------------------------------------------


class TestGetArticle:
    def test_serves_markdown(self, client, storage):
        content = "# Hello World\n\nThis is a test article."
        storage.put_bytes("articles/test.md", content.encode(), "text/markdown")

        resp = client.get("/api/articles/articles/test.md")
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]
        assert resp.text == content

    def test_serves_text_file(self, client, storage):
        content = "Plain text article."
        storage.put_bytes("articles/test.txt", content.encode(), "text/plain")

        resp = client.get("/api/articles/articles/test.txt")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert resp.text == content

    def test_rejects_non_text_extension(self, client, storage):
        storage.put_bytes("articles/data.json", b"{}", "application/json")

        resp = client.get("/api/articles/articles/data.json")
        assert resp.status_code == 403

    def test_returns_404_for_missing_article(self, client, storage):
        resp = client.get("/api/articles/articles/missing.md")
        assert resp.status_code == 404

    def test_rejects_non_utf8_content(self, client, storage):
        storage.put_bytes("articles/bad.md", b"\xff\xfe" * 100, "text/markdown")

        resp = client.get("/api/articles/articles/bad.md")
        assert resp.status_code == 422

    def test_cache_control_header(self, client, storage):
        storage.put_bytes("articles/test.md", b"# Title", "text/markdown")

        resp = client.get("/api/articles/articles/test.md")
        assert "max-age=300" in resp.headers.get("cache-control", "")

