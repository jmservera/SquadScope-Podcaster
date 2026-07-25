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
    degrade_for_render,
    render_edl,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _ffmpeg_has_drawtext() -> bool:
    if not _HAS_FFMPEG:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(
        line.split()[1:2] == ["drawtext"] for line in out.stdout.splitlines() if line.split()
    )


_HAS_DRAWTEXT = _ffmpeg_has_drawtext()


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


def test_build_plan_basic_argv_and_graph(monkeypatch):
    monkeypatch.setattr(
        "podcaster.video.edl_render.resolve_intermission_video_path", lambda _: None
    )
    edl = _edl(
        [
            _clip_seg(0, 10_000, "clip-a", [(0, 10_000)]),
            _interm_seg(10_000, 14_000),
        ]
    )
    plan = build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4")
    assert plan.argv[0] == "ffmpeg"
    assert "-i" in plan.argv and "/clips/a.mp4" in plan.argv
    assert plan.inputs == ("/clips/a.mp4",)
    assert "trim=start=0.000:end=10.000" in plan.filter_complex
    assert "color=c=black" in plan.filter_complex
    assert plan.final_label == "vout"
    assert "-map" in plan.argv and "[vout]" in plan.argv
    assert plan.output_path == "/out/ep.mp4"


def test_intermission_uses_animation_asset_with_title_overlay():
    asset = Path("build/test-edl-render/intermission.mp4")
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"not-a-real-video")
    try:
        card = TitleCardOverlay(text="Claracle", duration_ms=2_000)
        edl = _edl([_interm_seg(0, 4_000, card=card)])
        plan = build_render_plan(
            edl,
            {},
            "build/test-edl-render/out.mp4",
            config=RenderConfig(intermission_video_path=str(asset)),
        )
    finally:
        asset.unlink(missing_ok=True)
        try:
            asset.parent.rmdir()
        except OSError:
            pass

    assert plan.inputs == (str(asset),)
    assert "-stream_loop" in plan.argv
    assert "color=c=" not in plan.filter_complex
    assert "[0:v]trim=duration=4.000,setpts=PTS-STARTPTS" in plan.filter_complex
    assert "drawtext=" in plan.filter_complex
    assert "Claracle" in plan.filter_complex


def test_input_dedup_same_clip_one_input():
    edl = _edl(
        [
            _clip_seg(0, 10_000, "clip-a", [(0, 10_000)]),
            _clip_seg(10_000, 20_000, "clip-a", [(20_000, 30_000)]),
        ]
    )
    plan = build_render_plan(edl, {"clip-a": "/clips/a.mp4"}, "/out/ep.mp4")
    assert plan.inputs == ("/clips/a.mp4",)
    assert plan.argv.count("-i") == 1
    # both segments reference input index 0
    assert "[0:v]trim=start=0.000:end=10.000" in plan.filter_complex
    assert "[0:v]trim=start=20.000:end=30.000" in plan.filter_complex


def test_multi_range_segment_uses_concat():
    edl = _edl(
        [
            _clip_seg(0, 10_000, "clip-a", [(0, 4_000), (6_000, 12_000)]),
        ]
    )
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
    edl = _edl(
        [
            _clip_seg(0, 10_000, "clip-a", [(0, 10_000)], xfade=0),
            _clip_seg(10_000, 25_000, "clip-b", [(0, 15_000)], xfade=500),
        ]
    )
    plan = build_render_plan(edl, {"clip-a": "/a.mp4", "clip-b": "/b.mp4"}, "/out.mp4")
    assert "concat=n=2:v=1:a=0[vout]" in plan.filter_complex
    assert plan.expected_duration_ms == 25_000


def test_xfade_join_offsets_and_duration():
    edl = _edl(
        [
            _clip_seg(0, 10_000, "clip-a", [(0, 10_000)], xfade=0),
            _clip_seg(10_000, 25_000, "clip-b", [(0, 15_000)], xfade=500),
        ]
    )
    cfg = RenderConfig(enable_crossfades=True)
    plan = build_render_plan(edl, {"clip-a": "/a.mp4", "clip-b": "/b.mp4"}, "/out.mp4", config=cfg)
    # one transition: offset = 10.0 - 0.5 = 9.5s, duration 0.5s
    assert "xfade=transition=fade:duration=0.500:offset=9.500[vout]" in plan.filter_complex
    # output shortened by the 500ms overlap
    assert plan.expected_duration_ms == 24_500


