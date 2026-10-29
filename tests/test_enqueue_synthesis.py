from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest
from review_support import approval_evidence_for

from podcaster import queue as queue_module
from podcaster.costs import monthly_ledger_path
from podcaster.generation import manifest_bytes
from podcaster.jobs import run_generation_job
from podcaster.orchestration import load_manifest, process_review_decision
from podcaster.queue import (
    SYNTHESIS_QUEUE_SCHEMA_VERSION,
    enqueue_synthesis_job,
    parse_job_id,
)
from podcaster.storage import LocalStorageBackend
from podcaster.validation import RESPONSE_KEYS

LEGACY_STRING_PAYLOAD = {
    "week": "2026-W23",
    "article_title": "Reviewed weekly signal",
    "article_url": "https://example.com/article",
    "article_content": "The source describes a product rollout, engineering tradeoffs, customer "
    "impact, and competitive context, with sufficient detail for a substantive weekly discussion.",
    "source_artifacts": ["https://example.com/source-1", "https://example.com/source-2"],
}

SQUADSCOPE_OBJECT_PAYLOAD = {
    "week": "2026-W23",
    "article_title": "Reviewed weekly signal",
    "article_url": "https://example.com/article",
    "article_content": LEGACY_STRING_PAYLOAD["article_content"],
    "source_artifacts": [
        {"role": "primary", "name": "weekly-signal", "reference": "https://example.com/source-1"},
        {"role": "supporting", "name": "noise-digest", "reference": "https://example.com/source-2"},
    ],
}


class RecordingProducer:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, body: str) -> None:
        self.sent.append(body)


def _local_storage(suffix: str) -> LocalStorageBackend:
    root = Path(f".test-artifacts-enqueue-{suffix}")
    shutil.rmtree(root, ignore_errors=True)
    return LocalStorageBackend(root, "https://example.invalid/artifacts")


@pytest.mark.parametrize(
    "label,payload",
    [("legacy_string", LEGACY_STRING_PAYLOAD), ("squadscope_object", SQUADSCOPE_OBJECT_PAYLOAD)],
)
def test_async_202_stages_pending_review_without_enqueue_for_both_contracts(
    label: str, payload: dict, offline_script_generation
) -> None:
    storage = _local_storage(label)
    calls: list[str] = []

    result = run_generation_job(
        payload,
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
        enqueue=lambda job_id: calls.append(job_id) or True,
    )

    assert tuple(result.response.keys()) == RESPONSE_KEYS
    assert result.response["status"] == "accepted"
    assert result.response["job_id"]
    assert result.response["manifest_url"]
    assert result.response["errors"] == []
    assert calls == []
    assert result.manifest["review"]["status"] == "pending"
    assert result.manifest["generation"]["tts_synthesis"]["allowed"] is False
    assert "human_review" in result.manifest["generation"]["tts_synthesis"]["blocked_by"]


def test_enqueue_not_invoked_when_budget_gate_fails() -> None:
    storage = _local_storage("gate-fail")
    storage.put_bytes(
        monthly_ledger_path("2026-06"),
        manifest_bytes(
            {
                "schema_version": "squadscope-podcaster-monthly-cost-ledger-v1",
                "month": "2026-06",
                "episodes": [
                    {
                        "job_id": f"existing-{index}",
                        "week": f"2026-W2{index}",
                        "estimated_total_usd": "0.00",
                    }
                    for index in range(10)
                ],
            }
        ),
        "application/json; charset=utf-8",
    )
    calls: list[str] = []

    result = run_generation_job(
        {"week": "2026-W29", "article_url": "https://example.com/new-article"},
        storage=storage,
        now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
        enqueue=lambda job_id: calls.append(job_id) or True,
    )

    assert result.response["status"] == "failed"
    assert calls == []


def test_dry_run_does_not_enqueue_synthesis() -> None:
    storage = _local_storage("dry-run")
    calls: list[str] = []

    result = run_generation_job(
        {"week": "2026-W23", "article_url": "https://example.com/article", "dry_run": True},
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
        enqueue=lambda job_id: calls.append(job_id) or True,
    )

    assert result.response["status"] == "dry_run"
    assert calls == []


def test_generation_never_attempts_even_a_failing_enqueue_before_review(
    offline_script_generation,
) -> None:
    storage = _local_storage("enqueue-error")

    def boom(_job_id: str) -> bool:
        raise AssertionError("generation must not enqueue before human approval")

    result = run_generation_job(
        LEGACY_STRING_PAYLOAD,
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
        enqueue=boom,
    )

    assert result.response["status"] == "accepted"
    assert result.response["errors"] == []
    assert result.manifest["generation"]["tts_synthesis"]["allowed"] is False
    assert result.manifest["generation"]["synthesis_queue"]["enqueued_at"] is None


def _approve_staged(storage, result, **kwargs):
    kwargs = {**approval_evidence_for(result.manifest), **kwargs}
    return process_review_decision(
        result.response["job_id"],
        storage=storage,
        reviewer="leela",
        reviewed_at="2026-10-29T11:05:00Z",
        decision="approved",
        notes="Human reviewed the exact staged dialogue and supporting artifacts.",
        **kwargs,
    )


