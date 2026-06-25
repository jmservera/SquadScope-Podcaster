"""Tests for podcaster.video.perf — phase timing instrumentation (issue #396)."""

from __future__ import annotations

import time

import pytest

from podcaster.video.perf import (
    PhaseRecord,
    PhaseTimer,
    PipelineTimings,
)


class TestPhaseTimer:
    def test_records_phase_on_exit(self):
        timings = PipelineTimings()
        with timings.phase("recording"):
            time.sleep(0.01)
        assert len(timings.phases) == 1
        rec = timings.phases[0]
        assert rec.name == "recording"
        assert rec.wall_seconds >= 0.0
        assert rec.cpu_seconds >= 0.0

    def test_records_even_when_body_raises(self):
        timings = PipelineTimings()
        with pytest.raises(ValueError):
            with timings.phase("composition"):
                raise ValueError("boom")
        # The failed phase still contributes a timing record.
        assert len(timings.phases) == 1
        assert timings.phases[0].name == "composition"

    def test_timer_does_not_suppress_exception(self):
        timings = PipelineTimings()
        timer = PhaseTimer("x", timings)
        timer.__enter__()
        assert timer.__exit__(ValueError, ValueError("e"), None) is False


class TestPipelineTimings:
    def _populate(self) -> PipelineTimings:
        t = PipelineTimings()
        t.record(PhaseRecord("recording", 16.0, 10.0, 500.0))
        t.record(PhaseRecord("composition", 33.0, 30.0, 800.0))
        t.record(PhaseRecord("distribution", 3.0, 1.0, 400.0))
        return t

    def test_total_wall_seconds(self):
        assert self._populate().total_wall_seconds == pytest.approx(52.0)

    def test_slowest_identifies_bottlenecks(self):
        slow = self._populate().slowest(top=3)
        assert [p.name for p in slow] == ["composition", "recording", "distribution"]

    def test_slowest_top_limit(self):
        slow = self._populate().slowest(top=1)
        assert len(slow) == 1
        assert slow[0].name == "composition"

    def test_to_dict_has_pct_of_total(self):
        data = self._populate().to_dict()
        assert data["total_wall_seconds"] == pytest.approx(52.0)
        assert data["peak_rss_mb"] == 800.0
        comp = next(p for p in data["phases"] if p["name"] == "composition")
        assert comp["pct_of_total"] == pytest.approx(63.5, abs=0.1)

    def test_to_dict_empty_no_div_by_zero(self):
        data = PipelineTimings().to_dict()
        assert data["total_wall_seconds"] == 0.0
        assert data["phases"] == []

    def test_summary_mentions_bottlenecks(self):
        summary = self._populate().summary()
        assert "composition" in summary
        assert "bottlenecks" in summary
