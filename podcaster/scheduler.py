"""Dependency-driven DAG scheduler for the generation pipeline (#477).

This is the **keystone** of the Phase 5 parallel audio & video generation
pipeline (jmservera/SquadScope-Coordinator#31). Every other parallel stage —
the async TTS pool (#478), parallel browser recording (#479), parallel
normalization (#480), parallel pairwise composition (#481), progress reporting
(#482), partial-failure recovery (#483) and resource budgeting (#484) — plugs
into the scheduler defined here.

The generation pipeline is a directed acyclic graph (DAG) of tasks::

    script ─┬─▶ tts_seg_*  ─┐
            │               ├─▶ audio_mix ─┐
            └─▶ record_repo_* ─▶ norm_*    │
                                ╰──────────┴─▶ compose ─▶ mux ─▶ distribute

Tasks that do not depend on one another run **concurrently**; a task only starts
once *all* of its dependencies have completed. Because the real workloads are
I/O- and subprocess-bound (blob transfers, ffmpeg, TTS HTTP, Playwright), the
scheduler is **thread based** — matching the repository's existing
:class:`concurrent.futures.ThreadPoolExecutor` convention
(:mod:`podcaster.language_fanout`, :mod:`podcaster.video.video_compose`).

Three guarantees, mapped to the issue's acceptance criteria:

* **Concurrency with correct ordering.** Independent tasks run in parallel up to
  per-stage caps; dependent tasks wait for their dependencies.
* **Resumable.** A :class:`Checkpoint` records completed task IDs. A resumed run
  treats checkpointed tasks as already done and re-runs only what is incomplete.
* **Configurable per-stage caps.** Each *stage* (``tts``, ``record`` …) has its
  own concurrency cap, so overlapping stages never collectively exceed the
  container's RAM/CPU/API budget. (Central budgeting policy lives in #484; this
  module provides the enforcement mechanism and sensible defaults.)

Failure isolation: when a task fails, only its transitive dependents are marked
:attr:`TaskState.BLOCKED`; independent branches keep running. Per-task *retry*
is layered on top by #483 via the ``run`` callable / :class:`RetryPolicy`-style
wrappers — the scheduler itself runs each task's callable exactly once.

The scheduler is pure orchestration: callers inject each task's ``run`` callable,
so the engine, the job runner and the tests all compose the same control flow.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

logger = logging.getLogger("podcaster.scheduler")


# --------------------------------------------------------------------------- #
# Stage names + default per-stage concurrency caps
# --------------------------------------------------------------------------- #

#: Canonical pipeline stage identifiers. Tasks are grouped by stage so that
#: concurrency caps and progress reporting can be applied per stage.
STAGE_SCRIPT = "script"
STAGE_TTS = "tts"
STAGE_RECORD = "record"
STAGE_AUDIO_MIX = "audio_mix"
STAGE_NORMALIZE = "normalize"
STAGE_COMPOSE = "compose"
STAGE_MUX = "mux"
STAGE_DISTRIBUTE = "distribute"

#: Default concurrency cap applied to any stage not listed in
#: :data:`DEFAULT_STAGE_CONCURRENCY`.
DEFAULT_CONCURRENCY = 1

#: Sensible default per-stage caps for an ACA 4-core / 8 GB container. These are
#: a starting point; the resource-budgeting policy (#484) may override them. The
#: rationale per stage:
#:
#: * ``tts`` — API/network bound, so a higher fan-out is safe (provider rate
#:   limits permitting).
#: * ``record`` — Playwright contexts are RAM bound (~1.5 GB each).
#: * ``normalize`` — CPU-bound ffmpeg re-encode (~2 per core headroom).
#: * ``compose`` — CPU + disk-I/O bound pairwise concat.
DEFAULT_STAGE_CONCURRENCY: dict[str, int] = {
    STAGE_SCRIPT: 1,
    STAGE_TTS: 8,
    STAGE_RECORD: 3,
    STAGE_AUDIO_MIX: 1,
    STAGE_NORMALIZE: 2,
    STAGE_COMPOSE: 2,
    STAGE_MUX: 1,
    STAGE_DISTRIBUTE: 1,
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class SchedulerError(ValueError):
    """Base class for scheduler graph-construction errors."""


class UnknownDependencyError(SchedulerError):
    """A task declares a dependency on a task ID that is not in the graph."""


class DuplicateTaskError(SchedulerError):
    """Two tasks share the same ID."""


class CyclicDependencyError(SchedulerError):
    """The task graph contains a dependency cycle (not a DAG)."""


# --------------------------------------------------------------------------- #
# Task model
# --------------------------------------------------------------------------- #


class TaskState(str, Enum):
    """Lifecycle state of a single task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    #: A dependency failed (or was itself blocked), so this task can never run.
    BLOCKED = "blocked"
    #: Marked done by a checkpoint from a previous run; not executed this run.
    SKIPPED = "skipped"


