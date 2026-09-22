"""Recorder entrypoint for scale-out video recording (epic #552).

A **recorder** is a stateless, horizontally-scalable worker (KEDA min 0 / max 10)
that consumes **one** ``video-clip-jobs`` message — a ``(job_id, clip_index)``
pair — and records **exactly one** clip. It is the fan-out half of the
recorder/editor split described in ``docs/scaleout-recorder-rfc.md`` (§3, §5, §4).

Invariants (RFC §5, §4):

* **Manifest sentinel.** The per-clip ``manifest.json`` is written **strictly
  after** content-addressed ``.webm`` upload/readback and size/SHA/probe validation.
  Idempotency keys off the
  *manifest's* presence — not the clip's: a manifest present (success *or*
  fallback) means "done, skip"; a ``.webm`` present without its manifest means a
  recorder died mid-write, so we re-record and overwrite, then write the manifest.
* **Never overwrite a terminal manifest.** Once a manifest exists for an index it
  is authoritative.
* **Bounded abandonment.** Durable first admission spans redelivery; browser,
  visibility, clip-lifetime, parent fan-in, two-failed-execution, and poison
  limits converge to immutable browser-free fallback media or a fail-closed
  ``recording_insufficient`` terminal manifest.

The actual segment recording reuses the unchanged ``_record_segment`` logic from
:mod:`podcaster.video.video_gen` (imported lazily so unit tests and the
``PODCASTER_RECORDER_FAKE_BROWSER`` CI path need no Playwright/Chromium).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from podcaster.queue import (
    QueueMessage,
    create_clip_queue_backend,
    parse_clip_job,
)
from podcaster.storage import StorageBackend, create_scratch_storage_backend
from podcaster.video.budget import (
    CLIP_LIFETIME_SECONDS,
    ClipAdmission,
    RecorderTimingEnvelope,
    VideoStage,
    VideoStageBudget,
)
from podcaster.video.clip_manifest import ClipManifest
from podcaster.video.clipset import (
    ClipPlanEntry,
    Clipset,
    ClipsetJobMismatchError,
    clip_admission_blob_path,
    clip_attempts_blob_path,
    clip_blob_path,
    clip_content_blob_path,
    clip_manifest_blob_path,
    clipset_blob_path,
)
from podcaster.video.intermediates import StorageOperationTimeout, run_storage_operation
from podcaster.video.process import (
    MediaEvidence,
    OwnedCallableTimeout,
    collect_media_evidence,
    run_owned_callable,
    run_owned_process,
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
ENV_RECORDER_TIMEOUT = "PODCASTER_RECORDER_TIMEOUT"
DEFAULT_RECORDER_TIMEOUT = 900
DEFAULT_BROWSER_HARD_LIMIT_SECONDS = 600
RECORDER_FINALIZATION_RESERVE_SECONDS = 60
FAILED_EXECUTION_LIMIT = 2
ATTEMPT_STATE_SCHEMA_VERSION = 1

FALLBACK_ASSET_PATH = Path(__file__).resolve().parents[2] / "assets" / "images" / "claracle.jpeg"

_JSON_CONTENT_TYPE = "application/json; charset=utf-8"
_WEBM_CONTENT_TYPE = "video/webm"

#: Records one segment to *output_dir* and returns the produced ``.webm`` path
#: plus whether it is a fallback card. Injectable for tests / the fake path.
RecordSegmentFn = Callable[["VideoSegment", Path], "RecordResult"]

STATUS_SUCCESS = "success"
STATUS_FALLBACK = "fallback"
STATUS_RECORDING_INSUFFICIENT = "recording_insufficient"

OUTCOME_RECORDED = "recorded"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FALLBACK = "fallback"
OUTCOME_INSUFFICIENT = "recording_insufficient"
OUTCOME_RETRY = "retry"
OUTCOME_MALFORMED = "malformed"

MediaValidator = Callable[[Path, float], MediaEvidence]
FallbackRenderer = Callable[[Path, float], MediaEvidence]


@dataclass(frozen=True)
class RecordResult:
    """Output of a single segment recording: the clip file and its nature.

    Beyond the clip file, this carries the recording-outcome flags the editor
    needs to reproduce **identical** compose output (the compose path keys off
    ``is_fallback`` and ``has_pages``): ``has_pages``/``website_url`` (a GitHub
    Pages site was recorded), ``is_removed`` (planning flagged the repo gone), and
    which ``recovery_path`` produced the clip. They default to the no-website,
    direct-record case so existing callers are unaffected.
    """

    video_path: Path
    duration_ms: int
    is_fallback: bool = False
    has_pages: bool = False
    website_url: str | None = None
    is_removed: bool = False
    recovery_path: str = "direct"


@dataclass(frozen=True)
class ClipOutcome:
    """Terminal disposition of processing one clip message."""

    job_id: str
    clip_index: int
    status: str


class RecorderConfigError(RuntimeError):
    """Raised when the recorder is not configured (no scratch/queue backend)."""


class PermanentRecorderSetupError(ValueError):
    """Malformed durable recorder state that cannot succeed on redelivery."""


class ForeignClipsetRecorderSetupError(PermanentRecorderSetupError):
    """The clipset stored for a recorder message belongs to another job."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_positive_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    allow_zero: bool = False,
) -> int:
    raw = env.get(name, "")
    if not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if allow_zero and value == 0:
        return 0
    return value if value > 0 else default


