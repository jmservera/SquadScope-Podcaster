"""Tests for podcaster.video.section_cards (issue #377).

Card rendering is exercised via the injected command runner, so these tests do
not require ffmpeg or a browser.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from podcaster.video.section_cards import (
    DEFAULT_ACCENT,
    KNOWN_SECTIONS,
    SECTION_CARD_DURATION_MS,
    SECTION_CARD_FADE_MS,
    SectionCardConfig,
    SectionCardInsert,
    SectionMarker,
    _build_section_card_cmd,
    _classify_header,
    _marker_from_name,
    _normalize_repo_url,
    build_section_card_inserts,
    generate_section_card,
    parse_sections,
    plan_section_card_inserts,
)


def _mock_runner() -> MagicMock:
    runner = MagicMock()
    runner.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    return runner


SCRIPT_WITH_SECTIONS = """\
Title: Week 24 — Highlights
Source: https://github.com/jmservera/SquadScope
Generated: 2026-06-22T00:00:00Z
---

Ada: Welcome to the show! Today we have a packed Trends rundown ahead.
Beto: I am an AI-generated voice.

## 🔥 Trends
Ada: First up, see https://github.com/microsoft/vscode — a huge release.
Beto: Wonderful stuff.

[SECTION: Signal & Noise]
Ada: Cutting through the noise: https://github.com/astral-sh/ruff just hit 1.0.

**Blind Spots**
Beto: The blind spot is https://github.com/jmservera/SquadScope-Podcaster.

