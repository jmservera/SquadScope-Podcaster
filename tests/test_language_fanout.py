"""Tests for the language fan-out job architecture (#439)."""

from __future__ import annotations

import threading

import pytest

from podcaster.language_fanout import (
    FanOutResult,
    LanguageBranchResult,
    NonRetryableError,
    RetryPolicy,
    language_artifact_path,
    plan_language_branches,
    run_language_fanout,
    shared_artifact_path,
)


# --- artifact layout ----------------------------------------------------------


def test_shared_artifact_path():
    assert shared_artifact_path("job1", "brief.json") == "jobs/job1/brief.json"
    assert shared_artifact_path("job1", "/recordings/seg0.webm") == "jobs/job1/recordings/seg0.webm"


def test_language_artifact_path_nests_non_default():
    assert language_artifact_path("job1", "es", "script.txt") == "jobs/job1/es/script.txt"
    assert language_artifact_path("job1", "fr-FR", "audio.mp3") == "jobs/job1/fr/audio.mp3"


def test_language_artifact_path_english_stays_flat_by_default():
    assert language_artifact_path("job1", "en", "script.txt") == "jobs/job1/script.txt"


def test_language_artifact_path_can_nest_all_languages():
    assert (
        language_artifact_path("job1", "en", "script.txt", flat_default_language=None)
        == "jobs/job1/en/script.txt"
    )


# --- plan_language_branches ---------------------------------------------------


def test_plan_default_language_first():
    assert plan_language_branches(["es", "fr"]) == ["en", "es", "fr"]


def test_plan_dedupes_and_normalizes_locales():
    assert plan_language_branches(["es-419", "es", "fr-FR", "en-US"]) == ["en", "es", "fr"]


def test_plan_accepts_mapping():
    assert plan_language_branches({"es": object(), "fr": object()}) == ["en", "es", "fr"]


def test_plan_handles_none():
    assert plan_language_branches(None) == ["en"]


def test_plan_moves_default_to_front_if_present():
    assert plan_language_branches(["es", "en", "fr"]) == ["en", "es", "fr"]


# --- run_language_fanout: happy path ------------------------------------------


def test_fanout_gathers_source_once():
    calls = {"gather": 0}

    def gather():
        calls["gather"] += 1
        return {"brief": "shared"}

    def process(language, shared):
        return f"{language}:{shared['brief']}"

    result = run_language_fanout(["es", "fr"], gather_source=gather, process_language=process)
    assert calls["gather"] == 1
    assert isinstance(result, FanOutResult)
    assert result.all_succeeded
    assert set(result.branches) == {"en", "es", "fr"}
    assert result.branches["es"].payload == "es:shared"
    assert result.branches["es"].attempts == 1


# --- independent retry / failure isolation ------------------------------------


def test_one_language_failure_does_not_block_others():
    def gather():
        return "src"

    def process(language, shared):
        if language == "fr":
            raise RuntimeError("fr boom")
        return f"{language}-ok"

    result = run_language_fanout(
        ["es", "fr"],
        gather_source=gather,
        process_language=process,
        retry=RetryPolicy(max_attempts=2),
    )
    assert result.succeeded_languages == ["en", "es"]
    assert result.failed_languages == ["fr"]
    assert result.branches["fr"].attempts == 2
    assert "fr boom" in result.branches["fr"].error
    assert not result.all_succeeded


def test_retry_eventually_succeeds():
    attempts = {"es": 0}

    def gather():
        return "src"

    def process(language, shared):
        if language == "es":
            attempts["es"] += 1
            if attempts["es"] < 3:
                raise RuntimeError("transient")
        return "ok"

    result = run_language_fanout(
        ["es"],
        gather_source=gather,
        process_language=process,
        retry=RetryPolicy(max_attempts=3),
    )
    assert result.branches["es"].succeeded
    assert result.branches["es"].attempts == 3


def test_non_retryable_error_stops_immediately():
    attempts = {"es": 0}

    def gather():
        return "src"

    def process(language, shared):
        if language == "es":
            attempts["es"] += 1
            raise NonRetryableError("bad config")
        return "ok"

    result = run_language_fanout(
        ["es"],
        gather_source=gather,
        process_language=process,
        retry=RetryPolicy(max_attempts=5),
    )
    assert not result.branches["es"].succeeded
    assert attempts["es"] == 1
    assert "bad config" in result.branches["es"].error


def test_shared_gather_failure_is_fatal():
    def gather():
        raise RuntimeError("source gather failed")

    with pytest.raises(RuntimeError, match="source gather failed"):
        run_language_fanout(
            ["es"], gather_source=gather, process_language=lambda lang, s: None
        )


# --- parallelism --------------------------------------------------------------


def test_parallel_execution_runs_all_branches():
    seen = []
    lock = threading.Lock()

    def gather():
        return "src"

    def process(language, shared):
        with lock:
            seen.append(language)
        return language

    result = run_language_fanout(
        ["es", "fr"],
        gather_source=gather,
        process_language=process,
        max_workers=3,
    )
    assert set(seen) == {"en", "es", "fr"}
    assert list(result.branches) == ["en", "es", "fr"]  # planned order preserved
    assert result.all_succeeded


# --- misc ---------------------------------------------------------------------


def test_retry_policy_validates_attempts():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)


def test_branch_result_repr_fields():
    r = LanguageBranchResult(language="es", status="succeeded", attempts=1)
    assert r.succeeded and r.language == "es"