def _load_or_create_admission(
    scratch: StorageBackend,
    job_id: str,
    clip_index: int,
    *,
    now_utc: datetime,
) -> ClipAdmission:
    path = clip_admission_blob_path(job_id, clip_index)
    clip_id = _clip_id(clip_index)
    captured: dict[str, object] = {}

    def _update(current: bytes | None) -> bytes:
        try:
            if current:
                document = json.loads(current.decode("utf-8"))
                admission = ClipAdmission.first_or_existing(clip_id, existing=document)
            else:
                admission = ClipAdmission.first_or_existing(clip_id, now_utc=now_utc)
        except (KeyError, TypeError, UnicodeError, ValueError) as exc:
            raise PermanentRecorderSetupError("invalid recorder admission state") from exc
        captured.update(admission.to_dict())
        return json.dumps(admission.to_dict(), separators=(",", ":")).encode("utf-8")

    scratch.update_bytes(path, _JSON_CONTENT_TYPE, _update)
    try:
        return ClipAdmission.from_dict(captured)
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise PermanentRecorderSetupError("invalid recorder admission state") from exc


def _attempt_document(payload: bytes | None) -> dict[str, Any]:
    if not payload:
        return {
            "schema_version": ATTEMPT_STATE_SCHEMA_VERSION,
            "executions": [],
        }
    document = json.loads(payload.decode("utf-8"))
    try:
        schema_version = int(document.get("schema_version", 0))
    except (AttributeError, OverflowError, TypeError, ValueError) as exc:
        raise ValueError("invalid recorder attempt history") from exc
    if (
        not isinstance(document, dict)
        or schema_version != ATTEMPT_STATE_SCHEMA_VERSION
        or not isinstance(document.get("executions"), list)
        or any(not isinstance(execution, Mapping) for execution in document["executions"])
    ):
        raise ValueError("invalid recorder attempt history")
    return document


def _begin_execution(
    scratch: StorageBackend,
    job_id: str,
    clip_index: int,
    execution_key: str,
    *,
    now_utc: datetime,
) -> int:
    """Persist this dequeue and count executions with explicit failures."""
    path = clip_attempts_blob_path(job_id, clip_index)
    failed_count = 0

    def _update(current: bytes | None) -> bytes:
        nonlocal failed_count
        try:
            document = _attempt_document(current)
        except (KeyError, TypeError, UnicodeError, ValueError) as exc:
            raise PermanentRecorderSetupError("invalid recorder attempt history") from exc
        executions = document["executions"]
        existing = next(
            (execution for execution in executions if execution.get("key") == execution_key),
            None,
        )
        if existing is None:
            executions.append(
                {
                    "key": execution_key,
                    "started_at_utc": _iso(now_utc),
                    "status": "started",
                }
            )
        failed_count = sum(1 for execution in executions if execution.get("status") == "failed")
        document["executions"] = executions[-8:]
        return json.dumps(document, separators=(",", ":")).encode("utf-8")

    scratch.update_bytes(path, _JSON_CONTENT_TYPE, _update)
    return failed_count


def _finish_execution(
    scratch: StorageBackend,
    job_id: str,
    clip_index: int,
    execution_key: str,
    *,
    status: str,
    reason: str | None,
    now_utc: datetime,
) -> int:
    path = clip_attempts_blob_path(job_id, clip_index)
    failed_count = 0

    def _update(current: bytes | None) -> bytes:
        nonlocal failed_count
        document = _attempt_document(current)
        executions = document["executions"]
        execution = next(
            (item for item in executions if item.get("key") == execution_key),
            None,
        )
        if execution is None:
            execution = {"key": execution_key, "started_at_utc": _iso(now_utc)}
            executions.append(execution)
        execution["status"] = status
        execution["finished_at_utc"] = _iso(now_utc)
        if reason:
            execution["reason"] = str(reason)[:256]
        failed_count = sum(1 for item in executions if item.get("status") == "failed")
        document["executions"] = executions[-8:]
        return json.dumps(document, separators=(",", ":")).encode("utf-8")

    scratch.update_bytes(path, _JSON_CONTENT_TYPE, _update)
    return failed_count


def _load_attempts(scratch: StorageBackend, job_id: str, clip_index: int) -> dict[str, Any]:
    return _attempt_document(scratch.get_bytes(clip_attempts_blob_path(job_id, clip_index)))


def _browser_deadline(
    admission: ClipAdmission,
    clipset: Clipset,
    env: Mapping[str, str],
) -> datetime:
    if clipset.budget is None:
        raise ValueError("clipset is missing the parent video budget")
    hard_limit = _parse_positive_int(
        env,
        "VIDEO_MAX_CLIP_RECORD_SECONDS",
        DEFAULT_BROWSER_HARD_LIMIT_SECONDS,
        allow_zero=True,
    )
    if hard_limit == 0:
        hard_limit = CLIP_LIFETIME_SECONDS
    visibility_limit = max(
        0,
        _visibility_timeout(env) - RECORDER_FINALIZATION_RESERVE_SECONDS,
    )
    replica_limit = max(
        0,
        _parse_positive_int(env, ENV_RECORDER_TIMEOUT, DEFAULT_RECORDER_TIMEOUT)
        - RECORDER_FINALIZATION_RESERVE_SECONDS,
    )
    seconds = min(
        CLIP_LIFETIME_SECONDS,
        hard_limit,
        visibility_limit,
        replica_limit,
    )
    return min(
        admission.first_admitted_at_utc + timedelta(seconds=seconds),
        clipset.budget.stage_deadline_at_utc(VideoStage.FANIN),
    )