_TERMINAL_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.BLOCKED, TaskState.SKIPPED}
)
#: States that satisfy a downstream dependency (the dep produced its output).
_SATISFIED_STATES = frozenset({TaskState.COMPLETED, TaskState.SKIPPED})


@dataclass(frozen=True)
class TaskSpec:
    """One unit of work in the pipeline DAG.

    Args:
        id: Stable, unique task identifier (e.g. ``"tts_seg_03"``). The ID is
            what checkpoints and progress reporting key off, so it must be
            deterministic across runs for resume to work.
        stage: The stage this task belongs to (one of the ``STAGE_*``
            constants, or any custom string). Concurrency caps apply per stage.
        run: Zero-argument callable performing the work. Its return value is
            stored in :attr:`TaskResult.result`. Any exception it raises fails
            the task (and blocks its dependents). The scheduler invokes ``run``
            at most once per scheduler run.
        deps: IDs of tasks that must complete before this one starts.
    """

    id: str
    stage: str
    run: Callable[[], Any]
    deps: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.id:
            raise SchedulerError("task id must be a non-empty string")
        if not self.stage:
            raise SchedulerError(f"task {self.id!r} must declare a stage")
        if not callable(self.run):
            raise SchedulerError(f"task {self.id!r} run must be callable")
        # Normalise deps to a frozenset regardless of the iterable passed in.
        object.__setattr__(self, "deps", frozenset(self.deps))


@dataclass(frozen=True)
class TaskResult:
    """Outcome of a single task after a scheduler run."""

    id: str
    stage: str
    state: TaskState
    result: Any = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    #: True when the task was satisfied by a checkpoint rather than executed.
    resumed: bool = False


@dataclass(frozen=True)
class ScheduleResult:
    """Aggregate outcome of :func:`run_dag`."""

    tasks: dict[str, TaskResult]

    @property
    def succeeded(self) -> bool:
        """True when every task completed (or was resumed); none failed/blocked."""
        return all(
            r.state in _SATISFIED_STATES for r in self.tasks.values()
        )

    def by_state(self, state: TaskState) -> list[TaskResult]:
        return [r for r in self.tasks.values() if r.state == state]

    @property
    def failed(self) -> list[TaskResult]:
        return self.by_state(TaskState.FAILED)

    @property
    def blocked(self) -> list[TaskResult]:
        return self.by_state(TaskState.BLOCKED)


# --------------------------------------------------------------------------- #
# Checkpointing — resume support
# --------------------------------------------------------------------------- #


class Checkpoint(Protocol):
    """Persistence for the set of completed task IDs across runs.

    A resumed run reads :meth:`completed` and treats those tasks as already done
    (state :attr:`TaskState.SKIPPED`), so only incomplete work re-runs.
    """

    def completed(self) -> set[str]:
        """Return the IDs of tasks completed in a previous run."""
        ...

    def mark_completed(self, task_id: str) -> None:
        """Record that ``task_id`` completed (called as tasks finish)."""
        ...


