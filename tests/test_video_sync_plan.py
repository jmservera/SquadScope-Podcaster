"""Tests for podcaster.video.sync_plan module."""

from __future__ import annotations

import yaml
import pytest

from podcaster.video.sync_plan import (
    RepoReference,
    VideoSegment,
    extract_repo_urls,
    extract_source_url,
    generate_episode_plan,
    generate_episode_plan_timed,
    generate_generic_plan,
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

    def test_generates_generic_plan_on_no_repos(self):
        plan = plan_from_script(SCRIPT_NO_REPOS, total_duration_seconds=60.0)
        assert plan.total_duration_seconds == 60.0
        assert len(plan.segments) == 1
        seg = plan.segments[0]
        assert seg.is_generic
        assert seg.repo is None
        assert seg.start_seconds == 0.0
        assert seg.duration_seconds == pytest.approx(60.0)

    def test_generic_plan_uses_source_url(self):
        plan = plan_from_script(
            SCRIPT_NO_REPOS_WITH_SOURCE, total_duration_seconds=60.0
        )
        seg = plan.segments[0]
        assert seg.is_generic
        assert seg.source_url == "https://claracle.com/weekly/2026/W26/"

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

    def test_generates_generic_plan_on_no_repos(self):
        plan = plan_from_script_timed(SCRIPT_NO_REPOS, 60.0)
        assert len(plan.segments) == 1
        assert plan.segments[0].is_generic
        assert plan.total_duration_seconds == 60.0

    def test_generic_plan_uses_source_url(self):
        plan = plan_from_script_timed(SCRIPT_NO_REPOS_WITH_SOURCE, 60.0)
        assert plan.segments[0].is_generic
        assert (
            plan.segments[0].source_url
            == "https://claracle.com/weekly/2026/W26/"
        )

    def test_ordering_matches_script(self):
        # vscode is mentioned before ruff in SAMPLE_SCRIPT
        plan = plan_from_script_timed(SAMPLE_SCRIPT, 200.0)
        urls = [s.repo.url for s in plan.segments]
        idx_vscode = urls.index("https://github.com/microsoft/vscode")
        idx_ruff = urls.index("https://github.com/astral-sh/ruff")
        assert idx_vscode < idx_ruff


# --- Audio-boundary sync tests (#297) ---


from podcaster.video.sync_plan import (
    AudioCuePoint,
    VisualCue,
    VISUAL_KIND_IMAGE,
    VISUAL_KIND_RECORDING,
    VISUAL_KIND_SCREENSHOT,
    build_audio_cue_points,
    snap_to_audio_boundary,
    snap_episode_plan_to_audio,
    snap_visual_cues,
)
from podcaster.video.sync_plan import EpisodePlan, VideoSegment


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
