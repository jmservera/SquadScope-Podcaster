"""Layer 1 — Script Plan Metadata schema (jmservera/SquadScope-Coordinator#32, #485).

The **script plan** is the first of three layers in the Phase 4 audio–video
synchronization architecture::

    (1) Script Plan Metadata  →  (2) Realized Audio Metadata  →  (3) Edit Decision List

Its purpose is to make the script *declare its visual intent explicitly* instead
of relying on post-hoc NLP inference (regex repo-URL scraping, "no repo URL ⇒
generic background", …). Each spoken segment carries an explicit
:class:`VisualMode` so downstream layers can deterministically decide what the
video should show:

* ``repo``         — show a specific GitHub repository (``repo_url`` required).
* ``article``      — show the source article / weekly page.
* ``intermission`` — show an intentional intermission / breather card. This is an
  **explicit** mode, never merely "the absence of a repo reference".

Explicit markers are emitted in the script markdown as **non-spoken** header
lines, parallel to the ``## Section:`` markers from :mod:`podcaster.sections`::

    ## Section: AI Frameworks Showdown
    ## Visual: repo https://github.com/owner/repo-a
    HOST_A: This week three projects caught our eye...
    HOST_B: Right — the first one is wild.
    ## Visual: intermission
    HOST_A: Let's take a breath before the next batch.
    ## Visual: article
    HOST_B: Back to the rundown we published...

A ``## Visual:`` marker applies to every following host turn until the next
``## Visual:`` marker. Host turns before the first marker default to
:attr:`VisualMode.ARTICLE` (the source article view). Because the TTS pipeline
only synthesizes lines that start with a host label
(see :func:`podcaster.episode.parse_script_segments`), these marker lines are
inherently non-spoken; :func:`strip_visual_markers` is provided for callers that
need to scrub them from arbitrary text.

The schema is **versioned** (:data:`SCRIPT_PLAN_SCHEMA_VERSION`) and serialises
to a stable dict (:meth:`ScriptPlan.to_dict` / :meth:`ScriptPlan.from_dict`) so
Layer 2 (realized audio metadata) and Layer 3 (the EDL) can consume it without
re-parsing the markdown.

Design principle: *explicit markers > NLP inference*.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from podcaster.sections import (
    DEFAULT_TITLE_CARD_DURATION_SECONDS,
    ScriptSection,
    host_labels as _host_labels,
    match_section_header,
    parse_script_sections,
    script_body as _script_body,
    split_speaker as _split_speaker,
)

logger = logging.getLogger("podcaster.script_plan")

#: Schema version for the serialised script plan. Bump the **minor** for
#: backward-compatible additions and the **major** for breaking changes so
#: downstream layers can guard on it.
SCRIPT_PLAN_SCHEMA_VERSION = "1.0"

#: The non-spoken marker prefix emitted in the script markdown, parallel to
#: :data:`podcaster.sections.SECTION_HEADER_PREFIX`.
VISUAL_MARKER_PREFIX = "## Visual:"

#: Matches a visual-intent marker line (case-insensitive, tolerant of extra
#: ``#`` and spacing): ``## Visual: repo https://github.com/owner/repo``,
#: ``### visual - intermission``, ``## Visual: article``.
_VISUAL_MARKER_RE = re.compile(
    r"^\s*#{1,6}\s*visual\s*[:\-]\s*(?P<mode>repo|intermission|article)\b\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

#: A GitHub repo URL — mirrors :data:`podcaster.sections._GITHUB_URL_RE` so a URL
#: followed by sentence punctuation yields a clean ``owner/repo`` rather than a
#: trailing-dot slug.
_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]*[A-Za-z0-9_-])",
)

#: Anchored variant used to validate that a ``repo_url`` is a canonical repo
#: *root* (``https://github.com/owner/repo``) rather than any URL that merely
#: *starts* with one (e.g. ``.../blob/main/...``). An optional trailing slash is
#: tolerated. Downstream layers expect a clean repo root, so validation is
#: strict where extraction (:data:`_GITHUB_URL_RE`) is lenient.
_GITHUB_REPO_ROOT_RE = re.compile(
    r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]*[A-Za-z0-9_-]/?$",
)


class ScriptPlanValidationError(ValueError):
    """Raised when a script plan violates a blocking validation rule."""


class VisualMode(str, Enum):
    """What the video should show while a segment is spoken.

    ``str`` mixin so values serialise/compare as plain strings (``"repo"`` …).
    """

    REPO = "repo"
    ARTICLE = "article"
    INTERMISSION = "intermission"

    @classmethod
    def from_value(cls, value: Any) -> "VisualMode":
        """Coerce *value* (str/:class:`VisualMode`) to a :class:`VisualMode`."""
        if isinstance(value, VisualMode):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:  # pragma: no cover - defensive
            raise ScriptPlanValidationError(f"unknown visual_mode {value!r}") from exc


#: Visual mode applied to host turns that appear before any explicit marker.
DEFAULT_VISUAL_MODE = VisualMode.ARTICLE


# --- Data structures ---


@dataclass(frozen=True)
class ScriptPlanSegment:
    """One spoken host turn with its declared visual intent.

    Attributes:
        index: Zero-based position of the turn within the episode (spoken order).
        speaker: The host label as written in the script (e.g. ``"Theo"``).
        text: The spoken text for the turn.
        visual_mode: The declared :class:`VisualMode` in effect for this turn.
        repo_url: The repository URL when ``visual_mode`` is ``repo``; otherwise
            ``None``.
        section_id: The id of the enclosing :class:`ScriptSection`, or ``None``
            for turns before the first ``## Section:`` header (the cold open).
    """

    index: int
    speaker: str
    text: str
    visual_mode: VisualMode
    repo_url: str | None = None
    section_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "speaker": self.speaker,
            "text": self.text,
            "visual_mode": self.visual_mode.value,
            "repo_url": self.repo_url,
            "section_id": self.section_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScriptPlanSegment":
        return cls(
            index=int(data["index"]),
            speaker=str(data["speaker"]),
            text=str(data["text"]),
            visual_mode=VisualMode.from_value(data["visual_mode"]),
            repo_url=(str(data["repo_url"]) if data.get("repo_url") else None),
            section_id=(str(data["section_id"]) if data.get("section_id") else None),
        )


@dataclass(frozen=True)
class ScriptPlan:
    """The versioned Layer 1 plan: visual-annotated segments plus sections."""

    segments: tuple[ScriptPlanSegment, ...] = field(default_factory=tuple)
    sections: tuple[ScriptSection, ...] = field(default_factory=tuple)
    schema_version: str = SCRIPT_PLAN_SCHEMA_VERSION

    @property
    def repo_urls(self) -> tuple[str, ...]:
        """Unique repo URLs declared across the plan, in first-appearance order."""
        seen: set[str] = set()
        ordered: list[str] = []
        for seg in self.segments:
            if seg.visual_mode is VisualMode.REPO and seg.repo_url:
                key = seg.repo_url.lower()
                if key not in seen:
                    seen.add(key)
                    ordered.append(seg.repo_url)
        return tuple(ordered)

    @property
    def has_intermissions(self) -> bool:
        return any(seg.visual_mode is VisualMode.INTERMISSION for seg in self.segments)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the stable, versioned schema for downstream layers."""
        return {
            "schema_version": self.schema_version,
            "segments": [seg.to_dict() for seg in self.segments],
            "sections": [section.to_dict() for section in self.sections],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScriptPlan":
        segments = tuple(
            ScriptPlanSegment.from_dict(seg) for seg in data.get("segments", [])
        )
        sections = tuple(_section_from_dict(sec) for sec in data.get("sections", []))
        return cls(
            segments=segments,
            sections=sections,
            schema_version=str(data.get("schema_version", SCRIPT_PLAN_SCHEMA_VERSION)),
        )


def _section_from_dict(data: dict[str, Any]) -> ScriptSection:
    """Rebuild a :class:`ScriptSection` from its serialised metadata dict."""
    from podcaster.sections import TitleCard

    card = data.get("title_card") or {}
    return ScriptSection(
        id=str(data.get("id", "")),
        title=str(data.get("title", "")),
        summary=str(data.get("summary", "")),
        repo_slugs=tuple(str(s) for s in data.get("repo_slugs", [])),
        title_card=TitleCard(
            text=str(card.get("text", data.get("title", ""))),
            duration_seconds=float(
                card.get("duration_seconds", DEFAULT_TITLE_CARD_DURATION_SECONDS)
            ),
        ),
    )


# --- Marker parsing ---


def match_visual_marker(line: str) -> tuple[VisualMode, str | None] | None:
    """Return ``(visual_mode, repo_url)`` when *line* is a ``## Visual:`` marker.

    Matching is deliberately tolerant (1–6 leading ``#``, case-insensitive
    ``visual`` keyword, ``:`` or ``-`` separator, flexible spacing). For a
    ``repo`` marker the first GitHub URL in the trailing text is returned as the
    ``repo_url`` (or ``None`` if absent — caught by :func:`validate_script_plan`).
    Returns ``None`` for any line that is not a visual marker.
    """
    match = _VISUAL_MARKER_RE.match(line)
    if match is None:
        return None
    mode = VisualMode.from_value(match.group("mode"))
    repo_url: str | None = None
    if mode is VisualMode.REPO:
        url_match = _GITHUB_URL_RE.search(match.group("rest") or "")
        if url_match is not None:
            owner, repo = url_match.group(1), url_match.group(2)
            repo = repo[:-4] if repo.lower().endswith(".git") else repo
            repo_url = f"https://github.com/{owner}/{repo}"
    return mode, repo_url


def contains_visual_marker(text: str) -> bool:
    """True when *text* still contains a ``## Visual:`` marker line."""
    return any(match_visual_marker(line) is not None for line in text.splitlines())


def strip_visual_markers(text: str) -> str:
    """Remove ``## Visual:`` marker lines so they never reach TTS.

    Spoken dialogue, blank lines, and ``## Section:`` headers are preserved;
    only the non-spoken visual markers are dropped.
    """
    if not text:
        return text
    kept = [line for line in text.splitlines() if match_visual_marker(line) is None]
    result = "\n".join(kept)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


# --- Plan parsing ---


def parse_script_plan(script: str, podcast_config: Any = None) -> ScriptPlan:
    """Parse a script's spoken turns and visual markers into a :class:`ScriptPlan`.

    Walks the script body in order, tracking the most recent ``## Visual:`` marker
    and ``## Section:`` header. Each host turn becomes a
    :class:`ScriptPlanSegment` annotated with the visual mode in effect and its
    enclosing section id. Turns before the first marker default to
    :attr:`DEFAULT_VISUAL_MODE`.

    Returns an empty plan for blank input (the feature stays dormant for legacy
    scripts rather than erroring).
    """
    plan_sections = tuple(parse_script_sections(script, podcast_config))
    if not script or not script.strip():
        return ScriptPlan(sections=plan_sections)

    host_labels = _host_labels(script, podcast_config)
    body = _script_body(script)

    # Map each section title to its stable id, consumed in declaration order so
    # repeated titles still resolve to distinct sections.
    section_ids = _section_id_lookup(plan_sections)

    segments: list[ScriptPlanSegment] = []
    current_mode = DEFAULT_VISUAL_MODE
    current_repo_url: str | None = None
    current_section_id: str | None = None
    index = 0

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        title = match_section_header(line)
        if title is not None:
            current_section_id = section_ids.next_id(title)
            continue

        marker = match_visual_marker(line)
        if marker is not None:
            current_mode, current_repo_url = marker
            continue

        speaker_text = _split_speaker(line, host_labels)
        if speaker_text is None:
            continue
        speaker, text = speaker_text
        segments.append(
            ScriptPlanSegment(
                index=index,
                speaker=speaker,
                text=text,
                visual_mode=current_mode,
                repo_url=current_repo_url if current_mode is VisualMode.REPO else None,
                section_id=current_section_id,
            )
        )
        index += 1

    return ScriptPlan(segments=tuple(segments), sections=plan_sections)


class _SectionIdLookup:
    """Resolve section titles to ids, advancing through duplicate titles."""

    def __init__(self, sections: Sequence[ScriptSection]) -> None:
        self._by_title: dict[str, list[str]] = {}
        for section in sections:
            self._by_title.setdefault(section.title.strip().lower(), []).append(section.id)
        self._cursor: dict[str, int] = {}

    def next_id(self, title: str) -> str | None:
        key = title.strip().lower()
        ids = self._by_title.get(key)
        if not ids:
            return None
        cursor = self._cursor.get(key, 0)
        if cursor >= len(ids):
            cursor = len(ids) - 1
        self._cursor[key] = cursor + 1
        return ids[cursor]


def _section_id_lookup(sections: Sequence[ScriptSection]) -> _SectionIdLookup:
    return _SectionIdLookup(sections)


# --- Validation ---


def validate_script_plan(plan: ScriptPlan) -> list[str]:
    """Validate a :class:`ScriptPlan` against the Layer 1 rules.

    Blocking rules raise :class:`ScriptPlanValidationError`; soft rules are
    logged and returned as warning strings.

    Blocking:
        * every segment declares a known :class:`VisualMode`;
        * ``repo`` segments carry a well-formed GitHub ``repo_url``;
        * non-``repo`` segments carry no ``repo_url``.

    Soft (warnings): plan has no segments; plan declares no ``repo`` visuals
    (likely an inference regression); a ``repo`` URL points at an excluded
    project-owned repo is *not* checked here (that is the renderer's concern).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not plan.segments:
        warnings.append("script plan has no spoken segments")

    repo_segment_count = 0
    for seg in plan.segments:
        if not isinstance(seg.visual_mode, VisualMode):
            errors.append(f"segment {seg.index} has non-enum visual_mode {seg.visual_mode!r}")
            continue
        if seg.visual_mode is VisualMode.REPO:
            repo_segment_count += 1
            if not seg.repo_url:
                errors.append(f"segment {seg.index} is mode 'repo' but declares no repo_url")
            elif _GITHUB_REPO_ROOT_RE.match(seg.repo_url) is None:
                errors.append(
                    f"segment {seg.index} repo_url {seg.repo_url!r} is not a GitHub repo URL"
                )
        elif seg.repo_url:
            errors.append(
                f"segment {seg.index} is mode {seg.visual_mode.value!r} but declares a repo_url"
            )

    if errors:
        raise ScriptPlanValidationError("; ".join(errors))

    if plan.segments and repo_segment_count == 0:
        warnings.append("script plan declares no 'repo' visuals — check the generation prompt")

    for message in warnings:
        logger.warning("script plan: %s", message)
    return warnings


# --- Prompt guidance ---


def build_visual_marker_guidance() -> str:
    """Build the VISUAL INTENT guidance block for the script-generation prompt.

    Instructs the model to declare, per segment, what the video should show using
    explicit non-spoken ``## Visual:`` markers so downstream layers never have to
    infer visual intent from the prose.
    """
    return (
        "\nVISUAL INTENT (REQUIRED — declare what the video shows, do not leave it to inference):\n"
        "- Before the host turns that discuss a specific GitHub repository, emit a non-spoken line:\n"
        '  "## Visual: repo https://github.com/<owner>/<repo>" (use the real repo URL).\n'
        '- When the hosts step back to the weekly rundown or source article, emit "## Visual: article".\n'
        '- For an intentional breather between topic clusters, emit "## Visual: intermission".\n'
        "- A \"## Visual:\" marker stays in effect for every following host turn until the next marker.\n"
        "- Place a marker whenever the on-screen focus changes; every repo you discuss MUST have its own\n"
        '  "## Visual: repo <url>" marker — never rely on the URL merely appearing in the dialogue.\n'
        "- Intermission is an EXPLICIT choice, never just the absence of a repo. Only use it deliberately.\n"
        '- The "## Visual:" lines are NON-SPOKEN and are stripped before audio synthesis.\n'
    )
