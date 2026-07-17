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
from typing import TYPE_CHECKING, Sequence
from urllib.parse import urlparse

import requests
import yaml

if TYPE_CHECKING:
    from podcaster.audio_metadata import RealizedAudioMetadata

logger = logging.getLogger(__name__)

# Timeout (seconds) for fetching the source article page when the script
# contains no GitHub repo URLs.
_ARTICLE_FETCH_TIMEOUT = 10

# Host allowlist for article fetching (SSRF guard). Only the public Claracle
# article pages may be fetched; any other host returns an empty repo list.
_ARTICLE_FETCH_ALLOWED_HOSTS = frozenset({"claracle.com", "www.claracle.com"})

# Matches GitHub repo URLs: https://github.com/owner/repo (with optional trailing path)
_GITHUB_REPO_RE = re.compile(r"https?://github\.com/([A-Za-z0-9\-_.]+)/([A-Za-z0-9\-_.]+)")

# Label used for generic background segments that are not tied to a repo.
GENERIC_SEGMENT_LABEL = "__generic__"

# Repositories that must never appear in generated videos. These are the
# project's own repos: navigating to them would put SquadScope's own pages in
# the episode content (issue #353). Compared case-insensitively as
# ``(owner, name)`` tuples.
_EXCLUDED_REPOS = frozenset(
    {
        ("jmservera", "squadscope"),
    }
)


