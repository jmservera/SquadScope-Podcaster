"""Tests for podcaster.sections — script sections + title-card metadata (#417)."""

from __future__ import annotations

import logging

import pytest

from podcaster.config import HostConfig, PodcastConfig
from podcaster.sections import (
    DEFAULT_TITLE_CARD_DURATION_SECONDS,
    MAX_SECTIONS,
    MIN_HOST_TURNS_PER_SECTION,
    ScriptSection,
    SectionValidationError,
    TitleCard,
    contains_section_header,
    match_section_header,
    parse_script_sections,
    sections_to_metadata,
    strip_section_headers,
    validate_sections,
)

CONFIG = PodcastConfig(
    host_a=HostConfig(name="Theo", voice="fable", style=""),
    host_b=HostConfig(name="Vera", voice="alloy", style=""),
)


def _turns(n: int) -> tuple[tuple[str, str], ...]:
    # ~20 words per turn so a 4-turn section comfortably clears the 30s estimate
    # (avoids unrelated short-section warnings in the blocking-rule tests).
    line = (
        "This is host turn number {i} and it carries plenty of words so the "
        "estimated spoken duration stays nicely above the short threshold here."
    )
    return tuple(
        ("Theo" if i % 2 == 0 else "Vera", line.format(i=i))
        for i in range(n)
    )


def _section(index: int, *, title: str, turns: int = MIN_HOST_TURNS_PER_SECTION) -> ScriptSection:
    host_turns = _turns(turns)
    return ScriptSection(
        id=f"section-{index}",
        title=title,
        summary=title,
        repo_slugs=(),
        title_card=TitleCard(text=title),
        host_turns=host_turns,
    )


SCRIPT = """\
Title: Claracle Podcast – Week 2026-W24
Voices: Theo = fable; Vera = alloy
---

Theo: Welcome to Claracle, your weekly developer trends show!
Vera: I am an AI-generated voice, just so you know.

## Section: AI Frameworks Showdown
Theo: First up, https://github.com/microsoft/vscode shipped a huge release.
Vera: And https://github.com/astral-sh/ruff just hit a big milestone.
Theo: The contrast between them is fascinating to me this week.
Vera: Absolutely, the developer experience is night and day.

## Section: Agents Move Into Production
Theo: Speaking of developer experience, agents are everywhere now.
Vera: The repo https://github.com/openai/openai-python is exploding.
Theo: Teams are shipping these to real users at last.
Vera: It is a genuine shift in how we build software.

Host outro: Manual review is required before publishing.
"""


class TestMatchSectionHeader:
    def test_matches_standard_header(self):
        assert match_section_header("## Section: AI Frameworks Showdown") == "AI Frameworks Showdown"

    def test_matches_extra_hashes_and_spacing(self):
        assert match_section_header("###   Section :  Deep Dive ") == "Deep Dive"

    def test_ignores_plain_dialogue(self):
        assert match_section_header("Theo: We talk about sections today.") is None

    def test_ignores_plain_heading(self):
        assert match_section_header("## Trends") is None


class TestParseScriptSections:
    def test_parses_two_sections(self):
        sections = parse_script_sections(SCRIPT, CONFIG)
        assert [s.title for s in sections] == [
            "AI Frameworks Showdown",
            "Agents Move Into Production",
        ]
        assert [s.id for s in sections] == ["section-1", "section-2"]

    def test_groups_host_turns_per_section(self):
        sections = parse_script_sections(SCRIPT, CONFIG)
        assert sections[0].host_turn_count == 4
        assert sections[1].host_turn_count == 4

    def test_pre_section_dialogue_excluded(self):
        # The welcome/disclosure before the first header is not in any section.
        sections = parse_script_sections(SCRIPT, CONFIG)
        joined = " ".join(t for _, t in sections[0].host_turns)
        assert "AI-generated voice" not in joined

    def test_extracts_repo_slugs(self):
        sections = parse_script_sections(SCRIPT, CONFIG)
        assert sections[0].repo_slugs == ("microsoft/vscode", "astral-sh/ruff")
        assert sections[1].repo_slugs == ("openai/openai-python",)

    def test_repo_slug_ignores_trailing_sentence_period(self):
        from podcaster.sections import _repo_slugs

        # A repo URL ending a sentence must not capture the period, and
        # internal dots (repo.js) must be preserved.
        turns = [
            ("host_a", "Check out https://github.com/org/repo."),
            ("host_b", "Also https://github.com/acme/widget.js rocks."),
        ]
        assert _repo_slugs(turns) == ("org/repo", "acme/widget.js")

    def test_title_card_defaults(self):
        sections = parse_script_sections(SCRIPT, CONFIG)
        card = sections[0].title_card
        assert card.text == "AI Frameworks Showdown"
        assert card.duration_seconds == DEFAULT_TITLE_CARD_DURATION_SECONDS

    def test_custom_title_card_duration(self):
        sections = parse_script_sections(SCRIPT, CONFIG, title_card_duration_seconds=1.0)
        assert sections[0].title_card.duration_seconds == 1.0

    def test_no_sections_for_legacy_script(self):
        legacy = "Title: X\n---\n\nTheo: Hi.\nVera: Hello.\n"
        assert parse_script_sections(legacy, CONFIG) == []

    def test_empty_script(self):
        assert parse_script_sections("", CONFIG) == []
        assert parse_script_sections("   ", CONFIG) == []

    def test_parses_without_config(self):
        sections = parse_script_sections(SCRIPT)
        assert [s.title for s in sections] == [
            "AI Frameworks Showdown",
            "Agents Move Into Production",
        ]


