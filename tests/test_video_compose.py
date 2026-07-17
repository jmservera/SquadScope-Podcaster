"""Tests for podcaster.video.video_compose module.

Unit tests mock ffmpeg via the CommandRunner protocol.
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcaster.video import video_compose as vc
from podcaster.video.sync_plan import EpisodePlan, RepoReference, VideoSegment
from podcaster.video.video_compose import (
    BOUNDARY_CONTENT_TO_CONTENT,
    BOUNDARY_CONTENT_TO_OUTRO,
    BOUNDARY_INTRO_TO_CONTENT,
    DEFAULT_DOG_LOGO_URL,
    ENCODE_CRF,
    ENCODE_PIX_FMT,
    ENCODE_PRESET,
    INTRO_BLOB_PATH,
    LOWER_THIRD_DURATION,
    MIN_WEEKLY_LEAD_SECONDS,
    OUTPUT_FPS,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    OUTRO_BLOB_PATH,
    TRANSITION_FADE,
    TRANSITION_FADE_BLACK,
    TRANSITION_SLIDE_LEFT,
    TRANSITION_WIPE_LEFT,
    DogLogoConfig,
    LowerThird,
    SyncedSegment,
    _build_audio_overlay_cmd,
    _build_canonical_av_cmd,
    _build_concat_cmd,
    _build_dog_overlay_filter,
    _build_drawtext_filter,
    _build_fit_segment_cmd,
    _build_h264_metadata_cmd,
    _build_intro_dog_cmd,
    _build_normalize_cmd,
    _build_outro_xfade_cmd,
    _build_xfade_filter,
    _compute_lower_thirds,
    _fetch_blob_cached,
    _fetch_intro_outro,
    _fit_target_durations,
    _join_intro_outro,
    _probe_drawtext_ffmpeg,
    _splice_section_cards,
    _trim_first_for_intro,
    apply_sync,
    build_sync_map,
    compose_video,
    select_transitions,
    trim_recording_cmd,
)
from podcaster.video.video_gen import RecordedSegment


@pytest.fixture(autouse=True)
def _stub_drawtext_probe(monkeypatch):
    monkeypatch.setattr(
        "podcaster.video.video_compose._find_drawtext_capable_ffmpeg",
        lambda: "ffmpeg",
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
    runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
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


# --- Tests for _build_fit_segment_cmd ---


class TestBuildFitSegmentCmd:
    def test_forces_exact_duration_with_tpad(self):
        cmd = _build_fit_segment_cmd(Path("/in/clip.webm"), Path("/out/clip.mp4"), 7.5)
        assert cmd[0] == "ffmpeg"
        # An exact output duration is enforced.
        assert "-t" in cmd
        assert cmd[cmd.index("-t") + 1] == "7.500"
        # The vf both normalizes and freeze-extends short clips via tpad clone.
        vf = cmd[cmd.index("-vf") + 1]
        assert f"{OUTPUT_WIDTH}" in vf and f"{OUTPUT_HEIGHT}" in vf
        assert "tpad=stop_mode=clone:stop_duration=7.500" in vf
        assert "-an" in cmd  # video-only

    def test_negative_target_clamped_to_zero(self):
        cmd = _build_fit_segment_cmd(Path("/in/clip.webm"), Path("/out/clip.mp4"), -3.0)
        assert cmd[cmd.index("-t") + 1] == "0.000"


# --- Tests for _fit_target_durations ---


class TestFitTargetDurations:
    def test_scales_to_fill_window_with_overlap(self):
        # Two equal segments, 1s transition overlap, 10s content window.
        # Targets must sum to window + transition*(n-1) = 10 + 1 = 11.
        targets = _fit_target_durations([5.0, 5.0], 10.0, 1.0)
        assert sum(targets) == pytest.approx(11.0)
        assert targets[0] == pytest.approx(5.5)
        assert targets[1] == pytest.approx(5.5)

    def test_preserves_proportions(self):
        # 1:3 ratio is preserved after scaling.
        targets = _fit_target_durations([5.0, 15.0], 20.0, 0.0)
        assert sum(targets) == pytest.approx(20.0)
        assert targets[1] / targets[0] == pytest.approx(3.0)

    def test_single_segment_fills_window_no_overlap(self):
        targets = _fit_target_durations([30.0], 12.0, 1.0)
        assert targets == pytest.approx([12.0])

    def test_floors_below_transition(self):
        # A tiny segment is floored to just above the transition so xfade works.
        targets = _fit_target_durations([0.01, 100.0], 50.0, 1.0)
        assert targets[0] >= 1.5

    def test_zero_source_even_split(self):
        targets = _fit_target_durations([0.0, 0.0], 10.0, 0.0)
        assert targets == pytest.approx([5.0, 5.0])

    def test_window_floored_to_minimum(self):
        # An audio window smaller than the floor still yields a positive target.
        targets = _fit_target_durations([10.0], -5.0, 0.0)
        assert targets[0] > 0


# --- Tests for _trim_first_for_intro (weekly scroll lead-in, #588) ---


class TestTrimFirstForIntro:
    def test_no_intro_is_noop(self):
        assert _trim_first_for_intro(40.0, 0.0) == 40.0

    def test_normal_intro_trims_exactly_keeping_repo_cues(self):
        # Branded 18s intro, 40s weekly bridge -> 22s weekly, identical to the
        # old `max(first - intro, 0)` behaviour (floor does not bind), so every
        # repo stays at its measured audio cue.
        first = 40.0
        intro = 18.0
        trimmed = _trim_first_for_intro(first, intro)
        assert trimmed == pytest.approx(22.0)
        assert trimmed == pytest.approx(max(first - intro, 0.0))

    def test_long_intro_floors_to_minimum_lead_in(self):
        # A pathologically long ~30s title card would zero the 28s bridge; the
        # floor keeps a minimum visible weekly lead-in instead of erasing it.
        trimmed = _trim_first_for_intro(28.0, 30.0)
        assert trimmed == pytest.approx(MIN_WEEKLY_LEAD_SECONDS)
        assert trimmed > 0.0

    def test_floor_never_inflates_a_short_first_segment(self):
        # A first segment naturally shorter than the floor is never grown beyond
        # its own length (which would push the whole timeline).
        first = 5.0
        trimmed = _trim_first_for_intro(first, 30.0)
        assert trimmed == pytest.approx(first)
        assert trimmed <= first

    def test_boundary_exact_floor(self):
        # When the trim lands exactly at the floor, it is preserved.
        trimmed = _trim_first_for_intro(MIN_WEEKLY_LEAD_SECONDS + 10.0, 10.0)
        assert trimmed == pytest.approx(MIN_WEEKLY_LEAD_SECONDS)


# --- Tests for _build_xfade_filter ---


class TestBuildXfadeFilter:
    def test_single_segment_no_filter(self):
        result = _build_xfade_filter([10.0])
        assert result == ""

    def test_two_segments(self):
        # Default: uses select_transitions → "fade" for content_to_content
        result = _build_xfade_filter([10.0, 10.0], transition_duration=1.0)
        assert "xfade" in result
        assert "duration=1.0" in result
        assert "offset=9.000" in result
        assert "[v01]" in result

    def test_explicit_fadeblack(self):
        result = _build_xfade_filter(
            [10.0, 10.0], transition_duration=1.0, transitions=["fadeblack"]
        )
        assert "fadeblack" in result
        assert "offset=9.000" in result

    def test_three_segments(self):
        result = _build_xfade_filter([10.0, 10.0, 10.0], transition_duration=1.0)
        assert result.count("xfade") == 2
        assert "[v01]" in result
        assert "[vout]" in result

    def test_custom_transition_duration(self):
        result = _build_xfade_filter([8.0, 8.0], transition_duration=2.0)
        assert "duration=2.0" in result
        assert "offset=6.000" in result

    def test_explicit_transitions_used(self):
        result = _build_xfade_filter(
            [10.0, 10.0, 10.0],
            transition_duration=1.0,
            transitions=["slideleft", "wipeleft"],
        )
        assert "slideleft" in result
        assert "wipeleft" in result

    def test_transitions_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="transitions length"):
            _build_xfade_filter([10.0, 10.0, 10.0], transitions=["fade"])

    def test_default_transitions_vary_for_four_segments(self):
        # 4 segments → 3 boundaries → should not all be the same transition
        result = _build_xfade_filter([10.0, 10.0, 10.0, 10.0], transition_duration=1.0)
        # The rotation is: fade, slideleft, wipeleft
        assert "fade" in result
        assert "slideleft" in result
        assert "wipeleft" in result


# --- Tests for _build_drawtext_filter ---


class TestBuildDrawtextFilter:
    def test_empty_list(self):
        result = _build_drawtext_filter([])
        assert result == ""

    def test_single_lower_third(self):
        lts = [
            LowerThird(
                text="owner/repo",
                url="https://github.com/owner/repo",
                start_seconds=1.0,
                end_seconds=6.0,
            )
        ]
        result = _build_drawtext_filter(lts, "vout")
        assert "drawtext" in result
        assert "owner/repo" in result
        assert "github.com/owner/repo" in result
        assert "enable=" in result
        assert "[vout]" in result
        assert "[final]" in result

    def test_multiple_lower_thirds(self):
        lts = [
            LowerThird(
                text="a/b", url="https://github.com/a/b", start_seconds=0.5, end_seconds=5.5
            ),
            LowerThird(
                text="c/d", url="https://github.com/c/d", start_seconds=10.0, end_seconds=15.0
            ),
        ]
        result = _build_drawtext_filter(lts, "vout")
        assert result.count("drawtext") == 4  # 2 per lower-third (name + url)
        assert "[lt0]" in result
        assert "[final]" in result

    def test_special_chars_escaped(self):
        lts = [
            LowerThird(
                text="test:repo", url="https://github.com/t/r", start_seconds=0.0, end_seconds=5.0
            )
        ]
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
        # Should have: 1 normalize + 1 compose + 1 h264_metadata BSF
        assert runner.call_count == 3

    def test_two_segments_with_xfade(self, tmp_path):
        runner = _mock_runner()
        seg1 = _make_recorded_segment(
            owner="a", name="b", duration=10.0, video_path=tmp_path / "seg1.webm"
        )
        seg2 = _make_recorded_segment(
            owner="c", name="d", duration=10.0, video_path=tmp_path / "seg2.webm"
        )
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
        # 2 normalize + 1 compose + 1 h264_metadata BSF
        assert runner.call_count == 4

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
        # The audio overlay (penultimate call) re-encodes audio to aac; the
        # final call is the h264_metadata BSF stream-copy pass.
        overlay_cmd = runner.call_args_list[-2][0][0]
        assert "-c:a" in overlay_cmd
        assert "aac" in overlay_cmd
        final_cmd = runner.call_args_list[-1][0][0]
        assert any("h264_metadata" in str(a) for a in final_cmd)

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
            _make_recorded_segment(
                owner="a", name="x", duration=10.0, video_path=tmp_path / "s1.webm"
            ),
            _make_recorded_segment(
                owner="b", name="y", duration=10.0, video_path=tmp_path / "s2.webm"
            ),
            _make_recorded_segment(
                owner="c", name="z", duration=10.0, video_path=tmp_path / "s3.webm"
            ),
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
        # 3 normalize + 2 pairwise xfade passes + 1 h264_metadata BSF
        assert runner.call_count == 6

    def test_many_segments_pairwise_constant_inputs(self, tmp_path):
        """>10 segments compose without a cap; each xfade pass has 2 inputs (#349)."""
        runner = _mock_runner()
        segs = []
        for i in range(18):
            p = tmp_path / f"seg{i:02d}.webm"
            p.touch()
            segs.append(
                _make_recorded_segment(owner="o", name=f"r{i}", duration=10.0, video_path=p)
            )

        result = compose_video(
            segments=segs,
            output_dir=tmp_path / "out",
            runner=runner,
        )

        assert result.segment_count == 18
        # 18 normalize + 17 pairwise xfade passes + 1 h264_metadata BSF
        assert runner.call_count == 18 + 17 + 1

        # Every composition (xfade) pass must use exactly two video inputs so
        # memory stays constant regardless of segment count (no N-way graph).
        xfade_cmds = [
            c.args[0]
            for c in runner.call_args_list
            if "-filter_complex" in c.args[0]
            and "xfade" in c.args[0][c.args[0].index("-filter_complex") + 1]
        ]
        assert len(xfade_cmds) == 17
        for cmd in xfade_cmds:
            assert cmd.count("-i") == 2
            # bt709 colour flags preserved on every encode step
            assert "-colorspace" in cmd and "bt709" in cmd

    def test_compose_command_has_encode_settings(self, tmp_path):
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        compose_video(segments=[seg], output_dir=tmp_path / "out", runner=runner)

        # The encode settings live on the compose pass; the final call is the
        # h264_metadata BSF stream-copy pass.
        final_cmd = runner.call_args_list[-2][0][0]
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
        runner.side_effect = subprocess.CalledProcessError(1, "ffmpeg", stderr="error")
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        with pytest.raises(subprocess.CalledProcessError):
            compose_video(segments=[seg], output_dir=tmp_path / "out", runner=runner)

    def test_custom_transition_duration(self, tmp_path):
        runner = _mock_runner()
        seg1 = _make_recorded_segment(
            owner="a", name="b", duration=10.0, video_path=tmp_path / "s1.webm"
        )
        seg2 = _make_recorded_segment(
            owner="c", name="d", duration=10.0, video_path=tmp_path / "s2.webm"
        )
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

    def test_non_positive_transition_raises(self, tmp_path):
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        with pytest.raises(ValueError, match="transition_duration must be positive"):
            compose_video(
                segments=[seg],
                output_dir=tmp_path / "out",
                runner=_mock_runner(),
                transition_duration=0,
            )

    def test_transition_duration_exceeds_segment_raises(self, tmp_path):
        seg = _make_recorded_segment(duration=5.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        with pytest.raises(ValueError, match="must be less than"):
            compose_video(
                segments=[seg],
                output_dir=tmp_path / "out",
                runner=_mock_runner(),
                transition_duration=5.0,
            )


# --- Tests for drawtext binary detection (#282) ---


class TestProbeDrawtextFfmpeg:
    """Unit tests for _probe_drawtext_ffmpeg — mocks subprocess.run."""

    def _make_proc(
        self, stdout: str = "", stderr: str = "", returncode: int = 0
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def test_returns_first_capable_candidate(self):
        """Returns the first candidate whose -filters output contains 'drawtext'."""
        with patch("podcaster.video.video_compose.subprocess.run") as mock_run:
            mock_run.return_value = self._make_proc(stdout=" VS drawtext ")
            result = _probe_drawtext_ffmpeg(candidates=["/usr/bin/ffmpeg", "ffmpeg"])
        assert result == "/usr/bin/ffmpeg"

    def test_skips_incapable_candidate(self):
        """Skips candidates without drawtext and returns the first capable one."""
        outputs = [
            self._make_proc(stdout="scale, overlay, crop"),  # no drawtext
            self._make_proc(stdout="scale, drawtext, overlay"),
        ]
        with patch("podcaster.video.video_compose.subprocess.run", side_effect=outputs):
            result = _probe_drawtext_ffmpeg(candidates=["/static/ffmpeg", "/usr/bin/ffmpeg"])
        assert result == "/usr/bin/ffmpeg"

    def test_returns_none_when_no_candidate_has_drawtext(self):
        """Returns None when no candidate has drawtext in its filter list."""
        with patch("podcaster.video.video_compose.subprocess.run") as mock_run:
            mock_run.return_value = self._make_proc(stdout="scale, overlay, crop")
            result = _probe_drawtext_ffmpeg(candidates=["/static/ffmpeg"])
        assert result is None

    def test_skips_missing_binary(self):
        """FileNotFoundError for a candidate is silently skipped."""
        outputs = [FileNotFoundError("not found"), self._make_proc(stdout="drawtext")]
        with patch("podcaster.video.video_compose.subprocess.run", side_effect=outputs):
            result = _probe_drawtext_ffmpeg(candidates=["/missing/ffmpeg", "/usr/bin/ffmpeg"])
        assert result == "/usr/bin/ffmpeg"

    def test_drawtext_in_stderr_also_counts(self):
        """drawtext detected from stderr (some ffmpeg versions print there)."""
        with patch("podcaster.video.video_compose.subprocess.run") as mock_run:
            mock_run.return_value = self._make_proc(stderr="drawtext AVOptions")
            result = _probe_drawtext_ffmpeg(candidates=["ffmpeg"])
        assert result == "ffmpeg"

    def test_nonzero_probe_returncode_is_rejected(self):
        """Non-zero probes are rejected even when stderr mentions drawtext."""
        outputs = [
            self._make_proc(stderr="Unknown option drawtext", returncode=1),
            self._make_proc(stdout="drawtext", returncode=0),
        ]
        with patch("podcaster.video.video_compose.subprocess.run", side_effect=outputs):
            result = _probe_drawtext_ffmpeg(candidates=["/bad/ffmpeg", "/usr/bin/ffmpeg"])
        assert result == "/usr/bin/ffmpeg"

    def test_timeout_is_skipped(self):
        """TimeoutExpired for a candidate is silently skipped."""
        outputs = [
            subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=10),
            self._make_proc(stdout="drawtext"),
        ]
        with patch("podcaster.video.video_compose.subprocess.run", side_effect=outputs):
            result = _probe_drawtext_ffmpeg(candidates=["/hung/ffmpeg", "/usr/bin/ffmpeg"])
        assert result == "/usr/bin/ffmpeg"

    def test_empty_candidates_returns_none(self):
        result = _probe_drawtext_ffmpeg(candidates=[])
        assert result is None


class TestComposeVideoDrawtext:
    """Integration-style tests for drawtext detection within compose_video (#282)."""

    def test_drawtext_capable_binary_used_in_compose(self, tmp_path):
        """When _find_drawtext_capable_ffmpeg returns a path, compose cmd uses it."""
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=20.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        with patch(
            "podcaster.video.video_compose._find_drawtext_capable_ffmpeg",
            return_value="/usr/bin/ffmpeg",
        ):
            compose_video(segments=[seg], output_dir=tmp_path / "out", runner=runner)

        # The drawtext-capable binary is used on the compose pass; the final
        # call is the h264_metadata BSF stream-copy pass.
        final_cmd = runner.call_args_list[-2][0][0]
        assert final_cmd[0] == "/usr/bin/ffmpeg"
        # Lower third drawtext overlays must be in the filter_complex
        fc_idx = final_cmd.index("-filter_complex")
        filter_complex = final_cmd[fc_idx + 1]
        assert "drawtext" in filter_complex

    def test_drawtext_skipped_gracefully_when_unavailable(self, tmp_path, caplog):
        """When no drawtext-capable ffmpeg exists, overlays are skipped and video renders."""
        import logging

        runner = _mock_runner()
        seg = _make_recorded_segment(duration=20.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        with patch(
            "podcaster.video.video_compose._find_drawtext_capable_ffmpeg",
            return_value=None,
        ):
            with caplog.at_level(logging.WARNING, logger="podcaster.video.video_compose"):
                result = compose_video(segments=[seg], output_dir=tmp_path / "out", runner=runner)

        # Video must still be produced (no exception)
        assert result.segment_count == 1
        assert result.output_path.suffix == ".mp4"

        # Warning must have been emitted
        assert any("drawtext" in record.message for record in caplog.records)

        # Final compose command must NOT contain drawtext filter expressions
        final_cmd = runner.call_args_list[-1][0][0]
        assert "-filter_complex" not in final_cmd, (
            "drawtext filter_complex should not be in compose cmd"
        )
        assert not any(arg.startswith("drawtext=") for arg in final_cmd)

    def test_no_drawtext_probe_when_no_lower_thirds(self, tmp_path):
        """_find_drawtext_capable_ffmpeg is NOT called when no lower-thirds are needed."""
        runner = _mock_runner()
        # Duration=1.0 with transition_duration=0.1: lt_end=min(5.5, 0.5)=lt_start → no LT
        seg = _make_recorded_segment(duration=1.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        with patch("podcaster.video.video_compose._find_drawtext_capable_ffmpeg") as mock_probe:
            compose_video(
                segments=[seg],
                output_dir=tmp_path / "out",
                runner=runner,
                transition_duration=0.1,
            )

        mock_probe.assert_not_called()


# --- Tests for transition selection (#298) ---


class TestSelectTransitions:
    def test_empty_boundaries(self):
        assert select_transitions(0) == []

    def test_single_content_boundary(self):
        result = select_transitions(1)
        assert result == [TRANSITION_FADE]

    def test_intro_to_content_uses_fadeblack(self):
        result = select_transitions(1, [BOUNDARY_INTRO_TO_CONTENT])
        assert result == [TRANSITION_FADE_BLACK]

    def test_content_to_outro_uses_wipeleft(self):
        result = select_transitions(1, [BOUNDARY_CONTENT_TO_OUTRO])
        assert result == [TRANSITION_WIPE_LEFT]

    def test_three_content_boundaries_cycle(self):
        result = select_transitions(
            3,
            [
                BOUNDARY_CONTENT_TO_CONTENT,
                BOUNDARY_CONTENT_TO_CONTENT,
                BOUNDARY_CONTENT_TO_CONTENT,
            ],
        )
        # Should cycle: fade, slideleft, wipeleft
        assert result[0] == TRANSITION_FADE
        assert result[1] == TRANSITION_SLIDE_LEFT
        assert result[2] == TRANSITION_WIPE_LEFT

    def test_mixed_boundary_kinds(self):
        result = select_transitions(
            3,
            [
                BOUNDARY_INTRO_TO_CONTENT,
                BOUNDARY_CONTENT_TO_CONTENT,
                BOUNDARY_CONTENT_TO_OUTRO,
            ],
        )
        assert result[0] == TRANSITION_FADE_BLACK
        assert result[1] == TRANSITION_FADE  # first content rotation slot
        assert result[2] == TRANSITION_WIPE_LEFT

    def test_four_content_boundaries_wrap(self):
        # 4 content boundaries → should wrap around rotation
        result = select_transitions(4)
        # Rotation: fade, slideleft, wipeleft, slideright
        assert len(set(result)) > 1  # not all the same

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="boundary_kinds length"):
            select_transitions(3, [BOUNDARY_CONTENT_TO_CONTENT, BOUNDARY_CONTENT_TO_CONTENT])


class TestComposeVideoTransitions:
    """Tests for transition selection integration in compose_video (#298)."""

    def test_default_transitions_vary_with_four_segments(self, tmp_path):
        """compose_video with 4 segments should use varied xfade transitions."""
        runner = _mock_runner()
        segs = []
        for i in range(4):
            p = tmp_path / f"seg{i}.webm"
            p.touch()
            segs.append(_make_recorded_segment(duration=10.0, video_path=p))

        with patch(
            "podcaster.video.video_compose._find_drawtext_capable_ffmpeg",
            return_value=None,
        ):
            compose_video(
                segments=segs,
                output_dir=tmp_path / "out",
                runner=runner,
                transition_duration=1.0,
            )

        # Pairwise composition runs one xfade pass per boundary; collect the
        # transitions across every filter_complex pass.
        all_calls = [call[0][0] for call in runner.call_args_list]
        xfade_filters = " ".join(
            c[c.index("-filter_complex") + 1]
            for c in all_calls
            if "-filter_complex" in c and "xfade" in c[c.index("-filter_complex") + 1]
        )
        # With 4 segments, should see at least 2 distinct transition types
        assert "fade" in xfade_filters
        assert "slideleft" in xfade_filters

    def test_boundary_kinds_passed_through(self, tmp_path):
        """boundary_kinds parameter changes the transitions used."""
        runner = _mock_runner()
        segs = []
        for i in range(2):
            p = tmp_path / f"s{i}.webm"
            p.touch()
            segs.append(_make_recorded_segment(duration=10.0, video_path=p))

        with patch(
            "podcaster.video.video_compose._find_drawtext_capable_ffmpeg",
            return_value=None,
        ):
            compose_video(
                segments=segs,
                output_dir=tmp_path / "out",
                runner=runner,
                transition_duration=1.0,
                boundary_kinds=[BOUNDARY_INTRO_TO_CONTENT],
            )

        all_calls = [call[0][0] for call in runner.call_args_list]
        compose_cmd = all_calls[-2]
        fc_idx = compose_cmd.index("-filter_complex")
        assert "fadeblack" in compose_cmd[fc_idx + 1]

    def test_content_to_outro_uses_wipeleft(self, tmp_path):
        """content_to_outro boundary uses wipeleft transition."""
        runner = _mock_runner()
        segs = []
        for i in range(2):
            p = tmp_path / f"s{i}.webm"
            p.touch()
            segs.append(_make_recorded_segment(duration=10.0, video_path=p))

        with patch(
            "podcaster.video.video_compose._find_drawtext_capable_ffmpeg",
            return_value=None,
        ):
            compose_video(
                segments=segs,
                output_dir=tmp_path / "out",
                runner=runner,
                transition_duration=1.0,
                boundary_kinds=[BOUNDARY_CONTENT_TO_OUTRO],
            )

        all_calls = [call[0][0] for call in runner.call_args_list]
        compose_cmd = all_calls[-2]
        fc_idx = compose_cmd.index("-filter_complex")
        assert "wipeleft" in compose_cmd[fc_idx + 1]


# --- Tests for sync-map utilities (#296) ---


def _make_plan(*items: tuple[str, str, float, float]) -> EpisodePlan:
    """Build an EpisodePlan from (owner, name, start, duration) tuples."""
    segs = tuple(
        VideoSegment(
            repo=RepoReference(owner=owner, name=name),
            start_seconds=start,
            duration_seconds=dur,
        )
        for owner, name, start, dur in items
    )
    total = sum(s.start_seconds + s.duration_seconds for s in segs[-1:])
    return EpisodePlan(total_duration_seconds=total or 0.0, segments=segs)


class TestSyncedSegment:
    def _make(self, rec_dur: float, target_dur: float) -> SyncedSegment:
        rec = _make_recorded_segment(duration=rec_dur)
        return SyncedSegment(
            recorded=rec,
            target_start_seconds=10.0,
            target_duration_seconds=target_dur,
        )

    def test_needs_trim_when_recording_longer(self):
        ss = self._make(rec_dur=20.0, target_dur=10.0)
        assert ss.needs_trim is True

    def test_no_trim_when_within_tolerance(self):
        # recording is only 0.05 s longer — within the 0.1 s tolerance
        ss = self._make(rec_dur=10.05, target_dur=10.0)
        assert ss.needs_trim is False

    def test_no_trim_when_recording_shorter(self):
        ss = self._make(rec_dur=8.0, target_dur=10.0)
        assert ss.needs_trim is False

    def test_needs_trim_exactly_at_boundary(self):
        # recording is exactly 0.1 s over — NOT a trim (boundary is strictly >0.1)
        ss = self._make(rec_dur=10.1, target_dur=10.0)
        assert ss.needs_trim is False


class TestBuildSyncMap:
    def _repo_url(self, owner: str, name: str) -> str:
        return f"https://github.com/{owner}/{name}"

    def _rec(self, owner: str, name: str, dur: float = 10.0) -> RecordedSegment:
        seg = VideoSegment(
            repo=RepoReference(owner=owner, name=name),
            start_seconds=0.0,
            duration_seconds=dur,
        )
        return RecordedSegment(segment=seg, video_path=Path(f"/recs/{name}.webm"))

    def _plan_from_recs(self, *recs: tuple[str, str, float, float]) -> EpisodePlan:
        segs = tuple(
            VideoSegment(
                repo=RepoReference(owner=owner, name=name),
                start_seconds=start,
                duration_seconds=dur,
            )
            for owner, name, start, dur in recs
        )
        total = segs[-1].start_seconds + segs[-1].duration_seconds if segs else 0.0
        return EpisodePlan(total_duration_seconds=total, segments=segs)

    def test_matches_recordings_to_plan(self):
        plan = self._plan_from_recs(
            ("a", "repo1", 0.0, 30.0),
            ("a", "repo2", 30.0, 30.0),
        )
        recs = [self._rec("a", "repo1"), self._rec("a", "repo2")]
        sync_map = build_sync_map(plan, recs)
        assert len(sync_map) == 2
        assert sync_map[0].recorded.segment.repo.url == self._repo_url("a", "repo1")
        assert sync_map[0].target_start_seconds == 0.0
        assert sync_map[1].target_start_seconds == 30.0

    def test_raises_on_missing_recording(self):
        plan = self._plan_from_recs(("x", "missing", 0.0, 30.0))
        with pytest.raises(ValueError, match="No recording found"):
            build_sync_map(plan, [])

    def test_extra_recordings_are_ignored(self):
        plan = self._plan_from_recs(("a", "used", 0.0, 10.0))
        recs = [self._rec("a", "used"), self._rec("a", "extra")]
        sync_map = build_sync_map(plan, recs)
        assert len(sync_map) == 1
        assert sync_map[0].recorded.segment.repo.name == "used"

    def test_plan_order_preserved(self):
        plan = self._plan_from_recs(
            ("a", "first", 0.0, 20.0),
            ("a", "second", 20.0, 20.0),
            ("a", "third", 40.0, 20.0),
        )
        recs = [
            self._rec("a", "third"),
            self._rec("a", "first"),
            self._rec("a", "second"),
        ]
        sync_map = build_sync_map(plan, recs)
        names = [ss.recorded.segment.repo.name for ss in sync_map]
        assert names == ["first", "second", "third"]


class TestTrimRecordingCmd:
    def test_basic_structure(self):
        cmd = trim_recording_cmd(
            input_path=Path("/in/video.webm"),
            start_seconds=0.0,
            duration_seconds=15.0,
            output_path=Path("/out/trimmed.webm"),
        )
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert "/in/video.webm" in cmd
        assert "/out/trimmed.webm" in cmd
        assert "-ss" in cmd
        assert "-t" in cmd
        assert "-c" in cmd
        assert "copy" in cmd

    def test_custom_ffmpeg_bin(self):
        cmd = trim_recording_cmd(
            Path("/in/v.webm"), 1.0, 5.0, Path("/out/v.webm"), "/usr/bin/ffmpeg"
        )
        assert cmd[0] == "/usr/bin/ffmpeg"

    def test_duration_formatted(self):
        cmd = trim_recording_cmd(Path("/a.webm"), 0.0, 7.5, Path("/b.webm"))
        t_idx = cmd.index("-t")
        assert cmd[t_idx + 1] == "7.500"

    def test_start_seconds_formatted(self):
        cmd = trim_recording_cmd(Path("/a.webm"), 2.123, 5.0, Path("/b.webm"))
        ss_idx = cmd.index("-ss")
        assert cmd[ss_idx + 1] == "2.123"


class TestApplySync:
    def _make_rec(self, name: str, dur: float, path: Path) -> RecordedSegment:
        seg = VideoSegment(
            repo=RepoReference(owner="test", name=name),
            start_seconds=0.0,
            duration_seconds=dur,
        )
        return RecordedSegment(segment=seg, video_path=path)

    def _make_ss(
        self,
        name: str,
        rec_dur: float,
        target_start: float,
        target_dur: float,
        path: Path,
    ) -> SyncedSegment:
        return SyncedSegment(
            recorded=self._make_rec(name, rec_dur, path),
            target_start_seconds=target_start,
            target_duration_seconds=target_dur,
        )

    def test_trims_when_needed(self, tmp_path):
        runner = _mock_runner()
        src = tmp_path / "input.webm"
        src.touch()
        ss = self._make_ss("repo", rec_dur=30.0, target_start=10.0, target_dur=10.0, path=src)
        result = apply_sync([ss], output_dir=tmp_path / "out", runner=runner)

        assert len(result) == 1
        runner.assert_called_once()
        cmd = runner.call_args[0][0]
        assert "-t" in cmd
        assert result[0].segment.start_seconds == 10.0
        assert result[0].segment.duration_seconds == pytest.approx(10.0)

    def test_no_trim_when_within_tolerance(self, tmp_path):
        runner = _mock_runner()
        src = tmp_path / "input.webm"
        src.touch()
        ss = self._make_ss("repo", rec_dur=10.05, target_start=5.0, target_dur=10.0, path=src)
        result = apply_sync([ss], output_dir=tmp_path / "out", runner=runner)

        runner.assert_not_called()
        assert result[0].video_path == src
        assert result[0].segment.start_seconds == 5.0

    def test_creates_output_dir(self, tmp_path):
        runner = _mock_runner()
        output_dir = tmp_path / "nested" / "output"
        src = tmp_path / "v.webm"
        src.touch()
        ss = self._make_ss("r", rec_dur=20.0, target_start=0.0, target_dur=5.0, path=src)
        apply_sync([ss], output_dir=output_dir, runner=runner)
        assert output_dir.is_dir()

    def test_multiple_segments(self, tmp_path):
        runner = _mock_runner()
        src1 = tmp_path / "a.webm"
        src2 = tmp_path / "b.webm"
        src1.touch()
        src2.touch()
        ss1 = self._make_ss("a", 20.0, 0.0, 10.0, src1)  # needs trim
        ss2 = self._make_ss("b", 8.0, 10.0, 10.0, src2)  # no trim
        result = apply_sync([ss1, ss2], output_dir=tmp_path / "out", runner=runner)
        assert len(result) == 2
        assert runner.call_count == 1  # only ss1 triggered a trim
        assert result[0].segment.start_seconds == 0.0
        assert result[1].segment.start_seconds == 10.0


# --- Tests for reusable intro/outro integration (#319) ---


class _FakeStorage:
    """Minimal StorageBackend stub returning canned bytes for blob paths."""

    def __init__(self, blobs: dict[str, bytes] | None = None, *, raise_on=None):
        self._blobs = blobs or {}
        self._raise_on = raise_on or set()
        self.calls: list[str] = []

    def get_bytes(self, path: str) -> bytes | None:
        self.calls.append(path)
        if path in self._raise_on:
            raise RuntimeError("boom")
        return self._blobs.get(path)


def _ffprobe_runner(has_audio: bool = True, duration: float = 6.0) -> MagicMock:
    """A runner that answers ffprobe with JSON and other commands with success."""
    runner = MagicMock()

    def _side_effect(command):
        if command and command[0] == "ffprobe":
            streams = [{"codec_type": "video"}]
            if has_audio:
                streams.append({"codec_type": "audio"})
            payload = (
                '{"streams": '
                + str(streams).replace("'", '"')
                + ', "format": {"duration": "'
                + str(duration)
                + '"}}'
            )
            return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    runner.side_effect = _side_effect
    return runner


class TestJoinIntroOutroCrossfade:
    """content→outro crossfade and its short-clip fallback (issue #393)."""

    def test_build_outro_xfade_cmd_uses_xfade_filter(self, tmp_path):
        cmd = _build_outro_xfade_cmd(
            tmp_path / "content.mp4",
            tmp_path / "outro.mp4",
            TRANSITION_FADE,
            1.0,
            4.0,
            tmp_path / "out.mp4",
        )
        joined = " ".join(cmd)
        assert "xfade" in joined
        assert "transition=fade" in joined
        assert "duration=1.0" in joined
        assert "offset=4.000" in joined
        # Video-only: the podcast MP3 is overlaid later, so audio is never cut.
        assert "-an" in cmd

    def test_crossfades_content_into_outro(self, tmp_path):
        runner = _ffprobe_runner(has_audio=False, duration=5.0)
        content = tmp_path / "content.mp4"
        content.touch()
        outro = tmp_path / "outro.mp4"
        outro.touch()
        out = tmp_path / "joined.mp4"

        added = _join_intro_outro(
            content,
            out,
            intro_path=None,
            outro_path=outro,
            run=runner,
            work_dir=tmp_path / "work",
            transition_duration=1.0,
        )

        cmds = [c.args[0] for c in runner.call_args_list]
        xfade_cmds = [c for c in cmds if "xfade" in " ".join(c)]
        assert len(xfade_cmds) == 1
        # outro (5s) added minus the 1s crossfade overlap.
        assert added == pytest.approx(4.0)

    def test_falls_back_to_hard_cut_when_outro_too_short(self, tmp_path):
        # Outro shorter than the transition cannot be crossfaded safely.
        runner = _ffprobe_runner(has_audio=False, duration=0.5)
        content = tmp_path / "content.mp4"
        content.touch()
        outro = tmp_path / "outro.mp4"
        outro.touch()
        out = tmp_path / "joined.mp4"

        added = _join_intro_outro(
            content,
            out,
            intro_path=None,
            outro_path=outro,
            run=runner,
            work_dir=tmp_path / "work",
            transition_duration=1.0,
        )

        cmds = [c.args[0] for c in runner.call_args_list]
        assert not any("xfade" in " ".join(c) for c in cmds)
        concat_cmds = [c for c in cmds if "concat" in c]
        assert len(concat_cmds) == 1
        # Hard cut: full outro duration is added, no overlap subtracted.
        assert added == pytest.approx(0.5)

    def test_downloads_and_caches(self, tmp_path):
        storage = _FakeStorage({INTRO_BLOB_PATH: b"intro-bytes"})
        cache = tmp_path / "intro.mp4"
        result = _fetch_blob_cached(storage, INTRO_BLOB_PATH, cache, "intro")
        assert result == cache
        assert cache.read_bytes() == b"intro-bytes"
        assert storage.calls == [INTRO_BLOB_PATH]

    def test_reuses_existing_cache_no_redownload(self, tmp_path):
        storage = _FakeStorage({INTRO_BLOB_PATH: b"fresh"})
        cache = tmp_path / "intro.mp4"
        cache.write_bytes(b"cached")
        result = _fetch_blob_cached(storage, INTRO_BLOB_PATH, cache, "intro")
        assert result == cache
        # Existing non-empty cache is reused; storage is not queried.
        assert storage.calls == []
        assert cache.read_bytes() == b"cached"

    def test_missing_blob_returns_none(self, tmp_path):
        storage = _FakeStorage({})
        cache = tmp_path / "outro.mp4"
        result = _fetch_blob_cached(storage, OUTRO_BLOB_PATH, cache, "outro")
        assert result is None
        assert not cache.exists()

    def test_fetch_error_returns_none(self, tmp_path):
        storage = _FakeStorage({}, raise_on={INTRO_BLOB_PATH})
        cache = tmp_path / "intro.mp4"
        result = _fetch_blob_cached(storage, INTRO_BLOB_PATH, cache, "intro")
        assert result is None


class TestFetchIntroOutro:
    def test_returns_both_when_present(self, tmp_path):
        storage = _FakeStorage({INTRO_BLOB_PATH: b"i", OUTRO_BLOB_PATH: b"o"})
        intro, outro = _fetch_intro_outro(storage, tmp_path)
        assert intro is not None and intro.read_bytes() == b"i"
        assert outro is not None and outro.read_bytes() == b"o"

    def test_partial_availability(self, tmp_path):
        storage = _FakeStorage({INTRO_BLOB_PATH: b"i"})
        intro, outro = _fetch_intro_outro(storage, tmp_path)
        assert intro is not None
        assert outro is None


class TestBuildCanonicalAvCmd:
    def test_with_audio_maps_source_audio(self):
        cmd = _build_canonical_av_cmd(Path("/in.mp4"), Path("/out.mp4"), has_audio=True)
        assert cmd[0] == "ffmpeg"
        assert "0:a:0" in cmd
        assert "anullsrc" not in " ".join(cmd)
        assert "-shortest" not in cmd
        assert f"{OUTPUT_WIDTH}:{OUTPUT_HEIGHT}" in cmd[cmd.index("-filter_complex") + 1]

    def test_without_audio_synthesizes_silence(self):
        cmd = _build_canonical_av_cmd(Path("/in.mp4"), Path("/out.mp4"), has_audio=False)
        joined = " ".join(cmd)
        assert "anullsrc" in joined
        assert "1:a" in cmd
        assert "-shortest" in cmd


class TestBuildConcatCmd:
    def test_uses_concat_demuxer_copy(self, tmp_path):
        cmd = _build_concat_cmd(tmp_path / "list.txt", tmp_path / "out.mp4")
        assert "concat" in cmd
        assert "-safe" in cmd
        assert "copy" in cmd


class TestBuildH264MetadataCmd:
    def test_normalizes_color_metadata_stream_copy(self, tmp_path):
        cmd = _build_h264_metadata_cmd(tmp_path / "in.mp4", tmp_path / "out.mp4")
        assert cmd[0] == "ffmpeg"
        joined = " ".join(cmd)
        # video and audio are stream-copied (no re-encode)
        assert "-c:v" in cmd and "copy" in cmd
        assert "-c:a" in cmd
        # h264_metadata BSF forces consistent BT.709 VUI for Spotify
        assert "-bsf:v" in cmd
        assert (
            "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
            "matrix_coefficients=1:video_full_range_flag=0" in joined
        )
        assert "+faststart" in cmd
        assert str(tmp_path / "in.mp4") in cmd
        assert cmd[-1] == str(tmp_path / "out.mp4")


class TestEncodeConfigurability:
    """Codec/CRF/pixel-format are env-configurable for quality tuning (#376)."""

    def test_default_h264_high_profile_yuv420p_crf12(self):
        # Defaults: H.264 High profile, yuv420p (Spotify-mandated 4:2:0),
        # near-lossless CRF for screen content.
        args = vc._video_encode_args("slow")
        assert "-c:v" in args and "libx264" in args
        assert args[args.index("-crf") + 1] == "12"
        assert args[args.index("-pix_fmt") + 1] == "yuv420p"
        assert "-profile:v" in args and "high" in args

    def test_metadata_bsf_spec_h264_by_default(self):
        spec = vc._metadata_bsf_spec()
        assert spec.startswith("h264_metadata=")
        assert "colour_primaries=1" in spec
        assert "transfer_characteristics=1" in spec
        assert "matrix_coefficients=1" in spec
        assert "video_full_range_flag=0" in spec

    def test_hevc_env_switches_codec_crf_and_bsf(self, monkeypatch):
        # Switching the encoder to HEVC must flip the codec, the (higher) default
        # CRF, and the metadata bitstream filter to hevc_metadata.
        monkeypatch.setenv("VIDEO_ENCODE_VCODEC", "libx265")
        monkeypatch.delenv("VIDEO_ENCODE_CRF", raising=False)
        reloaded = importlib.reload(vc)
        try:
            assert reloaded.ENCODE_VCODEC == "libx265"
            assert reloaded.ENCODE_CRF == 18
            args = reloaded._video_encode_args("slow")
            assert "libx265" in args
            # No H.264-only High profile flag for HEVC.
            assert "-profile:v" not in args
            assert reloaded._metadata_bsf_spec().startswith("hevc_metadata=")
        finally:
            monkeypatch.delenv("VIDEO_ENCODE_VCODEC", raising=False)
            importlib.reload(reloaded)

    def test_crf_env_override(self, monkeypatch):
        monkeypatch.setenv("VIDEO_ENCODE_CRF", "10")
        reloaded = importlib.reload(vc)
        try:
            assert reloaded.ENCODE_CRF == 10
        finally:
            monkeypatch.delenv("VIDEO_ENCODE_CRF", raising=False)
            importlib.reload(reloaded)

    def test_overlays_audio_as_sole_track(self, tmp_path):
        cmd = _build_audio_overlay_cmd(
            tmp_path / "video.mp4", tmp_path / "audio.mp3", tmp_path / "out.mp4"
        )
        assert cmd[0] == "ffmpeg"
        # video copied, audio re-encoded to aac
        assert "copy" in cmd
        assert "aac" in cmd
        # audio is mapped from the 2nd input
        assert "0:v:0" in cmd
        assert "1:a:0" in cmd
        # audio is NEVER truncated: no -shortest in audio overlay
        assert "-shortest" not in cmd
        assert "-t" not in cmd
        assert str(tmp_path / "audio.mp3") in cmd
        assert cmd[-1] == str(tmp_path / "out.mp4")

    def test_extends_video_when_audio_is_longer(self, tmp_path):
        cmd = _build_audio_overlay_cmd(
            tmp_path / "video.mp4",
            tmp_path / "audio.mp3",
            tmp_path / "out.mp4",
            video_duration=10.0,
            audio_duration=15.0,
        )
        joined = " ".join(cmd)
        # video is re-encoded (not copied) so it can be padded + faded
        assert "-c:v" in cmd and "libx264" in cmd
        assert "copy" not in cmd
        # final frame held for 5s then faded to black over the last 2s of the
        # held region (fade starts where the original video ends)
        assert "tpad=stop_mode=clone:stop_duration=5.000" in joined
        assert "fade=t=out:st=10.000:d=2.000" in joined
        # explicit bt709 color flags for Spotify consistency
        assert "-colorspace" in cmd and "bt709" in cmd
        assert "-shortest" not in cmd

    def test_copies_video_when_video_is_longer(self, tmp_path):
        cmd = _build_audio_overlay_cmd(
            tmp_path / "video.mp4",
            tmp_path / "audio.mp3",
            tmp_path / "out.mp4",
            video_duration=20.0,
            audio_duration=15.0,
        )
        assert "copy" in cmd
        assert "tpad" not in " ".join(cmd)
        assert "-shortest" not in cmd
        # Audio shorter than video is padded with silence to the full video
        # length plus a small safety margin so a frame-span under-report never
        # leaves audio < video (issues #353, #549).
        joined = " ".join(cmd)
        assert "-af" in cmd
        assert "apad=whole_dur=20.100" in joined

    def test_no_audio_pad_when_audio_is_longer(self, tmp_path):
        cmd = _build_audio_overlay_cmd(
            tmp_path / "video.mp4",
            tmp_path / "audio.mp3",
            tmp_path / "out.mp4",
            video_duration=10.0,
            audio_duration=15.0,
        )
        assert "apad" not in " ".join(cmd)

    def test_no_audio_pad_without_durations(self, tmp_path):
        cmd = _build_audio_overlay_cmd(
            tmp_path / "video.mp4", tmp_path / "audio.mp3", tmp_path / "out.mp4"
        )
        assert "apad" not in " ".join(cmd)

    def test_fade_clamped_to_short_padding(self, tmp_path):
        # Padding (1s) is shorter than the 2s fade window: the fade must start
        # where the original video ends and last only as long as the padding so
        # it never bleeds into the real footage.
        cmd = _build_audio_overlay_cmd(
            tmp_path / "video.mp4",
            tmp_path / "audio.mp3",
            tmp_path / "out.mp4",
            video_duration=10.0,
            audio_duration=11.0,
        )
        joined = " ".join(cmd)
        assert "tpad=stop_mode=clone:stop_duration=1.000" in joined
        assert "fade=t=out:st=10.000:d=1.000" in joined


class TestComposeVideoContentVideoOnly:
    def test_content_composed_without_audio(self, tmp_path):
        runner = _ffprobe_runner(has_audio=True, duration=12.0)
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        audio = tmp_path / "audio.mp3"
        audio.touch()
        out = tmp_path / "out" / "episode.mp4"

        result = compose_video(
            segments=[seg],
            audio_path=audio,
            output_path=out,
            runner=runner,
        )

        cmds = [c.args[0] for c in runner.call_args_list]
        # The content composition writes content.mp4 video-only (-an, no audio map)
        compose_cmd = next(c for c in cmds if c[-1].endswith("content.mp4"))
        assert "-an" in compose_cmd
        assert str(audio) not in compose_cmd
        # The penultimate command overlays the podcast MP3 onto the content
        overlay_cmd = cmds[-2]
        assert str(audio) in overlay_cmd
        assert "-shortest" not in overlay_cmd
        assert overlay_cmd[-1].endswith("muxed.mp4")
        # The final command is the h264_metadata BSF pass writing the output
        final_cmd = cmds[-1]
        assert any("h264_metadata" in str(a) for a in final_cmd)
        assert final_cmd[-1] == str(out)
        # Reported duration is the full (untruncated) length; the runner probes
        # both audio and video as 12.0s here.
        assert result.has_audio is True
        assert result.duration_seconds == pytest.approx(12.0)

    def test_bookends_and_audio_overlay_on_joined_video(self, tmp_path):
        # _ffprobe_runner answers every probe with the same duration, so intro,
        # outro and audio all report 5.0s here.
        runner = _ffprobe_runner(has_audio=True, duration=5.0)
        storage = _FakeStorage({INTRO_BLOB_PATH: b"intro", OUTRO_BLOB_PATH: b"outro"})
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        audio = tmp_path / "audio.mp3"
        audio.touch()
        out = tmp_path / "out" / "episode.mp4"

        result = compose_video(
            segments=[seg],
            audio_path=audio,
            output_path=out,
            runner=runner,
            storage=storage,
            intro_outro_cache_dir=tmp_path / "cache",
        )

        cmds = [c.args[0] for c in runner.call_args_list]
        # intro/content/outro joined video-only into joined.mp4
        concat_cmds = [c for c in cmds if "concat" in c and c[-1].endswith("joined.mp4")]
        assert len(concat_cmds) == 1
        # canonicalized clips all generate a silent track (has_audio=False):
        # intro, content, outro, plus the re-canonicalised content+outro
        # crossfade clip (issue #393) = 4.
        canon_cmds = [
            c
            for c in cmds
            if "-filter_complex" in c and "join" in c[-1] and "anullsrc" in " ".join(c)
        ]
        assert len(canon_cmds) == 4
        assert all("anullsrc" in " ".join(c) for c in canon_cmds)
        # content->outro is joined with a crossfade (issue #393), not a hard cut.
        xfade_cmds = [c for c in cmds if "xfade" in " ".join(c)]
        assert len(xfade_cmds) == 1
        assert "transition=fade" in " ".join(xfade_cmds[0])
        # penultimate command overlays the podcast MP3 on the joined video
        overlay_cmd = cmds[-2]
        assert str(audio) in overlay_cmd
        assert overlay_cmd[-1].endswith("muxed.mp4")
        # final command is the h264_metadata BSF pass writing the output
        final_cmd = cmds[-1]
        assert any("h264_metadata" in str(a) for a in final_cmd)
        assert final_cmd[-1] == str(out)
        # video duration = 10 (content) + 5 + 5 (bookends); audio probes as 5.0s.
        # The runner mocks every probe at 5.0s, so the reported (untruncated)
        # length resolves to 5.0s here.
        assert result.duration_seconds == pytest.approx(5.0)


class TestComposeVideoIntroOutro:
    def test_no_storage_skips_intro_outro(self, tmp_path):
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        compose_video(segments=[seg], output_dir=tmp_path / "out", runner=runner)
        # 1 normalize + 1 compose + 1 h264_metadata BSF, no ffprobe/concat
        assert runner.call_count == 3
        assert all(c.args[0][0] != "ffprobe" for c in runner.call_args_list)

    def test_prepends_intro_and_appends_outro(self, tmp_path):
        runner = _ffprobe_runner(has_audio=True, duration=5.0)
        storage = _FakeStorage({INTRO_BLOB_PATH: b"intro", OUTRO_BLOB_PATH: b"outro"})
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        out = tmp_path / "out" / "episode.mp4"

        result = compose_video(
            segments=[seg],
            audio_path=None,
            output_path=out,
            runner=runner,
            storage=storage,
            intro_outro_cache_dir=tmp_path / "cache",
        )

        cmds = [c.args[0] for c in runner.call_args_list]
        # content composed to a temp file, not directly to output
        compose_cmd = next(c for c in cmds if c[0] in ("ffmpeg",) and "content.mp4" in c[-1])
        assert compose_cmd[-1].endswith("content.mp4")
        # a concat command joins intro + (content⨯outro crossfade) into
        # joined.mp4 (video-only)
        concat_cmds = [c for c in cmds if "concat" in c and c[-1].endswith("joined.mp4")]
        assert len(concat_cmds) == 1
        # content→outro is crossfaded with the same xfade filter used between
        # content segments (issue #393).
        xfade_cmds = [c for c in cmds if "xfade" in " ".join(c)]
        assert len(xfade_cmds) == 1
        assert "transition=fade" in " ".join(xfade_cmds[0])
        # the final h264_metadata BSF pass writes the real output
        final_cmd = cmds[-1]
        assert any("h264_metadata" in str(a) for a in final_cmd)
        assert final_cmd[-1] == str(out)
        # three ffprobe calls (intro + outro + content, the latter to compute
        # the crossfade offset)
        probe_cmds = [c for c in cmds if c[0] == "ffprobe"]
        assert len(probe_cmds) == 3
        # canonical re-encodes for intro, content, outro and the crossfaded
        # content+outro clip (4 canonicalize calls)
        canon_cmds = [
            c
            for c in cmds
            if "-filter_complex" in c and "anullsrc" in " ".join(c) and "join" in c[-1]
        ]
        assert len(canon_cmds) == 4
        # duration = 10s content + 5s intro + 5s outro, minus the 1s crossfade
        # overlap between content and outro (issue #393).
        assert result.duration_seconds == pytest.approx(19.0)

    def test_missing_intro_outro_graceful_fallback(self, tmp_path):
        runner = _mock_runner()
        storage = _FakeStorage({})  # neither intro nor outro present
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        out = tmp_path / "out" / "episode.mp4"

        result = compose_video(
            segments=[seg],
            output_path=out,
            runner=runner,
            storage=storage,
            intro_outro_cache_dir=tmp_path / "cache",
        )

        cmds = [c.args[0] for c in runner.call_args_list]
        # No concat, no ffprobe — behaves like the plain path.
        assert not any("concat" in c for c in cmds)
        assert not any(c[0] == "ffprobe" for c in cmds)
        # content written straight to the final output
        assert result.output_path == out
        assert result.duration_seconds == pytest.approx(10.0)

    def test_only_intro_available(self, tmp_path):
        runner = _ffprobe_runner(has_audio=False, duration=5.0)
        storage = _FakeStorage({INTRO_BLOB_PATH: b"intro"})
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        out = tmp_path / "out" / "episode.mp4"

        result = compose_video(
            segments=[seg],
            output_path=out,
            runner=runner,
            storage=storage,
            intro_outro_cache_dir=tmp_path / "cache",
        )

        cmds = [c.args[0] for c in runner.call_args_list]
        concat_cmds = [c for c in cmds if "concat" in c]
        assert len(concat_cmds) == 1
        # only the intro is probed
        probe_cmds = [c for c in cmds if c[0] == "ffprobe"]
        assert len(probe_cmds) == 1
        assert result.duration_seconds == pytest.approx(15.0)


class TestComposeVideoFitToWindow:
    """Fit content to the audio timeline minus intro/outro bumpers (#355)."""

    def test_segments_fit_to_window_with_bookends(self, tmp_path):
        # Every probe (intro, outro, audio, video) reports 5.0s.  With a 30s
        # audio duration and 5s intro + 5s outro, the content window is 20s.
        runner = _ffprobe_runner(has_audio=True, duration=5.0)
        storage = _FakeStorage({INTRO_BLOB_PATH: b"intro", OUTRO_BLOB_PATH: b"outro"})
        s1 = tmp_path / "a.webm"
        s2 = tmp_path / "b.webm"
        s1.touch()
        s2.touch()
        segs = [
            _make_recorded_segment(owner="a", name="b", duration=50.0, video_path=s1),
            _make_recorded_segment(owner="c", name="d", duration=50.0, video_path=s2),
        ]
        out = tmp_path / "out" / "episode.mp4"

        compose_video(
            segments=segs,
            audio_path=tmp_path / "audio.mp3",
            output_path=out,
            runner=runner,
            storage=storage,
            intro_outro_cache_dir=tmp_path / "cache",
            audio_duration=30.0,
        )
        (tmp_path / "audio.mp3").touch()

        cmds = [c.args[0] for c in runner.call_args_list]
        # Each segment is fit (tpad + -t), not plain-normalized.
        fit_cmds = [
            c
            for c in cmds
            if "-t" in c
            and any("tpad=stop_mode=clone" in str(a) for a in c)
            and c[-1].endswith(".mp4")
            and "seg_" in c[-1]
        ]
        assert len(fit_cmds) == 2
        # Targets sum to content_window + transition*(n-1) = 20 + 1 = 21,
        # split evenly across two equal segments → 10.5s each.
        targets = [float(c[c.index("-t") + 1]) for c in fit_cmds]
        assert sum(targets) == pytest.approx(21.0)
        assert all(t == pytest.approx(10.5) for t in targets)

    def test_first_last_segments_trimmed_for_audio_sync(self, tmp_path):
        # Sync regression (issue #544): with transition=0 the content maps the
        # audio window [intro, audio-outro] at 1:1.  The plan tiles the FULL
        # audio timeline, so the first segment's intro span and the last
        # segment's outro span must be trimmed before fitting — otherwise the
        # asymmetric segments are proportionally squeezed and drift off their
        # spoken mention.  Audio=30, intro=5, outro=5 -> content window 20.
        # Plan: seg0 covers audio [0,20] (20s), seg1 covers [20,30] (10s).
        # After trim: seg0=15, seg1=5 (sum 20 = window) -> kept ~1:1, NOT the
        # proportional 13.6/6.8 the old scale-only path produced.  A small
        # transition (0.5s) adds a uniform xfade-compensation stretch.
        runner = _ffprobe_runner(has_audio=True, duration=5.0)
        storage = _FakeStorage({INTRO_BLOB_PATH: b"intro", OUTRO_BLOB_PATH: b"outro"})
        s1 = tmp_path / "a.webm"
        s2 = tmp_path / "b.webm"
        s1.touch()
        s2.touch()
        segs = [
            _make_recorded_segment(owner="a", name="b", duration=20.0, video_path=s1),
            _make_recorded_segment(owner="c", name="d", duration=10.0, video_path=s2),
        ]
        out = tmp_path / "out" / "episode.mp4"

        compose_video(
            segments=segs,
            audio_path=tmp_path / "audio.mp3",
            output_path=out,
            runner=runner,
            storage=storage,
            intro_outro_cache_dir=tmp_path / "cache",
            audio_duration=30.0,
            transition_duration=0.5,
        )

        cmds = [c.args[0] for c in runner.call_args_list]
        fit_cmds = [
            c
            for c in cmds
            if "-t" in c
            and any("tpad=stop_mode=clone" in str(a) for a in c)
            and c[-1].endswith(".mp4")
            and "seg_" in c[-1]
        ]
        assert len(fit_cmds) == 2
        targets = [float(c[c.index("-t") + 1]) for c in fit_cmds]
        # target_sum = 20 (window) + 0.5 (one xfade) = 20.5; trimmed source = 20
        # so scale = 1.025 -> [15.375, 5.125], NOT the proportional ~[13.7, 6.8].
        assert targets[0] == pytest.approx(15.375)
        assert targets[1] == pytest.approx(5.125)

    def test_single_generic_segment_trimmed_to_window(self, tmp_path):
        runner = _ffprobe_runner(has_audio=True, duration=4.0)
        storage = _FakeStorage({INTRO_BLOB_PATH: b"intro", OUTRO_BLOB_PATH: b"outro"})
        s = tmp_path / "g.webm"
        s.touch()
        seg = _make_recorded_segment(duration=100.0, video_path=s)
        out = tmp_path / "out" / "episode.mp4"

        compose_video(
            segments=[seg],
            audio_path=tmp_path / "audio.mp3",
            output_path=out,
            runner=runner,
            storage=storage,
            intro_outro_cache_dir=tmp_path / "cache",
            audio_duration=20.0,
        )

        cmds = [c.args[0] for c in runner.call_args_list]
        fit_cmd = next(
            c for c in cmds if "-t" in c and any("tpad=stop_mode=clone" in str(a) for a in c)
        )
        # 20s audio - 4s intro - 4s outro = 12s content (single segment).
        assert float(fit_cmd[fit_cmd.index("-t") + 1]) == pytest.approx(12.0)

    def test_no_audio_duration_uses_plain_normalize(self, tmp_path):
        # Without audio_duration, segments are normalized (no tpad/-t fit).
        runner = _mock_runner()
        s = tmp_path / "s.webm"
        s.touch()
        seg = _make_recorded_segment(duration=10.0, video_path=s)
        compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=runner,
        )
        cmds = [c.args[0] for c in runner.call_args_list]
        assert not any("tpad=stop_mode=clone" in " ".join(c) for c in cmds)


# --- Tests for DOG (Digital On-Screen Graphic) watermark ---


class TestDogLogoConfig:
    def test_defaults_from_empty_dict(self):
        cfg = DogLogoConfig.from_dict({})
        assert cfg is not None
        assert cfg.url == DEFAULT_DOG_LOGO_URL
        assert cfg.position == "top-right"
        assert cfg.size == 80
        assert cfg.opacity == pytest.approx(0.5)

    def test_none_input_returns_none(self):
        assert DogLogoConfig.from_dict(None) is None
        assert DogLogoConfig.from_dict("nope") is None

    def test_custom_values(self):
        cfg = DogLogoConfig.from_dict(
            {
                "url": "https://example.com/logo.png",
                "position": "bottom-left",
                "size": 120,
                "opacity": 0.5,
            }
        )
        assert cfg.url == "https://example.com/logo.png"
        assert cfg.position == "bottom-left"
        assert cfg.size == 120
        assert cfg.opacity == pytest.approx(0.5)

    def test_invalid_values_fall_back(self):
        cfg = DogLogoConfig.from_dict(
            {
                "url": "   ",
                "position": "middle",
                "size": "huge",
                "opacity": "bad",
            }
        )
        assert cfg.url == DEFAULT_DOG_LOGO_URL
        assert cfg.position == "top-right"
        assert cfg.size == 80
        assert cfg.opacity == pytest.approx(0.5)

    def test_opacity_clamped(self):
        assert DogLogoConfig.from_dict({"opacity": 5}).opacity == pytest.approx(1.0)
        assert DogLogoConfig.from_dict({"opacity": -1}).opacity == pytest.approx(0.0)


class TestBuildDogOverlayFilter:
    def test_top_right_position(self):
        cfg = DogLogoConfig(size=80, opacity=0.3, position="top-right")
        f = _build_dog_overlay_filter(cfg, 2, "vout")
        assert "[2:v]scale=80:-1" in f
        assert "colorchannelmixer=aa=0.3" in f
        assert "[vout][dog]overlay=W-w-40:40" in f
        assert f.endswith("[dogout]")

    def test_bottom_left_position(self):
        cfg = DogLogoConfig(size=100, opacity=0.4, position="bottom-left")
        f = _build_dog_overlay_filter(cfg, 1, "0:v")
        assert "overlay=40:H-h-40" in f
        assert "scale=100:-1" in f

    def test_enable_expression_appended(self):
        cfg = DogLogoConfig(size=80, opacity=0.5, position="top-right")
        f = _build_dog_overlay_filter(cfg, 1, "0:v", enable="gte(t,7.000)")
        assert "overlay=W-w-40:40:format=auto:enable='gte(t,7.000)'" in f
        assert f.endswith("[dogout]")

    def test_no_enable_by_default(self):
        cfg = DogLogoConfig(size=80, opacity=0.5, position="top-right")
        f = _build_dog_overlay_filter(cfg, 1, "0:v")
        assert "enable=" not in f


class TestBuildIntroDogCmd:
    def test_overlay_enabled_on_intro_tail(self, tmp_path):
        cfg = DogLogoConfig(size=80, opacity=0.5, position="top-right")
        intro = tmp_path / "intro.mp4"
        logo = tmp_path / "logo.png"
        out = tmp_path / "intro_dog.mp4"
        cmd = _build_intro_dog_cmd(intro, cfg, logo, 7.0, out)
        assert cmd[0] == "ffmpeg"
        assert str(intro) in cmd
        assert str(logo) in cmd
        joined = " ".join(cmd)
        assert "enable='gte(t,7.000)'" in joined
        assert "[dogout]" in joined
        # video-only encode (no audio)
        assert "-an" in cmd
        assert cmd[-1] == str(out)


class TestComposeVideoDogLogo:
    def test_overlay_applied_to_content(self, tmp_path, monkeypatch):
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"fakepng")
        monkeypatch.setattr(
            "podcaster.video.video_compose._fetch_dog_logo",
            lambda url, cache_dir: logo,
        )
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=runner,
            dog_logo=DogLogoConfig(),
        )
        compose_cmd = next(
            c.args[0] for c in runner.call_args_list if "-filter_complex" in c.args[0]
        )
        joined = " ".join(compose_cmd)
        assert "overlay=" in joined
        assert "[dogout]" in joined
        # the logo image was added as an input
        assert str(logo) in compose_cmd

    def test_overlay_applied_to_intro_tail(self, tmp_path, monkeypatch):
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"fakepng")
        monkeypatch.setattr(
            "podcaster.video.video_compose._fetch_dog_logo",
            lambda url, cache_dir: logo,
        )
        runner = _ffprobe_runner(has_audio=False, duration=5.0)
        storage = _FakeStorage({INTRO_BLOB_PATH: b"intro"})
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        compose_video(
            segments=[seg],
            output_path=tmp_path / "out" / "episode.mp4",
            runner=runner,
            storage=storage,
            intro_outro_cache_dir=tmp_path / "cache",
            dog_logo=DogLogoConfig(),
        )
        cmds = [c.args[0] for c in runner.call_args_list]
        # the intro-tail DOG pass overlays the logo with a time-gated enable
        intro_dog_cmd = next(
            c for c in cmds if "-filter_complex" in c and c[-1].endswith("intro_dog.mp4")
        )
        joined = " ".join(intro_dog_cmd)
        assert "enable='gte(t," in joined
        assert "[dogout]" in joined
        assert str(logo) in intro_dog_cmd

    def test_no_config_skips_overlay(self, tmp_path):
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=runner,
            dog_logo=None,
        )
        for c in runner.call_args_list:
            assert "overlay=" not in " ".join(c.args[0])

    def test_failed_download_graceful(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "podcaster.video.video_compose._fetch_dog_logo",
            lambda url, cache_dir: None,
        )
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=runner,
            dog_logo=DogLogoConfig(),
        )
        for c in runner.call_args_list:
            assert "overlay=" not in " ".join(c.args[0])


