"""Parse podcast scripts and generate episode_plan.yaml with timed segments.

Part of the Video Epic (jmservera/SquadScope-Coordinator#23).
Phase 1: fixed-duration segments (total audio ÷ repo count).

This module:
1. Extracts GitHub repository URLs from a podcast script (header + body).
2. Generates an episode plan YAML with timed video segments for each repo.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Sequence

import yaml

logger = logging.getLogger(__name__)

# Matches GitHub repo URLs: https://github.com/owner/repo (with optional trailing path)
_GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9\-_.]+)/([A-Za-z0-9\-_.]+)"
)

# Label used for generic background segments that are not tied to a repo.
GENERIC_SEGMENT_LABEL = "__generic__"


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
    """A timed video segment in the episode plan.

    A segment with ``repo=None`` is a *generic* background segment: it is not
    tied to a GitHub repository.  When ``source_url`` is set, the generic
    segment is recorded by navigating to (and scrolling) that page; otherwise
    it is rendered with the static background animation.
    """

    start_seconds: float
    duration_seconds: float
    repo: RepoReference | None = None
    source_url: str | None = None

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds

    @property
    def is_generic(self) -> bool:
        """True when this segment has no associated repository."""
        return self.repo is None

    @property
    def label(self) -> str:
        """Stable identifier used to match recordings to plan segments."""
        return self.repo.url if self.repo is not None else GENERIC_SEGMENT_LABEL


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
                    "repo_url": seg.repo.url if seg.repo is not None else None,
                    "repo_owner": seg.repo.owner if seg.repo is not None else None,
                    "repo_name": seg.repo.name if seg.repo is not None else None,
                    "generic": seg.is_generic,
                    "source_url": seg.source_url,
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


# Matches the "Source URL:" header line, capturing the URL.
_SOURCE_URL_RE = re.compile(
    r"^\s*Source URL:\s*(\S+)", re.IGNORECASE | re.MULTILINE
)


def extract_source_url(script: str) -> str | None:
    """Extract the ``Source URL:`` value from the script header, if present.

    The podcast script header contains a line such as
    ``Source URL: https://claracle.com/weekly/2026/W26/``.  Returns the URL
    string, or ``None`` when the header has no such line.

    Only the header section (before the first ``---`` separator) is searched,
    and only ``https://`` URLs are accepted to prevent SSRF.
    """
    # Restrict to header section (before first ---)
    header = script.split("---", 1)[0] if "---" in script else script
    match = _SOURCE_URL_RE.search(header)
    if not match:
        return None
    url = match.group(1).strip()
    if not url:
        return None
    # Only allow https URLs to prevent SSRF / local-file navigation
    if not url.startswith("https://"):
        return None
    return url


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


def generate_generic_plan(
    total_duration_seconds: float,
    source_url: str | None = None,
) -> EpisodePlan:
    """Generate a plan with a single generic background segment.

    Used when a script contains no GitHub repository URLs (e.g. news-oriented
    episodes). The resulting segment has ``repo=None`` and spans the full audio
    duration.  When *source_url* is provided, the video pipeline records that
    page (navigate + scroll); otherwise it renders the static background
    animation with text overlays.

    Args:
        total_duration_seconds: Total audio duration in seconds.
        source_url: Optional fallback page URL (e.g. the article's
            ``Source URL:``) to record instead of the static background.

    Returns:
        An EpisodePlan with one generic, full-length segment.

    Raises:
        ValueError: If duration is non-positive.
    """
    if total_duration_seconds <= 0:
        raise ValueError(
            f"Total duration must be positive, got {total_duration_seconds}"
        )

    return EpisodePlan(
        total_duration_seconds=total_duration_seconds,
        segments=(
            VideoSegment(
                repo=None,
                source_url=source_url,
                start_seconds=0.0,
                duration_seconds=total_duration_seconds,
            ),
        ),
    )


def plan_from_script(
    script: str,
    total_duration_seconds: float,
) -> EpisodePlan:
    """End-to-end: parse script → extract repos → generate plan.

    Convenience function that combines extraction and plan generation.

    When the script contains no GitHub repository URLs, a generic background
    plan is generated instead of raising, so the video is still produced.

    Args:
        script: Full podcast script text (header + body).
        total_duration_seconds: Total audio duration in seconds.

    Returns:
        An EpisodePlan ready to serialize to YAML.

    Raises:
        ValueError: If duration is non-positive.
    """
    repos = extract_repo_urls(script)
    if not repos:
        source_url = extract_source_url(script)
        logger.info(
            "No GitHub repository URLs found in script; "
            "generating generic background plan (source_url=%s)",
            source_url,
        )
        return generate_generic_plan(total_duration_seconds, source_url)
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
    if pos < 0 and url.startswith("https://"):
        # extract_repo_urls() also matches http:// mentions, but RepoReference.url
        # always normalizes to https://. Try the http:// variant before giving up.
        pos = script.find("http://" + url[len("https://"):])
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

    When the script contains no GitHub repository URLs, a generic background
    plan is generated instead of raising, so the video is still produced.

    Args:
        script: Full podcast script text (header + body).
        total_duration_seconds: Total audio duration in seconds.
        min_segment_seconds: Minimum segment duration. Default 5.0 s.

    Returns:
        EpisodePlan with timing matching script mention positions.

    Raises:
        ValueError: If duration is non-positive.
    """
    repos = extract_repo_urls(script)
    if not repos:
        source_url = extract_source_url(script)
        logger.info(
            "No GitHub repository URLs found in script; "
            "generating generic background plan (source_url=%s)",
            source_url,
        )
        return generate_generic_plan(total_duration_seconds, source_url)
    return generate_episode_plan_timed(
        script, repos, total_duration_seconds, min_segment_seconds
    )


