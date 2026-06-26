"""Layer 2 — Realized Audio Metadata (jmservera/SquadScope-Coordinator#32, #486).

The **realized audio metadata** is the second of three layers in the Phase 4
audio–video synchronization architecture::

    (1) Script Plan Metadata  →  (2) Realized Audio Metadata  →  (3) Edit Decision List
            #485                          #486                          #488 / #490

Layer 1 declares *intent* (what the video should show for each spoken turn).
Layer 2 captures *reality*: the actual timing produced by the TTS pipeline once a
script has been synthesized. Downstream Layer 3 (the EDL) needs deterministic,
millisecond-precise timings to match audio segments to video clips — it must
never re-measure audio or re-infer structure from prose.

Concretely, given a :class:`~podcaster.script_plan.ScriptPlan` and the realized
per-segment audio durations (one duration per spoken host turn, e.g. from
:func:`podcaster.audio.probe_segment_durations`), this module emits:

* **Utterance timings** — one per spoken turn, carrying the raw speaker label,
  the normalized speaker id (``host_a`` / ``host_b``), ``start_ms`` / ``end_ms``
  on the assembled speech timeline, and the Layer 1 visual context
  (``visual_mode`` / ``repo_url`` / ``section_id``).
* **Word timings** — within each utterance, a deterministic, gap-free,
  non-overlapping breakdown of the utterance duration across its words. The
  Azure OpenAI ``/audio/speech`` endpoint does not return word timestamps, so
  word boundaries are *estimated* by distributing the measured utterance
  duration proportionally to each word's character length. This is reproducible
  for identical inputs and good enough for editorial caption / emphasis cues; it
  is explicitly an estimate, not a forced alignment.
* **Topic ranges** — contiguous runs of utterances that share the same Layer 1
  visual context ``(visual_mode, repo_url)``. Because that context only changes
  at an explicit ``## Visual:`` marker, the topic ranges *align to the script's
  visual markers* and map directly to the repo / article / intermission segments
  declared in Layer 1.

The realized timeline is built with the same primitives the production audio
pipeline uses to stitch segments — :func:`podcaster.audio.compute_segment_timeline`
with the inter-segment ``gap_seconds`` — so the timings agree with the final mix.
A ``speech_offset_seconds`` accounts for any lead-in before speech starts (e.g.
an intro-music full-volume period), mirroring
:func:`podcaster.episode.compute_section_timestamps`.

The schema is **versioned** (:data:`AUDIO_METADATA_SCHEMA_VERSION`) and serialises
to a stable dict (:meth:`RealizedAudioMetadata.to_dict` /
:meth:`RealizedAudioMetadata.from_dict`) so Layer 3 can consume it without
touching audio or markdown.

Design principle (inherited from Layer 1): *explicit, measured, deterministic >
inferred*.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from podcaster.audio import compute_segment_timeline
from podcaster.script_plan import ScriptPlan, ScriptPlanSegment, VisualMode

logger = logging.getLogger("podcaster.audio_metadata")

#: Schema version for the serialised realized-audio metadata. Bump the **minor**
#: for backward-compatible additions and the **major** for breaking changes so
#: Layer 3 can guard on it.
AUDIO_METADATA_SCHEMA_VERSION = "1.0"

#: Default inter-segment gap (seconds) used when stitching speech segments. Kept
#: in sync with :func:`podcaster.audio.compute_segment_timeline` and
#: :func:`podcaster.episode.compute_section_timestamps`.
DEFAULT_GAP_SECONDS = 0.35


class RealizedAudioMetadataError(ValueError):
    """Raised when realized audio metadata cannot be built from the inputs."""


def _to_ms(seconds: float) -> int:
    """Round *seconds* to whole milliseconds (deterministic, non-negative)."""
    return int(round(max(0.0, float(seconds)) * 1000.0))


def _role_name(index: int) -> str:
    """Stable normalized speaker id for the *index*-th distinct host."""
    return f"host_{chr(ord('a') + index)}" if 0 <= index < 26 else f"host_{index}"


def _resolve_speaker_ids(
    segments: Sequence[ScriptPlanSegment],
    host_labels: Sequence[str] | None,
) -> dict[str, str]:
    """Map each raw speaker label to a normalized ``host_*`` id.

    When ``host_labels`` is provided, ``host_labels[0]`` → ``host_a`` and
    ``host_labels[1]`` → ``host_b`` (case-insensitive); any further distinct
    speakers are assigned ``host_c``, ``host_d``, … in first-appearance order.
    Without ``host_labels``, every distinct speaker is assigned a role purely by
    first appearance, so the lead host (who speaks first) becomes ``host_a``.
    Deterministic for identical inputs.
    """
    result: dict[str, str] = {}
    if host_labels and len(host_labels) >= 2:
        result[host_labels[0].strip().lower()] = _role_name(0)
        result[host_labels[1].strip().lower()] = _role_name(1)
    used = len(set(result.values()))
    for segment in segments:
        key = segment.speaker.strip().lower()
        if key not in result:
            result[key] = _role_name(used)
            used += 1
    return result


# --- Data structures ---


@dataclass(frozen=True)
class WordTiming:
    """One word with its estimated time span within the enclosing utterance.

    Word boundaries are estimated (the TTS endpoint returns no word timestamps),
    so ``start_ms`` / ``end_ms`` are proportional to character length, contiguous
    and non-overlapping across the utterance.
    """

    text: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start_ms": self.start_ms, "end_ms": self.end_ms}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WordTiming":
        return cls(
            text=str(data["text"]),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
        )


@dataclass(frozen=True)
class UtteranceTiming:
    """One spoken host turn with its realized timing and Layer 1 visual context.

    Attributes:
        index: Zero-based spoken-order position (parallel to the script plan).
        speaker: Raw host label as written in the script (e.g. ``"Theo"``).
        speaker_id: Normalized host role (``"host_a"`` / ``"host_b"``).
        text: The spoken text for the turn.
        start_ms / end_ms: Span on the assembled speech timeline.
        visual_mode / repo_url / section_id: Carried through from Layer 1.
        words: Estimated per-word timings covering ``[start_ms, end_ms]``.
    """

    index: int
    speaker: str
    speaker_id: str
    text: str
    start_ms: int
    end_ms: int
    visual_mode: VisualMode
    repo_url: str | None = None
    section_id: str | None = None
    words: tuple[WordTiming, ...] = field(default_factory=tuple)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "speaker": self.speaker,
            "speaker_id": self.speaker_id,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "visual_mode": self.visual_mode.value,
            "repo_url": self.repo_url,
            "section_id": self.section_id,
            "words": [word.to_dict() for word in self.words],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UtteranceTiming":
        return cls(
            index=int(data["index"]),
            speaker=str(data["speaker"]),
            speaker_id=str(data["speaker_id"]),
            text=str(data["text"]),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            visual_mode=VisualMode.from_value(data["visual_mode"]),
            repo_url=(str(data["repo_url"]) if data.get("repo_url") else None),
            section_id=(str(data["section_id"]) if data.get("section_id") else None),
            words=tuple(WordTiming.from_dict(w) for w in data.get("words", [])),
        )


@dataclass(frozen=True)
class TopicRange:
    """A contiguous run of utterances sharing one Layer 1 visual context.

    Topic boundaries fall exactly where the script's ``## Visual:`` markers change
    the on-screen focus, so a ``repo`` topic range maps to a single repository
    discussion, an ``article`` range to the weekly-rundown view, and an
    ``intermission`` range to a deliberate breather.

    Attributes:
        visual_mode / repo_url: The shared Layer 1 visual context for the run.
        section_id: Section id of the first utterance in the run (or ``None``).
        start_ms / end_ms: Span from the first utterance's start to the last
            utterance's end.
        utterance_indices: Spoken-order indices of the utterances in this range.
    """

    visual_mode: VisualMode
    start_ms: int
    end_ms: int
    utterance_indices: tuple[int, ...]
    repo_url: str | None = None
    section_id: str | None = None

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "visual_mode": self.visual_mode.value,
            "repo_url": self.repo_url,
            "section_id": self.section_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "utterance_indices": list(self.utterance_indices),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TopicRange":
        return cls(
            visual_mode=VisualMode.from_value(data["visual_mode"]),
            repo_url=(str(data["repo_url"]) if data.get("repo_url") else None),
            section_id=(str(data["section_id"]) if data.get("section_id") else None),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            utterance_indices=tuple(int(i) for i in data.get("utterance_indices", [])),
        )


@dataclass(frozen=True)
class RealizedAudioMetadata:
    """The versioned Layer 2 metadata: realized utterance, word, and topic timing."""

    utterances: tuple[UtteranceTiming, ...] = field(default_factory=tuple)
    topics: tuple[TopicRange, ...] = field(default_factory=tuple)
    gap_ms: int = _to_ms(DEFAULT_GAP_SECONDS)
    speech_offset_ms: int = 0
    total_duration_ms: int = 0
    schema_version: str = AUDIO_METADATA_SCHEMA_VERSION

    @property
    def repo_topics(self) -> tuple[TopicRange, ...]:
        """Topic ranges that show a specific repository, in timeline order."""
        return tuple(t for t in self.topics if t.visual_mode is VisualMode.REPO)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the stable, versioned schema for Layer 3."""
        return {
            "schema_version": self.schema_version,
            "gap_ms": self.gap_ms,
            "speech_offset_ms": self.speech_offset_ms,
            "total_duration_ms": self.total_duration_ms,
            "utterances": [u.to_dict() for u in self.utterances],
            "topics": [t.to_dict() for t in self.topics],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RealizedAudioMetadata":
        return cls(
            utterances=tuple(
                UtteranceTiming.from_dict(u) for u in data.get("utterances", [])
            ),
            topics=tuple(TopicRange.from_dict(t) for t in data.get("topics", [])),
            gap_ms=int(data.get("gap_ms", _to_ms(DEFAULT_GAP_SECONDS))),
            speech_offset_ms=int(data.get("speech_offset_ms", 0)),
            total_duration_ms=int(data.get("total_duration_ms", 0)),
            schema_version=str(
                data.get("schema_version", AUDIO_METADATA_SCHEMA_VERSION)
            ),
        )


# --- Word-timing estimation ---


def distribute_word_timings(
    text: str, start_ms: int, end_ms: int
) -> tuple[WordTiming, ...]:
    """Estimate per-word timings spanning ``[start_ms, end_ms]``.

    The utterance duration is distributed across whitespace-delimited words in
    proportion to each word's character length (punctuation included, minimum
    weight 1 so empty-ish tokens still advance). The result is:

    * **contiguous** — each word starts where the previous one ended;
    * **non-overlapping & monotonic** — boundaries never go backwards;
    * **exact** — the first word starts at ``start_ms`` and the last ends at
      ``end_ms`` with no rounding drift;
    * **deterministic** — identical inputs always yield identical output.

    Returns an empty tuple for blank text or a non-positive span.
    """
    words = text.split()
    span = end_ms - start_ms
    if not words or span <= 0:
        return ()

    weights = [max(len(word), 1) for word in words]
    total_weight = sum(weights)

    timings: list[WordTiming] = []
    cursor = start_ms
    cumulative = 0
    last = len(words) - 1
    for i, (word, weight) in enumerate(zip(words, weights)):
        cumulative += weight
        if i == last:
            boundary = end_ms
        else:
            boundary = start_ms + int(round(span * cumulative / total_weight))
        # Keep boundaries monotonic and inside the span despite rounding.
        boundary = min(max(boundary, cursor), end_ms)
        timings.append(WordTiming(text=word, start_ms=cursor, end_ms=boundary))
        cursor = boundary
    return tuple(timings)


# --- Topic grouping ---


def _visual_key(segment: ScriptPlanSegment) -> tuple[str, str | None]:
    """Group key for a topic run: the Layer 1 visual context."""
    repo = segment.repo_url if segment.visual_mode is VisualMode.REPO else None
    return (segment.visual_mode.value, repo)


def _build_topics(utterances: Sequence[UtteranceTiming]) -> tuple[TopicRange, ...]:
    """Group consecutive utterances by shared visual context into topic ranges."""
    topics: list[TopicRange] = []
    run: list[UtteranceTiming] = []
    run_key: tuple[str, str | None] | None = None

    def flush() -> None:
        if not run:
            return
        first, last = run[0], run[-1]
        topics.append(
            TopicRange(
                visual_mode=first.visual_mode,
                repo_url=first.repo_url,
                section_id=first.section_id,
                start_ms=first.start_ms,
                end_ms=last.end_ms,
                utterance_indices=tuple(u.index for u in run),
            )
        )

    for utterance in utterances:
        key = (
            utterance.visual_mode.value,
            utterance.repo_url if utterance.visual_mode is VisualMode.REPO else None,
        )
        if run and key != run_key:
            flush()
            run = []
        run.append(utterance)
        run_key = key
    flush()
    return tuple(topics)


# --- Public API ---


def extract_realized_audio_metadata(
    plan: ScriptPlan,
    segment_durations: Sequence[float],
    *,
    gap_seconds: float = DEFAULT_GAP_SECONDS,
    speech_offset_seconds: float = 0.0,
    host_labels: Sequence[str] | None = None,
) -> RealizedAudioMetadata:
    """Build Layer 2 realized audio metadata from a plan and measured durations.

    Args:
        plan: The Layer 1 :class:`~podcaster.script_plan.ScriptPlan`. Its
            ``segments`` are in spoken order and parallel to ``segment_durations``.
        segment_durations: Realized duration (seconds) of each synthesized host
            turn, e.g. from :func:`podcaster.audio.probe_segment_durations`. Must
            be the same length as ``plan.segments``.
        gap_seconds: Inter-segment silence used when stitching speech, so the
            computed timeline matches the final mix.
        speech_offset_seconds: Lead-in before speech starts (e.g. intro-music
            full-volume period). Added to every timestamp.
        host_labels: Optional ``(host_a_label, host_b_label)`` to pin the
            normalized ``speaker_id`` mapping. When omitted, speaker ids are
            assigned by first appearance (lead host → ``host_a``).

    Returns:
        A :class:`RealizedAudioMetadata` with utterance, word, and topic timing.

    Raises:
        RealizedAudioMetadataError: when ``segment_durations`` is not parallel to
            ``plan.segments``, or any duration is negative.
    """
    if len(segment_durations) != len(plan.segments):
        raise RealizedAudioMetadataError(
            "segment_durations must be parallel to plan.segments "
            f"({len(segment_durations)} durations vs {len(plan.segments)} segments)"
        )
    if any(duration < 0 for duration in segment_durations):
        raise RealizedAudioMetadataError("segment durations must be non-negative")
    if speech_offset_seconds < 0:
        raise RealizedAudioMetadataError("speech_offset_seconds must be non-negative")

    durations = list(segment_durations)
    starts, total = compute_segment_timeline(durations, gap_seconds)
    offset_ms = _to_ms(speech_offset_seconds)
    speaker_ids = _resolve_speaker_ids(plan.segments, host_labels)

    utterances: list[UtteranceTiming] = []
    for segment, start, duration in zip(plan.segments, starts, durations):
        start_ms = _to_ms(start + speech_offset_seconds)
        end_ms = _to_ms(start + duration + speech_offset_seconds)
        utterances.append(
            UtteranceTiming(
                index=segment.index,
                speaker=segment.speaker,
                speaker_id=speaker_ids[segment.speaker.strip().lower()],
                text=segment.text,
                start_ms=start_ms,
                end_ms=end_ms,
                visual_mode=segment.visual_mode,
                repo_url=segment.repo_url,
                section_id=segment.section_id,
                words=distribute_word_timings(segment.text, start_ms, end_ms),
            )
        )

    metadata = RealizedAudioMetadata(
        utterances=tuple(utterances),
        topics=_build_topics(utterances),
        gap_ms=_to_ms(gap_seconds),
        speech_offset_ms=offset_ms,
        total_duration_ms=_to_ms(total + speech_offset_seconds),
        schema_version=AUDIO_METADATA_SCHEMA_VERSION,
    )

    if not utterances:
        logger.warning("realized audio metadata has no utterances (empty plan)")
    elif not metadata.repo_topics:
        logger.warning(
            "realized audio metadata declares no repo topics — check Layer 1 plan"
        )
    return metadata
