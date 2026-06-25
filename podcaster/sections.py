"""Script sections and video title-card metadata (#417).

A generated episode script is divided into explicit **sections** so that:

* brief title cards can be shown at section boundaries in the video
  (rendered by :mod:`podcaster.video.section_cards`), and
* the hosts have natural, narratively-motivated transition points.

Section boundaries are marked in the script markdown with a non-spoken header
line::

    ## Section: AI Frameworks Showdown

    HOST_A: We found three projects this week...
    HOST_B: Exactly — what stood out to us was...

These ``## Section:`` lines are **non-spoken** — the TTS pipeline must strip
them before synthesis (see :func:`strip_section_headers` and the verification in
:func:`podcaster.episode.parse_script_segments`).

Alongside the script we produce structured section metadata
(:class:`ScriptSection` / :func:`parse_script_sections`) matching the shape
requested in the issue::

    {
      "id": "section-1",
      "title": "AI Frameworks Showdown",
      "summary": "Comparison of AI framework repos",
      "repo_slugs": ["owner/repo-a", "owner/repo-b"],
      "title_card": {"text": "AI Frameworks Showdown", "duration_seconds": 0.75}
    }

Validation (:func:`validate_sections`) enforces the blocking rules from the
issue (section count 2–6, every section has a title and at least four host
turns, no empty sections, and the TTS input never contains section headers) and
logs the soft warnings (very short sections, over-long titles, generic titles).

Related: jmservera/SquadScope-Podcaster#417 (title-card rendering: #377).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

logger = logging.getLogger("podcaster.sections")

# --- Constants ---

#: The non-spoken section header marker emitted in the script markdown.
SECTION_HEADER_PREFIX = "## Section:"

#: Default on-screen duration of a section title card, in seconds (issue #417
#: asks for 0.5–1.0 s with a 0.75 s default).
DEFAULT_TITLE_CARD_DURATION_SECONDS = 0.75

#: Approximate spoken rate (words per second) used to *estimate* how long a
#: section runs from its dialogue text (~150 wpm).
WORDS_PER_SECOND = 2.5

#: Blocking bounds on the number of sections in an episode.
MIN_SECTIONS = 2
MAX_SECTIONS = 6

#: Blocking minimum number of host turns per section.
MIN_HOST_TURNS_PER_SECTION = 4

#: Warning thresholds.
SHORT_SECTION_SECONDS = 30.0
MAX_TITLE_CHARS = 60

#: Matches a section header line (case-insensitive, tolerant of extra ``#`` and
#: spacing): ``## Section: Title`` / ``### section : Title``.
_SECTION_HEADER_RE = re.compile(
    r"^\s*#{1,6}\s*section\s*[:\-]\s*(?P<title>.+?)\s*$",
    re.IGNORECASE,
)

#: A GitHub repo URL — used to associate ``owner/repo`` slugs with a section.
#: The repo group may contain internal dots (e.g. ``repo.js``) but must not end
#: in one, so a URL followed by sentence punctuation (``.../org/repo.``) yields
#: ``org/repo`` rather than an invalid ``org/repo.`` slug.
_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]*[A-Za-z0-9_-])",
)

#: Titles that read as article headings rather than video title cards. Kept as
#: normalised (lower-case) keys; numbered variants are matched separately.
_GENERIC_TITLES = frozenset(
    {
        "introduction",
        "intro",
        "conclusion",
        "outro",
        "the end",
        "welcome",
        "wrap up",
        "wrap-up",
        "wrapup",
        "closing",
        "ending",
        "summary",
        "overview",
    }
)

#: Numbered generic titles such as "Repo 1", "Section 2", "Part 3".
_GENERIC_NUMBERED_RE = re.compile(
    r"^(?:repo|section|segment|part|topic)\s*#?\s*\d+$",
    re.IGNORECASE,
)


class SectionValidationError(ValueError):
    """Raised when a script's sections violate a blocking validation rule."""


# --- Data structures ---


@dataclass(frozen=True)
class TitleCard:
    """The visual title card shown at a section boundary."""

    text: str
    duration_seconds: float = DEFAULT_TITLE_CARD_DURATION_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "duration_seconds": self.duration_seconds}


