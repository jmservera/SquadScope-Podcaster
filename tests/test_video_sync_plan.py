"""Tests for podcaster.video.sync_plan module."""

from __future__ import annotations

import yaml
import pytest

from podcaster.video.sync_plan import (
    RepoReference,
    extract_repo_urls,
    generate_episode_plan,
    generate_episode_plan_timed,
    plan_from_script,
    plan_from_script_timed,
    sort_repos_by_mention,
    _script_position,
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


# --- Script-position tests (#296) ---


class TestScriptPosition:
    def test_url_at_start(self):
        script = "https://github.com/a/b rest of script"
        assert _script_position(script, "https://github.com/a/b") == pytest.approx(0.0)

    def test_url_at_end(self):
        script = "start " + "https://github.com/a/b"
        pos = _script_position(script, "https://github.com/a/b")
        assert pos == pytest.approx(6 / len(script))

    def test_url_not_found_returns_one(self):
        assert _script_position("hello world", "https://github.com/x/y") == 1.0

    def test_empty_script_returns_zero(self):
        assert _script_position("", "https://github.com/a/b") == 0.0

    def test_position_between_zero_and_one(self):
        script = "aaa https://github.com/a/b zzz"
        pos = _script_position(script, "https://github.com/a/b")
        assert 0.0 < pos < 1.0


class TestSortReposByMention:
    def test_sorts_by_script_order(self):
        script = "first https://github.com/b/b ... later https://github.com/a/a"
        repos = [
            RepoReference(owner="a", name="a"),
            RepoReference(owner="b", name="b"),
        ]
        sorted_repos = sort_repos_by_mention(script, repos)
        assert sorted_repos[0].url == "https://github.com/b/b"
        assert sorted_repos[1].url == "https://github.com/a/a"

    def test_not_found_repos_go_last(self):
        script = "https://github.com/a/a is mentioned"
        repos = [
            RepoReference(owner="z", name="z"),
            RepoReference(owner="a", name="a"),
        ]
        sorted_repos = sort_repos_by_mention(script, repos)
        assert sorted_repos[0].url == "https://github.com/a/a"
        assert sorted_repos[1].url == "https://github.com/z/z"

    def test_empty_repos_returns_empty(self):
        assert sort_repos_by_mention("some script", []) == []

    def test_single_repo_unchanged(self):
        repo = RepoReference(owner="a", name="b")
        assert sort_repos_by_mention("https://github.com/a/b", [repo]) == [repo]


class TestGenerateEpisodePlanTimed:
    def _repo(self, owner: str, name: str) -> RepoReference:
        return RepoReference(owner=owner, name=name)

    def test_empty_repos_raises(self):
        with pytest.raises(ValueError, match="No repos"):
            generate_episode_plan_timed("script", [], 100.0)

    def test_zero_duration_raises(self):
        repos = [self._repo("a", "b")]
        with pytest.raises(ValueError, match="positive"):
            generate_episode_plan_timed("script", repos, 0.0)

    def test_negative_duration_raises(self):
        repos = [self._repo("a", "b")]
        with pytest.raises(ValueError, match="positive"):
            generate_episode_plan_timed("script", repos, -5.0)

    def test_single_repo_fills_total_duration(self):
        repos = [self._repo("a", "b")]
        plan = generate_episode_plan_timed(
            "intro https://github.com/a/b done", repos, 60.0
        )
        assert len(plan.segments) == 1
        seg = plan.segments[0]
        # Single segment: start + duration must equal total duration
        assert seg.duration_seconds > 0
        assert seg.start_seconds + seg.duration_seconds == pytest.approx(60.0, abs=0.1)

    def test_timing_reflects_script_position(self):
        # repo a/a is mentioned early (~10%), repo b/b mentioned late (~90%)
        script = (
            "aaa https://github.com/a/a bbb " + "x" * 800 +
            " https://github.com/b/b zzz"
        )
        repos = [
            self._repo("a", "a"),
            self._repo("b", "b"),
        ]
        plan = generate_episode_plan_timed(script, repos, 100.0)
        segs = {s.repo.url: s for s in plan.segments}
        start_a = segs["https://github.com/a/a"].start_seconds
        start_b = segs["https://github.com/b/b"].start_seconds
        assert start_a < start_b

    def test_min_segment_enforced(self):
        # Both repos appear at the very start — min_segment should separate them
        script = (
            "https://github.com/a/a https://github.com/b/b rest of script"
        )
        repos = [
            self._repo("a", "a"),
            self._repo("b", "b"),
        ]
        plan = generate_episode_plan_timed(script, repos, 20.0, min_segment_seconds=5.0)
        starts = [s.start_seconds for s in plan.segments]
        # second segment must start at least 5 s after the first
        assert starts[1] >= starts[0] + 4.9

    def test_segment_order_is_monotonic(self):
        script = (
            "https://github.com/c/c ... "
            "https://github.com/a/a ... "
            "https://github.com/b/b"
        )
        repos = [
            self._repo("a", "a"),
            self._repo("b", "b"),
            self._repo("c", "c"),
        ]
        plan = generate_episode_plan_timed(script, repos, 90.0)
        starts = [s.start_seconds for s in plan.segments]
        assert starts == sorted(starts)

    def test_total_duration_preserved(self):
        script = (
            "https://github.com/a/a ... "
            "https://github.com/b/b ... "
            "https://github.com/c/c"
        )
        repos = [
            self._repo("a", "a"),
            self._repo("b", "b"),
            self._repo("c", "c"),
        ]
        plan = generate_episode_plan_timed(script, repos, 90.0)
        assert plan.total_duration_seconds == 90.0
        last = plan.segments[-1]
        assert last.start_seconds + last.duration_seconds == pytest.approx(90.0, abs=1.0)


class TestPlanFromScriptTimed:
    def test_produces_plan(self):
        plan = plan_from_script_timed(SAMPLE_SCRIPT, 200.0)
        assert len(plan.segments) == 4
        assert plan.total_duration_seconds == 200.0

    def test_raises_on_no_repos(self):
        with pytest.raises(ValueError, match="No GitHub repository URLs"):
            plan_from_script_timed(SCRIPT_NO_REPOS, 60.0)

    def test_ordering_matches_script(self):
        # vscode is mentioned before ruff in SAMPLE_SCRIPT
        plan = plan_from_script_timed(SAMPLE_SCRIPT, 200.0)
        urls = [s.repo.url for s in plan.segments]
        idx_vscode = urls.index("https://github.com/microsoft/vscode")
        idx_ruff = urls.index("https://github.com/astral-sh/ruff")
        assert idx_vscode < idx_ruff
