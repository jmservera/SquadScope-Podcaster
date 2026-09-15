from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from podcaster.video.budget import VideoStage, VideoStageBudget
from podcaster.video.process import (
    MediaEvidence,
    MediaValidationError,
    MediaValidationReason,
    OwnedProcessTimeout,
    ProbeEvidence,
    collect_media_evidence,
    run_owned_process,
)


def _is_running(pid: int) -> bool:
    stat_path = Path(f"/proc/{pid}/stat")
    if not stat_path.exists():
        return False
    try:
        state = stat_path.read_text(encoding="utf-8").split()[2]
    except (OSError, IndexError):
        return False
    return state != "Z"


def test_runner_keeps_optional_unbounded_compatibility():
    result = run_owned_process([sys.executable, "-c", "print('ok')"])
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_runner_terminates_child_and_grandchild_tree_and_rejects_partial_output(tmp_path):
    child_pid_path = tmp_path / "child.pid"
    grandchild_pid_path = tmp_path / "grandchild.pid"
    partial_output = tmp_path / "partial.mp4"
    child_code = (
        "import signal,subprocess,sys,time,pathlib;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "p=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)']);"
        f"pathlib.Path({str(grandchild_pid_path)!r}).write_text(str(p.pid));"
        "time.sleep(60)"
    )
    parent_code = (
        "import signal,subprocess,sys,time,pathlib;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"pathlib.Path({str(partial_output)!r}).write_bytes(b'partial');"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(p.pid));"
        "time.sleep(60)"
    )

    with pytest.raises(OwnedProcessTimeout):
        run_owned_process(
            [sys.executable, "-c", parent_code],
            timeout_seconds=0.8,
            terminate_grace_seconds=0.2,
            reap_grace_seconds=1.0,
            output_paths=[partial_output],
        )

    child_pid = int(child_pid_path.read_text())
    grandchild_pid = int(grandchild_pid_path.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and (_is_running(child_pid) or _is_running(grandchild_pid)):
        time.sleep(0.02)
    assert not _is_running(child_pid)
    assert not _is_running(grandchild_pid)
    assert not partial_output.exists()


def test_runner_removes_output_on_checked_failure(tmp_path):
    output = tmp_path / "bad.mp4"
    code = f"from pathlib import Path; Path({str(output)!r}).write_bytes(b'x'); raise SystemExit(3)"
    with pytest.raises(subprocess.CalledProcessError):
        run_owned_process(
            [sys.executable, "-c", code],
            timeout_seconds=2,
            output_paths=[output],
            check=True,
        )
    assert not output.exists()


def test_budget_prevents_process_start_after_stage_deadline(tmp_path):
    started = datetime(2026, 9, 15, tzinfo=timezone.utc)
    budget = VideoStageBudget.start(
        now_utc=started,
        monotonic=lambda: 3300.0,
        utcnow=lambda: started + timedelta(seconds=3300),
    )
    output = tmp_path / "never-created"
    with pytest.raises(OwnedProcessTimeout) as exc_info:
        run_owned_process(
            [sys.executable, "-c", f"open({str(output)!r}, 'w').write('bad')"],
            budget=budget,
            stage=VideoStage.RENDER,
            output_paths=[output],
        )
    assert exc_info.value.timeout_seconds == 0
    assert not output.exists()


def test_media_evidence_collects_size_sha256_and_probe(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"valid-media")
    timeouts: list[float] = []

    def probe(path: Path, timeout: float) -> ProbeEvidence:
        timeouts.append(timeout)
        return ProbeEvidence("mov,mp4", 12.5)

    evidence = collect_media_evidence(media, probe=probe, timeout_seconds=7)

    assert evidence.size_bytes == len(b"valid-media")
    assert len(evidence.sha256) == 64
    assert evidence.probe == ProbeEvidence("mov,mp4", 12.5)
    assert timeouts == [7]
    assert MediaEvidence.from_dict(evidence.to_dict()) == evidence


@pytest.mark.parametrize(
    "content,reason",
    [
        (b"", MediaValidationReason.ZERO_BYTE),
    ],
)
def test_media_evidence_rejects_incomplete_files(tmp_path, content, reason):
    media = tmp_path / "clip.mp4"
    media.write_bytes(content)
    with pytest.raises(MediaValidationError) as exc_info:
        collect_media_evidence(media, probe=lambda path, timeout: ProbeEvidence("mp4", 1))
    assert exc_info.value.reason is reason


def test_media_evidence_rejects_missing_file(tmp_path):
    with pytest.raises(MediaValidationError) as exc_info:
        collect_media_evidence(
            tmp_path / "missing.mp4",
            probe=lambda path, timeout: ProbeEvidence("mp4", 1),
        )
    assert exc_info.value.reason is MediaValidationReason.MISSING


def test_media_evidence_rejects_equal_size_hash_change_before_probe(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"aaaa")
    expected = collect_media_evidence(media, probe=lambda path, timeout: ProbeEvidence("mp4", 1))
    media.write_bytes(b"bbbb")
    called = False

    def probe(path: Path, timeout: float) -> ProbeEvidence:
        nonlocal called
        called = True
        return ProbeEvidence("mp4", 1)

    with pytest.raises(MediaValidationError) as exc_info:
        collect_media_evidence(media, expected=expected, probe=probe)
    assert exc_info.value.reason is MediaValidationReason.SHA256_MISMATCH
    assert called is False


def test_media_evidence_rejects_failed_probe(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"bytes")

    def probe(path: Path, timeout: float) -> ProbeEvidence:
        raise OSError("probe unavailable")

    with pytest.raises(MediaValidationError) as exc_info:
        collect_media_evidence(media, probe=probe)
    assert exc_info.value.reason is MediaValidationReason.PROBE_FAILED


def test_media_evidence_accepts_equivalent_ffprobe_format_aliases(tmp_path):
    media = tmp_path / "clip.webm"
    media.write_bytes(b"valid-media")
    expected = collect_media_evidence(
        media,
        probe=lambda path, timeout: ProbeEvidence("matroska,webm", 1.0),
    )

    actual = collect_media_evidence(
        media,
        expected=expected,
        probe=lambda path, timeout: ProbeEvidence("webm", 1.0),
    )

    assert actual.probe.format_name == "webm"