class TestFetchDogLogoSSRF:
    """#601: _fetch_dog_logo must refuse SSRF targets and degrade gracefully."""

    def test_blocked_host_not_fetched(self, tmp_path, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr(vc, "safe_urlopen", called)
        result = vc._fetch_dog_logo("http://169.254.169.254/latest/meta-data/", tmp_path)
        assert result is None
        called.assert_not_called()

    def test_loopback_host_not_fetched(self, tmp_path, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr(vc, "safe_urlopen", called)
        result = vc._fetch_dog_logo("http://127.0.0.1:8080/logo.png", tmp_path)
        assert result is None
        called.assert_not_called()

    def test_unsupported_scheme_not_fetched(self, tmp_path, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr(vc, "safe_urlopen", called)
        result = vc._fetch_dog_logo("file:///etc/passwd", tmp_path)
        assert result is None
        called.assert_not_called()

    def test_safe_urlopen_used_for_public_url(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vc, "host_is_blocked", lambda _host: False)

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self):
                return b"pngbytes"

        opened = MagicMock(return_value=_Resp())
        monkeypatch.setattr(vc, "safe_urlopen", opened)
        result = vc._fetch_dog_logo("https://example.com/logo.png", tmp_path)
        assert result is not None
        assert result.read_bytes() == b"pngbytes"
        opened.assert_called_once()

    def test_redact_url_strips_credentials(self):
        assert vc._redact_url("https://user:secret@example.com/logo.png") == (
            "https://example.com/logo.png"
        )
        assert vc._redact_url("http://user:pw@10.0.0.1:8443/x") == "http://10.0.0.1:8443/x"
        # Query string and fragment are dropped (may carry signed tokens); the
        # host/port/path are preserved for debugging.
        assert vc._redact_url("https://example.com:9000/a?b=c#frag") == "https://example.com:9000/a"
        # IPv6 literal hosts are bracketed so the result stays a valid URL.
        assert vc._redact_url("https://[2001:db8::1]:8443/logo.png?t=x") == (
            "https://[2001:db8::1]:8443/logo.png"
        )


# --- Hardware-accelerated encoding (NVENC) — issue #396 ---


class TestHardwareAccelEncoding:
    def setup_method(self):
        vc._select_hwaccel_encoder.cache_clear()

    def teardown_method(self):
        vc._select_hwaccel_encoder.cache_clear()

    def test_default_cpu_path_unchanged(self):
        # auto-mode with no GPU device must return the exact libx264 flags.
        with (
            patch.object(vc, "_HWACCEL_MODE", "auto"),
            patch.object(vc, "_nvenc_available", return_value=False),
        ):
            vc._select_hwaccel_encoder.cache_clear()
            args = vc._video_encode_args("slow")
        assert args == [
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            str(vc.ENCODE_CRF),
            "-pix_fmt",
            vc.ENCODE_PIX_FMT,
            "-profile:v",
            "high",
        ]

    def test_off_mode_never_uses_nvenc(self):
        with (
            patch.object(vc, "_HWACCEL_MODE", "off"),
            patch.object(vc, "_nvenc_available", return_value=True),
        ):
            vc._select_hwaccel_encoder.cache_clear()
            assert vc._select_hwaccel_encoder() is None

    def test_auto_uses_nvenc_when_available(self):
        with (
            patch.object(vc, "_HWACCEL_MODE", "auto"),
            patch.object(vc, "_nvenc_available", return_value=True),
        ):
            vc._select_hwaccel_encoder.cache_clear()
            codec = vc._select_hwaccel_encoder()
        assert codec == vc._NVENC_CODEC

    def test_forced_nvenc_skips_detection(self):
        with (
            patch.object(vc, "_HWACCEL_MODE", "nvenc"),
            patch.object(vc, "_nvenc_available", return_value=False) as avail,
        ):
            vc._select_hwaccel_encoder.cache_clear()
            codec = vc._select_hwaccel_encoder()
        avail.assert_not_called()
        assert codec == vc._NVENC_CODEC

    def test_nvenc_encode_args_use_constqp_and_profile(self):
        args = vc._hwaccel_encode_args("h264_nvenc", "slow")
        assert "h264_nvenc" in args
        assert "-rc" in args and "constqp" in args
        assert "-qp" in args
        # Quality target mirrors the software CRF.
        assert str(vc.ENCODE_CRF) in args
        assert "-profile:v" in args and "high" in args
        # 8-bit 4:2:0 chroma preserved for Spotify compatibility.
        assert "yuv420p" in args

    def test_nvenc_preset_mapping(self):
        fast = vc._hwaccel_encode_args("h264_nvenc", "ultrafast")
        slow = vc._hwaccel_encode_args("h264_nvenc", "slow")
        assert "p1" in fast  # fastest NVENC preset for intermediates
        assert "p6" in slow  # high-quality preset for the final pass

    def test_nvenc_unavailable_without_gpu_device(self):
        # No /dev/nvidia* present → not available regardless of ffmpeg.
        with patch("podcaster.video.video_compose.os.path.exists", return_value=False):
            assert vc._nvenc_available() is False


# --- Tests for _splice_section_cards (issue #377) ---


class _Insert:
    """Lightweight SectionCardInsert stand-in for splice/compose tests."""

    def __init__(self, before_index, clip_path, duration_seconds, name="Card"):
        self.before_index = before_index
        self.clip_path = clip_path
        self.duration_seconds = duration_seconds
        self.name = name


class TestSpliceSectionCards:
    def _content(self, n):
        paths = [Path(f"/n/seg_{i}.mp4") for i in range(n)]
        durs = [10.0] * n
        trans = [TRANSITION_WIPE_LEFT] * (n - 1)
        return paths, durs, trans

    def test_insert_between_segments(self):
        paths, durs, trans = self._content(3)
        lts = {}
        card = Path("/n/card.mp4")
        new_paths, new_durs, new_trans, new_lts = _splice_section_cards(
            paths, durs, trans, lts, [(1, card, 2.5)]
        )
        # Card sits before original segment index 1.
        assert new_paths == [paths[0], card, paths[1], paths[2]]
        assert new_durs == [10.0, 2.5, 10.0, 10.0]
        # Boundaries: seg0->card (fade), card->seg1 (fade), seg1->seg2 (original).
        assert new_trans == [TRANSITION_FADE, TRANSITION_FADE, TRANSITION_WIPE_LEFT]
        assert new_lts == {}

    def test_insert_before_first(self):
        paths, durs, trans = self._content(2)
        card = Path("/n/card.mp4")
        new_paths, _, new_trans, _ = _splice_section_cards(paths, durs, trans, {}, [(0, card, 2.5)])
        assert new_paths == [card, paths[0], paths[1]]
        # card is first (no leading transition); card->seg0 fade; seg0->seg1 original.
        assert new_trans == [TRANSITION_FADE, TRANSITION_WIPE_LEFT]

    def test_insert_at_end_clamped(self):
        paths, durs, trans = self._content(2)
        card = Path("/n/card.mp4")
        new_paths, _, new_trans, _ = _splice_section_cards(
            paths, durs, trans, {}, [(99, card, 2.5)]
        )
        assert new_paths == [paths[0], paths[1], card]
        assert new_trans == [TRANSITION_WIPE_LEFT, TRANSITION_FADE]

    def test_multiple_cards(self):
        paths, durs, trans = self._content(3)
        c1, c2 = Path("/n/c1.mp4"), Path("/n/c2.mp4")
        new_paths, new_durs, new_trans, _ = _splice_section_cards(
            paths, durs, trans, {}, [(1, c1, 2.5), (2, c2, 2.5)]
        )
        assert new_paths == [paths[0], c1, paths[1], c2, paths[2]]
        assert len(new_trans) == len(new_paths) - 1
        assert all(t == TRANSITION_FADE for t in new_trans[:4])

    def test_lower_thirds_reindexed(self):
        paths, durs, trans = self._content(3)
        lt = LowerThird(text="r", url="https://github.com/o/r", start_seconds=0.5, end_seconds=5.0)
        lts = {0: lt, 2: lt}
        card = Path("/n/card.mp4")
        new_paths, _, _, new_lts = _splice_section_cards(
            paths, durs, trans, lts, [(1, card, 2.5)], 1.0
        )
        # Original index 0 stays at 0; original index 2 shifts to 3 (card inserted at 1).
        assert set(new_lts.keys()) == {0, 3}
        assert new_paths[0] == paths[0] and new_paths[3] == paths[2]
        # Index-0 LT precedes the card → no time shift.
        assert new_lts[0].start_seconds == pytest.approx(0.5)
        # Index-2 LT follows a 2.5 s card (1.0 s overlap) → +1.5 s shift.
        assert new_lts[3].start_seconds == pytest.approx(0.5 + 1.5)
        assert new_lts[3].end_seconds == pytest.approx(5.0 + 1.5)

    def test_leading_card_shifts_following_lower_third(self):
        paths, durs, trans = self._content(2)
        lt = LowerThird(text="r", url="u", start_seconds=0.5, end_seconds=4.0)
        card = Path("/n/card.mp4")
        _, _, _, new_lts = _splice_section_cards(paths, durs, trans, {0: lt}, [(0, card, 2.5)], 1.0)
        # Card before segment 0 → segment 0's LT lands at index 1, shifted +1.5 s.
        assert set(new_lts.keys()) == {1}
        assert new_lts[1].start_seconds == pytest.approx(2.0)

    def test_no_inserts_is_identity(self):
        paths, durs, trans = self._content(3)
        out = _splice_section_cards(paths, durs, trans, {}, [])
        assert out == (paths, durs, trans, {})


class TestComposeVideoSectionCards:
    def test_cards_normalized_and_spliced(self, tmp_path):
        runner = _mock_runner()
        seg1 = _make_recorded_segment(
            owner="a", name="b", duration=10.0, video_path=tmp_path / "seg1.webm"
        )
        seg2 = _make_recorded_segment(
            owner="c", name="d", duration=10.0, video_path=tmp_path / "seg2.webm"
        )
        (tmp_path / "seg1.webm").touch()
        (tmp_path / "seg2.webm").touch()
        card = tmp_path / "card.mp4"
        card.touch()
        inserts = [_Insert(1, card, 2.5, name="Trends")]

        result = compose_video(
            segments=[seg1, seg2],
            output_dir=tmp_path / "out",
            runner=runner,
            section_cards=inserts,
        )

        # The card clip must be normalized like content (its path appears in a cmd).
        normalize_inputs = [c.args[0] for c in runner.call_args_list if str(card) in c.args[0]]
        assert normalize_inputs, "section card was not normalized"
        # Result content count still reflects the original content segments.
        assert result.segment_count == 2

    def test_no_cards_matches_baseline(self, tmp_path):
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "s.webm")
        (tmp_path / "s.webm").touch()
        result = compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=runner,
            section_cards=None,
        )
        # 1 normalize + 1 compose + 1 h264 metadata — unchanged from baseline.
        assert runner.call_count == 3
        assert result.segment_count == 1

    def test_cards_reserve_audio_window(self, tmp_path):
        # With fit-to-window active, total composed video stays aligned with the
        # audio timeline: content window shrinks by (card_dur - transition) per card.
        runner = _mock_runner()
        seg1 = _make_recorded_segment(
            owner="a", name="b", duration=10.0, video_path=tmp_path / "seg1.webm"
        )
        seg2 = _make_recorded_segment(
            owner="c", name="d", duration=10.0, video_path=tmp_path / "seg2.webm"
        )
        (tmp_path / "seg1.webm").touch()
        (tmp_path / "seg2.webm").touch()
        card = tmp_path / "card.mp4"
        card.touch()
        inserts = [_Insert(1, card, 2.5)]

        result = compose_video(
            segments=[seg1, seg2],
            output_dir=tmp_path / "out",
            runner=runner,
            audio_duration=30.0,
            section_cards=inserts,
        )
        # 3 clips (2 content + 1 card) with 2 transition overlaps → 30 s total.
        assert result.duration_seconds == pytest.approx(30.0, abs=0.01)


