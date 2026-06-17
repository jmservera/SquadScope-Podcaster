"""Tests for podcaster.video.sync_plan module."""

from __future__ import annotations

import yaml
import pytest

from podcaster.video.sync_plan import (
    EpisodePlan,
    RepoReference,
    VideoSegment,
    extract_repo_urls,
    generate_episode_plan,
    plan_from_script,
)


# --- Fixtures ---

SAMPLE_SCRIPT = """\
Title: Week 24 — Open Source Highlights
Episode: 42
Published: 2026-06-15
Source: https://github.com/jmservera/SquadScope
Duration: 15:42
TTS Provider: OpenAI TTS (Ada shimmer / Beto echo) [synthesis pending review]
License: CC-BY-4.0
---

Ada: Welcome to this week's episode! We've got some exciting repos to cover.
Beto: Absolutely! Let's start with https://github.com/microsoft/vscode — the latest release is huge.
Ada: And don't forget https://github.com/astral-sh/ruff which just hit 1.0.
Beto: We should also mention https://github.com/jmservera/SquadScope-Podcaster for the meta angle.
Ada: Plus there's this interesting fork at https://github.com/astral-sh/ruff/issues/123 but that's just an issue link.
Beto: Great episode everyone!
"""

SCRIPT_NO_REPOS = """\
Title: No Repos Episode
Episode: 1
---

Ada: Welcome! Today we talk about general topics.
Beto: No GitHub links at all today.
"""


# --- extract_repo_urls tests ---


class TestExtractRepoUrls:
    def test_extracts_repos_from_header_and_body(self):
        repos = extract_repo_urls(SAMPLE_SCRIPT)
        urls = [r.url for r in repos]
        assert "https://github.com/jmservera/SquadScope" in urls
        assert "https://github.com/microsoft/vscode" in urls
        assert "https://github.com/astral-sh/ruff" in urls
        assert "https://github.com/jmservera/SquadScope-Podcaster" in urls

    def test_deduplicates_repos(self):
        repos = extract_repo_urls(SAMPLE_SCRIPT)
        # ruff appears twice (once as repo, once in /issues/123 path) but same owner/name
        ruff_refs = [r for r in repos if r.name == "ruff"]
        assert len(ruff_refs) == 1

    def test_preserves_first_occurrence_order(self):
        repos = extract_repo_urls(SAMPLE_SCRIPT)
        urls = [r.url for r in repos]
        # SquadScope is in header (first), vscode in first body line
        assert urls.index("https://github.com/jmservera/SquadScope") < urls.index(
            "https://github.com/microsoft/vscode"
        )

    def test_returns_empty_for_no_repos(self):
        repos = extract_repo_urls(SCRIPT_NO_REPOS)
        assert repos == []

    def test_handles_trailing_punctuation(self):
        script = "Check out https://github.com/owner/repo. It's great!"
        repos = extract_repo_urls(script)
        assert repos[0].name == "repo"

    def test_handles_http_and_https(self):
        script = "http://github.com/owner/repo1 and https://github.com/owner/repo2"
        repos = extract_repo_urls(script)
        assert len(repos) == 2


# --- generate_episode_plan tests ---


