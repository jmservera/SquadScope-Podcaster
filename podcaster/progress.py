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
          "tasks": { "<task_id>": { <latest in-flight task event> }, ... },
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
          "task_id": "norm_07",      # optional stable per-worker task id
          "task_status": "running",  # optional running|done|failed (with task_id)
          "message": "recording 12/18"  # optional human-readable detail
        }

    Parallel-stage observability (issue #482)
        Phase 5 runs several stages with multiple workers in flight at once
        (async TTS, parallel recording, per-clip normalize, pairwise compose).
        Aggregate ``segment N/M`` progress hides *which* individual tasks are
        running, so each parallel worker additionally emits ``task_id`` events
        with a ``task_status`` of ``running`` / ``done`` / ``failed``. The
        document keeps a ``tasks`` map of the currently in-flight tasks: a
        ``running`` event upserts the task, a terminal (``done`` / ``failed``)
        event removes it. Consumers read ``document["tasks"]`` to list the
        parallel work happening *right now*, while the ``events`` log retains the
        full start/finish history for each task.

    The schema is intentionally stable and self-describing so the stage-progress
    API (#470), the log viewer (#472), and the UI stage-visualization component
    (#474) can all consume the same events.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

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


class TaskStatus:
    """Lifecycle status for an individual parallel-worker task (issue #482).

    A ``RUNNING`` event marks a task as in-flight (it is added to the document's
    ``tasks`` map); ``DONE`` and ``FAILED`` are terminal and remove the task from
    the in-flight map.
    """

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    ALL = (RUNNING, DONE, FAILED)
    #: Statuses after which a task is no longer in flight.
    TERMINAL = (DONE, FAILED)


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
    task_id: str | None = None
    task_status: str | None = None
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
        clamped = max(0, min(segment_index, segment_total))
        return round(100.0 * clamped / segment_total, 1)
    return None


def _empty_document(job_id: str) -> dict[str, Any]:
    return {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "job_id": job_id,
        "updated_at": None,
        "current": None,
        "tasks": {},
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
    if not isinstance(doc.get("tasks"), dict):
        doc["tasks"] = {}
    return doc


def _safe_seq(event: Any) -> int | None:
    """Parse an event's ``seq`` as an int, or ``None`` when missing/malformed.

    The durable document may be partially corrupted (e.g. a non-numeric
    ``seq``); progress reading/emission must stay resilient rather than 500.
    """
    if not isinstance(event, dict):
        return None
    try:
        return int(event.get("seq", 0))
    except (ValueError, TypeError):
        return None


def _next_seq(events: list[Any]) -> int:
    """Next monotonic seq, derived defensively from the highest valid seq."""
    max_seq = 0
    for event in events:
        seq = _safe_seq(event)
        if seq is not None and seq > max_seq:
            max_seq = seq
    return max_seq + 1


def filter_events_since(events: list[Any], after_seq: int = 0) -> list[dict[str, Any]]:
    """Events from an already-loaded list whose ``seq`` exceeds ``after_seq``.

    Operating on an in-hand snapshot lets callers avoid re-reading the blob
    (keeping the current/terminal snapshot consistent with the returned events)
    and skips malformed events instead of raising.
    """
    return [
        event
        for event in events
        if (seq := _safe_seq(event)) is not None and seq > after_seq
    ]


def emit_progress(
    storage: StorageBackend,
    job_id: str,
    *,
    stage: str,
    phase: str | None = None,
    segment_index: int | None = None,
    segment_total: int | None = None,
    percent: float | None = None,
    task_id: str | None = None,
    task_status: str | None = None,
    message: str | None = None,
    at: datetime | None = None,
) -> ProgressEvent | None:
    """Append a progress event to the job's durable store.

    This is best-effort: progress reporting must *never* break the pipeline, so
    any storage failure is logged and swallowed (returning ``None``). The append
    is atomic via ``storage.update_bytes``, making it safe for concurrent
    workers and resilient to worker restarts.

    When ``task_id`` is supplied the event also maintains the document's
    ``tasks`` map of currently in-flight parallel work (issue #482): a
    ``task_status`` of :data:`TaskStatus.RUNNING` upserts the task, while a
    terminal status (:data:`TaskStatus.DONE` / :data:`TaskStatus.FAILED`)
    removes it. ``task_status`` defaults to ``RUNNING`` when a ``task_id`` is
    given without one.
    """

    moment = _iso(at or datetime.now(timezone.utc))
    resolved_percent = _derive_percent(percent, segment_index, segment_total)
    resolved_task_status = task_status
    if task_id is not None and resolved_task_status is None:
        resolved_task_status = TaskStatus.RUNNING
    captured: dict[str, Any] = {}

    def _apply(content: bytes | None) -> bytes:
        document = _load_document(content, job_id)
        events = document["events"]
        next_seq = _next_seq(events)
        event = ProgressEvent(
            seq=next_seq,
            at=moment,
            stage=stage,
            phase=phase,
            segment_index=segment_index,
            segment_total=segment_total,
            percent=resolved_percent,
            task_id=task_id,
            task_status=resolved_task_status,
            message=message,
        ).to_dict()
        events.append(event)
        if len(events) > MAX_EVENTS:
            del events[: len(events) - MAX_EVENTS]
        document["events"] = events
        document["current"] = {k: v for k, v in event.items() if k != "seq"}
        document["updated_at"] = moment
        if task_id is not None:
            tasks = document.get("tasks")
            if not isinstance(tasks, dict):
                tasks = {}
            if resolved_task_status in TaskStatus.TERMINAL:
                tasks.pop(task_id, None)
            else:
                tasks[task_id] = {k: v for k, v in event.items() if k != "seq"}
            document["tasks"] = tasks
        captured.update(event)
        return json.dumps(document, ensure_ascii=False).encode("utf-8")

    try:
        storage.update_bytes(progress_path(job_id), "application/json; charset=utf-8", _apply)
    except Exception:  # noqa: BLE001 - progress emission must never mask the real work
        logger.warning(
            "could not emit progress job_id=%s stage=%s", job_id, stage, exc_info=True
        )
        return None

    if not captured:
        return None
    return ProgressEvent(**{k: captured.get(k) for k in ProgressEvent.__dataclass_fields__})


#: A per-stage task-progress callback. ``status`` is one of :class:`TaskStatus`;
#: a falsy/None default lets parallel stages stay decoupled from storage.
TaskReporter = Callable[..., None]


def emit_task_progress(
    storage: StorageBackend,
    job_id: str,
    *,
    stage: str,
    task_id: str,
    status: str = TaskStatus.RUNNING,
    phase: str | None = None,
    segment_index: int | None = None,
    segment_total: int | None = None,
    message: str | None = None,
    at: datetime | None = None,
) -> ProgressEvent | None:
    """Emit a single parallel-worker task event (issue #482).

    Convenience wrapper over :func:`emit_progress` for the ``task_id`` path so
    parallel stages can report ``running`` / ``done`` / ``failed`` without
    repeating the keyword plumbing.
    """
    return emit_progress(
        storage,
        job_id,
        stage=stage,
        phase=phase,
        segment_index=segment_index,
        segment_total=segment_total,
        task_id=task_id,
        task_status=status,
        message=message,
        at=at,
    )


def make_task_reporter(
    storage: StorageBackend | None,
    job_id: str | None,
    *,
    stage: str,
) -> TaskReporter:
    """Build a best-effort ``reporter(task_id, status, **kw)`` for a parallel stage.

    The returned callable forwards to :func:`emit_task_progress`, binding the
    durable store, ``job_id`` and ``stage`` so deep parallel stages (normalize,
    TTS, recording) only need a stable ``task_id`` and a :class:`TaskStatus`.
    When ``storage`` or ``job_id`` is missing — local dev / unit tests — a no-op
    reporter is returned, so callers can pass it unconditionally. Like all
    progress emission it never raises into the worker.
    """

    if storage is None or not job_id:
        def _noop(task_id: str, status: str = TaskStatus.RUNNING, **kwargs: Any) -> None:
            return None

        return _noop

    def _report(task_id: str, status: str = TaskStatus.RUNNING, **kwargs: Any) -> None:
        emit_task_progress(
            storage,
            job_id,
            stage=stage,
            task_id=task_id,
            status=status,
            **kwargs,
        )

    return _report


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
    return filter_events_since(events, after_seq)


def is_terminal(document: dict[str, Any] | None) -> bool:
    """True when the latest event marks a terminal stage (completed/failed)."""
    if not document:
        return False
    current = document.get("current")
    if not isinstance(current, dict):
        return False
    return current.get("stage") in PipelineStage.TERMINAL


def in_flight_tasks(document: dict[str, Any] | None) -> dict[str, Any]:
    """Currently running parallel tasks keyed by ``task_id`` (issue #482).

    Returns the document's ``tasks`` map — each value is the latest ``running``
    event for that task — or an empty mapping when absent/malformed.
    """
    if not document:
        return {}
    tasks = document.get("tasks")
    if not isinstance(tasks, dict):
        return {}
    return tasks