def _timing_envelope(
    clipset: Clipset,
    admission: ClipAdmission,
    env: Mapping[str, str],
) -> RecorderTimingEnvelope:
    if clipset.budget is None:
        raise ValueError("clipset is missing the parent video budget")
    return RecorderTimingEnvelope(
        budget=clipset.budget,
        clip=admission,
        browser_deadline_at_utc=_browser_deadline(admission, clipset, env),
    )


def _remaining_seconds(
    envelope: RecorderTimingEnvelope,
    parent_budget: VideoStageBudget,
    *,
    admitted_at_utc: datetime,
    admitted_at_monotonic: float,
    utcnow: Callable[[], datetime],
    monotonic: Callable[[], float],
) -> float:
    durable = (envelope.effective_deadline_at_utc - utcnow()).total_seconds()
    initial = (envelope.effective_deadline_at_utc - admitted_at_utc).total_seconds()
    local = initial - (monotonic() - admitted_at_monotonic)
    return max(
        0.0,
        min(durable, local, parent_budget.remaining_seconds(VideoStage.FANIN)),
    )


def _remaining_finalization_seconds(
    envelope: RecorderTimingEnvelope,
    parent_budget: VideoStageBudget,
    *,
    admitted_at_utc: datetime,
    admitted_at_monotonic: float,
    utcnow: Callable[[], datetime],
    monotonic: Callable[[], float],
) -> float:
    deadline = min(
        envelope.browser_deadline_at_utc + timedelta(seconds=RECORDER_FINALIZATION_RESERVE_SECONDS),
        envelope.budget.stage_deadline_at_utc(VideoStage.FALLBACK),
    )
    durable = (deadline - utcnow()).total_seconds()
    initial = (deadline - admitted_at_utc).total_seconds()
    local = initial - (monotonic() - admitted_at_monotonic)
    return max(
        0.0,
        min(durable, local, parent_budget.remaining_seconds(VideoStage.FALLBACK)),
    )


def _run_queue_operation(call: Callable[[], Any], timeout_seconds: float) -> Any:
    try:
        return run_owned_callable(
            call,
            timeout_seconds,
            process_name="recorder-queue-disposition",
        )
    except OwnedCallableTimeout as exc:
        raise TimeoutError(
            f"queue disposition exceeded {timeout_seconds:.3f}s recorder budget"
        ) from exc


def _delete_queue_message(
    queue: Any,
    message: QueueMessage,
    *,
    remaining_seconds: float,
    operation_runner: Callable[[Callable[[], Any], float], Any] = _run_queue_operation,
) -> bool:
    timeout = max(0.0, remaining_seconds)
    if timeout <= 0:
        logger.warning(
            "retaining clip message with no finalization budget message_id=%s dequeue_count=%d",
            message.message_id,
            message.dequeue_count,
        )
        return False
    try:
        operation_runner(lambda: queue.delete_message(message), timeout)
        return True
    except Exception:
        logger.warning(
            "clip queue disposition failed; retaining message message_id=%s dequeue_count=%d",
            message.message_id,
            message.dequeue_count,
            exc_info=True,
        )
        return False


