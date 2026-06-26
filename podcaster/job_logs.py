"""Durable, structured per-job log collection (issue #472).

Part of the Phase 5 Observability epic (SquadScope-Coordinator#30). The pipeline
emits structured log *records* — level, message, optional task id and stage — to
a durable per-job store so the monitoring API and UI can surface a filterable,
searchable log for every job (independent of the manifest snapshot and the
real-time progress stream).

Why a durable blob store (and not stdlib ``logging`` alone)?
    Azure Container Apps executes the pipeline in serverless, stateless workers
    that can scale to zero or restart between executions. A per-job log that the
    monitoring API (a *different* process) can read back must therefore live in
    durable storage, not in worker memory. Records are appended to
    ``jobs/{job_id}/logs.json`` via the storage backend's atomic ``update_bytes``
    (the same compare-and-write primitive used for manifests and progress), so it
    is safe under concurrent workers and survives restarts.

Log document schema (``schema_version = "squadscope-podcaster-logs-v1"``)::

    {
      "schema_version": "squadscope-podcaster-logs-v1",
      "job_id": "<id>",
      "updated_at": "2026-06-26T12:00:00Z",
      "records": [ <LogRecord>, ... ]   # ordered, monotonic "seq"
    }

Each ``LogRecord`` is::

    {
      "seq": 1,                  # monotonic, 1-based
      "at": "2026-06-26T12:00:00Z",
      "level": "info",           # one of LogLevel.ALL
      "message": "recording 12/18 segments",
      "task_id": "tts-3",        # optional per-worker task id (ties to #482)
      "stage": "synthesis",      # optional pipeline stage
      "context": { ... }         # optional structured key/value detail
    }
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

LOGS_SCHEMA_VERSION = "squadscope-podcaster-logs-v1"

# Keep the appended-record log bounded so a long or pathological run can't grow
# the durable document without limit. The newest records are always retained.
MAX_RECORDS = 1000


class LogLevel:
    """Canonical structured-log severity levels, ordered least → most severe."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    ALL = (DEBUG, INFO, WARNING, ERROR)

    #: Severity rank used for minimum-level filtering (higher == more severe).
    _RANK = {DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40}

    @classmethod
    def normalize(cls, level: Any) -> str:
        """Coerce arbitrary input to a known level, defaulting to ``INFO``.

        Accepts common aliases (``warn`` → ``warning``, ``err``/``critical``/
        ``fatal`` → ``error``) so callers and query params stay forgiving.
        """
        if not isinstance(level, str):
            return cls.INFO
        value = level.strip().lower()
        aliases = {
            "warn": cls.WARNING,
            "err": cls.ERROR,
            "critical": cls.ERROR,
            "fatal": cls.ERROR,
            "trace": cls.DEBUG,
        }
        value = aliases.get(value, value)
        return value if value in cls._RANK else cls.INFO

    @classmethod
    def rank(cls, level: Any) -> int:
        return cls._RANK.get(cls.normalize(level), cls._RANK[cls.INFO])


@dataclass(frozen=True)
class LogRecord:
    """A single structured log record. See the module docstring for the schema."""

    seq: int
    at: str
    level: str
    message: str
    task_id: str | None = None
    stage: str | None = None
    context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def logs_path(job_id: str) -> str:
    """Durable blob path for a job's structured-log document."""
    return f"jobs/{job_id}/logs.json"


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _empty_document(job_id: str) -> dict[str, Any]:
    return {
        "schema_version": LOGS_SCHEMA_VERSION,
        "job_id": job_id,
        "updated_at": None,
        "records": [],
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
    doc.setdefault("schema_version", LOGS_SCHEMA_VERSION)
    doc.setdefault("job_id", job_id)
    if not isinstance(doc.get("records"), list):
        doc["records"] = []
    return doc


def _safe_seq(record: Any) -> int | None:
    """Parse a record's ``seq`` as an int, or ``None`` when missing/malformed.

    The durable document may be partially corrupted; log reading/emission must
    stay resilient rather than 500.
    """
    if not isinstance(record, dict):
        return None
    try:
        return int(record.get("seq", 0))
    except (ValueError, TypeError):
        return None


def _next_seq(records: list[Any]) -> int:
    """Next monotonic seq, derived defensively from the highest valid seq."""
    max_seq = 0
    for record in records:
        seq = _safe_seq(record)
        if seq is not None and seq > max_seq:
            max_seq = seq
    return max_seq + 1


def filter_records(
    records: list[Any],
    *,
    level: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Filter an already-loaded record list by minimum ``level`` and ``search``.

    ``level`` keeps records at *or above* the requested severity (e.g.
    ``warning`` returns warnings and errors), matching the usual "minimum level"
    semantics of a log viewer. ``search`` is a case-insensitive substring match
    against the message, task id and stage. Malformed records are skipped.
    """
    min_rank = LogLevel.rank(level) if level else None
    needle = search.strip().lower() if isinstance(search, str) and search.strip() else None

    out: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if min_rank is not None and LogLevel.rank(record.get("level")) < min_rank:
            continue
        if needle is not None:
            haystack = " ".join(
                str(record.get(field, ""))
                for field in ("message", "task_id", "stage")
            ).lower()
            if needle not in haystack:
                continue
        out.append(record)
    return out


def emit_log(
    storage: StorageBackend,
    job_id: str,
    *,
    message: str,
    level: str = LogLevel.INFO,
    task_id: str | None = None,
    stage: str | None = None,
    context: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> LogRecord | None:
    """Append a structured log record to the job's durable store.

    Best-effort: log collection must *never* break the pipeline, so any storage
    failure is logged and swallowed (returning ``None``). The append is atomic
    via ``storage.update_bytes``, making it safe for concurrent workers and
    resilient to worker restarts.
    """

    moment = _iso(at or datetime.now(timezone.utc))
    normalized_level = LogLevel.normalize(level)
    captured: dict[str, Any] = {}

    def _apply(content: bytes | None) -> bytes:
        document = _load_document(content, job_id)
        records = document["records"]
        next_seq = _next_seq(records)
        record = LogRecord(
            seq=next_seq,
            at=moment,
            level=normalized_level,
            message=message,
            task_id=task_id,
            stage=stage,
            context=context,
        ).to_dict()
        records.append(record)
        if len(records) > MAX_RECORDS:
            del records[: len(records) - MAX_RECORDS]
        document["records"] = records
        document["updated_at"] = moment
        captured.update(record)
        return json.dumps(document, ensure_ascii=False).encode("utf-8")

    try:
        storage.update_bytes(logs_path(job_id), "application/json; charset=utf-8", _apply)
    except Exception:  # noqa: BLE001 - log emission must never mask the real work
        logger.warning(
            "could not emit log job_id=%s level=%s", job_id, normalized_level, exc_info=True
        )
        return None

    if not captured:
        return None
    return LogRecord(**{k: captured.get(k) for k in LogRecord.__dataclass_fields__})


def read_logs(storage: StorageBackend, job_id: str) -> dict[str, Any] | None:
    """Return the full durable log document, or ``None`` if absent."""
    raw = storage.get_bytes(logs_path(job_id))
    if not raw:
        return None
    return _load_document(raw, job_id)
