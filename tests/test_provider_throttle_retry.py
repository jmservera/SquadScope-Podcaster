"""Bounded 429/503 retry for the chat provider and its retriable HTTP mapping."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest

from podcaster import script_gen
from podcaster.costs import monthly_ledger_path
from podcaster.jobs import ScriptGenerationFailed, build_job_id, run_generation_job
from podcaster.script_gen import (
    MAX_PROVIDER_RETRIES,
    MAX_PROVIDER_RETRY_WAIT_SECONDS,
    ProviderRetriableError,
    _request_dialogue,
)
from podcaster.storage import LocalStorageBackend
from tests.test_jobs import VALID_ARTICLE_CONTENT

OK_BODY = json.dumps(
    {"choices": [{"message": {"content": " hi "}, "finish_reason": "stop"}]}
).encode()


def _throttle(code: int = 429, retry_after: str | None = "30") -> HTTPError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return HTTPError("https://x.invalid", code, "throttled PRIVATE_BODY", headers, None)


def _scripted(outcomes: list):
    calls: list[object] = []

    def transport(request):
        calls.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return transport, calls


def _call(transport):
    return _request_dialogue(
        [{"role": "user", "content": "x"}], url="https://x.invalid", token="t", transport=transport
    )


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(script_gen, "_retry_sleep", recorded.append)
    return recorded


@pytest.mark.parametrize("code", [429, 503])
def test_throttle_then_success_honors_retry_after(sleeps, code) -> None:
    transport, calls = _scripted([_throttle(code, "30"), OK_BODY])
    assert _call(transport) == "hi"
    assert sleeps == [30]
    assert len(calls) == 2


def test_retry_after_is_capped_and_missing_header_uses_default(sleeps) -> None:
    transport, _ = _scripted([_throttle(429, "9999"), _throttle(429, None), OK_BODY])
    with pytest.raises(ProviderRetriableError):
        _call(transport)
    # 65s consumes the whole wait budget, so the second throttle is not slept on.
    assert sleeps == [MAX_PROVIDER_RETRY_WAIT_SECONDS]


def test_exhaustion_raises_retriable_error_within_bounds(sleeps) -> None:
    transport, calls = _scripted([_throttle(429, "10") for _ in range(10)])
    with pytest.raises(ProviderRetriableError) as caught:
        _call(transport)
    assert caught.value.retry_after_seconds == 10
    assert len(sleeps) == MAX_PROVIDER_RETRIES
    assert sum(sleeps) <= MAX_PROVIDER_RETRY_WAIT_SECONDS
    assert len(calls) == MAX_PROVIDER_RETRIES + 1
    assert "PRIVATE_BODY" not in str(caught.value)


def test_wait_budget_is_shared_across_calls_of_one_generation(sleeps) -> None:
    generation = script_gen._GenerationDiagnostics.__new__(script_gen._GenerationDiagnostics)
    generation.provider_retries = 0
    generation.provider_wait_seconds = 0.0
    generation.calls_used = 0
    generation.call_budget = 20
    generation.observe = lambda *a, **k: None  # type: ignore[method-assign]
    generation.word_range = None
    generation.previous_script = ""
    transport, _ = _scripted(
        [
            _throttle(429, "30"),
            OK_BODY,
            _throttle(429, "30"),
            OK_BODY,
            _throttle(429, "30"),
            OK_BODY,
        ]
    )
    for expected_sleeps in ([30], [30, 30]):
        _request_dialogue(
            [{"role": "user", "content": "x"}],
            url="https://x.invalid",
            token="t",
            transport=transport,
            generation=generation,
        )
        assert sleeps == expected_sleeps
    with pytest.raises(ProviderRetriableError):
        _request_dialogue(
            [{"role": "user", "content": "x"}],
            url="https://x.invalid",
            token="t",
            transport=transport,
            generation=generation,
        )
    assert sleeps == [30, 30]


def test_other_http_errors_are_not_retried(sleeps) -> None:
    transport, calls = _scripted([_throttle(500, "5")])
    with pytest.raises(HTTPError):
        _call(transport)
    assert sleeps == [] and len(calls) == 1


@pytest.mark.parametrize("cleanup_ok", [True, False])
def test_exhausted_throttle_releases_reservation_and_maps_to_503(
    monkeypatch, tmp_path: Path, sleeps, cleanup_ok
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")
    monkeypatch.setattr(
        script_gen.ManagedIdentityTokenCredential, "get_token", staticmethod(lambda scope: "t")
    )

    def always_429(request):
        raise _throttle(429, "20")

    monkeypatch.setattr(script_gen, "_default_transport", always_429)
    storage = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    payload = {
        "week": "2026-W24",
        "article_url": "https://example.com/ai-article",
        "article_title": "AI Revolution",
        "article_content": VALID_ARTICLE_CONTENT,
    }
    now = datetime(2026, 6, 12, 10, 0, 0, tzinfo=timezone.utc)
    job_id = build_job_id(payload)
    if not cleanup_ok:
        monkeypatch.setattr("podcaster.jobs._release_failed_reservation", lambda *a, **k: False)
    with pytest.raises(ScriptGenerationFailed) as caught:
        run_generation_job(payload, storage=storage, now=now)
    if not cleanup_ok:
        assert caught.value.retry_after_seconds is None
        return
    assert caught.value.retry_after_seconds == 20
    assert not caught.value.quality_rejected
    assert storage.get_bytes(f"jobs/{job_id}/inputs/replay.json") is None
    ledger = json.loads(storage.get_bytes(monthly_ledger_path("2026-06")) or b"{}")
    assert all(e.get("job_id") != job_id for e in ledger.get("episodes", []))
    assert sum(sleeps) <= MAX_PROVIDER_RETRY_WAIT_SECONDS