Ada: That wraps the Trends and Signal & Noise discussion. Thanks all!
"""

SEGMENT_URLS = [
    "https://github.com/jmservera/SquadScope",
    "https://github.com/microsoft/vscode",
    "https://github.com/astral-sh/ruff",
    "https://github.com/jmservera/SquadScope-Podcaster",
]


# --- parse_sections ---


class TestParseSections:
    def test_detects_all_conventions(self):
        markers = parse_sections(SCRIPT_WITH_SECTIONS)
        names = [m.name for m in markers]
        assert names == ["Trends", "Signal & Noise", "Blind Spots"]

    def test_enriches_known_sections_with_emoji_and_accent(self):
        markers = {m.name: m for m in parse_sections(SCRIPT_WITH_SECTIONS)}
        assert markers["Trends"].emoji == "🔥"
        assert markers["Trends"].accent == KNOWN_SECTIONS["trends"].accent
        assert markers["Signal & Noise"].emoji == "📡"

    def test_positions_are_ordered_and_in_body(self):
        markers = parse_sections(SCRIPT_WITH_SECTIONS)
        positions = [m.position for m in markers]
        assert positions == sorted(positions)
        # Each position points at the start of its header line in the script.
        for m in markers:
            line = SCRIPT_WITH_SECTIONS[m.position:].splitlines()[0]
            assert m.name.split()[0].lower() in line.lower() or m.emoji in line

    def test_dialogue_mentions_not_treated_as_headers(self):
        # "Trends" and "Signal & Noise" appear inside dialogue lines too; those
        # must not produce extra markers.
        markers = parse_sections(SCRIPT_WITH_SECTIONS)
        assert len(markers) == 3

    def test_empty_script_returns_empty(self):
        assert parse_sections("") == []
        assert parse_sections("   \n  ") == []

    def test_dialogue_only_script_returns_empty(self):
        script = "Title: X\n---\n\nAda: Hello there.\nBeto: Hi!\n"
        assert parse_sections(script) == []

    def test_metadata_header_not_matched(self):
        # A metadata key that happens to look like a section stays in the header
        # block (before ---) and must be ignored.
        script = "Title: Trends\nSource: https://github.com/o/r\n---\n\nAda: hi\n"
        assert parse_sections(script) == []

    def test_duplicate_section_collapsed(self):
        script = (
            "Title: X\n---\n\n## Trends\n🔥 Trends\n"
            "Ada: see https://github.com/o/r\n"
        )
        markers = parse_sections(script)
        assert [m.name for m in markers] == ["Trends"]

    def test_unknown_markdown_heading_accepted(self):
        script = "Title: X\n---\n\n## Wild Card Roundup\nAda: hi\n"
        markers = parse_sections(script)
        assert [m.name for m in markers] == ["Wild Card Roundup"]
        assert markers[0].accent == DEFAULT_ACCENT

    def test_long_prose_heading_rejected(self):
        script = (
            "Title: X\n---\n\n"
            "## This is a very long sentence that is clearly not a section title\n"
            "Ada: hi\n"
        )
        assert parse_sections(script) == []

    def test_no_separator_scans_whole_script(self):
        script = "## Trends\nAda: see https://github.com/o/r\n"
        markers = parse_sections(script)
        assert [m.name for m in markers] == ["Trends"]


# --- _classify_header ---


class TestClassifyHeader:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("## Trends", "Trends"),
            ("### 🔥 Trends", "Trends"),
            ("[SECTION: Signal & Noise]", "Signal & Noise"),
            ("[Blind Spots]", "Blind Spots"),
            ("**Industry**", "Industry"),
            ("📡 Signal and Noise", "Signal & Noise"),
            ("- Trends", "Trends"),
        ],
    )
    def test_recognised(self, line, expected):
        result = _classify_header(line)
        assert result is not None
        assert result[0] == expected

    @pytest.mark.parametrize(
        "line",
        [
            "Ada: We love Trends this week.",
            "Beto: Signal & Noise is great.",
            "",
            "   ",
            "Just a normal sentence about industry trends and blind spots here.",
        ],
    )
    def test_rejected(self, line):
        assert _classify_header(line) is None


# --- _normalize_repo_url ---


class TestNormalizeRepoUrl:
    def test_strips_git_suffix_and_lowercases(self):
        assert _normalize_repo_url("https://github.com/Owner/Repo.git") == "owner/repo"

    def test_strips_trailing_dot(self):
        assert _normalize_repo_url("https://github.com/Owner/Repo.") == "owner/repo"

    def test_equivalent_urls_match(self):
        a = _normalize_repo_url("https://github.com/astral-sh/ruff")
        b = _normalize_repo_url("http://github.com/astral-sh/ruff")
        assert a == b


# --- plan_section_card_inserts ---


class TestPlanSectionCardInserts:
    def test_maps_sections_to_opening_segments(self):
        sections = parse_sections(SCRIPT_WITH_SECTIONS)
        plan = plan_section_card_inserts(SCRIPT_WITH_SECTIONS, sections, SEGMENT_URLS)
        assert plan == [("Trends", 1), ("Signal & Noise", 2), ("Blind Spots", 3)]

    def test_empty_sections_returns_empty(self):
        assert plan_section_card_inserts(SCRIPT_WITH_SECTIONS, [], SEGMENT_URLS) == []

    def test_section_without_following_repo_skipped(self):
        script = "Title: X\n---\n\n## Trends\nAda: no links here at all.\n"
        sections = parse_sections(script)
        assert plan_section_card_inserts(script, sections, [None]) == []

    def test_generic_segments_are_none(self):
        # First segment is a generic (None) weekly card; the section maps to the
        # vscode repo at index 1.
        sections = parse_sections(SCRIPT_WITH_SECTIONS)
        urls = [None] + SEGMENT_URLS[1:]
        plan = plan_section_card_inserts(SCRIPT_WITH_SECTIONS, sections, urls)
        # Trends maps to vscode (index 1); Signal & Noise to ruff (index 2).
        assert ("Trends", 1) in plan

    def test_duplicate_target_index_deduped(self):
        # Two sections both followed first by the same repo → only one card.
        script = (
            "Title: X\n---\n\n"
            "## Trends\nAda: https://github.com/o/r is great.\n"
            "## Industry\nBeto: also https://github.com/o/r.\n"
        )
        sections = parse_sections(script)
        plan = plan_section_card_inserts(script, sections, ["https://github.com/o/r"])
        assert plan == [("Trends", 0)]

    def test_results_sorted_by_index(self):
        sections = parse_sections(SCRIPT_WITH_SECTIONS)
        plan = plan_section_card_inserts(SCRIPT_WITH_SECTIONS, sections, SEGMENT_URLS)
        assert [idx for _, idx in plan] == sorted(idx for _, idx in plan)


# --- _build_section_card_cmd ---


class TestBuildSectionCardCmd:
    def test_contains_drawtext_fade_and_duration(self):
        marker = _marker_from_name("Trends")
        cmd = _build_section_card_cmd(
            marker, Path("/out/card.mp4"), SectionCardConfig(), "ffmpeg"
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert "drawtext" in vf
        assert "Trends" in vf
        assert "fade=t=in:st=0:d=0.500" in vf
        assert "fade=t=out" in vf
        # 0.75 s total card duration (issue #417 default).
        assert cmd[cmd.index("-t") + 1] == "0.750"

    def test_uses_section_accent_for_rule(self):
        marker = _marker_from_name("Signal & Noise")
        cmd = _build_section_card_cmd(
            marker, Path("/out/card.mp4"), SectionCardConfig(), "ffmpeg"
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert KNOWN_SECTIONS["signal & noise"].accent in vf
        # The frame height is referenced as ih (not the box-height h) in drawbox.
        assert "y=(ih/2)" in vf

    def test_escapes_special_characters(self):
        marker = SectionMarker(name="A: B, C", position=0)
        cmd = _build_section_card_cmd(
            marker, Path("/out/card.mp4"), SectionCardConfig(), "ffmpeg"
        )
        vf = cmd[cmd.index("-vf") + 1]
        assert r"\:" in vf and r"\," in vf


# --- generate_section_card ---


class TestGenerateSectionCard:
    def test_runs_command_and_returns_clip_result(self, tmp_path):
        runner = _mock_runner()
        out = tmp_path / "card.mp4"
        result = generate_section_card(
            "Trends", out, ffmpeg_bin="ffmpeg", runner=runner
        )
        runner.assert_called_once()
        assert result.path == out
        assert result.duration_ms == SECTION_CARD_DURATION_MS
        assert result.width == 1920 and result.height == 1080

    def test_accepts_marker_object(self, tmp_path):
        runner = _mock_runner()
        marker = SectionMarker(name="Industry", position=0)
        generate_section_card(
            marker, tmp_path / "c.mp4", ffmpeg_bin="ffmpeg", runner=runner
        )
        runner.assert_called_once()

    def test_string_name_enriched_from_registry(self):
        marker = _marker_from_name("blind spots")
        assert marker.name == "Blind Spots"
        assert marker.emoji == "🫣"


# --- build_section_card_inserts ---


class TestBuildSectionCardInserts:
    def test_end_to_end_produces_inserts(self, tmp_path):
        runner = _mock_runner()
        inserts = build_section_card_inserts(
            SCRIPT_WITH_SECTIONS,
            SEGMENT_URLS,
            tmp_path,
            ffmpeg_bin="ffmpeg",
            runner=runner,
        )
        assert [i.name for i in inserts] == ["Trends", "Signal & Noise", "Blind Spots"]
        assert [i.before_index for i in inserts] == [1, 2, 3]
        assert all(isinstance(i, SectionCardInsert) for i in inserts)
        assert all(i.duration_seconds == SECTION_CARD_DURATION_MS / 1000.0 for i in inserts)
        # One render per card.
        assert runner.call_count == 3

    def test_no_sections_returns_empty(self, tmp_path):
        runner = _mock_runner()
        inserts = build_section_card_inserts(
            "Title: X\n---\n\nAda: hi\nBeto: bye\n",
            [None],
            tmp_path,
            ffmpeg_bin="ffmpeg",
            runner=runner,
        )
        assert inserts == []
        runner.assert_not_called()

    def test_missing_drawtext_ffmpeg_skips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "podcaster.video.section_cards._get_drawtext_ffmpeg", lambda: None
        )
        runner = _mock_runner()
        inserts = build_section_card_inserts(
            SCRIPT_WITH_SECTIONS, SEGMENT_URLS, tmp_path, runner=runner
        )
        assert inserts == []
        runner.assert_not_called()


# --- defaults sanity ---


def test_default_durations():
    assert SECTION_CARD_DURATION_MS == 750
    assert SECTION_CARD_FADE_MS == 500