def load_clipset(scratch: StorageBackend, job_id: str) -> Clipset:
    """Load and parse the editor-written ``clipset.json`` for *job_id*."""
    payload = scratch.get_bytes(clipset_blob_path(job_id))
    if payload is None:
        raise FileNotFoundError(f"clipset.json is unavailable for job {job_id}")
    try:
        return Clipset.from_bytes(payload, expected_job_id=job_id)
    except ClipsetJobMismatchError as exc:
        raise ForeignClipsetRecorderSetupError("invalid recorder clipset") from exc
    except (KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise PermanentRecorderSetupError("invalid recorder clipset") from exc


def _clip_manifest_bytes(
    manifest: ClipManifest,
    *,
    status: str,
    failure_reason: str | None = None,
    recording: "RecordResult | None" = None,
    media: MediaEvidence | None = None,
    media_blob_path: str | None = None,
    terminal_at_utc: datetime | None = None,
    attempts: Mapping[str, Any] | None = None,
) -> bytes:
    """Serialise a clip manifest with the recorder's terminal ``status`` marker.

    Extends ``ClipManifest.to_dict()`` with ``status`` (``success``/``fallback``)
    and an optional ``failure_reason`` without modifying the shared
    :class:`ClipManifest` schema — :meth:`ClipManifest.from_dict` ignores the
    extra keys, so the manifest still round-trips for the editor/EDL. When a
    *recording* is supplied its outcome flags (``has_pages``/``website_url``/
    ``is_removed``/``recovery_path``) are persisted too so the editor reproduces
    identical compose output (RFC §5).
    """
    data: dict[str, Any] = dict(manifest.to_dict())
    data["status"] = status
    if failure_reason:
        data["failure_reason"] = failure_reason
    if recording is not None:
        data["has_pages"] = bool(recording.has_pages)
        data["website_url"] = recording.website_url
        data["is_removed"] = bool(recording.is_removed)
        data["recovery_path"] = recording.recovery_path
    if media is not None:
        data["media"] = media.to_dict()
    if media_blob_path is not None:
        data["media_blob_path"] = media_blob_path
    if terminal_at_utc is not None:
        data["terminal_at_utc"] = _iso(terminal_at_utc)
    if attempts is not None:
        data["attempts"] = dict(attempts)
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
    timeout_seconds: float | None = None,
    media_validator: MediaValidator | None = None,
    terminal_at_utc: datetime | None = None,
    terminal_utcnow: Callable[[], datetime] = _utc_now,
    attempts: Mapping[str, Any] | None = None,
    admission_check: Callable[[], float] | None = None,
    capture_admission_check: Callable[[], float] | None = None,
    finalize_attempts: Callable[[], Mapping[str, Any]] | None = None,
    operation_runner: Callable[[Callable[[], Any], float], Any] = run_storage_operation,
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
        record_segment = _select_record_segment(env, timeout_seconds=timeout_seconds)
    if media_validator is None:
        media_validator = _validate_media

    def _finalize(call: Callable[[], Any]) -> Any:
        if admission_check is None:
            return call()
        remaining = admission_check()
        if remaining <= 0:
            raise StorageOperationTimeout("recorder deadline reached during clip finalization")
        return operation_runner(call, remaining)

    with tempfile.TemporaryDirectory(prefix=f"clip-{clip_index:03d}-") as tmp:
        output_dir = Path(tmp)
        result = record_segment(segment, output_dir)
        video_path = Path(result.video_path)
        if not video_path.exists():
            raise RuntimeError(
                f"recorder produced no clip file for job_id={job_id} clip_index={clip_index}"
            )
        if capture_admission_check is not None and capture_admission_check() <= 0:
            raise TimeoutError("recorder deadline reached before clip finalization")

        evidence = _finalize(
            lambda: media_validator(video_path, max(0.001, float(timeout_seconds or 30.0)))
        )
        if admission_check is not None and admission_check() <= 0:
            raise TimeoutError("recorder deadline reached before clip finalization")
        content_path = clip_content_blob_path(job_id, clip_index, evidence.sha256)
        legacy_path = clip_blob_path(job_id, clip_index)
        # Re-check the sentinel after the (potentially slow) record: a concurrent
        # recorder may have completed this clip while we worked. If so, leave the
        # authoritative clip/manifest pair untouched and skip.
        if _finalize(lambda: scratch.blob_exists(manifest_path)):
            logger.info(
                "terminal manifest appeared during recording; skipping write "
                "job_id=%s clip_index=%d",
                job_id,
                clip_index,
            )
            return ClipOutcome(job_id, clip_index, OUTCOME_SKIPPED)

        _finalize(lambda: scratch.upload_file(content_path, video_path, _WEBM_CONTENT_TYPE))
        if not _finalize(lambda: _verify_size(scratch, content_path, evidence.size_bytes)):
            # Drop the torn upload so the manifest is never written over an
            # unverified clip; the message is retried (no manifest = not done).
            _best_effort_delete(scratch, content_path)
            raise RuntimeError(
                f"clip size verification failed for job_id={job_id} clip_index={clip_index}"
            )
        try:
            _finalize(
                lambda: _verify_uploaded_content(
                    scratch,
                    content_path,
                    evidence,
                    output_dir / "content-readback.webm",
                )
            )
        except Exception:
            _best_effort_delete(scratch, content_path)
            raise
        # Preserve the legacy raw path for old diagnostics/integration consumers.
        # New manifests bind and editors consume only the immutable content path.
        _finalize(lambda: scratch.upload_file(legacy_path, video_path, _WEBM_CONTENT_TYPE))
        if not _finalize(lambda: _verify_size(scratch, legacy_path, evidence.size_bytes)):
            _best_effort_delete(scratch, legacy_path)
            _best_effort_delete(scratch, content_path)
            raise RuntimeError(
                f"legacy clip size verification failed for job_id={job_id} clip_index={clip_index}"
            )

        manifest = ClipManifest(
            clip_id=_clip_id(clip_index),
            duration_ms=int(result.duration_ms),
            repo_url=entry.repo_url,
            is_fallback=bool(result.is_fallback),
        )
        status = STATUS_FALLBACK if result.is_fallback else STATUS_SUCCESS
        if finalize_attempts is not None:
            attempts = _finalize(finalize_attempts)
        # Conditional create: never overwrite a terminal manifest another worker
        # may have just written (the .webm is content-addressed, so a duplicate
        # upload is harmless / last-write-wins same bytes).
        wrote = _finalize(
            lambda: _write_manifest_if_absent(
                scratch,
                manifest_path,
                _clip_manifest_bytes(
                    manifest,
                    status=status,
                    recording=result,
                    media=evidence,
                    media_blob_path=content_path,
                    terminal_at_utc=terminal_at_utc or terminal_utcnow(),
                    attempts=attempts,
                ),
                _JSON_CONTENT_TYPE,
            )
        )

    if not wrote:
        winner = _finalize(lambda: _read_manifest(scratch, manifest_path))
        if winner.get("media_blob_path") != content_path:
            _best_effort_delete(scratch, content_path)
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
    timeout_seconds: float = 30.0,
    renderer: FallbackRenderer | None = None,
    terminal_at_utc: datetime | None = None,
    terminal_utcnow: Callable[[], datetime] = _utc_now,
    attempts: Mapping[str, Any] | None = None,
    admission_check: Callable[[], float] | None = None,
    operation_runner: Callable[[Callable[[], Any], float], Any] = run_storage_operation,
) -> ClipOutcome:
    """Create immutable local fallback media, then CAS its terminal manifest.

    Honours the "never overwrite a terminal manifest" invariant: if a manifest
    (success or fallback) already exists for the index this is a no-op.
    """
    manifest_path = clip_manifest_blob_path(job_id, clip_index)

    def _finalize(
        call: Callable[[], Any],
        requested_seconds: float | None = None,
    ) -> Any:
        if admission_check is None:
            return call()
        remaining = admission_check()
        timeout = remaining if requested_seconds is None else min(remaining, requested_seconds)
        if timeout <= 0:
            raise StorageOperationTimeout("recorder deadline reached during fallback finalization")
        return operation_runner(call, timeout)

    # Fast-path skip (cheap) — the conditional write below is the authoritative
    # guard that holds under concurrency.
    if _finalize(lambda: scratch.blob_exists(manifest_path)):
        logger.info(
            "terminal manifest already present; not writing fallback job_id=%s clip_index=%d",
            job_id,
            clip_index,
        )
        return ClipOutcome(job_id, clip_index, OUTCOME_FALLBACK)

    repo_url: str | None = None
    try:
        repo_url = _finalize(lambda: load_clipset(scratch, job_id)).entry(clip_index).repo_url
    except (KeyError, ValueError):
        repo_url = None

    renderer = renderer or _render_static_fallback

    media: MediaEvidence | None = None
    content_path: str | None = None
    render_error: str | None = None
    try:
        if timeout_seconds <= 0:
            raise TimeoutError("no fallback rendering budget remains")
        with tempfile.TemporaryDirectory(prefix=f"fallback-{clip_index:03d}-") as tmp:
            output_path = Path(tmp) / "fallback.webm"
            media = _finalize(
                lambda: renderer(output_path, timeout_seconds),
                timeout_seconds,
            )
            content_path = clip_content_blob_path(job_id, clip_index, media.sha256)
            _finalize(lambda: scratch.upload_file(content_path, output_path, _WEBM_CONTENT_TYPE))
            if not _finalize(lambda: _verify_size(scratch, content_path, media.size_bytes)):
                _best_effort_delete(scratch, content_path)
                raise RuntimeError("fallback content-addressed upload size mismatch")
            _finalize(
                lambda: _verify_uploaded_content(
                    scratch,
                    content_path,
                    media,
                    Path(tmp) / "fallback-readback.webm",
                )
            )
            # Keep the old per-index path readable, but never consume it for a
            # hash-bound terminal manifest.
            legacy_path = clip_blob_path(job_id, clip_index)
            _finalize(lambda: scratch.upload_file(legacy_path, output_path, _WEBM_CONTENT_TYPE))
    except StorageOperationTimeout:
        if content_path is not None:
            _best_effort_delete(scratch, content_path)
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed without browser/network
        if content_path is not None:
            _best_effort_delete(scratch, content_path)
            content_path = None
            media = None
        render_error = f"{type(exc).__name__}: {exc}"[:256]
        logger.warning(
            "static fallback unavailable job_id=%s clip_index=%d error=%s",
            job_id,
            clip_index,
            render_error,
        )

    status = (
        STATUS_FALLBACK
        if media is not None and content_path is not None
        else (STATUS_RECORDING_INSUFFICIENT)
    )
    terminal_at = terminal_at_utc or terminal_utcnow()
    manifest = ClipManifest(
        clip_id=_clip_id(clip_index),
        duration_ms=1000 if status == STATUS_FALLBACK else 0,
        repo_url=repo_url,
        is_fallback=True,
    )
    wrote = _finalize(
        lambda: _write_manifest_if_absent(
            scratch,
            manifest_path,
            _clip_manifest_bytes(
                manifest,
                status=status,
                failure_reason=reason if render_error is None else f"{reason}; {render_error}",
                media=media,
                media_blob_path=content_path,
                terminal_at_utc=terminal_at,
                attempts=attempts,
            ),
            _JSON_CONTENT_TYPE,
        )
    )
    if wrote:
        logger.warning(
            "wrote terminal %s manifest job_id=%s clip_index=%d reason=%s",
            status,
            job_id,
            clip_index,
            reason,
        )
    else:
        winner = _finalize(lambda: _read_manifest(scratch, manifest_path))
        if content_path is not None and winner.get("media_blob_path") != content_path:
            _best_effort_delete(scratch, content_path)
        logger.info(
            "terminal manifest won by another worker; fallback not written job_id=%s clip_index=%d",
            job_id,
            clip_index,
        )
    return ClipOutcome(
        job_id,
        clip_index,
        OUTCOME_FALLBACK if status == STATUS_FALLBACK else OUTCOME_INSUFFICIENT,
    )


