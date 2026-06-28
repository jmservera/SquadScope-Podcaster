"""Tests for podcaster.video.sync_plan module."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from podcaster.script_plan import VisualMode
from podcaster.video.sync_plan import (
    REMOVED_REPO_REASON,
    VISUAL_KIND_IMAGE,
    VISUAL_KIND_RECORDING,
    VISUAL_KIND_SCREENSHOT,
    AudioCuePoint,
    EpisodePlan,
    RepoReference,
    VideoSegment,
    VisualCue,
    _script_position,
    annotate_removed_repos,
    build_audio_cue_points,
    check_repo_removed,
    extract_repo_urls,
    extract_source_url,
    fetch_repos_from_article,
    generate_episode_plan,
    generate_episode_plan_timed,
    generate_generic_plan,
    plan_from_realized_metadata,
    plan_from_script,
    plan_from_script_timed,
    prepend_weekly_segment,
    removed_repo_speaker_notes,
    snap_episode_plan_to_audio,
    snap_to_audio_boundary,
    snap_visual_cues,
    sort_repos_by_mention,
    weekly_url_from_job_id,
)

# --- Fixtures ---

SAMPLE_SCRIPT = (
    "Title: Week 24 — Open Source Highlights\n"
    "Episode: 42\n"
    "Published: 2026-06-15\n"
    "Source: https://github.com/jmservera/SquadScope\n"
    "Duration: 15:42\n"
    "TTS Provider: OpenAI TTS (Ada shimmer / Beto echo) [synthesis pending review]\n"
    "License: CC-BY-4.0\n"
    "---\n"
    "\n"
    "Ada: Welcome to this week's episode! We've got some exciting repos to cover.\n"
    "Beto: Absolutely! Let's start with https://github.com/microsoft/vscode — the latest "
    "release is huge.\n"
    "Ada: And don't forget https://github.com/astral-sh/ruff which just hit 1.0.\n"
    "Beto: We should also mention https://github.com/jmservera/SquadScope-Podcaster for "
    "the meta angle.\n"
    "Ada: Plus there's this interesting fork at https://github.com/astral-sh/ruff/issues/"
    "123 but that's just an issue link.\n"
    "Beto: Great episode everyone!\n"
)

SCRIPT_NO_REPOS = """\
Title: No Repos Episode
Episode: 1
---

Ada: Welcome! Today we talk about general topics.
Beto: No GitHub links at all today.
"""

SCRIPT_NO_REPOS_WITH_SOURCE = """\
Title: Weekly Roundup
Episode: 26
Source URL: https://claracle.com/weekly/2026/W26/
Generated: 2026-06-22T00:00:00Z
---

Ada: Welcome! Today we talk about general trends.
Beto: No GitHub links at all today.
"""


# --- extract_source_url tests ---


class TestExtractSourceUrl:
    def test_extracts_url_from_header(self):
        assert (
            extract_source_url(SCRIPT_NO_REPOS_WITH_SOURCE)
            == "https://claracle.com/weekly/2026/W26/"
        )

    def test_returns_none_when_absent(self):
        assert extract_source_url(SCRIPT_NO_REPOS) is None

    def test_ignores_url_in_body(self):
        script = "Title: X\n---\n\nAda: see Source URL: not-a-header here\n"
        # Only header-style lines (Source URL: at line start) are matched.
        assert extract_source_url(script) is None

    def test_rejects_non_https_urls(self):
        """Prevent SSRF via file://, http://, or metadata URLs."""
        for bad_url in [
            "file:///etc/passwd",
            "http://169.254.169.254/latest/meta-data/",
            "ftp://evil.com/payload",
        ]:
            script = f"Source URL: {bad_url}\n---\nContent"
            assert extract_source_url(script) is None


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class TestFetchReposFromArticle:
    _PAGE_HTML = (
        "<html><body>"
        "<a href='https://github.com/microsoft/vscode'>vscode</a>"
        "<a href='https://github.com/astral-sh/ruff'>ruff</a>"
        "</body></html>"
    )

    def test_tries_lowercase_url_first(self, monkeypatch):
        calls: list[str] = []

        def fake_get(url, timeout=None):
            calls.append(url)
            return _FakeResponse(200, self._PAGE_HTML)

        monkeypatch.setattr("podcaster.video.sync_plan.requests.get", fake_get)
        repos = fetch_repos_from_article("https://claracle.com/weekly/2026/W26/")
        # lowercase variant attempted first
        assert calls[0] == "https://claracle.com/weekly/2026/w26/"
        assert RepoReference("microsoft", "vscode") in repos
        assert RepoReference("astral-sh", "ruff") in repos

    def test_falls_back_to_original_case(self, monkeypatch):
        def fake_get(url, timeout=None):
            if url == "https://claracle.com/weekly/2026/w26/":
                return _FakeResponse(404, "not found")
            return _FakeResponse(200, self._PAGE_HTML)

        monkeypatch.setattr("podcaster.video.sync_plan.requests.get", fake_get)
        repos = fetch_repos_from_article("https://claracle.com/weekly/2026/W26/")
        assert RepoReference("microsoft", "vscode") in repos

    def test_excludes_own_project_repo(self, monkeypatch):
        # The project's own repo must never appear in the extracted list, even
        # when present on the article page (issue #353). Case-insensitive.
        html = (
            "<html><body>"
            "<a href='https://github.com/jmservera/SquadScope'>self</a>"
            "<a href='https://github.com/JMSERVERA/squadscope'>self2</a>"
            "<a href='https://github.com/microsoft/vscode'>vscode</a>"
            "</body></html>"
        )
        monkeypatch.setattr(
            "podcaster.video.sync_plan.requests.get",
            lambda url, timeout=None: _FakeResponse(200, html),
        )
        repos = fetch_repos_from_article("https://claracle.com/x/")
        assert RepoReference("microsoft", "vscode") in repos
        assert RepoReference("jmservera", "SquadScope") not in repos
        assert all(r.name.lower() != "squadscope" for r in repos)

    def test_returns_empty_on_network_error(self, monkeypatch):
        import requests as _requests

        def fake_get(url, timeout=None):
            raise _requests.RequestException("boom")

        monkeypatch.setattr("podcaster.video.sync_plan.requests.get", fake_get)
        assert fetch_repos_from_article("https://claracle.com/x/") == []

    def test_returns_empty_when_no_repos_on_page(self, monkeypatch):
        monkeypatch.setattr(
            "podcaster.video.sync_plan.requests.get",
            lambda url, timeout=None: _FakeResponse(200, "<html>nothing</html>"),
        )
        assert fetch_repos_from_article("https://claracle.com/x/") == []

    def test_rejects_non_https(self):
        assert fetch_repos_from_article("http://example.com/") == []
        assert fetch_repos_from_article("") == []

    def test_rejects_disallowed_host(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            "podcaster.video.sync_plan.requests.get",
            lambda url, timeout=None: called.append(url) or _FakeResponse(200, "<html></html>"),
        )
        # Hosts outside the allowlist must never be fetched (SSRF guard).
        assert fetch_repos_from_article("https://example.com/x/") == []
        assert fetch_repos_from_article("https://evil.claracle.com.attacker/") == []
        assert fetch_repos_from_article("https://169.254.169.254/latest/") == []
        assert called == []

    def test_allows_claracle_hosts(self, monkeypatch):
        monkeypatch.setattr(
            "podcaster.video.sync_plan.requests.get",
            lambda url, timeout=None: _FakeResponse(
                200, '<a href="https://github.com/microsoft/vscode">repo</a>'
            ),
        )
        for host in ("claracle.com", "www.claracle.com"):
            repos = fetch_repos_from_article(f"https://{host}/weekly/2026/W26/")
            assert repos == [RepoReference("microsoft", "vscode")]


