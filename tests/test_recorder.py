"""Unit tests for the scale-out recorder entrypoint (#562, RFC §3/§4/§5)."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from podcaster.queue import QueueMessage, encode_clip_message
from podcaster.storage import LocalStorageBackend
from podcaster.video import recorder
from podcaster.video.budget import VideoStageBudget
from podcaster.video.clipset import (
    Clipset,
    clip_blob_path,
    clip_content_blob_path,
    clip_manifest_blob_path,
    clipset_blob_path,
)
from podcaster.video.process import MediaEvidence, ProbeEvidence
from podcaster.video.recorder import (
    FAILED_EXECUTION_LIMIT,
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
    clipset = Clipset.from_segments(
        JOB_ID,
        segments,
        budget=VideoStageBudget.start().projection,
    )
    scratch.put_bytes(clipset_blob_path(JOB_ID), clipset.to_json_bytes(), "application/json")


def _recorder(payload: bytes = b"clip-bytes", *, is_fallback: bool = False):
    calls: list[VideoSegment] = []

    def _record(segment: VideoSegment, output_dir: Path) -> RecordResult:
        calls.append(segment)
        path = output_dir / "clip.webm"
        path.write_bytes(payload)
        return RecordResult(video_path=path, duration_ms=12345, is_fallback=is_fallback)

    return _record, calls


def _media(path: Path, _timeout: float) -> MediaEvidence:
    payload = path.read_bytes()
    return MediaEvidence(
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        probe=ProbeEvidence(format_name="matroska,webm", duration_seconds=1.0),
    )


def _fallback(path: Path, _timeout: float) -> MediaEvidence:
    path.write_bytes(b"deterministic-fallback")
    return _media(path, 1.0)


def _message(clip_index: int, *, dequeue_count: int = 1) -> QueueMessage:
    return QueueMessage(
        message_id=f"m-{clip_index}",
        pop_receipt="pr",
        body=encode_clip_message(JOB_ID, clip_index),
        dequeue_count=dequeue_count,
    )


@pytest.fixture(autouse=True)
def _stub_recording_probe(monkeypatch):
    monkeypatch.setattr(recorder, "_validate_media", _media)


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
    assert manifest["media_blob_path"].endswith(f"/{manifest['media']['sha256']}.webm")


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


def test_record_clip_persists_recording_outcome_metadata(tmp_path) -> None:
    """has_pages/website_url/is_removed/recovery_path round-trip to the manifest.

    The editor reads these extras to reproduce identical compose output (#563).
    """
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)

    def _record(segment: VideoSegment, output_dir: Path) -> RecordResult:
        path = output_dir / "clip.webm"
        path.write_bytes(b"clip-bytes")
        return RecordResult(
            video_path=path,
            duration_ms=12345,
            is_fallback=False,
            has_pages=True,
            website_url="https://octo.github.io/api",
            is_removed=False,
            recovery_path="website",
        )

    record_clip(JOB_ID, 1, scratch=scratch, record_segment=_record)

    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert manifest["has_pages"] is True
    assert manifest["website_url"] == "https://octo.github.io/api"
    assert manifest["is_removed"] is False
    assert manifest["recovery_path"] == "website"


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

    outcome = write_fallback_manifest(
        JOB_ID, 1, scratch=scratch, reason="poison", renderer=_fallback
    )
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
    write_fallback_manifest(JOB_ID, 1, scratch=scratch, reason="again", renderer=_fallback)
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

    outcome = process_clip_message(
        message,
        scratch=scratch,
        queue=queue,
        record_segment=record,
        fallback_renderer=_fallback,
    )

    assert outcome.status == OUTCOME_RECORDED
    assert queue.deleted == [message]
    assert len(calls) == 1
    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert manifest["attempts"]["executions"][0]["status"] == "succeeded"


def test_process_message_poison_writes_fallback_and_deletes(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    record, calls = _recorder()
    queue = FakeQueue()
    message = _message(1, dequeue_count=MAX_DEQUEUE_COUNT)

    outcome = process_clip_message(
        message,
        scratch=scratch,
        queue=queue,
        record_segment=record,
        fallback_renderer=_fallback,
    )

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
    assert fn2 is not recorder._production_record_segment
    assert callable(fn2)


def test_fake_record_segment_writes_clip(tmp_path) -> None:
    segment = VideoSegment(start_seconds=0.0, duration_seconds=2.0)
    result = recorder._fake_record_segment(segment, tmp_path)
    assert Path(result.video_path).exists()
    assert result.duration_ms == 2000
    assert result.is_fallback is False


def test_fake_record_segment_caps_long_duration(tmp_path, monkeypatch) -> None:
    # An over-long segment is reported at the per-clip recording cap (issue #592)
    # so the manifest's duration_ms matches the realized (truncated) clip and the
    # editor's EDL never seeks past the clip's end.
    import podcaster.video.video_gen as vg

    monkeypatch.setattr(vg, "MAX_CLIP_RECORD_SECONDS", 600)
    segment = VideoSegment(start_seconds=0.0, duration_seconds=1440.0)
    result = recorder._fake_record_segment(segment, tmp_path)
    assert result.duration_ms == 600_000


def test_two_failed_dequeued_executions_terminalize_without_waiting_for_poison(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    queue = FakeQueue()

    def _crash(_segment, _output_dir):
        raise RuntimeError("browser crashed")

    first = process_clip_message(
        _message(1, dequeue_count=1),
        scratch=scratch,
        queue=queue,
        record_segment=_crash,
        fallback_renderer=_fallback,
    )
    second_message = _message(1, dequeue_count=2)
    second = process_clip_message(
        second_message,
        scratch=scratch,
        queue=queue,
        record_segment=_crash,
        fallback_renderer=_fallback,
    )

    assert first.status == OUTCOME_RETRY
    assert second.status == OUTCOME_FALLBACK
    assert FAILED_EXECUTION_LIMIT == 2
    assert queue.deleted == [second_message]
    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert manifest["status"] == "fallback"
    assert len([e for e in manifest["attempts"]["executions"] if e["status"] == "failed"]) == 2


def test_overlapping_delivery_does_not_displace_active_successful_recorder(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)
    outer_queue = FakeQueue()
    inner_queue = FakeQueue()
    inner_outcomes = []

    def _outer_record(_segment, output_dir):
        def _inner_crash(_inner_segment, _inner_output_dir):
            raise RuntimeError("overlapping recorder crashed")

        inner_outcomes.append(
            process_clip_message(
                _message(1, dequeue_count=2),
                scratch=scratch,
                queue=inner_queue,
                record_segment=_inner_crash,
                fallback_renderer=_fallback,
            )
        )
        path = output_dir / "clip.webm"
        path.write_bytes(b"active-recorder-wins")
        return RecordResult(video_path=path, duration_ms=1000)

    outer_message = _message(1, dequeue_count=1)
    outer = process_clip_message(
        outer_message,
        scratch=scratch,
        queue=outer_queue,
        record_segment=_outer_record,
        fallback_renderer=_fallback,
    )

    assert inner_outcomes[0].status == OUTCOME_RETRY
    assert inner_queue.deleted == []
    assert outer.status == OUTCOME_RECORDED
    assert outer_queue.deleted == [outer_message]
    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert manifest["status"] == "success"
    statuses = {item["key"]: item["status"] for item in manifest["attempts"]["executions"]}
    assert statuses == {"m-1:1": "succeeded", "m-1:2": "failed"}


def test_first_admission_survives_redelivery_and_720_second_deadline(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    segments = [VideoSegment(start_seconds=0.0, duration_seconds=30.0)]
    clipset = Clipset.from_segments(
        JOB_ID,
        segments,
        budget=VideoStageBudget.start(now_utc=started, utcnow=lambda: started).projection,
    )
    scratch.put_bytes(clipset_blob_path(JOB_ID), clipset.to_json_bytes(), "application/json")
    now = {"utc": started + timedelta(seconds=100), "mono": 10.0}

    def _utcnow():
        return now["utc"]

    def _mono():
        return now["mono"]

    def _crash(_segment, _output_dir):
        raise RuntimeError("crash")

    first = process_clip_message(
        _message(0, dequeue_count=1),
        scratch=scratch,
        queue=FakeQueue(),
        record_segment=_crash,
        env={
            "VIDEO_MAX_CLIP_RECORD_SECONDS": "0",
            "PODCASTER_CLIP_VISIBILITY_TIMEOUT": "900",
            "PODCASTER_RECORDER_TIMEOUT": "900",
        },
        utcnow=_utcnow,
        monotonic=_mono,
        fallback_renderer=_fallback,
    )
    admission_before = scratch.get_bytes(recorder.clip_admission_blob_path(JOB_ID, 0))
    now["utc"] = started + timedelta(seconds=820)
    now["mono"] += 720
    calls: list[int] = []

    def _must_not_launch(_segment, _output_dir):
        calls.append(1)
        raise AssertionError("browser launched after clip deadline")

    queue = FakeQueue()
    second = process_clip_message(
        _message(0, dequeue_count=2),
        scratch=scratch,
        queue=queue,
        record_segment=_must_not_launch,
        env={
            "VIDEO_MAX_CLIP_RECORD_SECONDS": "0",
            "PODCASTER_CLIP_VISIBILITY_TIMEOUT": "900",
            "PODCASTER_RECORDER_TIMEOUT": "900",
        },
        utcnow=_utcnow,
        monotonic=_mono,
        fallback_renderer=_fallback,
    )

    assert first.status == OUTCOME_RETRY
    assert second.status == OUTCOME_FALLBACK
    assert calls == []
    assert scratch.get_bytes(recorder.clip_admission_blob_path(JOB_ID, 0)) == admission_before


def test_local_monotonic_skew_stops_slow_capture_before_finalization(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    started = datetime.now(timezone.utc)
    segments = [VideoSegment(start_seconds=0.0, duration_seconds=30.0)]
    clipset = Clipset.from_segments(
        JOB_ID,
        segments,
        budget=VideoStageBudget.start(now_utc=started, utcnow=lambda: started).projection,
    )
    scratch.put_bytes(clipset_blob_path(JOB_ID), clipset.to_json_bytes(), "application/json")
    clock = {"utc": started, "mono": 0.0}

    def _slow(_segment, output_dir):
        path = output_dir / "clip.webm"
        path.write_bytes(b"late-recording")
        clock["mono"] += 601
        return RecordResult(video_path=path, duration_ms=1000)

    queue = FakeQueue()
    outcome = process_clip_message(
        _message(0),
        scratch=scratch,
        queue=queue,
        record_segment=_slow,
        utcnow=lambda: clock["utc"],
        monotonic=lambda: clock["mono"],
        fallback_renderer=_fallback,
    )

    assert outcome.status == OUTCOME_FALLBACK
    assert queue.deleted
    assert json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 0)))["status"] == "fallback"


def test_parent_t_plus_1200_denies_browser_launch(tmp_path) -> None:
    scratch = _scratch(tmp_path)
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clipset = Clipset.from_segments(
        JOB_ID,
        [VideoSegment(start_seconds=0.0, duration_seconds=30.0)],
        budget=VideoStageBudget.start(now_utc=started, utcnow=lambda: started).projection,
    )
    scratch.put_bytes(clipset_blob_path(JOB_ID), clipset.to_json_bytes(), "application/json")
    now = started + timedelta(seconds=1200)
    launched: list[int] = []

    outcome = process_clip_message(
        _message(0),
        scratch=scratch,
        queue=FakeQueue(),
        record_segment=lambda *_args: launched.append(1),
        utcnow=lambda: now,
        monotonic=lambda: 1200.0,
        fallback_renderer=_fallback,
    )

    assert outcome.status == OUTCOME_FALLBACK
    assert launched == []


def test_static_fallback_is_deterministic_probe_valid_and_browser_free(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        recorder,
        "_production_record_segment",
        lambda *_args, **_kwargs: pytest.fail("Chromium path used"),
    )
    first = tmp_path / "first.webm"
    second = tmp_path / "second.webm"

    first_evidence = recorder._render_static_fallback(first, 30)
    second_evidence = recorder._render_static_fallback(second, 30)

    assert first.read_bytes() == second.read_bytes()
    assert first_evidence == second_evidence
    assert first_evidence.probe.format_name == "matroska,webm"
    assert first_evidence.probe.duration_seconds == pytest.approx(1.0)


@pytest.mark.parametrize(
    "renderer",
    [
        lambda _path, _timeout: (_ for _ in ()).throw(FileNotFoundError("asset")),
        lambda _path, _timeout: (_ for _ in ()).throw(TimeoutError("ffmpeg hung")),
    ],
)
def test_missing_asset_or_renderer_timeout_is_recording_insufficient(tmp_path, renderer) -> None:
    scratch = _scratch(tmp_path)
    _stage_clipset(scratch)

    outcome = write_fallback_manifest(
        JOB_ID,
        1,
        scratch=scratch,
        reason="fanin_deadline_reached",
        renderer=renderer,
    )

    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert outcome.status == recorder.OUTCOME_INSUFFICIENT
    assert manifest["status"] == recorder.STATUS_RECORDING_INSUFFICIENT
    assert "media_blob_path" not in manifest


def test_owned_browser_timeout_removes_partial_capture(tmp_path, monkeypatch) -> None:
    output = tmp_path / "owned"
    output.mkdir()
    partial = output / "partial.webm"

    def _hung(*_args, **_kwargs):
        partial.write_bytes(b"partial")
        raise TimeoutError("browser hung")

    monkeypatch.setattr(recorder, "run_owned_process", _hung)

    with pytest.raises(TimeoutError, match="browser hung"):
        recorder._owned_production_record_segment(
            VideoSegment(start_seconds=0.0, duration_seconds=1.0),
            output,
            timeout_seconds=1,
        )
    assert not partial.exists()


def test_browser_deadline_uses_earliest_hard_visibility_replica_clip_and_parent() -> None:
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clipset = Clipset.from_segments(
        JOB_ID,
        [VideoSegment(start_seconds=0.0, duration_seconds=30.0)],
        budget=VideoStageBudget.start(now_utc=started, utcnow=lambda: started).projection,
    )
    admission = recorder.ClipAdmission.first_or_existing(
        "clip-000",
        now_utc=started + timedelta(seconds=100),
    )

    deadline = recorder._browser_deadline(
        admission,
        clipset,
        {
            "VIDEO_MAX_CLIP_RECORD_SECONDS": "600",
            "PODCASTER_CLIP_VISIBILITY_TIMEOUT": "500",
            "PODCASTER_RECORDER_TIMEOUT": "400",
        },
    )

    assert deadline == started + timedelta(seconds=440)


def test_late_recorder_cannot_replace_terminal_manifest_or_hash_bound_blob(tmp_path) -> None:
    winner = b"winner-static-bytes"
    winner_evidence = MediaEvidence(
        size_bytes=len(winner),
        sha256=hashlib.sha256(winner).hexdigest(),
        probe=ProbeEvidence(format_name="matroska,webm", duration_seconds=1.0),
    )
    winner_path = clip_content_blob_path(JOB_ID, 1, winner_evidence.sha256)

    class RacingStorage(LocalStorageBackend):
        def upload_file(self, path, source, content_type):
            stored = super().upload_file(path, source, content_type)
            if path == clip_blob_path(JOB_ID, 1):
                self.put_bytes(winner_path, winner, "video/webm")
                self.put_bytes(
                    clip_manifest_blob_path(JOB_ID, 1),
                    json.dumps(
                        {
                            "clip_id": "clip-001",
                            "duration_ms": 1000,
                            "is_fallback": True,
                            "status": "fallback",
                            "media_blob_path": winner_path,
                            "media": winner_evidence.to_dict(),
                        }
                    ).encode(),
                    "application/json",
                )
            return stored

    scratch = RacingStorage(tmp_path / "race", "https://example.invalid/scratch")
    _stage_clipset(scratch)
    record, _ = _recorder(payload=b"late-recorder-bytes")

    outcome = record_clip(JOB_ID, 1, scratch=scratch, record_segment=record)

    manifest = json.loads(scratch.get_bytes(clip_manifest_blob_path(JOB_ID, 1)))
    assert outcome.status == OUTCOME_SKIPPED
    assert manifest["media_blob_path"] == winner_path
    assert scratch.get_bytes(winner_path) == winner
    loser_path = clip_content_blob_path(
        JOB_ID,
        1,
        hashlib.sha256(b"late-recorder-bytes").hexdigest(),
    )
    assert not scratch.blob_exists(loser_path)


def test_recorder_infra_preserves_parent_reserve_contract() -> None:
    bicep = Path("infra/modules/aca-recorder.bicep").read_text(encoding="utf-8")

    assert "param replicaTimeoutSeconds int = 840" in bicep
    assert "param clipVisibilityTimeoutSeconds int = 780" in bicep
    assert "param maxClipRecordSeconds int = 600" in bicep
    assert "name: 'PODCASTER_RECORDER_TIMEOUT'" in bicep
