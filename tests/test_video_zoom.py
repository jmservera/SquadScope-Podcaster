"""Tests for podcaster.video.zoom module (#299)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from podcaster.video.sync_plan import RepoReference, VideoSegment
from podcaster.video.video_gen import RecordedSegment
from podcaster.video.zoom import (
    DEFAULT_FPS,
    DEFAULT_VIDEO_H,
    DEFAULT_VIDEO_W,
    DEFAULT_ZOOM_LEVEL,
    FocusRegion,
    ZoomSpec,
    _zoompan_exprs,
    apply_zoom_to_segment,
    build_zoompan_cmd,
    find_focus_regions_from_script,
)


# --- Helpers ---


def _mock_runner() -> MagicMock:
    runner = MagicMock()
    runner.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    return runner


def _make_focus(
    x: float = 400.0,
    y: float = 200.0,
    w: float = 400.0,
    h: float = 300.0,
    start: float = 0.0,
    dur: float = 2.0,
    label: str = "diagram",
) -> FocusRegion:
    return FocusRegion(
        x=x, y=y, width=w, height=h,
        start_seconds=start, duration_seconds=dur, label=label,
    )


def _make_spec(focus: FocusRegion | None = None, **kw) -> ZoomSpec:
    return ZoomSpec(focus=focus or _make_focus(), **kw)


def _make_recorded(
    name: str = "repo",
    dur: float = 10.0,
    video_path: Path | None = None,
) -> RecordedSegment:
    seg = VideoSegment(
        repo=RepoReference(owner="test", name=name),
        start_seconds=0.0,
        duration_seconds=dur,
    )
    return RecordedSegment(
        segment=seg,
        video_path=video_path or Path(f"/recordings/{name}.webm"),
    )


# --- FocusRegion tests ---


class TestFocusRegion:
    def test_center_x(self):
        f = FocusRegion(x=100.0, y=50.0, width=400.0, height=200.0,
                        start_seconds=0.0, duration_seconds=2.0)
        assert f.center_x == pytest.approx(300.0)

    def test_center_y(self):
        f = FocusRegion(x=100.0, y=50.0, width=400.0, height=200.0,
                        start_seconds=0.0, duration_seconds=2.0)
        assert f.center_y == pytest.approx(150.0)

    def test_center_symmetric(self):
        # Centered region at 960x540 → center at (960, 540)
        f = FocusRegion(x=760.0, y=390.0, width=400.0, height=300.0,
                        start_seconds=0.0, duration_seconds=1.0)
        assert f.center_x == pytest.approx(960.0)
        assert f.center_y == pytest.approx(540.0)

    def test_default_label_empty(self):
        f = FocusRegion(x=0.0, y=0.0, width=100.0, height=100.0,
                        start_seconds=0.0, duration_seconds=1.0)
        assert f.label == ""


# --- ZoomSpec tests ---


class TestZoomSpec:
    def test_defaults(self):
        spec = ZoomSpec(focus=_make_focus())
        assert spec.zoom_level == DEFAULT_ZOOM_LEVEL
        assert spec.ease_in_s == 0.5
        assert spec.ease_out_s == 0.5

    def test_custom_zoom_level(self):
        spec = ZoomSpec(focus=_make_focus(), zoom_level=1.5)
        assert spec.zoom_level == 1.5


# --- _zoompan_exprs tests ---


class TestZoompanExprs:
    def test_returns_three_strings(self):
        spec = _make_spec()
        result = _zoompan_exprs(spec)
        assert len(result) == 3
        assert all(isinstance(s, str) for s in result)

    def test_z_expr_contains_zoom_level(self):
        spec = ZoomSpec(focus=_make_focus(), zoom_level=2.0)
        z, x, y = _zoompan_exprs(spec)
        assert "2.0000" in z

    def test_z_expr_references_n(self):
        z, _, _ = _zoompan_exprs(_make_spec())
        assert "n" in z

    def test_x_expr_has_clamp(self):
        _, x, _ = _zoompan_exprs(_make_spec())
        assert "max(0" in x
        assert "min(iw-iw/zoom" in x

    def test_y_expr_has_clamp(self):
        _, _, y = _zoompan_exprs(_make_spec())
        assert "max(0" in y
        assert "min(ih-ih/zoom" in y

    def test_focus_center_in_x_expr(self):
        focus = _make_focus(x=400.0, y=200.0, w=400.0, h=300.0)  # cx=600, cy=350
        _, x, _ = _zoompan_exprs(ZoomSpec(focus=focus))
        assert "600.00" in x

    def test_focus_center_in_y_expr(self):
        focus = _make_focus(x=400.0, y=200.0, w=400.0, h=300.0)  # cx=600, cy=350
        _, _, y = _zoompan_exprs(ZoomSpec(focus=focus))
        assert "350.00" in y

    def test_start_frame_offset_respected(self):
        # Focus starts at 2.0s → frame 60 at 30fps
        focus = _make_focus(start=2.0, dur=1.0)
        z, _, _ = _zoompan_exprs(ZoomSpec(focus=focus), fps=30)
        # Frame 60 should appear as the start boundary in z_expr
        assert "lt(n,60)" in z

    def test_ease_in_frames_from_seconds(self):
        # ease_in_s=0.5 → 15 frames at 30fps
        spec = ZoomSpec(focus=_make_focus(start=0.0, dur=2.0), ease_in_s=0.5)
        z, _, _ = _zoompan_exprs(spec, fps=30)
        # ease_in_end = 0 + 15 = 15
        assert "lte(n,15)" in z

    def test_different_zoom_levels_produce_different_exprs(self):
        f = _make_focus()
        z1, _, _ = _zoompan_exprs(ZoomSpec(focus=f, zoom_level=1.5))
        z2, _, _ = _zoompan_exprs(ZoomSpec(focus=f, zoom_level=2.5))
        assert z1 != z2

    def test_returns_to_full_view(self):
        # After ease-out, z_expr should return 1.0
        z, _, _ = _zoompan_exprs(_make_spec())
        assert z.endswith(",1.0))))")

    def test_x_returns_to_full_center_after_zoom(self):
        # After ease-out, x_center should be iw/2
        _, x, _ = _zoompan_exprs(_make_spec())
        assert "iw/2" in x


# --- build_zoompan_cmd tests ---


class TestBuildZoompanCmd:
    def test_basic_structure(self):
        spec = _make_spec()
        cmd = build_zoompan_cmd(Path("/in/v.webm"), Path("/out/v.mp4"), spec)
        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert "/in/v.webm" in cmd
        assert "/out/v.mp4" in cmd
        assert "-vf" in cmd

    def test_zoompan_in_filter(self):
        cmd = build_zoompan_cmd(Path("/a.webm"), Path("/b.mp4"), _make_spec())
        vf_idx = cmd.index("-vf")
        vf = cmd[vf_idx + 1]
        assert vf.startswith("zoompan=z='")
        assert ":x='" in vf
        assert ":y='" in vf
        assert ":d=1" in vf

    def test_output_size_in_filter(self):
        cmd = build_zoompan_cmd(
            Path("/a.webm"), Path("/b.mp4"), _make_spec(), video_w=1280, video_h=720
        )
        vf_idx = cmd.index("-vf")
        vf = cmd[vf_idx + 1]
        assert "s=1280x720" in vf

    def test_fps_in_filter(self):
        cmd = build_zoompan_cmd(
            Path("/a.webm"), Path("/b.mp4"), _make_spec(), fps=25
        )
        vf_idx = cmd.index("-vf")
        vf = cmd[vf_idx + 1]
        assert "fps=25" in vf

    def test_custom_ffmpeg_bin(self):
        cmd = build_zoompan_cmd(
            Path("/a.webm"), Path("/b.mp4"), _make_spec(),
            ffmpeg_bin="/usr/bin/ffmpeg",
        )
        assert cmd[0] == "/usr/bin/ffmpeg"

    def test_no_audio_stream(self):
        cmd = build_zoompan_cmd(Path("/a.webm"), Path("/b.mp4"), _make_spec())
        assert "-an" in cmd


# --- apply_zoom_to_segment tests ---


class TestApplyZoomToSegment:
    def test_no_specs_returns_original_unchanged(self, tmp_path):
        rec = _make_recorded()
        result = apply_zoom_to_segment(rec, [], tmp_path / "out")
        assert result is rec

    def test_single_spec_runs_ffmpeg(self, tmp_path):
        runner = _mock_runner()
        src = tmp_path / "seg.webm"
        src.touch()
        rec = _make_recorded(video_path=src)
        spec = _make_spec()
        result = apply_zoom_to_segment(rec, [spec], tmp_path / "out", runner=runner)
        runner.assert_called_once()
        assert result.video_path != rec.video_path

    def test_output_path_is_in_output_dir(self, tmp_path):
        runner = _mock_runner()
        src = tmp_path / "seg.webm"
        src.touch()
        rec = _make_recorded(video_path=src)
        out_dir = tmp_path / "zoomed"
        result = apply_zoom_to_segment(rec, [_make_spec()], out_dir, runner=runner)
        assert result.video_path.parent == out_dir

    def test_multiple_specs_chain(self, tmp_path):
        runner = _mock_runner()
        src = tmp_path / "seg.webm"
        src.touch()
        rec = _make_recorded(video_path=src)
        specs = [_make_spec(_make_focus(start=0.0, dur=1.0)),
                 _make_spec(_make_focus(start=3.0, dur=1.0))]
        apply_zoom_to_segment(rec, specs, tmp_path / "out", runner=runner)
        assert runner.call_count == 2

    def test_creates_output_dir(self, tmp_path):
        runner = _mock_runner()
        src = tmp_path / "seg.webm"
        src.touch()
        rec = _make_recorded(video_path=src)
        out_dir = tmp_path / "nested" / "output"
        apply_zoom_to_segment(rec, [_make_spec()], out_dir, runner=runner)
        assert out_dir.is_dir()

    def test_segment_metadata_preserved(self, tmp_path):
        runner = _mock_runner()
        src = tmp_path / "seg.webm"
        src.touch()
        rec = _make_recorded(video_path=src, dur=15.0)
        result = apply_zoom_to_segment(rec, [_make_spec()], tmp_path / "out", runner=runner)
        assert result.segment.duration_seconds == pytest.approx(15.0)
        assert result.segment.repo == rec.segment.repo
        assert result.is_fallback == rec.is_fallback
        assert result.has_pages == rec.has_pages

    def test_graceful_no_zoom_with_empty_specs(self):
        rec = _make_recorded()
        result = apply_zoom_to_segment(rec, [], Path("/any"))
        assert result is rec


# --- find_focus_regions_from_script tests ---


class TestFindFocusRegionsFromScript:
    def test_returns_empty_list(self):
        seg = VideoSegment(
            repo=RepoReference(owner="a", name="b"),
            start_seconds=0.0,
            duration_seconds=30.0,
        )
        result = find_focus_regions_from_script("some script mentioning a/b", seg)
        assert result == []

    def test_returns_list_type(self):
        seg = VideoSegment(
            repo=RepoReference(owner="x", name="y"),
            start_seconds=10.0,
            duration_seconds=20.0,
        )
        result = find_focus_regions_from_script("", seg)
        assert isinstance(result, list)

    def test_empty_script_returns_empty(self):
        seg = VideoSegment(
            repo=RepoReference(owner="a", name="b"),
            start_seconds=0.0,
            duration_seconds=5.0,
        )
        assert find_focus_regions_from_script("", seg) == []