class TestGenerateGenericPlan:
    def test_without_source_url(self):
        plan = generate_generic_plan(60.0)
        seg = plan.segments[0]
        assert seg.is_generic
        assert seg.source_url is None

    def test_with_source_url(self):
        plan = generate_generic_plan(60.0, "https://claracle.com/weekly/2026/W26/")
        seg = plan.segments[0]
        assert seg.is_generic
        assert seg.source_url == "https://claracle.com/weekly/2026/W26/"


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
        repos = [RepoReference(owner="x", name=f"r{i}") for i in range(5)]
        plan = generate_episode_plan(repos, total_duration_seconds=100.0)
        for i in range(len(plan.segments) - 1):
            assert plan.segments[i].end_seconds == pytest.approx(plan.segments[i + 1].start_seconds)

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

    def test_no_segment_cap(self):
        """All repos become segments — pairwise composition has no cap (#349)."""
        repos = [RepoReference(owner="o", name=f"r{i}") for i in range(20)]
        plan = generate_episode_plan(repos, total_duration_seconds=300.0)
        assert len(plan.segments) == 20
        # Duration is divided among all repos
        expected_dur = 300.0 / 20
        for seg in plan.segments:
            assert seg.duration_seconds == pytest.approx(expected_dur)


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

    def test_generates_generic_plan_on_no_repos(self):
        plan = plan_from_script(SCRIPT_NO_REPOS, total_duration_seconds=60.0)
        assert plan.total_duration_seconds == 60.0
        assert len(plan.segments) == 1
        seg = plan.segments[0]
        assert seg.is_generic
        assert seg.repo is None
        assert seg.start_seconds == 0.0
        assert seg.duration_seconds == pytest.approx(60.0)

    def test_generic_plan_uses_source_url(self, monkeypatch):
        # No repos on the fetched article either → fall back to generic plan.
        monkeypatch.setattr(
            "podcaster.video.sync_plan.fetch_repos_from_article",
            lambda url: [],
        )
        plan = plan_from_script(SCRIPT_NO_REPOS_WITH_SOURCE, total_duration_seconds=60.0)
        seg = plan.segments[0]
        assert seg.is_generic
        assert seg.source_url == "https://claracle.com/weekly/2026/W26/"

    def test_uses_repos_fetched_from_article(self, monkeypatch):
        fetched = [
            RepoReference("microsoft", "vscode"),
            RepoReference("astral-sh", "ruff"),
        ]
        monkeypatch.setattr(
            "podcaster.video.sync_plan.fetch_repos_from_article",
            lambda url: fetched,
        )
        plan = plan_from_script(SCRIPT_NO_REPOS_WITH_SOURCE, total_duration_seconds=60.0)
        # Real timed segments per repo, not a single generic background.
        assert len(plan.segments) == 2
        assert all(not s.is_generic for s in plan.segments)
        assert {s.repo for s in plan.segments} == set(fetched)

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
        plan = generate_episode_plan_timed("intro https://github.com/a/b done", repos, 60.0)
        assert len(plan.segments) == 1
        seg = plan.segments[0]
        # Single segment: start + duration must equal total duration
        assert seg.duration_seconds > 0
        assert seg.start_seconds + seg.duration_seconds == pytest.approx(60.0, abs=0.1)

    def test_timing_reflects_script_position(self):
        # repo a/a is mentioned early (~10%), repo b/b mentioned late (~90%)
        script = "aaa https://github.com/a/a bbb " + "x" * 800 + " https://github.com/b/b zzz"
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
        script = "https://github.com/a/a https://github.com/b/b rest of script"
        repos = [
            self._repo("a", "a"),
            self._repo("b", "b"),
        ]
        plan = generate_episode_plan_timed(script, repos, 20.0, min_segment_seconds=5.0)
        starts = [s.start_seconds for s in plan.segments]
        # second segment must start at least 5 s after the first
        assert starts[1] >= starts[0] + 4.9

    def test_segment_order_is_monotonic(self):
        script = "https://github.com/c/c ... https://github.com/a/a ... https://github.com/b/b"
        repos = [
            self._repo("a", "a"),
            self._repo("b", "b"),
            self._repo("c", "c"),
        ]
        plan = generate_episode_plan_timed(script, repos, 90.0)
        starts = [s.start_seconds for s in plan.segments]
        assert starts == sorted(starts)

    def test_total_duration_preserved(self):
        script = "https://github.com/a/a ... https://github.com/b/b ... https://github.com/c/c"
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

    def test_generates_generic_plan_on_no_repos(self):
        plan = plan_from_script_timed(SCRIPT_NO_REPOS, 60.0)
        assert len(plan.segments) == 1
        assert plan.segments[0].is_generic
        assert plan.total_duration_seconds == 60.0

    def test_generic_plan_uses_source_url(self, monkeypatch):
        monkeypatch.setattr(
            "podcaster.video.sync_plan.fetch_repos_from_article",
            lambda url: [],
        )
        plan = plan_from_script_timed(SCRIPT_NO_REPOS_WITH_SOURCE, 60.0)
        assert plan.segments[0].is_generic
        assert plan.segments[0].source_url == "https://claracle.com/weekly/2026/W26/"

    def test_article_repos_use_equal_split(self, monkeypatch):
        # Repos fetched from the article are absent from the script, so timed
        # positioning would clump them all at the end. Verify we fall back to an
        # equal-split plan with evenly distributed start times instead.
        fetched = [
            RepoReference("microsoft", "vscode"),
            RepoReference("astral-sh", "ruff"),
            RepoReference("python", "cpython"),
        ]
        monkeypatch.setattr(
            "podcaster.video.sync_plan.fetch_repos_from_article",
            lambda url: fetched,
        )
        plan = plan_from_script_timed(SCRIPT_NO_REPOS_WITH_SOURCE, 90.0)
        assert len(plan.segments) == 3
        assert all(not s.is_generic for s in plan.segments)
        starts = [s.start_seconds for s in plan.segments]
        assert starts == pytest.approx([0.0, 30.0, 60.0])

    def test_ordering_matches_script(self):
        # vscode is mentioned before ruff in SAMPLE_SCRIPT
        plan = plan_from_script_timed(SAMPLE_SCRIPT, 200.0)
        urls = [s.repo.url for s in plan.segments]
        idx_vscode = urls.index("https://github.com/microsoft/vscode")
        idx_ruff = urls.index("https://github.com/astral-sh/ruff")
        assert idx_vscode < idx_ruff


