"""Concurrent TTS generation pool (#478).

The production pipeline synthesizes every spoken segment of an episode through
the gated :func:`podcaster.tts.synthesize_turn`. Done sequentially, an
18-segment episode spends roughly ``18 * per_segment`` seconds waiting on the
Azure OpenAI ``/audio/speech`` endpoint. This module runs those calls through a
bounded-concurrency pool so the wall-clock TTS time drops to roughly ``1/N`` of
the sequential time (for ``N`` workers), while still:

* **Preserving gating** — synthesis is refused unless ``decision['allowed']`` is
  truthy, exactly like the sequential path. Concurrency never bypasses review.
* **Preserving order** — results are returned in plan order regardless of which
  worker finishes first, so callers (mixing, metadata) see the same sequence as
  before.
* **Respecting provider rate limits** — HTTP 429 / 5xx and transient network
  errors are retried with exponential backoff + jitter rather than failing the
  whole pipeline. The bounded semaphore is the primary backpressure mechanism.

The underlying :func:`podcaster.tts.synthesize_turn` is synchronous (it uses
``urllib``), so each call is dispatched to a worker thread via
:func:`asyncio.to_thread`; the ``asyncio`` layer only schedules and bounds the
concurrency.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError

from podcaster.tts import (
    TokenProvider,
    Transport,
    TtsConfig,
    VoiceTurn,
    synthesize_turn,
)

#: Default number of TTS calls allowed in flight at once. Sized for the Azure
#: OpenAI speech deployment's per-minute limits; tune via env in production.
DEFAULT_CONCURRENCY = 6
#: Retries *after* the first attempt for a single segment on transient/rate-limit
#: failures before the error propagates and fails the job.
DEFAULT_MAX_RETRIES = 4
DEFAULT_BACKOFF_BASE_SECONDS = 0.5
DEFAULT_BACKOFF_MAX_SECONDS = 20.0
DEFAULT_BACKOFF_JITTER = 0.25

#: Signature of an awaitable sleep used for backoff. Injected in tests so retry
#: behaviour can be exercised without real delays.
Sleeper = Callable[[float], Awaitable[None]]
#: Signature matching :func:`podcaster.tts.synthesize_turn`. Injected in tests.
SynthesizeFn = Callable[..., bytes]


@dataclass(frozen=True)
class TtsPoolConfig:
    """Concurrency and retry policy for the TTS generation pool."""

    concurrency: int = DEFAULT_CONCURRENCY
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
    backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS
    backoff_jitter: float = DEFAULT_BACKOFF_JITTER

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.backoff_base_seconds < 0 or self.backoff_max_seconds < 0:
            raise ValueError("backoff seconds must be non-negative")
        if not 0.0 <= self.backoff_jitter <= 1.0:
            raise ValueError("backoff_jitter must be between 0 and 1")

    def backoff_delay(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """Exponential backoff (seconds) for the ``attempt``-th retry (1-based).

        Caps at ``backoff_max_seconds`` and adds up to ``backoff_jitter`` of the
        delay as positive jitter to avoid thundering-herd retries.
        """

        if attempt < 1:
            return 0.0
        raw = self.backoff_base_seconds * (2 ** (attempt - 1))
        capped = min(raw, self.backoff_max_seconds)
        if self.backoff_jitter:
            jitter_rng = rng or random
            capped += capped * self.backoff_jitter * jitter_rng.random()
        return capped


def load_tts_pool_config(env: Mapping[str, str] | None = None) -> TtsPoolConfig:
    """Read pool settings from the environment with safe production defaults.

    Setting ``PODCASTER_TTS_CONCURRENCY=1`` restores fully sequential synthesis.
    """

    if env is None:
        import os

        env = os.environ

    return TtsPoolConfig(
        concurrency=_int_env(env, "PODCASTER_TTS_CONCURRENCY", DEFAULT_CONCURRENCY, minimum=1),
        max_retries=_int_env(env, "PODCASTER_TTS_MAX_RETRIES", DEFAULT_MAX_RETRIES, minimum=0),
        backoff_base_seconds=_float_env(
            env, "PODCASTER_TTS_BACKOFF_BASE_SECONDS", DEFAULT_BACKOFF_BASE_SECONDS
        ),
        backoff_max_seconds=_float_env(
            env, "PODCASTER_TTS_BACKOFF_MAX_SECONDS", DEFAULT_BACKOFF_MAX_SECONDS
        ),
        backoff_jitter=_float_env(
            env, "PODCASTER_TTS_BACKOFF_JITTER", DEFAULT_BACKOFF_JITTER
        ),
    )


def is_rate_limited(exc: BaseException) -> bool:
    """True when ``exc`` represents an HTTP 429 (Too Many Requests)."""

    return isinstance(exc, HTTPError) and exc.code == 429


def is_retryable(exc: BaseException) -> bool:
    """True when a segment failure is worth retrying with backoff.

    Covers provider rate limiting (429), server-side errors (5xx), and transient
    network failures. Client errors (4xx other than 429) and gating errors are
    *not* retried — they will not succeed on a retry.
    """

    if isinstance(exc, HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    if isinstance(exc, (URLError, socket.timeout, TimeoutError, ConnectionError)):
        return True
    return False


def synthesize_plan_concurrent(
    plan: Sequence[VoiceTurn],
    config: TtsConfig,
    decision: Mapping[str, object],
    *,
    pool: TtsPoolConfig | None = None,
    token_provider: TokenProvider | None = None,
    transport: Transport | None = None,
    synthesize: SynthesizeFn = synthesize_turn,
    sleeper: Sleeper | None = None,
) -> list[bytes]:
    """Synthesize ``plan`` concurrently, returning audio bytes in plan order.

    Fails closed like :func:`podcaster.tts.synthesize_two_voice`: if the gating
    decision does not allow synthesis the call raises :class:`PermissionError`
    before any network access. Falls back to a simple sequential loop when the
    pool is sized to one worker or there is a single turn, so the concurrent
    machinery (and event loop) is only spun up when it can help.
    """

    if not decision.get("allowed"):
        blocked_by = decision.get("blocked_by") or ["not_authorized"]
        raise PermissionError(
            f"tts synthesis is blocked: {', '.join(map(str, blocked_by))}"
        )
    if not plan:
        raise ValueError("voice plan is empty")

    pool = pool or load_tts_pool_config()
    turns = list(plan)

    # Fast path: a single worker / single turn with no retry policy can run as a
    # plain sequential loop without spinning up an event loop. When retries are
    # configured we must NOT bypass the pool, otherwise concurrency=1 would
    # silently drop the rate-limit/backoff handling the pool is meant to own.
    if (pool.concurrency <= 1 or len(turns) <= 1) and pool.max_retries == 0:
        return [
            synthesize(turn, config, token_provider=token_provider, transport=transport)
            for turn in turns
        ]

    coro = _synthesize_all(
        turns,
        config,
        pool=pool,
        token_provider=token_provider,
        transport=transport,
        synthesize=synthesize,
        sleeper=sleeper or asyncio.sleep,
    )
    return _run_coro(coro)


async def _synthesize_all(
    turns: list[VoiceTurn],
    config: TtsConfig,
    *,
    pool: TtsPoolConfig,
    token_provider: TokenProvider | None,
    transport: Transport | None,
    synthesize: SynthesizeFn,
    sleeper: Sleeper,
) -> list[bytes]:
    semaphore = asyncio.Semaphore(pool.concurrency)
    results: list[bytes | None] = [None] * len(turns)

    async def worker(index: int, turn: VoiceTurn) -> None:
        async with semaphore:
            results[index] = await _synthesize_one(
                index,
                turn,
                config,
                pool=pool,
                token_provider=token_provider,
                transport=transport,
                synthesize=synthesize,
                sleeper=sleeper,
            )

    logging.info(
        "tts pool synthesizing segments=%s concurrency=%s max_retries=%s",
        len(turns),
        pool.concurrency,
        pool.max_retries,
    )
    await asyncio.gather(*(worker(i, turn) for i, turn in enumerate(turns)))
    # Every slot must be populated once gather completes without raising; a None
    # here would mean a worker silently skipped its segment and would break the
    # order-preserving contract, so fail loudly instead of dropping entries.
    missing = [i for i, audio in enumerate(results) if audio is None]
    if missing:
        raise RuntimeError(f"tts pool left segments unsynthesized: {missing}")
    return [audio for audio in results if audio is not None]


async def _synthesize_one(
    index: int,
    turn: VoiceTurn,
    config: TtsConfig,
    *,
    pool: TtsPoolConfig,
    token_provider: TokenProvider | None,
    transport: Transport | None,
    synthesize: SynthesizeFn,
    sleeper: Sleeper,
) -> bytes:
    attempt = 0
    while True:
        try:
            return await asyncio.to_thread(
                synthesize,
                turn,
                config,
                token_provider=token_provider,
                transport=transport,
            )
        except Exception as exc:  # noqa: BLE001 - retry transient/rate-limit failures
            attempt += 1
            if attempt > pool.max_retries or not is_retryable(exc):
                raise
            delay = pool.backoff_delay(attempt)
            logging.warning(
                "tts pool segment=%s attempt=%s failed (%s%s); backing off %.2fs",
                index,
                attempt,
                type(exc).__name__,
                " rate-limited" if is_rate_limited(exc) else "",
                delay,
            )
            await sleeper(delay)


def _run_coro(coro: Awaitable[list[bytes]]) -> list[bytes]:
    """Run ``coro`` to completion from synchronous code.

    Uses :func:`asyncio.run` when no event loop is running. If a loop is already
    running on this thread (e.g. invoked from an async request handler), the
    coroutine is run on a fresh loop in a dedicated thread so we never try to
    nest event loops.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]

    import threading

    result: dict[str, object] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)  # type: ignore[arg-type]
        except BaseException as exc:  # noqa: BLE001 - re-raise on the caller thread
            result["error"] = exc

    thread = threading.Thread(target=_runner, name="tts-pool", daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return result["value"]  # type: ignore[return-value]


def _int_env(env: Mapping[str, str], key: str, default: int, *, minimum: int) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        logging.warning("invalid %s=%r; using default %s", key, raw, default)
        return default
    if value < minimum:
        logging.warning("%s=%s below minimum %s; clamping", key, value, minimum)
        return minimum
    return value


def _float_env(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except ValueError:
        logging.warning("invalid %s=%r; using default %s", key, raw, default)
        return default
    if value < 0:
        logging.warning("%s=%s is negative; using default %s", key, value, default)
        return default
    return value