class TestFreeComposeIntermediates:
    """Disk-relief cleanup of composition intermediates after the join step."""

    def test_removes_content_and_normalized_clips(self, tmp_path):
        content = tmp_path / "content.mp4"
        content.write_bytes(b"x" * 16)
        norm_dir = tmp_path / "normalized"
        norm_dir.mkdir()
        seg0 = norm_dir / "seg_000.mp4"
        seg1 = norm_dir / "seg_001.mp4"
        card0 = norm_dir / "card_000.mp4"
        for p in (seg0, seg1, card0):
            p.write_bytes(b"y" * 16)

        vc._free_compose_intermediates(content, norm_dir)

        assert not content.exists()
        assert not seg0.exists()
        assert not seg1.exists()
        assert not card0.exists()

    def test_is_best_effort_when_files_missing(self, tmp_path):
        # Must never raise even if nothing exists yet.
        vc._free_compose_intermediates(tmp_path / "content.mp4", tmp_path / "normalized")


# --- Blob-backed checkpoint/resume (issue #410) ------------------------------


def _touch_output_runner():
    """A command runner that creates any .mp4 argument so checkpoint uploads
    (which require the local source file to exist) succeed under mocked ffmpeg."""

    def _run(cmd):
        for arg in cmd:
            s = str(arg)
            if s.endswith(".mp4") and not Path(s).exists():
                Path(s).parent.mkdir(parents=True, exist_ok=True)
                Path(s).write_bytes(b"\x00" * 2048)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return _run