class InMemoryCheckpoint:
    """Thread-safe in-process checkpoint (tests / single-run resume)."""

    def __init__(self, completed: Iterable[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._completed: set[str] = set(completed or ())

    def completed(self) -> set[str]:
        with self._lock:
            return set(self._completed)

    def mark_completed(self, task_id: str) -> None:
        with self._lock:
            self._completed.add(task_id)


class StorageCheckpoint:
    """Checkpoint persisted to a :class:`~podcaster.storage.StorageBackend` blob.

    The completed-task set is stored as JSON at ``path`` (defaulting to
    ``jobs/{job_id}/scheduler/checkpoint.json``). Workers download their inputs
    from blob, process locally and upload results (#410), so persisting the
    checkpoint to the same backend means a fresh container can resume a run that
    a previous container started.

    Updates use the backend's read-modify-write ``update_bytes`` so concurrent
    ``mark_completed`` calls from parallel workers don't lose entries.
    """

    SCHEMA_VERSION = 1

    def __init__(self, storage: Any, path: str) -> None:
        self._storage = storage
        self._path = path
        self._lock = threading.Lock()

    @classmethod
    def for_job(cls, storage: Any, job_id: str) -> "StorageCheckpoint":
        return cls(storage, f"jobs/{job_id}/scheduler/checkpoint.json")

    @property
    def path(self) -> str:
        return self._path

    def _decode(self, raw: bytes | None) -> set[str]:
        if not raw:
            return set()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.warning("scheduler checkpoint at %s is corrupt; ignoring", self._path)
            return set()
        completed = payload.get("completed") if isinstance(payload, dict) else None
        if not isinstance(completed, list):
            return set()
        return {str(t) for t in completed}

    def completed(self) -> set[str]:
        return self._decode(self._storage.get_bytes(self._path))

    def mark_completed(self, task_id: str) -> None:
        with self._lock:

            def _update(current: bytes | None) -> bytes:
                existing = self._decode(current)
                existing.add(task_id)
                body = {
                    "schema_version": self.SCHEMA_VERSION,
                    "completed": sorted(existing),
                    "updated_at": _now_iso(),
                }
                return json.dumps(body, ensure_ascii=False).encode("utf-8")

            self._storage.update_bytes(
                self._path, "application/json; charset=utf-8", _update
            )


# --------------------------------------------------------------------------- #
# Progress / event hook
# --------------------------------------------------------------------------- #

#: A callback invoked on every task state transition. Feeds the progress
#: reporting stream (#482) and observability (jmservera/SquadScope-Coordinator#30).
ProgressCallback = Callable[[TaskResult], None]


# --------------------------------------------------------------------------- #
# Graph validation
# --------------------------------------------------------------------------- #


def _index_tasks(tasks: Sequence[TaskSpec]) -> dict[str, TaskSpec]:
    index: dict[str, TaskSpec] = {}
    for task in tasks:
        if task.id in index:
            raise DuplicateTaskError(f"duplicate task id: {task.id!r}")
        index[task.id] = task
    return index


def validate_graph(tasks: Sequence[TaskSpec]) -> dict[str, TaskSpec]:
    """Validate that ``tasks`` form a DAG and return them indexed by ID.

    Raises:
        DuplicateTaskError: two tasks share an ID.
        UnknownDependencyError: a dependency references an unknown task.
        CyclicDependencyError: the dependency graph contains a cycle.
    """

    index = _index_tasks(tasks)

    for task in index.values():
        for dep in task.deps:
            if dep not in index:
                raise UnknownDependencyError(
                    f"task {task.id!r} depends on unknown task {dep!r}"
                )
            if dep == task.id:
                raise CyclicDependencyError(f"task {task.id!r} depends on itself")

    _assert_acyclic(index)
    return index


def _assert_acyclic(index: Mapping[str, TaskSpec]) -> None:
    """Detect cycles via iterative DFS with a three-colour marking."""

    WHITE, GREY, BLACK = 0, 1, 2
    colour = {tid: WHITE for tid in index}

    for root in index:
        if colour[root] != WHITE:
            continue
        # Stack of (node, advanced?) to emulate recursion without depth limits.
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            node, processed = stack.pop()
            if processed:
                colour[node] = BLACK
                continue
            if colour[node] == BLACK:
                continue
            colour[node] = GREY
            stack.append((node, True))
            for dep in index[node].deps:
                if colour[dep] == GREY:
                    raise CyclicDependencyError(
                        f"dependency cycle detected involving {node!r} -> {dep!r}"
                    )
                if colour[dep] == WHITE:
                    stack.append((dep, False))


# --------------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------------- #


def run_dag(
    tasks: Sequence[TaskSpec],
    *,
    stage_concurrency: Mapping[str, int] | None = None,
    default_concurrency: int = DEFAULT_CONCURRENCY,
    checkpoint: Checkpoint | None = None,
    on_progress: ProgressCallback | None = None,
    max_workers: int | None = None,
) -> ScheduleResult:
    """Execute a task DAG, launching each task as soon as its deps are satisfied.

    Independent tasks run concurrently up to their stage's concurrency cap;
    dependent tasks wait for their dependencies. When a task fails, only its
    transitive dependents are :attr:`TaskState.BLOCKED`; independent branches
    keep running to completion.

    Args:
        tasks: The task specs forming the DAG.
        stage_concurrency: Per-stage concurrency caps (stage name -> max
            in-flight tasks). Stages not listed use ``default_concurrency``.
            Defaults to :data:`DEFAULT_STAGE_CONCURRENCY`.
        default_concurrency: Cap for stages absent from ``stage_concurrency``.
        checkpoint: Optional resume support. Tasks already present in
            ``checkpoint.completed()`` are marked :attr:`TaskState.SKIPPED` and
            not executed; completed tasks are recorded via ``mark_completed``.
        on_progress: Optional callback invoked on every state transition.
        max_workers: Hard cap on the thread pool size. Defaults to the sum of
            the resolved stage caps (bounded below by 1).

    Returns:
        A :class:`ScheduleResult` mapping each task ID to its outcome.
    """

    index = validate_graph(tasks)
    caps = _resolve_caps(index, stage_concurrency, default_concurrency)
    semaphores = {stage: threading.Semaphore(cap) for stage, cap in caps.items()}

    already_done = set(checkpoint.completed()) if checkpoint is not None else set()

    lock = threading.Lock()
    cond = threading.Condition(lock)

    states: dict[str, TaskState] = {}
    results: dict[str, TaskResult] = {}

    def _emit(result: TaskResult) -> None:
        results[result.id] = result
        if on_progress is not None:
            try:
                on_progress(result)
            except Exception:  # progress reporting must never break the run
                logger.exception("on_progress callback failed for task %s", result.id)

    # Seed initial state: checkpointed tasks are skipped; the rest are pending.
    for tid, task in index.items():
        if tid in already_done:
            states[tid] = TaskState.SKIPPED
            _emit(
                TaskResult(
                    id=tid,
                    stage=task.stage,
                    state=TaskState.SKIPPED,
                    resumed=True,
                )
            )
        else:
            states[tid] = TaskState.PENDING

    if max_workers is None:
        max_workers = max(1, sum(caps.values()))

    running_count = [0]

    def _deps_satisfied(task: TaskSpec) -> bool:
        return all(states[dep] in _SATISFIED_STATES for dep in task.deps)

    def _dep_blocked(task: TaskSpec) -> bool:
        return any(
            states[dep] in (TaskState.FAILED, TaskState.BLOCKED) for dep in task.deps
        )

    def _propagate_blocked() -> None:
        """Mark pending tasks whose deps failed/blocked as BLOCKED (transitively)."""
        changed = True
        while changed:
            changed = False
            for tid, task in index.items():
                if states[tid] == TaskState.PENDING and _dep_blocked(task):
                    states[tid] = TaskState.BLOCKED
                    _emit(
                        TaskResult(
                            id=tid,
                            stage=task.stage,
                            state=TaskState.BLOCKED,
                            error="dependency failed",
                            finished_at=_now_iso(),
                        )
                    )
                    changed = True

    def _execute(task: TaskSpec, started_at: str) -> None:
        """Run one task in a worker thread and record its outcome.

        Always runs off the scheduler thread, so the completion bookkeeping
        never re-enters the (non-reentrant) scheduler lock.
        """
        error: str | None = None
        value: Any = None
        try:
            value = task.run()
            state = TaskState.COMPLETED
        except BaseException as exc:  # noqa: BLE001 — record any failure
            state = TaskState.FAILED
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("task %s failed: %s", task.id, error)
        finished = _now_iso()
        with cond:
            states[task.id] = state
            _emit(
                TaskResult(
                    id=task.id,
                    stage=task.stage,
                    state=state,
                    result=value,
                    error=error,
                    started_at=started_at,
                    finished_at=finished,
                )
            )
            semaphores[task.stage].release()
            running_count[0] -= 1
            cond.notify_all()
        if state == TaskState.COMPLETED and checkpoint is not None:
            # Outside the lock: persisting may do blocking I/O.
            try:
                checkpoint.mark_completed(task.id)
            except Exception:
                logger.exception("failed to checkpoint task %s", task.id)

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dag") as pool:
        with cond:
            while True:
                _propagate_blocked()

                # Launch every ready task whose stage has spare capacity.
                progressed = True
                while progressed:
                    progressed = False
                    for tid, task in index.items():
                        if states[tid] != TaskState.PENDING:
                            continue
                        if not _deps_satisfied(task):
                            continue
                        if not semaphores[task.stage].acquire(blocking=False):
                            continue
                        started = _now_iso()
                        states[tid] = TaskState.RUNNING
                        _emit(
                            TaskResult(
                                id=tid,
                                stage=task.stage,
                                state=TaskState.RUNNING,
                                started_at=started,
                            )
                        )
                        running_count[0] += 1
                        pool.submit(_execute, task, started)
                        progressed = True

                if all(states[tid] in _TERMINAL_STATES for tid in index):
                    break

                # Nothing running and nothing launchable => remaining are blocked.
                if running_count[0] == 0 and not any(
                    states[tid] == TaskState.PENDING and _deps_satisfied(index[tid])
                    for tid in index
                ):
                    _propagate_blocked()
                    if all(states[tid] in _TERMINAL_STATES for tid in index):
                        break

                cond.wait()

    return ScheduleResult(tasks=dict(results))


def _resolve_caps(
    index: Mapping[str, TaskSpec],
    stage_concurrency: Mapping[str, int] | None,
    default_concurrency: int,
) -> dict[str, int]:
    overrides = dict(stage_concurrency) if stage_concurrency else dict(DEFAULT_STAGE_CONCURRENCY)
    caps: dict[str, int] = {}
    for task in index.values():
        if task.stage in caps:
            continue
        cap = overrides.get(task.stage, default_concurrency)
        caps[task.stage] = max(1, int(cap))
    return caps


# --------------------------------------------------------------------------- #
# Canonical pipeline graph builder
# --------------------------------------------------------------------------- #

#: Factory that produces a task's ``run`` callable. Given the stage, task ID and
#: the IDs of its dependencies, it returns the zero-argument work callable.
TaskRunFactory = Callable[[str, str, "frozenset[str]"], Callable[[], Any]]


def build_generation_dag(
    *,
    segment_ids: Sequence[str],
    repo_ids: Sequence[str],
    run_factory: TaskRunFactory,
    distribute: bool = True,
) -> list[TaskSpec]:
    """Build the canonical generation pipeline DAG.

    Produces the standard task graph that every Phase 5 stage plugs into::

        script ─┬─▶ tts_seg_*   ─┐
                │                ├─▶ audio_mix ─┐
                └─▶ record_*  ─▶ norm_*         │
                                  ╰─────────────┴─▶ compose ─▶ mux ─▶ distribute

    Wiring of the actual work is the responsibility of the per-stage issues
    (#478–#481): callers inject ``run_factory(stage, task_id, deps)`` to obtain
    each task's callable, keeping this builder pure graph construction.

    Args:
        segment_ids: Stable IDs of the script's audio segments (one TTS task
            each), e.g. ``["seg_00", "seg_01", ...]``.
        repo_ids: Stable IDs of the repo/website recordings (one record + one
            normalize task each), e.g. ``["repo_00", ...]``.
        run_factory: Produces each task's ``run`` callable.
        distribute: Append the terminal ``distribute`` task after ``mux``.

    Returns:
        The list of :class:`TaskSpec` (validated as a DAG before return).
    """

    specs: list[TaskSpec] = []

    def add(task_id: str, stage: str, deps: Iterable[str]) -> str:
        dep_set = frozenset(deps)
        specs.append(
            TaskSpec(
                id=task_id,
                stage=stage,
                deps=dep_set,
                run=run_factory(stage, task_id, dep_set),
            )
        )
        return task_id

    script_id = add("script", STAGE_SCRIPT, ())

    tts_ids = [
        add(f"tts_{sid}", STAGE_TTS, (script_id,)) for sid in segment_ids
    ]

    norm_ids: list[str] = []
    for rid in repo_ids:
        record_id = add(f"record_{rid}", STAGE_RECORD, (script_id,))
        norm_ids.append(add(f"norm_{rid}", STAGE_NORMALIZE, (record_id,)))

    # audio_mix waits for every TTS segment.
    audio_mix_deps = tts_ids or [script_id]
    audio_mix_id = add("audio_mix", STAGE_AUDIO_MIX, audio_mix_deps)

    # compose waits for the mixed audio and every normalized clip.
    compose_deps = [audio_mix_id, *norm_ids]
    compose_id = add("compose", STAGE_COMPOSE, compose_deps)

    mux_id = add("mux", STAGE_MUX, (compose_id,))

    if distribute:
        add("distribute", STAGE_DISTRIBUTE, (mux_id,))

    validate_graph(specs)
    return specs


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
