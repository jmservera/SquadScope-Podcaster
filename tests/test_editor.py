"""Tests for the editor fan-out/fan-in orchestration (epic #552, #563)."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from podcaster.video.budget import VideoStageBudget
from podcaster.video.clipset import (
    clip_blob_path,
    clip_content_blob_path,
    clip_manifest_blob_path,
    clips_prefix,
)
from podcaster.video.editor import (
    EditorLease,
    RecordingInsufficientError,
    acquire_or_renew_lease,
    assemble_recording,
    cleanup_clips,
    editor_lease_blob_path,
    enqueue_missing_clips,
    missing_indices,
    plan_or_load_clipset,
    record_via_fanout,
    release_lease,
    terminalize_missing_clips,
    wait_for_fanin,
)
from podcaster.video.intermediates import StorageOperationTimeout
from podcaster.video.process import MediaEvidence, ProbeEvidence
from podcaster.video.sync_plan import RepoReference, VideoSegment
from podcaster.video.video_gen import RecordedSegment

_JSON = "application/json; charset=utf-8"
_WEBM = "video/webm"


def _validate_media(path: Path, expected: MediaEvidence | None, _timeout: float) -> MediaEvidence:
    if expected is not None:
        return expected
    payload = path.read_bytes()
    return MediaEvidence(
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        probe=ProbeEvidence(format_name="matroska,webm", duration_seconds=1.0),
    )


@pytest.fixture(autouse=True)
def _stub_media_probe(monkeypatch):
    monkeypatch.setattr("podcaster.video.editor._validate_downloaded_media", _validate_media)


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
    payload = b"WEBMDATA"
    evidence = MediaEvidence(
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        probe=ProbeEvidence(format_name="matroska,webm", duration_seconds=1.0),
    )
    content_path = clip_content_blob_path(job_id, index, evidence.sha256)
    if write_clip:
        storage.put_bytes(content_path, payload, _WEBM)
    body = {
        "clip_id": f"clip-{index:03d}",
        "duration_ms": 10000,
        "is_fallback": is_fallback,
        "status": "fallback" if is_fallback else "success",
        "has_pages": has_pages,
        "website_url": website_url,
        "recovery_path": "fallback" if is_fallback else "direct",
        "media_blob_path": content_path,
        "media": evidence.to_dict(),
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
    second = plan_or_load_clipset(
        storage,
        "job1",
        _segments(2),
        budget=VideoStageBudget.start(),
    )
    assert second.count == first.count == 3
    assert second.indices() == [0, 1, 2]


def test_budgeted_clipset_rejects_malformed_cache_and_recomputes():
    storage = FakeStorage()
    storage.put_bytes("video-jobs/job1/clipset.json", b"{malformed", _JSON)
    storage.put_bytes("video-jobs/job1/clips/000.webm", b"stale", "video/webm")
    storage.put_bytes("video-jobs/job1/intermediates/segment.mp4", b"reusable", "video/mp4")
    storage.put_bytes("video-jobs/job1/checkpoint.json", b"keep", _JSON)
    lease_path = editor_lease_blob_path("job1")
    storage.put_bytes(
        lease_path,
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run-A",
                "expires_at": "2026-09-22T00:00:00Z",
            }
        ).encode("utf-8"),
        _JSON,
    )

    clipset = plan_or_load_clipset(
        storage,
        "job1",
        _segments(2),
        budget=VideoStageBudget.start(),
    )

    assert clipset.count == 2
    assert not storage.blob_exists("video-jobs/job1/clips/000.webm")
    assert storage.blob_exists("video-jobs/job1/intermediates/segment.mp4")
    assert storage.blob_exists("video-jobs/job1/checkpoint.json")
    assert storage.blob_exists(lease_path)


def test_plan_or_load_clipset_rejects_cross_job_cache_without_cleanup():
    storage = FakeStorage()
    foreign = plan_or_load_clipset(storage, "job2", _segments(2))
    foreign_bytes = foreign.to_json_bytes()
    storage.put_bytes("video-jobs/job1/clipset.json", foreign_bytes, _JSON)
    storage.put_bytes("video-jobs/job1/clips/000.webm", b"expected-job-data", _WEBM)
    before = dict(storage._data)

    with pytest.raises(ValueError, match="does not match expected"):
        plan_or_load_clipset(
            storage,
            "job1",
            _segments(2),
            budget=VideoStageBudget.start(),
        )

    assert storage._data == before


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


def test_enqueue_missing_clips_bounds_probe_and_blocked_send_with_shared_clock():
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(3))
    producer = FakeProducer()
    clock = {"t": 0.0}
    timeouts: list[float] = []

    def _remaining() -> float:
        return max(0.0, 1.0 - clock["t"])

    def _runner(call, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            result = call()
            clock["t"] += 0.6
            return result
        clock["t"] += timeout
        raise StorageOperationTimeout("blocked queue send")

    enqueued = enqueue_missing_clips(
        storage,
        clipset,
        producer=producer,
        admission_check=_remaining,
        operation_runner=_runner,
    )

    assert enqueued == []
    assert producer.sent == []
    assert timeouts == pytest.approx([1.0, 0.4])
    assert clock["t"] == pytest.approx(1.0)


def test_enqueue_missing_clips_stops_before_probe_or_send_at_cutoff():
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(3))
    producer = FakeProducer()
    clock = {"t": 0.0}
    calls = {"count": 0}

    def _remaining() -> float:
        return max(0.0, 1.0 - clock["t"])

    def _runner(call, _timeout):
        calls["count"] += 1
        result = call()
        clock["t"] += 0.5
        return result

    enqueued = enqueue_missing_clips(
        storage,
        clipset,
        producer=producer,
        admission_check=_remaining,
        operation_runner=_runner,
    )

    assert enqueued == [0]
    assert len(producer.sent) == 1
    assert calls["count"] == 2
    assert clock["t"] == pytest.approx(1.0)


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


def test_wait_for_fanin_stops_after_budgeted_probe_timeout():
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(3))
    probed: list[str] = []
    original = storage.blob_exists

    def tracked(path):
        probed.append(path)
        return original(path)

    def blocking_runner(call, _timeout):
        if probed:
            raise TimeoutError("blocked")
        return call()

    storage.blob_exists = tracked
    complete, present = wait_for_fanin(
        storage,
        clipset,
        budget=VideoStageBudget.start(),
        operation_runner=blocking_runner,
    )

    assert complete is False
    assert present == set()
    assert len(probed) == 1


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


@pytest.mark.parametrize(
    ("status", "is_fallback"),
    [("success", False), ("fallback", True)],
)
def test_assemble_recording_accepts_explicit_terminal_success_statuses(
    tmp_path, status, is_fallback
):
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(1))
    _write_manifest(storage, "job1", 0, is_fallback=is_fallback)
    manifest = json.loads(storage.get_bytes(clip_manifest_blob_path("job1", 0)))

    assert manifest["status"] == status
    result = assemble_recording(storage, clipset, tmp_path)

    assert len(result.recorded) == 1
    assert result.recorded[0].is_fallback is is_fallback


@pytest.mark.parametrize("status", ["", "unexpected_terminal"])
def test_assemble_recording_rejects_unrecognized_terminal_status(tmp_path, status):
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(1))
    _write_manifest(storage, "job1", 0)
    manifest_path = clip_manifest_blob_path("job1", 0)
    manifest = json.loads(storage.get_bytes(manifest_path))
    manifest["status"] = status
    storage.put_bytes(manifest_path, json.dumps(manifest).encode(), _JSON)

    with pytest.raises(
        RecordingInsufficientError,
        match="terminal manifest has invalid recorder status",
    ):
        assemble_recording(storage, clipset, tmp_path)


def test_assemble_recording_stops_after_budgeted_probe_timeout(tmp_path):
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(1))
    _write_manifest(storage, "job1", 0)
    payload = b"WEBMDATA"
    manifest_path = clip_manifest_blob_path("job1", 0)
    manifest = json.loads(storage.get_bytes(manifest_path))
    manifest["schema_version"] = "1.0"
    manifest["media"] = MediaEvidence(
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        probe=ProbeEvidence("matroska,webm", 1.0),
    ).to_dict()
    storage.put_bytes(manifest_path, json.dumps(manifest).encode(), _JSON)
    calls = 0

    def blocking_runner(call, _timeout):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("blocked")
        return call()

    with pytest.raises(TimeoutError, match="blocked"):
        assemble_recording(
            storage,
            clipset,
            tmp_path,
            budget=VideoStageBudget.start(),
            operation_runner=blocking_runner,
        )

    assert calls == 2


def test_assemble_recording_manifest_read_timeout_stops_storage_calls(tmp_path):
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clock = _Clock(started, elapsed=1499)
    budget = VideoStageBudget.start(
        now_utc=started,
        monotonic=clock.monotonic,
        utcnow=clock.utcnow,
    )
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(1))
    _write_manifest(storage, "job1", 0)
    manifest_path = clip_manifest_blob_path("job1", 0)
    storage_calls: list[tuple[str, str]] = []
    timeouts: list[float] = []
    original_get_bytes = storage.get_bytes
    original_blob_exists = storage.blob_exists
    original_download_file = storage.download_file

    def tracked_get_bytes(path):
        storage_calls.append(("get_bytes", path))
        return original_get_bytes(path)

    def tracked_blob_exists(path):
        storage_calls.append(("blob_exists", path))
        return original_blob_exists(path)

    def tracked_download_file(path, dest):
        storage_calls.append(("download_file", path))
        return original_download_file(path, dest)

    storage.get_bytes = tracked_get_bytes
    storage.blob_exists = tracked_blob_exists
    storage.download_file = tracked_download_file

    def blocking_runner(call, timeout):
        timeouts.append(timeout)
        call()
        clock.sleep(timeout)
        raise StorageOperationTimeout("manifest read stalled")

    with pytest.raises(
        RecordingInsufficientError,
        match="terminal manifest read timed out",
    ) as exc_info:
        assemble_recording(
            storage,
            clipset,
            tmp_path,
            budget=budget,
            operation_runner=blocking_runner,
        )

    assert isinstance(exc_info.value.__cause__, StorageOperationTimeout)
    assert str(exc_info.value.__cause__) == "manifest read stalled"
    assert timeouts == [1]
    assert clock.elapsed == 1500
    assert storage_calls == [("get_bytes", manifest_path)]


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


def test_cleanup_clips_skips_when_shutdown_budget_is_exhausted():
    storage = FakeStorage()
    storage.put_bytes(clip_blob_path("job1", 0), b"clip", _WEBM)
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    budget = VideoStageBudget.start(
        now_utc=started,
        monotonic=lambda: 5100.0,
        utcnow=lambda: started + timedelta(seconds=5100),
    )
    calls: list[float] = []

    removed = cleanup_clips(
        storage,
        "job1",
        budget=budget,
        operation_runner=lambda call, timeout: calls.append(timeout),
    )

    assert removed == 0
    assert calls == []
    assert storage.blob_exists(clip_blob_path("job1", 0))


def test_cleanup_clips_timeout_is_best_effort():
    storage = FakeStorage()
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    budget = VideoStageBudget.start(
        now_utc=started,
        monotonic=lambda: 5099.0,
        utcnow=lambda: started + timedelta(seconds=5099),
    )

    removed = cleanup_clips(
        storage,
        "job1",
        budget=budget,
        operation_runner=lambda call, timeout: (_ for _ in ()).throw(TimeoutError("blocked")),
    )

    assert removed == 0


def test_started_blocking_clip_cleanup_is_killed_before_late_side_effect(tmp_path):
    started_marker = tmp_path / "clip-cleanup-started"
    committed = tmp_path / "clip-cleanup-committed"

    class BlockingStorage(FakeStorage):
        def delete_prefix(self, prefix: str) -> int:
            started_marker.write_text(prefix, encoding="utf-8")
            time.sleep(0.5)
            committed.write_text("late", encoding="utf-8")
            return 1

    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    budget = VideoStageBudget.start(
        now_utc=started,
        monotonic=lambda: 5099.9,
        utcnow=lambda: started + timedelta(seconds=5099.9),
    )

    assert cleanup_clips(BlockingStorage(), "job1", budget=budget) == 0
    assert started_marker.exists()
    time.sleep(0.5)
    assert not committed.exists()
    assert not any(
        child.name == "video-storage-operation" for child in multiprocessing.active_children()
    )


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


class _Clock:
    def __init__(self, started: datetime, elapsed: float = 0.0) -> None:
        self.started = started
        self.elapsed = elapsed

    def utcnow(self) -> datetime:
        return self.started + timedelta(seconds=self.elapsed)

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds


def _fallback_renderer(path: Path, _timeout: float) -> MediaEvidence:
    path.write_bytes(b"fixed-static-fallback")
    payload = path.read_bytes()
    return MediaEvidence(
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        probe=ProbeEvidence(format_name="matroska,webm", duration_seconds=1.0),
    )


def test_fanin_stops_exactly_at_t_plus_1200_and_falls_back_by_t_plus_25(tmp_path):
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clock = _Clock(started, elapsed=1199)
    budget = VideoStageBudget.start(
        now_utc=started,
        monotonic=clock.monotonic,
        utcnow=clock.utcnow,
    )
    storage = FakeStorage()
    producer = FakeProducer()

    result = record_via_fanout(
        "job1",
        _segments(2),
        tmp_path,
        scratch=storage,
        producer=producer,
        poll_seconds=15,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        budget=budget,
        fallback_renderer=_fallback_renderer,
    )

    assert clock.elapsed == 1200
    assert len(producer.sent) == 2
    assert len(result.recorded) == 2
    for index in range(2):
        manifest = json.loads(storage.get_bytes(clip_manifest_blob_path("job1", index)))
        assert manifest["status"] == "fallback"
        assert manifest["media"]["sha256"]


def test_editor_threads_fallback_admission_and_owned_storage_runner(monkeypatch):
    from podcaster.video import recorder

    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clock = _Clock(started, elapsed=1400)
    budget = VideoStageBudget.start(
        now_utc=started,
        monotonic=clock.monotonic,
        utcnow=clock.utcnow,
    )
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(1))
    captured: dict[str, object] = {}

    def operation_runner(call, timeout):
        return call()

    def write_fallback(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(recorder, "write_fallback_manifest", write_fallback)

    terminalize_missing_clips(
        storage,
        clipset,
        budget=budget,
        renderer=_fallback_renderer,
        operation_runner=operation_runner,
    )

    assert captured["operation_runner"] is operation_runner
    assert captured["admission_check"]() == 100
    assert captured["timeout_seconds"] == 30


def test_fallback_missing_clip_inspection_times_out_at_t_plus_1500(monkeypatch):
    from podcaster.video import recorder

    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clock = _Clock(started, elapsed=1499)
    budget = VideoStageBudget.start(
        now_utc=started,
        monotonic=clock.monotonic,
        utcnow=clock.utcnow,
    )
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(2))
    timeouts: list[float] = []

    def operation_runner(_call, timeout):
        timeouts.append(timeout)
        clock.sleep(timeout)
        raise StorageOperationTimeout("inspection stalled")

    monkeypatch.setattr(
        recorder,
        "write_fallback_manifest",
        lambda *_args, **_kwargs: pytest.fail("fallback started after inspection timeout"),
    )

    with pytest.raises(StorageOperationTimeout, match="inspection stalled"):
        terminalize_missing_clips(
            storage,
            clipset,
            budget=budget,
            renderer=_fallback_renderer,
            operation_runner=operation_runner,
        )

    assert timeouts == [1]
    assert clock.elapsed == 1500


def test_t_plus_25_blocks_fallback_storage_finalization(tmp_path):
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    clock = _Clock(started, elapsed=1500)
    budget = VideoStageBudget.start(
        now_utc=started,
        monotonic=clock.monotonic,
        utcnow=clock.utcnow,
    )
    storage = FakeStorage()
    producer = FakeProducer()

    with pytest.raises(RecordingInsufficientError):
        record_via_fanout(
            "job1",
            _segments(1),
            tmp_path,
            scratch=storage,
            producer=producer,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            budget=budget,
            fallback_renderer=_fallback_renderer,
        )

    assert storage.get_bytes(clip_manifest_blob_path("job1", 0)) is None
    assert producer.sent == []


def test_hash_bound_terminal_media_ignores_late_legacy_overwrite(tmp_path):
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(1))
    winner = b"winning-fallback-bytes"
    evidence = MediaEvidence(
        size_bytes=len(winner),
        sha256=hashlib.sha256(winner).hexdigest(),
        probe=ProbeEvidence(format_name="matroska,webm", duration_seconds=1.0),
    )
    content_path = clip_content_blob_path("job1", 0, evidence.sha256)
    storage.put_bytes(content_path, winner, _WEBM)
    storage.put_bytes(clip_blob_path("job1", 0), b"late-recorder-overwrite", _WEBM)
    storage.put_bytes(
        clip_manifest_blob_path("job1", 0),
        json.dumps(
            {
                "clip_id": "clip-000",
                "duration_ms": 1000,
                "is_fallback": True,
                "status": "fallback",
                "media_blob_path": content_path,
                "media": evidence.to_dict(),
            }
        ).encode(),
        _JSON,
    )

    def _strict(path: Path, expected: MediaEvidence | None, _timeout: float) -> MediaEvidence:
        assert expected is not None
        payload = path.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected.sha256
        assert len(payload) == expected.size_bytes
        return expected

    result = assemble_recording(storage, clipset, tmp_path, media_validator=_strict)

    assert result.recorded[0].video_path.read_bytes() == winner
    assert (
        json.loads(storage.get_bytes(clip_manifest_blob_path("job1", 0)))["media_blob_path"]
        == content_path
    )


@pytest.mark.parametrize(
    ("clip_id", "path_job_id", "path_clip_index", "message"),
    [
        ("clip-999", "job1", 0, "clip_id does not match"),
        ("not-a-clip-id", "job1", 0, "clip_id does not match"),
        ("clip-000", "other-job", 0, "media path does not match"),
        ("clip-000", "job1", 1, "media path does not match"),
    ],
    ids=["mismatched-clip-id", "invalid-clip-id", "cross-job-media", "cross-clip-media"],
)
def test_assemble_recording_rejects_manifest_identity_mismatch(
    tmp_path, clip_id, path_job_id, path_clip_index, message
):
    storage = FakeStorage()
    clipset = plan_or_load_clipset(storage, "job1", _segments(1))
    payload = b"owned-clip"
    evidence = MediaEvidence(
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        probe=ProbeEvidence(format_name="matroska,webm", duration_seconds=1.0),
    )
    content_path = clip_content_blob_path(path_job_id, path_clip_index, evidence.sha256)
    storage.put_bytes(content_path, payload, _WEBM)
    manifest = {
        "clip_id": clip_id,
        "duration_ms": 1000,
        "is_fallback": False,
        "status": "success",
        "media_blob_path": content_path,
        "media": evidence.to_dict(),
    }
    storage.put_bytes(
        clip_manifest_blob_path("job1", 0),
        json.dumps(manifest).encode(),
        _JSON,
    )

    downloads: list[str] = []
    original_download = storage.download_file

    def tracked_download(path, dest):
        downloads.append(path)
        return original_download(path, dest)

    storage.download_file = tracked_download

    with pytest.raises(RecordingInsufficientError, match=message):
        assemble_recording(storage, clipset, tmp_path)

    assert downloads == []
