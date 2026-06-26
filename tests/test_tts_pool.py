from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcaster.tts import (  # noqa: E402
    build_voice_plan,
    load_tts_config,
    synthesis_decision,
    synthesize_two_voice,
)
from podcaster.tts_pool import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    TtsPoolConfig,
    is_rate_limited,
    is_retryable,
    load_tts_pool_config,
    synthesize_plan_concurrent,
)


def _production_config():
    return load_tts_config(
        {
            "AZURE_OPENAI_ENDPOINT": "https://podcaster-openai.openai.azure.com/",
            "AZURE_OPENAI_TTS_DEPLOYMENT": "tts",
            "AZURE_OPENAI_CHAT_DEPLOYMENT": "chat",
            "AZURE_OPENAI_TTS_VOICE_HOST_A": "fable",
            "AZURE_OPENAI_TTS_VOICE_HOST_B": "alloy",
            "AZURE_OPENAI_AUTH_MODE": "managed_identity",
        }
    )


def _plan(n: int):
    config = _production_config()
    segments = [("host_a" if i % 2 == 0 else "host_b", f"line {i}") for i in range(n)]
    return config, build_voice_plan(segments, config)


def _allowed_decision(config):
    return synthesis_decision(config, dry_run=False, review_approved=True)


async def _no_sleep(_delay: float) -> None:
    return None


# --- config -----------------------------------------------------------------


def test_pool_config_defaults_and_validation():
    cfg = TtsPoolConfig()
    assert cfg.concurrency == DEFAULT_CONCURRENCY
    with pytest.raises(ValueError):
        TtsPoolConfig(concurrency=0)
    with pytest.raises(ValueError):
        TtsPoolConfig(max_retries=-1)
    with pytest.raises(ValueError):
        TtsPoolConfig(backoff_jitter=2.0)


def test_load_pool_config_reads_env_and_clamps():
    cfg = load_tts_pool_config(
        {
            "PODCASTER_TTS_CONCURRENCY": "8",
            "PODCASTER_TTS_MAX_RETRIES": "2",
            "PODCASTER_TTS_BACKOFF_BASE_SECONDS": "1.5",
        }
    )
    assert cfg.concurrency == 8
    assert cfg.max_retries == 2
    assert cfg.backoff_base_seconds == 1.5

    clamped = load_tts_pool_config({"PODCASTER_TTS_CONCURRENCY": "0"})
    assert clamped.concurrency == 1

    fallback = load_tts_pool_config({"PODCASTER_TTS_CONCURRENCY": "not-a-number"})
    assert fallback.concurrency == DEFAULT_CONCURRENCY


def test_backoff_delay_grows_and_caps():
    cfg = TtsPoolConfig(backoff_base_seconds=1.0, backoff_max_seconds=4.0, backoff_jitter=0.0)
    assert cfg.backoff_delay(1) == 1.0
    assert cfg.backoff_delay(2) == 2.0
    assert cfg.backoff_delay(3) == 4.0
    assert cfg.backoff_delay(4) == 4.0  # capped


# --- error classification ---------------------------------------------------


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://x", code, "msg", hdrs=None, fp=None)


def test_error_classification():
    assert is_rate_limited(_http_error(429))
    assert not is_rate_limited(_http_error(500))
    assert is_retryable(_http_error(429))
    assert is_retryable(_http_error(503))
    assert is_retryable(URLError("boom"))
    assert is_retryable(TimeoutError())
    assert not is_retryable(_http_error(400))
    assert not is_retryable(ValueError("bad text"))


# --- concurrency / ordering -------------------------------------------------


def test_concurrent_preserves_plan_order_despite_out_of_order_completion():
    config, plan = _plan(6)
    decision = _allowed_decision(config)

    def synth(turn, cfg, *, token_provider=None, transport=None):
        # Later segments return faster, so completion order != plan order.
        idx = int(turn.text.split()[-1])
        time.sleep((6 - idx) * 0.005)
        return f"audio-{idx}".encode()

    result = synthesize_plan_concurrent(
        plan,
        config,
        decision,
        pool=TtsPoolConfig(concurrency=6),
        synthesize=synth,
    )
    assert result == [f"audio-{i}".encode() for i in range(6)]


def test_concurrency_is_bounded():
    config, plan = _plan(10)
    decision = _allowed_decision(config)
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def synth(turn, cfg, *, token_provider=None, transport=None):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.01)
        with lock:
            state["active"] -= 1
        return b"audio"

    synthesize_plan_concurrent(
        plan,
        config,
        decision,
        pool=TtsPoolConfig(concurrency=3),
        synthesize=synth,
    )
    assert state["peak"] <= 3
    assert state["peak"] >= 2  # actually ran in parallel


