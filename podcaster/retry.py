"""Reusable per-task retry with bounded attempts and exponential backoff.

Used by the parallel generation pipeline (issue #483) so a single failed task —
one segment normalize, one browser recording — retries *in isolation* instead of
aborting the whole run.  Tasks are expected to be idempotent: the pipeline's
blob-checkpoint resume (issue #410) makes re-running an already-completed task a
no-op, and a failed task leaves no checkpoint behind, so a retry recomputes it
cleanly.

Only ``Exception`` subclasses are retried by default; ``BaseException`` (e.g.
``KeyboardInterrupt`` / ``SystemExit``) always propagates immediately so an
interrupted run is never silently retried.
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid int for %s=%r; using default %d", name, raw, default)
        return default


# Default bounded attempts for a single pipeline task.  ``1`` disables retry
# (a single attempt); the floor is enforced in :func:`retry_call`.
DEFAULT_TASK_RETRIES = max(1, _env_int("PODCASTER_TASK_RETRIES", 3))


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = DEFAULT_TASK_RETRIES,
    base_delay: float = 0.5,
    backoff: float = 2.0,
    max_delay: float = 30.0,
    jitter: float = 0.1,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    give_up_on: tuple[type[BaseException], ...] = (),
    on_retry: Callable[[int, BaseException], None] | None = None,
    sleep: Callable[[float], None] | None = None,
    description: str = "task",
) -> T:
    """Call ``fn`` with bounded retries and exponential backoff.

    Args:
        fn: Zero-argument callable performing one idempotent task.
        attempts: Maximum number of attempts (total, not extra retries).  Values
            below 1 are treated as 1.
        base_delay: Delay before the first retry, in seconds.
        backoff: Multiplier applied to the delay after each failed attempt.
        max_delay: Upper bound for any single backoff delay.
        jitter: Fraction (0..1) of the delay added as uniform random jitter to
            avoid thundering-herd retries across parallel workers.  Negative
            values are clamped to ``0``.
        retry_on: Exception types that trigger a retry.  ``KeyboardInterrupt``
            and ``SystemExit`` always propagate immediately regardless of this
            setting.
        give_up_on: Exception types that must propagate immediately, even when
            they would otherwise match ``retry_on`` (takes precedence).
        on_retry: Optional callback invoked as ``on_retry(attempt, exc)`` after a
            failed attempt that will be retried (e.g. to report task progress).
        sleep: Sleep function (injectable for tests).  Defaults to
            :func:`time.sleep`, resolved at call time so monkeypatching
            ``podcaster.retry.time.sleep`` takes effect.
        description: Human-readable task label used in log messages.

    Returns:
        Whatever ``fn`` returns on the first successful attempt.

    Raises:
        The last exception raised by ``fn`` once attempts are exhausted, or
        immediately for any ``give_up_on`` / non-``retry_on`` exception, or for
        ``KeyboardInterrupt`` / ``SystemExit``.
    """
    attempts = max(1, attempts)
    jitter = max(0.0, jitter)
    _sleep = sleep if sleep is not None else time.sleep
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except give_up_on:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except retry_on as exc:
            last_exc = exc
            if attempt >= attempts:
                logger.error(
                    "%s failed after %d attempt(s): %s", description, attempt, exc
                )
                raise
            delay = min(max_delay, base_delay * (backoff ** (attempt - 1)))
            if jitter:
                delay += random.uniform(0.0, jitter * delay)
            logger.warning(
                "%s failed on attempt %d/%d (%s); retrying in %.2fs",
                description, attempt, attempts, exc, delay,
            )
            if on_retry is not None:
                on_retry(attempt, exc)
            _sleep(delay)
    # Unreachable: the loop either returns or raises.  Kept for type-checkers.
    assert last_exc is not None  # pragma: no cover
    raise last_exc  # pragma: no cover