class TestComposeVideoCheckpointResume:
    def _store(self, tmp_path):
        from podcaster.storage import LocalStorageBackend
        from podcaster.video.intermediates import IntermediateStore

        backend = LocalStorageBackend(
            root=tmp_path / "scratch", base_url="https://example.test/scratch"
        )
        return IntermediateStore(backend, "job-compose")

    def test_intermediates_checkpointed(self, tmp_path):
        store = self._store(tmp_path)
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").write_bytes(b"\x00" * 2048)

        compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=_touch_output_runner(),
            intermediates=store,
        )

        # Normalized segment and composed video are checkpointed to blob.
        assert store.exists("normalized_000.mp4") is True
        assert store.exists("composed_video.mp4") is True
        # Manifest tracks the composed-video stage.
        assert "composed_video" in store.load_manifest().get("stages", {})

    def test_resumes_from_composed_checkpoint(self, tmp_path):
        from podcaster.video.video_compose import COMPOSED_VIDEO_CHECKPOINT

        store = self._store(tmp_path)
        # Pre-seed the composed-video checkpoint as if a prior run finished it.
        composed = tmp_path / "composed_seed.mp4"
        composed.write_bytes(b"\x00" * 4096)
        assert store.upload(COMPOSED_VIDEO_CHECKPOINT, composed, "video/mp4") is True

        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        # Note: seg.webm intentionally absent — resume must not need it.

        runner = _mock_runner()
        result = compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=runner,
            intermediates=store,
        )

        # The composed video was pulled back to local disk.
        assert (tmp_path / "out" / COMPOSED_VIDEO_CHECKPOINT).exists()
        assert result.segment_count == 1
        # Only the final mux path ran (probe + h264 metadata); no normalize/compose.
        assert runner.call_count <= 2
        ran = [str(c[0][0]) for c in runner.call_args_list]
        assert not any("scale" in r for r in ran)

    def test_resumes_normalized_segment(self, tmp_path):
        store = self._store(tmp_path)
        # Pre-seed a normalized clip checkpoint.
        norm = tmp_path / "norm_seed.mp4"
        norm.write_bytes(b"\x00" * 2048)
        assert store.upload("normalized_000.mp4", norm, "video/mp4") is True

        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        # seg.webm absent: a resumed normalize must not touch the source.

        calls = []

        def _run(cmd):
            calls.append([str(a) for a in cmd])
            for arg in cmd:
                s = str(arg)
                if s.endswith(".mp4") and not Path(s).exists():
                    Path(s).parent.mkdir(parents=True, exist_ok=True)
                    Path(s).write_bytes(b"\x00" * 2048)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=_run,
            intermediates=store,
        )

        # No normalize command was issued for the resumed segment.
        assert not any(any("scale" in a for a in cmd) for cmd in calls)

    def test_normalized_clips_freed_locally_in_pairwise(self, tmp_path):
        """Issue #410 (comment 3): the per-segment normalized clips are uploaded
        and removed from local disk during normalize, fetched just-in-time by the
        pairwise compose, and released after use — so they never all coexist on
        local disk."""
        store = self._store(tmp_path)
        s0 = tmp_path / "s0.webm"
        s1 = tmp_path / "s1.webm"
        s0.write_bytes(b"\x00" * 2048)
        s1.write_bytes(b"\x00" * 2048)
        seg0 = _make_recorded_segment(name="r0", duration=10.0, video_path=s0)
        seg1 = _make_recorded_segment(name="r1", duration=10.0, video_path=s1)

        compose_video(
            segments=[seg0, seg1],
            output_dir=tmp_path / "out",
            runner=_touch_output_runner(),
            intermediates=store,
        )

        # Both normalized clips were checkpointed to blob …
        assert store.exists("normalized_000.mp4") is True
        assert store.exists("normalized_001.mp4") is True
        # … and none were left lingering on local disk.
        norm_dir = tmp_path / "out" / "normalized"
        leftover = list(norm_dir.glob("seg_*.mp4")) if norm_dir.exists() else []
        assert leftover == []

    def test_normalize_fetches_raw_recording_from_blob(self, tmp_path):
        """Issue #410 (comment 2): when record_episode has already freed the
        local recording, normalize pulls the raw clip back from its blob
        checkpoint on demand rather than requiring it on local disk."""
        store = self._store(tmp_path)
        # Seed the raw recording checkpoint; the local file is intentionally absent.
        raw = tmp_path / "raw_seed.webm"
        raw.write_bytes(b"\x00" * 2048)
        assert store.upload("recording_000.webm", raw, "video/webm") is True

        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "absent.webm")
        assert not seg.video_path.exists()

        calls = []

        def _run(cmd):
            calls.append([str(a) for a in cmd])
            for arg in cmd:
                s = str(arg)
                if s.endswith(".mp4") and not Path(s).exists():
                    Path(s).parent.mkdir(parents=True, exist_ok=True)
                    Path(s).write_bytes(b"\x00" * 2048)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=_run,
            intermediates=store,
        )

        # Normalization ran (a scale command was issued) against the fetched raw.
        assert any(any("scale" in a for a in cmd) for cmd in calls)
        assert store.exists("normalized_000.mp4") is True