def test_exact_human_approval_enqueues_same_stage_once_without_regeneration(reviewed_stage):
    storage, result, generator, producer, script = reviewed_stage
    job_id = result.response["job_id"]
    script_path = f"jobs/{job_id}/script.txt"
    assert producer.sent == []
    assert storage.get_bytes(script_path) == script
    generator.assert_called_once()

    outcome = _approve_staged(
        storage,
        result,
        script_sha256=sha256(script).hexdigest(),
        manifest_sha256=result.manifest["review"]["snapshot"]["manifest_sha256"],
    )
    assert [parse_job_id(body) for body in producer.sent] == [job_id]
    assert storage.get_bytes(script_path) == script
    generator.assert_called_once()
    assert outcome.manifest["review"]["status"] == "pending"
    review = outcome.manifest["review"]["synthesis_approval"]
    assert review["approved_by"] == "leela"
    assert review["approved_at"] == "2026-10-29T11:05:00Z"
    assert any(entry.get("actor") == "leela" for entry in review["audit_trail"])
    assert review["binding"]["script_sha256"] == sha256(script).hexdigest()
    assert (
        review["binding"]["manifest_sha256"]
        == result.manifest["review"]["snapshot"]["manifest_sha256"]
    )

    with pytest.raises(ValueError):
        _approve_staged(storage, result)
    assert len(producer.sent) == 1
    assert storage.get_bytes(script_path) == script
    generator.assert_called_once()


@pytest.mark.parametrize(
    "changed", ["script", "script_digest", "manifest_digest", "metadata", "config", "provider"]
)
def test_review_rejects_stale_exact_content_without_sending(reviewed_stage, changed):
    storage, result, generator, producer, script = reviewed_stage
    job_id = result.response["job_id"]
    script_digest = sha256(script).hexdigest()
    manifest_digest = result.manifest["review"]["snapshot"]["manifest_sha256"]
    if changed == "script":
        storage.put_bytes(f"jobs/{job_id}/script.txt", script + b"Changed.", "text/plain")
    elif changed == "script_digest":
        script_digest = "0" * 64
    elif changed == "manifest_digest":
        manifest_digest = "0" * 64
    elif changed == "provider":
        from unittest.mock import patch

        with patch.dict("os.environ", {"AZURE_OPENAI_TTS_DEPLOYMENT": "changed-deployment"}):
            with pytest.raises(ValueError):
                _approve_staged(storage, result)
        assert producer.sent == []
        assert load_manifest(storage, job_id)["review"]["status"] == "pending"
        return
    else:
        manifest = load_manifest(storage, job_id)
        if changed == "metadata":
            manifest["request"]["article_title"] = "Changed editorial title"
        else:
            manifest["request"]["podcast_config"]["host_a"]["voice"] = "changed-voice"
        storage.put_bytes(
            f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json"
        )

    with pytest.raises(ValueError):
        _approve_staged(
            storage,
            result,
            script_sha256=script_digest,
            manifest_sha256=manifest_digest,
        )
    assert producer.sent == []
    assert load_manifest(storage, job_id)["review"]["status"] != "approved"
    generator.assert_called_once()


@pytest.mark.parametrize("decision", ["rejected", "changes_requested"])
def test_negative_review_never_enqueues_or_authorizes_synthesis(reviewed_stage, decision):
    storage, result, generator, producer, script = reviewed_stage
    outcome = process_review_decision(
        result.response["job_id"],
        storage=storage,
        reviewer="leela",
        reviewed_at="2026-10-29T11:05:00Z",
        decision=decision,
    )
    assert outcome.manifest["review"]["status"] == "pending"
    assert outcome.manifest["review"]["synthesis_approval"]["status"] == decision
    assert outcome.manifest["generation"]["tts_synthesis"]["allowed"] is False
    assert producer.sent == []
    assert storage.get_bytes(f"jobs/{result.response['job_id']}/script.txt") == script
    generator.assert_called_once()


@pytest.mark.parametrize("missing", ["script_sha256", "manifest_sha256"])
def test_approval_requires_both_explicit_review_digests(reviewed_stage, missing):
    storage, result, generator, producer, _script = reviewed_stage
    with pytest.raises(ValueError):
        _approve_staged(storage, result, **{missing: None})
    assert producer.sent == []
    assert load_manifest(storage, result.response["job_id"])["review"]["status"] == "pending"
    generator.assert_called_once()


def test_concurrent_human_approval_has_one_durable_enqueue_intent(reviewed_stage, monkeypatch):
    storage, result, generator, producer, _script = reviewed_stage
    send_started = Event()
    release_send = Event()
    original_send = producer.send_message

    def blocked_send(body):
        original_send(body)
        send_started.set()
        assert release_send.wait(5), "concurrent review never released the queue send"

    monkeypatch.setattr(producer, "send_message", blocked_send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_approve_staged, storage, result)
        try:
            assert send_started.wait(5), "first review never reached the queue boundary"
            second = pool.submit(_approve_staged, storage, result)
        finally:
            release_send.set()
        first.result(timeout=5)
        with pytest.raises(ValueError):
            second.result(timeout=5)
    assert len(producer.sent) == 1
    generator.assert_called_once()


