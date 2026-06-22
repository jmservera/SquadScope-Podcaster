"""Pipeline lock to prevent concurrent audio and video processing of the same job (#268).

Both job runners (audio synthesis and video generation) update a shared manifest.
While the storage backend provides atomic updates (ETag-based optimistic concurrency),
we need to prevent logical conflicts where both pipelines run simultaneously on the
same job_id. This module provides a claim-based lock: a runner claims the pipeline
before starting work, and the other runner skips if a different pipeline already owns it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from podcaster.storage import StorageBackend

logger = logging.getLogger(__name__)

PIPELINE_AUDIO = "audio"
PIPELINE_VIDEO = "video"


def _manifest_path(job_id: str) -> str:
    return f"jobs/{job_id}/manifest.json"


def _synthesis_completed(generation: dict[str, Any]) -> bool:
    """Return True if audio synthesis has finished for this job.

    Video generation must run *after* audio synthesis, so a completed
    ``synthesis_runner`` is the signal that the audio pipeline is done and the
    video pipeline may take over the lock.
    """
    runner = generation.get("synthesis_runner")
    if not isinstance(runner, dict):
        return False
    return runner.get("status") == "completed"


def claim_pipeline(
    storage: StorageBackend,
    job_id: str,
    pipeline: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Attempt to claim the pipeline lock for a job.

    Returns True if the claim succeeded (or this pipeline already owns it).
    Returns False if a different pipeline already owns the lock.

    The lock is stored in ``manifest.generation.pipeline_lock``.
    """
    current = now or datetime.now(timezone.utc)

    def _apply(content: bytes | None) -> bytes:
        doc: dict[str, Any] = {}
        if content:
            try:
                doc = json.loads(content.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                doc = {}
        if not isinstance(doc, dict):
            doc = {}

        generation = doc.get("generation")
        if not isinstance(generation, dict):
            generation = {}
            doc["generation"] = generation
        lock = generation.get("pipeline_lock")

        if isinstance(lock, dict):
            owner = lock.get("pipeline")
            if owner and owner != pipeline:
                # Sequential handoff: video may take over an audio-held lock once
                # audio synthesis has completed. Without this, the lock claimed by
                # the audio runner is never released and video generation is
                # permanently skipped with "pipeline_locked_by_audio".
                if (
                    pipeline == PIPELINE_VIDEO
                    and owner == PIPELINE_AUDIO
                    and _synthesis_completed(generation)
                ):
                    pass  # fall through to claim ownership for video
                else:
                    # Another pipeline owns it — don't modify, raise to signal failure
                    raise _LockConflict(owner)

        # Claim or re-confirm ownership
        generation["pipeline_lock"] = {
            "pipeline": pipeline,
            "claimed_at": current.isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        return json.dumps(doc, separators=(",", ":")).encode("utf-8")

    try:
        storage.update_bytes(_manifest_path(job_id), "application/json; charset=utf-8", _apply)
        logger.info("pipeline lock claimed: job_id=%s pipeline=%s", job_id, pipeline)
        return True
    except _LockConflict as exc:
        logger.info(
            "pipeline lock conflict: job_id=%s requested=%s owner=%s",
            job_id, pipeline, exc.owner,
        )
        return False
    except Exception:
        # Storage errors are fail-closed — refusing to proceed without a confirmed
        # lock prevents both pipelines from running simultaneously on the same job.
        logger.warning(
            "pipeline lock check failed for job_id=%s, refusing to proceed", job_id, exc_info=True
        )
        return False


class _LockConflict(Exception):
    """Raised inside update_bytes callback to abort without writing."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        super().__init__(f"pipeline locked by {owner}")


def release_pipeline(
    storage: StorageBackend,
    job_id: str,
    pipeline: str,
) -> bool:
    """Release the pipeline lock for a job if it is owned by ``pipeline``.

    Returns True if the lock was released (or was not held / held by another
    pipeline, in which case nothing is changed). Returns False only on storage
    errors. Releasing a lock owned by a different pipeline is a no-op so a runner
    cannot clobber another pipeline's claim.
    """

    def _apply(content: bytes | None) -> bytes:
        doc: dict[str, Any] = {}
        if content:
            try:
                doc = json.loads(content.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                doc = {}
        if not isinstance(doc, dict):
            doc = {}

        generation = doc.get("generation")
        if isinstance(generation, dict):
            lock = generation.get("pipeline_lock")
            if isinstance(lock, dict) and lock.get("pipeline") == pipeline:
                generation.pop("pipeline_lock", None)
        return json.dumps(doc, separators=(",", ":")).encode("utf-8")

    try:
        storage.update_bytes(_manifest_path(job_id), "application/json; charset=utf-8", _apply)
        logger.info("pipeline lock released: job_id=%s pipeline=%s", job_id, pipeline)
        return True
    except Exception:
        logger.warning(
            "pipeline lock release failed for job_id=%s pipeline=%s", job_id, pipeline, exc_info=True
        )
        return False
