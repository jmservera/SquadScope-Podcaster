"""Interaction layer for natural-sounding audio (issue #419, Phase A).

This module builds a lightweight *interaction map* that lives alongside — and is
deliberately decoupled from — the main two-host script. The main script is
generated and synthesized unchanged; this layer only adds short *backchannel*
reactions ("right", "yeah", "exactly", ...) timed to natural pause points and
mixed quietly under the main speaker by :mod:`podcaster.audio`.

Phase A only. Phase B (scripted cut-ins) and Phase C (word-aligned true
interruptions) are future work. The data model here is intentionally forward
compatible: ``Interaction.type`` and the ``anchor`` structure leave room for the
later "cut_in" / word-timestamp anchoring without breaking the Phase A schema.

Density rules enforced here (per the issue):
- At most one backchannel per ``min_gap``..``max_gap`` seconds of speech.
- Never placed over numbers, repo names, URLs, punchlines, or technical terms.
- Only at natural pauses / clause boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from podcaster.config import BackchannelConfig

HOST_A = "host_a"
HOST_B = "host_b"

# Sentence/clause boundary punctuation used to detect natural pause points.
_CLAUSE_BOUNDARY = re.compile(r"[.,;:!?]")
# A URL anywhere in the surrounding text.
_URL_RE = re.compile(r"https?://|www\.|\b\w+\.(?:com|org|io|net|dev|ai|gg)\b", re.IGNORECASE)
# owner/repo style references.
_REPO_RE = re.compile(r"\b[\w.-]+/[\w.-]+\b")
# A bare or grouped number (including versions like 3.11, 1,000, percentages).
_NUMBER_RE = re.compile(r"\d")
# Technical-term heuristics: CamelCase, snake_case, dotted.paths, ALLCAPS
# acronyms, code spans in backticks, and a small keyword set.
_CAMEL_RE = re.compile(r"\b[a-z]+[A-Z]\w*\b")
_SNAKE_RE = re.compile(r"\b\w+_\w+\b")
_DOTTED_RE = re.compile(r"\b\w+\.\w+")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")
_CODE_SPAN_RE = re.compile(r"`[^`]+`")
_TECH_KEYWORDS = frozenset(
    {
        "api",
        "apis",
        "sdk",
        "cli",
        "json",
        "yaml",
        "http",
        "https",
        "tcp",
        "udp",
        "sql",
        "regex",
        "kubernetes",
        "k8s",
        "docker",
        "ffmpeg",
        "tts",
        "llm",
        "gpu",
        "cpu",
        "ram",
        "oauth",
        "jwt",
        "ssh",
        "tls",
        "url",
        "uri",
        "uuid",
        "ast",
        "repo",
        "repos",
        "commit",
        "branch",
        "merge",
        "endpoint",
        "schema",
        "payload",
        "embedding",
        "embeddings",
        "tokenizer",
        "transformer",
        "backchannel",
    }
)

# Tone -> ordered phrase preferences (intersected with the configured library).
_TONE_PREFERENCES: dict[str, tuple[str, ...]] = {
    "agreeing": ("right", "yeah", "exactly", "that's true"),
    "affirming": ("exactly", "that's true", "right", "yeah"),
    "interested": ("interesting", "oh wow", "hmm"),
    "surprised": ("oh wow", "interesting", "hmm"),
    "thinking": ("hmm", "interesting", "right"),
}
_DEFAULT_TONE_CYCLE = ("agreeing", "interested", "thinking")


@dataclass(frozen=True)
class Turn:
    """A single spoken turn of the main script assigned to one host."""

    turn_id: str
    speaker: str
    text: str


@dataclass(frozen=True)
class Interaction:
    """One entry of the interaction map (a Phase A backchannel).

    Mirrors the JSON shape in issue #419. ``type`` is always ``"backchannel"``
    in Phase A; the field exists so Phase B/C ("cut_in", "interruption") can
    reuse the same schema.
    """

    speaker: str
    under_turn_id: str
    anchor_text: str
    text: str
    tone: str
    gain_db: float
    max_duration_ms: int
    type: str = "backchannel"
    anchor_mode: str = "after_text"

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "speaker": self.speaker,
            "under_turn_id": self.under_turn_id,
            "anchor": {"mode": self.anchor_mode, "text": self.anchor_text},
            "text": self.text,
            "tone": self.tone,
            "gain_db": self.gain_db,
            "max_duration_ms": self.max_duration_ms,
        }


@dataclass(frozen=True)
class InteractionMap:
    """Ordered collection of interactions plus its serialization helpers."""

    interactions: tuple[Interaction, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return bool(self.interactions)

    def __len__(self) -> int:
        return len(self.interactions)

    def __iter__(self):
        return iter(self.interactions)

    def to_dict(self) -> dict[str, object]:
        return {"interactions": [i.to_dict() for i in self.interactions]}


def assign_turn_ids(segments: Iterable[tuple[str, str]]) -> list[Turn]:
    """Assign stable turn IDs to ``(speaker, text)`` script segments.

    IDs follow the issue's ``"a_014"`` convention: a speaker letter plus the
    zero-padded global line index, so they remain stable and human-readable.
    """

    turns: list[Turn] = []
    for index, (speaker, text) in enumerate(segments):
        # Normalize once (with stripping) and derive both the turn-id letter and
        # the stored speaker from that single value, so trailing whitespace or
        # casing (e.g. "host_b ") can't make the id disagree with the speaker.
        normalized = _normalize_speaker(speaker)
        letter = "b" if normalized == HOST_B else "a"
        turns.append(Turn(turn_id=f"{letter}_{index:03d}", speaker=normalized, text=text))
    return turns


def _normalize_speaker(speaker: str) -> str:
    return HOST_B if str(speaker).strip().lower().endswith("b") else HOST_A


def _other_speaker(speaker: str) -> str:
    return HOST_A if speaker == HOST_B else HOST_B


def find_pause_points(text: str) -> list[tuple[int, str]]:
    """Return ``(char_end_index, anchor_text)`` candidates at clause boundaries.

    ``char_end_index`` is the position *after* the boundary punctuation (the
    natural breath point); ``anchor_text`` is the clause's trailing words, used
    by the audio layer to anchor the backchannel and by the safety filter.
    """

    points: list[tuple[int, str]] = []
    last = 0
    for match in _CLAUSE_BOUNDARY.finditer(text):
        end = match.end()
        clause = text[last:end].strip()
        last = end
        if not clause:
            continue
        anchor = _trailing_anchor(clause)
        if anchor:
            points.append((end, anchor))
    return points


def _trailing_anchor(clause: str, *, max_words: int = 4) -> str:
    words = re.findall(r"[\w'/.\-]+", clause)
    if not words:
        return ""
    return " ".join(words[-max_words:])


def is_safe_anchor(anchor_text: str) -> bool:
    """Whether a backchannel may be placed right after ``anchor_text``.

    Enforces the issue's "never over numbers / repo names / URLs / technical
    terms" rule. Punchline avoidance is handled separately at the turn level.
    """

    if not anchor_text:
        return False
    if _URL_RE.search(anchor_text):
        return False
    if _NUMBER_RE.search(anchor_text):
        return False
    if _REPO_RE.search(anchor_text):
        return False
    if _CODE_SPAN_RE.search(anchor_text):
        return False
    if _CAMEL_RE.search(anchor_text):
        return False
    if _SNAKE_RE.search(anchor_text):
        return False
    if _DOTTED_RE.search(anchor_text):
        return False
    if _ACRONYM_RE.search(anchor_text):
        return False
    lowered = {w.lower() for w in re.findall(r"[A-Za-z']+", anchor_text)}
    if lowered & _TECH_KEYWORDS:
        return False
    return True


def _select_phrase_and_tone(library: tuple[str, ...], slot: int) -> tuple[str, str]:
    """Deterministically choose a (phrase, tone) for the ``slot``-th backchannel."""

    tone = _DEFAULT_TONE_CYCLE[slot % len(_DEFAULT_TONE_CYCLE)]
    for candidate in _TONE_PREFERENCES.get(tone, ()):  # honor tone preference
        if candidate in library:
            return candidate, tone
    # Fallback: rotate through the library so output stays varied + deterministic.
    return library[slot % len(library)], tone


#: Fractions of the ``[min_gap, max_gap]`` window used as the required spacing
#: before each successive backchannel.  Cycling through these makes the cadence
#: irregular (natural) instead of metronomic while staying fully deterministic;
#: every value keeps the spacing within the configured window (issue #419).
_GAP_WINDOW_CYCLE: tuple[float, ...] = (0.0, 0.6, 0.25, 1.0, 0.4)


def build_interaction_map(
    turns: list[Turn],
    durations: list[float],
    config: BackchannelConfig,
    *,
    gap_seconds: float = 0.35,
) -> InteractionMap:
    """Produce a density-limited :class:`InteractionMap` for ``turns``.

    ``durations[i]`` is the synthesized length (seconds) of ``turns[i]``. The
    function walks candidate pause points in playback order and places a
    backchannel only when:

    - the feature is enabled,
    - the candidate sits at a safe clause boundary (:func:`is_safe_anchor`),
    - it is not in the final clause of a turn (punchline avoidance), and
    - at least the current required gap has elapsed since the last placement.
      The required gap varies deterministically across the
      ``[min_gap_seconds, max_gap_seconds]`` window (see
      :data:`_GAP_WINDOW_CYCLE`) so consecutive backchannels are spaced
      irregularly and the rhythm sounds natural rather than metronomic — both
      bounds therefore affect placement density.

    Returns an empty map when disabled, so callers can always call it safely.
    """

    if not config.enabled:
        return InteractionMap()
    if not turns:
        return InteractionMap()
    if len(durations) != len(turns):
        raise ValueError("durations must be parallel to turns")

    gain_db = config.clamped_gain_db
    gap_window = max(0.0, config.max_gap_seconds - config.min_gap_seconds)
    placed: list[Interaction] = []
    last_time = -float("inf")
    slot = 0

    starts = _turn_starts(durations, gap_seconds)
    for turn, start, duration in zip(turns, starts, durations):
        if duration <= 0 or not turn.text.strip():
            continue
        text_len = len(turn.text)
        candidates = find_pause_points(turn.text)
        # Drop the very last clause boundary of a turn — likely a punchline /
        # closing beat where a backchannel reads as performative.
        if candidates:
            candidates = candidates[:-1]
        for char_index, anchor_text in candidates:
            if not is_safe_anchor(anchor_text):
                continue
            # Estimate absolute time of this pause via character proportion.
            position = start + duration * (char_index / text_len)
            # Required spacing varies across the [min_gap, max_gap] window so the
            # cadence isn't metronomic (issue #419).
            fraction = _GAP_WINDOW_CYCLE[slot % len(_GAP_WINDOW_CYCLE)]
            required_gap = config.min_gap_seconds + gap_window * fraction
            if position - last_time < required_gap:
                continue
            phrase, tone = _select_phrase_and_tone(config.library, slot)
            placed.append(
                Interaction(
                    speaker=_other_speaker(turn.speaker),
                    under_turn_id=turn.turn_id,
                    anchor_text=anchor_text,
                    text=phrase,
                    tone=tone,
                    gain_db=gain_db,
                    max_duration_ms=config.max_duration_ms,
                )
            )
            last_time = position
            slot += 1
            break  # at most one backchannel per turn keeps placement sparse

    return InteractionMap(tuple(placed))


def _turn_starts(durations: list[float], gap_seconds: float) -> list[float]:
    starts: list[float] = []
    current = 0.0
    for index, duration in enumerate(durations):
        starts.append(current)
        current += max(0.0, duration)
        if gap_seconds > 0 and index < len(durations) - 1:
            current += gap_seconds
    return starts


@dataclass(frozen=True)
class BackchannelPlacement:
    """A resolved backchannel ready for audio mixing.

    Combines an :class:`Interaction` with its synthesized clip bytes and the
    absolute start time (seconds) in the assembled speech timeline.
    """

    start_seconds: float
    gain_db: float
    max_duration_ms: int
    clip: bytes
    interaction: Interaction


def resolve_placements(
    interaction_map: InteractionMap,
    turns: list[Turn],
    durations: list[float],
    clips: dict[object, bytes],
    *,
    gap_seconds: float = 0.35,
) -> list[BackchannelPlacement]:
    """Resolve interactions to absolute-timed placements for :mod:`podcaster.audio`.

    ``clips`` maps either ``(speaker, text)`` or just ``text`` to synthesized
    audio bytes (small TTS clips from the configured library). Interactions
    whose clip is missing are skipped so a partial clip set degrades gracefully.

    Raises ``ValueError`` when *durations* is not parallel to *turns* (the same
    precondition :func:`build_interaction_map` enforces), so a length mismatch
    fails loudly instead of silently dropping or mis-timing placements.
    """

    if len(durations) != len(turns):
        raise ValueError("durations must be parallel to turns")

    starts = _turn_starts(durations, gap_seconds)
    by_id = {
        turn.turn_id: (start, duration) for turn, start, duration in zip(turns, starts, durations)
    }
    placements: list[BackchannelPlacement] = []
    for interaction in interaction_map:
        clip = clips.get((interaction.speaker, interaction.text)) or clips.get(interaction.text)
        if not clip:
            continue
        located = by_id.get(interaction.under_turn_id)
        if located is None:
            continue
        start, duration = located
        position = _anchor_time(interaction, start, duration, turns)
        placements.append(
            BackchannelPlacement(
                start_seconds=position,
                gain_db=interaction.gain_db,
                max_duration_ms=interaction.max_duration_ms,
                clip=clip,
                interaction=interaction,
            )
        )
    placements.sort(key=lambda p: p.start_seconds)
    return placements


def _anchor_time(
    interaction: Interaction,
    start: float,
    duration: float,
    turns: list[Turn],
) -> float:
    turn_text = next((t.text for t in turns if t.turn_id == interaction.under_turn_id), "")
    if not turn_text:
        return start
    idx = turn_text.find(interaction.anchor_text)
    if idx < 0:
        return start + duration  # anchor not found -> end of turn
    char_end = idx + len(interaction.anchor_text)
    return start + duration * (char_end / len(turn_text))
