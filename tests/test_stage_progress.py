"""Tests for stage-progress summary + ETA (podcaster.stage_progress, issue #470)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from podcaster.progress import PipelineStage
from podcaster.stage_progress import compute_eta, summarize


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _doc(events: list[dict], updated_at: str | None = None) -> dict:
    current = {k: v for k, v in events[-1].items() if k != "seq"} if events else None
    return {
        "schema_version": 1,
        "job_id": "job-1",
        "updated_at": updated_at or (events[-1]["at"] if events else None),
        "current": current,
        "events": events,
    }


START = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


def test_summarize_none_document_is_pending():
    summary = summarize(None, now=START)
    assert summary["phase"] == "pending"
    assert summary["stage"] is None
    assert summary["segment_index"] is None
    assert summary["terminal"] is False
    assert summary["eta"] is None


def test_summarize_in_flight_segment_counter():
    events = [
        {"seq": 1, "at": _iso(START), "stage": PipelineStage.SYNTHESIS, "phase": "recording", "segment_total": 18},
        {
            "seq": 2,
            "at": _iso(START + timedelta(seconds=60)),
            "stage": PipelineStage.SYNTHESIS,
            "phase": "recording",
            "segment_index": 12,
            "segment_total": 18,
            "percent": 66.7,
        },
    ]
    summary = summarize(_doc(events), now=START + timedelta(seconds=60))
    assert summary["stage"] == PipelineStage.SYNTHESIS
    assert summary["phase"] == "recording"
    assert summary["segment_index"] == 12
    assert summary["segment_total"] == 18
    assert summary["percent"] == 66.7
    assert summary["terminal"] is False
    # 60s for 12 segments → 5s each → 6 remaining → 30s ETA.
    assert abs(summary["eta_seconds"] - 30.0) < 0.1
    assert summary["eta"] is not None


def test_summarize_completed_is_terminal_with_zero_eta():
    events = [
        {"seq": 1, "at": _iso(START), "stage": PipelineStage.SYNTHESIS, "phase": "recording", "segment_total": 5},
        {
            "seq": 2,
            "at": _iso(START + timedelta(seconds=20)),
            "stage": PipelineStage.COMPLETED,
            "phase": "synthesis",
            "segment_index": 5,
            "segment_total": 5,
            "percent": 100.0,
        },
    ]
    now = START + timedelta(seconds=20)
    summary = summarize(_doc(events), now=now)
    assert summary["stage"] == PipelineStage.COMPLETED
    assert summary["terminal"] is True
    assert summary["eta_seconds"] == 0.0
    assert summary["eta"] == _iso(now)


def test_summarize_no_segment_counter_yet_has_no_eta():
    events = [
        {"seq": 1, "at": _iso(START), "stage": PipelineStage.SYNTHESIS, "phase": "recording", "segment_total": 8},
    ]
    summary = summarize(_doc(events), now=START + timedelta(seconds=5))
    assert summary["stage"] == PipelineStage.SYNTHESIS
    assert summary["segment_index"] is None
    assert summary["eta_seconds"] is None
    assert summary["eta"] is None


def test_summarize_falls_back_to_last_event_when_no_current():
    events = [
        {"seq": 1, "at": _iso(START), "stage": PipelineStage.SCRIPT, "phase": "drafting"},
    ]
    doc = _doc(events)
    doc["current"] = None
    summary = summarize(doc, now=START)
    assert summary["stage"] == PipelineStage.SCRIPT


# ---------------------------------------------------------------------------
# compute_eta
# ---------------------------------------------------------------------------


def test_compute_eta_extrapolates_from_stage_start():
    events = [
        {"seq": 1, "at": _iso(START), "stage": PipelineStage.SYNTHESIS, "segment_total": 10},
        {
            "seq": 2,
            "at": _iso(START + timedelta(seconds=40)),
            "stage": PipelineStage.SYNTHESIS,
            "segment_index": 4,
            "segment_total": 10,
        },
    ]
    remaining, eta = compute_eta(_doc(events), now=START + timedelta(seconds=40))
    # 40s / 4 done = 10s each * 6 remaining = 60s.
    assert abs(remaining - 60.0) < 0.1
    assert eta == _iso(START + timedelta(seconds=100))


def test_compute_eta_zero_when_all_segments_done_non_terminal():
    # Final per-segment event arrived (done == total) but the stage hasn't
    # advanced yet: remaining work is known to be 0, so ETA is now, not None.
    events = [
        {"seq": 1, "at": _iso(START), "stage": PipelineStage.SYNTHESIS, "segment_total": 8},
        {
            "seq": 2,
            "at": _iso(START + timedelta(seconds=40)),
            "stage": PipelineStage.SYNTHESIS,
            "segment_index": 8,
            "segment_total": 8,
        },
    ]
    now = START + timedelta(seconds=40)
    remaining, eta = compute_eta(_doc(events), now=now)
    assert remaining == 0.0
    assert eta == _iso(now)


def test_compute_eta_none_when_no_elapsed_time():
    events = [
        {"seq": 1, "at": _iso(START), "stage": PipelineStage.SYNTHESIS, "segment_index": 2, "segment_total": 10},
    ]
    # now == stage start → zero elapsed → cannot estimate.
    remaining, eta = compute_eta(_doc(events), now=START)
    assert remaining is None
    assert eta is None


def test_compute_eta_zero_on_terminal():
    events = [{"seq": 1, "at": _iso(START), "stage": PipelineStage.FAILED, "phase": "synthesis"}]
    now = START + timedelta(seconds=3)
    remaining, eta = compute_eta(_doc(events), now=now)
    assert remaining == 0.0
    assert eta == _iso(now)
