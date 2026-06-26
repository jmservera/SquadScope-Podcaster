"""Tests for podcaster.video.edl_render — ffmpeg EDL renderer (#490)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from podcaster.script_plan import VisualMode
from podcaster.video.edl import (
    EditDecisionList,
    EdlSegment,
    EdlSegmentKind,
    SourceRange,
    TitleCardOverlay,
)
from podcaster.video.edl_render import (
    EdlRenderError,
    RenderConfig,
    build_render_plan,
    render_edl,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _clip_seg(index_start, end, clip_id, ranges, *, repo=None, section=None, card=None, xfade=0):
    return EdlSegment(
        kind=EdlSegmentKind.CLIP,
        timeline_start_ms=index_start,
        timeline_end_ms=end,
        visual_mode=VisualMode.REPO,
        clip_id=clip_id,
        repo_url=repo,
        section_id=section,
        source_ranges=tuple(SourceRange(*r) for r in ranges),
        crossfade_in_ms=xfade,
        title_card=card,
    )


def _interm_seg(start, end, *, card=None, xfade=0):
    return EdlSegment(
        kind=EdlSegmentKind.INTERMISSION,
        timeline_start_ms=start,
        timeline_end_ms=end,
        visual_mode=VisualMode.INTERMISSION,
        crossfade_in_ms=xfade,
        title_card=card,
    )


def _edl(segments, crossfade_ms=500):
    total = segments[-1].timeline_end_ms
    return EditDecisionList(
        segments=tuple(segments),
        total_duration_ms=total,
        crossfade_ms=crossfade_ms,
    )


# --- pure graph construction ---


def test_build_plan_basic_argv_and_graph():
    edl = _edl([
        _clip_seg(0, 10_000, "clip-a", [(0, 10_000)]),
        _interm_seg(10_000, 14_000),
    ])
    plan = build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4")
    assert plan.argv[0] == "ffmpeg"
    assert "-i" in plan.argv and "/clips/a.mp4" in plan.argv
    assert plan.inputs == ("/clips/a.mp4",)
    assert "trim=start=0.000:end=10.000" in plan.filter_complex
    assert "color=c=black" in plan.filter_complex
    assert plan.final_label == "vout"
    assert f"-map" in plan.argv and "[vout]" in plan.argv
    assert plan.output_path == "/out/ep.mp4"


def test_input_dedup_same_clip_one_input():
    edl = _edl([
        _clip_seg(0, 10_000, "clip-a", [(0, 10_000)]),
        _clip_seg(10_000, 20_000, "clip-a", [(20_000, 30_000)]),
    ])
    plan = build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4")
    assert plan.inputs == ("/clips/a.mp4",)
    assert plan.argv.count("-i") == 1
    # both segments reference input index 0
    assert "[0:v]trim=start=0.000:end=10.000" in plan.filter_complex
    assert "[0:v]trim=start=20.000:end=30.000" in plan.filter_complex


def test_multi_range_segment_uses_concat():
    edl = _edl([
        _clip_seg(0, 10_000, "clip-a", [(0, 4_000), (6_000, 12_000)]),
    ])
    plan = build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4")
    assert "[s0_0][s0_1]concat=n=2:v=1:a=0[seg0_raw]" in plan.filter_complex


def test_single_segment_no_join():
    edl = _edl([_clip_seg(0, 10_000, "clip-a", [(0, 10_000)])])
    plan = build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4")
    assert plan.final_label == "seg0"
    assert "concat=n=" not in plan.filter_complex  # single range + single segment
    assert plan.expected_duration_ms == 10_000


def test_title_card_drawtext():
    card = TitleCardOverlay(text="AI Frameworks", duration_ms=2_000)
    edl = _edl([_clip_seg(0, 10_000, "clip-a", [(0, 10_000)], card=card)])
    plan = build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4")
    assert "drawtext=" in plan.filter_complex
    assert "AI Frameworks" in plan.filter_complex
    assert "between(t,0,2.000)" in plan.filter_complex


def test_concat_join_duration_is_exact():
    edl = _edl([
        _clip_seg(0, 10_000, "clip-a", [(0, 10_000)], xfade=0),
        _clip_seg(10_000, 25_000, "clip-b", [(0, 15_000)], xfade=500),
    ])
    plan = build_render_plan(edl, {"clip-a": "/a.mp4", "clip-b": "/b.mp4"}, "/out.mp4")
    assert "concat=n=2:v=1:a=0[vout]" in plan.filter_complex
    assert plan.expected_duration_ms == 25_000


def test_xfade_join_offsets_and_duration():
    edl = _edl([
        _clip_seg(0, 10_000, "clip-a", [(0, 10_000)], xfade=0),
        _clip_seg(10_000, 25_000, "clip-b", [(0, 15_000)], xfade=500),
    ])
    cfg = RenderConfig(enable_crossfades=True)
    plan = build_render_plan(edl, {"clip-a": "/a.mp4", "clip-b": "/b.mp4"}, "/out.mp4", config=cfg)
    # one transition: offset = 10.0 - 0.5 = 9.5s, duration 0.5s
    assert "xfade=transition=fade:duration=0.500:offset=9.500[vout]" in plan.filter_complex
    # output shortened by the 500ms overlap
    assert plan.expected_duration_ms == 24_500


def test_determinism():
    edl = _edl([
        _clip_seg(0, 10_000, "clip-a", [(0, 10_000)]),
        _interm_seg(10_000, 14_000),
    ])
    a = build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4")
    b = build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4")
    assert a == b


# --- errors ---


def test_empty_edl_raises():
    with pytest.raises(EdlRenderError):
        build_render_plan(EditDecisionList(), {}, "/out.mp4")


def test_missing_clip_path_raises():
    edl = _edl([_clip_seg(0, 10_000, "clip-a", [(0, 10_000)])])
    with pytest.raises(EdlRenderError):
        build_render_plan(edl, {}, "/out.mp4")


def test_clip_segment_without_source_ranges_raises():
    edl = _edl([_clip_seg(0, 10_000, "clip-a", [])])
    with pytest.raises(EdlRenderError, match="no source ranges"):
        build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out.mp4")


def test_render_edl_propagates_ffmpeg_failure():
    edl = _edl([_clip_seg(0, 10_000, "clip-a", [(0, 10_000)])])
    calls = {}

    def fake_runner(cmd):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    with pytest.raises(EdlRenderError, match="ffmpeg failed"):
        render_edl(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4", runner=fake_runner)
    assert calls["cmd"][0] == "ffmpeg"


# --- real ffmpeg integration (skipped when ffmpeg is unavailable) ---


def _make_clip(path: Path, color: str, seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-i",
            f"color=c={color}:s=320x240:r=30:d={seconds}",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )


def _probe_seconds(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_render_matches_edl_duration(tmp_path):
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, "red", 6.0)
    _make_clip(clip_b, "blue", 6.0)

    edl = _edl([
        _clip_seg(0, 3_000, "clip-a", [(0, 3_000)], section="s1"),
        _interm_seg(3_000, 5_000),
        _clip_seg(5_000, 9_000, "clip-b", [(0, 2_000), (3_000, 5_000)]),
    ])
    cfg = RenderConfig(width=320, height=240, fps=30, preset="ultrafast")
    out = tmp_path / "ep.mp4"
    render_edl(edl, {"clip-a": clip_a, "clip-b": clip_b}, out, config=cfg)

    assert out.exists()
    # hard-cut concat → duration equals the EDL total (9s) within tolerance
    assert abs(_probe_seconds(out) - 9.0) < 0.3


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_render_with_crossfade(tmp_path):
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, "green", 12.0)
    _make_clip(clip_b, "white", 12.0)

    edl = _edl([
        _clip_seg(0, 9_000, "clip-a", [(0, 9_000)], xfade=0),
        _clip_seg(9_000, 18_000, "clip-b", [(0, 9_000)], xfade=500),
    ])
    cfg = RenderConfig(width=320, height=240, fps=30, preset="ultrafast", enable_crossfades=True)
    out = tmp_path / "ep_xfade.mp4"
    render_edl(edl, {"clip-a": clip_a, "clip-b": clip_b}, out, config=cfg)
    # expected = 18s - 0.5s overlap = 17.5s
    assert abs(_probe_seconds(out) - 17.5) < 0.3
