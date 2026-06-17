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
