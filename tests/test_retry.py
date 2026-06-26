"""Unit tests for the per-task retry helper (issue #483)."""

from __future__ import annotations

import pytest

from podcaster import retry
from podcaster.retry import retry_call


def _no_sleep(_seconds: float) -> None:
    return None


class TestRetryCall:
    def test_returns_on_first_success(self):
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        assert retry_call(fn, sleep=_no_sleep) == "ok"
        assert len(calls) == 1

    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "recovered"

        assert retry_call(fn, attempts=3, sleep=_no_sleep) == "recovered"
        assert calls["n"] == 2

    def test_raises_after_exhausting_attempts(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise RuntimeError("always fails")

        with pytest.raises(RuntimeError, match="always fails"):
            retry_call(fn, attempts=3, sleep=_no_sleep)
        assert calls["n"] == 3

    def test_attempts_floor_is_one(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise ValueError("boom")

        with pytest.raises(ValueError):
            retry_call(fn, attempts=0, sleep=_no_sleep)
        assert calls["n"] == 1

    def test_give_up_on_propagates_immediately(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise ValueError("non-retryable")

        with pytest.raises(ValueError):
            retry_call(
                fn,
                attempts=5,
                retry_on=(Exception,),
                give_up_on=(ValueError,),
                sleep=_no_sleep,
            )
        assert calls["n"] == 1

    def test_unlisted_exception_is_not_retried(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise KeyError("nope")

        with pytest.raises(KeyError):
            retry_call(fn, attempts=5, retry_on=(ValueError,), sleep=_no_sleep)
        assert calls["n"] == 1

    def test_base_exception_not_retried(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            retry_call(fn, attempts=5, sleep=_no_sleep)
        assert calls["n"] == 1

    def test_on_retry_callback_invoked(self):
        events: list[tuple[int, str]] = []

        def fn():
            raise RuntimeError("fail")

        def on_retry(attempt, exc):
            events.append((attempt, str(exc)))

        with pytest.raises(RuntimeError):
            retry_call(
                fn, attempts=3, on_retry=on_retry, sleep=_no_sleep
            )
        # Two retries before the third (final) attempt raises.
        assert events == [(1, "fail"), (2, "fail")]

    def test_exponential_backoff_delays(self):
        delays: list[float] = []

        def fn():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            retry_call(
                fn,
                attempts=4,
                base_delay=1.0,
                backoff=2.0,
                jitter=0.0,
                sleep=delays.append,
            )
        # base * backoff**(attempt-1) for attempts 1,2,3 (4th raises, no sleep).
        assert delays == [1.0, 2.0, 4.0]

    def test_max_delay_caps_backoff(self):
        delays: list[float] = []

        def fn():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            retry_call(
                fn,
                attempts=5,
                base_delay=10.0,
                backoff=10.0,
                max_delay=15.0,
                jitter=0.0,
                sleep=delays.append,
            )
        assert all(d <= 15.0 for d in delays)
        assert delays[-1] == 15.0

    def test_default_task_retries_env(self, monkeypatch):
        # DEFAULT_TASK_RETRIES is resolved at import; verify the env parsing helper.
        monkeypatch.setenv("PODCASTER_TASK_RETRIES", "7")
        assert retry._env_int("PODCASTER_TASK_RETRIES", 3) == 7
        monkeypatch.setenv("PODCASTER_TASK_RETRIES", "bogus")
        assert retry._env_int("PODCASTER_TASK_RETRIES", 3) == 3

    def test_default_sleep_honors_monkeypatched_time_sleep(self, monkeypatch):
        # The default sleep must be resolved at call time so patching
        # ``podcaster.retry.time.sleep`` actually takes effect (issue #483 review).
        slept: list[float] = []
        monkeypatch.setattr("podcaster.retry.time.sleep", lambda s: slept.append(s))

        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "ok"

        # No explicit ``sleep=`` — relies on the default.
        assert retry_call(fn, attempts=3, base_delay=0.01, jitter=0.0) == "ok"
        assert slept == [0.01]

    def test_keyboard_interrupt_propagates_even_when_in_retry_on(self):
        # KeyboardInterrupt/SystemExit must always propagate immediately, even
        # if a caller foolishly lists BaseException in retry_on.
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            retry_call(fn, attempts=5, retry_on=(BaseException,), sleep=_no_sleep)
        assert calls["n"] == 1  # not retried

    def test_negative_jitter_is_clamped(self):
        delays: list[float] = []

        def fn():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            retry_call(
                fn,
                attempts=3,
                base_delay=1.0,
                backoff=1.0,
                jitter=-5.0,
                sleep=delays.append,
            )
        # Negative jitter clamped to 0 → delay never drops below base_delay.
        assert all(d == 1.0 for d in delays)

    def test_jitter_never_exceeds_max_delay(self):
        delays: list[float] = []

        def fn():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            retry_call(
                fn,
                attempts=6,
                base_delay=10.0,
                backoff=10.0,
                max_delay=15.0,
                jitter=1.0,  # up to +100% jitter
                sleep=delays.append,
            )
        # Even with full jitter, no slept delay exceeds max_delay.
        assert all(d <= 15.0 for d in delays)