# --- Tests for parallel pairwise composition tree (#481) ---


def _xfade_cmds(calls: list[list[str]]) -> list[list[str]]:
    """Return the recorded commands that perform an xfade pass."""
    out = []
    for cmd in calls:
        if "-filter_complex" in cmd:
            fc = cmd[cmd.index("-filter_complex") + 1]
            if "xfade" in fc:
                out.append(cmd)
    return out


def _filter_complex(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def _recording_runner():
    """A CommandRunner that records every invocation's argv as strings."""
    calls: list[list[str]] = []

    def _run(cmd):
        calls.append([str(a) for a in cmd])
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


class TestComposePairwiseParallel:
    """White-box tests for the level-parallel composition tree (#481)."""

    def _lts(self, n: int, durations: list[float], td: float) -> dict:
        abs_start = [0.0] * n
        for i in range(1, n):
            abs_start[i] = abs_start[i - 1] + durations[i - 1] - td
        return {
            i: vc.LowerThird(
                text=f"owner{i}/repo{i}",
                url=f"https://example.test/{i}",
                start_seconds=abs_start[i] + 0.5,
                end_seconds=abs_start[i] + 0.5 + vc.LOWER_THIRD_DURATION,
            )
            for i in range(n)
        }

    @pytest.mark.parametrize("n", [3, 4, 5, 6, 7, 8])
    def test_pass_count_is_n_minus_one(self, tmp_path, n):
        """A balanced tree performs exactly N-1 two-input xfade passes."""
        runner = _recording_runner()
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]
        durations = [10.0] * n
        transitions = [TRANSITION_FADE] * (n - 1)

        vc._compose_pairwise_parallel(
            paths,
            durations,
            1.0,
            transitions,
            {},
            None,
            None,
            None,
            tmp_path / "out.mp4",
            runner,
            tmp_path,
            concurrency=2,
        )

        xfades = _xfade_cmds(runner.calls)
        assert len(xfades) == n - 1
        for cmd in xfades:
            assert cmd.count("-i") == 2  # constant memory: 2 inputs per pass

    def test_disk_budget_scales_with_per_level_workers(self, tmp_path, monkeypatch):
        """The disk-budget estimate uses each level's *actual* concurrency, so the
        single-pair root level budgets for one pass — not the global concurrency
        (issue #481 review: avoid over-estimating and spurious disk errors)."""
        size = 1024
        n = 4  # level 0 -> 2 pairs (workers=2); root -> 1 pair (workers=1)
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]
        for p in paths:
            p.write_bytes(b"x" * size)

        requested: list[int] = []

        def _fake_budget(work_dir, needed):
            requested.append(needed)

        monkeypatch.setattr(vc, "ensure_disk_budget", _fake_budget)

        def _run(cmd):
            # Materialize the output so intermediate inputs exist for the next
            # level's budget computation, at the same fixed size.
            Path(str(cmd[-1])).write_bytes(b"x" * size)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        vc._compose_pairwise_parallel(
            paths,
            [10.0] * n,
            1.0,
            [TRANSITION_FADE] * (n - 1),
            {},
            None,
            None,
            None,
            tmp_path / "out.mp4",
            _run,
            tmp_path,
            concurrency=2,
        )

        # Each pair has two inputs of ``size`` -> sum*2 == 4*size per pass.
        # Level-0 pairs (workers=2) request 2x that; the root pass (workers=1) 1x.
        assert max(requested) == 4 * size * 2  # a level-0 pass with 2 workers
        assert requested[-1] == 4 * size * 1  # the final root pass, 1 worker

    def test_combine_failure_releases_inputs_and_drops_partial_output(self, tmp_path):
        """A failed xfade pass must reclaim disk: release the fetched leaf inputs
        and remove any partially-written output (issue #481 review)."""
        n = 2
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]
        for p in paths:
            p.write_bytes(b"x" * 1024)
        target = tmp_path / "out.mp4"

        fetched: list[Path] = []
        released: list[Path] = []

        def _fetch(p: Path) -> None:
            fetched.append(p)

        def _release(p: Path) -> None:
            released.append(p)

        def _run(cmd):
            # Simulate ffmpeg writing a partial output then failing.
            out = Path(str(cmd[-1]))
            out.write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, cmd, stderr="boom")

        with pytest.raises(subprocess.CalledProcessError):
            vc._compose_pairwise_parallel(
                paths,
                [10.0] * n,
                1.0,
                [TRANSITION_FADE] * (n - 1),
                {},
                None,
                None,
                None,
                target,
                _run,
                tmp_path,
                concurrency=1,
                fetch=_fetch,
                release=_release,
            )

        # Both leaf inputs were fetched and then released back on failure.
        assert set(released) == set(paths)
        # The partially-written output was cleaned up.
        assert not target.exists()

    def test_combine_releases_first_input_when_second_fetch_fails(self, tmp_path):
        """If the second input's fetch fails, the already-fetched first input is
        still released so it does not leak on disk (issue #481 review)."""
        n = 2
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]
        for p in paths:
            p.write_bytes(b"x" * 1024)
        target = tmp_path / "out.mp4"

        released: list[Path] = []

        def _fetch(p: Path) -> None:
            if p == paths[1]:  # the second (right) input fails to fetch
                raise RuntimeError("blob download failed")

        def _release(p: Path) -> None:
            released.append(p)

        def _run(cmd):  # pragma: no cover - must never run on fetch failure
            raise AssertionError("ffmpeg should not run when a fetch fails")

        with pytest.raises(RuntimeError, match="blob download failed"):
            vc._compose_pairwise_parallel(
                paths,
                [10.0] * n,
                1.0,
                [TRANSITION_FADE] * (n - 1),
                {},
                None,
                None,
                None,
                target,
                _run,
                tmp_path,
                concurrency=1,
                fetch=_fetch,
                release=_release,
            )

        # Only the first input was fetched, and it was released on the failure.
        assert released == [paths[0]]
        assert not target.exists()

    def test_compose_pairwise_clamps_concurrency_to_hard_cap(self, tmp_path, monkeypatch):
        """A caller-supplied concurrency above MAX_COMPOSE_CONCURRENCY is clamped
        before dispatch so the hard cap cannot be bypassed (issue #481 review)."""
        captured: dict[str, int] = {}

        def _spy(*args, **kwargs):
            captured["concurrency"] = kwargs["concurrency"]

        monkeypatch.setattr(vc, "_compose_pairwise_parallel", _spy)
        n = 4
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]

        vc._compose_pairwise(
            paths,
            [10.0] * n,
            1.0,
            [TRANSITION_FADE] * (n - 1),
            {},
            None,
            None,
            None,
            tmp_path / "out.mp4",
            _recording_runner(),
            tmp_path,
            concurrency=vc.MAX_COMPOSE_CONCURRENCY + 100,
        )

        assert captured["concurrency"] == vc.MAX_COMPOSE_CONCURRENCY

    def test_compose_pairwise_clamps_concurrency_floor(self, tmp_path, monkeypatch):
        """A concurrency below 1 is clamped up to the sequential path (>=1)."""
        called = {"parallel": False, "sequential": False}

        monkeypatch.setattr(
            vc,
            "_compose_pairwise_parallel",
            lambda *a, **k: called.__setitem__("parallel", True),
        )
        monkeypatch.setattr(
            vc,
            "_compose_pairwise_sequential",
            lambda *a, **k: called.__setitem__("sequential", True),
        )
        n = 4
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]

        vc._compose_pairwise(
            paths,
            [10.0] * n,
            1.0,
            [TRANSITION_FADE] * (n - 1),
            {},
            None,
            None,
            None,
            tmp_path / "out.mp4",
            _recording_runner(),
            tmp_path,
            concurrency=0,
        )

        # Clamped to 1 → sequential left-fold, never the parallel tree.
        assert called["sequential"] and not called["parallel"]

    def test_each_boundary_transition_used_once(self, tmp_path):
        """Every boundary's distinct transition is applied exactly once."""
        runner = _recording_runner()
        n = 6
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]
        durations = [10.0] * n
        # Distinct transitions per boundary so we can verify each is used once.
        transitions = [
            TRANSITION_FADE,
            TRANSITION_FADE_BLACK,
            TRANSITION_WIPE_LEFT,
            TRANSITION_SLIDE_LEFT,
            TRANSITION_FADE,
        ]

        vc._compose_pairwise_parallel(
            paths,
            durations,
            1.0,
            transitions,
            {},
            None,
            None,
            None,
            tmp_path / "out.mp4",
            runner,
            tmp_path,
            concurrency=2,
        )

        used = []
        for cmd in _xfade_cmds(runner.calls):
            fc = _filter_complex(cmd)
            token = fc.split("xfade=transition=", 1)[1].split(":", 1)[0]
            used.append(token)
        assert sorted(used) == sorted(transitions)

    def test_root_pass_writes_target_at_full_preset(self, tmp_path):
        """The final (root) xfade writes compose_target with ENCODE_PRESET."""
        runner = _recording_runner()
        target = tmp_path / "final.mp4"
        n = 4
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]

        vc._compose_pairwise_parallel(
            paths,
            [10.0] * n,
            1.0,
            [TRANSITION_FADE] * (n - 1),
            {},
            None,
            None,
            None,
            target,
            runner,
            tmp_path,
            concurrency=2,
        )

        # Exactly one xfade pass targets compose_target, and it uses the full
        # encode preset (intermediates use ultrafast).
        target_cmds = [c for c in _xfade_cmds(runner.calls) if str(target) in c]
        assert len(target_cmds) == 1
        root = target_cmds[0]
        assert ENCODE_PRESET in root
        intermediates = [c for c in _xfade_cmds(runner.calls) if str(target) not in c]
        for cmd in intermediates:
            assert "ultrafast" in cmd

    def test_lower_thirds_baked_once_at_node_local_time(self, tmp_path):
        """Each LT is baked exactly once, shifted into its subtree's local time."""
        runner = _recording_runner()
        n = 4
        durations = [10.0] * n
        td = 1.0
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]
        lts = self._lts(n, durations, td)

        vc._compose_pairwise_parallel(
            paths,
            durations,
            td,
            [TRANSITION_FADE] * (n - 1),
            lts,
            "ffmpeg",
            None,
            None,
            tmp_path / "out.mp4",
            runner,
            tmp_path,
            concurrency=2,
        )

        # Find, for each segment, the enable start time baked into its drawtext.
        def enable_start_for(text: str) -> float:
            hits = []
            for cmd in runner.calls:
                if "-filter_complex" not in cmd:
                    continue
                fc = _filter_complex(cmd)
                if f"text='{text}'" in fc:
                    seg = fc.split(f"text='{text}'", 1)[1]
                    between = seg.split("enable='between(t,", 1)[1]
                    hits.append(float(between.split(",", 1)[0]))
            assert len(hits) == 1, f"{text} baked {len(hits)} times, expected 1"
            return hits[0]

        # Tree: (0,1)->A@abs0, (2,3)->B@abs18, (A,B)->root.
        # Segments 0/1 sit in a subtree whose local origin is absolute 0, so
        # their enable starts equal the absolute values (0.5, 9.5).  Segments
        # 2/3 sit in a subtree whose local origin is absolute 18, so their
        # enable starts are shifted back by 18 -> 0.5 and 9.5, NOT 18.5/27.5.
        assert enable_start_for("owner0/repo0") == pytest.approx(0.5)
        assert enable_start_for("owner1/repo1") == pytest.approx(9.5)
        assert enable_start_for("owner2/repo2") == pytest.approx(0.5)
        assert enable_start_for("owner3/repo3") == pytest.approx(9.5)

    def test_level_zero_pairs_run_concurrently(self, tmp_path):
        """Independent level-0 pair composes overlap in time (true parallelism)."""
        import threading

        active = 0
        peak = 0
        call_idx = 0
        lock = threading.Lock()
        # The two level-0 passes (the first two runner calls) must be in-flight
        # together; a barrier forces a deterministic overlap so the test is not
        # flaky on slow/contended runners.
        overlap = threading.Barrier(2, timeout=10)

        def _run(cmd):
            nonlocal active, peak, call_idx
            with lock:
                idx = call_idx
                call_idx += 1
                active += 1
                peak = max(peak, active)
            if idx < 2:
                try:
                    overlap.wait()
                except threading.BrokenBarrierError:
                    pass
            with lock:
                active -= 1
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        n = 4
        paths = [tmp_path / f"seg_{i:02d}.mp4" for i in range(n)]
        vc._compose_pairwise_parallel(
            paths,
            [10.0] * n,
            1.0,
            [TRANSITION_FADE] * (n - 1),
            {},
            None,
            None,
            None,
            tmp_path / "out.mp4",
            _run,
            tmp_path,
            concurrency=2,
        )

        # Level 0 has two independent pair composes; with concurrency=2 they
        # must overlap, so peak in-flight ffmpeg passes is at least 2.
        assert peak >= 2

    def test_total_duration_matches_sequential(self, tmp_path):
        """Tree and left-fold yield the same composed (overlap-adjusted) length."""
        n = 7
        durations = [10.0, 8.0, 12.0, 9.0, 11.0, 7.0, 10.0]
        td = 1.0
        expected = sum(durations) - td * (n - 1)
        # Duration is a pure function of inputs; both paths must agree (this is
        # asserted at the compose_video level elsewhere — here we sanity-check
        # the arithmetic the tree relies on for its xfade offsets).
        abs_start = [0.0] * n
        for i in range(1, n):
            abs_start[i] = abs_start[i - 1] + durations[i - 1] - td
        # Root duration via the tree's additive rule equals the timeline length.
        assert abs_start[-1] + durations[-1] == pytest.approx(expected)