class TestMetadata:
    def test_to_dict_shape(self):
        section = parse_script_sections(SCRIPT, CONFIG)[0]
        data = section.to_dict()
        assert set(data) == {"id", "title", "summary", "repo_slugs", "title_card"}
        assert data["id"] == "section-1"
        assert data["title"] == "AI Frameworks Showdown"
        assert data["repo_slugs"] == ["microsoft/vscode", "astral-sh/ruff"]
        assert data["title_card"] == {
            "text": "AI Frameworks Showdown",
            "duration_seconds": DEFAULT_TITLE_CARD_DURATION_SECONDS,
        }

    def test_sections_to_metadata(self):
        sections = parse_script_sections(SCRIPT, CONFIG)
        metadata = sections_to_metadata(sections)
        assert len(metadata) == 2
        assert metadata[1]["id"] == "section-2"


class TestStripSectionHeaders:
    def test_removes_headers(self):
        stripped = strip_section_headers(SCRIPT)
        assert "## Section:" not in stripped
        assert not contains_section_header(stripped)

    def test_preserves_dialogue(self):
        stripped = strip_section_headers(SCRIPT)
        assert "Theo: Welcome to Claracle" in stripped
        assert "openai/openai-python" in stripped

    def test_idempotent_on_clean_text(self):
        clean = "Theo: Hi.\nVera: Hello."
        assert strip_section_headers(clean) == clean

    def test_contains_section_header(self):
        assert contains_section_header("## Section: X\nTheo: hi") is True
        assert contains_section_header("Theo: hi") is False


class TestValidationBlocking:
    def test_valid_sections_pass(self):
        sections = [
            _section(1, title="AI Frameworks Showdown"),
            _section(2, title="Agents In Production"),
        ]
        assert validate_sections(sections) == []

    def test_too_few_sections_raises(self):
        with pytest.raises(SectionValidationError, match="section count"):
            validate_sections([_section(1, title="Only One")])

    def test_too_many_sections_raises(self):
        sections = [_section(i, title=f"Title Number {i}") for i in range(1, MAX_SECTIONS + 2)]
        with pytest.raises(SectionValidationError, match="section count"):
            validate_sections(sections)

    def test_too_few_host_turns_raises(self):
        sections = [
            _section(1, title="Good Section Title"),
            _section(2, title="Thin Section", turns=MIN_HOST_TURNS_PER_SECTION - 1),
        ]
        with pytest.raises(SectionValidationError, match="host turn"):
            validate_sections(sections)

    def test_empty_section_raises(self):
        sections = [
            _section(1, title="Good Section Title"),
            _section(2, title="Empty One", turns=0),
        ]
        with pytest.raises(SectionValidationError, match="no host turns"):
            validate_sections(sections)

    def test_empty_title_raises(self):
        sections = [_section(1, title="Good One"), _section(2, title="   ")]
        with pytest.raises(SectionValidationError, match="empty title"):
            validate_sections(sections)

    def test_tts_with_header_raises(self):
        sections = [_section(1, title="A Good Title"), _section(2, title="Another Title")]
        tts = [("host_a", "Welcome."), ("host_a", "## Section: leaked")]
        with pytest.raises(SectionValidationError, match="TTS input contains a section header"):
            validate_sections(sections, tts_segments=tts)

    def test_tts_clean_passes(self):
        sections = [_section(1, title="A Good Title"), _section(2, title="Another Title")]
        tts = [("host_a", "Welcome to the show."), ("host_b", "Glad to be here.")]
        assert validate_sections(sections, tts_segments=tts) == []


class TestValidationWarnings:
    def test_generic_title_warns(self, caplog):
        sections = [
            _section(1, title="Introduction"),
            _section(2, title="A Specific Headline"),
        ]
        with caplog.at_level(logging.WARNING):
            warnings = validate_sections(sections)
        assert any("generic" in w for w in warnings)

    def test_numbered_generic_title_warns(self):
        sections = [_section(1, title="Repo 1"), _section(2, title="A Real Headline")]
        warnings = validate_sections(sections)
        assert any("generic" in w for w in warnings)

    def test_long_title_warns(self):
        long_title = "A" * 70
        sections = [_section(1, title=long_title), _section(2, title="Short One")]
        warnings = validate_sections(sections)
        assert any("chars" in w for w in warnings)

    def test_short_section_warns(self):
        # 4 turns of ~1 word each -> well under 30s estimate.
        host_turns = tuple(("Theo", "Word") for _ in range(MIN_HOST_TURNS_PER_SECTION))
        short = ScriptSection(
            id="section-1",
            title="Brisk Section",
            summary="",
            repo_slugs=(),
            title_card=TitleCard(text="Brisk Section"),
            host_turns=host_turns,
        )
        normal = _section(2, title="Normal Section")
        warnings = validate_sections([short, normal])
        assert any("estimated" in w for w in warnings)

    def test_warnings_do_not_raise(self):
        sections = [_section(1, title="Introduction"), _section(2, title="Conclusion")]
        # Generic titles are warnings only; must not raise.
        validate_sections(sections)