def test_concurrent_is_faster_than_sequential():
    config, plan = _plan(8)
    decision = _allowed_decision(config)

    def synth(turn, cfg, *, token_provider=None, transport=None):
        time.sleep(0.05)
        return b"audio"

    start = time.monotonic()
    synthesize_plan_concurrent(
        plan, config, decision, pool=TtsPoolConfig(concurrency=8), synthesize=synth
    )
    elapsed = time.monotonic() - start
    # 8 * 0.05s = 0.4s sequential; with 8 workers it should be well under half.
    assert elapsed < 0.2


# --- retry / backoff --------------------------------------------------------


def test_retries_rate_limited_segment_then_succeeds():
    config, plan = _plan(2)
    decision = _allowed_decision(config)
    attempts = {"host_a": 0, "host_b": 0}
    slept: list[float] = []

    async def record_sleep(delay: float) -> None:
        slept.append(delay)

    def synth(turn, cfg, *, token_provider=None, transport=None):
        attempts[turn.role] += 1
        if turn.role == "host_a" and attempts["host_a"] == 1:
            raise _http_error(429)
        return b"audio"

    result = synthesize_plan_concurrent(
        plan,
        config,
        decision,
        pool=TtsPoolConfig(concurrency=2, max_retries=3, backoff_jitter=0.0),
        synthesize=synth,
        sleeper=record_sleep,
    )
    assert result == [b"audio", b"audio"]
    assert attempts["host_a"] == 2
    assert slept  # backed off once


def test_gives_up_after_max_retries_and_raises():
    config, plan = _plan(2)
    decision = _allowed_decision(config)

    def synth(turn, cfg, *, token_provider=None, transport=None):
        raise _http_error(429)

    with pytest.raises(HTTPError):
        synthesize_plan_concurrent(
            plan,
            config,
            decision,
            pool=TtsPoolConfig(concurrency=2, max_retries=2, backoff_jitter=0.0),
            synthesize=synth,
            sleeper=_no_sleep,
        )


def test_non_retryable_error_propagates_immediately():
    config, plan = _plan(2)
    decision = _allowed_decision(config)
    calls = {"n": 0}

    def synth(turn, cfg, *, token_provider=None, transport=None):
        calls["n"] += 1
        raise _http_error(400)

    with pytest.raises(HTTPError):
        synthesize_plan_concurrent(
            plan,
            config,
            decision,
            pool=TtsPoolConfig(concurrency=2, max_retries=5),
            synthesize=synth,
            sleeper=_no_sleep,
        )
    # No retries for a 4xx client error.
    assert calls["n"] <= 2


# --- gating -----------------------------------------------------------------


def test_fails_closed_when_decision_blocked():
    config, plan = _plan(3)
    blocked = synthesis_decision(config, dry_run=True, review_approved=False)

    def synth(turn, cfg, *, token_provider=None, transport=None):
        pytest.fail("must not synthesize when blocked")

    with pytest.raises(PermissionError):
        synthesize_plan_concurrent(plan, config, blocked, synthesize=synth)


def test_empty_plan_raises():
    config, _ = _plan(1)
    with pytest.raises(ValueError):
        synthesize_plan_concurrent([], config, _allowed_decision(config))


# --- integration with synthesize_two_voice ----------------------------------


def test_synthesize_two_voice_uses_pool_when_supplied():
    config, plan = _plan(4)
    decision = _allowed_decision(config)
    seen = []
    lock = threading.Lock()

    def fake_transport(request):
        with lock:
            seen.append(1)
        return b"audio"

    result = synthesize_two_voice(
        plan,
        config,
        decision,
        transport=fake_transport,
        token_provider=lambda scope: "t",
        pool=TtsPoolConfig(concurrency=4),
    )
    assert result == [b"audio"] * 4
    assert len(seen) == 4


def test_synthesize_two_voice_sequential_without_pool():
    config, plan = _plan(3)
    decision = _allowed_decision(config)
    order = []

    def fake_transport(request):
        order.append(len(order))
        return b"audio"

    result = synthesize_two_voice(
        plan,
        config,
        decision,
        transport=fake_transport,
        token_provider=lambda scope: "t",
    )
    assert result == [b"audio"] * 3
    assert order == [0, 1, 2]