# --- Weekly-page first segment tests (#382) ---


class TestWeeklyUrlFromJobId:
    def test_derives_lowercase_padded_week(self):
        assert (
            weekly_url_from_job_id("podcast-2026-W26-de5f4e6e0435")
            == "https://claracle.com/weekly/2026/w26/"
        )

    def test_pads_single_digit_week(self):
        assert (
            weekly_url_from_job_id("podcast-2026-W6-abc") == "https://claracle.com/weekly/2026/w06/"
        )

    def test_accepts_lowercase_w(self):
        assert (
            weekly_url_from_job_id("podcast-2025-w03-xyz")
            == "https://claracle.com/weekly/2025/w03/"
        )

    def test_returns_none_without_week_token(self):
        assert weekly_url_from_job_id("nonsense-job") is None

    def test_returns_none_on_empty(self):
        assert weekly_url_from_job_id("") is None


class TestPrependWeeklySegment:
    def _repo_plan(self, first_start: float = 18.0) -> EpisodePlan:
        segs = (
            VideoSegment(
                repo=RepoReference("microsoft", "vscode"),
                start_seconds=first_start,
                duration_seconds=100.0,
            ),
            VideoSegment(
                repo=RepoReference("astral-sh", "ruff"),
                start_seconds=first_start + 100.0,
                duration_seconds=82.0,
            ),
        )
        return EpisodePlan(total_duration_seconds=200.0, segments=segs)

    def test_inserts_weekly_as_first_segment(self):
        plan = self._repo_plan()
        out = prepend_weekly_segment(plan, "podcast-2026-W26-de5f")
        assert len(out.segments) == 3
        first = out.segments[0]
        assert first.is_generic
        assert first.source_url == "https://claracle.com/weekly/2026/w26/"
        assert first.start_seconds == 0.0
        # repo segments shifted after the weekly segment
        assert out.segments[1].repo == RepoReference("microsoft", "vscode")

    def test_duration_fills_bridge_with_min_floor(self):
        # bridge of 18s -> used as-is (above the 15s floor)
        out = prepend_weekly_segment(self._repo_plan(18.0), "podcast-2026-W26-x")
        assert out.segments[0].duration_seconds == 18.0
        # bridge of 5s -> raised to the 15s minimum floor
        out_low = prepend_weekly_segment(self._repo_plan(5.0), "podcast-2026-W26-x")
        assert out_low.segments[0].duration_seconds == 15.0
        # bridge of 40s -> fills the whole bridge (no maximum clamp), so the
        # plan keeps tiling the timeline with no gap (issue #544)
        out_high = prepend_weekly_segment(self._repo_plan(40.0), "podcast-2026-W26-x")
        assert out_high.segments[0].duration_seconds == 40.0

    def test_no_week_token_returns_plan_unchanged(self):
        plan = self._repo_plan()
        assert prepend_weekly_segment(plan, "bad-job-id") is plan

    def test_generic_only_plan_unchanged(self):
        plan = EpisodePlan(
            total_duration_seconds=60.0,
            segments=(
                VideoSegment(
                    start_seconds=0.0,
                    duration_seconds=60.0,
                    repo=None,
                    source_url="https://claracle.com/weekly/2026/W26/",
                ),
            ),
        )
        assert prepend_weekly_segment(plan, "podcast-2026-W26-x") is plan

    def test_does_not_double_count_bridge_time(self):
        # bridge of 18s is within [15, 20]: weekly segment occupies exactly the
        # existing bridge, so existing segments must NOT shift and the total
        # duration must be unchanged (issue #382).
        plan = self._repo_plan(18.0)
        out = prepend_weekly_segment(plan, "podcast-2026-W26-x")
        assert out.segments[0].start_seconds == 0.0
        assert out.segments[0].duration_seconds == 18.0
        assert out.segments[1].start_seconds == 18.0
        assert out.segments[2].start_seconds == 118.0
        assert out.total_duration_seconds == plan.total_duration_seconds

    def test_shifts_only_extra_clamped_time(self):
        # bridge of 5s is clamped up to the 15s minimum: only the extra 10s is
        # introduced, so existing segments shift by 10s and total grows by 10s.
        plan = self._repo_plan(5.0)
        out = prepend_weekly_segment(plan, "podcast-2026-W26-x")
        assert out.segments[0].duration_seconds == 15.0
        assert out.segments[1].start_seconds == 15.0
        assert out.segments[2].start_seconds == 115.0
        assert out.total_duration_seconds == plan.total_duration_seconds + 10.0

    def test_large_bridge_filled_without_shift(self):
        # bridge of 40s: the weekly segment fills the whole bridge (no maximum
        # clamp), so no shift, no total-duration change, and the plan tiles the
        # timeline with no gap before the first repo (issue #544).
        plan = self._repo_plan(40.0)
        out = prepend_weekly_segment(plan, "podcast-2026-W26-x")
        assert out.segments[0].duration_seconds == 40.0
        assert out.segments[1].start_seconds == 40.0
        assert out.total_duration_seconds == plan.total_duration_seconds
        # The weekly segment ends exactly where the first repo begins (no hole).
        assert out.segments[0].duration_seconds == out.segments[1].start_seconds

    def test_idempotent(self):
        plan = self._repo_plan()
        once = prepend_weekly_segment(plan, "podcast-2026-W26-x")
        twice = prepend_weekly_segment(once, "podcast-2026-W26-x")
        assert len(twice.segments) == len(once.segments)
        assert twice.segments[0].source_url == once.segments[0].source_url

    def test_plan_tiles_timeline_with_no_gap(self):
        # The composed video lays segments out by duration, so the plan must tile
        # [0, total] contiguously — any gap shifts every repo earlier than it is
        # discussed (issue #544). Build a realistic plan from real mention times
        # (which tiles [first_start, total]); prepend must fill the leading
        # bridge so the whole timeline tiles, for both small and large bridges.
        repos = (
            RepoReference("vercel", "eve"),
            RepoReference("astral-sh", "ruff"),
            RepoReference("microsoft", "vscode"),
        )
        for first_start in (5.0, 18.0, 106.0):
            plan = EpisodePlan(
                total_duration_seconds=first_start + 280.0,
                segments=(
                    VideoSegment(repo=repos[0], start_seconds=first_start, duration_seconds=90.0),
                    VideoSegment(
                        repo=repos[1],
                        start_seconds=first_start + 90.0,
                        duration_seconds=90.0,
                    ),
                    VideoSegment(
                        repo=repos[2],
                        start_seconds=first_start + 180.0,
                        duration_seconds=100.0,
                    ),
                ),
            )
            out = prepend_weekly_segment(plan, "podcast-2026-W26-x")
            assert out.segments[0].is_generic
            assert out.segments[0].start_seconds == 0.0
            cursor = 0.0
            for seg in out.segments:
                assert abs(seg.start_seconds - cursor) < 1e-6, (
                    f"gap/overlap at {seg.start_seconds} (expected {cursor}) "
                    f"for first_start={first_start}"
                )
                cursor += seg.duration_seconds
            assert abs(cursor - out.total_duration_seconds) < 1e-6


