"""Unit tests for the scale-out recorder entrypoint (#562, RFC §3/§4/§5)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from podcaster.queue import QueueMessage, encode_clip_message
from podcaster.storage import LocalStorageBackend
from podcaster.video import recorder
from podcaster.video.clipset import (
    Clipset,
    clip_blob_path,
    clip_manifest_blob_path,
    clipset_blob_path,
)
from podcaster.video.recorder import (
    MAX_DEQUEUE_COUNT,
    OUTCOME_FALLBACK,
    OUTCOME_RECORDED,
    OUTCOME_RETRY,
    OUTCOME_SKIPPED,
    RecordResult,
    process_clip_message,
    record_clip,
    write_fallback_manifest,
)
from podcaster.video.sync_plan import RepoReference, VideoSegment

JOB_ID = "podcast-2026-W23-deadbeef"


class FakeQueue:
    def __init__(self) -> None:
        self.deleted: list[QueueMessage] = []
        self.inbox: list[QueueMessage] = []

    def receive_messages(self, max_messages: int = 1, *, visibility_timeout: int = 600):
        batch = self.inbox[:max_messages]
        self.inbox = self.inbox[max_messages:]
        return batch

    def delete_message(self, message: QueueMessage) -> None:
        self.deleted.append(message)


def _scratch(tmp_path: Path) -> LocalStorageBackend:
    root = tmp_path / "scratch"
    shutil.rmtree(root, ignore_errors=True)
    return LocalStorageBackend(root, "https://example.invalid/scratch")


def _stage_clipset(scratch: LocalStorageBackend) -> None:
    segments = [
        VideoSegment(start_seconds=0.0, duration_seconds=30.0),
        VideoSegment(
            start_seconds=30.0,
            duration_seconds=45.0,
            repo=RepoReference(owner="octo", name="api"),
        ),
    ]
    clipset = Clipset.from_segments(JOB_ID, segments)
    scratch.put_bytes(clipset_blob_path(JOB_ID), clipset.to_json_bytes(), "application/json")


def _recorder(payload: bytes = b"clip-bytes", *, is_fallback: bool = False):
    calls: list[VideoSegment] = []

    def _record(segment: VideoSegment, output_dir: Path) -> RecordResult:
        calls.append(segment)
        path = output_dir / "clip.webm"
        path.write_bytes(payload)
        return RecordResult(video_path=path, duration_ms=12345, is_fallback=is_fallback)

    return _record, calls


def _message(clip_index: int, *, dequeue_count: int = 1) -> QueueMessage:
    return QueueMessage(
        message_id=f"m-{clip_index}",
        pop_receipt="pr",
        body=encode_clip_message(JOB_ID, clip_index),
        dequeue_count=dequeue_count,
    )


def test_record_clip_writes_clip_then_manifest(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    record, calls = _recorder()

    outcome = record_clip(JOB_ID, 1, scratch=scratch, record_segment=record)

    assert outcome.status == OUTCOME_RECORDED
    assert len(calls) == 1
    assert scratch.blob_exists(clip_blob_path(JOB_ID, 1))
    manifest_raw = scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1))
    manifest = json.loads(manifest_raw)
    assert manifest["clip_id"] == "clip-001"
    assert manifest["status"] == "success"
    assert manifest["is_fallback"] is False
    assert manifest["repo_url"] == "https://github.com/octo/api"
    assert manifest["duration_ms"] == 12345


def test_record_clip_skips_when_manifest_present(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    record, calls = _recorder()
    record_clip(JOB_ID, 1, scratch=scratch, record_segment=record)

    record2, calls2 = _recorder()
    outcome = record_clip(JOB_ID, 1, scratch=scratch, record_segment=record2)

    assert outcome.status == OUTCOME_SKIPPED
    assert calls2 == []  # never re-recorded


def test_record_clip_rerecords_when_clip_present_but_manifest_missing(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    # Simulate a recorder that died after the .webm but before the manifest.
    scratch.put_bytes(clip_blob_path(JOB_ID, 1), b"torn", "video/webm")
    assert not scratch.blob_exists(clip_manifest_blob_path(JOB_ID, 1))

    record, calls = _recorder(payload=b"fresh-bytes")
    outcome = record_clip(JOB_ID, 1, scratch=scratch, record_segment=record)

    assert outcome.status == OUTCOME_RECORDED
    assert len(calls) == 1  # re-recorded
    assert scratch.get_bytes(clip_blob_path(JOB_ID, 1)) == b"fresh-bytes"
    assert scratch.blob_exists(clip_manifest_blob_path(JOB_ID, 1))


def test_record_clip_fallback_segment_marks_status_fallback(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    record, _ = _recorder(is_fallback=True)

    record_clip(JOB_ID, 0, scratch=scratch, record_segment=record)

    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 0)))
    assert manifest["is_fallback"] is True
    assert manifest["status"] == "fallback"


def test_record_clip_out_of_plan_index_raises(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    record, _ = _recorder()
    with pytest.raises(KeyError):
        record_clip(JOB_ID, 99, scratch=scratch, record_segment=record)


def test_record_clip_size_verify_failure_deletes_clip_and_raises(tmp_path, monkeypatch) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    record, _ = _recorder()

    monkeypatch.setattr(recorder, "_verify_size", lambda *a, **k: False)
    with pytest.raises(RuntimeError):
        record_clip(JOB_ID, 1, scratch=scratch, record_segment=record)

    assert not scratch.blob_exists(clip_blob_path(JOB_ID, 1))
    assert not scratch.blob_exists(clip_manifest_blob_path(JOB_ID, 1))


def test_write_fallback_manifest_is_terminal_and_never_overwrites(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)

    outcome = write_fallback_manifest(JOB_ID, 1, scratch=scratch, reason="poison")
    assert outcome.status == OUTCOME_FALLBACK
    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert manifest["is_fallback"] is True
    assert manifest["status"] == "fallback"
    assert manifest["failure_reason"] == "poison"

    # A second call must not overwrite the existing terminal manifest.
    scratch.put_bytes(
        clip_manifest_blob_path(JOB_ID, 1),
        b'{"clip_id":"clip-001","sentinel":true}',
        "application/json",
    )
    write_fallback_manifest(JOB_ID, 1, scratch=scratch, reason="again")
    preserved = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert preserved.get("sentinel") is True


def test_process_message_malformed_body_is_deleted(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    queue = FakeQueue()
    message = QueueMessage(
        message_id="bad-1",
        pop_receipt="pr",
        body="not-base64-or-json",
        dequeue_count=1,
    )

    outcome = process_clip_message(message, scratch=scratch, queue=queue)

    assert outcome.status == recorder.OUTCOME_MALFORMED
    assert queue.deleted == [message]  # poison removed, no crash-loop


def test_write_manifest_if_absent_never_overwrites(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    path = "video-jobs/job-x/clips/000.manifest.json"

    assert recorder._write_manifest_if_absent(scratch, path, b"first", "application/json")
    # A second writer with different bytes must NOT overwrite the terminal blob.
    assert not recorder._write_manifest_if_absent(scratch, path, b"second", "application/json")
    assert scratch.get_bytes(path) == b"first"


def test_record_clip_skips_if_manifest_appears_mid_record(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)

    def _record(segment, output_dir):
        # Simulate a concurrent recorder finishing this clip while we record.
        scratch.put_bytes(
            clip_manifest_blob_path(JOB_ID, 1),
            b'{"clip_id":"clip-001","winner":true}',
            "application/json",
        )
        path = output_dir / "clip.webm"
        path.write_bytes(b"late-bytes")
        return RecordResult(video_path=path, duration_ms=1, is_fallback=False)

    outcome = record_clip(JOB_ID, 1, scratch=scratch, record_segment=_record)

    assert outcome.status == OUTCOME_SKIPPED
    # The authoritative manifest from the "winner" is preserved untouched.
    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert manifest.get("winner") is True


def test_process_message_records_and_deletes(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    record, calls = _recorder()
    queue = FakeQueue()
    message = _message(1)

    outcome = process_clip_message(message, scratch=scratch, queue=queue, record_segment=record)

    assert outcome.status == OUTCOME_RECORDED
    assert queue.deleted == [message]
    assert len(calls) == 1


def test_process_message_poison_writes_fallback_and_deletes(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    record, calls = _recorder()
    queue = FakeQueue()
    message = _message(1, dequeue_count=MAX_DEQUEUE_COUNT)

    outcome = process_clip_message(message, scratch=scratch, queue=queue, record_segment=record)

    assert outcome.status == OUTCOME_FALLBACK
    assert calls == []  # never attempted a real record
    assert queue.deleted == [message]
    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert manifest["is_fallback"] is True


def test_process_message_transient_error_leaves_message(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    queue = FakeQueue()

    def _boom(segment, output_dir):
        raise RuntimeError("browser crashed")

    message = _message(1)
    outcome = process_clip_message(message, scratch=scratch, queue=queue, record_segment=_boom)

    assert outcome.status == OUTCOME_RETRY
    assert queue.deleted == []  # left for redelivery / eventual poison


def test_drain_processes_until_empty(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    queue = FakeQueue()
    queue.inbox = [_message(0), _message(1)]
    env = {"PODCASTER_RECORDER_FAKE_BROWSER": "1"}

    outcomes = recorder.drain(queue, scratch, env=env)

    assert [o.status for o in outcomes] == [OUTCOME_RECORDED, OUTCOME_RECORDED]
    assert len(queue.deleted) == 2
    assert scratch.blob_exists(clip_manifest_blob_path(JOB_ID, 0))
    assert scratch.blob_exists(clip_manifest_blob_path(JOB_ID, 1))


def test_fake_browser_env_selects_fake_recorder(monkeypatch) -> None:
    monkeypatch.setenv("PODCASTER_RECORDER_FAKE_BROWSER", "1")
    fn = recorder._select_record_segment({"PODCASTER_RECORDER_FAKE_BROWSER": "1"})
    assert fn is recorder._fake_record_segment

    fn2 = recorder._select_record_segment({})
    assert fn2 is recorder._production_record_segment


def test_fake_record_segment_writes_clip(tmp_path) -> None:
    segment = VideoSegment(start_seconds=0.0, duration_seconds=2.0)
    result = recorder._fake_record_segment(segment, tmp_path)
    assert Path(result.video_path).exists()
    assert result.duration_ms == 2000
    assert result.is_fallback is False
