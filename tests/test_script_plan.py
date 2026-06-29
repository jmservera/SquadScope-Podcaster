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
    infer_repo_visual_markers,
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
        (
            "## Visual: repo https://github.com/owner/repo",
            VisualMode.REPO,
            "https://github.com/owner/repo",
        ),
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


@pytest.mark.parametrize(
    "repo_url",
    [
        "https://github.com/owner/repo/blob/main/README.md",
        "https://github.com/owner/repo/tree/main",
        "https://github.com/owner/repo/issues/1",
        "https://github.com/owner",
        "https://gitlab.com/owner/repo",
    ],
)
def test_validate_non_root_github_url_raises(repo_url):
    plan = ScriptPlan(
        segments=(ScriptPlanSegment(0, "Theo", "hi", VisualMode.REPO, repo_url=repo_url),)
    )
    with pytest.raises(ScriptPlanValidationError, match="not a GitHub repo URL"):
        validate_script_plan(plan)


def test_validate_warns_when_no_repo_visuals():
    plan = ScriptPlan(segments=(ScriptPlanSegment(0, "Theo", "hi", VisualMode.ARTICLE),))
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


def test_infer_repo_visual_markers_injects_from_inline_links():
    """Inline repo links become explicit markers so repo cards render (#555)."""
    script = (
        "Theo: Welcome to the show.\n"
        "Vera: Check out [vercel/eve](https://github.com/vercel/eve), it's great.\n"
        "Theo: And [openai/gym](https://github.com/openai/gym) too.\n"
    )
    out = infer_repo_visual_markers(script, CONFIG)
    assert "## Visual: repo https://github.com/vercel/eve" in out
    assert "## Visual: repo https://github.com/openai/gym" in out

    plan = parse_script_plan(out, CONFIG)
    repos = [s.repo_url for s in plan.segments if s.visual_mode is VisualMode.REPO]
    assert "https://github.com/vercel/eve" in repos
    assert "https://github.com/openai/gym" in repos


def test_infer_repo_visual_markers_first_marker_precedes_first_repo_turn():
    script = (
        "Theo: Intro with no repo.\n"
        "Vera: Now [vercel/eve](https://github.com/vercel/eve) is the anchor.\n"
    )
    out = infer_repo_visual_markers(script, CONFIG).splitlines()
    marker_idx = out.index("## Visual: repo https://github.com/vercel/eve")
    turn_idx = next(i for i, line in enumerate(out) if line.startswith("Vera:"))
    assert marker_idx == turn_idx - 1


def test_infer_repo_visual_markers_is_idempotent_and_preserves_explicit():
    script = (
        "## Visual: repo https://github.com/vercel/eve\n"
        "Theo: Talking about [vercel/eve](https://github.com/vercel/eve).\n"
        "Vera: Still the same repo here.\n"
    )
    once = infer_repo_visual_markers(script, CONFIG)
    twice = infer_repo_visual_markers(once, CONFIG)
    # No duplicate marker injected when one is already in effect.
    assert once.count("## Visual: repo") == 1
    assert once == twice


def test_infer_repo_visual_markers_no_repo_is_noop():
    script = "Theo: Just a friendly chat.\nVera: No repositories at all today.\n"
    assert infer_repo_visual_markers(script, CONFIG) == script


def test_infer_repo_visual_markers_remarks_when_topic_returns():
    """Returning to an earlier repo after another repo re-emits its marker."""
    script = (
        "Theo: See [a/one](https://github.com/a/one).\n"
        "Vera: Then [b/two](https://github.com/b/two).\n"
        "Theo: Back to [a/one](https://github.com/a/one) again.\n"
    )
    out = infer_repo_visual_markers(script, CONFIG)
    assert out.count("## Visual: repo https://github.com/a/one") == 2
    assert out.count("## Visual: repo https://github.com/b/two") == 1


def test_infer_repo_visual_markers_from_bare_slug_anchors_to_first_naming():
    """#558: hosts name repos as bare ``owner/repo`` slugs; the header carries the
    full URLs. The marker must land at the first SPOKEN naming, not the header."""
    script = (
        "Title: Weekly\n"
        "Repos featured: https://github.com/vercel/eve "
        "https://github.com/openai/gym\n"
        "---\n"
        "Theo: Welcome, a quick intro with no repo named yet.\n"
        "Vera: Agent frameworks matter. vercel/eve is the cleanest anchor here.\n"
        "Theo: Later we also cover openai/gym for benchmarking work.\n"
    )
    out = infer_repo_visual_markers(script, CONFIG).splitlines()
    eve_marker = out.index("## Visual: repo https://github.com/vercel/eve")
    eve_turn = next(i for i, ln in enumerate(out) if ln.startswith("Vera: Agent frameworks"))
    # Marker sits immediately before the turn that first names eve (audio cue).
    assert eve_marker == eve_turn - 1
    # No repo marker is injected before the first naming (lead-in stays article).
    assert not any(ln.startswith("## Visual: repo") for ln in out[:eve_marker])
    gym_marker = out.index("## Visual: repo https://github.com/openai/gym")
    assert gym_marker > eve_marker

    plan = parse_script_plan("\n".join(out), CONFIG)
    repo_segs = [s for s in plan.segments if s.visual_mode is VisualMode.REPO]
    assert repo_segs[0].repo_url == "https://github.com/vercel/eve"
    assert plan.segments[0].visual_mode is VisualMode.ARTICLE


