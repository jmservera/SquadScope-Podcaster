"""Video-only Spotify mode (SPOTIFY_PUBLISH_ENABLED=false): skipped is not failed."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from podcaster import orchestration
from podcaster.monitoring import app, set_storage
from podcaster.orchestration import manifest_path
from podcaster.publish import PublishResult
from podcaster.spotify_mode import review_publish_fields
from podcaster.storage import LocalStorageBackend
from tests.test_api import make_handler
from tests.test_orchestration import _job_id, _stage, _synthesized_manifest

API_KEY = "test-key-123"


def _no_spotify_mutation(*args, **kwargs):
    raise AssertionError("audio Spotify publish must not run in video-only mode")


@pytest.fixture
def staged(tmp_path: Path) -> LocalStorageBackend:
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    _stage(storage, _synthesized_manifest())
    return _use(storage)


def _wrap_process(storage: LocalStorageBackend, calls: list[dict]):
    real = orchestration.process_review_decision

    def _process(job_id, **kwargs):
        calls.append(kwargs)
        assert kwargs["storage"] is storage
        return real(job_id, **kwargs)

    return _process


def _persisted(storage: LocalStorageBackend) -> dict:
    return json.loads(storage.get_bytes(manifest_path(_job_id())).decode("utf-8"))


def _review_body() -> dict:
    return {"job_id": _job_id(), "reviewer": "leela", "decision": "approved"}


def _post_monitoring(body: dict) -> tuple[int, dict]:
    response = TestClient(app).post("/api/review", json=body)
    return response.status_code, response.json()


def _post_api(body: dict) -> tuple[int, dict]:
    raw = json.dumps(body).encode()
    headers = {"x-podcaster-api-key": API_KEY, "Content-Length": str(len(raw))}
    with patch.dict(os.environ, {"PODCASTER_API_KEY": API_KEY}):
        handler = make_handler("POST", "/api/review", body=raw, headers=headers)
    return handler.response_code, handler.get_response_json()


ENDPOINTS = [
    pytest.param("podcaster.monitoring.process_review_decision", _post_monitoring, id="monitoring"),
    pytest.param("podcaster.api.process_review_decision", _post_api, id="api"),
]


_CURRENT_STORAGE: list[LocalStorageBackend] = []


@pytest.fixture(autouse=True)
def _handler_storage(monkeypatch):
    """Route both review handlers to the storage staged by the test."""
    _CURRENT_STORAGE.clear()
    monkeypatch.setattr("podcaster.api.create_storage_backend", lambda: _CURRENT_STORAGE[-1])
    set_storage(None)
    yield
    set_storage(None)


def _use(storage: LocalStorageBackend) -> LocalStorageBackend:
    _CURRENT_STORAGE.append(storage)
    set_storage(storage)
    return storage


@pytest.mark.parametrize(("target", "post"), ENDPOINTS)
def test_video_only_approval_skips_audio_publish_without_failing(
    target, post, staged, monkeypatch
) -> None:
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "false")
    monkeypatch.setattr("podcaster.orchestration.publish_episode", _no_spotify_mutation)
    calls: list[dict] = []

    with patch(target, side_effect=_wrap_process(staged, calls)):
        status_code, body = post(_review_body())

    assert status_code == HTTPStatus.OK
    assert calls[0]["publish_on_approval"] is False
    assert body["publish_status"] == "skipped"
    assert body["publish_error"] is None
    assert body["publish_skipped_reason"] == "spotify_audio_publish_disabled"
    assert body["status"] == "review_approved"
    persisted = _persisted(staged)
    assert persisted["status"] == "review_approved"
    assert persisted["review"]["status"] == "approved"
    assert persisted["publishing"]["eligible"] is True
    assert persisted["publishing"]["result"]["status"] == "skipped"
    assert persisted["publishing"]["result"]["outcome"] is None
    assert persisted["publishing"]["result"]["details"] == {
        "reason": "spotify_audio_publish_disabled"
    }
    assert body["manifest"]["publishing"]["result"]["status"] == "skipped"


@pytest.mark.parametrize(("target", "post"), ENDPOINTS)
def test_reapproval_publishes_audio_after_audio_is_reenabled(
    target, post, staged, monkeypatch
) -> None:
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "false")
    monkeypatch.setattr("podcaster.orchestration.publish_episode", _no_spotify_mutation)
    calls: list[dict] = []
    with patch(target, side_effect=_wrap_process(staged, calls)):
        post(_review_body())

    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "true")
    published: list[str] = []
    monkeypatch.setattr(
        "podcaster.orchestration.publish_episode",
        lambda *args, **kwargs: (
            published.append("audio") or PublishResult(status="published", anchor_episode_id=7)
        ),
    )
    with patch(target, side_effect=_wrap_process(staged, calls)):
        status_code, body = post(_review_body())

    assert status_code == HTTPStatus.OK
    assert calls[-1]["publish_on_approval"] is True
    assert published == ["audio"]
    assert body["publish_status"] == "published"
    assert "publish_skipped_reason" not in body


@pytest.mark.parametrize(("target", "post"), ENDPOINTS)
def test_non_approval_decision_is_not_reported_as_skipped(
    target, post, staged, monkeypatch
) -> None:
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "false")
    calls: list[dict] = []
    with patch(target, side_effect=_wrap_process(staged, calls)):
        status_code, body = post({**_review_body(), "decision": "changes_requested"})

    assert status_code == HTTPStatus.OK
    assert body["publish_status"] is None
    assert "publish_skipped_reason" not in body


@pytest.mark.parametrize(("target", "post"), ENDPOINTS)
def test_explicit_no_publish_approval_is_not_reported_as_skipped(
    target, post, staged, monkeypatch
) -> None:
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "false")
    calls: list[dict] = []
    with patch(target, side_effect=_wrap_process(staged, calls)):
        status_code, body = post({**_review_body(), "publish_on_approval": False})

    assert status_code == HTTPStatus.OK
    assert calls[0]["publish_on_approval"] is False
    assert body["publish_status"] is None
    assert "publish_skipped_reason" not in body


def test_review_publish_fields_prefers_real_result() -> None:
    result = PublishResult(status="failed", error="boom")
    assert review_publish_fields(result, audio_publish_skipped=True) == {
        "publish_status": "failed",
        "publish_error": "boom",
    }


@pytest.mark.parametrize(("target", "post"), ENDPOINTS)
def test_video_only_approval_of_blocked_job_reports_blocked_not_skipped(
    target, post, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "false")
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    manifest = _synthesized_manifest()
    manifest["generation"]["audio_validation"] = {"status": "failed", "ready": False}
    _stage(storage, manifest)
    _use(storage)
    calls: list[dict] = []

    with patch(target, side_effect=_wrap_process(storage, calls)):
        status_code, body = post(_review_body())

    assert status_code == HTTPStatus.OK
    assert body["publish_status"] == "blocked"
    assert "audio_validation_not_passed" in body["publish_blocked_by"]
    assert "publish_skipped_reason" not in body


_LEGACY_DISABLED_FAILURE = {
    "status": "failed",
    "error": "Spotify publishing disabled (SPOTIFY_PUBLISH_ENABLED != true).",
    "outcome": "manual_handoff_required",
    "anchor_episode_id": None,
}


@pytest.mark.parametrize(("target", "post"), ENDPOINTS)
def test_video_only_reapproval_normalizes_legacy_disabled_failures(
    target, post, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "false")
    monkeypatch.setattr("podcaster.orchestration.publish_episode", _no_spotify_mutation)
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    manifest = _synthesized_manifest()
    manifest["status"] = "publish_failed"
    manifest["publishing"]["result"] = dict(_LEGACY_DISABLED_FAILURE)
    manifest["generation"]["publish_result"] = {
        "anchor_id": None,
        "status": "failed",
        "outcome": "manual_handoff_required",
        "publish_run_id": "123",
        "dry_run": False,
        "error": "Spotify publishing disabled (SPOTIFY_PUBLISH_ENABLED != true).",
        "details": {},
    }
    _stage(storage, manifest)
    _use(storage)
    calls: list[dict] = []

    with patch(target, side_effect=_wrap_process(storage, calls)):
        status_code, body = post(_review_body())

    assert status_code == HTTPStatus.OK
    assert body["publish_status"] == "skipped"
    persisted = _persisted(storage)
    assert persisted["status"] == "review_approved"
    assert persisted["publishing"]["result"]["status"] == "skipped"
    assert persisted["generation"]["publish_result"]["status"] == "skipped"
    assert persisted["generation"]["publish_result"]["outcome"] is None
    assert persisted["generation"]["publish_result"]["publish_run_id"] == "123"
    detail = TestClient(app).get(f"/api/jobs/{_job_id()}").json()
    assert detail["publication_outcome"] is None


@pytest.mark.parametrize(("target", "post"), ENDPOINTS)
def test_video_only_reapproval_preserves_real_provider_history(
    target, post, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "false")
    monkeypatch.setattr("podcaster.orchestration.publish_episode", _no_spotify_mutation)
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    manifest = _synthesized_manifest()
    real_result = {
        "status": "failed",
        "error": "Spotify go-live not confirmed by provider readback",
        "outcome": "publication_unknown",
        "anchor_episode_id": 42,
    }
    manifest["publishing"]["result"] = dict(real_result)
    _stage(storage, manifest)
    _use(storage)

    with patch(target, side_effect=_wrap_process(storage, [])):
        status_code, _body = post(_review_body())

    assert status_code == HTTPStatus.OK
    assert _persisted(storage)["publishing"]["result"] == real_result


@pytest.mark.parametrize(("target", "post"), ENDPOINTS)
def test_video_only_skip_persistence_failure_is_not_reported_as_skipped(
    target, post, staged, monkeypatch
) -> None:
    monkeypatch.setenv("SPOTIFY_PUBLISH_ENABLED", "false")
    monkeypatch.setattr("podcaster.orchestration.publish_episode", _no_spotify_mutation)

    def _fail(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr("podcaster.spotify_mode.record_review_audio_skip", _fail)
    monkeypatch.setattr("podcaster.api.report_failure", lambda **kwargs: None)
    monkeypatch.setattr("podcaster.monitoring.report_failure", lambda **kwargs: None)

    with patch(target, side_effect=_wrap_process(staged, [])):
        status_code, body = post(_review_body())

    assert status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert body.get("publish_status") != "skipped"