@dataclass(frozen=True)
class ScriptSection:
    """A parsed script section with its dialogue and title-card metadata.

    Attributes:
        id: Stable identifier (``"section-1"``, ``"section-2"``, …).
        title: Section title — also the title-card text.
        summary: Short human summary derived from the section's dialogue.
        repo_slugs: ``owner/repo`` slugs referenced within the section, in first
            appearance order.
        title_card: The :class:`TitleCard` rendered at the section start.
        host_turns: Ordered ``(host_label, spoken_text)`` pairs in the section.
    """

    id: str
    title: str
    summary: str
    repo_slugs: tuple[str, ...]
    title_card: TitleCard
    host_turns: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def host_turn_count(self) -> int:
        return len(self.host_turns)

    @property
    def estimated_seconds(self) -> float:
        """Estimated spoken duration of the section, in seconds."""
        words = sum(len(text.split()) for _, text in self.host_turns)
        return words / WORDS_PER_SECOND if WORDS_PER_SECOND else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the metadata shape requested in issue #417."""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "repo_slugs": list(self.repo_slugs),
            "title_card": self.title_card.to_dict(),
        }


# --- Parsing ---


def _script_body(script: str) -> str:
    """Return the dialogue body (after the ``---`` metadata separator)."""
    _, sep, after = script.partition("\n---")
    return after if sep else script


def _host_labels(podcast_config: Any) -> tuple[str, str] | None:
    """Best-effort ``(host_a, host_b)`` labels from a podcast config."""
    if podcast_config is None:
        return None
    try:
        return podcast_config.host_a.name, podcast_config.host_b.name
    except AttributeError:
        return None


def _split_speaker(line: str, host_labels: tuple[str, str] | None) -> tuple[str, str] | None:
    """Return ``(speaker_label, text)`` when *line* is a dialogue line."""
    if host_labels is not None:
        host_a, host_b = host_labels
        for label in (host_a, host_b):
            prefix = f"{label}:"
            if line.startswith(prefix):
                text = line[len(prefix):].strip()
                return (label, text) if text else None
        # With a known config, only the configured hosts are spoken turns.
        return None
    # Generic "Speaker: text" fallback so parsing works without a config.
    match = re.match(r"^([A-Za-z][A-Za-z0-9 _'.-]{0,30}):\s*(.+)$", line)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None


def match_section_header(line: str) -> str | None:
    """Return the section *title* when *line* is a section header.

    Matching is deliberately tolerant (see :data:`_SECTION_HEADER_RE`): it
    accepts 1–6 leading ``#`` characters, a case-insensitive ``section`` keyword,
    either ``:`` or ``-`` as the separator, and flexible surrounding whitespace
    (``## Section: Title``, ``### section - Title``, …).  Returns ``None`` for
    any line that is not a section header.
    """
    match = _SECTION_HEADER_RE.match(line)
    return match.group("title").strip() if match else None


def _summarise(title: str, host_turns: Sequence[tuple[str, str]]) -> str:
    """Derive a short summary from the section's dialogue (first sentence)."""
    if not host_turns:
        return title
    first = host_turns[0][1].strip()
    # First sentence, capped, with URLs elided so the summary stays readable.
    first = _GITHUB_URL_RE.sub(lambda m: f"{m.group(1)}/{m.group(2)}", first)
    sentence = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0].strip()
    if len(sentence) > 160:
        sentence = sentence[:157].rstrip() + "..."
    return sentence or title


def _repo_slugs(host_turns: Iterable[tuple[str, str]]) -> tuple[str, ...]:
    """Extract unique ``owner/repo`` slugs from section dialogue, in order."""
    slugs: list[str] = []
    seen: set[str] = set()
    for _, text in host_turns:
        for owner, repo in _GITHUB_URL_RE.findall(text):
            repo = repo[:-4] if repo.lower().endswith(".git") else repo
            slug = f"{owner}/{repo}"
            key = slug.lower()
            if key not in seen:
                seen.add(key)
                slugs.append(slug)
    return tuple(slugs)