class TestComposePairwiseDispatch:
    """The public _compose_pairwise routes to the right strategy (#481)."""

    def test_concurrency_one_uses_sequential(self, tmp_path, monkeypatch):
        seq = MagicMock()
        par = MagicMock()
        monkeypatch.setattr(vc, "_compose_pairwise_sequential", seq)
        monkeypatch.setattr(vc, "_compose_pairwise_parallel", par)
        paths = [tmp_path / f"s{i}.mp4" for i in range(4)]
        vc._compose_pairwise(
            paths,
            [10.0] * 4,
            1.0,
            [TRANSITION_FADE] * 3,
            {},
            None,
            None,
            None,
            tmp_path / "o.mp4",
            _mock_runner(),
            tmp_path,
            concurrency=1,
        )
        seq.assert_called_once()
        par.assert_not_called()

    def test_few_clips_use_sequential(self, tmp_path, monkeypatch):
        seq = MagicMock()
        par = MagicMock()
        monkeypatch.setattr(vc, "_compose_pairwise_sequential", seq)
        monkeypatch.setattr(vc, "_compose_pairwise_parallel", par)
        paths = [tmp_path / f"s{i}.mp4" for i in range(2)]
        vc._compose_pairwise(
            paths,
            [10.0] * 2,
            1.0,
            [TRANSITION_FADE],
            {},
            None,
            None,
            None,
            tmp_path / "o.mp4",
            _mock_runner(),
            tmp_path,
            concurrency=4,
        )
        seq.assert_called_once()
        par.assert_not_called()

    def test_many_clips_with_concurrency_use_parallel(self, tmp_path, monkeypatch):
        seq = MagicMock()
        par = MagicMock()
        monkeypatch.setattr(vc, "_compose_pairwise_sequential", seq)
        monkeypatch.setattr(vc, "_compose_pairwise_parallel", par)
        paths = [tmp_path / f"s{i}.mp4" for i in range(5)]
        vc._compose_pairwise(
            paths,
            [10.0] * 5,
            1.0,
            [TRANSITION_FADE] * 4,
            {},
            None,
            None,
            None,
            tmp_path / "o.mp4",
            _mock_runner(),
            tmp_path,
            concurrency=3,
        )
        par.assert_called_once()
        seq.assert_not_called()