# --- Audio-boundary sync tests (#297) ---


class TestBuildAudioCuePoints:
    def test_empty_segments_returns_empty(self):
        assert build_audio_cue_points([], []) == []

    def test_single_segment_produces_start_and_end(self):
        cues = build_audio_cue_points([0.0], [10.0])
        times = [c.time_seconds for c in cues]
        assert 0.0 in times
        assert 10.0 in times

    def test_single_segment_no_gap_midpoint(self):
        cues = build_audio_cue_points([0.0], [10.0], gap_seconds=0.35)
        kinds = [c.kind for c in cues]
        assert "gap_midpoint" not in kinds

    def test_two_segments_produces_gap_midpoint(self):
        # segment 0: 0..5, gap 0.5, segment 1: 5.5..10.5
        cues = build_audio_cue_points([0.0, 5.5], [5.0, 5.0], gap_seconds=0.5)
        kinds = [c.kind for c in cues]
        assert "gap_midpoint" in kinds
        midpoint_cue = next(c for c in cues if c.kind == "gap_midpoint")
        assert midpoint_cue.time_seconds == pytest.approx(5.25)  # midpoint of 5.0..5.5

    def test_cues_sorted_by_time(self):
        cues = build_audio_cue_points([0.0, 10.0, 20.0], [8.0, 8.0, 8.0], gap_seconds=2.0)
        times = [c.time_seconds for c in cues]
        assert times == sorted(times)

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError, match="length"):
            build_audio_cue_points([0.0, 5.0], [5.0])

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            build_audio_cue_points([0.0], [-1.0])

    def test_no_gap_when_gap_seconds_zero(self):
        cues = build_audio_cue_points([0.0, 10.0], [10.0, 10.0], gap_seconds=0.0)
        kinds = [c.kind for c in cues]
        assert "gap_midpoint" not in kinds

    def test_deduplication_within_10ms(self):
        # Two segments that end/start at almost the same time
        cues = build_audio_cue_points([0.0, 10.005], [10.0, 5.0], gap_seconds=0.0)
        times = [c.time_seconds for c in cues]
        # 10.0 (end of seg 0) and 10.005 (start of seg 1) should deduplicate
        close = [t for t in times if 9.99 < t < 10.02]
        assert len(close) == 1


