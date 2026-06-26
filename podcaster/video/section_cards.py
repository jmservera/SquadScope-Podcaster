"""Generate visual section title cards for podcast video segments (#377).

Between the editorial sections of a weekly episode (Trends, Industry,
Signal & Noise, Blind Spots, …) the video previously cut straight from one repo
recording to the next with no indication of which section was being discussed.
This module adds brief, minimalist title cards at those section boundaries.

The cards are rendered ffmpeg-natively — a dark ``color`` background with a
large white ``drawtext`` section name, a brand-accent rule, and smooth
fade-in/fade-out — exactly the proven approach used for the intro/outro bumpers
(:mod:`podcaster.video.intro_outro`).  No Playwright/browser is required.

Pipeline integration (all graceful — a missing/dormant feature is a no-op):

1. :func:`parse_sections` detects section headers in the script text.  Current
   scripts are plain dialogue with no headers, so this returns ``[]`` and the
   whole feature stays dormant until scripts gain section markers.
2. :func:`plan_section_card_inserts` maps each detected section to the recorded
   content segment that opens it (matched by the first GitHub repo URL after the
   header), yielding the index *before* which a card should be spliced.
3. :func:`build_section_card_inserts` renders one card per mapped section and
   returns :class:`SectionCardInsert` records for
   :func:`podcaster.video.video_compose.compose_video`.

Constraints: section metadata from issue #417 defaults cards to 0.75 s (kept in
the 0.5–1.0 s range), professional and consistent with the intro style, and
absent sections are skipped without error.

Related: jmservera/SquadScope-Podcaster#377 (parent epic #372, branding #295).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from podcaster.video.intro_outro import (
    FPS,
    HEIGHT,
    TITLE_FONT,
    WIDTH,
    ClipResult,
    _default_runner,
    _escape_drawtext,
    _get_drawtext_ffmpeg,
)
from podcaster.video.localization import localize_section_name

logger = logging.getLogger(__name__)

# --- Constants ---

#: Default on-screen duration of a section card, in milliseconds (issue #417
#: asks for 0.5–1.0 s with a 0.75 s default).
SECTION_CARD_DURATION_MS = 750

#: Fade-in / fade-out length for each card, in milliseconds (issue #377 asks for
#: 0.5 s transitions).
SECTION_CARD_FADE_MS = 500

#: Card background — the same near-black used by the intro/outro bumpers so the
#: cards feel like part of the same branded set.
CARD_BG = "#0d1117"

#: Brand-accent colour used when a detected section is not in
#: :data:`KNOWN_SECTIONS` (Claracle blue, matching the intro headline accent).
DEFAULT_ACCENT = "#58a6ff"


@dataclass(frozen=True)
class SectionDef:
    """Canonical metadata for a well-known editorial section.

    Attributes:
        name: Display name shown on the card (e.g. ``"Signal & Noise"``).
        emoji: The section's editorial emoji (kept for logging/metadata; it is
            *not* drawn because the bundled DejaVu font lacks colour-emoji
            glyphs and would render an empty box).
        accent: Hex colour for the brand-accent rule under the title.
    """

    name: str
    emoji: str
    accent: str


# Known editorial sections from the Claracle weekly article (issue #377).  Keyed
# by the normalised (lower-case, ampersand-spelled) section name so several
# spellings collapse to one canonical card.
KNOWN_SECTIONS: dict[str, SectionDef] = {
    "trends": SectionDef("Trends", "🔥", "#f0883e"),
    "industry": SectionDef("Industry", "🏭", "#58a6ff"),
    "signal & noise": SectionDef("Signal & Noise", "📡", "#3fb950"),
    "signal and noise": SectionDef("Signal & Noise", "📡", "#3fb950"),
    "blind spots": SectionDef("Blind Spots", "🫣", "#bc8cff"),
    "blind spot": SectionDef("Blind Spots", "🫣", "#bc8cff"),
    "deep dive": SectionDef("Deep Dive", "🔬", "#d29922"),
    "hot off the press": SectionDef("Hot Off The Press", "📰", "#f85149"),
    "breaking news": SectionDef("Breaking News", "📰", "#f85149"),
}


@dataclass(frozen=True)
class SectionMarker:
    """A section header detected in the script.

    Attributes:
        name: The display section name (canonicalised when the header matches a
            :data:`KNOWN_SECTIONS` entry).
        position: Character offset of the header within the original script.
            Used to find the first repo URL that follows the header.
        emoji: Editorial emoji for the section (``""`` when unknown).
        accent: Hex brand-accent colour for the card rule.
        line_index: Zero-based line number of the header within the script.
    """

    name: str
    position: int
    emoji: str = ""
    accent: str = DEFAULT_ACCENT
    line_index: int = 0


@dataclass
class SectionCardConfig:
    """Rendering configuration for a section title card."""

    duration_ms: int = SECTION_CARD_DURATION_MS
    fade_ms: int = SECTION_CARD_FADE_MS
    width: int = WIDTH
    height: int = HEIGHT
    background: str = CARD_BG
    font_size: int = 108
    locale: str = "en"


@dataclass(frozen=True)
class SectionCardInsert:
    """A rendered section card and where it belongs in the content stream.

    Consumed by :func:`podcaster.video.video_compose.compose_video` via its
    ``section_cards`` argument.

    Attributes:
        name: Section display name (for logging).
        clip_path: Path to the rendered card MP4.
        before_index: Index into the recorded **content** segment list before
            which this card is spliced.  ``0`` places the card ahead of the
            first content segment; ``len(segments)`` appends it at the end.
        duration_seconds: On-screen duration of the card, in seconds.
    """

    name: str
    clip_path: Path
    before_index: int
    duration_seconds: float


# --- Section parsing ---

# First GitHub repo URL after a header (mirrors sync_plan's repo detection but we
# only need the position/owner/name to map a section to its opening segment).
_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]*[A-Za-z0-9_-])",
)

# Strip leading list/heading/emphasis/bracket decoration and an optional
# "Section:" / "Segment:" kicker so "## 🔥 Trends", "[SECTION: Trends]" and
# "**Trends**" all reduce to the bare section name.
_DECORATION_RE = re.compile(
    r"""^\s*
        (?:\#{1,6}\s*)?            # markdown heading hashes
        (?:[-*]\s+)?              # bullet
        \[?\s*                    # opening bracket
        (?:\*\*)?\s*              # opening bold
        (?:(?:SECTION|SEGMENT)\s*[:\-]\s*)?   # "SECTION:" kicker
        (?P<body>.+?)
        \s*(?:\*\*)?\s*\]?\s*$    # closing bold / bracket
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A run of leading non-alphanumeric, non-ASCII characters (emoji + punctuation)
# at the start of a header body.
_LEADING_SYMBOLS_RE = re.compile(r"^[^\w]+", re.UNICODE)

# Detect whether a stripped run is (mostly) emoji/symbol so we can capture it.
_EMOJI_RUN_RE = re.compile(r"[^\sA-Za-z0-9_.,&'\"!?:;()/\\-]+")


def _normalize_name(name: str) -> str:
    """Lower-case and collapse whitespace for :data:`KNOWN_SECTIONS` lookups."""
    return re.sub(r"\s+", " ", name).strip().lower()


def _looks_like_dialogue(line: str) -> bool:
    """True when *line* looks like a spoken dialogue line (``Name: text``).

    Section headers are never dialogue, so we use this to avoid mistaking a
    sentence that merely mentions a section name for a header.
    """
    head = line.lstrip("-* \t")
    # "Speaker: ..." where the speaker label is a short single/two-word name.
    m = re.match(r"^([A-Z][A-Za-z0-9 ]{0,20}):\s", head)
    return m is not None


def _classify_header(line: str) -> tuple[str, str] | None:
    """Return ``(display_name, emoji)`` when *line* is a section header.

    A line is treated as a header when it is *either* a markdown heading
    (``#``-prefixed) *or*, after stripping decoration/emoji, its entire content
    equals a known editorial section name.  Returns ``None`` otherwise.
    """
    stripped = line.strip()
    if not stripped:
        return None

    is_markdown_heading = bool(re.match(r"^\s*#{1,6}\s+\S", line))

    match = _DECORATION_RE.match(stripped)
    if not match:
        return None
    body = match.group("body").strip()
    if not body:
        return None

    # Pull off a leading emoji/symbol run so "🔥 Trends" → emoji="🔥", name="Trends".
    emoji = ""
    lead = _LEADING_SYMBOLS_RE.match(body)
    if lead:
        candidate = lead.group(0).strip()
        # Only treat it as an emoji if it is non-ASCII (skip plain punctuation).
        if _EMOJI_RUN_RE.search(candidate):
            emoji = candidate
        body = body[lead.end():].strip()

    if not body or _looks_like_dialogue(stripped):
        return None

    known = KNOWN_SECTIONS.get(_normalize_name(body))
    if known is not None:
        return known.name, known.emoji or emoji

    if is_markdown_heading:
        # An explicit markdown heading is a section even if it is not in the
        # known registry — but reject overly long prose (a sentence heading).
        words = body.split()
        if 1 <= len(words) <= 6 and len(body) <= 48:
            return body, emoji

    return None


def parse_sections(script: str) -> list[SectionMarker]:
    """Detect section headers in *script* and return them in document order.

    Recognised header conventions (any may appear in the script body):

    * Markdown headings — ``## Trends``, ``### 🔥 Trends``.
    * Bracketed markers — ``[SECTION: Signal & Noise]``, ``[Blind Spots]``.
    * Bold/emoji standalone lines naming a known editorial section —
      ``**Industry**``, ``📡 Signal & Noise``.

    Only the script *body* (after the first ``---`` header separator, when
    present) is scanned so episode metadata never matches.  Lines that look like
    spoken dialogue (``Name: …``) are ignored.

    Args:
        script: Full podcast script text.

    Returns:
        Ordered list of :class:`SectionMarker`.  Empty when no sections are
        found — callers treat this as "skip title cards" (graceful, no error).
    """
    if not script or not script.strip():
        return []

    # Restrict to the body (after the metadata header block) when present.
    header, sep, body = script.partition("\n---")
    if sep:
        offset = len(header) + len(sep)
        source = body
    else:
        offset = 0
        source = script

    markers: list[SectionMarker] = []
    seen: set[str] = set()
    # Walk the body line by line, tracking each line's absolute char offset.
    pos = offset
    for line_no, line in enumerate(source.splitlines(keepends=True)):
        classified = _classify_header(line)
        if classified is not None:
            name, emoji = classified
            key = _normalize_name(name)
            # Keep only the first occurrence of each section (collapses a
            # heading followed by a duplicate emoji line into one card).
            if key not in seen:
                known = KNOWN_SECTIONS.get(key)
                accent = known.accent if known else DEFAULT_ACCENT
                emoji = (known.emoji if known else "") or emoji
                markers.append(
                    SectionMarker(
                        name=name,
                        position=pos,
                        emoji=emoji,
                        accent=accent,
                        line_index=line_no,
                    )
                )
                seen.add(key)
        pos += len(line)

    if markers:
        logger.info(
            "Detected %d section header(s): %s",
            len(markers), ", ".join(m.name for m in markers),
        )
    return markers


# --- Section → segment mapping ---


def plan_section_card_inserts(
    script: str,
    sections: Sequence[SectionMarker],
    segment_repo_urls: Sequence[str | None],
) -> list[tuple[str, int]]:
    """Map each section to the content-segment index that opens it.

    For every section header, the first GitHub repo URL appearing *after* the
    header in the script identifies the repository discussed first in that
    section.  The matching entry in *segment_repo_urls* (the per-segment repo
    URL, ``None`` for generic segments) gives the segment index, and the card is
    inserted immediately *before* it.

    Args:
        script: Full podcast script text (used to locate repo URLs by position).
        sections: Detected section markers from :func:`parse_sections`.
        segment_repo_urls: Repo URL for each recorded content segment in order
            (``None`` for generic/weekly segments).

    Returns:
        A list of ``(section_name, before_index)`` pairs, ordered by segment
        index, de-duplicated so at most one card lands at any boundary.  Empty
        when nothing maps (graceful skip).
    """
    if not sections:
        return []

    # Normalise segment URLs (strip trailing slash, lowercase) for matching.
    norm_urls: list[str | None] = []
    for url in segment_repo_urls:
        norm_urls.append(_normalize_repo_url(url) if url else None)

    inserts: list[tuple[str, int]] = []
    used_indices: set[int] = set()

    for section in sections:
        match = _GITHUB_URL_RE.search(script, section.position)
        if match is None:
            logger.debug(
                "Section %r has no repo URL after it — skipping card",
                section.name,
            )
            continue
        target = _normalize_repo_url(match.group(0))
        index = next(
            (i for i, u in enumerate(norm_urls) if u == target),
            None,
        )
        if index is None or index in used_indices:
            continue
        used_indices.add(index)
        inserts.append((section.name, index))

    inserts.sort(key=lambda pair: pair[1])
    return inserts


def _normalize_repo_url(url: str) -> str:
    """Normalise a GitHub URL to ``owner/name`` (lower-case) for comparison."""
    match = _GITHUB_URL_RE.search(url)
    if match is None:
        return url.rstrip("/").lower()
    owner, name = match.group(1), match.group(2)
    if name.endswith(".git"):
        name = name[:-4]
    return f"{owner.lower()}/{name.rstrip('.').lower()}"


# --- Card rendering (ffmpeg drawtext) ---


def _build_section_card_cmd(
    marker: SectionMarker,
    output_path: Path,
    config: SectionCardConfig,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build an ffmpeg command that renders one section title card.

    The card is a dark ``color`` source with the section name in large white
    ``drawtext``, a short brand-accent rule beneath it, and ``fade`` filters for
    a 0.5 s in/out.  Mirrors :func:`podcaster.video.intro_outro._build_intro_ffmpeg_cmd`.

    Args:
        marker: The section to render (name + accent colour).
        output_path: Destination MP4 path.
        config: Card rendering configuration.
        ffmpeg_bin: ffmpeg binary (must support ``drawtext``).

    Returns:
        Command list suitable for :func:`subprocess.run`.
    """
    duration_sec = config.duration_ms / 1000.0
    fade_sec = config.fade_ms / 1000.0
    fade_out_st = max(0.0, duration_sec - fade_sec)

    # A short accent rule centred under the title for brand flair.  Note: in
    # ``drawbox`` the bare ``h``/``w`` refer to the *box* dimensions, so the
    # frame height must be referenced as ``ih`` (and width as ``iw``).
    rule_w = 280
    rule_h = 6
    rule_y = "(ih/2)+70"

    display_name = localize_section_name(marker.name, config.locale)

    filters: list[str] = [
        # Section name — large white headline, the focal point.
        (
            f"drawtext=fontfile={TITLE_FONT}"
            f":text='{_escape_drawtext(display_name)}'"
            f":fontsize={config.font_size}:fontcolor=white"
            f":x=(w-text_w)/2:y=(h-text_h)/2-30"
            f":enable='gte(t,0.15)'"
        ),
        # Brand-accent rule beneath the title.
        (
            f"drawbox=x=(iw-{rule_w})/2:y={rule_y}:w={rule_w}:h={rule_h}"
            f":color={marker.accent}@1.0:t=fill"
            f":enable='gte(t,0.3)'"
        ),
        f"fade=t=in:st=0:d={fade_sec:.3f}",
        f"fade=t=out:st={fade_out_st:.3f}:d={fade_sec:.3f}",
    ]

    return [
        ffmpeg_bin, "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "lavfi",
        "-i", f"color=c={config.background}:size={config.width}x{config.height}:rate={FPS}",
        "-t", f"{duration_sec:.3f}",
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        str(output_path),
    ]


def generate_section_card(
    marker: SectionMarker | str,
    output_path: Path,
    config: SectionCardConfig | None = None,
    ffmpeg_bin: str | None = None,
    runner: Any = None,
) -> ClipResult:
    """Render a single section title card to *output_path*.

    Args:
        marker: A :class:`SectionMarker` or a bare section name string.  Strings
            are looked up in :data:`KNOWN_SECTIONS` for emoji/accent enrichment.
        output_path: Destination MP4 file path.
        config: Card rendering configuration (defaults to 0.75 s / 0.5 s fades).
        ffmpeg_bin: Explicit drawtext-capable ffmpeg binary.  Auto-detected via
            :func:`podcaster.video.intro_outro._get_drawtext_ffmpeg` when ``None``.
        runner: Command runner for test injection.  Uses
            :func:`subprocess.run` (via ``_default_runner``) when ``None``.

    Returns:
        :class:`ClipResult` describing the rendered card.
    """
    if config is None:
        config = SectionCardConfig()
    if isinstance(marker, str):
        marker = _marker_from_name(marker)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if ffmpeg_bin is None:
        ffmpeg_bin = _get_drawtext_ffmpeg() or "ffmpeg"

    cmd = _build_section_card_cmd(marker, output_path, config, ffmpeg_bin)
    run = runner or _default_runner
    run(cmd)
    logger.info("Generated section title card '%s': %s", marker.name, output_path)

    return ClipResult(
        path=output_path,
        duration_ms=config.duration_ms,
        width=config.width,
        height=config.height,
    )


def _marker_from_name(name: str, position: int = 0) -> SectionMarker:
    """Build a :class:`SectionMarker` from a bare name, enriching from registry."""
    known = KNOWN_SECTIONS.get(_normalize_name(name))
    if known is not None:
        return SectionMarker(
            name=known.name, position=position,
            emoji=known.emoji, accent=known.accent,
        )
    return SectionMarker(name=name, position=position, accent=DEFAULT_ACCENT)


def build_section_card_inserts(
    script: str,
    segment_repo_urls: Sequence[str | None],
    output_dir: Path,
    config: SectionCardConfig | None = None,
    ffmpeg_bin: str | None = None,
    runner: Any = None,
) -> list[SectionCardInsert]:
    """Parse sections, map them to segments, and render one card per section.

    This is the single entry point used by the video job runner.  It is fully
    graceful: an empty/dialogue-only script, sections with no following repo, or
    unmappable sections all yield an empty list (title cards are simply skipped).

    Args:
        script: Full podcast script text.
        segment_repo_urls: Per-segment repo URL in recording order (``None`` for
            generic/weekly segments).
        output_dir: Directory for the rendered card MP4s.
        config: Card rendering configuration.
        ffmpeg_bin: Explicit drawtext-capable ffmpeg binary (auto-detected when
            ``None``).
        runner: Command runner for test injection.

    Returns:
        Ordered list of :class:`SectionCardInsert` (by ``before_index``).
    """
    if config is None:
        config = SectionCardConfig()

    sections = parse_sections(script)
    plan = plan_section_card_inserts(script, sections, segment_repo_urls)
    if not plan:
        return []

    if ffmpeg_bin is None:
        ffmpeg_bin = _get_drawtext_ffmpeg()
    if ffmpeg_bin is None:
        logger.warning(
            "No drawtext-capable ffmpeg found; skipping %d section title card(s).",
            len(plan),
        )
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    duration_sec = config.duration_ms / 1000.0
    inserts: list[SectionCardInsert] = []
    for ordinal, (name, before_index) in enumerate(plan):
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "section"
        card_path = output_dir / f"section_{ordinal:02d}_{slug}.mp4"
        marker = _marker_from_name(name)
        generate_section_card(
            marker, card_path, config=config,
            ffmpeg_bin=ffmpeg_bin, runner=runner,
        )
        inserts.append(
            SectionCardInsert(
                name=name,
                clip_path=card_path,
                before_index=before_index,
                duration_seconds=duration_sec,
            )
        )

    logger.info("Prepared %d section title card insert(s)", len(inserts))
    return inserts