def _is_excluded_repo(repo: "RepoReference") -> bool:
    """True when *repo* is in the project's own-repo exclusion list."""
    return (repo.owner.lower(), repo.name.lower()) in _EXCLUDED_REPOS


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
    # Set when a pre-flight check found the repo's GitHub page is gone (HTTP
    # 404) — e.g. a polymarket/spam bot repo that GitHub removed (issue #394).
    # Carries the human-readable reason shown to speakers and on the video card.
    # The recorder skips navigation for these segments and renders a clean
    # "Repo removed" card instead of wasting time on a dead URL.
    removed_reason: str | None = None

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds

    @property
    def is_generic(self) -> bool:
        """True when this segment has no associated repository."""
        return self.repo is None

    @property
    def is_removed(self) -> bool:
        """True when a pre-flight check flagged the repo as removed (issue #394)."""
        return self.removed_reason is not None

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
                    "removed_reason": seg.removed_reason,
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
_SOURCE_URL_RE = re.compile(r"^\s*Source URL:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


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


def fetch_repos_from_article(url: str) -> list[RepoReference]:
    """Fetch *url* and extract GitHub repo references from its HTML.

    The podcast script never contains GitHub repo URLs, but the source article
    page does.  This helper navigates to the article page and scrapes any
    ``https://github.com/owner/repo`` links from the raw HTML.

    The Source URL recorded in the script header uses an uppercase ISO week
    segment (e.g. ``/W26/``) while the live claracle.com pages are served from a
    lowercase path (``/w26/``).  We therefore try the lowercase variant first
    and fall back to the original casing.

    Network and HTTP errors are swallowed: on any failure the function returns
    an empty list so the caller can fall back to a generic plan gracefully.

    Args:
        url: The article ``Source URL:`` (https only).

    Returns:
        Ordered, de-duplicated list of repo references found on the page, or an
        empty list when the page cannot be fetched or contains no repos.
    """
    if not url or not url.startswith("https://"):
        return []

    # SSRF guard: only fetch from the known public article hosts. Any other
    # host (including internal/metadata endpoints) yields an empty list.
    host = (urlparse(url).hostname or "").lower()
    if host not in _ARTICLE_FETCH_ALLOWED_HOSTS:
        logger.warning(
            "Refusing to fetch article page from disallowed host %r (url=%s)",
            host,
            url,
        )
        return []

    # Try lowercase first (live pages use lowercase week segments), then the
    # original casing.  Preserve order and skip duplicates.
    candidates: list[str] = []
    lowered = url.lower()
    for candidate in (lowered, url):
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        try:
            response = requests.get(candidate, timeout=_ARTICLE_FETCH_TIMEOUT)
        except requests.RequestException as exc:  # noqa: BLE001 — best-effort
            logger.warning("Failed to fetch article page %s: %s", candidate, exc)
            continue
        if response.status_code != 200:
            logger.info("Article page %s returned HTTP %s", candidate, response.status_code)
            continue
        repos = extract_repo_urls(response.text)
        repos = [r for r in repos if not _is_excluded_repo(r)]
        if repos:
            logger.info(
                "Extracted %d GitHub repo(s) from article page %s",
                len(repos),
                candidate,
            )
            return repos
        logger.info("No GitHub repos found on article page %s", candidate)

    return []


def generate_episode_plan(
    repos: Sequence[RepoReference],
    total_duration_seconds: float,
) -> EpisodePlan:
    """Generate an episode plan with fixed-duration segments.

    Phase 1 strategy: divides total audio duration equally among repos.
    There is no segment cap — composition uses pairwise xfade passes
    (see ``video_compose._compose_pairwise``) with constant memory, so any
    number of segments can be composed without OOMing the ACA container.

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
        raise ValueError(f"Total duration must be positive, got {total_duration_seconds}")

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
        raise ValueError(f"Total duration must be positive, got {total_duration_seconds}")

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
        if source_url:
            repos = fetch_repos_from_article(source_url)
        if repos:
            logger.info(
                "Using %d GitHub repo(s) fetched from source article for plan",
                len(repos),
            )
            return generate_episode_plan(repos, total_duration_seconds)
        logger.info(
            "No GitHub repository URLs found in script or source article; "
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
        pos = script.find("http://" + url[len("https://") :])
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
        raise ValueError(f"Total duration must be positive, got {total_duration_seconds}")

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
        segments.append(VideoSegment(repo=repo, start_seconds=start, duration_seconds=duration))

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
        if source_url:
            repos = fetch_repos_from_article(source_url)
        if repos:
            logger.info(
                "Using %d GitHub repo(s) fetched from source article for plan; "
                "using equal-split timing (repos are absent from the script, so "
                "mention-based positions are unavailable)",
                len(repos),
            )
            # Repos came from the article page, not the script. Their URLs do not
            # appear in the script text, so _script_position() would return 1.0
            # for every repo and clump all segments at the end. Fall back to an
            # equal-split plan instead of mention-based timing.
            return generate_episode_plan(repos, total_duration_seconds)
        logger.info(
            "No GitHub repository URLs found in script or source article; "
            "generating generic background plan (source_url=%s)",
            source_url,
        )
        return generate_generic_plan(total_duration_seconds, source_url)
    return generate_episode_plan_timed(script, repos, total_duration_seconds, min_segment_seconds)


# --- Removed/bot repo pre-flight detection (issue #394) ---

# Speaker note + on-screen card text shown for a repo whose GitHub page returns
# HTTP 404 during the pre-flight check.  Some repos (e.g. ``mktail``, a
# polymarket bot) are taken down by GitHub for spam/abuse; detecting this before
# recording avoids wasting recording time on a dead URL and lets the hosts
# comment on why the project is gone.
REMOVED_REPO_REASON = "This repo was removed from GitHub"

# Timeout (seconds) for the per-repo HEAD pre-flight check.
_REMOVED_CHECK_TIMEOUT = 5.0


def check_repo_removed(url: str, timeout: float = _REMOVED_CHECK_TIMEOUT) -> bool:
    """Return True when *url* looks like a removed GitHub repo (HTTP 404).

    Issues a lightweight ``HEAD`` request and treats **only** a 404 as
    "removed".  Any other status (200/3xx redirects, 429, 5xx, …) or a network
    error returns ``False`` so a healthy, rate-limited, or merely slow repo is
    never mistaken for a removed one — the recorder still attempts it and has
    its own recovery flow (issues #378, #386).

    Note: GitHub also returns 404 for *private* repos to unauthenticated
    callers.  In this pipeline all referenced repos are public open-source
    projects, so a 404 reliably means the repo was taken down/renamed.
    """
    if not url:
        return False
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:  # noqa: BLE001 — best-effort probe
        logger.warning("Removed-repo pre-check failed for %s: %s", url, exc)
        return False
    return resp.status_code == 404


def annotate_removed_repos(
    plan: EpisodePlan,
    *,
    checker=None,
    timeout: float = _REMOVED_CHECK_TIMEOUT,
) -> EpisodePlan:
    """Return a copy of *plan* with removed repos annotated (issue #394).

    Each repo segment's URL is pre-checked (HEAD) via *checker*.  When a repo
    is found removed (HTTP 404) the corresponding segment gets
    ``removed_reason=REMOVED_REPO_REASON`` so the recorder skips navigation and
    shows a "Repo removed" card instead of attempting a doomed recording.

    Generic segments, already-annotated segments, and excluded repos are left
    untouched.  Segment timing is preserved exactly so the audio stays in sync.

    Args:
        plan: The episode plan to annotate.
        checker: Callable ``(url, timeout) -> bool`` returning True when the
            repo is removed.  Defaults to :func:`check_repo_removed` (resolved
            at call time so it can be monkeypatched); override in tests to avoid
            network calls.
        timeout: Per-repo HEAD timeout passed to *checker*.

    Returns:
        A new :class:`EpisodePlan`; the original is not mutated.
    """
    if checker is None:
        checker = check_repo_removed
    new_segments: list[VideoSegment] = []
    removed_count = 0
    for seg in plan.segments:
        if seg.repo is None or seg.removed_reason is not None:
            new_segments.append(seg)
            continue
        try:
            removed = checker(seg.repo.url, timeout=timeout)
        except Exception:  # noqa: BLE001 — never let a probe abort planning
            logger.exception("Removed-repo check raised for %s; assuming present", seg.repo.url)
            removed = False
        if removed:
            removed_count += 1
            logger.info(
                "Repo %s pre-flight 404 — marking segment as removed (issue #394)",
                seg.repo.url,
            )
            new_segments.append(
                VideoSegment(
                    start_seconds=seg.start_seconds,
                    duration_seconds=seg.duration_seconds,
                    repo=seg.repo,
                    source_url=seg.source_url,
                    removed_reason=REMOVED_REPO_REASON,
                )
            )
        else:
            new_segments.append(seg)

    if removed_count:
        logger.info(
            "Annotated %d/%d repo segment(s) as removed before recording",
            removed_count,
            sum(1 for s in plan.segments if s.repo is not None),
        )
    return EpisodePlan(
        total_duration_seconds=plan.total_duration_seconds,
        segments=tuple(new_segments),
    )


def speaker_note_for_removed_repo(repo: RepoReference, reason: str) -> str:
    """Build a one-line speaker cue for a removed repo (issue #394)."""
    return (
        f"[{repo.owner}/{repo.name}] {reason} — likely flagged as a "
        f"bot/spam project. Briefly note that it's gone and move on."
    )


def removed_repo_speaker_notes(plan: EpisodePlan) -> list[str]:
    """Return ordered speaker cues for every removed repo segment (issue #394).

    Empty when no segment was annotated by :func:`annotate_removed_repos`.
    These notes are surfaced to the hosts so they can comment on why a project
    was taken down rather than silently skipping it.
    """
    notes: list[str] = []
    for seg in plan.segments:
        if seg.removed_reason is not None and seg.repo is not None:
            notes.append(speaker_note_for_removed_repo(seg.repo, seg.removed_reason))
    return notes


# --- Realized-audio-metadata (Layer 2) sync planning (#553) ---


def _repo_ref_from_url(url: str | None) -> RepoReference | None:
    """Build a :class:`RepoReference` from a GitHub repo URL, or ``None``."""
    if not url:
        return None
    match = _GITHUB_REPO_RE.search(url)
    if match is None:
        return None
    name = match.group(2).rstrip(".")
    if name.endswith(".git"):
        name = name[:-4]
    return RepoReference(owner=match.group(1), name=name)


# Segments shorter than this are dropped from a metadata-backed plan: they would
# otherwise feed ffmpeg a near-zero ``-t`` trim (empty/failed output). The value
# is well below any real on-screen window yet absorbs float/rounding drift.
_MIN_SEGMENT_DURATION_SECONDS = 0.05


def plan_from_realized_metadata(
    metadata: "RealizedAudioMetadata",
    total_duration_seconds: float,
    *,
    weekly_url: str | None = None,
    source_url: str | None = None,
) -> EpisodePlan:
    """Build an :class:`EpisodePlan` from Layer 2 realized audio metadata (#553).

    This is the deterministic replacement for whisper forced alignment. The
    metadata's :class:`~podcaster.audio_metadata.TopicRange` list already encodes
    — in milliseconds and from the *measured* TTS clip durations — when each repo
    is discussed, the article/weekly rundown is on screen, and where
    intermissions fall. Topic boundaries fall exactly at the script's
    ``## Visual:`` markers, and the metadata already bakes in the inter-segment
    ``gap_seconds`` and the intro-music ``speech_offset`` so the timings agree
    with the final mixed audio.

    The resulting segments **tile the timeline gap-free** because composition lays
    segments out by list order + ``duration_seconds`` (not ``start_seconds``):

    * the first segment absorbs the pre-speech lead-in (it starts at 0), so the
      cumulative start of every later segment equals its topic's ``start_ms`` —
      each repo appears on screen when the hosts name it;
    * the last segment is extended to ``total_duration_seconds`` so a mixed outro
      that runs past the final spoken word is still covered (never black);
    * ``repo`` topics become repo segments; ``article`` topics show the weekly
      page (``weekly_url``) / source article; ``intermission`` topics render the
      static background.

    Falls back to a single generic, full-length segment when the metadata has no
    topics.

    Args:
        metadata: Layer 2 :class:`~podcaster.audio_metadata.RealizedAudioMetadata`.
        total_duration_seconds: Probed final-MP3 duration in seconds.
        weekly_url: claracle.com weekly page URL for ``article`` topics (#382).
        source_url: Optional article ``Source URL:`` fallback for ``article``
            topics when no ``weekly_url`` is available.

    Returns:
        An :class:`EpisodePlan` whose segments cover ``[0, total_duration_seconds]``.

    Raises:
        ValueError: If ``total_duration_seconds`` is non-positive.
    """
    from podcaster.script_plan import VisualMode

    if total_duration_seconds <= 0:
        raise ValueError(f"Total duration must be positive, got {total_duration_seconds}")

    topics = list(metadata.topics)
    if not topics:
        logger.info("realized metadata has no topics; using generic full-length plan")
        return generate_generic_plan(total_duration_seconds, weekly_url or source_url)

    # Normally the script opens with host banter under the default
    # ``VisualMode.ARTICLE``, so the first topic is the article/weekly view that
    # naturally absorbs the pre-speech lead-in (see below). If a script instead
    # opens directly on a ``## Visual: repo`` (or intermission) marker, forcing
    # that topic to start at 0 would show the repo during the intro and drop the
    # weekly page entirely. Guard that by synthesising a leading article segment
    # spanning the bridge before the first topic, so the weekly page is never
    # lost and the first real topic keeps its measured start (issues #382/#544).
    weekly_source = weekly_url or source_url
    lead_article: VideoSegment | None = None
    if topics[0].visual_mode is not VisualMode.ARTICLE and weekly_source:
        first_start = max(0.0, min(topics[0].start_ms / 1000.0, total_duration_seconds))
        if first_start > 0.0:
            lead_article = VideoSegment(
                start_seconds=0.0,
                duration_seconds=first_start,
                repo=None,
                source_url=weekly_source,
            )

    n = len(topics)
    # Boundary start of each segment. When no synthetic lead article is
    # prepended, the first segment absorbs the pre-speech lead-in (starts at 0);
    # every later segment starts at its topic's measured start. Clamp into range
    # and keep the sequence non-decreasing so durations never go negative under
    # floating-point/rounding drift.
    starts: list[float] = []
    for i, topic in enumerate(topics):
        raw = topic.start_ms / 1000.0 if (i > 0 or lead_article is not None) else 0.0
        bounded = max(0.0, min(raw, total_duration_seconds))
        if starts:
            bounded = max(bounded, starts[-1])
        starts.append(bounded)

    segments: list[VideoSegment] = []
    if lead_article is not None:
        segments.append(lead_article)
    for i, topic in enumerate(topics):
        start = starts[i]
        end = starts[i + 1] if i + 1 < n else total_duration_seconds
        duration = max(0.0, end - start)
        repo = _repo_ref_from_url(topic.repo_url) if topic.visual_mode is VisualMode.REPO else None
        if repo is not None and _is_excluded_repo(repo):
            repo = None
        seg_source: str | None = None
        if repo is None and topic.visual_mode is VisualMode.ARTICLE:
            seg_source = weekly_source
        segments.append(
            VideoSegment(
                start_seconds=start,
                duration_seconds=duration,
                repo=repo,
                source_url=seg_source,
            )
        )

    # Drop zero-/near-zero-duration segments. A topic whose measured start lands
    # at or past the probed audio duration (e.g. stale/corrupt metadata) clamps
    # to a 0s window, which downstream would feed ffmpeg a ``-t 0`` trim and fail
    # or yield empty output. Dropping them keeps the timeline tiling intact (each
    # contributes 0 to the cumulative layout) and is monotonic-safe. If nothing
    # survives, fall back to a generic full-length plan.
    segments = [s for s in segments if s.duration_seconds > _MIN_SEGMENT_DURATION_SECONDS]
    if not segments:
        logger.warning(
            "realized metadata produced no positive-duration segments "
            "(total=%.3fs); using generic full-length plan",
            total_duration_seconds,
        )
        return generate_generic_plan(total_duration_seconds, weekly_source)

    repo_count = sum(1 for s in segments if s.repo is not None)
    logger.info(
        "metadata-backed plan: %d segment(s), %d repo window(s) from %d topic(s)",
        len(segments),
        repo_count,
        n,
    )
    return EpisodePlan(
        total_duration_seconds=total_duration_seconds,
        segments=tuple(segments),
    )


# --- claracle.com weekly page as the first content segment (issue #382) ---

# The weekly page is shown after the intro, before the hosts discuss any repo.
# It spans the entire bridge before the first repo mention (only a minimum
# floor), so the plan keeps tiling the audio timeline with no gap (issue #544).
WEEKLY_SEGMENT_MIN_SECONDS = 15.0

# Extracts the ISO year and week from a job_id such as
# ``podcast-2026-W26-de5f4e6e0435`` (case-insensitive ``W``).
_JOB_ID_WEEK_RE = re.compile(r"(\d{4})-[Ww](\d{1,2})\b")


def weekly_url_from_job_id(job_id: str) -> str | None:
    """Derive the claracle.com weekly page URL from a *job_id* (issue #382).

    ``podcast-2026-W26-de5f4e6e0435`` -> ``https://claracle.com/weekly/2026/w26/``.

    The live claracle.com weekly pages use a lowercase, zero-padded week segment
    (``/w26/``).  Returns ``None`` when *job_id* contains no ``YYYY-Www`` token.
    """
    if not job_id:
        return None
    match = _JOB_ID_WEEK_RE.search(job_id)
    if not match:
        return None
    year = match.group(1)
    week = int(match.group(2))
    return f"https://claracle.com/weekly/{year}/w{week:02d}/"


def prepend_weekly_segment(
    plan: EpisodePlan,
    job_id: str,
    *,
    use_live_source: bool = True,
) -> EpisodePlan:
    """Insert the claracle.com weekly page as the first content segment (issue #382).

    The weekly page (derived from *job_id*) is shown right after the intro and
    before any repo is discussed.  It is added as a *generic* segment carrying a
    ``source_url`` so the recorder navigates to and scrolls the page like any
    other website segment.

    Its duration spans the entire bridge before the first repo mention (the
    first segment's ``start_seconds``), with a ``WEEKLY_SEGMENT_MIN_SECONDS``
    floor.  Filling the whole bridge keeps the plan tiling the audio timeline
    with no gap: clamping the weekly page to a small maximum used to leave the
    rest of the bridge uncovered, and because composition lays segments out by
    duration that gap collapsed and shifted every repo earlier than the moment
    the hosts actually name it (issue #544).

    The plan is returned unchanged when *job_id* yields no weekly URL or the
    weekly page is already the first segment (idempotent).
    """
    url = weekly_url_from_job_id(job_id) if use_live_source else None
    if not use_live_source:
        logger.info(
            "Using pinned replay input instead of live weekly URL for job_id=%s",
            job_id,
        )
    if url is None:
        if not use_live_source:
            url = ""
        else:
            logger.info(
                "No weekly URL derivable from job_id=%s; skipping weekly segment",
                job_id,
            )
            return plan

    segments = list(plan.segments)
    # A plan with no repo segments is already a generic/full-length page (often
    # the weekly page itself); there is nothing to precede, so leave it as-is.
    if not any(seg.repo is not None for seg in segments):
        logger.info(
            "Plan for job_id=%s has no repo segments; skipping weekly segment",
            job_id,
        )
        return plan
    if segments and segments[0].source_url == (url or None) and segments[0].is_generic:
        return plan

    # ``segments`` is guaranteed non-empty here: the function returns early above
    # when the plan has no repo segments.
    first_start = segments[0].start_seconds
    # Span the whole bridge (only a minimum floor) so the weekly page fills the
    # pre-first-repo gap and the plan keeps tiling the audio timeline with no
    # hole — otherwise every repo is shown earlier than it is discussed (#544).
    weekly_duration = max(first_start, WEEKLY_SEGMENT_MIN_SECONDS)

    weekly_segment = VideoSegment(
        start_seconds=0.0,
        duration_seconds=weekly_duration,
        repo=None,
        source_url=url or None,
    )

    # The first repo already starts at ``first_start`` (the pre-first-repo
    # "bridge" the weekly segment is meant to occupy). Only shift existing
    # segments by the *extra* time the clamped weekly segment introduces beyond
    # that bridge; otherwise the bridge time is double-counted (issue #382).
    shift = max(0.0, weekly_duration - first_start)
    shifted = [
        VideoSegment(
            start_seconds=seg.start_seconds + shift,
            duration_seconds=seg.duration_seconds,
            repo=seg.repo,
            source_url=seg.source_url,
            removed_reason=seg.removed_reason,
        )
        for seg in segments
    ]

    logger.info(
        "Inserting claracle.com weekly page %s as first segment (%.1fs, shift %.1fs)",
        url,
        weekly_duration,
        shift,
    )
    return EpisodePlan(
        total_duration_seconds=plan.total_duration_seconds + shift,
        segments=tuple([weekly_segment, *shifted]),
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
        snapped = snap_to_audio_boundary(seg.start_seconds, cue_points, tolerance_seconds)
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
                removed_reason=seg.removed_reason,
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
            time_seconds=snap_to_audio_boundary(cue.time_seconds, cue_points, tolerance_seconds),
            kind=cue.kind,
            label=cue.label,
        )
        for cue in cues
    ]
