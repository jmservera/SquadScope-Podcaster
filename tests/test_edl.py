"""Tests for podcaster.video.edl — Layer 3 timeline planner / EDL (#488)."""

from __future__ import annotations

import pytest

from podcaster.audio_metadata import RealizedAudioMetadata, TopicRange
from podcaster.script_plan import VisualMode
from podcaster.video.clip_manifest import (
    ClipManifest,
    LoopSection,
    TrimRange,
)
from podcaster.video.edl import (
    EDL_SCHEMA_VERSION,
    EditDecisionList,
    EdlError,
    EdlSegment,
    EdlSegmentKind,
    SourceRange,
    plan_edl,
    plan_source_ranges,
    validate_edl,
)

REPO_A = "https://github.com/owner/repo-a"
REPO_B = "https://github.com/owner/repo-b"


def _topic(mode, start, end, repo=None, section=None, indices=(0,)):
    return TopicRange(
        visual_mode=mode,
        start_ms=start,
        end_ms=end,
        utterance_indices=indices,
        repo_url=repo,
        section_id=section,
    )


def _clip(clip_id, duration, repo=None, trim=(), loop=(), fallback=False):
    return ClipManifest(
        clip_id=clip_id,
        duration_ms=duration,
        repo_url=repo,
        trim_ranges=tuple(trim),
        loop_sections=tuple(loop),
        is_fallback=fallback,
    )


# --- plan_source_ranges: trim / loop / exact ---


def test_source_ranges_exact():
    m = _clip("c", 1_000)
    ranges, looped = plan_source_ranges(m, 1_000)
    assert ranges == (SourceRange(0, 1_000),)
    assert looped is False


def test_source_ranges_trim_from_safe_range():
    m = _clip("c", 1_000, trim=[TrimRange(100, 900)])
    ranges, looped = plan_source_ranges(m, 600)
    # excess 400 removed from the tail of the safe range [500,900]
    assert ranges == (SourceRange(0, 500), SourceRange(900, 1_000))
    assert sum(r.duration_ms for r in ranges) == 600
    assert looped is False


def test_source_ranges_trim_insufficient_safe_range_trims_tail():
    m = _clip("c", 1_000, trim=[TrimRange(100, 300)])  # only 200ms safe
    ranges, looped = plan_source_ranges(m, 600)  # need to remove 400ms
    assert sum(r.duration_ms for r in ranges) == 600
    # safe range removed, plus 200ms hard-trimmed off the clip tail
    assert ranges == (SourceRange(0, 100), SourceRange(300, 800))
    assert looped is False


def test_source_ranges_loop_to_fill():
    m = _clip("c", 1_000, loop=[LoopSection(200, 700)])  # 500ms loopable
    ranges, looped = plan_source_ranges(m, 1_800)  # deficit 800
    assert ranges[0] == SourceRange(0, 1_000)
    assert sum(r.duration_ms for r in ranges) == 1_800
    assert looped is True


def test_source_ranges_loop_without_loop_sections_repeats_clip():
    m = _clip("c", 1_000)
    ranges, looped = plan_source_ranges(m, 2_500)
    assert sum(r.duration_ms for r in ranges) == 2_500
    assert looped is True


# --- plan_edl: basic gap-free coverage ---


def _basic_metadata():
    topics = (
        _topic(VisualMode.ARTICLE, 0, 10_000),
        _topic(VisualMode.REPO, 10_500, 30_000, REPO_A, "s1"),
        _topic(VisualMode.REPO, 30_500, 45_000, REPO_B, "s1"),
        _topic(VisualMode.INTERMISSION, 45_500, 50_000, None, "s2"),
    )
    return RealizedAudioMetadata(topics=topics, total_duration_ms=50_000)


def _basic_clips():
    return {
        REPO_A: _clip("clip-a", 60_000, REPO_A, trim=[TrimRange(1_000, 59_000)]),
        REPO_B: _clip("clip-b", 60_000, REPO_B, trim=[TrimRange(1_000, 59_000)]),
    }


