"""Tests for the parallel browser recording pool (#479)."""

from __future__ import annotations

import threading
import time

import pytest

from podcaster.video.recording_pool import (
    DEFAULT_RECORDING_CONCURRENCY,
    MAX_RECORDING_CONCURRENCY,
    RecordingPoolConfig,
    load_recording_pool_config,
    record_segments_parallel,
)

# --- Fakes --------------------------------------------------------------------


class _FakePlaywright:
    """Minimal stand-in for the ``sync_playwright()`` context manager."""

    def __enter__(self) -> "_FakePlaywright":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _FakeBrowser:
    def __init__(self, browser_id: int) -> None:
        self.browser_id = browser_id
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _make_factory():
    """Return a ``playwright_factory`` plus the list of instances it creates."""
    created: list[_FakePlaywright] = []
    lock = threading.Lock()

    def factory() -> _FakePlaywright:
        pw = _FakePlaywright()
        with lock:
            created.append(pw)
        return pw

    return factory, created


def _make_launcher():
    """Return a ``launch_browser`` factory plus the list of browsers it makes."""
    browsers: list[_FakeBrowser] = []
    lock = threading.Lock()

    def launch(pw) -> _FakeBrowser:
        with lock:
            browser = _FakeBrowser(len(browsers))
            browsers.append(browser)
            return browser

    return launch, browsers


def _pending(n: int) -> list[tuple[int, str]]:
    """Build ``n`` (index, segment) pairs; the segment is a label string here."""
    return [(i, f"seg-{i}") for i in range(n)]


# --- Config -------------------------------------------------------------------


class TestConfig:
    def test_default_is_parallel(self) -> None:
        cfg = RecordingPoolConfig()
        assert cfg.concurrency == DEFAULT_RECORDING_CONCURRENCY
        assert cfg.parallel is True

    def test_concurrency_one_is_sequential(self) -> None:
        assert RecordingPoolConfig(concurrency=1).parallel is False

    def test_rejects_below_one(self) -> None:
        with pytest.raises(ValueError):
            RecordingPoolConfig(concurrency=0)

    def test_load_default_when_unset(self) -> None:
        cfg = load_recording_pool_config({})
        assert cfg.concurrency == DEFAULT_RECORDING_CONCURRENCY

    def test_load_blank_falls_back_to_default(self) -> None:
        cfg = load_recording_pool_config({"PODCASTER_RECORDING_CONCURRENCY": "  "})
        assert cfg.concurrency == DEFAULT_RECORDING_CONCURRENCY

    def test_load_explicit_value(self) -> None:
        cfg = load_recording_pool_config({"PODCASTER_RECORDING_CONCURRENCY": "4"})
        assert cfg.concurrency == 4

    def test_load_clamps_to_max(self) -> None:
        cfg = load_recording_pool_config(
            {"PODCASTER_RECORDING_CONCURRENCY": "999"}
        )
        assert cfg.concurrency == MAX_RECORDING_CONCURRENCY

    def test_load_clamps_below_one_to_sequential(self) -> None:
        cfg = load_recording_pool_config(
            {"PODCASTER_RECORDING_CONCURRENCY": "0"}
        )
        assert cfg.concurrency == 1

    def test_load_invalid_falls_back_to_default(self) -> None:
        cfg = load_recording_pool_config(
            {"PODCASTER_RECORDING_CONCURRENCY": "lots"}
        )
        assert cfg.concurrency == DEFAULT_RECORDING_CONCURRENCY


# --- record_segments_parallel -------------------------------------------------


