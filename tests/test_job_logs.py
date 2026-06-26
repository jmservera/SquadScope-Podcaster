"""Tests for the durable structured per-job log store (podcaster.job_logs)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from podcaster.job_logs import (
    LOGS_SCHEMA_VERSION,
    MAX_RECORDS,
    LogLevel,
    emit_log,
    filter_records,
    logs_path,
    read_logs,
)


class MemoryStorageBackend:
    """Minimal in-memory storage for testing the log store."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put_bytes(self, path: str, content: bytes, content_type: str) -> Any:
        self._blobs[path] = content
        return type("SA", (), {"path": path})()

    def get_bytes(self, path: str) -> bytes | None:
        return self._blobs.get(path)

    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> Any:
        updated = update(self._blobs.get(path))
        self._blobs[path] = updated
        return self.put_bytes(path, updated, content_type)


class FailingStorageBackend(MemoryStorageBackend):
    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> Any:
        raise RuntimeError("storage unavailable")


def test_logs_path():
    assert logs_path("job-1") == "jobs/job-1/logs.json"


class TestLogLevel:
    def test_normalize_known(self):
        assert LogLevel.normalize("ERROR") == LogLevel.ERROR
        assert LogLevel.normalize("Info") == LogLevel.INFO

    def test_normalize_aliases(self):
        assert LogLevel.normalize("warn") == LogLevel.WARNING
        assert LogLevel.normalize("critical") == LogLevel.ERROR
        assert LogLevel.normalize("fatal") == LogLevel.ERROR
        assert LogLevel.normalize("trace") == LogLevel.DEBUG

    def test_normalize_unknown_defaults_info(self):
        assert LogLevel.normalize("verbose") == LogLevel.INFO
        assert LogLevel.normalize(None) == LogLevel.INFO
        assert LogLevel.normalize(123) == LogLevel.INFO

    def test_rank_ordering(self):
        assert LogLevel.rank("debug") < LogLevel.rank("info") < LogLevel.rank("warning") < LogLevel.rank("error")


class TestEmitLog:
    def test_emit_creates_document(self):
        storage = MemoryStorageBackend()
        rec = emit_log(storage, "job-1", message="hello", level="info")
        assert rec is not None
        assert rec.seq == 1
        doc = read_logs(storage, "job-1")
        assert doc is not None
        assert doc["schema_version"] == LOGS_SCHEMA_VERSION
        assert doc["job_id"] == "job-1"
        assert len(doc["records"]) == 1
        assert doc["records"][0]["message"] == "hello"
        assert doc["records"][0]["level"] == "info"

    def test_emit_monotonic_seq(self):
        storage = MemoryStorageBackend()
        emit_log(storage, "job-1", message="a")
        emit_log(storage, "job-1", message="b")
        r3 = emit_log(storage, "job-1", message="c")
        assert r3 is not None and r3.seq == 3
        doc = read_logs(storage, "job-1")
        assert [r["seq"] for r in doc["records"]] == [1, 2, 3]

    def test_emit_normalizes_level(self):
        storage = MemoryStorageBackend()
        rec = emit_log(storage, "job-1", message="x", level="WARN")
        assert rec is not None and rec.level == LogLevel.WARNING

    def test_emit_includes_optional_fields(self):
        storage = MemoryStorageBackend()
        emit_log(
            storage,
            "job-1",
            message="task done",
            level="info",
            task_id="tts-3",
            stage="synthesis",
            context={"segments": 5},
        )
        rec = read_logs(storage, "job-1")["records"][0]
        assert rec["task_id"] == "tts-3"
        assert rec["stage"] == "synthesis"
        assert rec["context"] == {"segments": 5}

    def test_emit_omits_none_fields(self):
        storage = MemoryStorageBackend()
        emit_log(storage, "job-1", message="bare")
        rec = read_logs(storage, "job-1")["records"][0]
        assert "task_id" not in rec
        assert "stage" not in rec
        assert "context" not in rec

    def test_emit_explicit_timestamp(self):
        storage = MemoryStorageBackend()
        moment = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
        rec = emit_log(storage, "job-1", message="x", at=moment)
        assert rec is not None and rec.at == "2026-06-26T12:00:00Z"

    def test_emit_bounded_to_max_records(self):
        storage = MemoryStorageBackend()
        for i in range(MAX_RECORDS + 25):
            emit_log(storage, "job-1", message=f"m{i}")
        doc = read_logs(storage, "job-1")
        assert len(doc["records"]) == MAX_RECORDS
        # newest retained, seq stays monotonic
        assert doc["records"][-1]["message"] == f"m{MAX_RECORDS + 24}"

    def test_emit_swallows_storage_failure(self):
        storage = FailingStorageBackend()
        assert emit_log(storage, "job-1", message="x") is None

    def test_emit_recovers_from_corrupt_document(self):
        storage = MemoryStorageBackend()
        storage.put_bytes(logs_path("job-1"), b"not json", "application/json")
        rec = emit_log(storage, "job-1", message="ok")
        assert rec is not None and rec.seq == 1


class TestReadLogs:
    def test_read_absent_returns_none(self):
        assert read_logs(MemoryStorageBackend(), "missing") is None

    def test_read_corrupt_returns_empty_document(self):
        storage = MemoryStorageBackend()
        storage.put_bytes(logs_path("job-1"), b"{bad", "application/json")
        doc = read_logs(storage, "job-1")
        assert doc is not None and doc["records"] == []


class TestFilterRecords:
    def _records(self):
        return [
            {"seq": 1, "level": "debug", "message": "verbose detail", "stage": "brief"},
            {"seq": 2, "level": "info", "message": "recording 5 segments", "stage": "synthesis"},
            {"seq": 3, "level": "warning", "message": "music skipped", "task_id": "mix-1"},
            {"seq": 4, "level": "error", "message": "synthesis failed", "stage": "synthesis"},
        ]

    def test_no_filter_returns_all(self):
        recs = self._records()
        assert filter_records(recs) == recs

    def test_min_level_warning(self):
        out = filter_records(self._records(), level="warning")
        assert [r["seq"] for r in out] == [3, 4]

    def test_min_level_error(self):
        out = filter_records(self._records(), level="error")
        assert [r["seq"] for r in out] == [4]

    def test_search_matches_message(self):
        out = filter_records(self._records(), search="recording")
        assert [r["seq"] for r in out] == [2]

    def test_search_matches_task_id(self):
        out = filter_records(self._records(), search="mix-1")
        assert [r["seq"] for r in out] == [3]

    def test_search_case_insensitive(self):
        out = filter_records(self._records(), search="FAILED")
        assert [r["seq"] for r in out] == [4]

    def test_level_and_search_combined(self):
        out = filter_records(self._records(), level="warning", search="synthesis")
        assert [r["seq"] for r in out] == [4]

    def test_skips_malformed_records(self):
        recs = self._records() + ["not a dict", 42]
        out = filter_records(recs, level="info")
        assert all(isinstance(r, dict) for r in out)