class TestComposeTreeRealFfmpeg:
    """End-to-end offset-math validation against a real ffmpeg (#481)."""

    def test_four_clip_tree_composes_with_correct_duration(self, tmp_path):
        ffmpeg = vc._find_drawtext_capable_ffmpeg() or "ffmpeg"
        import shutil as _sh

        if _sh.which(ffmpeg) is None and _sh.which("ffmpeg") is None:
            pytest.skip("ffmpeg not available")
        binary = ffmpeg if _sh.which(ffmpeg) else "ffmpeg"

        # Build four short solid-colour clips with real ffmpeg.
        durations = [3.0, 2.0, 4.0, 2.0]
        colours = ["red", "green", "blue", "white"]
        paths = []
        for i, (d, c) in enumerate(zip(durations, colours)):
            p = tmp_path / f"clip_{i}.mp4"
            rc = subprocess.run(
                [
                    binary,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={c}:s=320x240:r=30:d={d}",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(p),
                ],
                capture_output=True,
            )
            if rc.returncode != 0:
                pytest.skip(f"ffmpeg cannot synthesise test clips: {rc.stderr[:200]!r}")
            paths.append(p)

        td = 1.0
        target = tmp_path / "composed.mp4"

        def _run(cmd):
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode("utf-8", "replace")[:500])
            return subprocess.CompletedProcess(cmd, 0, "", "")

        vc._compose_pairwise_parallel(
            paths,
            durations,
            td,
            [TRANSITION_FADE] * 3,
            {},
            None,
            None,
            None,
            target,
            _run,
            tmp_path,
            concurrency=2,
        )

        assert target.exists() and target.stat().st_size > 0
        probe = subprocess.run(
            [binary, "-hide_banner", "-i", str(target)],
            capture_output=True,
            text=True,
        )
        # Composed length = sum(durations) - 3 overlaps = 11 - 3 = 8s.
        expected = sum(durations) - td * (len(durations) - 1)
        # Parse "Duration: HH:MM:SS.xx" from ffmpeg stderr.
        duration_lines = [ln for ln in probe.stderr.splitlines() if "Duration:" in ln]
        assert duration_lines, (
            f"ffprobe stderr did not contain a 'Duration:' line; stderr was:\n{probe.stderr}"
        )
        line = duration_lines[0]
        hms = line.split("Duration:", 1)[1].split(",", 1)[0].strip()
        h, m, s = hms.split(":")
        actual = int(h) * 3600 + int(m) * 60 + float(s)
        assert actual == pytest.approx(expected, abs=0.5)


