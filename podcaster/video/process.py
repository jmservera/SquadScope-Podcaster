"""Owned process-tree execution and media validation foundations."""

from __future__ import annotations

import ctypes
import hashlib
import json
import multiprocessing
import os
import pickle
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from podcaster.video.budget import TimeoutReason, VideoStage, VideoStageBudget

MAX_CAPTURE_CHARS = 16_384
DEFAULT_TERMINATE_GRACE_SECONDS = 2.0
DEFAULT_REAP_GRACE_SECONDS = 1.0
MEDIA_VALIDATION_SCHEMA_VERSION = 1


class MediaValidationReason(str, Enum):
    MISSING = "missing"
    ZERO_BYTE = "zero_byte"
    SIZE_MISMATCH = "size_mismatch"
    SHA256_MISMATCH = "sha256_mismatch"
    PROBE_FAILED = "probe_failed"
    DEADLINE_REACHED = "deadline_reached"
    SCHEMA_MISMATCH = "schema_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    PROBE_MISMATCH = "probe_mismatch"


class OwnedProcessTimeout(TimeoutError):
    def __init__(
        self,
        command: Sequence[str],
        timeout_seconds: float,
        *,
        stdout: str = "",
        stderr: str = "",
        reason: TimeoutReason = TimeoutReason.OPERATION_DEADLINE,
    ) -> None:
        super().__init__(
            f"owned process exceeded {timeout_seconds:.3f}s deadline: "
            f"{Path(command[0]).name if command else '<empty>'}"
        )
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self.stdout = stdout
        self.stderr = stderr
        self.reason = reason


class OwnedCallableTimeout(TimeoutError):
    """Raised after a fork-owned callable and its process group are stopped."""