class TestSnapToAudioBoundary:
    def _cues(self, *times: float) -> list[AudioCuePoint]:
        return [AudioCuePoint(t, "turn_start") for t in times]

    def test_snaps_within_tolerance(self):
        cues = self._cues(0.0, 10.0, 20.0)
        assert snap_to_audio_boundary(9.7, cues, tolerance_seconds=0.5) == pytest.approx(10.0)

    def test_no_snap_outside_tolerance(self):
        cues = self._cues(0.0, 10.0, 20.0)
        assert snap_to_audio_boundary(11.0, cues, tolerance_seconds=0.5) == pytest.approx(11.0)

    def test_empty_cues_returns_original(self):
        assert snap_to_audio_boundary(5.0, [], 0.5) == pytest.approx(5.0)

    def test_exact_match_snaps(self):
        cues = self._cues(5.0)
        assert snap_to_audio_boundary(5.0, cues, 0.5) == pytest.approx(5.0)

    def test_boundary_at_tolerance_snaps(self):
        # Distance == tolerance → should snap
        cues = self._cues(10.0)
        assert snap_to_audio_boundary(10.5, cues, tolerance_seconds=0.5) == pytest.approx(10.0)

    def test_just_outside_tolerance_does_not_snap(self):
        cues = self._cues(10.0)
        assert snap_to_audio_boundary(10.51, cues, tolerance_seconds=0.5) == pytest.approx(10.51)

    def test_negative_offset_snaps(self):
        cues = self._cues(10.0)
        assert snap_to_audio_boundary(9.6, cues, tolerance_seconds=0.5) == pytest.approx(10.0)


class TestSnapEpisodePlanToAudio:
    def _plan(self, *segs: tuple[str, float, float]) -> EpisodePlan:
        """Build plan from (name, start, duration) tuples."""
        segments = tuple(
            VideoSegment(
                repo=RepoReference(owner="test", name=name),
                start_seconds=start,
                duration_seconds=dur,
            )
            for name, start, dur in segs
        )
        total = segments[-1].start_seconds + segments[-1].duration_seconds if segments else 0.0
        return EpisodePlan(total_duration_seconds=total, segments=segments)

    def _cues(self, *times: float) -> list[AudioCuePoint]:
        return [AudioCuePoint(t, "turn_start") for t in times]

    def test_empty_plan_returns_unchanged(self):
        plan = EpisodePlan(total_duration_seconds=60.0, segments=())
        result = snap_episode_plan_to_audio(plan, self._cues(10.0))
        assert result == plan

    def test_snaps_segment_starts(self):
        plan = self._plan(("a", 9.7, 30.0), ("b", 39.7, 30.0))
        cues = self._cues(10.0, 40.0)
        result = snap_episode_plan_to_audio(plan, cues, tolerance_seconds=0.5)
        assert result.segments[0].start_seconds == pytest.approx(10.0)
        assert result.segments[1].start_seconds == pytest.approx(40.0)

    def test_no_snap_outside_tolerance(self):
        plan = self._plan(("a", 5.0, 30.0))
        cues = self._cues(10.0)
        result = snap_episode_plan_to_audio(plan, cues, tolerance_seconds=0.5)
        assert result.segments[0].start_seconds == pytest.approx(5.0)

    def test_total_duration_preserved(self):
        plan = self._plan(("a", 0.0, 30.0), ("b", 30.0, 30.0))
        cues = self._cues(5.0, 35.0)
        result = snap_episode_plan_to_audio(plan, cues)
        assert result.total_duration_seconds == plan.total_duration_seconds

    def test_segment_durations_adjusted(self):
        # After snapping start of seg[1] from 29.8 → 30.0, seg[0] duration grows
        plan = self._plan(("a", 0.0, 29.8), ("b", 29.8, 30.2))
        cues = self._cues(0.0, 30.0, 60.0)
        result = snap_episode_plan_to_audio(plan, cues, tolerance_seconds=0.5)
        assert result.segments[0].duration_seconds == pytest.approx(30.0)
        assert result.segments[1].start_seconds == pytest.approx(30.0)

    def test_monotonic_order_maintained(self):
        # Two segments both snap to same cue — second should not precede first
        plan = self._plan(("a", 9.8, 0.3), ("b", 10.1, 30.0))
        cues = self._cues(10.0)
        result = snap_episode_plan_to_audio(plan, cues, tolerance_seconds=0.5)
        starts = [s.start_seconds for s in result.segments]
        assert starts[0] <= starts[1]


