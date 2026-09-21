"""Editor / orchestrator fan-out + fan-in for scale-out video (epic #552).

The scale-out design (``docs/scaleout-recorder-rfc.md`` §3) splits the video
pipeline into a **recorder** (records one clip per ``video-clip-jobs`` message,
:mod:`podcaster.video.recorder`) and an **editor** (the existing
:mod:`podcaster.video.job_runner`, refactored). This module owns the editor's
fan-out / fan-in seam so ``run_video_generation``'s compose/distribute path stays
**byte-for-byte unchanged**: :func:`record_via_fanout` returns the same
:class:`podcaster.video.video_gen.RecordingResult` the inline ``record_episode``
returns today.

Responsibilities (RFC §5, §6, §8):

* **Editor execution lease** (§6.2). ``pipeline_lock`` only guards audio-vs-video
  and permits a same-pipeline re-confirm, so two editors for one ``job_id`` could
  both proceed if KEDA transiently over-provisions. A dedicated lease blob
  (``video-jobs/{job_id}/editor.lease.json``), claimed via ``update_bytes`` CAS and
  heartbeat-renewed, makes a second editor that sees an **unexpired foreign lease**
  exit without working. The lease is a **dedicated blob** (not the shared video
  state manifest) precisely because that manifest is rewritten with a non-CAS
  ``put_bytes`` — co-locating the lease there would let a state write clobber it.
* **Immutable ``clipset.json``** (§6.3). Written **create-if-absent**; on redelivery
  the editor loads the existing plan as the source of truth instead of re-planning,
  so the expected clip set can't drift if script/metadata changed between attempts.
* **Additive fan-out** (§6.3). Only indices with no terminal manifest yet are
  (re-)enqueued; the recorder's "never overwrite a terminal manifest" rule plus
  content-addressed paths make a duplicate in-flight recorder harmless.
* **Per-index fan-in barrier** (§5). Poll ``blob_exists`` on each expected clip's
  manifest — **not** ``list_blobs`` (which caps at 10). Complete iff every index has
  a terminal manifest (success *or* fallback).
* **Bounded fallback** (§8). At fan-in cutoff the editor creates a deterministic
  local static clip and hash-bound terminal manifest, or records
  ``recording_insufficient`` before composition. It never launches Chromium or
  network work from the fallback path.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from podcaster.queue import QueueProducer, enqueue_clip_job
from podcaster.storage import StorageBackend
from podcaster.video.budget import VideoStage, VideoStageBudget
from podcaster.video.clip_manifest import CLIP_MANIFEST_SCHEMA_VERSION
from podcaster.video.clipset import (
    Clipset,
    ClipsetJobMismatchError,
    clip_blob_path,
    clip_content_blob_path,
    clip_manifest_blob_path,
    clips_prefix,
    clipset_blob_path,
    job_prefix,
)
from podcaster.video.intermediates import StorageOperationTimeout, run_storage_operation
from podcaster.video.process import MediaEvidence, MediaValidationError, collect_media_evidence
from podcaster.video.recorder import STATUS_FALLBACK, STATUS_RECORDING_INSUFFICIENT, STATUS_SUCCESS
from podcaster.video.sync_plan import VideoSegment
from podcaster.video.video_gen import RecordedSegment, RecordingResult

logger = logging.getLogger("podcaster.video.editor")

_JSON_CONTENT_TYPE = "application/json; charset=utf-8"
_WEBM_CONTENT_TYPE = "video/webm"

#: Default editor lease TTL (seconds). Comfortably exceeds one barrier poll so the
#: heartbeat renewal (each poll) keeps a live editor's lease from expiring, while a
#: crashed editor's lease frees for a retry within this window.
DEFAULT_LEASE_TTL_SECONDS = 1800

#: Default bound on the fan-in wait (seconds). On timeout the editor composes with
#: whatever terminal manifests exist (fallbacks fill the gaps) rather than hanging.
DEFAULT_FANIN_TIMEOUT_SECONDS = 1200

#: Seconds between fan-in barrier polls.
DEFAULT_FANIN_POLL_SECONDS = 15

#: Legacy injectable gap filler retained for callers that explicitly provide one.
#: Production terminalization uses the hash-bound static fallback path instead.
FillGapFn = Callable[[VideoSegment, Path, int], RecordedSegment]
MediaValidator = Callable[[Path, MediaEvidence | None, float], MediaEvidence]


class RecordingInsufficientError(RuntimeError):
    """A terminal clip cannot provide validated media for composition."""

    def __init__(self, job_id: str, clip_index: int, reason: str) -> None:
        super().__init__(
            f"recording insufficient for job_id={job_id} clip_index={clip_index}: {reason}"
        )
        self.job_id = job_id
        self.clip_index = clip_index
        self.reason = reason


def editor_lease_blob_path(job_id: str) -> str:
    """Blob path of the dedicated editor execution lease for *job_id*."""
    return f"{job_prefix(job_id)}editor.lease.json"


@dataclass(frozen=True)
class EditorLease:
    """A heartbeat-renewed editor execution lease (RFC §6.2)."""

    run_id: str
    claimed_at: datetime
    expires_at: datetime

    def to_bytes(self) -> bytes:
        import json

        return json.dumps(
            {
                "run_id": self.run_id,
                "claimed_at": _iso(self.claimed_at),
                "expires_at": _iso(self.expires_at),
            },
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, payload: bytes | None) -> "EditorLease | None":
        if not payload:
            return None
        import json

        try:
            data = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        run_id = data.get("run_id")
        claimed = _parse_iso(data.get("claimed_at"))
        expires = _parse_iso(data.get("expires_at"))
        if not isinstance(run_id, str) or claimed is None or expires is None:
            return None
        return cls(run_id=run_id, claimed_at=claimed, expires_at=expires)


def acquire_or_renew_lease(
    scratch: StorageBackend,
    job_id: str,
    run_id: str,
    *,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
) -> bool:
    """Claim or heartbeat-renew the editor lease for *job_id* via CAS.

    Returns ``True`` when *run_id* holds the lease after this call (it was free,
    expired, or already ours), ``False`` when an **unexpired foreign** lease is
    present — in which case the caller must exit without working (RFC §6.2).
    """
    current_now = now or datetime.now(timezone.utc)
    expires_at = current_now + timedelta(seconds=ttl_seconds)
    acquired = {"ok": False}

    def _update(current: bytes | None) -> bytes:
        existing = EditorLease.from_bytes(current)
        if existing is not None and existing.run_id != run_id and existing.expires_at > current_now:
            # Unexpired lease owned by another editor — leave it untouched.
            return current if current is not None else b""
        acquired["ok"] = True
        return EditorLease(run_id, current_now, expires_at).to_bytes()

    scratch.update_bytes(editor_lease_blob_path(job_id), _JSON_CONTENT_TYPE, _update)
    return acquired["ok"]


def release_lease(scratch: StorageBackend, job_id: str, run_id: str) -> None:
    """Best-effort release of *job_id*'s editor lease if still owned by *run_id*.

    Uses a CAS update (not an unconditional delete) so a successor editor that
    has already re-acquired an expired lease is never clobbered by a stale owner.
    The lease is freed by writing an empty blob, which ``EditorLease.from_bytes``
    reads back as "no lease".
    """
    path = editor_lease_blob_path(job_id)

    def _update(current: bytes | None) -> bytes:
        existing = EditorLease.from_bytes(current)
        if existing is not None and existing.run_id != run_id:
            # A successor already owns the lease — leave it untouched.
            return current if current is not None else b""
        return b""

    try:
        scratch.update_bytes(path, _JSON_CONTENT_TYPE, _update)
    except Exception:  # pragma: no cover - defensive best-effort cleanup
        logger.debug("failed to release editor lease for job_id=%s", job_id, exc_info=True)


def plan_or_load_clipset(
    scratch: StorageBackend,
    job_id: str,
    segments: Sequence[VideoSegment],
    *,
    budget: VideoStageBudget | None = None,
) -> Clipset:
    """Return the immutable ``clipset.json`` for *job_id*, creating it if absent.

    The first editor writes the plan create-if-absent (CAS); a redelivered editor
    **loads** the existing plan as the source of truth so the expected clip set
    can't drift (RFC §6.3).
    """
    path = clipset_blob_path(job_id)
    planned = Clipset.from_segments(
        job_id,
        segments,
        budget=budget.projection if budget is not None else None,
    )
    written = planned.to_json_bytes()

    def _update(current: bytes | None) -> bytes:
        return current if current else written

    scratch.update_bytes(path, _JSON_CONTENT_TYPE, _update)
    # Re-read the authoritative bytes the CAS settled on: an existing plan wins
    # over our freshly-planned one (immutability), our plan wins when absent.
    raw = scratch.get_bytes(path) or written
    try:
        clipset = Clipset.from_bytes(raw, expected_job_id=job_id)
    except ClipsetJobMismatchError:
        raise
    except (TypeError, ValueError):
        if budget is None or not budget.admit(VideoStage.PREFLIGHT).allowed:
            raise
        scratch.delete_blob(path)
        scratch.delete_prefix(clips_prefix(job_id))
        scratch.update_bytes(path, _JSON_CONTENT_TYPE, lambda _current: written)
        clipset = planned
    if clipset != planned:
        logger.warning(
            "reusing immutable existing clipset job_id=%s existing_count=%d planned_count=%d",
            job_id,
            clipset.count,
            planned.count,
        )
    return clipset


def missing_indices(
    scratch: StorageBackend,
    clipset: Clipset,
    *,
    admission_check: Callable[[], float] | None = None,
    operation_runner: Callable[[Callable[[], Any], float], Any] = run_storage_operation,
) -> list[int]:
    """Expected indices with no terminal manifest yet (fan-in incomplete)."""
    pending: list[int] = []
    for index in clipset.indices():
        path = clip_manifest_blob_path(clipset.job_id, index)
        if admission_check is None:
            exists = scratch.blob_exists(path)
        else:
            remaining = admission_check()
            if remaining <= 0:
                raise StorageOperationTimeout(
                    "fallback deadline reached during missing clip inspection"
                )
            exists = operation_runner(lambda path=path: scratch.blob_exists(path), remaining)
        if not exists:
            pending.append(index)
    return pending


def enqueue_missing_clips(
    scratch: StorageBackend,
    clipset: Clipset,
    *,
    producer: QueueProducer | None = None,
) -> list[int]:
    """Enqueue ``video-clip-jobs`` messages **additively** for pending indices.

    Only indices without a terminal manifest are (re-)enqueued; the recorder's
    idempotency (manifest sentinel + content-addressed paths) makes a duplicate
    in-flight recorder harmless (RFC §6.3).
    """
    pending = missing_indices(scratch, clipset)
    for index in pending:
        enqueue_clip_job(clipset.job_id, index, producer=producer)
    logger.info(
        "fan-out job_id=%s enqueued=%d total=%d",
        clipset.job_id,
        len(pending),
        clipset.count,
    )
    return pending


def wait_for_fanin(
    scratch: StorageBackend,
    clipset: Clipset,
    *,
    timeout_seconds: float = DEFAULT_FANIN_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_FANIN_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_poll: Callable[[set[int]], None] | None = None,
    budget: VideoStageBudget | None = None,
    operation_runner: Callable[[Callable[[], Any], float], Any] = run_storage_operation,
) -> tuple[bool, set[int]]:
    """Block until every expected index has a terminal manifest, or timeout.

    Returns ``(complete, present_indices)``. The barrier is a pure per-index
    ``blob_exists`` check on the manifest sentinel — never ``list_blobs`` (which
    caps at 10 and mixes ``.webm`` with ``.manifest.json``) (RFC §5).
    """
    expected = set(clipset.indices())
    effective_timeout = (
        min(timeout_seconds, budget.remaining_seconds(VideoStage.FANIN))
        if budget is not None
        else timeout_seconds
    )
    deadline = monotonic() + max(0.0, effective_timeout)
    present: set[int] = set()
    while True:
        present = set()
        for index in expected:
            path = clip_manifest_blob_path(clipset.job_id, index)
            if budget is None:
                exists = scratch.blob_exists(path)
            else:
                remaining = min(
                    deadline - monotonic(),
                    budget.remaining_seconds(VideoStage.FANIN),
                )
                if remaining <= 0:
                    return False, present
                try:
                    exists = operation_runner(
                        lambda path=path: scratch.blob_exists(path),
                        remaining,
                    )
                except TimeoutError:
                    logger.warning(
                        "fan-in probe timed out job_id=%s clip_index=%d",
                        clipset.job_id,
                        index,
                    )
                    return False, present
            if exists:
                present.add(index)
        logger.info(
            "fan-in barrier job_id=%s present=%d expected=%d",
            clipset.job_id,
            len(present),
            len(expected),
        )
        if on_poll is not None:
            on_poll(present)
        if present >= expected:
            return True, present
        remaining = deadline - monotonic()
        if budget is not None:
            remaining = min(remaining, budget.remaining_seconds(VideoStage.FANIN))
        if remaining <= 0:
            logger.warning(
                "fan-in barrier timed out job_id=%s present=%d expected=%d",
                clipset.job_id,
                len(present),
                len(expected),
            )
            return False, present
        sleep(min(poll_seconds, remaining))


def assemble_recording(
    scratch: StorageBackend,
    clipset: Clipset,
    output_dir: Path,
    *,
    fill_gap: FillGapFn | None = None,
    media_validator: MediaValidator | None = None,
    validation_timeout_seconds: float = 30.0,
    budget: VideoStageBudget | None = None,
    operation_runner: Callable[[Callable[[], Any], float], Any] = run_storage_operation,
) -> RecordingResult:
    """Download terminal clips into *output_dir* as a :class:`RecordingResult`.

    For each expected index, download the exact blob referenced by its terminal
    manifest and validate size, SHA-256, and media decodability before rebuilding
    the :class:`RecordedSegment`. Missing/invalid terminal media fails closed as
    ``recording_insufficient``. *fill_gap* remains only as an explicit legacy/test
    injection seam; production does not use it.
    """
    if media_validator is None:
        media_validator = _validate_downloaded_media

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    recorded: list[RecordedSegment] = []

    def _storage_call(call: Callable[[], Any]) -> Any:
        if budget is None:
            return call()
        timeout = budget.operation_timeout(VideoStage.FALLBACK)
        if timeout <= 0:
            raise TimeoutError("fallback deadline reached during recording assembly")
        return operation_runner(call, timeout)

    for entry in clipset.clips:
        index = entry.clip_index
        segment = entry.to_segment()
        try:
            manifest = _read_clip_manifest(
                scratch,
                clipset.job_id,
                index,
                storage_call=_storage_call,
            )
        except TimeoutError as exc:
            raise RecordingInsufficientError(
                clipset.job_id,
                index,
                "terminal manifest read timed out",
            ) from exc
        if budget is not None and manifest.get("schema_version") != CLIP_MANIFEST_SCHEMA_VERSION:
            raise RecordingInsufficientError(
                clipset.job_id,
                index,
                "terminal manifest has unknown or legacy schema",
            )
        status = str(manifest.get("status", ""))
        if status == STATUS_RECORDING_INSUFFICIENT:
            raise RecordingInsufficientError(
                clipset.job_id,
                index,
                str(manifest.get("failure_reason", "local fallback unavailable")),
            )
        if manifest and status not in {STATUS_SUCCESS, STATUS_FALLBACK}:
            raise RecordingInsufficientError(
                clipset.job_id,
                index,
                f"terminal manifest has invalid recorder status: {status or '<empty>'}",
            )
        media_data = manifest.get("media")
        try:
            expected_media = (
                MediaEvidence.from_dict(media_data) if isinstance(media_data, Mapping) else None
            )
        except (TypeError, ValueError) as exc:
            raise RecordingInsufficientError(
                clipset.job_id,
                index,
                "terminal manifest has invalid media evidence",
            ) from exc
        raw_media_path = manifest.get("media_blob_path")
        manifest_path = clip_manifest_blob_path(clipset.job_id, index)
        manifest_exists = _storage_call(lambda: scratch.blob_exists(manifest_path))
        if manifest_exists and not manifest:
            raise RecordingInsufficientError(
                clipset.job_id,
                index,
                "terminal manifest is empty or malformed",
            )
        if manifest:
            if expected_media is None:
                raise RecordingInsufficientError(
                    clipset.job_id,
                    index,
                    "terminal manifest is missing media evidence",
                )
            expected_clip_id = f"clip-{index:03d}"
            if manifest.get("clip_id") != expected_clip_id:
                raise RecordingInsufficientError(
                    clipset.job_id,
                    index,
                    "terminal manifest clip_id does not match clip index",
                )
            expected_media_path = clip_content_blob_path(
                clipset.job_id,
                index,
                expected_media.sha256,
            )
            if raw_media_path != expected_media_path:
                raise RecordingInsufficientError(
                    clipset.job_id,
                    index,
                    "terminal manifest media path does not match clip identity",
                )
            clip_path = expected_media_path
        else:
            clip_path = clip_blob_path(clipset.job_id, index)
        dest = output_dir / f"clip_{index:03d}.webm"

        # Only treat a clip as terminal when its manifest sentinel is present
        # (RFC §5). On the normal path the fan-in barrier already waited for it;
        # on a timeout a half-written ``.webm`` may exist without a manifest, in
        # which case we fill the gap rather than compose an unverified clip.
        if (
            manifest_exists
            and _storage_call(lambda: scratch.blob_exists(clip_path))
            and _storage_call(lambda: scratch.download_file(clip_path, dest))
        ):
            try:
                media_validator(dest, expected_media, validation_timeout_seconds)
            except (MediaValidationError, OSError, ValueError) as exc:
                dest.unlink(missing_ok=True)
                reason = getattr(exc, "reason", type(exc).__name__)
                reason_value = getattr(reason, "value", str(reason))
                raise RecordingInsufficientError(
                    clipset.job_id,
                    index,
                    f"terminal media validation failed: {reason_value}",
                ) from exc
            recorded.append(
                _recorded_from_manifest(
                    segment,
                    dest,
                    manifest,
                    media_evidence=expected_media,
                )
            )
        else:
            if fill_gap is not None:
                recorded.append(fill_gap(segment, output_dir, index))
                continue
            raise RecordingInsufficientError(
                clipset.job_id,
                index,
                "terminal manifest does not reference available media",
            )
    return RecordingResult(recorded=recorded, output_dir=output_dir)


def terminalize_missing_clips(
    scratch: StorageBackend,
    clipset: Clipset,
    *,
    budget: VideoStageBudget,
    renderer=None,
    operation_runner: Callable[[Callable[[], Any], float], Any] = run_storage_operation,
) -> None:
    """After fan-in cutoff, resolve every missing index without browser/network."""
    from podcaster.video.recorder import write_fallback_manifest

    if budget.remaining_seconds(VideoStage.FALLBACK) <= 0:
        return
    pending = missing_indices(
        scratch,
        clipset,
        admission_check=lambda: budget.remaining_seconds(VideoStage.FALLBACK),
        operation_runner=operation_runner,
    )
    for index in pending:
        remaining = budget.remaining_seconds(VideoStage.FALLBACK)
        if remaining <= 0:
            break
        write_fallback_manifest(
            clipset.job_id,
            index,
            scratch=scratch,
            reason="fanin_deadline_reached",
            timeout_seconds=min(30.0, remaining),
            renderer=renderer,
            admission_check=lambda: budget.remaining_seconds(VideoStage.FALLBACK),
            operation_runner=operation_runner,
        )


def _validate_downloaded_media(
    path: Path,
    expected: MediaEvidence | None,
    timeout_seconds: float,
) -> MediaEvidence:
    return collect_media_evidence(
        path,
        expected=expected,
        timeout_seconds=timeout_seconds,
    )


def cleanup_clips(
    scratch: StorageBackend,
    job_id: str,
    *,
    budget: VideoStageBudget | None = None,
    timeout_seconds: float | None = 30.0,
    operation_runner: Callable[[Callable[[], Any], float], Any] = run_storage_operation,
) -> int:
    """Delete the per-job ``clips/**`` scratch after a successful compose (RFC §5)."""
    timeout = (
        None if budget is None else budget.operation_timeout(VideoStage.SHUTDOWN, timeout_seconds)
    )
    if timeout is not None and timeout <= 0:
        logger.warning("clip cleanup skipped with no shutdown budget job_id=%s", job_id)
        return 0

    def operation() -> int:
        return scratch.delete_prefix(clips_prefix(job_id))

    try:
        return operation() if timeout is None else operation_runner(operation, timeout)
    except Exception:  # pragma: no cover - best-effort; lifecycle rule is the backstop
        logger.warning(
            "clip cleanup failed job_id=%s; lifecycle policy will reclaim",
            job_id,
            exc_info=True,
        )
        return 0


def record_via_fanout(
    job_id: str,
    segments: Sequence[VideoSegment],
    output_dir: Path,
    *,
    scratch: StorageBackend,
    producer: QueueProducer | None = None,
    timeout_seconds: float = DEFAULT_FANIN_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_FANIN_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    fill_gap: FillGapFn | None = None,
    heartbeat: Callable[[], None] | None = None,
    budget: VideoStageBudget | None = None,
    fallback_renderer=None,
    media_validator: MediaValidator | None = None,
    operation_runner: Callable[[Callable[[], Any], float], Any] = run_storage_operation,
) -> RecordingResult:
    """Plan → fan out → fan in → assemble, returning a ``RecordingResult``.

    The compose/distribute caller is unchanged: it receives the same
    ``RecordingResult`` shape ``record_episode`` returns. *heartbeat* (when given)
    is invoked on every barrier poll so the caller can renew the editor lease.
    """
    clipset = plan_or_load_clipset(scratch, job_id, segments, budget=budget)
    if budget is None or budget.admit(VideoStage.FANIN).allowed:
        enqueue_missing_clips(scratch, clipset, producer=producer)
    else:
        logger.warning("fan-out cutoff reached; no clip jobs enqueued job_id=%s", job_id)

    def _on_poll(_present: set[int]) -> None:
        if heartbeat is not None:
            heartbeat()

    complete, present = wait_for_fanin(
        scratch,
        clipset,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        monotonic=monotonic,
        on_poll=_on_poll,
        budget=budget,
        operation_runner=operation_runner,
    )
    if not complete:
        logger.warning(
            "composing job_id=%s with partial fan-in present=%d expected=%d "
            "(missing indices substitute fallback cards)",
            job_id,
            len(present),
            clipset.count,
        )
        if budget is not None:
            terminalize_missing_clips(
                scratch,
                clipset,
                budget=budget,
                renderer=fallback_renderer,
                operation_runner=operation_runner,
            )
    validation_timeout = (
        min(30.0, budget.remaining_seconds(VideoStage.FALLBACK)) if budget is not None else 30.0
    )
    return assemble_recording(
        scratch,
        clipset,
        output_dir,
        fill_gap=fill_gap,
        media_validator=media_validator,
        validation_timeout_seconds=validation_timeout,
        budget=budget,
        operation_runner=operation_runner,
    )


def _read_clip_manifest(
    scratch: StorageBackend,
    job_id: str,
    clip_index: int,
    *,
    storage_call: Callable[[Callable[[], Any]], Any] | None = None,
) -> Mapping[str, Any]:
    def read() -> bytes | None:
        return scratch.get_bytes(clip_manifest_blob_path(job_id, clip_index))

    raw = read() if storage_call is None else storage_call(read)
    if not raw:
        return {}
    import json

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _recorded_from_manifest(
    segment: VideoSegment,
    video_path: Path,
    manifest: Mapping[str, Any],
    *,
    media_evidence: MediaEvidence | None = None,
) -> RecordedSegment:
    """Rebuild a :class:`RecordedSegment` from a downloaded clip + its manifest.

    The recorder persists the recording-outcome flags (``has_pages``,
    ``website_url``, ``is_removed``, ``recovery_path``) as manifest extras so the
    editor reproduces **identical** compose output; missing keys fall back to the
    ``RecordedSegment`` defaults.
    """
    return RecordedSegment(
        segment=segment,
        video_path=video_path,
        is_fallback=bool(manifest.get("is_fallback", False)),
        has_pages=bool(manifest.get("has_pages", False)),
        website_url=_opt_str(manifest.get("website_url")),
        is_removed=bool(manifest.get("is_removed", False)),
        recovery_path=str(manifest.get("recovery_path", "direct")),
        media_evidence=media_evidence,
    )


def _production_fill_gap(
    segment: VideoSegment, output_dir: Path, clip_index: int
) -> RecordedSegment:
    """Render a browser-free fixed local fallback for compatibility callers."""
    from podcaster.video.recorder import _render_static_fallback

    path = Path(output_dir) / f"gap_{clip_index:03d}.webm"
    _render_static_fallback(path, 30.0)
    return RecordedSegment(
        segment=segment,
        video_path=path,
        is_fallback=True,
        recovery_path="fallback",
    )


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
