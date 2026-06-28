"""Tests for the editor fan-out/fan-in orchestration (epic #552, #563)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from podcaster.video.clipset import (
    clip_blob_path,
    clip_manifest_blob_path,
    clips_prefix,
)
from podcaster.video.editor import (
    EditorLease,
    acquire_or_renew_lease,
    assemble_recording,
    cleanup_clips,
    editor_lease_blob_path,
    enqueue_missing_clips,
    missing_indices,
    plan_or_load_clipset,
    record_via_fanout,
    release_lease,
    wait_for_fanin,
)
from podcaster.video.sync_plan import RepoReference, VideoSegment
from podcaster.video.video_gen import RecordedSegment

_JSON = "application/json; charset=utf-8"
_WEBM = "video/webm"


class FakeStorage:
    """In-memory StorageBackend with atomic update_bytes + prefix ops."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def get_bytes(self, path: str) -> bytes | None:
        return self._data.get(path)

    def put_bytes(self, path: str, content: bytes, content_type: str):
        self._data[path] = content
        return _Stored(path, len(content))

    def update_bytes(self, path: str, content_type: str, update):
        updated = update(self._data.get(path))
        self._data[path] = updated
        return _Stored(path, len(updated))

    def blob_exists(self, path: str) -> bool:
        return path in self._data

    def blob_size(self, path: str) -> int | None:
        blob = self._data.get(path)
        return None if blob is None else len(blob)

    def upload_file(self, path: str, source: Path, content_type: str):
        self._data[path] = Path(source).read_bytes()
        return _Stored(path, len(self._data[path]))

    def download_file(self, path: str, dest: Path) -> bool:
        blob = self._data.get(path)
        if blob is None:
            return False
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(blob)
        return True

    def delete_blob(self, path: str) -> bool:
        return self._data.pop(path, None) is not None

    def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self._data if k.startswith(prefix)]
        for k in keys:
            del self._data[k]
        return len(keys)


class _Stored:
    def __init__(self, path: str, size: int) -> None:
        self.path = path
        self.size_bytes = size