class TestApadFrameSpanRealFfmpeg:
    """audio_duration >= video_duration after mux on a synthetic concat (#549)."""

    def _ffprobe_durations(self, binary: str, path):
        import json as _json

        def _dur(args):
            r = subprocess.run([binary, *args], capture_output=True, text=True, check=True)
            return _json.loads(r.stdout or "{}")

        vinfo = _dur(
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_packets",
                "-show_entries",
                "stream=nb_read_packets,avg_frame_rate,r_frame_rate",
                "-of",
                "json",
                str(path),
            ]
        )
        ainfo = _dur(
            [
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=duration",
                "-of",
                "json",
                str(path),
            ]
        )
        vs = (vinfo.get("streams") or [{}])[0]
        nb = int(vs.get("nb_read_packets", 0) or 0)
        fps = vc._parse_fps(vs.get("avg_frame_rate", "")) or vc._parse_fps(
            vs.get("r_frame_rate", "")
        )
        video_dur = nb / fps if fps else 0.0
        audio_dur = float((ainfo.get("streams") or [{}])[0].get("duration", 0.0) or 0.0)
        return video_dur, audio_dur

    def test_muxed_audio_not_shorter_than_video(self, tmp_path):
        import shutil as _sh

        ffprobe = _sh.which("ffprobe")
        ffmpeg = _sh.which("ffmpeg")
        if not ffmpeg or not ffprobe:
            pytest.skip("ffmpeg/ffprobe not available")

        def _run(cmd):
            r = subprocess.run([str(a) for a in cmd], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(" ".join(map(str, cmd)) + "\n" + r.stderr[:800])
            return subprocess.CompletedProcess(cmd, 0, r.stdout, r.stderr)

        # Build a concat-demuxer (stream-copy) video whose declared container
        # duration can under-report the true frame span — the #549 scenario.
        clips = []
        for i, c in enumerate(["red", "green", "blue"]):
            p = tmp_path / f"seg_{i}.mp4"
            _run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={c}:s=320x240:r=30:d=2.0",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(p),
                ]
            )
            clips.append(p)
        list_file = tmp_path / "list.txt"
        list_file.write_text("".join(f"file '{p}'\n" for p in clips))
        video_only = tmp_path / "video_only.mp4"
        _run(vc._build_concat_cmd(list_file, video_only))

        # Podcast audio deliberately a touch shorter than the video span.
        audio = tmp_path / "audio.m4a"
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=5.85",
                "-c:a",
                "aac",
                str(audio),
            ]
        )

        out = tmp_path / "final.mp4"
        result = vc._finalize_output(
            video_only_path=video_only,
            video_duration=0.0,
            audio_path=audio,
            output_path=out,
            segment_count=len(clips),
            run=_run,
        )
        assert out.exists() and out.stat().st_size > 0
        video_dur, audio_dur = self._ffprobe_durations(ffprobe, out)
        assert audio_dur >= video_dur, (
            f"audio ({audio_dur:.3f}s) shorter than video ({video_dur:.3f}s) — "
            "Spotify would reject VIDEO_DURATION_LONGER_THAN_AUDIO"
        )
        assert result.has_audio
        # duration_seconds must reflect the padded audio (video + epsilon), not
        # the unpadded input audio, in the video>audio case (#549 review).
        assert result.duration_seconds >= video_dur
        assert result.duration_seconds == pytest.approx(video_dur + vc.AUDIO_PAD_EPSILON, abs=0.06)


class TestProbeVideoFrameSpan:
    """Unit coverage for the true-frame-span probe (#549)."""

    def test_computes_span_from_packet_count(self):
        import json as _json

        def _run(cmd):
            payload = _json.dumps(
                {
                    "streams": [
                        {
                            "nb_read_packets": "14316",
                            "avg_frame_rate": "30/1",
                            "r_frame_rate": "30/1",
                        }
                    ]
                }
            )
            return subprocess.CompletedProcess(cmd, 0, payload, "")

        span = vc._probe_video_frame_span(Path("/x.mp4"), _run, fallback=1.0)
        assert span == pytest.approx(477.2, abs=1e-6)

    def test_prefers_avg_frame_rate_falls_back_to_r_frame_rate(self):
        import json as _json

        # avg_frame_rate is 0/0 (unknown) → fall back to r_frame_rate.
        def _run(cmd):
            payload = _json.dumps(
                {
                    "streams": [
                        {
                            "nb_read_packets": "300",
                            "avg_frame_rate": "0/0",
                            "r_frame_rate": "30/1",
                        }
                    ]
                }
            )
            return subprocess.CompletedProcess(cmd, 0, payload, "")

        span = vc._probe_video_frame_span(Path("/x.mp4"), _run, fallback=1.0)
        assert span == pytest.approx(10.0, abs=1e-6)

    def test_falls_back_on_probe_failure(self):
        def _run(cmd):
            raise RuntimeError("boom")

        assert vc._probe_video_frame_span(Path("/x.mp4"), _run, fallback=12.5) == 12.5

    def test_falls_back_when_no_packets(self):
        import json as _json

        def _run(cmd):
            return subprocess.CompletedProcess(cmd, 0, _json.dumps({"streams": [{}]}), "")

        assert vc._probe_video_frame_span(Path("/x.mp4"), _run, fallback=7.0) == 7.0


class TestParseFps:
    """Unit coverage for the rational frame-rate parser (#549)."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("30/1", 30.0),
            ("60000/1001", pytest.approx(59.94, abs=0.01)),
            ("25", 25.0),
            ("0/0", 0.0),
            ("", 0.0),
            ("N/A", 0.0),
            ("30/0", 0.0),
        ],
    )
    def test_parse(self, value, expected):
        assert vc._parse_fps(value) == expected


class _FlakyNormalizeRunner:
    """Command runner that fails the normalize of one segment a fixed number of
    times before succeeding; all other commands succeed.

    A normalize/fit command is identified by its output path (the last arg)
    ending in ``seg_<idx>.mp4`` under the ``normalized`` directory.
    """

    def __init__(self, *, fail_seg: str, fail_times: int):
        self.fail_seg = fail_seg
        self.fail_times = fail_times
        self.calls: list[list[str]] = []
        self.norm_calls: dict[str, int] = {}

    def __call__(self, cmd):
        self.calls.append([str(a) for a in cmd])
        out = str(cmd[-1])
        if "normalized" in out and out.endswith(".mp4"):
            seg = Path(out).stem  # e.g. seg_001
            self.norm_calls[seg] = self.norm_calls.get(seg, 0) + 1
            if seg == self.fail_seg and self.norm_calls[seg] <= self.fail_times:
                raise subprocess.CalledProcessError(1, cmd, stderr="transient ffmpeg error")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


@pytest.fixture
def _instant_retry(monkeypatch):
    """Make retry backoff instantaneous for tests."""
    monkeypatch.setattr("podcaster.retry.time.sleep", lambda _s: None)


class TestNormalizeTaskRetry:
    def test_single_transient_failure_retries_only_that_task(self, tmp_path, _instant_retry):
        runner = _FlakyNormalizeRunner(fail_seg="seg_001", fail_times=1)
        seg0 = _make_recorded_segment(
            owner="a", name="b", duration=10.0, video_path=tmp_path / "seg0.webm"
        )
        seg1 = _make_recorded_segment(
            owner="c", name="d", duration=10.0, video_path=tmp_path / "seg1.webm"
        )
        (tmp_path / "seg0.webm").touch()
        (tmp_path / "seg1.webm").touch()

        result = compose_video(
            segments=[seg0, seg1],
            output_dir=tmp_path / "out",
            runner=runner,
        )

        # Pipeline completed despite the transient failure.
        assert result.segment_count == 2
        # The failing segment was normalized twice (1 failure + 1 success);
        # the healthy segment ran exactly once — only the failing task retried.
        assert runner.norm_calls["seg_001"] == 2
        assert runner.norm_calls["seg_000"] == 1

    def test_exhausted_retries_propagate(self, tmp_path, monkeypatch, _instant_retry):
        monkeypatch.setattr(vc, "NORMALIZE_TASK_RETRIES", 2)
        # Force the single-thread path so the failure surfaces deterministically.
        monkeypatch.setattr(vc, "NORMALIZE_WORKERS", 1)
        runner = _FlakyNormalizeRunner(fail_seg="seg_000", fail_times=99)
        seg0 = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg0.webm")
        (tmp_path / "seg0.webm").touch()

        with pytest.raises(subprocess.CalledProcessError):
            compose_video(
                segments=[seg0],
                output_dir=tmp_path / "out",
                runner=runner,
            )
        # Attempted exactly NORMALIZE_TASK_RETRIES times, then gave up.
        assert runner.norm_calls["seg_000"] == 2


class TestNormalizeTaskReporter:
    """Per-worker task progress wiring for the parallel normalize stage (#482)."""

    def test_reporter_receives_running_and_done_per_segment(self, tmp_path):
        runner = _mock_runner()
        seg1 = _make_recorded_segment(
            owner="a", name="b", duration=10.0, video_path=tmp_path / "seg1.webm"
        )
        seg2 = _make_recorded_segment(
            owner="c", name="d", duration=10.0, video_path=tmp_path / "seg2.webm"
        )
        (tmp_path / "seg1.webm").touch()
        (tmp_path / "seg2.webm").touch()

        events: list[tuple[str, str]] = []

        def reporter(task_id, status, **kwargs):
            events.append((task_id, status))

        compose_video(
            segments=[seg1, seg2],
            output_dir=tmp_path / "out",
            runner=runner,
            task_reporter=reporter,
        )

        # Each segment reports a running then a done task event.
        assert ("norm_000", "running") in events
        assert ("norm_000", "done") in events
        assert ("norm_001", "running") in events
        assert ("norm_001", "done") in events
        # running precedes done for each task.
        for tid in ("norm_000", "norm_001"):
            statuses = [s for (t, s) in events if t == tid]
            assert statuses == ["running", "done"]

    def test_reporter_reports_failed_on_normalize_error(self, tmp_path):
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        def _boom(cmd):
            raise subprocess.CalledProcessError(1, cmd)

        events: list[tuple[str, str]] = []

        with pytest.raises(subprocess.CalledProcessError):
            compose_video(
                segments=[seg],
                output_dir=tmp_path / "out",
                runner=_boom,
                task_reporter=lambda task_id, status, **kw: events.append((task_id, status)),
            )

        assert ("norm_000", "running") in events
        assert ("norm_000", "failed") in events
        assert ("norm_000", "done") not in events

    def test_failing_reporter_never_breaks_composition(self, tmp_path):
        runner = _mock_runner()
        seg = _make_recorded_segment(duration=10.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        def _bad_reporter(task_id, status, **kwargs):
            raise RuntimeError("reporter blew up")

        result = compose_video(
            segments=[seg],
            output_dir=tmp_path / "out",
            runner=runner,
            task_reporter=_bad_reporter,
        )
        assert result.segment_count == 1
