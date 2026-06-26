"""Stage-progress summary + ETA over the durable progress stream (issue #470).

Issue #469 provides the real-time progress *transport* — a durable per-job
document of append-only events (stage, phase, segment ``N/M``, percent).  This
module derives the higher-level *stage progress* view asked for by #470: the
current pipeline stage, the segment counter, the phase, and an estimated time of
completion (ETA) computed from the *observed* timings of the event stream.

It is read-only and pure (no storage writes), so it can be unit-tested against a
plain document and reused by the monitoring API and the real-time channel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from podcaster.progress import PipelineStage

# Stages whose appearance means no further work is expected.
_TERMINAL = set(PipelineStage.TERMINAL)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _events(document: dict[str, Any]) -> list[dict[str, Any]]:
    events = document.get("events")
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, dict)]


def _current(document: dict[str, Any]) -> dict[str, Any]:
    current = document.get("current")
    if isinstance(current, dict):
        return current
    events = _events(document)
    return events[-1] if events else {}


def _stage_started_at(events: list[dict[str, Any]], stage: str) -> datetime | None:
    """Timestamp of the earliest event belonging to *stage* (when it began)."""
    for event in events:
        if event.get("stage") == stage:
            return _parse_iso(event.get("at"))
    return None


def compute_eta(
    document: dict[str, Any], *, now: datetime
) -> tuple[float | None, str | None]:
    """Estimate ``(remaining_seconds, eta_iso)`` from observed event timings.

    The estimate extrapolates the current stage: the elapsed wall-clock time
    since the stage began, divided by the number of segments completed so far,
    projected over the segments still to do.  Returns ``(None, None)`` when there
    is not enough signal (e.g. no segment counter yet), and ``(0.0, now)`` once a
    terminal stage is reached.
    """
    current = _current(document)
    stage = current.get("stage")

    if stage in _TERMINAL:
        return 0.0, _iso(now)

    total = current.get("segment_total")
    done = current.get("segment_index")
    if not (isinstance(total, int) and total > 0 and isinstance(done, int) and 0 < done < total):
        return None, None

    started = _stage_started_at(_events(document), stage)
    if started is None:
        return None, None

    elapsed = max((now - started).total_seconds(), 0.0)
    if elapsed <= 0:
        return None, None

    per_segment = elapsed / done
    remaining = per_segment * (total - done)
    eta = now + timedelta(seconds=remaining)
    return round(remaining, 1), _iso(eta)


def summarize(document: dict[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:
    """Build the stage-progress summary for a job's durable progress document.

    Returns a plain dict so the monitoring layer can wrap it in a response
    model.  A missing/empty document yields a ``pending`` summary so callers can
    distinguish "no progress yet" from "job not found".
    """
    moment = now or _utcnow()
    if not document:
        return {
            "stage": None,
            "phase": "pending",
            "segment_index": None,
            "segment_total": None,
            "percent": None,
            "message": None,
            "updated_at": None,
            "terminal": False,
            "eta": None,
            "eta_seconds": None,
        }

    current = _current(document)
    stage = current.get("stage")
    remaining, eta = compute_eta(document, now=moment)
    return {
        "stage": stage,
        "phase": current.get("phase") or ("pending" if stage is None else None),
        "segment_index": current.get("segment_index"),
        "segment_total": current.get("segment_total"),
        "percent": current.get("percent"),
        "message": current.get("message"),
        "updated_at": document.get("updated_at"),
        "terminal": stage in _TERMINAL,
        "eta": eta,
        "eta_seconds": remaining,
    }
