"""Tests for podcaster.video.clip_manifest — clip manifests (#487)."""

from __future__ import annotations

import pytest

from podcaster.video.clip_manifest import (
    CLIP_MANIFEST_SCHEMA_VERSION,
    DISCUSSION_MARGIN_FACTOR,
    REQUIRED_CLIP_MIN_SECONDS,
    ClipChapter,
    ClipManifest,
    ClipManifestError,
    LoopSection,
    TrimRange,
    build_clip_manifest,
    required_clip_seconds,
)

# --- clip-length policy ---


def test_required_clip_seconds_floor():
    # short discussion → floor of 60s
    assert required_clip_seconds(10) == REQUIRED_CLIP_MIN_SECONDS
    assert required_clip_seconds(0) == REQUIRED_CLIP_MIN_SECONDS


def test_required_clip_seconds_margin():
    # long discussion → 1.5x margin
    assert required_clip_seconds(80) == 80 * DISCUSSION_MARGIN_FACTOR
    assert required_clip_seconds(100) == 150


def test_required_clip_seconds_negative_clamped():
    assert required_clip_seconds(-5) == REQUIRED_CLIP_MIN_SECONDS


def test_required_clip_seconds_boundary():
    # exactly at the crossover (40 * 1.5 == 60)
    assert required_clip_seconds(40) == 60.0


# --- manifest construction: trim ranges + loop sections from chapters ---


def test_build_manifest_derives_trim_and_loop_from_chapters():
    chapters = [
        ClipChapter("readme", 0, 30_000),
        ClipChapter("file-tree", 30_000, 50_000),
    ]
    m = build_clip_manifest(
        "clip-000", 60_000, repo_url="https://github.com/o/r", chapters=chapters
    )
    # interior = chapter minus 500ms each side
    assert m.trim_ranges == (
        TrimRange(500, 29_500),
        TrimRange(30_500, 49_500),
    )
    assert m.loop_sections == (
        LoopSection(500, 29_500),
        LoopSection(30_500, 49_500),
    )
    assert m.repo_url == "https://github.com/o/r"
    assert m.is_fallback is False


def test_short_chapter_yields_no_safe_range():
    # 1200ms chapter minus 2*500 margin = 200ms < min_safe_range (1000)
    chapters = [ClipChapter("blip", 0, 1_200)]
    m = build_clip_manifest("clip-001", 60_000, chapters=chapters)
    assert m.trim_ranges == ()
    assert m.loop_sections == ()


def test_trimmable_and_min_trimmed_duration():
    chapters = [ClipChapter("a", 0, 20_000), ClipChapter("b", 20_000, 40_000)]
    m = build_clip_manifest("clip-002", 40_000, chapters=chapters)
    # each interior is 19_000ms → 38_000ms trimmable
    assert m.trimmable_ms == 38_000
    assert m.min_trimmed_duration_ms == 2_000


def test_fallback_clip_is_fully_trimmable_and_loopable():
    m = build_clip_manifest("clip-fb", 5_000, is_fallback=True)
    assert m.is_fallback is True
    assert m.trim_ranges == (TrimRange(0, 5_000),)
    assert m.loop_sections == (LoopSection(0, 5_000),)


def test_covers_discussion_time():
    m = build_clip_manifest("clip-003", 90_000)
    assert m.covers(80) is True  # 80s ≤ 90s
    assert m.covers(95) is False  # 95s > 90s


# --- validation ---


def test_non_positive_duration_raises():
    with pytest.raises(ClipManifestError):
        build_clip_manifest("clip-x", 0)
    with pytest.raises(ClipManifestError):
        build_clip_manifest("clip-x", -1)


def test_chapter_outside_clip_raises():
    with pytest.raises(ClipManifestError):
        build_clip_manifest("clip-y", 10_000, chapters=[ClipChapter("oops", 0, 20_000)])


def test_overlapping_chapters_raise():
    with pytest.raises(ClipManifestError):
        build_clip_manifest(
            "clip-z",
            60_000,
            chapters=[
                ClipChapter("a", 0, 30_000),
                ClipChapter("b", 20_000, 40_000),
            ],
        )


def test_non_positive_chapter_raises():
    with pytest.raises(ClipManifestError):
        build_clip_manifest("clip-w", 60_000, chapters=[ClipChapter("a", 10_000, 10_000)])


# --- serialization round-trip ---


def test_round_trip_serialization():
    chapters = [ClipChapter("readme", 0, 30_000), ClipChapter("issues", 30_000, 55_000)]
    m = build_clip_manifest("clip-rt", 60_000, repo_url="https://github.com/o/r", chapters=chapters)
    restored = ClipManifest.from_dict(m.to_dict())
    assert restored == m
    assert restored.schema_version == CLIP_MANIFEST_SCHEMA_VERSION


def test_to_dict_shape():
    m = build_clip_manifest("clip-s", 60_000, is_fallback=True)
    data = m.to_dict()
    assert data["schema_version"] == CLIP_MANIFEST_SCHEMA_VERSION
    assert {
        "clip_id",
        "repo_url",
        "duration_ms",
        "is_fallback",
        "chapters",
        "trim_ranges",
        "loop_sections",
    } <= data.keys()


def test_component_dataclass_round_trips():
    ch = ClipChapter("readme", 0, 1_000)
    assert ClipChapter.from_dict(ch.to_dict()) == ch
    tr = TrimRange(100, 900)
    assert TrimRange.from_dict(tr.to_dict()) == tr
    ls = LoopSection(100, 900)
    assert LoopSection.from_dict(ls.to_dict()) == ls


def test_fallback_with_chapters_or_repo_url_raises():
    with pytest.raises(ClipManifestError, match="static card"):
        build_clip_manifest(
            "clip-fb", 5_000, is_fallback=True, chapters=[ClipChapter("c", 0, 5_000)]
        )
    with pytest.raises(ClipManifestError, match="static card"):
        build_clip_manifest("clip-fb", 5_000, is_fallback=True, repo_url="https://github.com/o/r")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("False", False),
        ("yes", True),
        ("no", False),
        (1, True),
        (0, False),
        ("", False),
    ],
)
def test_from_dict_parses_is_fallback_robustly(raw, expected):
    m = build_clip_manifest("clip-b", 5_000, is_fallback=True)
    data = m.to_dict()
    data["is_fallback"] = raw
    assert ClipManifest.from_dict(data).is_fallback is expected


def test_from_dict_rejects_ambiguous_is_fallback():
    m = build_clip_manifest("clip-b", 5_000, is_fallback=True)
    data = m.to_dict()
    data["is_fallback"] = "maybe"
    with pytest.raises(ClipManifestError, match="is_fallback"):
        ClipManifest.from_dict(data)