def test_determinism():
    edl = _edl(
        [
            _clip_seg(0, 10_000, "clip-a", [(0, 10_000)]),
            _interm_seg(10_000, 14_000),
        ]
    )
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


def _screenshot_seg(start, end, image_id, *, repo=None, section=None, card=None, xfade=0):
    return EdlSegment(
        kind=EdlSegmentKind.SCREENSHOT,
        timeline_start_ms=start,
        timeline_end_ms=end,
        visual_mode=VisualMode.REPO,
        repo_url=repo,
        section_id=section,
        crossfade_in_ms=xfade,
        title_card=card,
        is_fallback=True,
        fallback_image_id=image_id,
    )


def _card_seg(start, end, text, *, repo=None, section=None, xfade=0):
    return EdlSegment(
        kind=EdlSegmentKind.CARD,
        timeline_start_ms=start,
        timeline_end_ms=end,
        visual_mode=VisualMode.REPO,
        repo_url=repo,
        section_id=section,
        crossfade_in_ms=xfade,
        is_fallback=True,
        fallback_text=text,
    )


# --- fallback chain rendering (#489) ---


def test_build_plan_renders_card_fill_with_text():
    edl = _edl([_card_seg(0, 10_000, "owner/repo-b")])
    plan = build_render_plan(edl, {}, "/out.mp4")
    assert plan.inputs == ()  # cards need no input
    assert "color=c=" in plan.filter_complex
    assert "drawtext" in plan.filter_complex
    assert "owner/repo-b" in plan.filter_complex


def test_build_plan_screenshot_uses_looped_image_input():
    edl = _edl([_screenshot_seg(0, 10_000, "shot-b", repo="r")])
    plan = build_render_plan(edl, {}, "/out.mp4", image_paths={"shot-b": "/imgs/b.png"})
    assert plan.inputs == ("/imgs/b.png",)
    # still image is held for the segment with -loop 1 -t <dur>
    assert "-loop" in plan.argv and "1" in plan.argv
    assert "-t" in plan.argv and "10.000" in plan.argv
    assert "[0:v]" in plan.filter_complex


def test_build_plan_screenshot_missing_image_raises():
    edl = _edl([_screenshot_seg(0, 10_000, "shot-b", repo="r")])
    with pytest.raises(EdlRenderError, match="unknown image id"):
        build_render_plan(edl, {}, "/out.mp4", image_paths={})


def test_degrade_for_render_missing_clip_to_card():
    edl = _edl([_clip_seg(0, 10_000, "clip-a", [(0, 10_000)], repo="https://x/owner/repo-a")])
    # clip-a path not provided → degrade
    degraded = degrade_for_render(edl, {})
    seg = degraded.segments[0]
    assert seg.kind is EdlSegmentKind.CARD
    assert seg.is_fallback is True
    assert seg.fallback_text == "owner/repo-a"
    assert seg.clip_id is None and seg.source_ranges == ()
    # timeline bounds preserved
    assert (seg.timeline_start_ms, seg.timeline_end_ms) == (0, 10_000)


def test_degrade_for_render_prefers_screenshot():
    edl = _edl([_clip_seg(0, 10_000, "clip-a", [(0, 10_000)], repo="r")])
    degraded = degrade_for_render(
        edl, {}, image_paths={"shot-r": "/i.png"}, screenshots={"r": "shot-r"}
    )
    seg = degraded.segments[0]
    assert seg.kind is EdlSegmentKind.SCREENSHOT
    assert seg.fallback_image_id == "shot-r"


def test_degrade_for_render_noop_when_all_present():
    edl = _edl([_clip_seg(0, 10_000, "clip-a", [(0, 10_000)])])
    degraded = degrade_for_render(edl, {"clip-a": "/a.mp4"})
    assert degraded is edl


def test_render_edl_does_not_fail_on_missing_clip(monkeypatch):
    # The whole point of #489: a missing clip must not hard-fail the render.
    edl = _edl([_clip_seg(0, 10_000, "clip-a", [(0, 10_000)], repo="https://x/o/repo")])
    captured = {}

    def fake_runner(cmd):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    # clip-a path missing → degraded to a card; pretend the output got written.
    monkeypatch.setattr(Path, "exists", lambda self: True)
    render_edl(edl, {}, "/out/ep.mp4", runner=fake_runner)

    cmd = captured["cmd"]
    # no -i inputs (card fill), but a color/drawtext graph was built
    assert "-i" not in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "color=c=" in fc and "drawtext" in fc


