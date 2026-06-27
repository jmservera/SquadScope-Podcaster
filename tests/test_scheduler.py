"""Tests for podcaster.scheduler — DAG generation-pipeline scheduler (#477).

Acceptance criteria exercised:

* Independent tasks run concurrently; dependent tasks wait correctly.
* Resume after failure re-runs only incomplete tasks (checkpointing).
* Concurrency caps are configurable per stage.
"""

from __future__ import annotations

import threading
import time

import pytest

from podcaster.scheduler import (
    DEFAULT_STAGE_CONCURRENCY,
    CyclicDependencyError,
    DuplicateTaskError,
    InMemoryCheckpoint,
    StorageCheckpoint,
    TaskResult,
    TaskSpec,
    TaskState,
    UnknownDependencyError,
    build_generation_dag,
    run_dag,
    validate_graph,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _spec(task_id, stage, run, deps=()):  # convenience
    return TaskSpec(id=task_id, stage=stage, run=run, deps=frozenset(deps))


def _record_order(log, name, hold=0.0):
    def _run():
        log.append(("start", name))
        if hold:
            time.sleep(hold)
        log.append(("end", name))
        return name

    return _run


# --------------------------------------------------------------------------- #
# Graph validation
# --------------------------------------------------------------------------- #


def test_validate_graph_rejects_unknown_dependency():
    tasks = [_spec("a", "s", lambda: None, deps=("missing",))]
    with pytest.raises(UnknownDependencyError):
        validate_graph(tasks)


def test_validate_graph_rejects_duplicate_ids():
    tasks = [_spec("a", "s", lambda: None), _spec("a", "s", lambda: None)]
    with pytest.raises(DuplicateTaskError):
        validate_graph(tasks)


def test_validate_graph_rejects_self_cycle():
    tasks = [_spec("a", "s", lambda: None, deps=("a",))]
    with pytest.raises(CyclicDependencyError):
        validate_graph(tasks)


def test_validate_graph_rejects_multi_node_cycle():
    tasks = [
        _spec("a", "s", lambda: None, deps=("c",)),
        _spec("b", "s", lambda: None, deps=("a",)),
        _spec("c", "s", lambda: None, deps=("b",)),
    ]
    with pytest.raises(CyclicDependencyError):
        validate_graph(tasks)


def test_validate_graph_accepts_diamond():
    tasks = [
        _spec("a", "s", lambda: None),
        _spec("b", "s", lambda: None, deps=("a",)),
        _spec("c", "s", lambda: None, deps=("a",)),
        _spec("d", "s", lambda: None, deps=("b", "c")),
    ]
    index = validate_graph(tasks)
    assert set(index) == {"a", "b", "c", "d"}


# --------------------------------------------------------------------------- #
# Ordering / dependency correctness
# --------------------------------------------------------------------------- #


def test_dependent_tasks_wait_for_dependencies():
    log: list[tuple[str, str]] = []
    tasks = [
        _spec("a", "s", _record_order(log, "a", hold=0.02)),
        _spec("b", "s", _record_order(log, "b"), deps=("a",)),
        _spec("c", "s", _record_order(log, "c"), deps=("b",)),
    ]
    result = run_dag(tasks, stage_concurrency={"s": 4})
    assert result.succeeded
    # b must start only after a ends; c only after b ends.
    assert log.index(("start", "b")) > log.index(("end", "a"))
    assert log.index(("start", "c")) > log.index(("end", "b"))


def test_independent_tasks_run_concurrently():
    barrier = threading.Barrier(3, timeout=5)

    def _await():
        # All three must reach the barrier simultaneously, proving concurrency.
        barrier.wait()
        return "ok"

    tasks = [_spec(f"t{i}", "s", _await) for i in range(3)]
    result = run_dag(tasks, stage_concurrency={"s": 3})
    assert result.succeeded
    assert all(r.state == TaskState.COMPLETED for r in result.tasks.values())


def test_results_capture_return_values():
    tasks = [
        _spec("a", "s", lambda: 1),
        _spec("b", "s", lambda: 2, deps=("a",)),
    ]
    result = run_dag(tasks)
    assert result.tasks["a"].result == 1
    assert result.tasks["b"].result == 2


# --------------------------------------------------------------------------- #
# Per-stage concurrency caps
# --------------------------------------------------------------------------- #


def test_stage_concurrency_cap_is_respected():
    lock = threading.Lock()
    state = {"in_flight": 0, "peak": 0}

    def _work():
        with lock:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
        time.sleep(0.02)
        with lock:
            state["in_flight"] -= 1
        return None

    tasks = [_spec(f"t{i}", "record", _work) for i in range(6)]
    result = run_dag(tasks, stage_concurrency={"record": 2})
    assert result.succeeded
    assert state["peak"] == 2  # never exceeded the cap


def test_different_stages_have_independent_caps():
    lock = threading.Lock()
    peak = {"a": 0, "b": 0}
    cur = {"a": 0, "b": 0}

    def _work(stage):
        def _run():
            with lock:
                cur[stage] += 1
                peak[stage] = max(peak[stage], cur[stage])
            time.sleep(0.02)
            with lock:
                cur[stage] -= 1

        return _run

    tasks = [_spec(f"a{i}", "a", _work("a")) for i in range(4)]
    tasks += [_spec(f"b{i}", "b", _work("b")) for i in range(4)]
    result = run_dag(tasks, stage_concurrency={"a": 1, "b": 4})
    assert result.succeeded
    assert peak["a"] == 1
    assert peak["b"] == 4


def test_default_concurrency_used_for_unlisted_stage():
    lock = threading.Lock()
    state = {"cur": 0, "peak": 0}

    def _work():
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        time.sleep(0.02)
        with lock:
            state["cur"] -= 1

    tasks = [_spec(f"t{i}", "custom", _work) for i in range(4)]
    result = run_dag(tasks, stage_concurrency={}, default_concurrency=1)
    assert result.succeeded
    assert state["peak"] == 1


# --------------------------------------------------------------------------- #
# Failure isolation
# --------------------------------------------------------------------------- #


def test_failure_blocks_only_dependents():
    ran: list[str] = []

    def _ok(name):
        def _run():
            ran.append(name)
            return name

        return _run

    def _boom():
        raise RuntimeError("kaboom")

    tasks = [
        _spec("root", "s", _ok("root")),
        _spec("fail", "s", _boom, deps=("root",)),
        _spec("downstream", "s", _ok("downstream"), deps=("fail",)),
        _spec("independent", "s", _ok("independent"), deps=("root",)),
    ]
    result = run_dag(tasks)
    assert not result.succeeded
    assert result.tasks["fail"].state == TaskState.FAILED
    # Error records only the exception type (no message), so secrets in
    # exception text can never leak into results/logs.
    assert result.tasks["fail"].error == "RuntimeError"
    assert "kaboom" not in (result.tasks["fail"].error or "")
    assert result.tasks["downstream"].state == TaskState.BLOCKED
    # The independent branch still ran to completion.
    assert result.tasks["independent"].state == TaskState.COMPLETED
    assert "independent" in ran
    assert "downstream" not in ran


def test_transitive_blocking():
    def _boom():
        raise ValueError("x")

    tasks = [
        _spec("a", "s", _boom),
        _spec("b", "s", lambda: None, deps=("a",)),
        _spec("c", "s", lambda: None, deps=("b",)),
    ]
    result = run_dag(tasks)
    assert result.tasks["a"].state == TaskState.FAILED
    assert result.tasks["b"].state == TaskState.BLOCKED
    assert result.tasks["c"].state == TaskState.BLOCKED


# --------------------------------------------------------------------------- #
# Resume / checkpointing
# --------------------------------------------------------------------------- #


def test_resume_skips_checkpointed_tasks():
    ran: list[str] = []

    def _track(name):
        def _run():
            ran.append(name)
            return name

        return _run

    checkpoint = InMemoryCheckpoint(completed={"a"})
    tasks = [
        _spec("a", "s", _track("a")),
        _spec("b", "s", _track("b"), deps=("a",)),
    ]
    result = run_dag(tasks, checkpoint=checkpoint)
    assert result.succeeded
    assert "a" not in ran  # skipped via checkpoint
    assert "b" in ran
    assert result.tasks["a"].state == TaskState.SKIPPED
    assert result.tasks["a"].resumed is True
    assert result.tasks["b"].state == TaskState.COMPLETED


def test_failed_run_then_resume_runs_only_incomplete():
    ran_first: list[str] = []

    def _track(log, name, fail=False):
        def _run():
            log.append(name)
            if fail:
                raise RuntimeError("boom")
            return name

        return _run

    checkpoint = InMemoryCheckpoint()

    # First run: "b" fails, so "c" is blocked; "a" completes and is checkpointed.
    tasks_run1 = [
        _spec("a", "s", _track(ran_first, "a")),
        _spec("b", "s", _track(ran_first, "b", fail=True), deps=("a",)),
        _spec("c", "s", _track(ran_first, "c"), deps=("b",)),
    ]
    result1 = run_dag(tasks_run1, checkpoint=checkpoint)
    assert result1.tasks["a"].state == TaskState.COMPLETED
    assert result1.tasks["b"].state == TaskState.FAILED
    assert result1.tasks["c"].state == TaskState.BLOCKED
    assert checkpoint.completed() == {"a"}

    # Second run: "b" now succeeds. "a" must NOT re-run; only b + c execute.
    ran_second: list[str] = []
    tasks_run2 = [
        _spec("a", "s", _track(ran_second, "a")),
        _spec("b", "s", _track(ran_second, "b"), deps=("a",)),
        _spec("c", "s", _track(ran_second, "c"), deps=("b",)),
    ]
    result2 = run_dag(tasks_run2, checkpoint=checkpoint)
    assert result2.succeeded
    assert "a" not in ran_second  # skipped on resume
    assert ran_second == ["b", "c"]
    assert checkpoint.completed() == {"a", "b", "c"}


# --------------------------------------------------------------------------- #
# StorageCheckpoint (blob-backed resume)
# --------------------------------------------------------------------------- #


class _FakeStorage:
    """Minimal StorageBackend supporting get_bytes + update_bytes."""

    def __init__(self):
        self._data: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def get_bytes(self, path):
        return self._data.get(path)

    def update_bytes(self, path, content_type, update):
        with self._lock:
            new = update(self._data.get(path))
            self._data[path] = new
            return new


def test_storage_checkpoint_roundtrip_and_resume():
    storage = _FakeStorage()
    checkpoint = StorageCheckpoint.for_job(storage, "job-123")
    assert checkpoint.path == "jobs/job-123/scheduler/checkpoint.json"
    assert checkpoint.completed() == set()

    ran: list[str] = []

    def _track(name):
        def _run():
            ran.append(name)
            return name

        return _run

    tasks = [
        _spec("a", "s", _track("a")),
        _spec("b", "s", _track("b"), deps=("a",)),
    ]
    run_dag(tasks, checkpoint=checkpoint)
    assert checkpoint.completed() == {"a", "b"}

    # A fresh checkpoint reading the same storage sees the persisted set.
    reopened = StorageCheckpoint.for_job(storage, "job-123")
    assert reopened.completed() == {"a", "b"}

    ran.clear()
    run_dag(tasks, checkpoint=reopened)
    assert ran == []  # everything resumed; nothing re-ran


def test_storage_checkpoint_ignores_corrupt_blob():
    storage = _FakeStorage()
    storage._data["jobs/j/scheduler/checkpoint.json"] = b"not json{"
    checkpoint = StorageCheckpoint.for_job(storage, "j")
    assert checkpoint.completed() == set()


# --------------------------------------------------------------------------- #
# Progress callback
# --------------------------------------------------------------------------- #


def test_on_progress_reports_transitions():
    events: list[tuple[str, TaskState]] = []

    def _cb(result: TaskResult):
        events.append((result.id, result.state))

    tasks = [
        _spec("a", "s", lambda: None),
        _spec("b", "s", lambda: None, deps=("a",)),
    ]
    run_dag(tasks, on_progress=_cb)
    # Each task should report RUNNING then COMPLETED.
    assert ("a", TaskState.RUNNING) in events
    assert ("a", TaskState.COMPLETED) in events
    assert ("b", TaskState.RUNNING) in events
    assert ("b", TaskState.COMPLETED) in events
    # RUNNING precedes COMPLETED for "a".
    assert events.index(("a", TaskState.RUNNING)) < events.index(("a", TaskState.COMPLETED))


def test_on_progress_failure_does_not_break_run():
    def _bad_cb(_result):
        raise RuntimeError("callback explodes")

    tasks = [_spec("a", "s", lambda: 1)]
    result = run_dag(tasks, on_progress=_bad_cb)
    assert result.succeeded


# --------------------------------------------------------------------------- #
# Canonical pipeline graph builder
# --------------------------------------------------------------------------- #


def test_build_generation_dag_shape():
    calls: list[tuple[str, str, frozenset]] = []

    def factory(stage, task_id, deps):
        calls.append((stage, task_id, deps))
        return lambda: task_id

    specs = build_generation_dag(
        segment_ids=["seg_00", "seg_01"],
        repo_ids=["repo_00", "repo_01"],
        run_factory=factory,
    )
    by_id = {s.id: s for s in specs}

    # Expected nodes exist.
    assert "script" in by_id
    assert {"tts_seg_00", "tts_seg_01"} <= set(by_id)
    assert {"record_repo_00", "norm_repo_00"} <= set(by_id)
    assert {"audio_mix", "compose", "mux", "distribute"} <= set(by_id)

    # Dependency wiring.
    assert by_id["tts_seg_00"].deps == frozenset({"script"})
    assert by_id["record_repo_00"].deps == frozenset({"script"})
    assert by_id["norm_repo_00"].deps == frozenset({"record_repo_00"})
    assert by_id["audio_mix"].deps == frozenset({"tts_seg_00", "tts_seg_01"})
    assert by_id["compose"].deps == frozenset({"audio_mix", "norm_repo_00", "norm_repo_01"})
    assert by_id["mux"].deps == frozenset({"compose"})
    assert by_id["distribute"].deps == frozenset({"mux"})

    # It is a valid DAG and executes end-to-end with the injected callables.
    result = run_dag(specs)
    assert result.succeeded
    assert result.tasks["distribute"].result == "distribute"


def test_build_generation_dag_without_distribute():
    specs = build_generation_dag(
        segment_ids=["s0"],
        repo_ids=["r0"],
        run_factory=lambda stage, tid, deps: lambda: tid,
        distribute=False,
    )
    ids = {s.id for s in specs}
    assert "mux" in ids
    assert "distribute" not in ids


def test_build_generation_dag_no_repos_still_valid():
    specs = build_generation_dag(
        segment_ids=["s0", "s1"],
        repo_ids=[],
        run_factory=lambda stage, tid, deps: lambda: tid,
    )
    by_id = {s.id: s for s in specs}
    # compose depends on audio_mix only when there are no repo clips.
    assert by_id["compose"].deps == frozenset({"audio_mix"})
    result = run_dag(specs)
    assert result.succeeded


def test_default_stage_concurrency_constants_present():
    # Documented defaults the budgeting policy (#484) builds on.
    assert DEFAULT_STAGE_CONCURRENCY["tts"] >= DEFAULT_STAGE_CONCURRENCY["normalize"]
    assert DEFAULT_STAGE_CONCURRENCY["script"] == 1