class TestSnapVisualCues:
    def _cues(self, *times: float) -> list[AudioCuePoint]:
        return [AudioCuePoint(t, "turn_start") for t in times]

    def test_snaps_recording_cue(self):
        cues = self._cues(10.0)
        vcues = [VisualCue(time_seconds=9.8, kind=VISUAL_KIND_RECORDING, label="repo")]
        result = snap_visual_cues(vcues, cues, tolerance_seconds=0.5)
        assert result[0].time_seconds == pytest.approx(10.0)
        assert result[0].kind == VISUAL_KIND_RECORDING

    def test_snaps_image_cue(self):
        cues = self._cues(5.0)
        vcues = [VisualCue(time_seconds=5.3, kind=VISUAL_KIND_IMAGE, label="img.png")]
        result = snap_visual_cues(vcues, cues, tolerance_seconds=0.5)
        assert result[0].time_seconds == pytest.approx(5.0)

    def test_snaps_screenshot_cue(self):
        cues = self._cues(15.0)
        vcues = [VisualCue(time_seconds=14.7, kind=VISUAL_KIND_SCREENSHOT, label="shot.png")]
        result = snap_visual_cues(vcues, cues, tolerance_seconds=0.5)
        assert result[0].time_seconds == pytest.approx(15.0)

    def test_preserves_label_and_kind(self):
        cues = self._cues(10.0)
        vcues = [VisualCue(9.9, VISUAL_KIND_IMAGE, "my_label")]
        result = snap_visual_cues(vcues, cues)
        assert result[0].label == "my_label"
        assert result[0].kind == VISUAL_KIND_IMAGE

    def test_no_snap_outside_tolerance(self):
        cues = self._cues(10.0)
        vcues = [VisualCue(time_seconds=15.0, kind=VISUAL_KIND_IMAGE, label="")]
        result = snap_visual_cues(vcues, cues, tolerance_seconds=0.5)
        assert result[0].time_seconds == pytest.approx(15.0)

    def test_empty_cues_returns_empty(self):
        assert snap_visual_cues([], [AudioCuePoint(0.0, "turn_start")]) == []

    def test_multiple_cues_snapped_independently(self):
        audio_cues = self._cues(10.0, 20.0, 30.0)
        vcues = [
            VisualCue(9.7, VISUAL_KIND_RECORDING, "a"),
            VisualCue(19.8, VISUAL_KIND_IMAGE, "b"),
            VisualCue(35.0, VISUAL_KIND_SCREENSHOT, "c"),  # out of tolerance
        ]
        result = snap_visual_cues(vcues, audio_cues, tolerance_seconds=0.5)
        assert result[0].time_seconds == pytest.approx(10.0)
        assert result[1].time_seconds == pytest.approx(20.0)
        assert result[2].time_seconds == pytest.approx(35.0)  # unchanged


# --- Removed/bot repo pre-flight detection (issue #394) ---


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


class TestCheckRepoRemoved:
    def test_404_is_removed(self):
        with patch("podcaster.video.sync_plan.requests.head", return_value=_FakeResp(404)):
            assert check_repo_removed("https://github.com/someuser/mktail") is True

    def test_200_is_not_removed(self):
        with patch("podcaster.video.sync_plan.requests.head", return_value=_FakeResp(200)):
            assert check_repo_removed("https://github.com/microsoft/vscode") is False

    def test_other_status_is_not_removed(self):
        # Rate-limiting / server errors must not be mistaken for removal.
        for status in (429, 500, 503):
            with patch("podcaster.video.sync_plan.requests.head", return_value=_FakeResp(status)):
                assert check_repo_removed("https://github.com/a/b") is False

    def test_network_error_is_not_removed(self):
        import requests as _requests

        with patch(
            "podcaster.video.sync_plan.requests.head",
            side_effect=_requests.RequestException("boom"),
        ):
            assert check_repo_removed("https://github.com/a/b") is False

    def test_empty_url_is_not_removed(self):
        assert check_repo_removed("") is False


class TestAnnotateRemovedRepos:
    def _plan(self):
        return EpisodePlan(
            total_duration_seconds=30.0,
            segments=(
                VideoSegment(
                    repo=RepoReference("microsoft", "vscode"),
                    start_seconds=0.0,
                    duration_seconds=10.0,
                ),
                VideoSegment(
                    repo=RepoReference("someuser", "mktail"),
                    start_seconds=10.0,
                    duration_seconds=10.0,
                ),
                VideoSegment(
                    repo=None,
                    source_url="https://claracle.com/x",
                    start_seconds=20.0,
                    duration_seconds=10.0,
                ),
            ),
        )

    def test_marks_only_removed_repo(self):
        def checker(url, timeout=5.0):
            return "mktail" in url

        result = annotate_removed_repos(self._plan(), checker=checker)
        segs = result.segments
        assert segs[0].removed_reason is None  # vscode present
        assert segs[1].removed_reason == REMOVED_REPO_REASON  # mktail removed
        assert segs[1].is_removed is True
        assert segs[2].removed_reason is None  # generic untouched
        # Timing preserved exactly so audio stays in sync.
        assert [s.start_seconds for s in segs] == [0.0, 10.0, 20.0]
        assert result.total_duration_seconds == 30.0

    def test_does_not_mutate_original(self):
        plan = self._plan()
        annotate_removed_repos(plan, checker=lambda url, timeout=5.0: True)
        assert all(s.removed_reason is None for s in plan.segments)

    def test_checker_exception_treated_as_present(self):
        def checker(url, timeout=5.0):
            raise RuntimeError("boom")

        result = annotate_removed_repos(self._plan(), checker=checker)
        assert all(s.removed_reason is None for s in result.segments)

    def test_already_annotated_segment_left_untouched(self):
        plan = EpisodePlan(
            total_duration_seconds=10.0,
            segments=(
                VideoSegment(
                    repo=RepoReference("a", "b"),
                    start_seconds=0.0,
                    duration_seconds=10.0,
                    removed_reason="pre-set",
                ),
            ),
        )
        calls = []

        def checker(url, timeout=5.0):
            calls.append(url)
            return False

        result = annotate_removed_repos(plan, checker=checker)
        assert calls == []  # not re-checked
        assert result.segments[0].removed_reason == "pre-set"


class TestRemovedRepoSpeakerNotes:
    def test_notes_for_removed_repos_only(self):
        plan = EpisodePlan(
            total_duration_seconds=20.0,
            segments=(
                VideoSegment(
                    repo=RepoReference("microsoft", "vscode"),
                    start_seconds=0.0,
                    duration_seconds=10.0,
                ),
                VideoSegment(
                    repo=RepoReference("someuser", "mktail"),
                    start_seconds=10.0,
                    duration_seconds=10.0,
                    removed_reason=REMOVED_REPO_REASON,
                ),
            ),
        )
        notes = removed_repo_speaker_notes(plan)
        assert len(notes) == 1
        assert "someuser/mktail" in notes[0]
        assert REMOVED_REPO_REASON in notes[0]

    def test_no_notes_when_none_removed(self):
        plan = EpisodePlan(
            total_duration_seconds=10.0,
            segments=(
                VideoSegment(
                    repo=RepoReference("a", "b"), start_seconds=0.0, duration_seconds=10.0
                ),
            ),
        )
        assert removed_repo_speaker_notes(plan) == []