def process_clip_message(
    message: QueueMessage,
    *,
    scratch: StorageBackend,
    queue: Any,
    record_segment: RecordSegmentFn | None = None,
    env: Mapping[str, str] | None = None,
    utcnow: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] | None = None,
    media_validator: MediaValidator | None = None,
    fallback_renderer: FallbackRenderer | None = None,
    setup_operation_runner: Callable[[Callable[[], Any], float], Any] = run_storage_operation,
    queue_operation_runner: Callable[[Callable[[], Any], float], Any] = _run_queue_operation,
) -> ClipOutcome:
    """Process one ``video-clip-jobs`` message end-to-end.

    * ``dequeue_count >= MAX_DEQUEUE_COUNT`` → write fallback manifest, then try
      bounded queue disposition with the remaining finalization budget.
    * otherwise record the clip; only terminal dispositions
      (recorded/skipped/fallback) attempt queue deletion. Failed/timed-out queue
      deletion leaves the message for redelivery.

    A body that cannot be parsed into ``(job_id, clip_index)`` is unactionable
    poison: it is logged and **deleted** (mirroring
    :func:`podcaster.video.job_runner.process_message`) so it cannot crash-loop
    the recorder.
    """
    env = env if env is not None else os.environ
    monotonic = monotonic or __import__("time").monotonic
    try:
        job_id, clip_index = parse_clip_job(message.body)
    except ValueError:
        logger.warning(
            "discarding malformed clip message message_id=%s dequeue_count=%d",
            message.message_id,
            message.dequeue_count,
        )
        _delete_queue_message(
            queue,
            message,
            remaining_seconds=30.0,
            operation_runner=queue_operation_runner,
        )
        return ClipOutcome("", -1, OUTCOME_MALFORMED)

    now_utc = utcnow()
    execution_key = f"{message.message_id}:{message.dequeue_count}"

    try:
        clipset = setup_operation_runner(
            lambda: load_clipset(scratch, job_id),
            30.0,
        )
        admission = setup_operation_runner(
            lambda: _load_or_create_admission(
                scratch,
                job_id,
                clip_index,
                now_utc=now_utc,
            ),
            30.0,
        )
        try:
            envelope = _timing_envelope(clipset, admission, env)
            parent_budget = VideoStageBudget.from_dict(
                envelope.budget.to_dict(),
                now_utc=now_utc,
                monotonic=monotonic,
                utcnow=utcnow,
            )
        except (KeyError, TypeError, UnicodeError, ValueError) as exc:
            raise PermanentRecorderSetupError("invalid recorder timing state") from exc
        admitted_at_monotonic = monotonic()
        failed_before = setup_operation_runner(
            lambda: _begin_execution(
                scratch,
                job_id,
                clip_index,
                execution_key,
                now_utc=now_utc,
            ),
            30.0,
        )
    except PermanentRecorderSetupError as exc:
        outcome = write_fallback_manifest(
            job_id,
            clip_index,
            scratch=scratch,
            reason=f"recording timing unavailable: {type(exc).__name__}",
            timeout_seconds=30 if isinstance(exc, ForeignClipsetRecorderSetupError) else 0,
            renderer=fallback_renderer,
            terminal_utcnow=utcnow,
        )
        _delete_queue_message(
            queue,
            message,
            remaining_seconds=30.0,
            operation_runner=queue_operation_runner,
        )
        return outcome
    except Exception:
        logger.exception(
            "transient recorder setup failure job_id=%s clip_index=%d (left for retry)",
            job_id,
            clip_index,
        )
        return ClipOutcome(job_id, clip_index, OUTCOME_RETRY)

    remaining = _remaining_seconds(
        envelope,
        parent_budget,
        admitted_at_utc=now_utc,
        admitted_at_monotonic=admitted_at_monotonic,
        utcnow=utcnow,
        monotonic=monotonic,
    )

    def _remaining_finalization() -> float:
        return _remaining_finalization_seconds(
            envelope,
            parent_budget,
            admitted_at_utc=now_utc,
            admitted_at_monotonic=admitted_at_monotonic,
            utcnow=utcnow,
            monotonic=monotonic,
        )

    if scratch.blob_exists(clip_manifest_blob_path(job_id, clip_index)):
        _delete_queue_message(
            queue,
            message,
            remaining_seconds=_remaining_finalization(),
            operation_runner=queue_operation_runner,
        )
        return ClipOutcome(job_id, clip_index, OUTCOME_SKIPPED)

    def _remaining_capture() -> float:
        return _remaining_seconds(
            envelope,
            parent_budget,
            admitted_at_utc=now_utc,
            admitted_at_monotonic=admitted_at_monotonic,
            utcnow=utcnow,
            monotonic=monotonic,
        )

    terminal_reason: str | None = None
    if message.dequeue_count >= MAX_DEQUEUE_COUNT:
        terminal_reason = f"poison: dequeue_count={message.dequeue_count} >= {MAX_DEQUEUE_COUNT}"
    elif failed_before >= FAILED_EXECUTION_LIMIT:
        terminal_reason = f"failed_executions={failed_before} >= {FAILED_EXECUTION_LIMIT}"
    elif remaining <= 0:
        terminal_reason = "recorder deadline reached before browser admission"

    if terminal_reason is not None:
        outcome = write_fallback_manifest(
            job_id,
            clip_index,
            scratch=scratch,
            reason=terminal_reason,
            timeout_seconds=parent_budget.operation_timeout(VideoStage.FALLBACK, 30),
            renderer=fallback_renderer,
            terminal_utcnow=utcnow,
            attempts=_load_attempts(scratch, job_id, clip_index),
            admission_check=_remaining_finalization,
        )
        _delete_queue_message(
            queue,
            message,
            remaining_seconds=_remaining_finalization(),
            operation_runner=queue_operation_runner,
        )
        return outcome

    def _finalize_attempts() -> Mapping[str, Any]:
        _finish_execution(
            scratch,
            job_id,
            clip_index,
            execution_key,
            status="succeeded",
            reason=None,
            now_utc=utcnow(),
        )
        return _load_attempts(scratch, job_id, clip_index)

    try:
        outcome = record_clip(
            job_id,
            clip_index,
            scratch=scratch,
            record_segment=record_segment,
            env=env,
            timeout_seconds=remaining,
            media_validator=media_validator,
            terminal_utcnow=utcnow,
            attempts=_load_attempts(scratch, job_id, clip_index),
            admission_check=_remaining_finalization,
            capture_admission_check=_remaining_capture,
            finalize_attempts=_finalize_attempts,
        )
    except Exception as exc:  # noqa: BLE001 - retry once, then terminalize
        failed_count = _finish_execution(
            scratch,
            job_id,
            clip_index,
            execution_key,
            status="failed",
            reason=type(exc).__name__,
            now_utc=utcnow(),
        )
        logger.exception(
            "transient recorder failure job_id=%s clip_index=%d (left for retry)",
            job_id,
            clip_index,
        )
        remaining = _remaining_seconds(
            envelope,
            parent_budget,
            admitted_at_utc=now_utc,
            admitted_at_monotonic=admitted_at_monotonic,
            utcnow=utcnow,
            monotonic=monotonic,
        )
        if failed_count >= FAILED_EXECUTION_LIMIT or remaining <= 0:
            outcome = write_fallback_manifest(
                job_id,
                clip_index,
                scratch=scratch,
                reason=(
                    f"failed_executions={failed_count}"
                    if failed_count >= FAILED_EXECUTION_LIMIT
                    else "recorder deadline reached after failed execution"
                ),
                timeout_seconds=parent_budget.operation_timeout(VideoStage.FALLBACK, 30),
                renderer=fallback_renderer,
                terminal_utcnow=utcnow,
                attempts=_load_attempts(scratch, job_id, clip_index),
                admission_check=_remaining_finalization,
            )
            _delete_queue_message(
                queue,
                message,
                remaining_seconds=_remaining_finalization(),
                operation_runner=queue_operation_runner,
            )
            return outcome
        return ClipOutcome(job_id, clip_index, OUTCOME_RETRY)

    _delete_queue_message(
        queue,
        message,
        remaining_seconds=_remaining_finalization(),
        operation_runner=queue_operation_runner,
    )
    return outcome