# --- Audio-boundary sync utilities (#297) ---

# Available timing granularity: TTS segment (VoiceTurn) level.
# Azure OpenAI TTS returns raw audio bytes with no word/sentence timestamps.
# Natural visual transition points are therefore segment boundaries (turn starts/ends)
# and gap midpoints between consecutive TTS turns.

_CUE_TURN_START = "turn_start"
_CUE_TURN_END = "turn_end"
_CUE_GAP_MIDPOINT = "gap_midpoint"

# Visual element kinds
VISUAL_KIND_RECORDING = "recording"
VISUAL_KIND_IMAGE = "image"
VISUAL_KIND_SCREENSHOT = "screenshot"


@dataclass(frozen=True)
class AudioCuePoint:
    """A natural audio boundary suitable for visual cue alignment.

    These are derived from the TTS segment (VoiceTurn) timeline and represent
    the best available points at which visual transitions feel natural — they
    coincide with speaker-turn boundaries and the silence gaps between them.

    Attributes:
        time_seconds: Absolute time in the assembled audio.
        kind: One of ``"turn_start"``, ``"turn_end"``, ``"gap_midpoint"``.
    """

    time_seconds: float
    kind: str


@dataclass(frozen=True)
class VisualCue:
    """A visual element with an intended display time and source kind.

    Used as input to :func:`snap_visual_cues`.  The ``time_seconds`` field
    is the *desired* display time before snapping; the returned list will have
    adjusted ``time_seconds`` values.

    Attributes:
        time_seconds: Desired display start time (seconds into assembled audio).
        kind: ``"recording"``, ``"image"``, or ``"screenshot"``.
        label: Human-readable identifier (repo URL, file name, etc.).
    """

    time_seconds: float
    kind: str
    label: str = ""


def build_audio_cue_points(
    segment_starts: list[float],
    segment_durations: list[float],
    gap_seconds: float = 0.35,
) -> list[AudioCuePoint]:
    """Build cue points from a TTS segment timeline.

    Produces one cue per turn start, one per turn end, and one per gap
    midpoint (where applicable).  The resulting list is sorted by
    ``time_seconds`` and deduplicated to within 10 ms.

    Args:
        segment_starts: Absolute start times of each TTS turn (seconds).
            Obtain via :func:`~podcaster.audio.compute_segment_timeline`.
        segment_durations: Duration (seconds) of each TTS turn, same length
            as *segment_starts*.
        gap_seconds: Gap between consecutive turns in the assembled audio.
            Must match the value used when calling
            :func:`~podcaster.audio.compute_segment_timeline`.

    Returns:
        Sorted, deduplicated list of :class:`AudioCuePoint` objects.

    Raises:
        ValueError: If *segment_starts* and *segment_durations* lengths differ,
            or if any duration is negative.
    """
    if len(segment_starts) != len(segment_durations):
        raise ValueError(
            f"segment_starts length ({len(segment_starts)}) must equal "
            f"segment_durations length ({len(segment_durations)})"
        )
    for dur in segment_durations:
        if dur < 0:
            raise ValueError(f"All segment durations must be non-negative, got {dur}")

    cues: list[AudioCuePoint] = []
    n = len(segment_starts)
    for i, (start, dur) in enumerate(zip(segment_starts, segment_durations)):
        cues.append(AudioCuePoint(time_seconds=start, kind=_CUE_TURN_START))
        end = start + dur
        cues.append(AudioCuePoint(time_seconds=end, kind=_CUE_TURN_END))
        # Gap midpoint between this turn and the next
        if gap_seconds > 0 and i < n - 1:
            next_start = segment_starts[i + 1]
            midpoint = end + (next_start - end) / 2.0
            cues.append(AudioCuePoint(time_seconds=midpoint, kind=_CUE_GAP_MIDPOINT))

    # Sort and deduplicate within 10 ms
    cues.sort(key=lambda c: c.time_seconds)
    deduped: list[AudioCuePoint] = []
    for cue in cues:
        if deduped and abs(cue.time_seconds - deduped[-1].time_seconds) < 0.01:
            continue
        deduped.append(cue)
    return deduped