class TestRemovedReasonSerialization:
    def test_to_yaml_includes_removed_reason(self):
        plan = EpisodePlan(
            total_duration_seconds=10.0,
            segments=(
                VideoSegment(
                    repo=RepoReference("a", "b"),
                    start_seconds=0.0,
                    duration_seconds=10.0,
                    removed_reason=REMOVED_REPO_REASON,
                ),
            ),
        )
        data = yaml.safe_load(plan.to_yaml())
        assert data["segments"][0]["removed_reason"] == REMOVED_REPO_REASON


# --- Realized-audio-metadata (Layer 2) sync planning (#553) ---


class TestPlanFromRealizedMetadata:
    """Tests for plan_from_realized_metadata() — the whisper replacement (#553)."""

    @staticmethod
    def _meta(total_duration_ms: int, topics):
        from podcaster.audio_metadata import RealizedAudioMetadata, TopicRange

        ranges = []
        for mode, repo_url, start_ms, end_ms in topics:
            ranges.append(
                TopicRange(
                    visual_mode=mode,
                    repo_url=repo_url,
                    section_id=None,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    utterance_indices=(),
                )
            )
        return RealizedAudioMetadata(topics=tuple(ranges), total_duration_ms=total_duration_ms)

    def test_repo_topics_map_to_on_screen_windows_within_one_second(self):
        meta = self._meta(
            190_000,
            [
                # Leading article (cold open) starts after a 10s intro lead-in.
                (VisualMode.ARTICLE, None, 10_000, 25_000),
                (VisualMode.REPO, "https://github.com/acme/alpha", 25_350, 70_000),
                (VisualMode.REPO, "https://github.com/acme/beta", 70_350, 130_000),
                (VisualMode.REPO, "https://github.com/acme/gamma", 130_350, 180_000),
            ],
        )
        plan = plan_from_realized_metadata(meta, total_duration_seconds=190.0)

        # Cumulative on-screen start of each segment (composition lays out by
        # duration, not start_seconds) must match each REPO topic start ±1s.
        cursor = 0.0
        windows = {}
        for seg in plan.segments:
            if seg.repo is not None:
                windows[(seg.repo.owner, seg.repo.name)] = cursor
            cursor += seg.duration_seconds
        assert windows[("acme", "alpha")] == pytest.approx(25.35, abs=1.0)
        assert windows[("acme", "beta")] == pytest.approx(70.35, abs=1.0)
        assert windows[("acme", "gamma")] == pytest.approx(130.35, abs=1.0)

    def test_plan_tiles_timeline_gap_free_and_covers_outro_tail(self):
        # total (probed MP3) extends past the last spoken word → last segment
        # must stretch to cover the mixed-outro tail (never black).
        meta = self._meta(
            120_000,
            [
                (VisualMode.ARTICLE, None, 10_000, 30_000),
                (VisualMode.REPO, "https://github.com/acme/alpha", 30_350, 118_000),
            ],
        )
        plan = plan_from_realized_metadata(meta, total_duration_seconds=130.0)
        assert plan.segments[0].start_seconds == 0.0
        cursor = 0.0
        for seg in plan.segments:
            assert abs(seg.start_seconds - cursor) < 1e-6
            cursor += seg.duration_seconds
        assert cursor == pytest.approx(130.0)

    def test_leading_article_topic_carries_weekly_url(self):
        meta = self._meta(
            60_000,
            [
                (VisualMode.ARTICLE, None, 10_000, 30_000),
                (VisualMode.REPO, "https://github.com/acme/alpha", 30_350, 55_000),
            ],
        )
        plan = plan_from_realized_metadata(
            meta,
            total_duration_seconds=60.0,
            weekly_url="https://claracle.com/weekly/2026/w26/",
        )
        assert plan.segments[0].is_generic
        assert plan.segments[0].source_url == "https://claracle.com/weekly/2026/w26/"

    def test_no_topics_falls_back_to_generic_plan(self):
        meta = self._meta(60_000, [])
        plan = plan_from_realized_metadata(
            meta, total_duration_seconds=60.0, weekly_url="https://claracle.com/x/"
        )
        assert len(plan.segments) == 1
        assert plan.segments[0].is_generic
        assert plan.segments[0].source_url == "https://claracle.com/x/"

    def test_non_positive_duration_raises(self):
        meta = self._meta(60_000, [])
        with pytest.raises(ValueError):
            plan_from_realized_metadata(meta, total_duration_seconds=0.0)

    def test_excluded_repo_becomes_generic(self):
        meta = self._meta(
            60_000,
            [
                (VisualMode.REPO, "https://github.com/jmservera/squadscope", 0, 60_000),
            ],
        )
        plan = plan_from_realized_metadata(meta, total_duration_seconds=60.0)
        assert plan.segments[0].repo is None

    def test_repo_first_topic_prepends_weekly_lead_article(self):
        # A script that opens directly on a `## Visual: repo` marker must not drop
        # the weekly page nor show the repo during the intro lead-in (#382/#544).
        meta = self._meta(
            60_000,
            [
                (VisualMode.REPO, "https://github.com/acme/alpha", 12_000, 40_000),
                (VisualMode.REPO, "https://github.com/acme/beta", 40_000, 58_000),
            ],
        )
        plan = plan_from_realized_metadata(
            meta,
            total_duration_seconds=60.0,
            weekly_url="https://claracle.com/weekly/2026/w26/",
        )
        # A synthetic leading article segment covers the bridge before the repo.
        assert plan.segments[0].is_generic
        assert plan.segments[0].start_seconds == 0.0
        assert plan.segments[0].source_url == "https://claracle.com/weekly/2026/w26/"
        # The first repo appears at its measured start, not at 0.
        cursor = 0.0
        windows = {}
        for seg in plan.segments:
            if seg.repo is not None:
                windows[(seg.repo.owner, seg.repo.name)] = cursor
            cursor += seg.duration_seconds
        assert windows[("acme", "alpha")] == pytest.approx(12.0, abs=1.0)
        assert cursor == pytest.approx(60.0)

    def test_repo_first_topic_without_weekly_url_keeps_lead_in_absorption(self):
        # Without a weekly/source URL there is nothing to show, so fall back to
        # the lead-in-absorbing behaviour (first topic starts at 0).
        meta = self._meta(
            60_000,
            [
                (VisualMode.REPO, "https://github.com/acme/alpha", 12_000, 60_000),
            ],
        )
        plan = plan_from_realized_metadata(meta, total_duration_seconds=60.0)
        assert plan.segments[0].start_seconds == 0.0
        assert plan.segments[0].repo is not None

    def test_drops_zero_duration_segments_for_out_of_range_topics(self):
        # A topic starting at/after the probed duration clamps to a 0s window and
        # must be dropped (otherwise ffmpeg gets a ``-t 0`` trim). Remaining
        # segments still tile [0, total] with no gap.
        meta = self._meta(
            60_000,
            [
                (VisualMode.ARTICLE, None, 0, 30_000),
                (VisualMode.REPO, "https://github.com/acme/alpha", 30_000, 60_000),
                # Stale/corrupt topic past the end of the audio → 0s window.
                (VisualMode.REPO, "https://github.com/acme/beta", 90_000, 95_000),
            ],
        )
        plan = plan_from_realized_metadata(meta, total_duration_seconds=60.0)
        assert all(s.duration_seconds > 0 for s in plan.segments)
        assert not any(s.repo is not None and s.repo.name == "beta" for s in plan.segments)
        cursor = 0.0
        for seg in plan.segments:
            assert abs(seg.start_seconds - cursor) < 1e-6
            cursor += seg.duration_seconds
        assert cursor == pytest.approx(60.0)

    def test_all_out_of_range_topics_fall_back_to_generic(self):
        meta = self._meta(
            60_000,
            [
                (VisualMode.REPO, "https://github.com/acme/alpha", 90_000, 95_000),
            ],
        )
        plan = plan_from_realized_metadata(
            meta,
            total_duration_seconds=60.0,
            weekly_url="https://claracle.com/weekly/2026/w26/",
        )
        assert len(plan.segments) == 1
        assert plan.segments[0].is_generic
        assert plan.segments[0].source_url == "https://claracle.com/weekly/2026/w26/"
        assert plan.segments[0].duration_seconds == pytest.approx(60.0)


