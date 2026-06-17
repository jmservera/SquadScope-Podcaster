"""Tests for podcaster.video.video_compose module.

Unit tests mock ffmpeg via the CommandRunner protocol.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from podcaster.video.sync_plan import RepoReference, VideoSegment
from podcaster.video.video_gen import RecordedSegment
from podcaster.video.video_compose import (
    ENCODE_CRF,
    ENCODE_PIX_FMT,
    ENCODE_PRESET,
    LOWER_THIRD_DURATION,
    OUTPUT_FPS,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    TRANSITION_DURATION,
    ComposeResult,
    LowerThird,
    _build_drawtext_filter,
    _build_normalize_cmd,
    _build_xfade_filter,
    _compute_lower_thirds,
    compose_video,
)


# --- Helpers ---


def _make_recorded_segment(
    owner: str = "test-owner",
    name: str = "test-repo",
    start: float = 0.0,
    duration: float = 10.0,
    video_path: Path | None = None,
) -> RecordedSegment:
    seg = VideoSegment(
        repo=RepoReference(owner=owner, name=name),
        start_seconds=start,
        duration_seconds=duration,
    )
    return RecordedSegment(
        segment=seg,
        video_path=video_path or Path(f"/tmp/{owner}_{name}.webm"),
    )


def _mock_runner() -> MagicMock:
    """Create a mock command runner that returns success."""
    runner = MagicMock()
    runner.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    return runner


# --- Tests for _build_normalize_cmd ---


class TestBuildNormalizeCmd:
    def test_basic_normalize(self):
        cmd = _build_normalize_cmd(Path("/in/clip.webm"), Path("/out/clip.mp4"))
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert str(Path("/in/clip.webm")) in cmd
        assert str(Path("/out/clip.mp4")) in cmd
        assert "-an" in cmd  # No audio in normalize step

    def test_scale_filter_contains_dimensions(self):
        cmd = _build_normalize_cmd(Path("/in/clip.webm"), Path("/out/clip.mp4"))
        vf_idx = cmd.index("-vf")
        vf_value = cmd[vf_idx + 1]
        assert f"{OUTPUT_WIDTH}" in vf_value
        assert f"{OUTPUT_HEIGHT}" in vf_value
        assert f"fps={OUTPUT_FPS}" in vf_value


# --- Tests for _build_xfade_filter ---


class TestBuildXfadeFilter:
    def test_single_segment_no_filter(self):
        result = _build_xfade_filter([10.0])
        assert result == ""

    def test_two_segments(self):
        result = _build_xfade_filter([10.0, 10.0], transition_duration=1.0)
        assert "xfade" in result
        assert "fadeblack" in result
        assert "duration=1.0" in result
        assert "offset=9.000" in result
        assert "[v01]" in result

    def test_three_segments(self):
        result = _build_xfade_filter([10.0, 10.0, 10.0], transition_duration=1.0)
        assert result.count("xfade") == 2
        assert "[v01]" in result
        assert "[vout]" in result

    def test_custom_transition_duration(self):
        result = _build_xfade_filter([8.0, 8.0], transition_duration=2.0)
        assert "duration=2.0" in result
        assert "offset=6.000" in result


# --- Tests for _build_drawtext_filter ---


class TestBuildDrawtextFilter:
    def test_empty_list(self):
        result = _build_drawtext_filter([])
        assert result == ""

    def test_single_lower_third(self):
        lts = [LowerThird(text="owner/repo", url="https://github.com/owner/repo",
                          start_seconds=1.0, end_seconds=6.0)]
        result = _build_drawtext_filter(lts, "vout")
        assert "drawtext" in result
        assert "owner/repo" in result
        assert "github.com/owner/repo" in result
        assert "enable=" in result
        assert "[vout]" in result
        assert "[final]" in result

    def test_multiple_lower_thirds(self):
        lts = [
            LowerThird(text="a/b", url="https://github.com/a/b",
                       start_seconds=0.5, end_seconds=5.5),
            LowerThird(text="c/d", url="https://github.com/c/d",
                       start_seconds=10.0, end_seconds=15.0),
        ]
        result = _build_drawtext_filter(lts, "vout")
        assert result.count("drawtext") == 4  # 2 per lower-third (name + url)
        assert "[lt0]" in result
        assert "[final]" in result

    def test_special_chars_escaped(self):
        lts = [LowerThird(text="test:repo", url="https://github.com/t/r",
                          start_seconds=0.0, end_seconds=5.0)]
        result = _build_drawtext_filter(lts, "vout")
        assert r"test\:repo" in result


# --- Tests for _compute_lower_thirds ---


class TestComputeLowerThirds:
    def test_single_segment(self):
        segments = [_make_recorded_segment(duration=20.0)]
        lts = _compute_lower_thirds(segments)
        assert len(lts) == 1
        assert lts[0].text == "test-owner/test-repo"
        assert lts[0].start_seconds == 0.5
        assert lts[0].end_seconds == 0.5 + LOWER_THIRD_DURATION

    def test_multiple_segments(self):
        segments = [
            _make_recorded_segment(owner="a", name="b", duration=15.0),
            _make_recorded_segment(owner="c", name="d", duration=15.0),
        ]
        lts = _compute_lower_thirds(segments, transition_duration=1.0)
        assert len(lts) == 2
        assert lts[0].text == "a/b"
        assert lts[1].text == "c/d"
        # Second segment starts after first minus transition
        assert lts[1].start_seconds > lts[0].end_seconds

    def test_short_segment_clamps_duration(self):
        segments = [_make_recorded_segment(duration=2.0)]
        lts = _compute_lower_thirds(segments)
        assert len(lts) == 1
        # End should be clamped
        assert lts[0].end_seconds <= 2.0


# --- Tests for compose_video ---


class TestComposeVideo:
    def test_empty_segments_raises(self):
        with pytest.raises(ValueError, match="No segments"):
            compose_video(segments=[])

    def test_single_segment_no_xfade(self, tmp_path):
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        result = compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=runner,
        )

        assert result.segment_count == 1
        assert result.output_path.suffix == ".mp4"
        assert result.has_audio is False
        # Should have: 1 normalize + 1 compose call
        assert runner.call_count == 2

    def test_two_segments_with_xfade(self, tmp_path):
        runner = _mock_runner()
        seg1 = _make_recorded_segment(owner="a", name="b", duration=10.0,
                                      video_path=tmp_path / "seg1.webm")
        seg2 = _make_recorded_segment(owner="c", name="d", duration=10.0,
                                      video_path=tmp_path / "seg2.webm")
        (tmp_path / "seg1.webm").touch()
        (tmp_path / "seg2.webm").touch()

        result = compose_video(
            segments=[seg1, seg2],
            output_dir=tmp_path / "out",
            runner=runner,
        )

        assert result.segment_count == 2
        # Duration accounts for 1 transition overlap
        assert result.duration_seconds == pytest.approx(19.0)
        # 2 normalize + 1 compose
        assert runner.call_count == 3

    def test_with_audio(self, tmp_path):
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()
        audio = tmp_path / "audio.mp3"
        audio.touch()

        result = compose_video(
            segments=[seg],
            audio_path=audio,
            output_dir=tmp_path / "out",
            runner=runner,
        )

        assert result.has_audio is True
        # Final compose command should include audio encoding flags
        final_cmd = runner.call_args_list[-1][0][0]
        assert "-c:a" in final_cmd
        assert "aac" in final_cmd

    def test_explicit_output_path(self, tmp_path):
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()
        out = tmp_path / "my_video.mp4"

        result = compose_video(
            segments=[seg],
            output_path=out,
            runner=runner,
        )

        assert result.output_path == out

    def test_three_segments_duration(self, tmp_path):
        runner = _mock_runner()
        segs = [
            _make_recorded_segment(owner="a", name="x", duration=10.0,
                                   video_path=tmp_path / "s1.webm"),
            _make_recorded_segment(owner="b", name="y", duration=10.0,
                                   video_path=tmp_path / "s2.webm"),
            _make_recorded_segment(owner="c", name="z", duration=10.0,
                                   video_path=tmp_path / "s3.webm"),
        ]
        for s in segs:
            s.video_path.touch()

        result = compose_video(
            segments=segs,
            output_dir=tmp_path / "out",
            runner=runner,
        )

        assert result.segment_count == 3
        # 30s - 2 transitions of 1s each = 28s
        assert result.duration_seconds == pytest.approx(28.0)
        # 3 normalize + 1 compose
        assert runner.call_count == 4

    def test_compose_command_has_encode_settings(self, tmp_path):
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        compose_video(segments=[seg], output_dir=tmp_path / "out", runner=runner)

        final_cmd = runner.call_args_list[-1][0][0]
        assert "-preset" in final_cmd
        assert ENCODE_PRESET in final_cmd
        assert "-crf" in final_cmd
        assert str(ENCODE_CRF) in final_cmd
        assert "-pix_fmt" in final_cmd
        assert ENCODE_PIX_FMT in final_cmd
        assert "-movflags" in final_cmd
        assert "+faststart" in final_cmd

    def test_ffmpeg_failure_propagates(self, tmp_path):
        runner = MagicMock()
        runner.side_effect = subprocess.CalledProcessError(
            1, "ffmpeg", stderr="error"
        )
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        with pytest.raises(subprocess.CalledProcessError):
            compose_video(segments=[seg], output_dir=tmp_path / "out", runner=runner)

    def test_custom_transition_duration(self, tmp_path):
        runner = _mock_runner()
        seg1 = _make_recorded_segment(owner="a", name="b", duration=10.0,
                                      video_path=tmp_path / "s1.webm")
        seg2 = _make_recorded_segment(owner="c", name="d", duration=10.0,
                                      video_path=tmp_path / "s2.webm")
        (tmp_path / "s1.webm").touch()
        (tmp_path / "s2.webm").touch()

        result = compose_video(
            segments=[seg1, seg2],
            output_dir=tmp_path / "out",
            runner=runner,
            transition_duration=2.0,
        )

        # 20s - 2s transition = 18s
        assert result.duration_seconds == pytest.approx(18.0)
