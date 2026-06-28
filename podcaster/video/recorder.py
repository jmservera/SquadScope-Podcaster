"""Recorder entrypoint for scale-out video recording (epic #552).

A **recorder** is a stateless, horizontally-scalable worker (KEDA min 0 / max 10)
that consumes **one** ``video-clip-jobs`` message — a ``(job_id, clip_index)``
pair — and records **exactly one** clip. It is the fan-out half of the
recorder/editor split described in ``docs/scaleout-recorder-rfc.md`` (§3, §5, §4).

Invariants (RFC §5, §4):

* **Manifest sentinel.** The per-clip ``manifest.json`` is written **strictly
  after** the ``.webm`` is uploaded and size-verified. Idempotency keys off the
  *manifest's* presence — not the clip's: a manifest present (success *or*
  fallback) means "done, skip"; a ``.webm`` present without its manifest means a
  recorder died mid-write, so we re-record and overwrite, then write the manifest.
* **Never overwrite a terminal manifest.** Once a manifest exists for an index it
  is authoritative.
* **Poison → terminal fallback manifest.** At ``dequeue_count >= MAX_DEQUEUE_COUNT``
  the recorder does not silently drop the message: it writes a terminal
  ``is_fallback`` manifest for the index and deletes the message, so the editor's
  fan-in barrier (a pure per-index presence check) always converges.

The actual segment recording reuses the unchanged ``_record_segment`` logic from
:mod:`podcaster.video.video_gen` (imported lazily so unit tests and the
``PODCASTER_RECORDER_FAKE_BROWSER`` CI path need no Playwright/Chromium).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from podcaster.queue import (
    QueueMessage,
    create_clip_queue_backend,
    parse_clip_job,
)
from podcaster.storage import StorageBackend, create_scratch_storage_backend
from podcaster.video.clip_manifest import ClipManifest
from podcaster.video.clipset import (
    Clipset,
    clip_blob_path,
    clip_manifest_blob_path,
    clipset_blob_path,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from podcaster.video.sync_plan import VideoSegment

logger = logging.getLogger("podcaster.video.recorder")

#: Mirror the editor's poison threshold (``job_runner.MAX_DEQUEUE_COUNT``) so a
#: clip that repeatedly fails to record converges to a terminal fallback manifest.
MAX_DEQUEUE_COUNT = 5

#: When set truthy, ``main`` synthesises a tiny placeholder clip instead of
#: launching Chromium — used by CI / the Azurite fan-out harness (RFC §9).
ENV_FAKE_BROWSER = "PODCASTER_RECORDER_FAKE_BROWSER"

#: Visibility timeout (seconds) for a received clip message: must be >= the
#: worst-case single-clip record time so a slow clip is not double-delivered
#: mid-flight (RFC §8).
ENV_CLIP_VISIBILITY_TIMEOUT = "PODCASTER_CLIP_VISIBILITY_TIMEOUT"
DEFAULT_CLIP_VISIBILITY_TIMEOUT = 900

_JSON_CONTENT_TYPE = "application/json; charset=utf-8"
_WEBM_CONTENT_TYPE = "video/webm"

#: Records one segment to *output_dir* and returns the produced ``.webm`` path
#: plus whether it is a fallback card. Injectable for tests / the fake path.
RecordSegmentFn = Callable[["VideoSegment", Path], "RecordResult"]

STATUS_SUCCESS = "success"
STATUS_FALLBACK = "fallback"

OUTCOME_RECORDED = "recorded"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FALLBACK = "fallback"
OUTCOME_RETRY = "retry"
OUTCOME_MALFORMED = "malformed"


@dataclass(frozen=True)
class RecordResult:
    """Output of a single segment recording: the clip file and its nature."""

    video_path: Path
    duration_ms: int
    is_fallback: bool = False


@dataclass(frozen=True)
class ClipOutcome:
    """Terminal disposition of processing one clip message."""

    job_id: str
    clip_index: int
    status: str


class RecorderConfigError(RuntimeError):
    """Raised when the recorder is not configured (no scratch/queue backend)."""


def load_clipset(scratch: StorageBackend, job_id: str) -> Clipset:
    """Load and parse the editor-written ``clipset.json`` for *job_id*."""
    payload = scratch.get_bytes(clipset_blob_path(job_id))
    return Clipset.from_bytes(payload)


def _clip_manifest_bytes(
    manifest: ClipManifest, *, status: str, failure_reason: str | None = None
) -> bytes:
    """Serialise a clip manifest with the recorder's terminal ``status`` marker.

    Extends ``ClipManifest.to_dict()`` with ``status`` (``success``/``fallback``)
    and an optional ``failure_reason`` without modifying the shared
    :class:`ClipManifest` schema — :meth:`ClipManifest.from_dict` ignores the
    extra keys, so the manifest still round-trips for the editor/EDL.
    """
    data: dict[str, Any] = dict(manifest.to_dict())
    data["status"] = status
    if failure_reason:
        data["failure_reason"] = failure_reason
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def _clip_id(clip_index: int) -> str:
    return f"clip-{clip_index:03d}"


def _write_manifest_if_absent(
    scratch: StorageBackend, path: str, content: bytes, content_type: str
) -> bool:
    """Atomically write *content* to *path* only if no blob is there yet.

    Uses :meth:`StorageBackend.update_bytes` (ETag ``If-None-Match: *`` CAS on
    Azure, ``flock`` locally) so the "never overwrite a terminal manifest"
    invariant holds even when two recorders race on the same ``clip_index``
    (double-delivery / additive re-enqueue). Returns ``True`` when this call
    wrote the blob, ``False`` when an authoritative manifest already existed.
    """
    wrote = False

    def _update(current: bytes | None) -> bytes:
        nonlocal wrote
        if current:
            # A terminal manifest is already present — keep it byte-for-byte.
            return current
        wrote = True
        return content

    scratch.update_bytes(path, content_type, _update)
    return wrote


def record_clip(
    job_id: str,
    clip_index: int,
    *,
    scratch: StorageBackend,
    record_segment: RecordSegmentFn | None = None,
    env: Mapping[str, str] | None = None,
) -> ClipOutcome:
    """Record exactly one clip ``(job_id, clip_index)`` to ``video-scratch``.

    Idempotent via the manifest sentinel: returns ``OUTCOME_SKIPPED`` without
    re-recording when a terminal manifest already exists. Otherwise records the
    segment, uploads the size-verified ``.webm``, then writes the manifest and
    returns ``OUTCOME_RECORDED``.
    """
    env = env if env is not None else os.environ
    manifest_path = clip_manifest_blob_path(job_id, clip_index)

    if scratch.blob_exists(manifest_path):
        logger.info(
            "clip already has a terminal manifest; skipping job_id=%s clip_index=%d",
            job_id,
            clip_index,
        )
        return ClipOutcome(job_id, clip_index, OUTCOME_SKIPPED)

    clipset = load_clipset(scratch, job_id)
    entry = clipset.entry(clip_index)
    segment = entry.to_segment()

    if record_segment is None:
        record_segment = _select_record_segment(env)

    with tempfile.TemporaryDirectory(prefix=f"clip-{clip_index:03d}-") as tmp:
        output_dir = Path(tmp)
        result = record_segment(segment, output_dir)
        video_path = Path(result.video_path)
        if not video_path.exists():
            raise RuntimeError(
                f"recorder produced no clip file for job_id={job_id} clip_index={clip_index}"
            )

        clip_path = clip_blob_path(job_id, clip_index)
        expected_size = video_path.stat().st_size
        # Re-check the sentinel after the (potentially slow) record: a concurrent
        # recorder may have completed this clip while we worked. If so, leave the
        # authoritative clip/manifest pair untouched and skip.
        if scratch.blob_exists(manifest_path):
            logger.info(
                "terminal manifest appeared during recording; skipping write "
                "job_id=%s clip_index=%d",
                job_id,
                clip_index,
            )
            return ClipOutcome(job_id, clip_index, OUTCOME_SKIPPED)

        scratch.upload_file(clip_path, video_path, _WEBM_CONTENT_TYPE)
        if not _verify_size(scratch, clip_path, expected_size):
            # Drop the torn upload so the manifest is never written over an
            # unverified clip; the message is retried (no manifest = not done).
            _best_effort_delete(scratch, clip_path)
            raise RuntimeError(
                f"clip size verification failed for job_id={job_id} clip_index={clip_index}"
            )

        manifest = ClipManifest(
            clip_id=_clip_id(clip_index),
            duration_ms=int(result.duration_ms),
            repo_url=entry.repo_url,
            is_fallback=bool(result.is_fallback),
        )
        status = STATUS_FALLBACK if result.is_fallback else STATUS_SUCCESS
        # Conditional create: never overwrite a terminal manifest another worker
        # may have just written (the .webm is content-addressed, so a duplicate
        # upload is harmless / last-write-wins same bytes).
        wrote = _write_manifest_if_absent(
            scratch,
            manifest_path,
            _clip_manifest_bytes(manifest, status=status),
            _JSON_CONTENT_TYPE,
        )

    if not wrote:
        logger.info(
            "terminal manifest already present at write time; skipped job_id=%s clip_index=%d",
            job_id,
            clip_index,
        )
        return ClipOutcome(job_id, clip_index, OUTCOME_SKIPPED)

    logger.info("recorded clip job_id=%s clip_index=%d status=%s", job_id, clip_index, status)
    return ClipOutcome(job_id, clip_index, OUTCOME_RECORDED)


def write_fallback_manifest(
    job_id: str,
    clip_index: int,
    *,
    scratch: StorageBackend,
    reason: str,
) -> ClipOutcome:
    """Write a terminal ``is_fallback`` manifest so the barrier converges (§4).

    Honours the "never overwrite a terminal manifest" invariant: if a manifest
    (success or fallback) already exists for the index this is a no-op.
    """
    manifest_path = clip_manifest_blob_path(job_id, clip_index)
    # Fast-path skip (cheap) — the conditional write below is the authoritative
    # guard that holds under concurrency.
    if scratch.blob_exists(manifest_path):
        logger.info(
            "terminal manifest already present; not writing fallback job_id=%s clip_index=%d",
            job_id,
            clip_index,
        )
        return ClipOutcome(job_id, clip_index, OUTCOME_FALLBACK)

    repo_url: str | None = None
    try:
        repo_url = load_clipset(scratch, job_id).entry(clip_index).repo_url
    except (KeyError, ValueError):
        # No/partial clipset: still write a fallback so the editor can converge.
        repo_url = None

    manifest = ClipManifest(
        clip_id=_clip_id(clip_index),
        duration_ms=0,
        repo_url=repo_url,
        is_fallback=True,
    )
    # Conditional create so a racing success-write is never clobbered by a
    # fallback (and vice-versa): "never overwrite a terminal manifest" (§4).
    wrote = _write_manifest_if_absent(
        scratch,
        manifest_path,
        _clip_manifest_bytes(manifest, status=STATUS_FALLBACK, failure_reason=reason),
        _JSON_CONTENT_TYPE,
    )
    if wrote:
        logger.warning(
            "wrote terminal fallback manifest job_id=%s clip_index=%d reason=%s",
            job_id,
            clip_index,
            reason,
        )
    else:
        logger.info(
            "terminal manifest won by another worker; fallback not written job_id=%s clip_index=%d",
            job_id,
            clip_index,
        )
    return ClipOutcome(job_id, clip_index, OUTCOME_FALLBACK)


def process_clip_message(
    message: QueueMessage,
    *,
    scratch: StorageBackend,
    queue: Any,
    record_segment: RecordSegmentFn | None = None,
    env: Mapping[str, str] | None = None,
) -> ClipOutcome:
    """Process one ``video-clip-jobs`` message end-to-end.

    * ``dequeue_count >= MAX_DEQUEUE_COUNT`` → write fallback manifest, delete msg.
    * otherwise record the clip; delete the msg only on a terminal disposition
      (recorded/skipped/fallback). On a transient error the message is **left**
      on the queue for redelivery.

    A body that cannot be parsed into ``(job_id, clip_index)`` is unactionable
    poison: it is logged and **deleted** (mirroring
    :func:`podcaster.video.job_runner.process_message`) so it cannot crash-loop
    the recorder.
    """
    try:
        job_id, clip_index = parse_clip_job(message.body)
    except ValueError:
        logger.warning(
            "discarding malformed clip message message_id=%s dequeue_count=%d",
            message.message_id,
            message.dequeue_count,
        )
        queue.delete_message(message)
        return ClipOutcome("", -1, OUTCOME_MALFORMED)

    if message.dequeue_count >= MAX_DEQUEUE_COUNT:
        outcome = write_fallback_manifest(
            job_id,
            clip_index,
            scratch=scratch,
            reason=f"poison: dequeue_count={message.dequeue_count} >= {MAX_DEQUEUE_COUNT}",
        )
        queue.delete_message(message)
        return outcome

    try:
        outcome = record_clip(
            job_id,
            clip_index,
            scratch=scratch,
            record_segment=record_segment,
            env=env,
        )
    except Exception:  # noqa: BLE001 - leave message for retry / eventual poison
        logger.exception(
            "transient recorder failure job_id=%s clip_index=%d (left for retry)",
            job_id,
            clip_index,
        )
        return ClipOutcome(job_id, clip_index, OUTCOME_RETRY)

    queue.delete_message(message)
    return outcome


def _verify_size(scratch: StorageBackend, path: str, expected: int) -> bool:
    getter = getattr(scratch, "blob_size", None)
    if getter is None:
        return True  # best-effort: backend cannot report size
    actual = getter(path)
    return actual is not None and int(actual) == int(expected)


def _best_effort_delete(scratch: StorageBackend, path: str) -> None:
    deleter = getattr(scratch, "delete_blob", None)
    if deleter is None:
        return
    try:
        deleter(path)
    except Exception:  # pragma: no cover - defensive cleanup
        logger.debug("failed to delete unverified clip %s", path, exc_info=True)


def _fake_browser_enabled(env: Mapping[str, str]) -> bool:
    raw = env.get(ENV_FAKE_BROWSER, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _select_record_segment(env: Mapping[str, str]) -> RecordSegmentFn:
    if _fake_browser_enabled(env):
        return _fake_record_segment
    return _production_record_segment


def _fake_record_segment(segment: "VideoSegment", output_dir: Path) -> RecordResult:
    """Synthesise a tiny placeholder clip (no Chromium) for CI / fan-out tests."""
    video_path = output_dir / "clip.webm"
    # A minimal non-empty payload — the fan-out harness only asserts the blob and
    # manifest appear; compose is exercised separately with its own fakes.
    video_path.write_bytes(b"\x1aE\xdf\xa3FAKE-CLIP")
    duration_ms = int(round(float(segment.duration_seconds) * 1000))
    return RecordResult(video_path=video_path, duration_ms=duration_ms, is_fallback=False)


def _production_record_segment(segment: "VideoSegment", output_dir: Path) -> RecordResult:
    """Record one segment with a real Chromium browser via the unchanged path."""
    from playwright.sync_api import sync_playwright

    from podcaster.video.video_gen import _record_segment

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            recorded = _record_segment(
                browser,
                segment,
                output_dir,
                check_accessibility=True,
                source_url=segment.source_url,
            )
        finally:
            browser.close()

    video_path = Path(recorded.video_path)
    duration_ms = int(round(float(segment.duration_seconds) * 1000))
    return RecordResult(
        video_path=video_path,
        duration_ms=duration_ms,
        is_fallback=bool(recorded.is_fallback),
    )


def _visibility_timeout(env: Mapping[str, str]) -> int:
    raw = env.get(ENV_CLIP_VISIBILITY_TIMEOUT, "")
    if not raw.strip():
        return DEFAULT_CLIP_VISIBILITY_TIMEOUT
    try:
        value = int(raw.strip())
    except ValueError:
        return DEFAULT_CLIP_VISIBILITY_TIMEOUT
    return value if value > 0 else DEFAULT_CLIP_VISIBILITY_TIMEOUT


def drain(
    queue: Any,
    scratch: StorageBackend,
    *,
    max_messages: int = 256,
    env: Mapping[str, str] | None = None,
) -> list[ClipOutcome]:
    """Process clip messages until the queue drains or *max_messages* is hit."""
    env = env if env is not None else os.environ
    visibility = _visibility_timeout(env)
    outcomes: list[ClipOutcome] = []
    while len(outcomes) < max_messages:
        messages = queue.receive_messages(max_messages=1, visibility_timeout=visibility)
        if not messages:
            break
        for message in messages:
            outcomes.append(process_clip_message(message, scratch=scratch, queue=queue, env=env))
    return outcomes


def main(argv: list[str] | None = None) -> int:
    """ACA Job entrypoint: drain the ``video-clip-jobs`` queue, then exit."""
    logging.basicConfig(level=logging.INFO)
    scratch = create_scratch_storage_backend()
    if scratch is None:
        raise RecorderConfigError(
            "video scratch container is not configured (set PODCASTER_VIDEO_SCRATCH_CONTAINER)"
        )
    queue = create_clip_queue_backend()
    if queue is None:
        raise RecorderConfigError("clip queue is not configured (set PODCASTER_STORAGE_QUEUE_URL)")
    outcomes = drain(queue, scratch)
    logger.info("recorder drained %d clip message(s)", len(outcomes))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
