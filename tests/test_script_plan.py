"""Tests for podcaster.script_plan — Layer 1 script plan metadata (#485)."""

from __future__ import annotations

import pytest

from podcaster.config import HostConfig, PodcastConfig
from podcaster.episode import parse_script_segments
from podcaster.script_plan import (
    DEFAULT_VISUAL_MODE,
    SCRIPT_PLAN_SCHEMA_VERSION,
    ScriptPlan,
    ScriptPlanSegment,
    ScriptPlanValidationError,
    VisualMode,
    build_visual_marker_guidance,
    contains_visual_marker,
    match_visual_marker,
    parse_script_plan,
    strip_visual_markers,
    validate_script_plan,
)

CONFIG = PodcastConfig(
    host_a=HostConfig(name="Theo", voice="fable", style=""),
    host_b=HostConfig(name="Vera", voice="alloy", style=""),
)

SCRIPT = """Title: x
Voices: Theo = fable; Vera = alloy
---
Theo: Welcome to the show everyone, glad to have you.
## Section: AI Frameworks Showdown
## Visual: repo https://github.com/owner/repo-a
Vera: This first repo is wild and full of words here.
Theo: Totally agree with you on that point my friend.
## Visual: intermission
Vera: Let us take a quick breather now together.
## Section: Tooling Roundup
## Visual: article
Theo: Back to the weekly rundown we published online.
"""


# --- marker matching ---


@pytest.mark.parametrize(
    "line,mode,url",
    [
        ("## Visual: repo https://github.com/owner/repo", VisualMode.REPO, "https://github.com/owner/repo"),
        ("### visual - repo https://github.com/o/r.git", VisualMode.REPO, "https://github.com/o/r"),
        ("## Visual: intermission", VisualMode.INTERMISSION, None),
        ("## VISUAL: article", VisualMode.ARTICLE, None),
        ("  ## visual : article  ", VisualMode.ARTICLE, None),
    ],
)
def test_match_visual_marker(line, mode, url):
    assert match_visual_marker(line) == (mode, url)


@pytest.mark.parametrize(
    "line",
    [
        "Theo: regular dialogue",
        "## Section: A Title",
        "## Visualize the data",
        "",
        "Visual: repo https://github.com/o/r",  # missing ## prefix
    ],
)
def test_match_visual_marker_non_markers(line):
    assert match_visual_marker(line) is None


def test_repo_marker_without_url_returns_none_url():
    assert match_visual_marker("## Visual: repo") == (VisualMode.REPO, None)


# --- parsing ---


def test_parse_script_plan_segments():
    plan = parse_script_plan(SCRIPT, CONFIG)
    modes = [(s.speaker, s.visual_mode, s.repo_url, s.section_id) for s in plan.segments]
    assert modes == [
        ("Theo", VisualMode.ARTICLE, None, None),  # cold open default
        ("Vera", VisualMode.REPO, "https://github.com/owner/repo-a", "section-1"),
        ("Theo", VisualMode.REPO, "https://github.com/owner/repo-a", "section-1"),
        ("Vera", VisualMode.INTERMISSION, None, "section-1"),
        ("Theo", VisualMode.ARTICLE, None, "section-2"),
    ]
    assert [s.index for s in plan.segments] == [0, 1, 2, 3, 4]


def test_parse_script_plan_repo_urls_and_intermissions():
    plan = parse_script_plan(SCRIPT, CONFIG)
    assert plan.repo_urls == ("https://github.com/owner/repo-a",)
    assert plan.has_intermissions is True


def test_parse_script_plan_includes_sections():
    plan = parse_script_plan(SCRIPT, CONFIG)
    assert [s.id for s in plan.sections] == ["section-1", "section-2"]
    assert plan.schema_version == SCRIPT_PLAN_SCHEMA_VERSION


def test_parse_empty_script_is_dormant():
    plan = parse_script_plan("", CONFIG)
    assert plan.segments == ()
    assert plan.sections == ()


def test_default_visual_mode_is_article():
    assert DEFAULT_VISUAL_MODE is VisualMode.ARTICLE
    script = "Voices: Theo = a; Vera = b\n---\nTheo: No marker here at all friend.\n"
    plan = parse_script_plan(script, CONFIG)
    assert plan.segments[0].visual_mode is VisualMode.ARTICLE