class FakeProducer:
    """Records enqueued clip message bodies."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, body: str) -> None:
        self.sent.append(body)


def _segments(n: int) -> list[VideoSegment]:
    return [
        VideoSegment(
            start_seconds=float(i * 10),
            duration_seconds=10.0,
            repo=RepoReference(owner="acme", name=f"repo{i}"),
        )
        for i in range(n)
    ]


def _write_manifest(
    storage: FakeStorage,
    job_id: str,
    index: int,
    *,
    is_fallback: bool = False,
    has_pages: bool = False,
    website_url: str | None = None,
    write_clip: bool = True,
) -> None:
    if write_clip:
        storage.put_bytes(clip_blob_path(job_id, index), b"WEBMDATA", _WEBM)
    body = {
        "clip_id": f"clip-{index:03d}",
        "duration_ms": 10000,
        "is_fallback": is_fallback,
        "status": "fallback" if is_fallback else "success",
        "has_pages": has_pages,
        "website_url": website_url,
        "recovery_path": "fallback" if is_fallback else "direct",
    }
    storage.put_bytes(
        clip_manifest_blob_path(job_id, index),
        json.dumps(body).encode(),
        _JSON,
    )


# --- clipset planning / immutability -----------------------------------------


def test_plan_or_load_clipset_creates_when_absent():
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(3))
    assert clipset.count == 3
    assert clipset.indices() == [0, 1, 2]
    # Persisted for recorders to read.
    assert storage.blob_exists("video-jobs/job1/clipset.json")


def test_plan_or_load_clipset_is_immutable_on_redelivery():
    storage = FakeStorage()
    first = plan_or_load_clipset(storage, "job1", _segments(3))
    # A redelivered editor plans a *different* (shorter) set, but must reuse the
    # original immutable clipset rather than overwrite it.
    second = plan_or_load_clipset(storage, "job1", _segments(2))
    assert second.count == first.count == 3
    assert second.indices() == [0, 1, 2]


# --- additive fan-out ---------------------------------------------------------


def test_enqueue_missing_clips_enqueues_all_when_none_recorded():
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(3))
    producer = FakeProducer()
    pending = enqueue_missing_clips(storage, clipset, producer=producer)
    assert pending == [0, 1, 2]
    assert len(producer.sent) == 3


def test_enqueue_missing_clips_is_additive():
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(3))
    _write_manifest(storage, "job1", 1)  # index 1 already done
    producer = FakeProducer()
    pending = enqueue_missing_clips(storage, clipset, producer=producer)
    assert pending == [0, 2]
    assert len(producer.sent) == 2
    assert missing_indices(storage, clipset) == [0, 2]


# --- fan-in barrier -----------------------------------------------------------


def test_wait_for_fanin_completes_when_all_present():
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(2))
    for i in range(2):
        _write_manifest(storage, "job1", i)
    complete, present = wait_for_fanin(
        storage, clipset, sleep=lambda _s: None, monotonic=lambda: 0.0
    )
    assert complete is True
    assert present == {0, 1}


def test_wait_for_fanin_blocks_until_manifests_appear():
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(2))
    clock = {"t": 0.0}

    def _mono() -> float:
        return clock["t"]

    polls = {"n": 0}

    def _sleep(_s: float) -> None:
        clock["t"] += 1.0
        polls["n"] += 1
        # Manifests land after the first poll.
        if polls["n"] == 1:
            for i in range(2):
                _write_manifest(storage, "job1", i)

    complete, present = wait_for_fanin(
        storage, clipset, timeout_seconds=100, poll_seconds=1, sleep=_sleep, monotonic=_mono
    )
    assert complete is True
    assert present == {0, 1}


def test_wait_for_fanin_times_out_with_partial():
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(3))
    _write_manifest(storage, "job1", 0)  # only one of three lands
    clock = {"t": 0.0}

    def _sleep(_s: float) -> None:
        clock["t"] += 100.0

    complete, present = wait_for_fanin(
        storage,
        clipset,
        timeout_seconds=10,
        poll_seconds=5,
        sleep=_sleep,
        monotonic=lambda: clock["t"],
    )
    assert complete is False
    assert present == {0}


# --- assemble (download + reconstruct) ---------------------------------------


def test_assemble_recording_reconstructs_metadata(tmp_path):
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(2))
    _write_manifest(storage, "job1", 0, has_pages=True, website_url="https://acme.io")
    _write_manifest(storage, "job1", 1, is_fallback=True)
    result = assemble_recording(storage, clipset, tmp_path)
    assert len(result.recorded) == 2
    assert result.recorded[0].has_pages is True
    assert result.recorded[0].website_url == "https://acme.io"
    assert result.recorded[0].is_fallback is False
    assert result.recorded[1].is_fallback is True
    # Both clips were downloaded locally.
    for rec in result.recorded:
        assert rec.video_path.exists()


def test_assemble_recording_fills_poison_gap(tmp_path):
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(2))
    _write_manifest(storage, "job1", 0)
    # index 1 is poison: terminal fallback manifest but NO .webm clip.
    _write_manifest(storage, "job1", 1, is_fallback=True, write_clip=False)
    storage.delete_blob(clip_blob_path("job1", 1))

    filled: list[int] = []

    def _fill_gap(segment, output_dir, clip_index) -> RecordedSegment:
        filled.append(clip_index)
        path = Path(output_dir) / f"gap_{clip_index}.webm"
        path.write_bytes(b"GAPCARD")
        return RecordedSegment(
            segment=segment, video_path=path, is_fallback=True, recovery_path="fallback"
        )

    result = assemble_recording(storage, clipset, tmp_path, fill_gap=_fill_gap)
    assert filled == [1]
    assert len(result.recorded) == 2
    assert result.recorded[1].is_fallback is True
    assert result.recorded[1].video_path.read_bytes() == b"GAPCARD"


def test_assemble_recording_fills_clip_without_manifest(tmp_path):
    # On a fan-in TIMEOUT a half-written .webm may exist without its terminal
    # manifest sentinel. Such a clip is NOT trustworthy → fill the gap (#563).
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(2))
    _write_manifest(storage, "job1", 0)
    # index 1 has a .webm but NO manifest (not terminal).
    storage.put_bytes(clip_blob_path("job1", 1), b"PARTIAL", _WEBM)

    filled: list[int] = []

    def _fill_gap(segment, output_dir, clip_index) -> RecordedSegment:
        filled.append(clip_index)
        path = Path(output_dir) / f"gap_{clip_index}.webm"
        path.write_bytes(b"GAPCARD")
        return RecordedSegment(
            segment=segment, video_path=path, is_fallback=True, recovery_path="fallback"
        )

    result = assemble_recording(storage, clipset, tmp_path, fill_gap=_fill_gap)
    assert filled == [1]
    assert result.recorded[1].video_path.read_bytes() == b"GAPCARD"


# --- editor lease -------------------------------------------------------------


def test_acquire_lease_when_free():
    storage = FakeStorage()
    assert acquire_or_renew_lease(storage, "job1", "run-A") is True
    lease = EditorLease.from_bytes(storage.get_bytes(editor_lease_blob_path("job1")))
    assert lease is not None and lease.run_id == "run-A"


def test_foreign_unexpired_lease_blocks_second_editor():
    storage = FakeStorage()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert acquire_or_renew_lease(storage, "job1", "run-A", now=now) is True
    # A second editor a moment later sees the unexpired foreign lease and no-ops.
    later = now + timedelta(seconds=5)
    assert acquire_or_renew_lease(storage, "job1", "run-B", now=later) is False
    lease = EditorLease.from_bytes(storage.get_bytes(editor_lease_blob_path("job1")))
    assert lease.run_id == "run-A"


def test_expired_foreign_lease_can_be_taken_over():
    storage = FakeStorage()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    acquire_or_renew_lease(storage, "job1", "run-A", now=now, ttl_seconds=10)
    # Long after run-A's lease expired, run-B takes over.
    later = now + timedelta(seconds=100)
    assert acquire_or_renew_lease(storage, "job1", "run-B", now=later) is True
    lease = EditorLease.from_bytes(storage.get_bytes(editor_lease_blob_path("job1")))
    assert lease.run_id == "run-B"


def test_owner_can_renew_its_own_lease():
    storage = FakeStorage()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    acquire_or_renew_lease(storage, "job1", "run-A", now=now, ttl_seconds=10)
    later = now + timedelta(seconds=5)
    assert acquire_or_renew_lease(storage, "job1", "run-A", now=later, ttl_seconds=10) is True
    lease = EditorLease.from_bytes(storage.get_bytes(editor_lease_blob_path("job1")))
    assert lease.expires_at == later + timedelta(seconds=10)


def test_heartbeat_renew_keeps_lease_past_original_ttl():
    # A periodic renew with a FRESH timestamp keeps the lease alive across a
    # barrier longer than the TTL — the job_runner heartbeat fix (#563): if the
    # renew reused the run-start time, expires_at would never advance.
    storage = FakeStorage()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    acquire_or_renew_lease(storage, "job1", "run-A", now=start, ttl_seconds=30)
    # Heartbeat every 15s for 90s (3x the TTL): each beat advances expiry.
    for beat in range(15, 91, 15):
        moment = start + timedelta(seconds=beat)
        assert acquire_or_renew_lease(storage, "job1", "run-A", now=moment, ttl_seconds=30) is True
    lease = EditorLease.from_bytes(storage.get_bytes(editor_lease_blob_path("job1")))
    assert lease.run_id == "run-A"
    assert lease.expires_at == start + timedelta(seconds=90 + 30)


def test_release_lease_only_when_owned():
    storage = FakeStorage()
    acquire_or_renew_lease(storage, "job1", "run-A")
    release_lease(storage, "job1", "run-B")  # not the owner → no-op
    held = EditorLease.from_bytes(storage.get_bytes(editor_lease_blob_path("job1")))
    assert held is not None and held.run_id == "run-A"
    release_lease(storage, "job1", "run-A")  # owner → released (lease reads as free)
    assert EditorLease.from_bytes(storage.get_bytes(editor_lease_blob_path("job1"))) is None
    # A successor can immediately re-acquire the freed lease.
    assert acquire_or_renew_lease(storage, "job1", "run-C")


# --- cleanup ------------------------------------------------------------------


def test_cleanup_clips_removes_only_clip_prefix():
    storage = FakeStorage()
    plan_or_load_clipset(storage, "job1", _segments(2))
    for i in range(2):
        _write_manifest(storage, "job1", i)
    removed = cleanup_clips(storage, "job1")
    assert removed == 4  # 2 webm + 2 manifest
    assert not storage.blob_exists(clip_blob_path("job1", 0))
    # The clipset.json itself is outside clips/ and survives.
    assert storage.blob_exists("video-jobs/job1/clipset.json")
    assert storage.delete_prefix(clips_prefix("job1")) == 0


# --- end-to-end orchestration -------------------------------------------------


def test_record_via_fanout_end_to_end(tmp_path):
    storage = FakeStorage()
    producer = FakeProducer()
    job_id = "job1"

    # Simulate recorders completing all clips on the first barrier poll.
    def _sleep(_s: float) -> None:
        for i in range(3):
            _write_manifest(storage, job_id, i)

    result = record_via_fanout(
        job_id,
        _segments(3),
        tmp_path,
        scratch=storage,
        producer=producer,
        timeout_seconds=100,
        poll_seconds=1,
        sleep=_sleep,
        monotonic=lambda: 0.0,
    )
    assert len(result.recorded) == 3
    assert len(producer.sent) == 3  # all fanned out
    assert all(r.video_path.exists() for r in result.recorded)


def test_record_via_fanout_renews_lease_via_heartbeat(tmp_path):
    storage = FakeStorage()
    producer = FakeProducer()
    job_id = "job1"
    beats: list[int] = []

    # All clips present immediately so the barrier completes on the first poll
    # (which fires the heartbeat) without sleeping.
    for i in range(2):
        _write_manifest(storage, job_id, i)

    def _heartbeat() -> None:
        beats.append(1)

    result = record_via_fanout(
        job_id,
        _segments(2),
        tmp_path,
        scratch=storage,
        producer=producer,
        sleep=lambda _s: None,
        monotonic=lambda: 0.0,
        heartbeat=_heartbeat,
    )
    assert len(result.recorded) == 2
    assert beats  # heartbeat fired at least once on the barrier poll


def test_record_via_fanout_aborts_when_heartbeat_raises(tmp_path):
    # A heartbeat that signals lost-lease (raises) must propagate out of the
    # barrier wait so the editor stops before compose/publish (#563).
    storage = FakeStorage()
    producer = FakeProducer()
    job_id = "job1"
    for i in range(2):
        _write_manifest(storage, job_id, i)

    def _heartbeat() -> None:
        raise RuntimeError("lease lost")

    with pytest.raises(RuntimeError, match="lease lost"):
        record_via_fanout(
            job_id,
            _segments(2),
            tmp_path,
            scratch=storage,
            producer=producer,
            sleep=lambda _s: None,
            monotonic=lambda: 0.0,
            heartbeat=_heartbeat,
        )
