"""Parallel browser recording pool (#479).

The video pipeline records each repo/website segment with a headless Chromium
browser driven by Playwright. Done sequentially, an episode with ``N`` segments
spends roughly ``N * per_segment`` seconds in the recording phase — by far the
dominant cost of the whole pipeline (~35 min for a busy week). Recording the
segments concurrently instead drops that wall-clock time to roughly ``1/N`` of
the sequential time, bounded by a small worker pool sized to the container's RAM
budget.

Design notes
------------
* **One browser per worker.** Playwright's *synchronous* API is not safe to
  share across threads from a single ``sync_playwright()`` instance, so every
  worker thread starts its own ``sync_playwright()`` + Chromium browser and
  records its share of the segments. This yields true parallelism (separate
  browser processes) while keeping each worker simple and sequential.
* **RAM-bounded concurrency.** Each Chromium context needs ~1.5 GB on the
  production ACA job (4 vCPU / 8 GB), so the default pool size is small
  (:data:`DEFAULT_RECORDING_CONCURRENCY`) and hard-capped
  (:data:`MAX_RECORDING_CONCURRENCY`) so a misconfiguration can't OOM the job.
  ``concurrency=1`` restores the original fully-sequential behaviour.
* **Order preserved.** Results are returned keyed by their original segment
  index regardless of which worker finishes first, so downstream composition
  sees the same ordering as the sequential path.
* **Language-independent.** Recording produces locale-neutral clips, so the
  pool is reusable across every language fan-out without change.

The pool itself is intentionally agnostic about *what* a recording does: the
caller supplies a ``record_one`` callback (which performs the actual
``_record_segment`` + checkpoint) and a ``launch_browser`` factory. This keeps
all Playwright/recording specifics in ``video_gen`` and makes the pool trivial
to unit-test with fakes.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Mapping

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.sync_api import Browser, Playwright

    from podcaster.video.sync_plan import VideoSegment
    from podcaster.video.video_gen import RecordedSegment

logger = logging.getLogger(__name__)

#: Default number of browsers recording at once. Sized for the production ACA
#: job (4 vCPU / 8 GB) where each Chromium context costs ~1.5 GB; three workers
#: leave headroom for ffmpeg composition and the Python process itself.
DEFAULT_RECORDING_CONCURRENCY = 3
#: Hard upper bound on workers regardless of configuration — a guardrail so an
#: over-eager ``PODCASTER_RECORDING_CONCURRENCY`` can't OOM the container.
MAX_RECORDING_CONCURRENCY = 8
#: Environment variable that overrides the pool size in production.
ENV_RECORDING_CONCURRENCY = "PODCASTER_RECORDING_CONCURRENCY"

#: Factory that launches a fresh browser from a per-thread Playwright instance.
LaunchBrowser = Callable[["Playwright"], "Browser"]
#: Records one segment given a browser, its plan index and the segment itself.
RecordOne = Callable[["Browser", int, "VideoSegment"], "RecordedSegment"]


@dataclass(frozen=True)
class RecordingPoolConfig:
    """Concurrency policy for the parallel recording pool."""

    concurrency: int = DEFAULT_RECORDING_CONCURRENCY

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")

    @property
    def parallel(self) -> bool:
        """True when more than one browser should record concurrently."""
        return self.concurrency > 1


def load_recording_pool_config(
    env: Mapping[str, str] | None = None,
) -> RecordingPoolConfig:
    """Build a :class:`RecordingPoolConfig` from the environment.

    Reads :data:`ENV_RECORDING_CONCURRENCY`. An unset/blank/invalid value falls
    back to :data:`DEFAULT_RECORDING_CONCURRENCY`; values are clamped into
    ``[1, MAX_RECORDING_CONCURRENCY]`` so the pool can never exceed the RAM
    guardrail.
    """
    env = env if env is not None else os.environ
    raw = env.get(ENV_RECORDING_CONCURRENCY)
    if raw is None or not raw.strip():
        return RecordingPoolConfig()
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(
            "invalid %s=%r; using default concurrency %d",
            ENV_RECORDING_CONCURRENCY,
            raw,
            DEFAULT_RECORDING_CONCURRENCY,
        )
        return RecordingPoolConfig()
    if value < 1:
        logger.warning(
            "%s=%d below minimum; clamping to 1 (sequential)",
            ENV_RECORDING_CONCURRENCY,
            value,
        )
        value = 1
    elif value > MAX_RECORDING_CONCURRENCY:
        logger.warning(
            "%s=%d exceeds RAM-safe maximum; clamping to %d",
            ENV_RECORDING_CONCURRENCY,
            value,
            MAX_RECORDING_CONCURRENCY,
        )
        value = MAX_RECORDING_CONCURRENCY
    return RecordingPoolConfig(concurrency=value)


def record_segments_parallel(
    pending: list[tuple[int, "VideoSegment"]],
    record_one: RecordOne,
    launch_browser: LaunchBrowser,
    config: RecordingPoolConfig,
    *,
    playwright_factory: Callable[[], object] | None = None,
) -> dict[int, "RecordedSegment"]:
    """Record ``pending`` segments concurrently across a pool of browsers.

    Args:
        pending: ``(index, segment)`` pairs that still need recording. The
            ``index`` is the segment's position in the episode plan and is used
            as the result key so callers can re-assemble plan order.
        record_one: Callback that records a single segment with a given browser
            and returns its :class:`RecordedSegment`.
        launch_browser: Factory invoked once per worker thread with that
            worker's own Playwright instance to obtain its browser.
        config: Pool sizing policy.
        playwright_factory: Zero-arg callable returning a ``sync_playwright()``
            context manager. Defaults to importing Playwright lazily; injectable
            so callers (and tests) can supply a patched factory.

    Returns:
        ``dict`` mapping each segment index to its :class:`RecordedSegment`.

    Raises:
        The first encountered error (by lowest segment index) if any segment
        fails to record, mirroring the fail-fast semantics of the sequential
        path. Every worker's browser is still closed before the error
        propagates.
    """
    if playwright_factory is None:
        from playwright.sync_api import sync_playwright

        playwright_factory = sync_playwright

    if not pending:
        return {}

    worker_count = max(1, min(config.concurrency, len(pending)))
    work: "queue.Queue[tuple[int, VideoSegment]]" = queue.Queue()
    for item in pending:
        work.put(item)

    results: dict[int, "RecordedSegment"] = {}
    errors: dict[int, Exception] = {}
    lock = threading.Lock()

    def _worker(worker_id: int) -> None:
        # Each worker owns its own Playwright instance + browser; the sync API
        # is not safe to share across threads.
        with playwright_factory() as pw:
            browser = launch_browser(pw)
            try:
                while True:
                    try:
                        index, segment = work.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        recorded = record_one(browser, index, segment)
                        with lock:
                            results[index] = recorded
                    except Exception as exc:  # noqa: BLE001 - recorded, re-raised
                        logger.exception(
                            "recording worker %d failed on segment %d",
                            worker_id,
                            index,
                        )
                        with lock:
                            errors[index] = exc
                    finally:
                        work.task_done()
            finally:
                try:
                    browser.close()
                except Exception:  # pragma: no cover - defensive cleanup
                    logger.debug(
                        "error closing browser in worker %d",
                        worker_id,
                        exc_info=True,
                    )

    logger.info(
        "Recording %d segment(s) across %d parallel browser worker(s)",
        len(pending),
        worker_count,
    )
    threads = [
        threading.Thread(target=_worker, args=(i,), name=f"rec-worker-{i}")
        for i in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if errors:
        # Surface the earliest-failing segment so behaviour is deterministic and
        # matches the sequential path (which would have raised on it first).
        first_index = min(errors)
        raise errors[first_index]

    return results