# --- #558: end-to-end audio-master cue placement (slug naming → on-screen time) ---


class TestAudioMasterCuePlacement:
    """Each repo's on-screen window must start at the spoken-cue time derived
    from the realized clip metadata, even when the dialogue names repos as bare
    ``owner/repo`` slugs (issue #558)."""

    def _config(self):
        from podcaster.config import HostConfig, PodcastConfig

        return PodcastConfig(
            host_a=HostConfig(name="Theo", voice="fable", style=""),
            host_b=HostConfig(name="Vera", voice="alloy", style=""),
        )

    def test_repo_window_tracks_spoken_cue_from_clip_metadata(self):
        from podcaster.audio_metadata import extract_realized_audio_metadata
        from podcaster.script_plan import infer_repo_visual_markers, parse_script_plan

        config = self._config()
        eve = "https://github.com/vercel/eve"
        gym = "https://github.com/openai/gym"
        # Full URLs only in the header; dialogue uses bare slugs (like W26).
        script = (
            "Title: Weekly\n"
            f"Repos featured: {eve} {gym}\n"
            "---\n"
            "Theo: Welcome to the show, here is a quick intro with no repo yet.\n"
            "Vera: A heads-up about the format before we get into anything.\n"
            "Vera: Agent frameworks matter and vercel/eve is the cleanest anchor.\n"
            "Theo: Later we also cover openai/gym for the benchmarking angle.\n"
        )
        marked = infer_repo_visual_markers(script, config)
        plan = parse_script_plan(marked, config)

        # One realized duration per spoken segment; intro turns precede eve.
        durations = [12.0, 10.0, 15.0, 14.0]
        assert len(plan.segments) == len(durations)
        gap = 0.35
        offset = 10.0
        meta = extract_realized_audio_metadata(
            plan,
            durations,
            gap_seconds=gap,
            speech_offset_seconds=offset,
            host_labels=("Theo", "Vera"),
        )

        # Spoken cue for eve = realized start of the first turn that names it.
        eve_utt = next(u for u in meta.utterances if u.repo_url == eve)
        cue_seconds = eve_utt.start_ms / 1000.0
        # Sanity: that cue is the intro lead-in + the two intro turns + gaps,
        # i.e. ~32.7s — early, not pushed to the back of the episode.
        assert cue_seconds == pytest.approx(offset + 12.0 + gap + 10.0 + gap, abs=0.01)

        total = offset + sum(durations) + gap * (len(durations) - 1) + 5.0
        video_plan = plan_from_realized_metadata(
            meta, total_duration_seconds=total, weekly_url="https://claracle.com/weekly/2026/w26/"
        )

        eve_seg = next(s for s in video_plan.segments if s.repo and s.repo.name == "eve")
        gym_seg = next(s for s in video_plan.segments if s.repo and s.repo.name == "gym")
        # The on-screen window starts within a couple seconds of the spoken cue.
        assert eve_seg.start_seconds == pytest.approx(cue_seconds, abs=2.0)
        # Repos transition in discussion order: gym follows eve.
        assert gym_seg.start_seconds > eve_seg.start_seconds
        # The lead-in (article/weekly) covers everything before the first repo.
        first = video_plan.segments[0]
        assert first.repo is None
        assert first.start_seconds == pytest.approx(0.0)
        assert first.duration_seconds == pytest.approx(eve_seg.start_seconds, abs=0.01)