class MediaValidationError(ValueError):
    def __init__(self, reason: MediaValidationReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ProbeEvidence:
    format_name: str
    duration_seconds: float | None

    def __post_init__(self) -> None:
        if not self.format_name.strip():
            raise ValueError("probe format_name must not be empty")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("probe duration must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_name": self.format_name,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class MediaEvidence:
    size_bytes: int
    sha256: str
    probe: ProbeEvidence

    def __post_init__(self) -> None:
        if self.size_bytes <= 0:
            raise ValueError("media evidence requires a positive size")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("media evidence requires a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "probe": self.probe.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> MediaEvidence:
        probe_data = data.get("probe")
        if not isinstance(probe_data, Mapping):
            raise ValueError("media evidence probe is missing")
        duration = probe_data.get("duration_seconds")
        return cls(
            size_bytes=int(data.get("size_bytes", 0)),
            sha256=str(data.get("sha256", "")),
            probe=ProbeEvidence(
                format_name=str(probe_data.get("format_name", "")),
                duration_seconds=float(duration) if duration is not None else None,
            ),
        )


@dataclass(frozen=True)
class MediaValidationRecord:
    """Versioned evidence binding reusable media bytes to a logical identity."""

    artifact_kind: str
    identity: Mapping[str, str]
    media: MediaEvidence
    schema_version: int = MEDIA_VALIDATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEDIA_VALIDATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported media validation schema {self.schema_version}")
        if not self.artifact_kind.strip():
            raise ValueError("artifact_kind must not be empty")
        normalized = {str(key): str(value) for key, value in self.identity.items()}
        if not normalized or any(not key.strip() or not value for key, value in normalized.items()):
            raise ValueError("media validation identity must contain non-empty strings")
        object.__setattr__(self, "identity", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_kind": self.artifact_kind,
            "identity": dict(sorted(self.identity.items())),
            "media": self.media.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> MediaValidationRecord:
        if int(data.get("schema_version", 0)) != MEDIA_VALIDATION_SCHEMA_VERSION:
            raise ValueError("unknown or legacy media validation schema")
        identity = data.get("identity")
        media = data.get("media")
        if not isinstance(identity, Mapping) or not isinstance(media, Mapping):
            raise ValueError("media validation record is malformed")
        return cls(
            artifact_kind=str(data.get("artifact_kind", "")),
            identity={str(key): str(value) for key, value in identity.items()},
            media=MediaEvidence.from_dict(media),
        )

    def require_identity(self, artifact_kind: str, identity: Mapping[str, str]) -> None:
        expected = {str(key): str(value) for key, value in identity.items()}
        if self.artifact_kind != artifact_kind or dict(self.identity) != expected:
            raise MediaValidationError(
                MediaValidationReason.IDENTITY_MISMATCH,
                f"{artifact_kind} validation identity does not match",
            )


def _bounded_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return text[-MAX_CAPTURE_CHARS:]


def _format_aliases(format_name: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in format_name.split(",") if part.strip())


def _remove_outputs(output_paths: Sequence[Path | str]) -> None:
    for output_path in output_paths:
        try:
            Path(output_path).unlink(missing_ok=True)
        except OSError:
            pass


def _enable_child_subreaper() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(36, 1, 0, 0, 0) == 0
    except (AttributeError, OSError):
        return False


def _signal_process_group(process: subprocess.Popen[Any], sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return


def _reap_adopted_group(process_group: int, grace_seconds: float) -> None:
    if not sys.platform.startswith("linux"):
        return
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while True:
        reaped_any = False
        try:
            while True:
                pid, _ = os.waitpid(-process_group, os.WNOHANG)
                if pid <= 0:
                    break
                reaped_any = True
        except ChildProcessError:
            return
        if time.monotonic() >= deadline:
            return
        if not reaped_any:
            time.sleep(0.01)


def run_owned_process(
    command: Sequence[str],
    *,
    timeout_seconds: float | None = None,
    budget: VideoStageBudget | None = None,
    stage: VideoStage = VideoStage.SHUTDOWN,
    terminate_grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS,
    reap_grace_seconds: float = DEFAULT_REAP_GRACE_SECONDS,
    output_paths: Sequence[Path | str] = (),
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command in an owned session and kill/reap its process tree on timeout."""

    if not command:
        raise ValueError("command must not be empty")
    effective_timeout = timeout_seconds
    if budget is not None:
        effective_timeout = budget.operation_timeout(stage, timeout_seconds)
    if effective_timeout is not None and effective_timeout <= 0:
        _remove_outputs(output_paths)
        raise OwnedProcessTimeout(command, 0.0, reason=TimeoutReason.STAGE_DEADLINE)

    subreaper_enabled = _enable_child_subreaper()
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=effective_timeout)
    except subprocess.TimeoutExpired as exc:
        _signal_process_group(process, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=max(0.0, terminate_grace_seconds))
        except subprocess.TimeoutExpired:
            _signal_process_group(process, signal.SIGKILL)
            try:
                stdout, stderr = process.communicate(timeout=max(0.0, reap_grace_seconds))
            except subprocess.TimeoutExpired as reap_exc:
                stdout = reap_exc.stdout
                stderr = reap_exc.stderr
                for pipe in (process.stdout, process.stderr, process.stdin):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except OSError:
                            pass
                try:
                    process.wait(timeout=max(0.0, reap_grace_seconds))
                except subprocess.TimeoutExpired:
                    pass
        if subreaper_enabled:
            _reap_adopted_group(process.pid, reap_grace_seconds)
        _remove_outputs(output_paths)
        captured_stdout = _bounded_text(stdout) or _bounded_text(exc.stdout)
        captured_stderr = _bounded_text(stderr) or _bounded_text(exc.stderr)
        raise OwnedProcessTimeout(
            command,
            float(effective_timeout or 0.0),
            stdout=captured_stdout,
            stderr=captured_stderr,
            reason=(
                TimeoutReason.STAGE_DEADLINE
                if budget is not None and budget.remaining_seconds(stage) <= 0
                else TimeoutReason.OPERATION_DEADLINE
            ),
        ) from exc

    completed = subprocess.CompletedProcess(
        args=list(command),
        returncode=process.returncode,
        stdout=_bounded_text(stdout),
        stderr=_bounded_text(stderr),
    )
    if check and completed.returncode != 0:
        _remove_outputs(output_paths)
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def _owned_callable_state_objects(call: Callable[[], Any]) -> list[Any]:
    objects: list[Any] = []
    seen: set[int] = set()

    def _add(value: Any) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        if hasattr(value, "__dict__") and not isinstance(value, type):
            objects.append(value)

    _add(getattr(call, "__self__", None))
    closure = getattr(call, "__closure__", None)
    if closure:
        for cell in closure:
            try:
                _add(cell.cell_contents)
            except ValueError:
                continue
    return objects


def _snapshot_owned_callable_state(objects: Sequence[Any]) -> dict[int, dict[str, Any]]:
    snapshots: dict[int, dict[str, Any]] = {}
    for index, value in enumerate(objects):
        attributes: dict[str, Any] = {}
        for name, attribute in vars(value).items():
            try:
                pickle.dumps(attribute)
            except (AttributeError, pickle.PickleError, TypeError):
                continue
            attributes[name] = attribute
        if attributes:
            snapshots[index] = attributes
    return snapshots


def _transport_owned_callable_result(value: Any) -> Any:
    try:
        pickle.dumps(value)
        return value
    except (AttributeError, pickle.PickleError, TypeError):
        attributes: dict[str, Any] = {}
        for name, attribute in vars(value).items():
            if name.startswith("_"):
                continue
            try:
                pickle.dumps(attribute)
            except (AttributeError, pickle.PickleError, TypeError):
                continue
            attributes[name] = attribute
        if attributes:
            return SimpleNamespace(**attributes)
        raise


def _apply_owned_callable_state(
    objects: Sequence[Any],
    snapshots: Mapping[int, Mapping[str, Any]],
) -> None:
    for index, attributes in snapshots.items():
        if index >= len(objects):
            continue
        value = objects[index]
        for name, attribute in attributes.items():
            try:
                setattr(value, name, attribute)
            except (AttributeError, TypeError):
                continue


def _owned_callable_child(
    call: Callable[[], Any],
    state_objects: Sequence[Any],
    connection: Any,
) -> None:
    try:
        os.setsid()
        connection.send(("ready", None))
        try:
            result = call()
            connection.send(
                (
                    "result",
                    _transport_owned_callable_result(result),
                    _snapshot_owned_callable_state(state_objects),
                )
            )
        except BaseException as exc:
            try:
                connection.send(("exception", exc, _snapshot_owned_callable_state(state_objects)))
            except BaseException:
                connection.send(
                    (
                        "exception",
                        RuntimeError(
                            f"owned callable raised untransportable {type(exc).__name__}: {exc}"
                        ),
                        {},
                    )
                )
    finally:
        connection.close()


def _stop_owned_callable(
    worker: multiprocessing.Process,
    *,
    session_ready: bool,
    subreaper_enabled: bool,
) -> None:
    if session_ready:
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif worker.is_alive():
        worker.kill()
    worker.join()
    if subreaper_enabled:
        _reap_adopted_group(worker.pid, DEFAULT_REAP_GRACE_SECONDS)


def run_owned_callable(
    call: Callable[[], Any],
    timeout_seconds: float,
    *,
    process_name: str = "video-owned-operation",
) -> Any:
    """Run a closure in a fork-owned process and kill/reap it at its deadline.

    The Linux ``fork`` boundary preserves existing closure-based injectable
    seams without requiring callables or SDK clients to be picklable. A timed
    out operation is killed as an owned process group before this function
    raises, so it cannot continue running after the caller regains control.
    Picklable state on closure objects is copied back only after the callable
    completes, preserving in-memory test doubles without leaking timed-out work.
    """

    if timeout_seconds <= 0:
        raise OwnedCallableTimeout("owned callable has no remaining budget")
    if "fork" not in multiprocessing.get_all_start_methods():
        raise RuntimeError("owned callable deadlines require a fork-capable platform")

    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    state_objects = _owned_callable_state_objects(call)
    worker = context.Process(
        target=_owned_callable_child,
        args=(call, state_objects, sender),
        name=process_name,
    )
    subreaper_enabled = _enable_child_subreaper()
    started = time.monotonic()
    session_ready = False
    worker.start()
    sender.close()
    try:
        while True:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            if receiver.poll(min(remaining, 0.05)):
                message = receiver.recv()
                kind, value = message[:2]
                if kind == "ready":
                    session_ready = True
                    continue
                _stop_owned_callable(
                    worker,
                    session_ready=session_ready,
                    subreaper_enabled=subreaper_enabled,
                )
                _apply_owned_callable_state(state_objects, message[2])
                if kind == "result":
                    return value
                raise value
            if not worker.is_alive():
                _stop_owned_callable(
                    worker,
                    session_ready=session_ready,
                    subreaper_enabled=subreaper_enabled,
                )
                raise RuntimeError(
                    f"owned callable worker exited with code {worker.exitcode} without a result"
                )

        _stop_owned_callable(
            worker,
            session_ready=session_ready,
            subreaper_enabled=subreaper_enabled,
        )
        raise OwnedCallableTimeout(f"owned callable exceeded {timeout_seconds:.3f}s deadline")
    finally:
        receiver.close()
        if worker.is_alive():
            worker.kill()
            worker.join()


def probe_media(
    path: Path | str,
    timeout_seconds: float,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_owned_process,
    budget: VideoStageBudget | None = None,
    stage: VideoStage = VideoStage.RENDER,
) -> ProbeEvidence:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration",
        "-of",
        "json",
        str(path),
    ]
    if runner is run_owned_process:
        result = runner(
            command,
            timeout_seconds=timeout_seconds,
            budget=budget,
            stage=stage,
            check=True,
        )
    else:
        result = runner(command, timeout_seconds=timeout_seconds, check=True)
    try:
        document = json.loads(result.stdout)
        format_data = document["format"]
        format_name = str(format_data["format_name"])
        raw_duration = format_data.get("duration")
        duration = float(raw_duration) if raw_duration not in (None, "N/A") else None
        return ProbeEvidence(format_name=format_name, duration_seconds=duration)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaValidationError(
            MediaValidationReason.PROBE_FAILED, f"invalid ffprobe output for {path}"
        ) from exc


def collect_media_evidence(
    path: Path | str,
    *,
    probe: Callable[[Path, float], ProbeEvidence] = probe_media,
    timeout_seconds: float = 30.0,
    budget: VideoStageBudget | None = None,
    stage: VideoStage = VideoStage.RENDER,
    expected: MediaEvidence | None = None,
    hash_runner: Callable[[Callable[[], Any], float], Any] = run_owned_callable,
) -> MediaEvidence:
    """Collect size, SHA-256 and bounded probe evidence, rejecting incomplete media."""

    media_path = Path(path)
    if not media_path.is_file():
        raise MediaValidationError(MediaValidationReason.MISSING, f"media is missing: {media_path}")
    size = media_path.stat().st_size
    if size <= 0:
        raise MediaValidationError(MediaValidationReason.ZERO_BYTE, f"media is empty: {media_path}")
    if expected is not None and size != expected.size_bytes:
        raise MediaValidationError(
            MediaValidationReason.SIZE_MISMATCH,
            f"media size mismatch for {media_path}: expected {expected.size_bytes}, got {size}",
        )

    hash_timeout = timeout_seconds
    if budget is not None:
        hash_timeout = budget.operation_timeout(stage, timeout_seconds)
    if hash_timeout <= 0:
        raise MediaValidationError(
            MediaValidationReason.DEADLINE_REACHED,
            f"no remaining hash budget for {media_path}",
        )

    def _hash() -> str:
        digest = hashlib.sha256()
        with media_path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    try:
        sha256 = str(hash_runner(_hash, hash_timeout))
    except TimeoutError as exc:
        raise MediaValidationError(
            MediaValidationReason.DEADLINE_REACHED,
            f"media hashing exceeded its deadline for {media_path}",
        ) from exc
    if expected is not None and sha256 != expected.sha256:
        raise MediaValidationError(
            MediaValidationReason.SHA256_MISMATCH, f"media SHA-256 mismatch for {media_path}"
        )

    effective_timeout = timeout_seconds
    if budget is not None:
        effective_timeout = budget.operation_timeout(stage, timeout_seconds)
    if effective_timeout <= 0:
        raise MediaValidationError(
            MediaValidationReason.DEADLINE_REACHED,
            f"no remaining probe budget for {media_path}",
        )
    try:
        if probe is probe_media:
            probe_evidence = probe(
                media_path,
                effective_timeout,
                budget=budget,
                stage=stage,
            )
        else:
            probe_evidence = probe(media_path, effective_timeout)
    except MediaValidationError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise MediaValidationError(
            MediaValidationReason.PROBE_FAILED, f"media probe failed for {media_path}"
        ) from exc
    if expected is not None:
        if not (
            _format_aliases(probe_evidence.format_name)
            & _format_aliases(expected.probe.format_name)
        ):
            raise MediaValidationError(
                MediaValidationReason.PROBE_MISMATCH,
                (
                    f"media format mismatch for {media_path}: "
                    f"expected {expected.probe.format_name}, got {probe_evidence.format_name}"
                ),
            )
        if (
            expected.probe.duration_seconds is not None
            and probe_evidence.duration_seconds is not None
            and abs(probe_evidence.duration_seconds - expected.probe.duration_seconds) > 0.25
        ):
            raise MediaValidationError(
                MediaValidationReason.PROBE_MISMATCH,
                f"media duration mismatch for {media_path}",
            )
    return MediaEvidence(size_bytes=size, sha256=sha256, probe=probe_evidence)


def validate_media_record(
    path: Path | str,
    record: MediaValidationRecord,
    *,
    artifact_kind: str,
    identity: Mapping[str, str],
    probe: Callable[[Path, float], ProbeEvidence] = probe_media,
    timeout_seconds: float = 30.0,
    budget: VideoStageBudget | None = None,
    stage: VideoStage = VideoStage.RENDER,
) -> MediaEvidence:
    """Validate a reusable artifact against its versioned identity and bytes."""

    record.require_identity(artifact_kind, identity)
    return collect_media_evidence(
        path,
        probe=probe,
        timeout_seconds=timeout_seconds,
        budget=budget,
        stage=stage,
        expected=record.media,
    )