# --- real ffmpeg integration (skipped when ffmpeg is unavailable) ---


def _make_clip(path: Path, color: str, seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x240:r=30:d={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _probe_seconds(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_render_matches_edl_duration(tmp_path):
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, "red", 6.0)
    _make_clip(clip_b, "blue", 6.0)

    edl = _edl(
        [
            _clip_seg(0, 3_000, "clip-a", [(0, 3_000)], section="s1"),
            _interm_seg(3_000, 5_000),
            _clip_seg(5_000, 9_000, "clip-b", [(0, 2_000), (3_000, 5_000)]),
        ]
    )
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

    edl = _edl(
        [
            _clip_seg(0, 9_000, "clip-a", [(0, 9_000)], xfade=0),
            _clip_seg(9_000, 18_000, "clip-b", [(0, 9_000)], xfade=500),
        ]
    )
    cfg = RenderConfig(width=320, height=240, fps=30, preset="ultrafast", enable_crossfades=True)
    out = tmp_path / "ep_xfade.mp4"
    render_edl(edl, {"clip-a": clip_a, "clip-b": clip_b}, out, config=cfg)
    # expected = 18s - 0.5s overlap = 17.5s
    assert abs(_probe_seconds(out) - 17.5) < 0.3


def _make_image(path: Path, color: str) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x240",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_render_screenshot_fallback_duration(tmp_path):
    # A real clip plus a screenshot still (no drawtext needed) must concatenate
    # to the EDL total — proving the screenshot fallback renders end to end.
    clip_a = tmp_path / "a.mp4"
    _make_clip(clip_a, "red", 6.0)
    shot = tmp_path / "shot.png"
    _make_image(shot, "blue")

    edl = _edl(
        [
            _clip_seg(0, 4_000, "clip-a", [(0, 4_000)]),
            _screenshot_seg(4_000, 9_000, "shot-x"),
        ]
    )
    cfg = RenderConfig(width=320, height=240, fps=30, preset="ultrafast")
    out = tmp_path / "ep_shot.mp4"
    render_edl(edl, {"clip-a": clip_a}, out, image_paths={"shot-x": shot}, config=cfg)
    assert out.exists()
    assert abs(_probe_seconds(out) - 9.0) < 0.3


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not available")
def test_real_render_degrades_missing_clip_to_screenshot(tmp_path):
    # A clip with no provided path is degraded — with a screenshot available it
    # becomes a screenshot fill and still renders (no hard failure, #489).
    clip_a = tmp_path / "a.mp4"
    _make_clip(clip_a, "green", 6.0)
    shot = tmp_path / "shot.png"
    _make_image(shot, "white")

    edl = _edl(
        [
            _clip_seg(0, 4_000, "clip-a", [(0, 4_000)]),
            _clip_seg(4_000, 9_000, "clip-missing", [(0, 5_000)], repo="https://x/o/repo-z"),
        ]
    )
    cfg = RenderConfig(width=320, height=240, fps=30, preset="ultrafast")
    out = tmp_path / "ep_degraded.mp4"
    render_edl(
        edl,
        {"clip-a": clip_a},
        out,
        image_paths={"shot-z": shot},
        screenshots={"https://x/o/repo-z": "shot-z"},
        config=cfg,
    )
    assert out.exists()
    assert abs(_probe_seconds(out) - 9.0) < 0.3


@pytest.mark.skipif(not _HAS_DRAWTEXT, reason="ffmpeg drawtext filter not available")
def test_real_render_card_fallback_duration(tmp_path):
    # A real clip plus a text card (drawtext) — the card fill renders and the
    # total duration matches the EDL.
    clip_a = tmp_path / "a.mp4"
    _make_clip(clip_a, "red", 6.0)

    edl = _edl(
        [
            _clip_seg(0, 4_000, "clip-a", [(0, 4_000)]),
            _card_seg(4_000, 9_000, "owner/missing-repo"),
        ]
    )
    cfg = RenderConfig(width=320, height=240, fps=30, preset="ultrafast")
    out = tmp_path / "ep_card.mp4"
    render_edl(edl, {"clip-a": clip_a}, out, config=cfg)
    assert out.exists()
    assert abs(_probe_seconds(out) - 9.0) < 0.3
