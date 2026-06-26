"""Tests for the durable progress event store (podcaster.progress, issue #469)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from podcaster.progress import (
    MAX_EVENTS,
    PROGRESS_SCHEMA_VERSION,
    PipelineStage,
    emit_progress,
    events_since,
    filter_events_since,
    is_terminal,
    progress_path,
    read_progress,
)


class MemoryStorageBackend:
    """Minimal in-memory storage backend for progress tests."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put_bytes(self, path: str, content: bytes, content_type: str) -> Any:
        self._blobs[path] = content
        return type("SA", (), {"path": path, "size_bytes": len(content)})()

    def get_bytes(self, path: str) -> bytes | None:
        return self._blobs.get(path)

    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> Any:
        updated = update(self._blobs.get(path))
        self._blobs[path] = updated
        return self.put_bytes(path, updated, content_type)


def _at(second: int = 0) -> datetime:
    return datetime(2026, 6, 26, 12, 0, second, tzinfo=timezone.utc)


def test_emit_creates_document_with_schema_and_seq():
    storage = MemoryStorageBackend()
    event = emit_progress(
        storage,
        "job-1",
        stage=PipelineStage.SYNTHESIS,
        phase="recording",
        segment_total=18,
        message="recording 18 segments",
        at=_at(0),
    )

    assert event is not None
    assert event.seq == 1
    assert event.stage == PipelineStage.SYNTHESIS

    document = read_progress(storage, "job-1")
    assert document is not None
    assert document["schema_version"] == PROGRESS_SCHEMA_VERSION
    assert document["job_id"] == "job-1"
    assert document["updated_at"] == "2026-06-26T12:00:00Z"
    assert document["current"]["stage"] == PipelineStage.SYNTHESIS
    assert document["current"]["phase"] == "recording"
    assert "seq" not in document["current"]
    assert len(document["events"]) == 1


def test_seq_is_monotonic_across_emits():
    storage = MemoryStorageBackend()
    emit_progress(storage, "job-1", stage=PipelineStage.QUEUED, at=_at(0))
    emit_progress(storage, "job-1", stage=PipelineStage.SCRIPT, at=_at(1))
    third = emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS, at=_at(2))

    assert third is not None and third.seq == 3
    seqs = [e["seq"] for e in read_progress(storage, "job-1")["events"]]
    assert seqs == [1, 2, 3]


def test_percent_derived_from_segments():
    storage = MemoryStorageBackend()
    event = emit_progress(
        storage,
        "job-1",
        stage=PipelineStage.SYNTHESIS,
        segment_index=12,
        segment_total=18,
        at=_at(0),
    )
    assert event is not None
    assert event.percent == 66.7


def test_explicit_percent_is_clamped():
    storage = MemoryStorageBackend()
    over = emit_progress(storage, "job-1", stage=PipelineStage.MUX, percent=140.0, at=_at(0))
    under = emit_progress(storage, "job-1", stage=PipelineStage.MUX, percent=-5.0, at=_at(1))
    assert over is not None and over.percent == 100.0
    assert under is not None and under.percent == 0.0


def test_events_since_returns_only_newer():
    storage = MemoryStorageBackend()
    for i in range(5):
        emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS, segment_index=i, at=_at(i))

    newer = events_since(storage, "job-1", after_seq=3)
    assert [e["seq"] for e in newer] == [4, 5]
    assert events_since(storage, "job-1", after_seq=5) == []


def test_events_since_missing_job_is_empty():
    storage = MemoryStorageBackend()
    assert events_since(storage, "missing", after_seq=0) == []
    assert read_progress(storage, "missing") is None


def test_is_terminal_detects_completed_and_failed():
    storage = MemoryStorageBackend()
    emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS, at=_at(0))
    assert is_terminal(read_progress(storage, "job-1")) is False
    emit_progress(storage, "job-1", stage=PipelineStage.COMPLETED, at=_at(1))
    assert is_terminal(read_progress(storage, "job-1")) is True

    emit_progress(storage, "job-2", stage=PipelineStage.FAILED, at=_at(0))
    assert is_terminal(read_progress(storage, "job-2")) is True


def test_events_capped_at_max_keeping_newest():
    storage = MemoryStorageBackend()
    total = MAX_EVENTS + 25
    for i in range(total):
        emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS, segment_index=i, at=_at(0))

    events = read_progress(storage, "job-1")["events"]
    assert len(events) == MAX_EVENTS
    # Newest retained, oldest dropped; seq remains monotonic.
    assert events[-1]["seq"] == total
    assert events[0]["seq"] == total - MAX_EVENTS + 1


def test_emit_swallows_storage_errors():
    class BrokenStorage(MemoryStorageBackend):
        def update_bytes(self, path, content_type, update):  # type: ignore[override]
            raise RuntimeError("storage down")

    # Must not raise — progress reporting can never break the pipeline.
    assert emit_progress(BrokenStorage(), "job-1", stage=PipelineStage.SYNTHESIS) is None


def test_corrupt_document_is_recovered():
    storage = MemoryStorageBackend()
    storage._blobs[progress_path("job-1")] = b"not-json{"
    event = emit_progress(storage, "job-1", stage=PipelineStage.SYNTHESIS, at=_at(0))
    assert event is not None and event.seq == 1
    document = read_progress(storage, "job-1")
    assert document["schema_version"] == PROGRESS_SCHEMA_VERSION
    assert len(document["events"]) == 1


def test_document_is_valid_json_bytes():
    storage = MemoryStorageBackend()
    emit_progress(storage, "job-1", stage=PipelineStage.PUBLISH, message="done", at=_at(0))
    raw = storage.get_bytes(progress_path("job-1"))
    assert raw is not None
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["events"][0]["message"] == "done"


def test_emit_recovers_from_malformed_seq_in_existing_events():
    storage = MemoryStorageBackend()
    storage._blobs[progress_path("job-1")] = json.dumps(
        {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "job_id": "job-1",
            "events": [
                {"seq": 1, "stage": "brief"},
                {"seq": "x", "stage": "script"},  # corrupted seq
                {"seq": 3, "stage": "synthesis"},
            ],
        }
    ).encode("utf-8")
    event = emit_progress(storage, "job-1", stage=PipelineStage.COMPOSE, at=_at(0))
    # next_seq derived from highest valid seq (3), not crashing on "x".
    assert event is not None and event.seq == 4


def test_events_since_skips_malformed_seq():
    storage = MemoryStorageBackend()
    storage._blobs[progress_path("job-1")] = json.dumps(
        {
            "events": [
                {"seq": 1, "stage": "brief"},
                {"seq": "bad", "stage": "script"},
                {"seq": 2, "stage": "synthesis"},
            ],
        }
    ).encode("utf-8")
    # No raise; malformed event skipped, valid ones returned.
    out = events_since(storage, "job-1", 0)
    assert [e["seq"] for e in out] == [1, 2]


def test_filter_events_since_filters_and_skips_bad():
    events = [
        {"seq": 1},
        {"seq": "x"},
        {"seq": 5},
        "not-a-dict",
    ]
    assert [e["seq"] for e in filter_events_since(events, 1)] == [5]


def test_derive_percent_clamps_negative_segment_index():
    storage = MemoryStorageBackend()
    event = emit_progress(
        storage,
        "job-1",
        stage=PipelineStage.SYNTHESIS,
        segment_index=-1,
        segment_total=10,
        at=_at(0),
    )
    assert event is not None and event.percent == 0.0
