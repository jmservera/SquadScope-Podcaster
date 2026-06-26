"""Tests for the job monitoring API (podcaster.monitoring)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from podcaster.monitoring import app, set_storage
from podcaster.orchestration import JobPublishOutcome
from podcaster.publish import PublishResult

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

    def blob_exists(self, path: str) -> bool:
        return path in self._blobs

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
# Tests: GET /api/jobs/{id}/logs — structured logs (#472) + filtering
# ---------------------------------------------------------------------------


def _write_structured_logs(storage, job_id, records):
    doc = {
        "schema_version": "squadscope-podcaster-logs-v1",
        "job_id": job_id,
        "updated_at": records[-1]["at"] if records else None,
        "records": records,
    }
    storage.put_bytes(f"jobs/{job_id}/logs.json", json.dumps(doc).encode(), "application/json")


class TestStructuredJobLogs:
    JOB = "podcast-2026-W24-abc123"

    def _setup(self, storage):
        m = _make_manifest(self.JOB)
        m["lifecycle"]["transitions"] = [
            {"at": "2026-06-15T12:00:00Z", "to": "accepted", "reason": "initial_staging"},
        ]
        storage.put_bytes(f"jobs/{self.JOB}/manifest.json", json.dumps(m).encode(), "application/json")
        _write_structured_logs(
            storage,
            self.JOB,
            [
                {"seq": 1, "at": "2026-06-15T12:02:00Z", "level": "info", "message": "recording 5 segments", "stage": "synthesis"},
                {"seq": 2, "at": "2026-06-15T12:03:00Z", "level": "warning", "message": "music skipped", "task_id": "mix-1"},
                {"seq": 3, "at": "2026-06-15T12:04:00Z", "level": "error", "message": "synthesis failed", "stage": "synthesis"},
            ],
        )

    def test_merges_structured_and_manifest_logs(self, client, storage):
        self._setup(storage)
        resp = client.get(f"/api/jobs/{self.JOB}/logs")
        assert resp.status_code == 200
        data = resp.json()
        sources = {log["source"] for log in data["logs"]}
        assert sources == {"manifest", "structured"}
        messages = [log["message"] for log in data["logs"]]
        assert "recording 5 segments" in messages
        assert data["total"] == len(data["logs"])

    def test_structured_logs_carry_level_and_fields(self, client, storage):
        self._setup(storage)
        resp = client.get(f"/api/jobs/{self.JOB}/logs")
        data = resp.json()
        by_msg = {log["message"]: log for log in data["logs"]}
        assert by_msg["music skipped"]["level"] == "warning"
        assert by_msg["music skipped"]["task_id"] == "mix-1"
        assert by_msg["synthesis failed"]["level"] == "error"
        assert by_msg["synthesis failed"]["stage"] == "synthesis"

    def test_level_filter_minimum_severity(self, client, storage):
        self._setup(storage)
        resp = client.get(f"/api/jobs/{self.JOB}/logs?level=warning")
        data = resp.json()
        levels = {log["level"] for log in data["logs"]}
        assert levels <= {"warning", "error"}
        assert "info" not in levels
        assert data["level"] == "warning"

    def test_level_filter_error_only(self, client, storage):
        self._setup(storage)
        resp = client.get(f"/api/jobs/{self.JOB}/logs?level=error")
        data = resp.json()
        assert all(log["level"] == "error" for log in data["logs"])
        assert any(log["message"] == "synthesis failed" for log in data["logs"])

    def test_search_filter(self, client, storage):
        self._setup(storage)
        resp = client.get(f"/api/jobs/{self.JOB}/logs?search=recording")
        data = resp.json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["message"] == "recording 5 segments"
        assert data["search"] == "recording"

    def test_search_is_case_insensitive(self, client, storage):
        self._setup(storage)
        resp = client.get(f"/api/jobs/{self.JOB}/logs?search=MUSIC")
        data = resp.json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["task_id"] == "mix-1"

    def test_level_and_search_combined(self, client, storage):
        self._setup(storage)
        resp = client.get(f"/api/jobs/{self.JOB}/logs?level=warning&search=synthesis")
        data = resp.json()
        assert len(data["logs"]) == 1
        assert data["logs"][0]["message"] == "synthesis failed"

    def test_blank_level_param_does_not_drop_debug(self, client, storage):
        # `?level=%20` (whitespace) must behave like "not provided", not the
        # `info` default which would silently drop debug entries (#472 review).
        self._setup(storage)
        resp = client.get(f"/api/jobs/{self.JOB}/logs?level=%20")
        assert resp.status_code == 200
        data = resp.json()
        assert data["level"] is None
        # Info-level entry still present (not filtered out by a phantom default).
        assert any(log["message"] == "recording 5 segments" for log in data["logs"])

    def test_blank_search_param_echoed_as_none(self, client, storage):
        self._setup(storage)
        resp = client.get(f"/api/jobs/{self.JOB}/logs?search=%20%20")
        assert resp.status_code == 200
        data = resp.json()
        assert data["search"] is None
        assert data["total"] == len(data["logs"])

    def test_manifest_failed_transition_inferred_error(self, client, storage):
        m = _make_manifest(self.JOB)
        m["lifecycle"]["transitions"] = [
            {"at": "2026-06-15T12:00:00Z", "to": "failed", "reason": "synthesis_failed"},
        ]
        storage.put_bytes(f"jobs/{self.JOB}/manifest.json", json.dumps(m).encode(), "application/json")
        resp = client.get(f"/api/jobs/{self.JOB}/logs?level=error")
        data = resp.json()
        assert any(log["event"] == "transition:failed" for log in data["logs"])

    def test_no_structured_logs_still_returns_manifest(self, client, storage):
        m = _make_manifest(self.JOB)
        storage.put_bytes(f"jobs/{self.JOB}/manifest.json", json.dumps(m).encode(), "application/json")
        resp = client.get(f"/api/jobs/{self.JOB}/logs")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


# ---------------------------------------------------------------------------
# Tests: POST /api/jobs/{id}/video/generate
# ---------------------------------------------------------------------------


class TestEnqueueVideo:
    def test_not_found(self, client, storage):
        resp = client.post("/api/jobs/nonexistent/video/generate")
        assert resp.status_code == 404

    def test_corrupt_manifest(self, client, storage):
        storage.put_bytes("jobs/bad-job/manifest.json", b"not json", "application/json")
        resp = client.post("/api/jobs/bad-job/video/generate")
        assert resp.status_code == 500

    def test_enqueues_successfully(self, client, storage):
        m = _make_manifest("podcast-2026-W24-abc123")
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m).encode(), "application/json")

        with patch("podcaster.monitoring.enqueue_video_job", return_value=True) as mock_enqueue:
            resp = client.post("/api/jobs/podcast-2026-W24-abc123/video/generate")

        assert resp.status_code == 200
        assert resp.json() == {"job_id": "podcast-2026-W24-abc123", "enqueued": True}
        mock_enqueue.assert_called_once_with("podcast-2026-W24-abc123")

    def test_queue_not_configured_returns_503(self, client, storage):
        m = _make_manifest("podcast-2026-W24-abc123")
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m).encode(), "application/json")

        with patch("podcaster.monitoring.enqueue_video_job", return_value=False):
            resp = client.post("/api/jobs/podcast-2026-W24-abc123/video/generate")

        assert resp.status_code == 503

    def test_enqueue_failure_returns_502(self, client, storage):
        m = _make_manifest("podcast-2026-W24-abc123")
        storage.put_bytes("jobs/podcast-2026-W24-abc123/manifest.json", json.dumps(m).encode(), "application/json")

        with patch("podcaster.monitoring.enqueue_video_job", side_effect=RuntimeError("boom")):
            resp = client.post("/api/jobs/podcast-2026-W24-abc123/video/generate")

        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: GET /healthz
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_healthy(self, client, storage):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


class TestGenerateEndpoint:
    def test_invalid_json_returns_400(self, client, storage):
        resp = client.post("/api/generate", data="not json", headers={"content-type": "application/json"})

        assert resp.status_code == 400
        assert "request body must be valid JSON" in resp.json()["errors"]

    def test_validation_errors_return_contract_body(self, client, storage):
        resp = client.post("/api/generate", json={})

        assert resp.status_code == 400
        body = resp.json()
        assert body["status"] == "failed"
        assert "week is required" in body["errors"]

    @patch("podcaster.monitoring.run_generation_job")
    def test_success_returns_202(self, mock_run_generation_job, client, storage):
        mock_run_generation_job.return_value = type(
            "Result",
            (),
            {"response": {"job_id": "job-123", "status": "accepted", "errors": [], "warnings": []}},
        )()

        resp = client.post(
            "/api/generate",
            json={"week": "2026-W24", "article_url": "https://example.com/article"},
        )

        assert resp.status_code == 202
        assert resp.json()["job_id"] == "job-123"


class TestReviewEndpoint:
    def test_missing_required_fields_return_400(self, client, storage):
        resp = client.post("/api/review", json={"reviewer": "leela"})

        assert resp.status_code == 400
        assert "job_id is required" in resp.json()["errors"]

    @patch("podcaster.monitoring.process_review_decision")
    def test_returns_manifest_and_publish_status(self, mock_process_review_decision, client, storage):
        mock_process_review_decision.return_value = JobPublishOutcome(
            manifest={"job_id": "podcast-1", "status": "published", "review_status": "approved"},
            publish_result=PublishResult(status="published"),
        )

        resp = client.post(
            "/api/review",
            json={"job_id": "podcast-1", "reviewer": "leela", "decision": "approved"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == "podcast-1"
        assert body["publish_status"] == "published"
        assert body["manifest"]["status"] == "published"

    @patch("podcaster.monitoring.process_review_decision", side_effect=ValueError("missing job"))
    def test_returns_404_for_missing_job(self, _mock_process_review_decision, client, storage):
        resp = client.post(
            "/api/review",
            json={"job_id": "podcast-1", "reviewer": "leela", "decision": "approved"},
        )

        assert resp.status_code == 404
        assert resp.json() == {"error": "missing job"}


class TestUiNavigationEndpoints:
    def test_credentials_list_returns_empty(self, client, storage, monkeypatch):
        monkeypatch.setenv("PODCASTER_API_KEY", "api-key")
        monkeypatch.setenv("UI_AUTH_USERNAME", "admin")
        monkeypatch.setenv("UI_AUTH_PASSWORD", "hunter2")
        monkeypatch.setenv("UI_AUTH_SECRET", "secret-256-bits-long-enough")

        resp = client.get("/api/credentials", headers={"x-podcaster-api-key": "api-key"})

        assert resp.status_code == 200
        assert resp.json() == {"credentials": []}

    def test_config_endpoints_return_runtime_settings(self, client, storage, monkeypatch):
        monkeypatch.setenv("MONITORING_API_KEY", "monitor-key")
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "episodes")
        headers = {"x-podcaster-api-key": "monitor-key"}

        get_resp = client.get("/api/config", headers=headers)
        post_resp = client.post("/api/config", json={"foo": "bar"}, headers=headers)

        assert get_resp.status_code == 200
        assert get_resp.json()["storage_backend"] == "local"
        assert get_resp.json()["storage_container"] == "episodes"
        assert get_resp.json()["cors_origins"] == ["*"]
        assert post_resp.status_code == 200
        assert post_resp.json()["status"] == "accepted"

    def test_cors_preflight_allows_wildcard_origin(self, client, storage):
        resp = client.options(
            "/api/generate",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )

        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "*"


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

    def test_episode_with_video_artifact(self, client, storage):
        """Video blob path is surfaced as video_path/video_url from the video runner."""
        m = self._manifest_with_audio("job-vid", audio_path="jobs/job-vid/episode.mp3")
        m["generation"]["video_runner"] = {
            "status": "completed",
            "distribution": {"status": "archived", "blob_path": "jobs/job-vid/video/job-vid.mp4"},
        }
        storage.put_bytes("jobs/job-vid/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        data = resp.json()
        ep = data["episodes"][0]
        assert ep["video_path"] == "jobs/job-vid/video/job-vid.mp4"
        assert ep["video_url"] == "/api/stream/jobs/job-vid/video/job-vid.mp4"

    def test_episode_video_full_url_normalized_to_blob_path(self, client, storage, monkeypatch):
        """A full blob URL in distribution.blob_path is normalized to a container-relative path."""
        monkeypatch.setenv("PODCASTER_STORAGE_CONTAINER", "podcaster-artifacts")
        m = self._manifest_with_audio("job-url", audio_path="jobs/job-url/episode.mp3")
        m["generation"]["video_runner"] = {
            "status": "completed",
            "distribution": {
                "status": "archived",
                "blob_path": "https://acct.blob.core.windows.net/podcaster-artifacts/jobs/job-url/video/job-url.mp4",
            },
        }
        storage.put_bytes("jobs/job-url/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        ep = resp.json()["episodes"][0]
        assert ep["video_path"] == "jobs/job-url/video/job-url.mp4"
        assert ep["video_url"] == "/api/stream/jobs/job-url/video/job-url.mp4"

    def test_episode_without_video_has_null_video_fields(self, client, storage):
        m = self._manifest_with_audio("job-novid", audio_path="jobs/job-novid/episode.mp3")
        storage.put_bytes("jobs/job-novid/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        ep = resp.json()["episodes"][0]
        assert ep["video_path"] is None
        assert ep["video_url"] is None

    def test_episode_lists_extra_artifacts_excluding_audio_and_video(self, client, storage):
        """Extra files (e.g. wav) are exposed as artifacts; the primary audio and
        video are excluded since they have dedicated players."""
        m = _make_manifest("job-art")
        m["generation"]["synthesis_runner"] = {
            "status": "completed",
            "audio": {
                "path": "jobs/job-art/episode.mp3",
                "artifacts": {
                    "mp3": {"path": "jobs/job-art/episode.mp3"},
                    "wav": {"path": "jobs/job-art/episode.wav"},
                },
            },
        }
        m["generation"]["video_runner"] = {
            "status": "completed",
            "distribution": {"status": "archived", "blob_path": "jobs/job-art/video/job-art.mp4"},
        }
        storage.put_bytes("jobs/job-art/manifest.json", json.dumps(m).encode(), "application/json")

        resp = client.get("/api/episodes")
        ep = resp.json()["episodes"][0]
        artifact_paths = {a["path"] for a in ep["artifacts"]}
        assert "jobs/job-art/episode.wav" in artifact_paths
        assert "jobs/job-art/episode.mp3" not in artifact_paths
        assert "jobs/job-art/video/job-art.mp4" not in artifact_paths
        wav = next(a for a in ep["artifacts"] if a["path"] == "jobs/job-art/episode.wav")
        assert wav["url"] == "/api/stream/jobs/job-art/episode.wav"
        assert wav["content_type"] == "audio/wav"


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


class TestCredentialsCrudMonitoring:
    """Tests for credential CRUD endpoints wired into monitoring.py."""

    def _headers(self, monkeypatch):
        monkeypatch.setenv("PODCASTER_API_KEY", "api-key")
        monkeypatch.setenv("UI_AUTH_SECRET", "test-secret-256-bits-long-enough")
        return {"x-podcaster-api-key": "api-key"}

    def test_credentials_crud_lifecycle(self, client, storage, monkeypatch):
        headers = self._headers(monkeypatch)

        # Create
        resp = client.post(
            "/api/credentials",
            json={"type": "spotify", "label": "Main", "values": {"show_id": "s1"}},
            headers=headers,
        )
        assert resp.status_code == 200
        created = resp.json()
        assert created["type"] == "spotify"
        assert created["label"] == "Main"
        assert created["is_set"] is True
        assert "values" not in created
        cred_id = created["id"]

        # List
        resp = client.get("/api/credentials", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["credentials"]) == 1

        # Update
        resp = client.put(
            f"/api/credentials/{cred_id}",
            json={"type": "youtube", "label": "Updated", "values": {"ch": "c1"}},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["label"] == "Updated"

        # Delete
        resp = client.delete(f"/api/credentials/{cred_id}", headers=headers)
        assert resp.status_code == 204

        # Verify empty
        resp = client.get("/api/credentials", headers=headers)
        assert resp.json() == {"credentials": []}

    def test_credentials_require_ui_auth_secret(self, client, storage, monkeypatch):
        monkeypatch.setenv("PODCASTER_API_KEY", "api-key")
        monkeypatch.delenv("UI_AUTH_SECRET", raising=False)
        resp = client.get("/api/credentials", headers={"x-podcaster-api-key": "api-key"})
        assert resp.status_code == 501

    def test_credentials_reject_invalid_payload(self, client, storage, monkeypatch):
        headers = self._headers(monkeypatch)
        resp = client.post(
            "/api/credentials",
            json={"type": "bad", "label": "", "values": []},
            headers=headers,
        )
        assert resp.status_code == 400


class TestPodcastConfigMonitoring:
    """Tests for podcast-config endpoints wired into monitoring.py."""

    def _headers(self, monkeypatch):
        monkeypatch.setenv("PODCASTER_API_KEY", "api-key")
        return {"x-podcaster-api-key": "api-key"}

    def test_get_returns_defaults(self, client, storage, monkeypatch):
        headers = self._headers(monkeypatch)
        resp = client.get("/api/podcast-config", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == ""
        assert data["auto_publish"] is False

    def test_save_and_read_back(self, client, storage, monkeypatch):
        headers = self._headers(monkeypatch)
        payload = {
            "name": "My Show",
            "intro_music_url": None,
            "outro_music_url": None,
            "publish_targets": [],
            "auto_publish": False,
            "schedule": None,
        }
        resp = client.post("/api/podcast-config", json=payload, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "My Show"

        resp = client.get("/api/podcast-config", headers=headers)
        assert resp.json()["name"] == "My Show"

    def test_save_rejects_invalid(self, client, storage, monkeypatch):
        headers = self._headers(monkeypatch)
        resp = client.post(
            "/api/podcast-config",
            json={"name": "", "publish_targets": [], "auto_publish": "yes"},
            headers=headers,
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: GET /api/jobs/{id}/progress and /progress/stream (issue #469)
# ---------------------------------------------------------------------------

from podcaster.progress import PipelineStage, emit_progress  # noqa: E402


def _store_manifest(storage, job_id: str) -> None:
    manifest = _make_manifest(job_id)
    storage.put_bytes(
        f"jobs/{job_id}/manifest.json",
        json.dumps(manifest).encode(),
        "application/json",
    )


class TestProgressPoll:
    def test_unknown_job_404(self, client, storage):
        resp = client.get("/api/jobs/missing/progress")
        assert resp.status_code == 404

    def test_job_without_progress_returns_empty(self, client, storage):
        _store_manifest(storage, "job-1")
        resp = client.get("/api/jobs/job-1/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "job-1"
        assert data["current"] is None
        assert data["events"] == []
        assert data["terminal"] is False

    def test_returns_events_and_current(self, client, storage):
        _store_manifest(storage, "job-1")
        emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS, segment_index=1, segment_total=3)
        emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS, segment_index=2, segment_total=3)

        resp = client.get("/api/jobs/job-1/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 2
        assert data["last_seq"] == 2
        assert data["current"]["segment_index"] == 2
        assert data["terminal"] is False

    def test_since_cursor_filters_events(self, client, storage):
        _store_manifest(storage, "job-1")
        for _ in range(4):
            emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS)

        resp = client.get("/api/jobs/job-1/progress", params={"since": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert [e["seq"] for e in data["events"]] == [3, 4]
        assert data["last_seq"] == 4

    def test_terminal_flag_set_on_completion(self, client, storage):
        _store_manifest(storage, "job-1")
        emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS)
        emit_progress(storage, "job-1", stage=PipelineStage.COMPLETED, percent=100.0)

        resp = client.get("/api/jobs/job-1/progress")
        assert resp.json()["terminal"] is True


class TestProgressStream:
    def test_unknown_job_404(self, client, storage):
        resp = client.get("/api/jobs/missing/progress/stream")
        assert resp.status_code == 404

    def test_stream_emits_terminal_events_and_closes(self, client, storage):
        _store_manifest(storage, "job-1")
        emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS, segment_index=1, segment_total=2)
        emit_progress(storage, "job-1", stage=PipelineStage.COMPLETED, percent=100.0)

        # The stream terminates immediately because the latest event is terminal.
        resp = client.get("/api/jobs/job-1/progress/stream")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        data_lines = [line for line in body.splitlines() if line.startswith("data: ")]
        assert len(data_lines) == 2
        first = json.loads(data_lines[0][len("data: "):])
        assert first["stage"] == PipelineStage.SYNTHESIS
        last = json.loads(data_lines[1][len("data: "):])
        assert last["stage"] == PipelineStage.COMPLETED
        assert ": end" in body

    def test_stream_resumes_from_since(self, client, storage):
        _store_manifest(storage, "job-1")
        emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS)
        emit_progress(storage, "job-1", stage=PipelineStage.COMPLETED)

        resp = client.get("/api/jobs/job-1/progress/stream", params={"since": 1})
        assert resp.status_code == 200
        data_lines = [line for line in resp.text.splitlines() if line.startswith("data: ")]
        # Only the event after seq=1 should be replayed.
        assert len(data_lines) == 1
        assert json.loads(data_lines[0][len("data: "):])["stage"] == PipelineStage.COMPLETED


# ---------------------------------------------------------------------------
# Tests: GET /api/jobs/{id}/progress/summary (issue #470)
# ---------------------------------------------------------------------------


class TestProgressSummary:
    def test_unknown_job_404(self, client, storage):
        resp = client.get("/api/jobs/missing/progress/summary")
        assert resp.status_code == 404

    def test_job_without_progress_is_pending(self, client, storage):
        _store_manifest(storage, "job-1")
        resp = client.get("/api/jobs/job-1/progress/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "job-1"
        assert data["phase"] == "pending"
        assert data["stage"] is None
        assert data["eta"] is None
        assert data["terminal"] is False

    def test_in_flight_segment_counter_and_eta(self, client, storage):
        _store_manifest(storage, "job-1")
        base = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
        # Stage start, then 12/18 done 60s later → 5s/segment → 30s ETA.
        emit_progress(
            storage, "job-1", stage=PipelineStage.SYNTHESIS, phase="recording",
            segment_total=18, at=base,
        )
        emit_progress(
            storage, "job-1", stage=PipelineStage.SYNTHESIS, phase="recording",
            segment_index=12, segment_total=18, at=base + timedelta(seconds=60),
        )

        with patch("podcaster.stage_progress._utcnow") as mock_now:
            mock_now.return_value = base + timedelta(seconds=60)
            resp = client.get("/api/jobs/job-1/progress/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == PipelineStage.SYNTHESIS
        assert data["segment_index"] == 12
        assert data["segment_total"] == 18
        assert data["phase"] == "recording"
        assert data["terminal"] is False
        assert abs(data["eta_seconds"] - 30.0) < 0.5
        assert data["eta"] is not None

    def test_completed_is_terminal(self, client, storage):
        _store_manifest(storage, "job-1")
        emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS, segment_index=1, segment_total=2)
        emit_progress(storage, "job-1", stage=PipelineStage.COMPLETED, percent=100.0)

        resp = client.get("/api/jobs/job-1/progress/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage"] == PipelineStage.COMPLETED
        assert data["terminal"] is True
        assert data["eta_seconds"] == 0.0


# ---------------------------------------------------------------------------
# Tests: GET /api/jobs/{id}/assets (issue #471)
# ---------------------------------------------------------------------------


class TestJobAssets:
    def test_unknown_job_404(self, client, storage):
        resp = client.get("/api/jobs/missing/assets")
        assert resp.status_code == 404

    def test_job_without_media_returns_empty(self, client, storage):
        _store_manifest(storage, "job-1")
        resp = client.get("/api/jobs/job-1/assets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "job-1"
        assert data["assets"] == []
        assert data["total"] == 0

    def test_lists_media_and_skips_non_media(self, client, storage):
        _store_manifest(storage, "job-1")
        storage.put_bytes("jobs/job-1/video/job-1.mp4", b"v", "video/mp4")
        storage.put_bytes("jobs/job-1/episode.mp3", b"a", "audio/mpeg")
        storage.put_bytes("jobs/job-1/segments/seg-01.wav", b"a", "audio/wav")
        storage.put_bytes("jobs/job-1/thumbnail.png", b"i", "image/png")
        # Non-media blobs must be excluded.
        storage.put_bytes("jobs/job-1/progress.json", b"{}", "application/json")
        storage.put_bytes("jobs/job-1/script.txt", b"x", "text/plain")

        resp = client.get("/api/jobs/job-1/assets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        paths = {a["path"] for a in data["assets"]}
        assert "jobs/job-1/progress.json" not in paths
        assert "jobs/job-1/script.txt" not in paths
        assert "jobs/job-1/manifest.json" not in paths

    def test_orders_video_then_audio_then_image(self, client, storage):
        _store_manifest(storage, "job-1")
        storage.put_bytes("jobs/job-1/thumbnail.png", b"i", "image/png")
        storage.put_bytes("jobs/job-1/episode.mp3", b"a", "audio/mpeg")
        storage.put_bytes("jobs/job-1/video/job-1.mp4", b"v", "video/mp4")

        resp = client.get("/api/jobs/job-1/assets")
        kinds = [a["kind"] for a in resp.json()["assets"]]
        assert kinds == ["video", "audio", "image"]

    def test_asset_fields_use_stream_proxy(self, client, storage):
        _store_manifest(storage, "job-1")
        storage.put_bytes("jobs/job-1/video/job-1.mp4", b"v", "video/mp4")

        resp = client.get("/api/jobs/job-1/assets")
        asset = resp.json()["assets"][0]
        assert asset["name"] == "video/job-1.mp4"
        assert asset["path"] == "jobs/job-1/video/job-1.mp4"
        assert asset["url"] == "/api/stream/jobs/job-1/video/job-1.mp4"
        assert asset["content_type"] == "video/mp4"
        assert asset["kind"] == "video"
