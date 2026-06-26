"""Durable, real-time pipeline progress reporting (issue #469).

This module is the transport foundation for the Phase 5 Observability epic
(SquadScope-Coordinator#30). The pipeline emits granular progress *events* —
stage, phase, segment ``N/M``, optional percent — to a durable per-job store so
the monitoring UI can show live progress instead of deriving status from
manifest snapshots.

Why a durable blob store (and not in-memory state)?
    Azure Container Apps executes the pipeline in serverless, stateless workers
    that can scale to zero or restart between executions. Any progress channel
    must therefore survive a worker restart and be readable by a *different*
    process (the monitoring API) than the one that produced it. Events are
    appended to ``jobs/{job_id}/progress.json`` via the storage backend's
    atomic ``update_bytes`` (the same compare-and-write primitive used for
    manifests), which is safe under concurrent writers.

Event schema (``schema_version = "squadscope-podcaster-progress-v1"``)
    The progress document is a JSON object::

        {
          "schema_version": "squadscope-podcaster-progress-v1",
          "job_id": "<id>",
          "updated_at": "2026-06-26T12:00:00Z",
          "current": { <latest event, without "seq"> },
          "events": [ <ProgressEvent>, ... ]   # ordered, monotonic "seq"
        }

    Each ``ProgressEvent`` is::

        {
          "seq": 1,                  # monotonic, 1-based; use for "since" polling
          "at": "2026-06-26T12:00:00Z",
          "stage": "synthesis",      # one of PipelineStage values
          "phase": "recording",      # optional free-form sub-phase
          "segment_index": 12,       # optional 1-based segment counter
          "segment_total": 18,       # optional total segments
          "percent": 66.7,           # optional 0..100 (auto-derived from N/M)
          "message": "recording 12/18"  # optional human-readable detail
        }

    The schema is intentionally stable and self-describing so the stage-progress
    API (#470), the log viewer (#472), and the UI stage-visualization component
    (#474) can all consume the same events.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from podcaster.storage import StorageBackend

logger = logging.getLogger(__name__)

PROGRESS_SCHEMA_VERSION = "squadscope-podcaster-progress-v1"

# Keep the appended-event log bounded so a long or pathological run can't grow
# the durable document without limit. The newest events are always retained.
MAX_EVENTS = 500


class PipelineStage:
    """Canonical pipeline stage identifiers used in progress events.

    Shared vocabulary for every #30 consumer. ``record``/``compose``/``mux``/
    ``publish`` mirror the stages called out in the stage-progress API (#470).
    """

    QUEUED = "queued"
    BRIEF = "brief"
    SCRIPT = "script"
    SYNTHESIS = "synthesis"  # TTS recording of segments
    COMPOSE = "compose"  # video composition
    MUX = "mux"  # audio/video mux + encode
    PUBLISH = "publish"
    COMPLETED = "completed"
    FAILED = "failed"

    ALL = (
        QUEUED,
        BRIEF,
        SCRIPT,
        SYNTHESIS,
        COMPOSE,
        MUX,
        PUBLISH,
        COMPLETED,
        FAILED,
    )

    #: Stages after which no further progress events are expected.
    TERMINAL = (COMPLETED, FAILED)


@dataclass(frozen=True)
class ProgressEvent:
    """A single progress event. See the module docstring for the schema."""

    seq: int
    at: str
    stage: str
    phase: str | None = None
    segment_index: int | None = None
    segment_total: int | None = None
    percent: float | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def progress_path(job_id: str) -> str:
    """Durable blob path for a job's progress document."""
    return f"jobs/{job_id}/progress.json"


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _derive_percent(
    percent: float | None,
    segment_index: int | None,
    segment_total: int | None,
) -> float | None:
    if percent is not None:
        return max(0.0, min(100.0, round(float(percent), 1)))
    if segment_index is not None and segment_total and segment_total > 0:
        return round(100.0 * min(segment_index, segment_total) / segment_total, 1)
    return None


def _empty_document(job_id: str) -> dict[str, Any]:
    return {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "job_id": job_id,
        "updated_at": None,
        "current": None,
        "events": [],
    }


def _load_document(raw: bytes | None, job_id: str) -> dict[str, Any]:
    if not raw:
        return _empty_document(job_id)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _empty_document(job_id)
    if not isinstance(doc, dict):
        return _empty_document(job_id)
    doc.setdefault("schema_version", PROGRESS_SCHEMA_VERSION)
    doc.setdefault("job_id", job_id)
    if not isinstance(doc.get("events"), list):
        doc["events"] = []
    return doc


def emit_progress(
    storage: StorageBackend,
    job_id: str,
    *,
    stage: str,
    phase: str | None = None,
    segment_index: int | None = None,
    segment_total: int | None = None,
    percent: float | None = None,
    message: str | None = None,
    at: datetime | None = None,
) -> ProgressEvent | None:
    """Append a progress event to the job's durable store.

    This is best-effort: progress reporting must *never* break the pipeline, so
    any storage failure is logged and swallowed (returning ``None``). The append
    is atomic via ``storage.update_bytes``, making it safe for concurrent
    workers and resilient to worker restarts.
    """

    moment = _iso(at or datetime.now(timezone.utc))
    resolved_percent = _derive_percent(percent, segment_index, segment_total)
    captured: dict[str, Any] = {}

    def _apply(content: bytes | None) -> bytes:
        document = _load_document(content, job_id)
        events = document["events"]
        next_seq = (events[-1]["seq"] + 1) if events else 1
        event = ProgressEvent(
            seq=next_seq,
            at=moment,
            stage=stage,
            phase=phase,
            segment_index=segment_index,
            segment_total=segment_total,
            percent=resolved_percent,
            message=message,
        ).to_dict()
        events.append(event)
        if len(events) > MAX_EVENTS:
            del events[: len(events) - MAX_EVENTS]
        document["events"] = events
        document["current"] = {k: v for k, v in event.items() if k != "seq"}
        document["updated_at"] = moment
        captured.update(event)
        return json.dumps(document, ensure_ascii=False).encode("utf-8")

    try:
        storage.update_bytes(progress_path(job_id), "application/json; charset=utf-8", _apply)
    except Exception:  # noqa: BLE001 - progress emission must never mask the real work
        logger.warning("could not emit progress job_id=%s stage=%s", job_id, stage)
        return None

    if not captured:
        return None
    return ProgressEvent(**{k: captured.get(k) for k in ProgressEvent.__dataclass_fields__})


def read_progress(storage: StorageBackend, job_id: str) -> dict[str, Any] | None:
    """Return the full durable progress document, or ``None`` if absent."""
    raw = storage.get_bytes(progress_path(job_id))
    if not raw:
        return None
    return _load_document(raw, job_id)


def events_since(storage: StorageBackend, job_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    """Return progress events with ``seq`` strictly greater than ``after_seq``.

    Drives incremental polling and SSE catch-up: a consumer remembers the last
    ``seq`` it saw and asks only for newer events.
    """
    document = read_progress(storage, job_id)
    if not document:
        return []
    events = document.get("events")
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, dict) and int(e.get("seq", 0)) > after_seq]


def is_terminal(document: dict[str, Any] | None) -> bool:
    """True when the latest event marks a terminal stage (completed/failed)."""
    if not document:
        return False
    current = document.get("current")
    if not isinstance(current, dict):
        return False
    return current.get("stage") in PipelineStage.TERMINAL
