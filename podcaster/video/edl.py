"""Layer 3 — Timeline planner: audio metadata + clip manifests → EDL (#488).

This is the editorial decisioning stage of the Phase 4 audio–video
synchronization architecture (jmservera/SquadScope-Coordinator#32)::

    (1) Script Plan Metadata  →  (2) Realized Audio Metadata  →  (3) Edit Decision List
            #485                          #486                          #488 (this) / #490

Given the **realized audio metadata** (Layer 2, :mod:`podcaster.audio_metadata`)
and the **clip manifests** (:mod:`podcaster.video.clip_manifest`) for the repo /
article clips, this module deterministically produces an **Edit Decision List
(EDL)**: an ordered, gap-free sequence of timeline segments that matches video
material to the audio timeline. The EDL is the *plan*; the ffmpeg renderer
(#490) executes it.

Editorial rules enforced here:

* **Minimum visual segment duration.** A visual segment shorter than
  ``min_visual_ms`` (default 8s) is *merged into a neighbour* so the screen never
  flips faster than a viewer can follow. (An intermission is still allowed to be
  short because it is an intentional breather.)
* **Trim to fit / loop to fill.** Each clip is recorded long (see
  :func:`podcaster.video.clip_manifest.required_clip_seconds`). The planner trims
  it to the exact audio duration using the manifest's *safe trim ranges*, or
  loops a *loop section* when a clip is unexpectedly too short — never stretching.
* **Graceful degradation.** A repo/article block whose clip is missing degrades
  to an intermission fill rather than failing (ties into #489).
* **Crossfades & title cards.** A crossfade is declared into every segment after
  the first; a title card overlay is declared on the first segment of each
  section.

Guarantees (deterministic for identical inputs):

* the EDL covers ``[0, total_audio_ms]`` with **no gaps and no overlaps**;
* every non-intermission segment is at least ``min_visual_ms`` long;
* each clip segment's source ranges concatenate to exactly its timeline duration.

The EDL is **versioned** and serialises to a stable dict for the renderer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from podcaster.audio_metadata import RealizedAudioMetadata, TopicRange
from podcaster.script_plan import VisualMode
from podcaster.video.clip_manifest import ClipManifest

logger = logging.getLogger("podcaster.video.edl")

#: Schema version for the serialised EDL.
EDL_SCHEMA_VERSION = "1.0"

#: Minimum on-screen duration (ms) for a non-intermission visual segment.
DEFAULT_MIN_VISUAL_MS = 8_000

#: Default crossfade duration (ms) declared into each segment after the first.
DEFAULT_CROSSFADE_MS = 500

#: Default title-card overlay duration (ms) at the start of a section.
DEFAULT_TITLE_CARD_MS = 2_000


class EdlError(ValueError):
    """Raised when an EDL cannot be planned from the inputs."""


class EdlSegmentKind(str, Enum):
    """What a timeline segment renders."""

    CLIP = "clip"  # a trimmed/looped repo or article clip
    INTERMISSION = "intermission"  # a generated intermission/breather fill


@dataclass(frozen=True)
class SourceRange:
    """A sub-range (ms) of a source clip, played as part of a segment."""

    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRange":
        return cls(start_ms=int(data["start_ms"]), end_ms=int(data["end_ms"]))


@dataclass(frozen=True)
class TitleCardOverlay:
    """A title-card overlay shown at the start of a segment for *duration_ms*."""

    text: str
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "duration_ms": self.duration_ms}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TitleCardOverlay":
        return cls(text=str(data["text"]), duration_ms=int(data["duration_ms"]))


@dataclass(frozen=True)
class EdlSegment:
    """One contiguous timeline segment in the EDL.

    Attributes:
        kind: ``clip`` or ``intermission``.
        timeline_start_ms / timeline_end_ms: Position on the final video timeline.
        visual_mode: Layer 1 visual mode this segment represents.
        clip_id: Source clip id (``None`` for an intermission fill).
        repo_url: Repo shown (``None`` for article/intermission).
        section_id: Enclosing section id (for grouping / title cards).
        source_ranges: Sub-ranges of the source clip, concatenated to fill the
            segment. Their durations sum to ``timeline_end_ms - timeline_start_ms``.
            Empty for an intermission fill.
        looped: True when the clip was extended by repeating a loop section.
        crossfade_in_ms: Crossfade duration into this segment from the previous
            one (0 for the first segment).
        title_card: Optional title-card overlay at the start of the segment.
        is_fallback: True when this segment degraded to a fill because its clip
            was missing.
    """

    kind: EdlSegmentKind
    timeline_start_ms: int
    timeline_end_ms: int
    visual_mode: VisualMode
    clip_id: str | None = None
    repo_url: str | None = None
    section_id: str | None = None
    source_ranges: tuple[SourceRange, ...] = field(default_factory=tuple)
    looped: bool = False
    crossfade_in_ms: int = 0
    title_card: TitleCardOverlay | None = None
    is_fallback: bool = False

    @property
    def duration_ms(self) -> int:
        return self.timeline_end_ms - self.timeline_start_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "timeline_start_ms": self.timeline_start_ms,
            "timeline_end_ms": self.timeline_end_ms,
            "visual_mode": self.visual_mode.value,
            "clip_id": self.clip_id,
            "repo_url": self.repo_url,
            "section_id": self.section_id,
            "source_ranges": [r.to_dict() for r in self.source_ranges],
            "looped": self.looped,
            "crossfade_in_ms": self.crossfade_in_ms,
            "title_card": self.title_card.to_dict() if self.title_card else None,
            "is_fallback": self.is_fallback,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EdlSegment":
        card = data.get("title_card")
        return cls(
            kind=EdlSegmentKind(str(data["kind"])),
            timeline_start_ms=int(data["timeline_start_ms"]),
            timeline_end_ms=int(data["timeline_end_ms"]),
            visual_mode=VisualMode.from_value(data["visual_mode"]),
            clip_id=(str(data["clip_id"]) if data.get("clip_id") else None),
            repo_url=(str(data["repo_url"]) if data.get("repo_url") else None),
            section_id=(str(data["section_id"]) if data.get("section_id") else None),
            source_ranges=tuple(
                SourceRange.from_dict(r) for r in data.get("source_ranges", [])
            ),
            looped=bool(data.get("looped", False)),
            crossfade_in_ms=int(data.get("crossfade_in_ms", 0)),
            title_card=TitleCardOverlay.from_dict(card) if card else None,
            is_fallback=bool(data.get("is_fallback", False)),
        )


@dataclass(frozen=True)
class EditDecisionList:
    """The versioned Layer 3 EDL: an ordered, gap-free timeline."""

    segments: tuple[EdlSegment, ...] = field(default_factory=tuple)
    total_duration_ms: int = 0
    crossfade_ms: int = DEFAULT_CROSSFADE_MS
    min_visual_ms: int = DEFAULT_MIN_VISUAL_MS
    schema_version: str = EDL_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "total_duration_ms": self.total_duration_ms,
            "crossfade_ms": self.crossfade_ms,
            "min_visual_ms": self.min_visual_ms,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditDecisionList":
        return cls(
            segments=tuple(EdlSegment.from_dict(s) for s in data.get("segments", [])),
            total_duration_ms=int(data.get("total_duration_ms", 0)),
            crossfade_ms=int(data.get("crossfade_ms", DEFAULT_CROSSFADE_MS)),
            min_visual_ms=int(data.get("min_visual_ms", DEFAULT_MIN_VISUAL_MS)),
            schema_version=str(data.get("schema_version", EDL_SCHEMA_VERSION)),
        )


# --- Internal: visual blocks (topics after min-duration merging) ---


@dataclass
class _Block:
    visual_mode: VisualMode
    start_ms: int
    end_ms: int
    repo_url: str | None
    section_id: str | None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def _merge_short_topics(
    topics: Sequence[TopicRange], min_visual_ms: int
) -> list[_Block]:
    """Merge sub-``min_visual_ms`` non-intermission topics into a neighbour.

    A short topic extends the *previous* block (which keeps its own clip), so the
    screen does not flip faster than a viewer can follow. A short *leading* block
    is absorbed by the block that follows it. Intermissions are exempt — they are
    deliberate breathers and may be short.
    """
    blocks: list[_Block] = []
    for topic in topics:
        block = _Block(
            visual_mode=topic.visual_mode,
            start_ms=topic.start_ms,
            end_ms=topic.end_ms,
            repo_url=topic.repo_url,
            section_id=topic.section_id,
        )
        too_short = (
            block.visual_mode is not VisualMode.INTERMISSION
            and block.duration_ms < min_visual_ms
        )
        if too_short and blocks:
            blocks[-1].end_ms = block.end_ms  # extend previous; keep its clip
        else:
            blocks.append(block)

    # A short leading block is absorbed forward into the next block.
    while (
        len(blocks) >= 2
        and blocks[0].visual_mode is not VisualMode.INTERMISSION
        and blocks[0].duration_ms < min_visual_ms
    ):
        blocks[1].start_ms = blocks[0].start_ms
        blocks.pop(0)
    return blocks


def _fill_gaps(blocks: list[_Block], total_duration_ms: int) -> None:
    """Stretch block boundaries so they tile ``[0, total_duration_ms]`` exactly."""
    if not blocks:
        return
    blocks[0].start_ms = 0
    for current, nxt in zip(blocks, blocks[1:]):
        current.end_ms = nxt.start_ms
    blocks[-1].end_ms = total_duration_ms


# --- Internal: trim-to-fit / loop-to-fill source ranges ---


def _excise(start: int, end: int, removed: Sequence[tuple[int, int]]) -> list[SourceRange]:
    """Return ``[start, end]`` minus the *removed* intervals (merged), in order."""
    merged: list[list[int]] = []
    for lo, hi in sorted(removed):
        lo = max(lo, start)
        hi = min(hi, end)
        if hi <= lo:
            continue
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    kept: list[SourceRange] = []
    cursor = start
    for lo, hi in merged:
        if lo > cursor:
            kept.append(SourceRange(cursor, lo))
        cursor = max(cursor, hi)
    if cursor < end:
        kept.append(SourceRange(cursor, end))
    return kept


def plan_source_ranges(manifest: ClipManifest, target_ms: int) -> tuple[tuple[SourceRange, ...], bool]:
    """Plan the source ranges of *manifest* that fill exactly *target_ms*.

    Returns ``(source_ranges, looped)`` where the range durations sum to
    ``target_ms``:

    * **exact** — the whole clip when ``target == duration``;
    * **trim** — remove the excess from the manifest's safe trim ranges
      (largest-first, deterministic); if the safe ranges are insufficient, trim
      the remainder off the clip tail;
    * **loop** — when the clip is shorter than *target*, repeat its loop sections
      (or, lacking any, the whole clip) to make up the deficit (``looped=True``).
    """
    duration = manifest.duration_ms
    if target_ms <= 0:
        return (), False
    if target_ms == duration:
        return (SourceRange(0, duration),), False

    if target_ms < duration:
        excess = duration - target_ms
        removed: list[tuple[int, int]] = []
        for r in sorted(manifest.trim_ranges, key=lambda x: (-x.duration_ms, x.start_ms)):
            if excess <= 0:
                break
            take = min(excess, r.duration_ms)
            removed.append((r.end_ms - take, r.end_ms))  # cut from the range's tail
            excess -= take
        if excess > 0:
            removed.append((duration - excess, duration))  # hard-trim the clip tail
        kept = tuple(_excise(0, duration, removed))
        return kept, False

    # target_ms > duration → loop to fill
    deficit = target_ms - duration
    ranges: list[SourceRange] = [SourceRange(0, duration)]
    loops = sorted(manifest.loop_sections, key=lambda x: (-x.duration_ms, x.start_ms))
    if loops:
        i = 0
        guard = 0
        while deficit > 0 and guard < 100_000:
            section = loops[i % len(loops)]
            take = min(deficit, section.duration_ms)
            ranges.append(SourceRange(section.start_ms, section.start_ms + take))
            deficit -= take
            i += 1
            guard += 1
    while deficit > 0:  # no loop sections (or guard) — repeat the whole clip
        take = min(deficit, duration)
        ranges.append(SourceRange(0, take))
        deficit -= take
    return tuple(ranges), True


# --- Public API ---


def plan_edl(
    metadata: RealizedAudioMetadata,
    clips: Mapping[str, ClipManifest],
    *,
    article_clip: ClipManifest | None = None,
    section_titles: Mapping[str, str] | None = None,
    min_visual_ms: int = DEFAULT_MIN_VISUAL_MS,
    crossfade_ms: int = DEFAULT_CROSSFADE_MS,
    title_card_ms: int = DEFAULT_TITLE_CARD_MS,
) -> EditDecisionList:
    """Plan an :class:`EditDecisionList` from Layer 2 metadata and clip manifests.

    Args:
        metadata: Layer 2 :class:`~podcaster.audio_metadata.RealizedAudioMetadata`.
        clips: Repo clip manifests keyed by ``repo_url``.
        article_clip: Manifest for the article / weekly-rundown clip (used for
            ``article`` visual mode). When absent, article blocks become fills.
        section_titles: Optional ``section_id`` → title text for title cards.
        min_visual_ms: Minimum on-screen duration for a non-intermission segment.
        crossfade_ms: Crossfade declared into each segment after the first.
        title_card_ms: Title-card overlay duration at the start of each section.

    Returns:
        A gap-free, deterministic :class:`EditDecisionList` covering the full
        audio duration.

    Raises:
        EdlError: when ``min_visual_ms``/``crossfade_ms`` are negative.
    """
    if min_visual_ms < 0 or crossfade_ms < 0 or title_card_ms < 0:
        raise EdlError("min_visual_ms, crossfade_ms and title_card_ms must be non-negative")

    total = metadata.total_duration_ms
    if not metadata.topics or total <= 0:
        return EditDecisionList(
            total_duration_ms=max(total, 0),
            crossfade_ms=crossfade_ms,
            min_visual_ms=min_visual_ms,
        )

    blocks = _merge_short_topics(metadata.topics, min_visual_ms)
    _fill_gaps(blocks, total)

    section_titles = section_titles or {}
    segments: list[EdlSegment] = []
    prev_section: str | None = None
    first_block = True

    for block in blocks:
        target = block.duration_ms
        crossfade_in = 0 if first_block else min(crossfade_ms, target)

        title_card: TitleCardOverlay | None = None
        if block.section_id and block.section_id != prev_section and title_card_ms > 0:
            label = section_titles.get(block.section_id, block.section_id)
            title_card = TitleCardOverlay(text=label, duration_ms=min(title_card_ms, target))

        segment = _plan_block_segment(
            block,
            clips=clips,
            article_clip=article_clip,
            target=target,
            crossfade_in=crossfade_in,
            title_card=title_card,
        )
        segments.append(segment)
        prev_section = block.section_id
        first_block = False

    return EditDecisionList(
        segments=tuple(segments),
        total_duration_ms=total,
        crossfade_ms=crossfade_ms,
        min_visual_ms=min_visual_ms,
    )


def _plan_block_segment(
    block: _Block,
    *,
    clips: Mapping[str, ClipManifest],
    article_clip: ClipManifest | None,
    target: int,
    crossfade_in: int,
    title_card: TitleCardOverlay | None,
) -> EdlSegment:
    """Build the EDL segment for one visual block, degrading to a fill if needed."""
    manifest: ClipManifest | None = None
    if block.visual_mode is VisualMode.REPO and block.repo_url:
        manifest = clips.get(block.repo_url)
    elif block.visual_mode is VisualMode.ARTICLE:
        manifest = article_clip

    if block.visual_mode is VisualMode.INTERMISSION or manifest is None:
        is_fallback = (
            block.visual_mode is not VisualMode.INTERMISSION and manifest is None
        )
        if is_fallback:
            logger.warning(
                "no clip for %s block (repo_url=%s) — degrading to intermission fill",
                block.visual_mode.value,
                block.repo_url,
            )
        return EdlSegment(
            kind=EdlSegmentKind.INTERMISSION,
            timeline_start_ms=block.start_ms,
            timeline_end_ms=block.end_ms,
            visual_mode=block.visual_mode,
            repo_url=block.repo_url,
            section_id=block.section_id,
            crossfade_in_ms=crossfade_in,
            title_card=title_card,
            is_fallback=is_fallback,
        )

    source_ranges, looped = plan_source_ranges(manifest, target)
    return EdlSegment(
        kind=EdlSegmentKind.CLIP,
        timeline_start_ms=block.start_ms,
        timeline_end_ms=block.end_ms,
        visual_mode=block.visual_mode,
        clip_id=manifest.clip_id,
        repo_url=block.repo_url,
        section_id=block.section_id,
        source_ranges=source_ranges,
        looped=looped,
        crossfade_in_ms=crossfade_in,
        title_card=title_card,
        is_fallback=manifest.is_fallback,
    )


def validate_edl(edl: EditDecisionList) -> None:
    """Assert the EDL invariants (raises :class:`EdlError` on violation).

    * segments tile ``[0, total_duration_ms]`` with no gaps or overlaps;
    * every clip segment's source ranges sum to its timeline duration;
    * every non-intermission segment is at least ``min_visual_ms`` long.
    """
    if not edl.segments:
        if edl.total_duration_ms not in (0,):
            raise EdlError("non-empty timeline has no segments")
        return

    cursor = 0
    for seg in edl.segments:
        if seg.timeline_start_ms != cursor:
            raise EdlError(
                f"gap/overlap at {seg.timeline_start_ms} (expected {cursor})"
            )
        if seg.timeline_end_ms <= seg.timeline_start_ms:
            raise EdlError("segment has non-positive duration")
        if seg.kind is EdlSegmentKind.CLIP:
            covered = sum(r.duration_ms for r in seg.source_ranges)
            if covered != seg.duration_ms:
                raise EdlError(
                    f"clip segment source ranges cover {covered}ms, "
                    f"expected {seg.duration_ms}ms"
                )
        if (
            seg.visual_mode is not VisualMode.INTERMISSION
            and seg.duration_ms < edl.min_visual_ms
        ):
            raise EdlError(
                f"segment {seg.duration_ms}ms is below min_visual_ms {edl.min_visual_ms}"
            )
        cursor = seg.timeline_end_ms

    if cursor != edl.total_duration_ms:
        raise EdlError(
            f"timeline ends at {cursor}, expected {edl.total_duration_ms}"
        )
