"""Checkpoint/resume store for video pipeline intermediates (issue #410).

The video pipeline used to write every intermediate (segment recordings,
normalized clips, the composed video) to local ``/tmp`` on the ACA replica. On
the size-limited ephemeral disk that caused disk-exhaustion failures, and an
interrupted job lost all progress.

:class:`IntermediateStore` moves those intermediates into a dedicated Azure Blob
*scratch* container under ``video-jobs/{job-id}/intermediates/``. Each pipeline
stage can:

* ``exists(name)`` — check whether its output is already checkpointed in blob,
* ``download(name, dest)`` — pull a previously-checkpointed intermediate back to
  local disk (resume), and
* ``upload(name, source)`` — checkpoint a freshly-produced intermediate.

This keeps local disk holding only the file currently being processed, and lets
a restarted job skip stages whose output already survived in blob.

The store is intentionally *optional*: when constructed with a ``None`` backend
(local development, unit tests) every operation is a graceful no-op, so callers
need no branching and the legacy all-local-disk behaviour is preserved.

A small JSON manifest (``manifest.json``) tracks per-intermediate completion so
operators (and the resume logic) can see what survived a crash. Identity-only
data plane — no keys, tokens, or secrets are logged.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from podcaster.video.budget import VideoStage, VideoStageBudget
from podcaster.video.process import (
    MediaValidationRecord,
    OwnedCallableTimeout,
    ProbeEvidence,
    collect_media_evidence,
    run_owned_callable,
    validate_media_record,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from podcaster.storage import StorageBackend

logger = logging.getLogger("podcaster.video.intermediates")

# Top-level prefix for all video scratch artifacts (issue #410).
SCRATCH_ROOT = "video-jobs"
INTERMEDIATES_DIR = "intermediates"
MANIFEST_NAME = "manifest.json"
VALIDATION_SUFFIX = ".validation.json"

_OCTET_STREAM = "application/octet-stream"

# Safety margin kept free on local disk on top of an operation's estimated
# input+output footprint (issue #410 disk-budget guard).
DISK_MARGIN_BYTES = 500 * 1024 * 1024


class InsufficientDiskError(RuntimeError):
    """Raised when local disk cannot fit a pipeline stage's input+output.

    Surfaced as a *resumable* failure: the job aborts cleanly and a later run
    resumes from the blob checkpoints already written, so no work is lost.
    """


class StorageOperationTimeout(TimeoutError):
    """Raised when a storage operation exhausts its shared stage budget."""


def _run_bounded(call: Callable[[], Any], timeout_seconds: float) -> Any:
    """Run a potentially blocking operation in an owned cancellable process."""

    if timeout_seconds <= 0:
        raise StorageOperationTimeout("storage operation has no remaining budget")
    try:
        return run_owned_callable(
            call,
            timeout_seconds,
            process_name="video-storage-operation",
        )
    except OwnedCallableTimeout as exc:
        raise StorageOperationTimeout(
            f"storage operation exceeded {timeout_seconds:.3f}s deadline"
        ) from exc


run_storage_operation = _run_bounded


def ensure_disk_budget(
    work_dir: "Path | str",
    required_bytes: int,
    *,
    margin: int = DISK_MARGIN_BYTES,
) -> None:
    """Fail fast (resumable) when ``work_dir`` cannot fit ``required_bytes``.

    ``required_bytes`` is the caller's estimate of the operation's peak local
    footprint (sum of input sizes + estimated output).  A fixed ``margin`` is
    held back on top.  When the free space cannot be determined the check is
    skipped (best-effort).
    """
    import shutil

    try:
        usage = shutil.disk_usage(str(work_dir))
    except OSError:
        return
    needed = int(required_bytes) + int(margin)
    if usage.free < needed:
        raise InsufficientDiskError(
            "insufficient local disk for video stage: "
            f"need ~{needed} bytes (required={int(required_bytes)} + margin={int(margin)}), "
            f"free={usage.free} at {work_dir}"
        )


class IntermediateStore:
    """Blob-backed checkpoint store for one video job's intermediate files.

    When ``backend`` is ``None`` the store is *disabled*: :meth:`enabled` is
    ``False`` and every mutating/reading operation is a no-op (``exists`` returns
    ``False``, ``download`` returns ``False``). This lets the pipeline run
    unchanged in local development and tests where no scratch container exists.
    """

    def __init__(
        self,
        backend: "StorageBackend | None",
        job_id: str,
        *,
        operation_runner: Callable[[Callable[[], Any], float], Any] = _run_bounded,
    ) -> None:
        self._backend = backend
        self._job_id = job_id
        self._operation_runner = operation_runner

    @property
    def enabled(self) -> bool:
        return self._backend is not None

    @property
    def job_id(self) -> str:
        return self._job_id

    def blob_path(self, name: str) -> str:
        """Blob path for an intermediate named ``name`` within this job."""
        safe = name.strip().strip("/")
        if not safe:
            raise ValueError("intermediate name must not be empty")
        return f"{SCRATCH_ROOT}/{self._job_id}/{INTERMEDIATES_DIR}/{safe}"

    def prefix(self) -> str:
        """Blob prefix covering every intermediate for this job."""
        return f"{SCRATCH_ROOT}/{self._job_id}/{INTERMEDIATES_DIR}/"

    @staticmethod
    def validation_name(name: str) -> str:
        return f"{name}{VALIDATION_SUFFIX}"

    def _call(
        self,
        operation: Callable[[], Any],
        *,
        budget: VideoStageBudget | None,
        stage: VideoStage,
        timeout_seconds: float | None,
    ) -> Any:
        if budget is None:
            return operation()
        timeout = budget.operation_timeout(stage, timeout_seconds)
        return self._operation_runner(operation, timeout)

    def exists(
        self,
        name: str,
        *,
        budget: VideoStageBudget | None = None,
        stage: VideoStage = VideoStage.RENDER,
        timeout_seconds: float | None = 30.0,
    ) -> bool:
        """Return True when intermediate ``name`` is already checkpointed."""
        if self._backend is None:
            return False
        try:
            return bool(
                self._call(
                    lambda: self._backend.blob_exists(self.blob_path(name)),
                    budget=budget,
                    stage=stage,
                    timeout_seconds=timeout_seconds,
                )
            )
        except Exception:
            # Checkpoint lookups must never break the pipeline; a failed probe
            # simply means we re-do the stage (correct, just slower).
            logger.warning(
                "intermediate existence check failed job_id=%s name=%s; treating as absent",
                self._job_id,
                name,
                exc_info=True,
            )
            return False

    def download(
        self,
        name: str,
        dest: Path,
        *,
        budget: VideoStageBudget | None = None,
        stage: VideoStage = VideoStage.RENDER,
        timeout_seconds: float | None = 30.0,
    ) -> bool:
        """Download checkpointed intermediate ``name`` to ``dest``.

        Returns True when the blob existed and was written to ``dest``; False
        when the store is disabled or the blob is absent. Download failures are
        swallowed (return False) so a corrupt/partial checkpoint just triggers a
        clean recompute rather than aborting the job.
        """
        if self._backend is None:
            return False
        try:
            ok = self._call(
                lambda: self._backend.download_file(self.blob_path(name), Path(dest)),
                budget=budget,
                stage=stage,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            logger.warning(
                "intermediate download failed job_id=%s name=%s; will recompute",
                self._job_id,
                name,
                exc_info=True,
            )
            return False
        if ok:
            logger.info("resumed intermediate from blob job_id=%s name=%s", self._job_id, name)
        return ok

    def upload(
        self,
        name: str,
        source: Path,
        content_type: str = _OCTET_STREAM,
        *,
        budget: VideoStageBudget | None = None,
        stage: VideoStage = VideoStage.RENDER,
        timeout_seconds: float | None = 30.0,
    ) -> bool:
        """Checkpoint local file ``source`` as intermediate ``name``.

        After the streamed upload the blob's size is verified against the local
        file size (issue #410 upload safety): the checkpoint is only trusted —
        and the caller only deletes the local copy — when the two match, so a
        truncated upload never masquerades as a complete checkpoint on resume.

        Returns True on a successful, verified upload, False when the store is
        disabled.  Upload failures are logged and swallowed: a missing checkpoint
        only costs a recompute on resume and must never fail a healthy job.
        """
        if self._backend is None:
            return False
        source = Path(source)
        if not source.exists():
            logger.warning(
                "intermediate upload skipped job_id=%s name=%s: source missing %s",
                self._job_id,
                name,
                source,
            )
            return False
        expected = source.stat().st_size
        try:
            self._call(
                lambda: self._backend.upload_file(self.blob_path(name), source, content_type),
                budget=budget,
                stage=stage,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            logger.warning(
                "intermediate upload failed job_id=%s name=%s; continuing without checkpoint",
                self._job_id,
                name,
                exc_info=True,
            )
            return False
        if not self._verify_size(
            name,
            expected,
            budget=budget,
            stage=stage,
            timeout_seconds=timeout_seconds,
        ):
            logger.warning(
                "intermediate upload size mismatch job_id=%s name=%s expected=%d; "
                "discarding unverified checkpoint",
                self._job_id,
                name,
                expected,
            )
            # Best-effort: drop the unverified blob so resume won't reuse it.
            deleter = getattr(self._backend, "delete_blob", None)
            if deleter is not None:
                try:
                    deleter(self.blob_path(name))
                except Exception:
                    logger.debug("could not delete unverified blob %s", name, exc_info=True)
            return False
        logger.info("checkpointed intermediate to blob job_id=%s name=%s", self._job_id, name)
        return True

    def _verify_size(
        self,
        name: str,
        expected: int,
        *,
        budget: VideoStageBudget | None = None,
        stage: VideoStage = VideoStage.RENDER,
        timeout_seconds: float | None = 30.0,
    ) -> bool:
        """Verify the uploaded blob's size equals ``expected`` local bytes.

        Returns True when sizes match.  When the backend cannot report a size
        (older backend) or the probe itself errors, the check passes (best
        effort) — the upload itself already succeeded.
        """
        getter = getattr(self._backend, "blob_size", None)
        if getter is None:
            return True
        try:
            actual = self._call(
                lambda: getter(self.blob_path(name)),
                budget=budget,
                stage=stage,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            logger.debug(
                "blob size probe failed job_id=%s name=%s",
                self._job_id,
                name,
                exc_info=True,
            )
            return True
        if actual is None:
            return False
        return int(actual) == int(expected)

    def read_text(
        self,
        name: str,
        *,
        budget: VideoStageBudget | None = None,
        stage: VideoStage = VideoStage.RENDER,
        timeout_seconds: float | None = 30.0,
    ) -> str | None:
        """Return the UTF-8 text of intermediate ``name`` (sidecar metadata)."""
        if self._backend is None:
            return None
        try:
            raw = self._call(
                lambda: self._backend.get_bytes(self.blob_path(name)),
                budget=budget,
                stage=stage,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            logger.warning(
                "intermediate read failed job_id=%s name=%s",
                self._job_id,
                name,
                exc_info=True,
            )
            return None
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def write_text(
        self,
        name: str,
        text: str,
        content_type: str = "application/json; charset=utf-8",
        *,
        budget: VideoStageBudget | None = None,
        stage: VideoStage = VideoStage.RENDER,
        timeout_seconds: float | None = 30.0,
    ) -> bool:
        """Checkpoint small text/JSON intermediate ``name`` (sidecar metadata)."""
        if self._backend is None:
            return False
        try:
            self._call(
                lambda: self._backend.put_bytes(
                    self.blob_path(name), text.encode("utf-8"), content_type
                ),
                budget=budget,
                stage=stage,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            logger.warning(
                "intermediate text write failed job_id=%s name=%s",
                self._job_id,
                name,
                exc_info=True,
            )
            return False
        return True

    def upload_validated(
        self,
        name: str,
        source: Path,
        *,
        artifact_kind: str,
        identity: Mapping[str, str],
        content_type: str = _OCTET_STREAM,
        budget: VideoStageBudget | None = None,
        stage: VideoStage = VideoStage.RENDER,
        timeout_seconds: float = 30.0,
        probe: Callable[[Path, float], ProbeEvidence] | None = None,
    ) -> MediaValidationRecord | None:
        """Upload media and publish its validation sidecar only after verification."""

        if self._backend is None:
            return None
        kwargs: dict[str, Any] = {
            "timeout_seconds": timeout_seconds,
            "budget": budget,
            "stage": stage,
        }
        if probe is not None:
            kwargs["probe"] = probe
        evidence = collect_media_evidence(source, **kwargs)
        if not self.upload(
            name,
            source,
            content_type,
            budget=budget,
            stage=stage,
            timeout_seconds=timeout_seconds,
        ):
            return None
        record = MediaValidationRecord(
            artifact_kind=artifact_kind,
            identity=identity,
            media=evidence,
        )
        if not self.write_text(
            self.validation_name(name),
            json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":")),
            budget=budget,
            stage=stage,
            timeout_seconds=timeout_seconds,
        ):
            self._delete_unverified(name)
            return None
        return record

    def download_validated(
        self,
        name: str,
        dest: Path,
        *,
        artifact_kind: str,
        identity: Mapping[str, str],
        budget: VideoStageBudget | None = None,
        stage: VideoStage = VideoStage.RENDER,
        timeout_seconds: float = 30.0,
        probe: Callable[[Path, float], ProbeEvidence] | None = None,
    ) -> MediaValidationRecord | None:
        """Atomically download and validate a versioned reusable media artifact."""

        if self._backend is None:
            return None
        sidecar = self.read_text(
            self.validation_name(name),
            budget=budget,
            stage=stage,
            timeout_seconds=timeout_seconds,
        )
        if not sidecar:
            return None
        try:
            raw = json.loads(sidecar)
            if not isinstance(raw, dict):
                return None
            record = MediaValidationRecord.from_dict(raw)
            record.require_identity(artifact_kind, identity)
        except (TypeError, ValueError):
            return None

        dest = Path(dest)
        temporary = dest.with_name(f"{dest.name}.validation-{uuid.uuid4().hex}.part")
        try:
            if not self.download(
                name,
                temporary,
                budget=budget,
                stage=stage,
                timeout_seconds=timeout_seconds,
            ):
                return None
            kwargs: dict[str, Any] = {
                "artifact_kind": artifact_kind,
                "identity": identity,
                "timeout_seconds": timeout_seconds,
                "budget": budget,
                "stage": stage,
            }
            if probe is not None:
                kwargs["probe"] = probe
            validate_media_record(temporary, record, **kwargs)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, dest)
            return record
        except Exception:
            dest.unlink(missing_ok=True)
            return None
        finally:
            temporary.unlink(missing_ok=True)

    def _delete_unverified(self, name: str) -> None:
        if self._backend is None:
            return
        deleter = getattr(self._backend, "delete_blob", None)
        if deleter is None:
            return
        for candidate in (name, self.validation_name(name)):
            try:
                deleter(self.blob_path(candidate))
            except Exception:
                logger.debug("could not delete unverified blob %s", candidate, exc_info=True)

    def cleanup(
        self,
        *,
        budget: VideoStageBudget | None = None,
        timeout_seconds: float | None = 30.0,
        operation_runner: Callable[[Callable[[], Any], float], Any] | None = None,
    ) -> int:
        """Delete every intermediate for this job after a successful publish.

        Returns the number of blobs deleted (0 when disabled). Best-effort: the
        7-day lifecycle policy on the scratch container is the safety net, so a
        failed cleanup is logged and swallowed.
        """
        if self._backend is None:
            return 0
        runner = operation_runner or self._operation_runner
        timeout = (
            None
            if budget is None
            else budget.operation_timeout(VideoStage.SHUTDOWN, timeout_seconds)
        )
        if timeout is not None and timeout <= 0:
            logger.warning(
                "intermediate cleanup skipped with no shutdown budget job_id=%s",
                self._job_id,
            )
            return 0

        def operation() -> int:
            return self._backend.delete_prefix(self.prefix())

        try:
            deleted = operation() if timeout is None else runner(operation, timeout)
        except Exception:
            logger.warning(
                "intermediate cleanup failed job_id=%s; lifecycle policy will reclaim",
                self._job_id,
                exc_info=True,
            )
            return 0
        logger.info("cleaned up %d intermediate blob(s) job_id=%s", deleted, self._job_id)
        return deleted

    # -- manifest helpers ---------------------------------------------------

    def load_manifest(self) -> dict:
        """Load the per-job intermediates manifest (empty dict when absent)."""
        text = self.read_text(MANIFEST_NAME)
        if not text:
            return {}
        try:
            doc = json.loads(text)
        except ValueError:
            return {}
        return doc if isinstance(doc, dict) else {}

    def mark(self, stage: str, status: str = "complete", **extra) -> None:
        """Record per-stage completion in the intermediates manifest.

        Tracks which stages have a checkpoint so operators can see resume state
        (acceptance criterion: "Manifest tracks per-segment completion status").
        """
        if self._backend is None:
            return
        doc = self.load_manifest()
        stages = doc.setdefault("stages", {})
        if not isinstance(stages, dict):
            stages = {}
            doc["stages"] = stages
        entry = {"status": status}
        entry.update(extra)
        stages[stage] = entry
        doc["job_id"] = self._job_id
        self.write_text(MANIFEST_NAME, json.dumps(doc, indent=2, sort_keys=True))


def create_intermediate_store(job_id: str) -> IntermediateStore:
    """Build an :class:`IntermediateStore` from the environment (issue #410).

    Uses :func:`podcaster.storage.create_scratch_storage_backend`, which returns
    ``None`` (disabling the store) when no scratch container is configured.
    """
    from podcaster.storage import create_scratch_storage_backend

    return IntermediateStore(create_scratch_storage_backend(), job_id)