def test_plan_edl_is_gap_free_and_valid():
    meta = _basic_metadata()
    article = _clip("clip-article", 30_000, trim=[TrimRange(500, 29_500)])
    edl = plan_edl(meta, _basic_clips(), article_clip=article)

    validate_edl(edl)  # raises on any invariant violation
    assert edl.total_duration_ms == 50_000
    assert [s.kind for s in edl.segments] == [
        EdlSegmentKind.CLIP,
        EdlSegmentKind.CLIP,
        EdlSegmentKind.CLIP,
        EdlSegmentKind.INTERMISSION,
    ]
    # tiles [0, total] with no gaps
    assert edl.segments[0].timeline_start_ms == 0
    assert edl.segments[-1].timeline_end_ms == 50_000
    for a, b in zip(edl.segments, edl.segments[1:]):
        assert a.timeline_end_ms == b.timeline_start_ms


def test_plan_edl_trims_clip_to_audio_duration():
    meta = _basic_metadata()
    edl = plan_edl(meta, _basic_clips(), article_clip=_clip("art", 30_000))
    repo_a_seg = next(s for s in edl.segments if s.repo_url == REPO_A)
    # block duration after gap-fill: 10500..30500 = 20000ms
    assert repo_a_seg.duration_ms == 20_000
    assert sum(r.duration_ms for r in repo_a_seg.source_ranges) == 20_000
    assert repo_a_seg.looped is False


def test_crossfade_declared_after_first_segment():
    meta = _basic_metadata()
    edl = plan_edl(meta, _basic_clips(), article_clip=_clip("art", 30_000), crossfade_ms=500)
    assert edl.segments[0].crossfade_in_ms == 0
    assert all(s.crossfade_in_ms == 500 for s in edl.segments[1:])


def test_title_card_on_section_change():
    meta = _basic_metadata()
    titles = {"s1": "AI Frameworks", "s2": "Breather"}
    edl = plan_edl(
        meta, _basic_clips(), article_clip=_clip("art", 30_000), section_titles=titles
    )
    # first repo-a starts section s1 → title card; repo-b (same s1) → none
    repo_segs = [s for s in edl.segments if s.section_id == "s1"]
    assert repo_segs[0].title_card is not None
    assert repo_segs[0].title_card.text == "AI Frameworks"
    assert repo_segs[1].title_card is None
    # intermission starts s2 → title card
    interm = edl.segments[-1]
    assert interm.title_card is not None and interm.title_card.text == "Breather"


# --- min-duration merging ---


def test_short_repo_topic_merged_into_previous():
    topics = (
        _topic(VisualMode.ARTICLE, 0, 20_000),
        _topic(VisualMode.REPO, 20_000, 22_000, REPO_A, "s1"),  # 2s < 8s
        _topic(VisualMode.REPO, 22_000, 40_000, REPO_B, "s1"),
    )
    meta = RealizedAudioMetadata(topics=topics, total_duration_ms=40_000)
    clips = {REPO_B: _clip("clip-b", 60_000, REPO_B, trim=[TrimRange(1_000, 59_000)])}
    edl = plan_edl(meta, clips, article_clip=_clip("art", 30_000))
    validate_edl(edl)
    # repo-a vanished (merged into the article block); two segments remain
    assert [s.repo_url for s in edl.segments] == [None, REPO_B]
    assert edl.segments[0].duration_ms == 22_000  # article extended over repo-a


def test_short_leading_block_absorbed_forward():
    topics = (
        _topic(VisualMode.REPO, 0, 3_000, REPO_A, "s1"),  # 3s leading, < 8s
        _topic(VisualMode.REPO, 3_000, 30_000, REPO_B, "s1"),
    )
    meta = RealizedAudioMetadata(topics=topics, total_duration_ms=30_000)
    clips = {REPO_B: _clip("clip-b", 60_000, REPO_B, trim=[TrimRange(1_000, 59_000)])}
    edl = plan_edl(meta, clips, article_clip=None)
    validate_edl(edl)
    assert len(edl.segments) == 1
    assert edl.segments[0].repo_url == REPO_B
    assert edl.segments[0].timeline_start_ms == 0


