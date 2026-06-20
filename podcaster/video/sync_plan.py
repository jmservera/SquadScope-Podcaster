"""Parse podcast scripts and generate episode_plan.yaml with timed segments.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Phase 1: fixed-duration segments (total audio ÷ repo count).

This module:
1. Extracts GitHub repository URLs from a podcast script (header + body).
2. Generates an episode plan YAML with timed video segments for each repo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

import yaml

# Matches GitHub repo URLs: https://github.com/owner/repo (with optional trailing path)
_GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9\-_.]+)/([A-Za-z0-9\-_.]+)"
)


@dataclass(frozen=True)
class RepoReference:
    """A GitHub repository referenced in the script."""

    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RepoReference):
            return NotImplemented
        return self.owner == other.owner and self.name == other.name

    def __hash__(self) -> int:
        return hash((self.owner, self.name))


@dataclass(frozen=True)
class VideoSegment:
    """A timed video segment in the episode plan."""

    repo: RepoReference
    start_seconds: float
    duration_seconds: float

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds


@dataclass(frozen=True)
class EpisodePlan:
    """Complete episode plan for video generation."""

    total_duration_seconds: float
    segments: tuple[VideoSegment, ...] = field(default_factory=tuple)

    def to_yaml(self) -> str:
        """Serialize the plan to YAML."""
        data = {
            "total_duration_seconds": self.total_duration_seconds,
            "segments": [
                {
                    "repo_url": seg.repo.url,
                    "repo_owner": seg.repo.owner,
                    "repo_name": seg.repo.name,
                    "start_seconds": round(seg.start_seconds, 3),
                    "duration_seconds": round(seg.duration_seconds, 3),
                    "end_seconds": round(seg.end_seconds, 3),
                }
                for seg in self.segments
            ],
        }
        return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


def extract_repo_urls(script: str) -> list[RepoReference]:
    """Extract unique GitHub repo URLs from a podcast script, preserving order.

    Scans both the header and dialogue body for GitHub repository URLs.
    Deduplicates while preserving first-occurrence order.
    """
    seen: set[RepoReference] = set()
    repos: list[RepoReference] = []

    for match in _GITHUB_REPO_RE.finditer(script):
        owner = match.group(1)
        name = match.group(2)
        # Strip common suffixes that aren't part of repo names
        name = name.rstrip(".")
        if name.endswith(".git"):
            name = name[:-4]
        ref = RepoReference(owner=owner, name=name)
        if ref not in seen:
            seen.add(ref)
            repos.append(ref)

    return repos


def generate_episode_plan(
    repos: Sequence[RepoReference],
    total_duration_seconds: float,
) -> EpisodePlan:
    """Generate an episode plan with fixed-duration segments.

    Phase 1 strategy: divides total audio duration equally among repos.

    Args:
        repos: Ordered list of repo references for the episode.
        total_duration_seconds: Total audio duration in seconds.

    Returns:
        An EpisodePlan with evenly-distributed timed segments.

    Raises:
        ValueError: If repos is empty or duration is non-positive.
    """
    if not repos:
        raise ValueError("No repos provided for episode plan generation")
    if total_duration_seconds <= 0:
        raise ValueError(
            f"Total duration must be positive, got {total_duration_seconds}"
        )

    segment_duration = total_duration_seconds / len(repos)
    segments: list[VideoSegment] = []

    for i, repo in enumerate(repos):
        segments.append(
            VideoSegment(
                repo=repo,
                start_seconds=i * segment_duration,
                duration_seconds=segment_duration,
            )
        )

    return EpisodePlan(
        total_duration_seconds=total_duration_seconds,
        segments=tuple(segments),
    )


def plan_from_script(
    script: str,
    total_duration_seconds: float,
) -> EpisodePlan:
    """End-to-end: parse script → extract repos → generate plan.

    Convenience function that combines extraction and plan generation.

    Args:
        script: Full podcast script text (header + body).
        total_duration_seconds: Total audio duration in seconds.

    Returns:
        An EpisodePlan ready to serialize to YAML.

    Raises:
        ValueError: If no repos found or duration is non-positive.
    """
    repos = extract_repo_urls(script)
    if not repos:
        raise ValueError("No GitHub repository URLs found in script")
    return generate_episode_plan(repos, total_duration_seconds)


# --- Script-position-aware sync planning (#296) ---


def _script_position(script: str, url: str) -> float:
    """Return the fractional character position (0.0–1.0) of the first mention
    of *url* in *script*.  Returns 1.0 if the URL is not found (placing the
    segment at the end) or 0.0 if the script is empty.
    """
    if not script:
        return 0.0
    pos = script.find(url)
    if pos < 0:
        return 1.0
    return pos / len(script)


def sort_repos_by_mention(
    script: str,
    repos: Sequence[RepoReference],
) -> list[RepoReference]:
    """Return *repos* sorted by first-mention position in *script*.

    Repos whose URL is not found in the script are placed at the end (position
    1.0).  Stable sort: repos at equal positions preserve their input order.

    Args:
        script: Full podcast script text.
        repos: Repo references to sort.

    Returns:
        New list sorted by ascending script position.
    """
    return sorted(repos, key=lambda r: _script_position(script, r.url))


def generate_episode_plan_timed(
    script: str,
    repos: Sequence[RepoReference],
    total_duration_seconds: float,
    min_segment_seconds: float = 5.0,
) -> EpisodePlan:
    """Generate a plan where segment timing mirrors where each repo is mentioned.

    Unlike :func:`generate_episode_plan` (equal split), this function places
    each segment at a timestamp proportional to its first mention in *script*.
    Useful for synchronising screen recordings with the audio track so each
    repo appears on screen exactly when the hosts discuss it.

    When multiple repos are mentioned close together, the minimum-segment
    floor (``min_segment_seconds``) separates them.  The floor is automatically
    reduced if the total available time is smaller than ``n_repos ×
    min_segment_seconds``.

    Args:
        script: Full podcast script text used to derive timing positions.
        repos: Repo references.  Ordering in the output follows script mention
            order (not the input order).
        total_duration_seconds: Total audio duration in seconds.
        min_segment_seconds: Minimum duration for any single segment. Default 5.0 s.

    Returns:
        EpisodePlan with segment timing derived from script text positions.

    Raises:
        ValueError: If *repos* is empty or *total_duration_seconds* is non-positive.
    """
    if not repos:
        raise ValueError("No repos provided for episode plan generation")
    if total_duration_seconds <= 0:
        raise ValueError(
            f"Total duration must be positive, got {total_duration_seconds}"
        )

    n = len(repos)
    # Cap the minimum so all n segments always fit within total_duration
    effective_min = min(min_segment_seconds, total_duration_seconds / n)

    ordered = sort_repos_by_mention(script, repos)

    start_times: list[float] = []
    for i, repo in enumerate(ordered):
        pos = _script_position(script, repo.url)
        # Upper bound: leave room for this segment and all subsequent ones
        max_start = total_duration_seconds - (n - i) * effective_min
        start = max(0.0, min(pos * total_duration_seconds, max_start))
        # Enforce strictly-increasing order with minimum gap
        if i > 0:
            start = max(start, start_times[i - 1] + effective_min)
        start_times.append(start)

    segments: list[VideoSegment] = []
    for i, (repo, start) in enumerate(zip(ordered, start_times)):
        if i < n - 1:
            duration = start_times[i + 1] - start
        else:
            duration = total_duration_seconds - start
        # Guard against floating-point drift producing tiny negatives
        duration = max(duration, effective_min)
        segments.append(
            VideoSegment(repo=repo, start_seconds=start, duration_seconds=duration)
        )

    return EpisodePlan(
        total_duration_seconds=total_duration_seconds,
        segments=tuple(segments),
    )


def plan_from_script_timed(
    script: str,
    total_duration_seconds: float,
    min_segment_seconds: float = 5.0,
) -> EpisodePlan:
    """End-to-end: parse script → extract repos → generate timing-aware plan.

    Like :func:`plan_from_script` but derives per-segment timing from each
    repo's first mention position in *script* rather than distributing time
    equally.

    Args:
        script: Full podcast script text (header + body).
        total_duration_seconds: Total audio duration in seconds.
        min_segment_seconds: Minimum segment duration. Default 5.0 s.

    Returns:
        EpisodePlan with timing matching script mention positions.

    Raises:
        ValueError: If no repos found or duration is non-positive.
    """
    repos = extract_repo_urls(script)
    if not repos:
        raise ValueError("No GitHub repository URLs found in script")
    return generate_episode_plan_timed(
        script, repos, total_duration_seconds, min_segment_seconds
    )
