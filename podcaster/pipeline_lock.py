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