def _verify_size(scratch: StorageBackend, path: str, expected: int) -> bool:
    getter = getattr(scratch, "blob_size", None)
    if getter is None:
        return True  # best-effort: backend cannot report size
    actual = getter(path)
    return actual is not None and int(actual) == int(expected)


def _verify_uploaded_content(
    scratch: StorageBackend,
    path: str,
    expected: MediaEvidence,
    readback_path: Path,
) -> None:
    if not scratch.download_file(path, readback_path):
        raise RuntimeError(f"uploaded media could not be read back: {path}")
    payload_size = readback_path.stat().st_size
    digest = hashlib.sha256()
    with readback_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if payload_size != expected.size_bytes or digest.hexdigest() != expected.sha256:
        raise RuntimeError(f"uploaded media identity mismatch: {path}")


def _read_manifest(scratch: StorageBackend, path: str) -> dict[str, Any]:
    payload = scratch.get_bytes(path)
    if not payload:
        return {}
    try:
        document = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _validate_media(path: Path, timeout_seconds: float) -> MediaEvidence:
    return collect_media_evidence(path, timeout_seconds=timeout_seconds)


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


def _select_record_segment(
    env: Mapping[str, str],
    *,
    timeout_seconds: float | None = None,
) -> RecordSegmentFn:
    if _fake_browser_enabled(env):
        return _fake_record_segment
    return lambda segment, output_dir: _owned_production_record_segment(
        segment,
        output_dir,
        timeout_seconds=max(0.001, float(timeout_seconds or DEFAULT_BROWSER_HARD_LIMIT_SECONDS)),
    )