def parse_script_sections(
    script: str,
    podcast_config: Any = None,
    *,
    title_card_duration_seconds: float = DEFAULT_TITLE_CARD_DURATION_SECONDS,
) -> list[ScriptSection]:
    """Parse section headers and their dialogue from *script*.

    A line is treated as a section header when it matches the tolerant
    :data:`_SECTION_HEADER_RE` (canonically the :data:`SECTION_HEADER_PREFIX`
    ``## Section: <Title>`` form, but also 1–6 ``#``, case-insensitive
    ``section``, ``:`` or ``-`` separators, and flexible spacing).  Each header
    starts a new section; dialogue before the first such header is not
    attributed to any section (it is the cold open / welcome).

    Returns an empty list when the script has no section headers, so the feature
    stays dormant for legacy scripts (no error).
    """
    if not script or not script.strip():
        return []

    host_labels = _host_labels(podcast_config)
    body = _script_body(script)

    current_title: str | None = None
    current_turns: list[tuple[str, str]] = []
    collected: list[tuple[str, list[tuple[str, str]]]] = []

    def _flush() -> None:
        if current_title is not None:
            collected.append((current_title, current_turns))

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        title = match_section_header(line)
        if title is not None:
            _flush()
            current_title = title
            current_turns = []
            continue
        if current_title is None:
            continue
        speaker = _split_speaker(line, host_labels)
        if speaker is not None:
            current_turns.append(speaker)
    _flush()

    sections: list[ScriptSection] = []
    for index, (title, turns) in enumerate(collected, start=1):
        turns_tuple = tuple(turns)
        sections.append(
            ScriptSection(
                id=f"section-{index}",
                title=title,
                summary=_summarise(title, turns_tuple),
                repo_slugs=_repo_slugs(turns_tuple),
                title_card=TitleCard(text=title, duration_seconds=title_card_duration_seconds),
                host_turns=turns_tuple,
            )
        )

    if sections:
        logger.info(
            "Parsed %d script section(s): %s",
            len(sections),
            ", ".join(s.title for s in sections),
        )
    return sections


def sections_to_metadata(sections: Sequence[ScriptSection]) -> list[dict[str, Any]]:
    """Serialise sections to the issue #417 metadata list."""
    return [section.to_dict() for section in sections]


# --- TTS header stripping ---


def strip_section_headers(text: str) -> str:
    """Remove ``## Section:`` header lines so they never reach TTS.

    Spoken dialogue and blank lines are preserved; only the non-spoken section
    markers are dropped.
    """
    if not text:
        return text
    kept = [line for line in text.splitlines() if match_section_header(line) is None]
    result = "\n".join(kept)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def contains_section_header(text: str) -> bool:
    """True when *text* still contains a ``## Section:`` header line."""
    return any(match_section_header(line) is not None for line in text.splitlines())


# --- Validation ---


def _is_generic_title(title: str) -> bool:
    normalised = re.sub(r"\s+", " ", title).strip().lower()
    if normalised in _GENERIC_TITLES:
        return True
    return bool(_GENERIC_NUMBERED_RE.match(normalised))


def validate_sections(
    sections: Sequence[ScriptSection],
    *,
    tts_segments: Sequence[tuple[str, str]] | None = None,
) -> list[str]:
    """Validate parsed sections against the issue #417 rules.

    Blocking rules raise :class:`SectionValidationError`; soft rules are logged
    and returned as a list of warning strings.

    Args:
        sections: Parsed sections for the episode.
        tts_segments: Optional ``(label, text)`` pairs that will be sent to TTS;
            when provided, they are checked to ensure no section header leaked
            into the spoken text (blocking).

    Returns:
        The list of (non-blocking) warning messages.
    """
    errors: list[str] = []
    warnings: list[str] = []

    count = len(sections)
    if count < MIN_SECTIONS or count > MAX_SECTIONS:
        errors.append(
            f"section count {count} is out of range "
            f"({MIN_SECTIONS}-{MAX_SECTIONS} required)"
        )

    for section in sections:
        if not section.title.strip():
            errors.append(f"{section.id} has an empty title")
        if section.host_turn_count == 0:
            errors.append(f"{section.id} ({section.title!r}) has no host turns")
        elif section.host_turn_count < MIN_HOST_TURNS_PER_SECTION:
            errors.append(
                f"{section.id} ({section.title!r}) has only "
                f"{section.host_turn_count} host turn(s); "
                f"at least {MIN_HOST_TURNS_PER_SECTION} required"
            )

        if section.host_turns and section.estimated_seconds < SHORT_SECTION_SECONDS:
            warnings.append(
                f"{section.id} ({section.title!r}) is estimated at only "
                f"{section.estimated_seconds:.0f}s (< {SHORT_SECTION_SECONDS:.0f}s)"
            )
        if len(section.title) > MAX_TITLE_CHARS:
            warnings.append(
                f"{section.id} title is {len(section.title)} chars "
                f"(> {MAX_TITLE_CHARS}); title cards should be concise"
            )
        if _is_generic_title(section.title):
            warnings.append(
                f"{section.id} title {section.title!r} is generic; "
                "prefer a title-card-style headline"
            )

    if tts_segments is not None:
        for _, text in tts_segments:
            if contains_section_header(text):
                errors.append("TTS input contains a section header (must be stripped)")
                break

    for message in warnings:
        logger.warning("section validation: %s", message)

    if errors:
        raise SectionValidationError("; ".join(errors))

    return warnings