def test_repo_url_cleared_when_mode_switches_away():
    script = (
        "Voices: Theo = a; Vera = b\n---\n"
        "## Visual: repo https://github.com/o/r\n"
        "Theo: about the repo and lots of words here.\n"
        "## Visual: article\n"
        "Vera: now we are on the article instead now.\n"
    )
    plan = parse_script_plan(script, CONFIG)
    assert plan.segments[0].repo_url == "https://github.com/o/r"
    assert plan.segments[1].repo_url is None


# --- stripping / non-spoken guarantee ---


def test_strip_visual_markers_removes_only_markers():
    stripped = strip_visual_markers(SCRIPT)
    assert "## Visual" not in stripped
    assert "## Section: AI Frameworks Showdown" in stripped
    assert "Theo: Welcome to the show" in stripped


def test_contains_visual_marker():
    assert contains_visual_marker(SCRIPT) is True
    assert contains_visual_marker(strip_visual_markers(SCRIPT)) is False


def test_markers_never_reach_tts():
    segments = parse_script_segments(SCRIPT, CONFIG)
    joined = " ".join(text for _, text in segments)
    assert "## Visual" not in joined
    assert "github.com" not in joined
    assert len(segments) == 5


# --- validation ---


def test_validate_clean_plan_returns_no_warnings():
    plan = parse_script_plan(SCRIPT, CONFIG)
    assert validate_script_plan(plan) == []


def test_validate_repo_segment_missing_url_raises():
    plan = ScriptPlan(
        segments=(ScriptPlanSegment(0, "Theo", "hi", VisualMode.REPO, repo_url=None),)
    )
    with pytest.raises(ScriptPlanValidationError, match="repo_url"):
        validate_script_plan(plan)


def test_validate_non_repo_segment_with_url_raises():
    plan = ScriptPlan(
        segments=(
            ScriptPlanSegment(
                0, "Theo", "hi", VisualMode.ARTICLE, repo_url="https://github.com/o/r"
            ),
        )
    )
    with pytest.raises(ScriptPlanValidationError, match="declares a repo_url"):
        validate_script_plan(plan)


def test_validate_malformed_repo_url_raises():
    plan = ScriptPlan(
        segments=(ScriptPlanSegment(0, "Theo", "hi", VisualMode.REPO, repo_url="not-a-url"),)
    )
    with pytest.raises(ScriptPlanValidationError, match="not a GitHub repo URL"):
        validate_script_plan(plan)


def test_validate_warns_when_no_repo_visuals():
    plan = ScriptPlan(
        segments=(ScriptPlanSegment(0, "Theo", "hi", VisualMode.ARTICLE),)
    )
    warnings = validate_script_plan(plan)
    assert any("no 'repo' visuals" in w for w in warnings)


def test_validate_warns_on_empty_plan():
    warnings = validate_script_plan(ScriptPlan())
    assert any("no spoken segments" in w for w in warnings)


# --- serialization ---


def test_to_dict_is_versioned():
    plan = parse_script_plan(SCRIPT, CONFIG)
    data = plan.to_dict()
    assert data["schema_version"] == SCRIPT_PLAN_SCHEMA_VERSION
    assert len(data["segments"]) == 5
    assert len(data["sections"]) == 2
    first = data["segments"][1]
    assert first["visual_mode"] == "repo"
    assert first["repo_url"] == "https://github.com/owner/repo-a"


def test_roundtrip_to_from_dict():
    plan = parse_script_plan(SCRIPT, CONFIG)
    data = plan.to_dict()
    assert ScriptPlan.from_dict(data).to_dict() == data


def test_visual_mode_from_value():
    assert VisualMode.from_value("repo") is VisualMode.REPO
    assert VisualMode.from_value(VisualMode.ARTICLE) is VisualMode.ARTICLE
    with pytest.raises(ScriptPlanValidationError):
        VisualMode.from_value("nope")


# --- prompt guidance ---


def test_build_visual_marker_guidance_mentions_modes():
    guidance = build_visual_marker_guidance()
    assert "## Visual: repo" in guidance
    assert "## Visual: article" in guidance
    assert "## Visual: intermission" in guidance
    assert "NON-SPOKEN" in guidance


def test_generation_system_prompt_includes_guidance():
    from podcaster.script_gen import _build_system_prompt

    prompt = _build_system_prompt(podcast_config=CONFIG)
    assert "VISUAL INTENT" in prompt
    assert "## Visual: repo" in prompt
