"""Tests for podcaster.video.video_compose module.

Unit tests mock ffmpeg via the CommandRunner protocol.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from podcaster.video.sync_plan import RepoReference, VideoSegment
from podcaster.video.video_gen import RecordedSegment
from podcaster.video.video_compose import (
    BOUNDARY_CONTENT_TO_CONTENT,
    BOUNDARY_CONTENT_TO_OUTRO,
    BOUNDARY_INTRO_TO_CONTENT,
    ENCODE_CRF,
    ENCODE_PIX_FMT,
    ENCODE_PRESET,
    INTRO_BLOB_PATH,
    OUTRO_BLOB_PATH,
    LOWER_THIRD_DURATION,
    OUTPUT_FPS,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    TRANSITION_FADE,
    TRANSITION_FADE_BLACK,
    TRANSITION_SLIDE_LEFT,
    TRANSITION_WIPE_LEFT,
    DEFAULT_DOG_LOGO_URL,
    DogLogoConfig,
    LowerThird,
    _build_audio_overlay_cmd,
    _build_canonical_av_cmd,
    _build_concat_cmd,
    _build_dog_overlay_filter,
    _build_drawtext_filter,
    _build_normalize_cmd,
    _build_xfade_filter,
    _compute_lower_thirds,
    _fetch_blob_cached,
    _fetch_intro_outro,
    _probe_drawtext_ffmpeg,
    compose_video,
    select_transitions,
)


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
        result = _build_xfade_filter(
            [10.0, 10.0, 10.0, 10.0], transition_duration=1.0
        )
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
            result = _probe_drawtext_ffmpeg(
                candidates=["/bad/ffmpeg", "/usr/bin/ffmpeg"]
            )
        assert result == "/usr/bin/ffmpeg"

    def test_timeout_is_skipped(self):
        """TimeoutExpired for a candidate is silently skipped."""
        outputs = [
            subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=10),
            self._make_proc(stdout="drawtext"),
        ]
        with patch("podcaster.video.video_compose.subprocess.run", side_effect=outputs):
            result = _probe_drawtext_ffmpeg(
                candidates=["/hung/ffmpeg", "/usr/bin/ffmpeg"]
            )
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

        final_cmd = runner.call_args_list[-1][0][0]
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
                result = compose_video(
                    segments=[seg], output_dir=tmp_path / "out", runner=runner
                )

        # Video must still be produced (no exception)
        assert result.segment_count == 1
        assert result.output_path.suffix == ".mp4"

        # Warning must have been emitted
        assert any("drawtext" in record.message for record in caplog.records)

        # Final compose command must NOT contain drawtext filter expressions
        final_cmd = runner.call_args_list[-1][0][0]
        assert "-filter_complex" not in final_cmd, "drawtext filter_complex should not be in compose cmd"
        assert not any(arg.startswith("drawtext=") for arg in final_cmd)

    def test_no_drawtext_probe_when_no_lower_thirds(self, tmp_path):
        """_find_drawtext_capable_ffmpeg is NOT called when no lower-thirds are needed."""
        runner = _mock_runner()
        # Duration=1.0 with transition_duration=0.1: lt_end=min(5.5, 0.5)=lt_start → no LT
        seg = _make_recorded_segment(duration=1.0, video_path=tmp_path / "seg.webm")
        (tmp_path / "seg.webm").touch()

        with patch(
            "podcaster.video.video_compose._find_drawtext_capable_ffmpeg"
        ) as mock_probe:
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
        result = select_transitions(3, [
            BOUNDARY_CONTENT_TO_CONTENT,
            BOUNDARY_CONTENT_TO_CONTENT,
            BOUNDARY_CONTENT_TO_CONTENT,
        ])
        # Should cycle: fade, slideleft, wipeleft
        assert result[0] == TRANSITION_FADE
        assert result[1] == TRANSITION_SLIDE_LEFT
        assert result[2] == TRANSITION_WIPE_LEFT

    def test_mixed_boundary_kinds(self):
        result = select_transitions(3, [
            BOUNDARY_INTRO_TO_CONTENT,
            BOUNDARY_CONTENT_TO_CONTENT,
            BOUNDARY_CONTENT_TO_OUTRO,
        ])
        assert result[0] == TRANSITION_FADE_BLACK
        assert result[1] == TRANSITION_FADE    # first content rotation slot
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

        # Find the filter_complex call
        all_calls = [call[0][0] for call in runner.call_args_list]
        compose_cmd = all_calls[-1]
        fc_idx = compose_cmd.index("-filter_complex") if "-filter_complex" in compose_cmd else -1
        assert fc_idx >= 0
        fc_value = compose_cmd[fc_idx + 1]
        # With 4 segments, should see at least 2 distinct transition types
        assert "fade" in fc_value
        assert "slideleft" in fc_value

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
        compose_cmd = all_calls[-1]
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
        compose_cmd = all_calls[-1]
        fc_idx = compose_cmd.index("-filter_complex")
        assert "wipeleft" in compose_cmd[fc_idx + 1]


# --- Tests for sync-map utilities (#296) ---


from podcaster.video.video_compose import (
    SyncedSegment,
    build_sync_map,
    trim_recording_cmd,
    apply_sync,
)
from podcaster.video.sync_plan import EpisodePlan


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
        seg = VideoSegment(
            repo=rec.segment.repo,
            start_seconds=0.0,
            duration_seconds=target_dur,
        )
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

    def _plan_from_recs(
        self, *recs: tuple[str, str, float, float]
    ) -> EpisodePlan:
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
        ss1 = self._make_ss("a", 20.0, 0.0, 10.0, src1)   # needs trim
        ss2 = self._make_ss("b", 8.0, 10.0, 10.0, src2)   # no trim
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


class TestFetchBlobCached:
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
        storage = _FakeStorage(
            {INTRO_BLOB_PATH: b"i", OUTRO_BLOB_PATH: b"o"}
        )
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
        cmd = _build_canonical_av_cmd(
            Path("/in.mp4"), Path("/out.mp4"), has_audio=True
        )
        assert cmd[0] == "ffmpeg"
        assert "0:a:0" in cmd
        assert "anullsrc" not in " ".join(cmd)
        assert "-shortest" not in cmd
        assert f"{OUTPUT_WIDTH}:{OUTPUT_HEIGHT}" in cmd[cmd.index("-filter_complex") + 1]

    def test_without_audio_synthesizes_silence(self):
        cmd = _build_canonical_av_cmd(
            Path("/in.mp4"), Path("/out.mp4"), has_audio=False
        )
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


class TestBuildAudioOverlayCmd:
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
        # audio is NEVER truncated: no -shortest anywhere
        assert "-shortest" not in cmd
        assert "-t" not in cmd
        assert str(tmp_path / "audio.mp3") in cmd
        assert cmd[-1] == str(tmp_path / "out.mp4")

    def test_extends_video_when_audio_is_longer(self, tmp_path):
        cmd = _build_audio_overlay_cmd(
            tmp_path / "video.mp4", tmp_path / "audio.mp3", tmp_path / "out.mp4",
            video_duration=10.0, audio_duration=15.0,
        )
        joined = " ".join(cmd)
        # video is re-encoded (not copied) so it can be padded + faded
        assert "-c:v" in cmd and "libx264" in cmd
        assert "copy" not in cmd
        # final frame held for 5s then faded to black over the last 2s
        assert "tpad=stop_mode=clone:stop_duration=5.000" in joined
        assert "fade=t=out:st=13.000:d=2.0" in joined
        # explicit bt709 color flags for Spotify consistency
        assert "-colorspace" in cmd and "bt709" in cmd
        assert "-shortest" not in cmd

    def test_copies_video_when_video_is_longer(self, tmp_path):
        cmd = _build_audio_overlay_cmd(
            tmp_path / "video.mp4", tmp_path / "audio.mp3", tmp_path / "out.mp4",
            video_duration=20.0, audio_duration=15.0,
        )
        assert "copy" in cmd
        assert "tpad" not in " ".join(cmd)
        assert "-shortest" not in cmd


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
        # The final command overlays the podcast MP3 onto the content
        final_cmd = cmds[-1]
        assert str(audio) in final_cmd
        assert "-shortest" not in final_cmd
        assert final_cmd[-1] == str(out)
        # Reported duration is the full (untruncated) length; the runner probes
        # both audio and video as 12.0s here.
        assert result.has_audio is True
        assert result.duration_seconds == pytest.approx(12.0)

    def test_bookends_and_audio_overlay_on_joined_video(self, tmp_path):
        # _ffprobe_runner answers every probe with the same duration, so intro,
        # outro and audio all report 5.0s here.
        runner = _ffprobe_runner(has_audio=True, duration=5.0)
        storage = _FakeStorage(
            {INTRO_BLOB_PATH: b"intro", OUTRO_BLOB_PATH: b"outro"}
        )
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
        concat_cmds = [
            c for c in cmds if "concat" in c and c[-1].endswith("joined.mp4")
        ]
        assert len(concat_cmds) == 1
        # canonicalized clips all generate a silent track (has_audio=False)
        canon_cmds = [
            c for c in cmds
            if "-filter_complex" in c and "join" in c[-1]
        ]
        assert len(canon_cmds) == 3
        assert all("anullsrc" in " ".join(c) for c in canon_cmds)
        # final command overlays the podcast MP3 on the joined video
        final_cmd = cmds[-1]
        assert str(audio) in final_cmd
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
        # 1 normalize + 1 compose, no ffprobe/concat
        assert runner.call_count == 2
        assert all(
            c.args[0][0] != "ffprobe" for c in runner.call_args_list
        )

    def test_prepends_intro_and_appends_outro(self, tmp_path):
        runner = _ffprobe_runner(has_audio=True, duration=5.0)
        storage = _FakeStorage(
            {INTRO_BLOB_PATH: b"intro", OUTRO_BLOB_PATH: b"outro"}
        )
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
        # a concat command writing the final output exists
        concat_cmds = [c for c in cmds if "concat" in c and c[-1] == str(out)]
        assert len(concat_cmds) == 1
        # two ffprobe calls (intro + outro)
        probe_cmds = [c for c in cmds if c[0] == "ffprobe"]
        assert len(probe_cmds) == 2
        # canonical re-encodes for intro, content, outro (3 canonicalize calls)
        canon_cmds = [c for c in cmds if "-filter_complex" in c and "[0:v]" in " ".join(c)
                      and "join" in c[-1]]
        assert len(canon_cmds) == 3
        # duration includes the two 5s clips added to the 10s content
        assert result.duration_seconds == pytest.approx(20.0)

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


# --- Tests for DOG (Digital On-Screen Graphic) watermark ---


class TestDogLogoConfig:
    def test_defaults_from_empty_dict(self):
        cfg = DogLogoConfig.from_dict({})
        assert cfg is not None
        assert cfg.url == DEFAULT_DOG_LOGO_URL
        assert cfg.position == "top-right"
        assert cfg.size == 80
        assert cfg.opacity == pytest.approx(0.3)

    def test_none_input_returns_none(self):
        assert DogLogoConfig.from_dict(None) is None
        assert DogLogoConfig.from_dict("nope") is None

    def test_custom_values(self):
        cfg = DogLogoConfig.from_dict({
            "url": "https://example.com/logo.png",
            "position": "bottom-left",
            "size": 120,
            "opacity": 0.5,
        })
        assert cfg.url == "https://example.com/logo.png"
        assert cfg.position == "bottom-left"
        assert cfg.size == 120
        assert cfg.opacity == pytest.approx(0.5)

    def test_invalid_values_fall_back(self):
        cfg = DogLogoConfig.from_dict({
            "url": "   ",
            "position": "middle",
            "size": "huge",
            "opacity": "bad",
        })
        assert cfg.url == DEFAULT_DOG_LOGO_URL
        assert cfg.position == "top-right"
        assert cfg.size == 80
        assert cfg.opacity == pytest.approx(0.3)

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
            c.args[0] for c in runner.call_args_list
            if "-filter_complex" in c.args[0]
        )
        joined = " ".join(compose_cmd)
        assert "overlay=" in joined
        assert "[dogout]" in joined
        # the logo image was added as an input
        assert str(logo) in compose_cmd

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
