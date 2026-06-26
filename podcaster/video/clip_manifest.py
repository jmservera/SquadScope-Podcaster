"""Clip manifests for repo video generation (jmservera/SquadScope-Coordinator#32, #487).

Repo clips are the raw video material the Layer 3 Edit Decision List (#488 / #490)
trims and sequences against the realized audio timeline. To make that editing
*deterministic* — and to let clips be generated **in parallel**, ahead of and
independent from the audio — each generated clip is accompanied by a
**clip manifest** describing what the clip contains and how it may be reshaped:

* **duration** — the realized length of the recorded clip;
* **chapters** — labeled regions (e.g. ``readme``, ``file-tree``, ``issues``)
  with their time spans, so the editor knows what is on screen when;
* **safe trim ranges** — interior regions that may be cut to shorten the clip
  without landing on a transient (page load, scroll animation) — supporting the
  *"generate long, trim to fit"* design principle;
* **loopable sections** — stable regions that can be repeated seamlessly to
  *extend* a clip that is unexpectedly shorter than its discussion needs.

Design principle: **generate long, trim to fit.** A clip is recorded for
``required_clip_seconds(discussion_seconds) = max(60s, discussion * 1.5)`` so it
always covers its discussion time with margin; the EDL then trims it down to the
exact realized duration using the declared safe ranges.

The manifest is **versioned** (:data:`CLIP_MANIFEST_SCHEMA_VERSION`) and
serialises to a stable dict (:meth:`ClipManifest.to_dict` /
:meth:`ClipManifest.from_dict`) so the Layer 3 planner can consume it without
touching the video file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger("podcaster.video.clip_manifest")

#: Schema version for the serialised clip manifest. Bump the **minor** for
#: backward-compatible additions and the **major** for breaking changes.
CLIP_MANIFEST_SCHEMA_VERSION = "1.0"

#: Minimum recorded clip length (seconds), regardless of how short the discussion
#: is — a clip should never be so short that trimming has nothing to work with.
REQUIRED_CLIP_MIN_SECONDS = 60.0

#: Multiplier applied to the discussion time so every clip is recorded with
#: head-room for the EDL to trim against.
DISCUSSION_MARGIN_FACTOR = 1.5

#: Default margin (ms) trimmed off each end of a chapter before treating its
#: interior as a *safe* trim range, keeping cuts away from transitions.
DEFAULT_CHAPTER_EDGE_MARGIN_MS = 500

#: A chapter must be at least this long (ms) to yield a usable safe trim range or
#: a loopable section after the edge margins are removed.
DEFAULT_MIN_SAFE_RANGE_MS = 1_000


class ClipManifestError(ValueError):
    """Raised when a clip manifest cannot be built or is internally inconsistent."""


def required_clip_seconds(discussion_seconds: float) -> float:
    """Length (seconds) a repo clip must be recorded to, per *discussion_seconds*.

    ``max(REQUIRED_CLIP_MIN_SECONDS, discussion_seconds * DISCUSSION_MARGIN_FACTOR)``
    — long enough to cover the discussion with margin so the EDL can always trim
    to fit rather than stretch. Negative inputs are clamped to the floor.
    """
    if discussion_seconds < 0:
        return REQUIRED_CLIP_MIN_SECONDS
    return max(REQUIRED_CLIP_MIN_SECONDS, discussion_seconds * DISCUSSION_MARGIN_FACTOR)


# --- Data structures ---


@dataclass(frozen=True)
class ClipChapter:
    """A labeled region of a clip with its time span (ms)."""

    label: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "start_ms": self.start_ms, "end_ms": self.end_ms}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClipChapter":
        return cls(
            label=str(data["label"]),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
        )


@dataclass(frozen=True)
class TrimRange:
    """An interior region (ms) that is *safe* to cut when shortening a clip."""

    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrimRange":
        return cls(start_ms=int(data["start_ms"]), end_ms=int(data["end_ms"]))


@dataclass(frozen=True)
class LoopSection:
    """A stable region (ms) that can be repeated seamlessly to *extend* a clip."""

    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LoopSection":
        return cls(start_ms=int(data["start_ms"]), end_ms=int(data["end_ms"]))


@dataclass(frozen=True)
class ClipManifest:
    """A recorded clip plus the metadata Layer 3 needs to trim/loop it to fit.

    Attributes:
        clip_id: Stable identifier for the clip (e.g. ``"clip-000"``).
        repo_url: The repository the clip shows, or ``None`` for an
            article/intermission/fallback clip.
        duration_ms: Realized length of the recorded clip.
        chapters: Labeled regions in timeline order.
        trim_ranges: Safe interior ranges to cut when shortening.
        loop_sections: Stable ranges to repeat when extending.
        is_fallback: True when the clip is a static fallback card (no live page),
            in which case it is freely loopable/trimmable.
        schema_version: Versioned schema marker.
    """

    clip_id: str
    duration_ms: int
    repo_url: str | None = None
    chapters: tuple[ClipChapter, ...] = field(default_factory=tuple)
    trim_ranges: tuple[TrimRange, ...] = field(default_factory=tuple)
    loop_sections: tuple[LoopSection, ...] = field(default_factory=tuple)
    is_fallback: bool = False
    schema_version: str = CLIP_MANIFEST_SCHEMA_VERSION

    @property
    def trimmable_ms(self) -> int:
        """Total milliseconds that may be removed using the safe trim ranges."""
        return sum(r.duration_ms for r in self.trim_ranges)

    @property
    def min_trimmed_duration_ms(self) -> int:
        """Shortest duration achievable by applying every safe trim range."""
        return self.duration_ms - self.trimmable_ms

    def covers(self, discussion_seconds: float) -> bool:
        """True when the clip is long enough to cover *discussion_seconds*."""
        return self.duration_ms >= int(round(discussion_seconds * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "clip_id": self.clip_id,
            "repo_url": self.repo_url,
            "duration_ms": self.duration_ms,
            "is_fallback": self.is_fallback,
            "chapters": [c.to_dict() for c in self.chapters],
            "trim_ranges": [r.to_dict() for r in self.trim_ranges],
            "loop_sections": [s.to_dict() for s in self.loop_sections],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClipManifest":
        return cls(
            clip_id=str(data["clip_id"]),
            duration_ms=int(data["duration_ms"]),
            repo_url=(str(data["repo_url"]) if data.get("repo_url") else None),
            chapters=tuple(ClipChapter.from_dict(c) for c in data.get("chapters", [])),
            trim_ranges=tuple(TrimRange.from_dict(r) for r in data.get("trim_ranges", [])),
            loop_sections=tuple(
                LoopSection.from_dict(s) for s in data.get("loop_sections", [])
            ),
            is_fallback=bool(data.get("is_fallback", False)),
            schema_version=str(
                data.get("schema_version", CLIP_MANIFEST_SCHEMA_VERSION)
            ),
        )


# --- Manifest construction ---


def _safe_interior(
    start_ms: int, end_ms: int, edge_margin_ms: int, min_span_ms: int
) -> tuple[int, int] | None:
    """Interior of ``[start_ms, end_ms]`` minus *edge_margin_ms* on each side.

    Returns ``None`` when the remaining span is shorter than *min_span_ms*.
    """
    inner_start = start_ms + edge_margin_ms
    inner_end = end_ms - edge_margin_ms
    if inner_end - inner_start >= min_span_ms:
        return inner_start, inner_end
    return None


def build_clip_manifest(
    clip_id: str,
    duration_ms: int,
    *,
    repo_url: str | None = None,
    chapters: Sequence[ClipChapter] | None = None,
    is_fallback: bool = False,
    edge_margin_ms: int = DEFAULT_CHAPTER_EDGE_MARGIN_MS,
    min_safe_range_ms: int = DEFAULT_MIN_SAFE_RANGE_MS,
) -> ClipManifest:
    """Build a :class:`ClipManifest`, deriving trim ranges and loop sections.

    Safe trim ranges and loopable sections are derived from *chapters*: each
    chapter's interior (minus ``edge_margin_ms`` on each side, when at least
    ``min_safe_range_ms`` remains) is both a safe place to cut and a region that
    can be looped to extend the clip. A *fallback* clip (a static card with no
    chapters) is treated as fully loopable and trimmable across its whole span.

    Raises:
        ClipManifestError: for a non-positive duration, or chapters that fall
            outside ``[0, duration_ms]`` or are not ordered/disjoint.
    """
    if duration_ms <= 0:
        raise ClipManifestError(f"clip {clip_id!r} must have a positive duration")

    chapters = tuple(chapters or ())
    _validate_chapters(clip_id, chapters, duration_ms)

    trim_ranges: list[TrimRange] = []
    loop_sections: list[LoopSection] = []

    if chapters:
        for chapter in chapters:
            interior = _safe_interior(
                chapter.start_ms, chapter.end_ms, edge_margin_ms, min_safe_range_ms
            )
            if interior is None:
                continue
            inner_start, inner_end = interior
            trim_ranges.append(TrimRange(inner_start, inner_end))
            loop_sections.append(LoopSection(inner_start, inner_end))
    elif is_fallback:
        # A static fallback card is uniform — its whole span is safe to trim/loop.
        trim_ranges.append(TrimRange(0, duration_ms))
        loop_sections.append(LoopSection(0, duration_ms))

    manifest = ClipManifest(
        clip_id=clip_id,
        duration_ms=duration_ms,
        repo_url=repo_url,
        chapters=chapters,
        trim_ranges=tuple(trim_ranges),
        loop_sections=tuple(loop_sections),
        is_fallback=is_fallback,
    )

    if not is_fallback and not manifest.trim_ranges:
        logger.warning(
            "clip %s has no safe trim ranges (chapters too short) — EDL must trim "
            "at the edges or loop instead",
            clip_id,
        )
    return manifest


def _validate_chapters(
    clip_id: str, chapters: Sequence[ClipChapter], duration_ms: int
) -> None:
    """Ensure chapters are inside the clip, well-formed, ordered and disjoint."""
    prev_end = 0
    for chapter in chapters:
        if chapter.end_ms <= chapter.start_ms:
            raise ClipManifestError(
                f"clip {clip_id!r} chapter {chapter.label!r} has non-positive duration"
            )
        if chapter.start_ms < 0 or chapter.end_ms > duration_ms:
            raise ClipManifestError(
                f"clip {clip_id!r} chapter {chapter.label!r} "
                f"[{chapter.start_ms},{chapter.end_ms}] is outside [0,{duration_ms}]"
            )
        if chapter.start_ms < prev_end:
            raise ClipManifestError(
                f"clip {clip_id!r} chapter {chapter.label!r} overlaps the previous chapter"
            )
        prev_end = chapter.end_ms