class TestRecordSegmentsParallel:
    def test_empty_pending_returns_empty(self) -> None:
        factory, _ = _make_factory()
        launch, browsers = _make_launcher()
        result = record_segments_parallel(
            [], lambda b, i, s: s, launch, RecordingPoolConfig(3),
            playwright_factory=factory,
        )
        assert result == {}
        assert browsers == []  # no workers started

    def test_results_keyed_by_index(self) -> None:
        factory, _ = _make_factory()
        launch, _ = _make_launcher()

        def record_one(browser, index, segment):
            return f"recorded-{index}-{segment}"

        result = record_segments_parallel(
            _pending(5), record_one, launch, RecordingPoolConfig(3),
            playwright_factory=factory,
        )
        assert result == {
            0: "recorded-0-seg-0",
            1: "recorded-1-seg-1",
            2: "recorded-2-seg-2",
            3: "recorded-3-seg-3",
            4: "recorded-4-seg-4",
        }

    def test_every_segment_recorded_once(self) -> None:
        factory, _ = _make_factory()
        launch, _ = _make_launcher()
        seen: list[int] = []
        lock = threading.Lock()

        def record_one(browser, index, segment):
            with lock:
                seen.append(index)
            return index

        record_segments_parallel(
            _pending(12), record_one, launch, RecordingPoolConfig(4),
            playwright_factory=factory,
        )
        assert sorted(seen) == list(range(12))

    def test_each_worker_gets_its_own_browser(self) -> None:
        factory, created = _make_factory()
        launch, browsers = _make_launcher()
        used: dict[int, int] = {}
        lock = threading.Lock()

        def record_one(browser, index, segment):
            with lock:
                used[index] = browser.browser_id
            return index

        record_segments_parallel(
            _pending(8), record_one, launch, RecordingPoolConfig(4),
            playwright_factory=factory,
        )
        # One Playwright instance + one browser per worker; never more workers
        # than pending items, and capped by the configured concurrency.
        assert len(browsers) == 4
        assert len(created) == 4
        # Every browser used by some segment is one we launched, and all closed.
        assert set(used.values()).issubset({b.browser_id for b in browsers})
        assert all(b.closed for b in browsers)

    def test_worker_count_capped_by_pending(self) -> None:
        factory, created = _make_factory()
        launch, browsers = _make_launcher()
        record_segments_parallel(
            _pending(2), lambda b, i, s: i, launch, RecordingPoolConfig(8),
            playwright_factory=factory,
        )
        # Only 2 items → only 2 browsers despite concurrency=8.
        assert len(browsers) == 2
        assert len(created) == 2

    def test_respects_concurrency_bound(self) -> None:
        factory, _ = _make_factory()
        launch, _ = _make_launcher()
        concurrency = 3
        in_flight = 0
        max_in_flight = 0
        lock = threading.Lock()

        def record_one(browser, index, segment):
            nonlocal in_flight, max_in_flight
            with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            time.sleep(0.02)
            with lock:
                in_flight -= 1
            return index

        record_segments_parallel(
            _pending(12), record_one, launch, RecordingPoolConfig(concurrency),
            playwright_factory=factory,
        )
        assert max_in_flight <= concurrency

    def test_parallel_runs_workers_concurrently(self) -> None:
        factory, _ = _make_factory()
        launch, _ = _make_launcher()
        concurrency = 4
        n = 8
        # A reusable barrier of `concurrency` parties only releases once that
        # many workers are simultaneously inside record_one. This is a
        # deterministic proof of real parallelism that does not depend on
        # wall-clock timing: if fewer than `concurrency` workers ran at once the
        # barrier would never fill and would raise BrokenBarrierError on timeout.
        barrier = threading.Barrier(concurrency, timeout=5)
        thread_ids: set[int] = set()
        lock = threading.Lock()

        def record_one(browser, index, segment):
            with lock:
                thread_ids.add(threading.get_ident())
            barrier.wait()
            return index

        record_segments_parallel(
            _pending(n), record_one, launch, RecordingPoolConfig(concurrency),
            playwright_factory=factory,
        )
        # At least `concurrency` distinct worker threads were active together.
        assert len(thread_ids) >= concurrency

    def test_error_propagates_lowest_index(self) -> None:
        factory, _ = _make_factory()
        launch, browsers = _make_launcher()

        class Boom(RuntimeError):
            pass

        def record_one(browser, index, segment):
            if index in (3, 5):
                raise Boom(f"boom-{index}")
            return index

        with pytest.raises(Boom) as excinfo:
            record_segments_parallel(
                _pending(8), record_one, launch, RecordingPoolConfig(4),
                playwright_factory=factory,
            )
        # The earliest-failing segment's error wins for deterministic behaviour.
        assert "boom-3" in str(excinfo.value)
        # All browsers are still closed even though recording failed.
        assert all(b.closed for b in browsers)

    def test_single_worker_records_all_in_one_browser(self) -> None:
        factory, created = _make_factory()
        launch, browsers = _make_launcher()
        result = record_segments_parallel(
            _pending(3), lambda b, i, s: i, launch, RecordingPoolConfig(1),
            playwright_factory=factory,
        )
        assert result == {0: 0, 1: 1, 2: 2}
        assert len(browsers) == 1
        assert len(created) == 1
        assert browsers[0].closed is True