def test_ambiguous_review_send_is_never_blindly_retried(reviewed_stage, monkeypatch):
    storage, result, generator, producer, script = reviewed_stage
    original_send = producer.send_message

    def ambiguous_send(body):
        original_send(body)
        raise RuntimeError("queue accepted message but acknowledgement was lost")

    monkeypatch.setattr(producer, "send_message", ambiguous_send)
    _approve_staged(storage, result)
    with pytest.raises(ValueError):
        _approve_staged(storage, result)
    assert len(producer.sent) == 1
    assert storage.get_bytes(f"jobs/{result.response['job_id']}/script.txt") == script
    generator.assert_called_once()


def test_dry_run_approval_cannot_adopt_preview_into_production(tmp_path, monkeypatch):
    producer = RecordingProducer()
    monkeypatch.setattr(queue_module, "create_queue_backend", lambda: producer)
    storage = LocalStorageBackend(tmp_path / "artifacts", "https://example.invalid/artifacts")
    result = run_generation_job(
        {
            "week": "2026-W44",
            "article_url": "https://example.com/weekly/w44",
            "dry_run": True,
        },
        storage=storage,
    )
    before = storage.get_bytes(f"jobs/{result.response['job_id']}/manifest.json")
    with pytest.raises(ValueError):
        _approve_staged(storage, result)
    assert producer.sent == []
    assert storage.get_bytes(f"jobs/{result.response['job_id']}/manifest.json") == before


def test_enqueue_synthesis_job_carries_only_job_id_and_no_secret(caplog) -> None:
    producer = RecordingProducer()
    job_id = "podcast-2026-W23-deadbeef0001"

    with caplog.at_level("INFO"):
        sent = enqueue_synthesis_job(job_id, producer=producer)

    assert sent is True
    assert len(producer.sent) == 1
    body = producer.sent[0]
    assert parse_job_id(body) == job_id

    decoded = queue_module.base64.b64decode(body).decode("utf-8")
    message = json.loads(decoded)
    assert message == {"schema_version": SYNTHESIS_QUEUE_SCHEMA_VERSION, "job_id": job_id}

    for record in caplog.records:
        text = record.getMessage()
        assert "Bearer" not in text
        assert "secret" not in text.lower()


def test_enqueue_synthesis_job_skips_when_queue_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("PODCASTER_STORAGE_QUEUE_URL", raising=False)
    assert enqueue_synthesis_job("podcast-2026-W23-deadbeef0002") is False


def test_enqueue_video_job_carries_only_job_id_with_video_schema(caplog) -> None:
    from podcaster.queue import VIDEO_QUEUE_SCHEMA_VERSION, enqueue_video_job

    producer = RecordingProducer()
    job_id = "podcast-2026-W23-deadbeef0003"

    with caplog.at_level("INFO"):
        sent = enqueue_video_job(job_id, producer=producer)

    assert sent is True
    assert len(producer.sent) == 1
    body = producer.sent[0]
    assert parse_job_id(body) == job_id

    decoded = queue_module.base64.b64decode(body).decode("utf-8")
    message = json.loads(decoded)
    assert message == {"schema_version": VIDEO_QUEUE_SCHEMA_VERSION, "job_id": job_id}

    for record in caplog.records:
        text = record.getMessage()
        assert "Bearer" not in text
        assert "secret" not in text.lower()


def test_enqueue_video_job_skips_when_queue_not_configured(monkeypatch) -> None:
    from podcaster.queue import enqueue_video_job

    monkeypatch.delenv("PODCASTER_STORAGE_QUEUE_URL", raising=False)
    assert enqueue_video_job("podcast-2026-W23-deadbeef0004") is False


def test_enqueue_video_job_diagnostics_mask_url(monkeypatch, caplog) -> None:
    from podcaster.queue import enqueue_video_job

    monkeypatch.setenv(
        "PODCASTER_STORAGE_QUEUE_URL",
        "https://acct.queue.core.windows.net/?sig=SECRETSAS",
    )
    monkeypatch.setenv("PODCASTER_VIDEO_QUEUE", "video-jobs")

    producer = RecordingProducer()
    with caplog.at_level("INFO"):
        enqueue_video_job("podcast-2026-W23-deadbeef0005", producer=producer)

    diag = [r.getMessage() for r in caplog.records if "diagnostics" in r.getMessage()]
    assert diag, "expected a diagnostics log line"
    text = diag[0]
    assert "PODCASTER_STORAGE_QUEUE_URL_set=True" in text
    assert "video-jobs" in text
    assert "SECRETSAS" not in text
    assert "sig=" not in text