def test_intermission_exempt_from_min_duration():
    topics = (
        _topic(VisualMode.REPO, 0, 20_000, REPO_A, "s1"),
        _topic(VisualMode.INTERMISSION, 20_000, 23_000, None, "s1"),  # 3s ok
        _topic(VisualMode.REPO, 23_000, 40_000, REPO_B, "s1"),
    )
    meta = RealizedAudioMetadata(topics=topics, total_duration_ms=40_000)
    clips = {
        REPO_A: _clip("a", 60_000, REPO_A, trim=[TrimRange(1_000, 59_000)]),
        REPO_B: _clip("b", 60_000, REPO_B, trim=[TrimRange(1_000, 59_000)]),
    }
    edl = plan_edl(meta, clips)
    validate_edl(edl)
    kinds = [s.kind for s in edl.segments]
    assert EdlSegmentKind.INTERMISSION in kinds
    assert len(edl.segments) == 3


# --- graceful degradation ---


def test_missing_clip_degrades_to_intermission_fill():
    meta = _basic_metadata()
    # no clip for repo-b, and no article clip
    clips = {REPO_A: _clip("clip-a", 60_000, REPO_A, trim=[TrimRange(1_000, 59_000)])}
    edl = plan_edl(meta, clips, article_clip=None)
    validate_edl(edl)
    repo_b_seg = next(s for s in edl.segments if s.repo_url == REPO_B)
    assert repo_b_seg.kind is EdlSegmentKind.INTERMISSION
    assert repo_b_seg.is_fallback is True
    # article block (no article clip) also degrades
    article_seg = edl.segments[0]
    assert article_seg.kind is EdlSegmentKind.INTERMISSION
    assert article_seg.is_fallback is True


# --- determinism, empty, validation ---


def test_plan_edl_is_deterministic():
    meta = _basic_metadata()
    art = _clip("art", 30_000, trim=[TrimRange(500, 29_500)])
    a = plan_edl(meta, _basic_clips(), article_clip=art)
    b = plan_edl(meta, _basic_clips(), article_clip=art)
    assert a == b


def test_empty_metadata_yields_empty_edl():
    edl = plan_edl(RealizedAudioMetadata(), {})
    assert edl.segments == ()
    validate_edl(edl)


def test_negative_config_raises():
    with pytest.raises(EdlError):
        plan_edl(_basic_metadata(), _basic_clips(), min_visual_ms=-1)


def test_validate_edl_catches_gap():
    bad = EditDecisionList(
        segments=(
            EdlSegment(
                kind=EdlSegmentKind.INTERMISSION,
                timeline_start_ms=0,
                timeline_end_ms=1_000,
                visual_mode=VisualMode.INTERMISSION,
            ),
            EdlSegment(
                kind=EdlSegmentKind.INTERMISSION,
                timeline_start_ms=2_000,  # gap!
                timeline_end_ms=3_000,
                visual_mode=VisualMode.INTERMISSION,
            ),
        ),
        total_duration_ms=3_000,
    )
    with pytest.raises(EdlError):
        validate_edl(bad)


# --- serialization ---


def test_round_trip_serialization():
    meta = _basic_metadata()
    edl = plan_edl(
        meta,
        _basic_clips(),
        article_clip=_clip("art", 30_000, trim=[TrimRange(500, 29_500)]),
        section_titles={"s1": "AI", "s2": "Break"},
    )
    restored = EditDecisionList.from_dict(edl.to_dict())
    assert restored == edl
    assert restored.schema_version == EDL_SCHEMA_VERSION
