"""Lightweight phase timing + resource instrumentation for the video pipeline.

Part of the video performance review (issue #396).  The video pipeline runs in
a single ACA container (4 vCPU / 8 GB / 90 min timeout) through four heavy
phases — recording, composition, canonicalization, distribution — and the only
way to drive optimisation is to *measure* where the wall-clock and memory go.

This module provides a tiny, dependency-free instrument:

* :class:`PhaseTimer` — a context manager that records a single phase's wall
  time, CPU time (user+sys) and peak RSS, appending the result to a
  :class:`PipelineTimings` accumulator.
* :class:`PipelineTimings` — collects per-phase records, renders a human log
  summary and a JSON-serialisable breakdown (persisted into the manifest so
  before/after comparisons are possible without re-instrumenting).

It deliberately uses only the standard library (``time``, ``resource``) so it
adds no runtime dependency and negligible overhead.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

try:  # ``resource`` is POSIX-only; the pipeline runs on Linux (ACA).
    import resource as _resource
except ImportError:  # pragma: no cover - non-POSIX fallback
    _resource = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _peak_rss_mb() -> float:
    """Return peak resident-set size in MiB, or 0.0 when unavailable.

    ``ru_maxrss`` is reported in KiB on Linux and bytes on macOS; we assume the
    Linux convention (the production runtime) and convert KiB → MiB.
    """
    if _resource is None:
        return 0.0
    try:
        kib = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # pragma: no cover - defensive
        return 0.0
    return round(kib / 1024.0, 1)


def _cpu_seconds() -> float:
    """Return cumulative user+system CPU seconds for this process (0.0 if N/A)."""
    if _resource is None:
        return 0.0
    try:
        usage = _resource.getrusage(_resource.RUSAGE_SELF)
    except Exception:  # pragma: no cover - defensive
        return 0.0
    return usage.ru_utime + usage.ru_stime


@dataclass
class PhaseRecord:
    """Timing + resource measurement for one pipeline phase."""

    name: str
    wall_seconds: float
    cpu_seconds: float
    peak_rss_mb: float

    def to_dict(self) -> dict[str, float | str]:
        return {
            "name": self.name,
            "wall_seconds": round(self.wall_seconds, 3),
            "cpu_seconds": round(self.cpu_seconds, 3),
            "peak_rss_mb": self.peak_rss_mb,
        }


@dataclass
class PipelineTimings:
    """Accumulates :class:`PhaseRecord` measurements across the pipeline."""

    phases: list[PhaseRecord] = field(default_factory=list)

    def record(self, phase: PhaseRecord) -> None:
        self.phases.append(phase)

    def phase(self, name: str) -> "PhaseTimer":
        """Return a :class:`PhaseTimer` context manager bound to this collector."""
        return PhaseTimer(name, self)

    @property
    def total_wall_seconds(self) -> float:
        return sum(p.wall_seconds for p in self.phases)

    def slowest(self, top: int = 3) -> list[PhaseRecord]:
        """Return the *top* phases by wall time, descending (the bottlenecks)."""
        return sorted(self.phases, key=lambda p: p.wall_seconds, reverse=True)[:top]

    def to_dict(self) -> dict[str, object]:
        total = self.total_wall_seconds
        out: list[dict[str, object]] = []
        for p in self.phases:
            row = p.to_dict()
            row["pct_of_total"] = round(100.0 * p.wall_seconds / total, 1) if total else 0.0
            out.append(row)
        return {
            "total_wall_seconds": round(total, 3),
            "peak_rss_mb": max((p.peak_rss_mb for p in self.phases), default=0.0),
            "phases": out,
        }

    def summary(self) -> str:
        """Render a one-line-per-phase human summary for logging."""
        total = self.total_wall_seconds
        lines = [f"Pipeline timing breakdown (total {total:.1f}s):"]
        for p in self.phases:
            pct = (100.0 * p.wall_seconds / total) if total else 0.0
            lines.append(
                f"  {p.name:<16} {p.wall_seconds:8.1f}s ({pct:4.1f}%)  "
                f"cpu={p.cpu_seconds:7.1f}s  peakRSS={p.peak_rss_mb:.0f}MiB"
            )
        slow = self.slowest()
        if slow:
            names = ", ".join(f"{p.name} ({p.wall_seconds:.0f}s)" for p in slow)
            lines.append(f"  bottlenecks: {names}")
        return "\n".join(lines)

    def log_summary(self, log: logging.Logger | None = None) -> None:
        (log or logger).info("%s", self.summary())


class PhaseTimer:
    """Context manager measuring one phase's wall/CPU time and peak RSS.

    On exit the measurement is appended to the bound :class:`PipelineTimings`
    *even when the body raises*, so a failed phase still contributes timing data
    (then the exception propagates).
    """

    def __init__(self, name: str, collector: PipelineTimings) -> None:
        self.name = name
        self._collector = collector
        self._wall_start = 0.0
        self._cpu_start = 0.0
        self.record: PhaseRecord | None = None

    def __enter__(self) -> "PhaseTimer":
        self._wall_start = time.monotonic()
        self._cpu_start = _cpu_seconds()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        wall = time.monotonic() - self._wall_start
        cpu = max(0.0, _cpu_seconds() - self._cpu_start)
        self.record = PhaseRecord(
            name=self.name,
            wall_seconds=wall,
            cpu_seconds=cpu,
            peak_rss_mb=_peak_rss_mb(),
        )
        self._collector.record(self.record)
        logger.info(
            "phase=%s wall=%.1fs cpu=%.1fs peakRSS=%.0fMiB%s",
            self.name,
            wall,
            cpu,
            self.record.peak_rss_mb,
            " (failed)" if exc_type is not None else "",
        )
        # Never suppress exceptions.
        return False
