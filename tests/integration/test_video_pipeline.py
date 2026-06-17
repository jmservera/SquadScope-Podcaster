from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from podcaster.video.sync_plan import (
    RepoReference,
    VideoSegment,
    extract_repo_urls,
    generate_episode_plan,
)
from podcaster.video.video_compose import ComposeResult, compose_video
from podcaster.video.video_gen import RecordedSegment


def _mp4_bytes(size: int = 2048) -> bytes:
    header = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41"
    return header + b"\x00" * max(0, size - len(header))


class FakeCommandRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command[:])
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_mp4_bytes())
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _recorded_segments(
    *,
    plan,
    output_dir: Path,
    fake_webm: Path,
) -> list[RecordedSegment]:
    output_dir.mkdir(parents=True, exist_ok=True)
    recorded: list[RecordedSegment] = []
    for index, segment in enumerate(plan.segments):
        webm_path = output_dir / f"segment-{index:02d}.webm"
        webm_path.write_bytes(fake_webm.read_bytes())
        recorded.append(RecordedSegment(segment=segment, video_path=webm_path))
    return recorded


@pytest.mark.parametrize("with_audio", [True, False], ids=["with-audio", "without-audio"])
def test_video_pipeline_generates_mp4_output(
    tmp_path: Path,
    sample_script: str,
    fake_webm: Path,
    fake_mp3: Path,
    with_audio: bool,
) -> None:
    repos = extract_repo_urls(sample_script)

    assert repos == [
        RepoReference(owner="octocat", name="hello-world"),
        RepoReference(owner="pallets", name="flask"),
    ]

    plan = generate_episode_plan(repos, total_duration_seconds=12.0)

    assert len(plan.segments) == 2
    assert all(isinstance(repo, RepoReference) for repo in repos)
    assert all(isinstance(segment, VideoSegment) for segment in plan.segments)

    recorded_segments = _recorded_segments(
        plan=plan,
        output_dir=tmp_path / "recordings",
        fake_webm=fake_webm,
    )
    runner = FakeCommandRunner()
    output_path = tmp_path / ("episode-with-audio.mp4" if with_audio else "episode-without-audio.mp4")

    result = compose_video(
        recorded_segments,
        audio_path=fake_mp3 if with_audio else None,
        output_path=output_path,
        runner=runner,
    )

    assert isinstance(result, ComposeResult)
    assert result.output_path.exists()
    assert result.output_path.suffix == ".mp4"
    assert result.segment_count == 2
    assert result.duration_seconds == pytest.approx(11.0)
    assert result.has_audio is with_audio
    assert result.output_path.read_bytes().startswith(b"\x00\x00\x00\x18ftyp")

    final_command = runner.commands[-1]
    if with_audio:
        assert str(fake_mp3) in final_command
    else:
        assert str(fake_mp3) not in final_command