class TestGenerateEpisodePlan:
    def test_equal_distribution(self):
        repos = [
            RepoReference(owner="a", name="r1"),
            RepoReference(owner="b", name="r2"),
            RepoReference(owner="c", name="r3"),
        ]
        plan = generate_episode_plan(repos, total_duration_seconds=90.0)
        assert len(plan.segments) == 3
        for seg in plan.segments:
            assert seg.duration_seconds == pytest.approx(30.0)

    def test_segments_cover_full_duration(self):
        repos = [
            RepoReference(owner="a", name="r1"),
            RepoReference(owner="b", name="r2"),
        ]
        plan = generate_episode_plan(repos, total_duration_seconds=120.0)
        assert plan.segments[0].start_seconds == pytest.approx(0.0)
        assert plan.segments[-1].end_seconds == pytest.approx(120.0)

    def test_segments_are_contiguous(self):
        repos = [
            RepoReference(owner="x", name=f"r{i}") for i in range(5)
        ]
        plan = generate_episode_plan(repos, total_duration_seconds=100.0)
        for i in range(len(plan.segments) - 1):
            assert plan.segments[i].end_seconds == pytest.approx(
                plan.segments[i + 1].start_seconds
            )

    def test_single_repo(self):
        repos = [RepoReference(owner="o", name="r")]
        plan = generate_episode_plan(repos, total_duration_seconds=60.0)
        assert len(plan.segments) == 1
        assert plan.segments[0].start_seconds == 0.0
        assert plan.segments[0].duration_seconds == 60.0

    def test_raises_on_empty_repos(self):
        with pytest.raises(ValueError, match="No repos"):
            generate_episode_plan([], total_duration_seconds=60.0)

    def test_raises_on_zero_duration(self):
        repos = [RepoReference(owner="o", name="r")]
        with pytest.raises(ValueError, match="positive"):
            generate_episode_plan(repos, total_duration_seconds=0.0)

    def test_raises_on_negative_duration(self):
        repos = [RepoReference(owner="o", name="r")]
        with pytest.raises(ValueError, match="positive"):
            generate_episode_plan(repos, total_duration_seconds=-10.0)


# --- EpisodePlan.to_yaml tests ---


class TestEpisodePlanYaml:
    def test_yaml_roundtrip_structure(self):
        repos = [
            RepoReference(owner="microsoft", name="vscode"),
            RepoReference(owner="astral-sh", name="ruff"),
        ]
        plan = generate_episode_plan(repos, total_duration_seconds=600.0)
        yaml_str = plan.to_yaml()
        data = yaml.safe_load(yaml_str)

        assert data["total_duration_seconds"] == 600.0
        assert len(data["segments"]) == 2
        seg0 = data["segments"][0]
        assert seg0["repo_url"] == "https://github.com/microsoft/vscode"
        assert seg0["repo_owner"] == "microsoft"
        assert seg0["repo_name"] == "vscode"
        assert seg0["start_seconds"] == 0.0
        assert seg0["duration_seconds"] == 300.0
        assert seg0["end_seconds"] == 300.0

    def test_yaml_is_valid(self):
        repos = [RepoReference(owner="o", name="r")]
        plan = generate_episode_plan(repos, total_duration_seconds=42.5)
        yaml_str = plan.to_yaml()
        # Should not raise
        data = yaml.safe_load(yaml_str)
        assert isinstance(data, dict)


# --- plan_from_script end-to-end tests ---


class TestPlanFromScript:
    def test_end_to_end(self):
        plan = plan_from_script(SAMPLE_SCRIPT, total_duration_seconds=240.0)
        assert plan.total_duration_seconds == 240.0
        # 4 unique repos in sample script
        assert len(plan.segments) == 4
        # Each segment should be 60 seconds
        for seg in plan.segments:
            assert seg.duration_seconds == pytest.approx(60.0)

    def test_raises_on_no_repos(self):
        with pytest.raises(ValueError, match="No GitHub repository URLs"):
            plan_from_script(SCRIPT_NO_REPOS, total_duration_seconds=60.0)

    def test_yaml_output_has_all_repos(self):
        plan = plan_from_script(SAMPLE_SCRIPT, total_duration_seconds=200.0)
        yaml_str = plan.to_yaml()
        data = yaml.safe_load(yaml_str)
        repo_urls = [s["repo_url"] for s in data["segments"]]
        assert "https://github.com/jmservera/SquadScope" in repo_urls
        assert "https://github.com/microsoft/vscode" in repo_urls
        assert "https://github.com/astral-sh/ruff" in repo_urls
        assert "https://github.com/jmservera/SquadScope-Podcaster" in repo_urls
