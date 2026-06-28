"""Unit tests for the per-clip queue message codec (#561, RFC §4)."""

from __future__ import annotations

import base64
import json

import pytest

from podcaster.queue import (
    CLIP_QUEUE_SCHEMA_VERSION,
    encode_clip_message,
    enqueue_clip_job,
    parse_clip_job,
)


class RecordingProducer:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, body: str) -> None:
        self.sent.append(body)


@pytest.mark.parametrize("clip_index", [0, 1, 7, 99, 1000])
def test_encode_parse_round_trip(clip_index: int) -> None:
    job_id = "podcast-2026-W23-deadbeef0001"
    body = encode_clip_message(job_id, clip_index)
    assert parse_clip_job(body) == (job_id, clip_index)


def test_encode_emits_base64_json_with_clip_schema() -> None:
    job_id = "podcast-2026-W23-deadbeef0002"
    body = encode_clip_message(job_id, 7)

    decoded = base64.b64decode(body).decode("utf-8")
    message = json.loads(decoded)
    assert message == {
        "schema_version": CLIP_QUEUE_SCHEMA_VERSION,
        "job_id": job_id,
        "clip_index": 7,
    }


def test_encode_strips_job_id_whitespace() -> None:
    body = encode_clip_message("  job-1  ", 3)
    assert parse_clip_job(body) == ("job-1", 3)


def test_parse_accepts_raw_json() -> None:
    raw = json.dumps(
        {
            "schema_version": CLIP_QUEUE_SCHEMA_VERSION,
            "job_id": "job-raw",
            "clip_index": 4,
        }
    )
    assert parse_clip_job(raw) == ("job-raw", 4)


def test_parse_accepts_extra_fields() -> None:
    raw = json.dumps({"job_id": "job-x", "clip_index": 2, "extra": "ignored"})
    assert parse_clip_job(raw) == ("job-x", 2)


@pytest.mark.parametrize("clip_index", [-1, -100])
def test_encode_rejects_negative_clip_index(clip_index: int) -> None:
    with pytest.raises(ValueError):
        encode_clip_message("job-1", clip_index)


@pytest.mark.parametrize("clip_index", [None, "3", 1.5, True])
def test_encode_rejects_non_int_clip_index(clip_index) -> None:
    with pytest.raises(ValueError):
        encode_clip_message("job-1", clip_index)


@pytest.mark.parametrize("job_id", ["", "   ", None, 123])
def test_encode_rejects_bad_job_id(job_id) -> None:
    with pytest.raises(ValueError):
        encode_clip_message(job_id, 1)


@pytest.mark.parametrize("body", ["", "   ", "not-base64-or-json", "{}", "[]"])
def test_parse_rejects_malformed_body(body: str) -> None:
    with pytest.raises(ValueError):
        parse_clip_job(body)


def test_parse_rejects_missing_clip_index() -> None:
    raw = json.dumps({"job_id": "job-1"})
    with pytest.raises(ValueError):
        parse_clip_job(raw)


def test_parse_rejects_missing_job_id() -> None:
    raw = json.dumps({"clip_index": 1})
    with pytest.raises(ValueError):
        parse_clip_job(raw)


def test_parse_rejects_negative_clip_index() -> None:
    raw = json.dumps({"job_id": "job-1", "clip_index": -1})
    with pytest.raises(ValueError):
        parse_clip_job(raw)


def test_parse_rejects_bool_clip_index() -> None:
    raw = json.dumps({"job_id": "job-1", "clip_index": True})
    with pytest.raises(ValueError):
        parse_clip_job(raw)


def test_enqueue_clip_job_sends_only_job_id_and_clip_index(caplog) -> None:
    producer = RecordingProducer()
    job_id = "podcast-2026-W23-deadbeef0003"

    with caplog.at_level("INFO"):
        sent = enqueue_clip_job(job_id, 5, producer=producer)

    assert sent is True
    assert len(producer.sent) == 1
    assert parse_clip_job(producer.sent[0]) == (job_id, 5)

    for record in caplog.records:
        text = record.getMessage()
        assert "Bearer" not in text
        assert "secret" not in text.lower()


def test_enqueue_clip_job_skips_when_queue_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("PODCASTER_STORAGE_QUEUE_URL", raising=False)
    assert enqueue_clip_job("podcast-2026-W23-deadbeef0004", 0) is False


def test_create_clip_queue_backend_honors_env(monkeypatch) -> None:
    from podcaster.queue import create_clip_queue_backend

    monkeypatch.delenv("PODCASTER_STORAGE_QUEUE_URL", raising=False)
    assert create_clip_queue_backend() is None

    monkeypatch.setenv("PODCASTER_STORAGE_QUEUE_URL", "https://acct.queue.core.windows.net/")
    monkeypatch.setenv("PODCASTER_VIDEO_CLIP_QUEUE", "custom-clip-queue")
    backend = create_clip_queue_backend()
    assert backend is not None
    assert backend._queue_url.endswith("/custom-clip-queue")