def test_infer_repo_visual_markers_slug_boundaries_avoid_false_positives():
    """A known slug must not match inside a longer token (owner/repo vs owner/repo-old)."""
    script = (
        "Repos featured: https://github.com/acme/tool\n"
        "---\n"
        "Theo: We tried acme/tool-old which is unrelated and different.\n"
        "Vera: But acme/tool itself is the real one we mean.\n"
    )
    out = infer_repo_visual_markers(script, CONFIG).splitlines()
    marker = out.index("## Visual: repo https://github.com/acme/tool")
    real_turn = next(i for i, ln in enumerate(out) if ln.startswith("Vera: But acme/tool"))
    # The marker attaches to the real bare-slug mention, not the -old substring.
    assert marker == real_turn - 1


def test_infer_repo_visual_markers_bare_slug_with_trailing_punctuation():
    """A bare slug ending a sentence (``owner/repo.``) must still be detected (#558)."""
    script = (
        "Repos featured: https://github.com/acme/tool\n"
        "---\n"
        "Theo: This week's standout is acme/tool. It changes everything.\n"
    )
    out = infer_repo_visual_markers(script, CONFIG).splitlines()
    marker = out.index("## Visual: repo https://github.com/acme/tool")
    turn = next(i for i, ln in enumerate(out) if ln.startswith("Theo: This week's"))
    # End-of-sentence period is a boundary, so the slug is still recognised.
    assert marker == turn - 1


def test_infer_repo_visual_markers_first_named_wins_in_multi_repo_turn():
    """When one turn names several repos, the earliest-named repo owns the turn."""
    script = (
        "Repos featured: https://github.com/a/first https://github.com/b/second\n"
        "---\n"
        "Theo: Today b/second and a/first both appear, a/first comes second here.\n"
    )
    out = infer_repo_visual_markers(script, CONFIG).splitlines()
    markers = [ln for ln in out if ln.startswith("## Visual: repo")]
    # b/second is named first in the sentence, so it gets the marker.
    assert markers[0] == "## Visual: repo https://github.com/b/second"


def test_parse_script_plan_backfills_each_later_named_repo():
    """#579: a first explicit repo marker must not mask later bare-slug cues."""
    script = (
        "Title: Weekly\n"
        "Repos featured: https://github.com/vercel/eve "
        "https://github.com/openai/gym https://github.com/astral-sh/ruff\n"
        "---\n"
        "## Visual: repo https://github.com/vercel/eve\n"
        "Theo: vercel/eve sets the stage for this sequence.\n"
        "Vera: openai/gym is next in the script and needs its own clip.\n"
        "Theo: astral-sh/ruff closes the loop with a third cue.\n"
    )

    plan = parse_script_plan(script, CONFIG)

    assert plan.repo_urls == (
        "https://github.com/vercel/eve",
        "https://github.com/openai/gym",
        "https://github.com/astral-sh/ruff",
    )
    assert [seg.repo_url for seg in plan.segments] == list(plan.repo_urls)


def test_parse_script_plan_does_not_backfill_header_without_host_labels():
    """#582 review: ``Repos featured:`` is metadata, not a speaker turn."""
    script = (
        "Title: Weekly\n"
        "Repos featured: https://github.com/vercel/eve https://github.com/openai/gym\n"
        "---\n"
        "Alice: vercel/eve is the first project we actually say out loud.\n"
        "Bob: openai/gym follows at its own spoken cue.\n"
    )

    marked = infer_repo_visual_markers(script)
    lines = marked.splitlines()
    separator = lines.index("---")
    eve_marker = lines.index("## Visual: repo https://github.com/vercel/eve")
    eve_turn = next(i for i, line in enumerate(lines) if line.startswith("Alice:"))

    assert eve_marker > separator
    assert eve_marker == eve_turn - 1

    plan = parse_script_plan(script)
    assert [seg.visual_mode for seg in plan.segments] == [VisualMode.REPO, VisualMode.REPO]
    assert plan.repo_urls == ("https://github.com/vercel/eve", "https://github.com/openai/gym")