def _fake_record_segment(segment: "VideoSegment", output_dir: Path) -> RecordResult:
    """Render a valid deterministic local placeholder (no Chromium/network)."""
    from podcaster.video.video_gen import capped_record_seconds

    video_path = output_dir / "clip.webm"
    _render_static_fallback(video_path, 30.0)
    # Report the cap-clamped duration so the manifest matches what a real
    # recording would produce for an over-long segment (issue #592).
    duration_ms = int(round(capped_record_seconds(float(segment.duration_seconds)) * 1000))
    return RecordResult(video_path=video_path, duration_ms=duration_ms, is_fallback=False)


def _render_static_fallback(output_path: Path, timeout_seconds: float) -> MediaEvidence:
    """Render the fixed repository-owned fallback asset with deterministic inputs."""
    if timeout_seconds <= 0:
        raise TimeoutError("no fallback rendering budget remains")
    if not FALLBACK_ASSET_PATH.is_file():
        raise FileNotFoundError(f"fallback asset is missing: {FALLBACK_ASSET_PATH}")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-loop",
        "1",
        "-i",
        str(FALLBACK_ASSET_PATH),
        "-t",
        "1",
        "-vf",
        (
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
        ),
        "-an",
        "-r",
        "30",
        "-c:v",
        "libvpx-vp9",
        "-deadline",
        "good",
        "-cpu-used",
        "4",
        "-threads",
        "1",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        "-y",
        str(output_path),
    ]
    run_owned_process(
        command,
        timeout_seconds=timeout_seconds,
        output_paths=(output_path,),
        check=True,
    )
    return collect_media_evidence(
        output_path,
        timeout_seconds=max(0.001, min(10.0, timeout_seconds)),
    )


