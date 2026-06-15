from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcaster import queue as queue_module
from podcaster.costs import monthly_ledger_path
from podcaster.generation import manifest_bytes
from podcaster.jobs import run_generation_job
from podcaster.queue import (
    SYNTHESIS_QUEUE_SCHEMA_VERSION,
    enqueue_synthesis_job,
    parse_job_id,
)
from podcaster.storage import LocalStorageBackend
from podcaster.validation import RESPONSE_KEYS

LEGACY_STRING_PAYLOAD = {
    "week": "2026-W23",
    "article_url": "https://example.com/article",
    "source_artifacts": ["https://example.com/source-1", "https://example.com/source-2"],
}

SQUADSCOPE_OBJECT_PAYLOAD = {
    "week": "2026-W23",
    "article_url": "https://example.com/article",
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
def test_async_202_shape_and_single_enqueue_for_both_contracts(label: str, payload: dict) -> None:
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
    assert calls == [result.response["job_id"]]


def test_enqueue_not_invoked_when_budget_gate_fails() -> None:
    storage = _local_storage("gate-fail")
    storage.put_bytes(
        monthly_ledger_path("2026-06"),
        manifest_bytes(
            {
                "schema_version": "squadscope-podcaster-monthly-cost-ledger-v1",
                "month": "2026-06",
                "episodes": [
                    {"job_id": f"existing-{index}", "week": f"2026-W2{index}", "estimated_total_usd": "0.00"}
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


def test_enqueue_failure_does_not_break_202_contract() -> None:
    storage = _local_storage("enqueue-error")

    def boom(_job_id: str) -> bool:
        raise RuntimeError("queue send exploded")

    result = run_generation_job(
        LEGACY_STRING_PAYLOAD,
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
        enqueue=boom,
    )

    assert result.response["status"] == "accepted"
    assert result.response["errors"] == []
    assert "synthesis enqueue failed; job remains staged until synthesis is replayed" in result.response["warnings"]
    assert result.manifest["generation"]["synthesis_queue"]["status"] == "failed"


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