def snap_to_audio_boundary(
    time_seconds: float,
    cue_points: list[AudioCuePoint],
    tolerance_seconds: float = 0.5,
) -> float:
    """Snap a visual cue time to the nearest audio boundary within tolerance.

    Finds the closest :class:`AudioCuePoint` to *time_seconds*.  If it is
    within *tolerance_seconds*, returns its time; otherwise returns
    *time_seconds* unchanged.  This prevents both premature visual jumps (the
    cue would shift earlier by at most *tolerance_seconds*) and lingering on
    old content (the cue shifts later by at most *tolerance_seconds*).

    Args:
        time_seconds: The desired visual cue time to snap.
        cue_points: Natural audio boundaries from :func:`build_audio_cue_points`.
        tolerance_seconds: Maximum snap distance. Default 0.5 s.

    Returns:
        Snapped time (seconds).  Equals *time_seconds* if no boundary is close
        enough or *cue_points* is empty.
    """
    if not cue_points:
        return time_seconds

    nearest = min(cue_points, key=lambda c: abs(c.time_seconds - time_seconds))
    if abs(nearest.time_seconds - time_seconds) <= tolerance_seconds:
        return nearest.time_seconds
    return time_seconds


def snap_episode_plan_to_audio(
    plan: EpisodePlan,
    cue_points: list[AudioCuePoint],
    tolerance_seconds: float = 0.5,
) -> EpisodePlan:
    """Snap all segment start times in an EpisodePlan to natural audio boundaries.

    Applies :func:`snap_to_audio_boundary` to every segment's ``start_seconds``.
    Segment durations are adjusted to preserve the relative order and gap
    between consecutive segments; the last segment is extended/trimmed to fill
    the plan's ``total_duration_seconds``.

    The result enforces:

    * **No premature jump**: a visual won't appear more than *tolerance_seconds*
      before the audio cue.
    * **No linger**: a visual won't stay on screen more than *tolerance_seconds*
      after the audio has moved on.

    Args:
        plan: Episode plan whose segment starts will be snapped.
        cue_points: Audio boundaries from :func:`build_audio_cue_points`.
        tolerance_seconds: Maximum snap distance per segment. Default 0.5 s.

    Returns:
        New :class:`EpisodePlan` with snapped segment timings.
    """
    if not plan.segments:
        return plan

    snapped_starts: list[float] = []
    for seg in plan.segments:
        snapped = snap_to_audio_boundary(
            seg.start_seconds, cue_points, tolerance_seconds
        )
        # Enforce monotonic order after snapping
        if snapped_starts and snapped <= snapped_starts[-1]:
            snapped = snapped_starts[-1] + 0.0  # keep previous; will recalc dur below
        snapped_starts.append(snapped)

    new_segments: list[VideoSegment] = []
    n = len(plan.segments)
    for i, (seg, new_start) in enumerate(zip(plan.segments, snapped_starts)):
        if i < n - 1:
            new_dur = snapped_starts[i + 1] - new_start
        else:
            new_dur = plan.total_duration_seconds - new_start
        # Guard against floating-point negatives
        new_dur = max(new_dur, 0.0)
        new_segments.append(
            VideoSegment(
                repo=seg.repo,
                source_url=seg.source_url,
                start_seconds=new_start,
                duration_seconds=new_dur,
            )
        )

    return EpisodePlan(
        total_duration_seconds=plan.total_duration_seconds,
        segments=tuple(new_segments),
    )


def snap_visual_cues(
    cues: list[VisualCue],
    cue_points: list[AudioCuePoint],
    tolerance_seconds: float = 0.5,
) -> list[VisualCue]:
    """Snap a list of visual element cues to natural audio boundaries.

    Generalisation of :func:`snap_episode_plan_to_audio` that works with any
    visual element kind (recording, image, screenshot).  Each cue's
    ``time_seconds`` is independently snapped; ordering is preserved but
    otherwise unchanged.

    Args:
        cues: Visual elements to snap. Any ``kind`` is accepted.
        cue_points: Audio boundaries from :func:`build_audio_cue_points`.
        tolerance_seconds: Maximum snap distance per cue. Default 0.5 s.

    Returns:
        New list of :class:`VisualCue` objects with snapped ``time_seconds``.
    """
    return [
        VisualCue(
            time_seconds=snap_to_audio_boundary(
                cue.time_seconds, cue_points, tolerance_seconds
            ),
            kind=cue.kind,
            label=cue.label,
        )
        for cue in cues
    ]