def _owned_production_record_segment(
    segment: "VideoSegment",
    output_dir: Path,
    *,
    timeout_seconds: float,
) -> RecordResult:
    """Run Playwright recording behind an owned killable process boundary."""
    entry = ClipPlanEntry.from_segment(0, segment)
    payload = json.dumps(
        {"segment": entry.to_dict(), "output_dir": str(Path(output_dir).resolve())},
        separators=(",", ":"),
    )
    try:
        result = run_owned_process(
            [sys.executable, "-m", "podcaster.video.recorder", "--record-one"],
            timeout_seconds=timeout_seconds,
            input_text=payload,
            check=True,
        )
    except Exception:
        for partial in Path(output_dir).glob("*.webm"):
            partial.unlink(missing_ok=True)
        raise
    document = json.loads(result.stdout)
    return RecordResult(
        video_path=Path(document["video_path"]),
        duration_ms=int(document["duration_ms"]),
        is_fallback=bool(document.get("is_fallback", False)),
        has_pages=bool(document.get("has_pages", False)),
        website_url=document.get("website_url"),
        is_removed=bool(document.get("is_removed", False)),
        recovery_path=str(document.get("recovery_path", "direct")),
    )


def _production_record_segment(segment: "VideoSegment", output_dir: Path) -> RecordResult:
    """Record one segment with a real Chromium browser via the unchanged path."""
    from playwright.sync_api import sync_playwright

    from podcaster.video.video_gen import _record_segment, capped_record_seconds

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
    # The recorder caps the effective recording length (issue #592), so the
    # realized clip is at most ``MAX_CLIP_RECORD_SECONDS`` long. Persist the
    # cap-clamped duration in the manifest so the editor's EDL trims/loops within
    # the clip's actual bounds (never seeking past EOF).
    duration_ms = int(round(capped_record_seconds(float(segment.duration_seconds)) * 1000))
    return RecordResult(
        video_path=video_path,
        duration_ms=duration_ms,
        is_fallback=bool(recorded.is_fallback),
        has_pages=bool(getattr(recorded, "has_pages", False)),
        website_url=getattr(recorded, "website_url", None),
        is_removed=bool(getattr(recorded, "is_removed", False)),
        recovery_path=str(getattr(recorded, "recovery_path", "direct")),
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


def _run_record_one_child() -> int:
    payload = json.loads(sys.stdin.read())
    entry = ClipPlanEntry.from_dict(payload["segment"])
    result = _production_record_segment(entry.to_segment(), Path(payload["output_dir"]))
    sys.stdout.write(
        json.dumps(
            {
                "video_path": str(Path(result.video_path).resolve()),
                "duration_ms": result.duration_ms,
                "is_fallback": result.is_fallback,
                "has_pages": result.has_pages,
                "website_url": result.website_url,
                "is_removed": result.is_removed,
                "recovery_path": result.recovery_path,
            },
            separators=(",", ":"),
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """ACA Job entrypoint: drain the ``video-clip-jobs`` queue, then exit."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--record-one"]:
        return _run_record_one_child()
    logging.basicConfig(level=logging.INFO)
    scratch = create_scratch_storage_backend()
    if scratch is None:
        raise RecorderConfigError(
            "video scratch container is not configured (set PODCASTER_VIDEO_SCRATCH_CONTAINER)"
        )
    queue = create_clip_queue_backend()
    if queue is None:
        raise RecorderConfigError("clip queue is not configured (set PODCASTER_STORAGE_QUEUE_URL)")
    outcomes = drain(queue, scratch, max_messages=1)
    logger.info("recorder drained %d clip message(s)", len(outcomes))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
