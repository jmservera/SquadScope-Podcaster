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

from podcaster.repo_naming import naturalize_name, repo_name_from_slug
from podcaster.sections import (
    DEFAULT_TITLE_CARD_DURATION_SECONDS,
    ScriptSection,
    match_section_header,
    parse_script_sections,
)
from podcaster.sections import (
    host_labels as _host_labels,
)
from podcaster.sections import (
    script_body as _script_body,
)
from podcaster.sections import (
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
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]*[A-Za-z0-9_-])",
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
        additional_repo_urls: Later repositories named in the same spoken turn
            after ``repo_url``. These are auxiliary visual candidates for long
            multi-repo section openings; the first-cue ``repo_url`` remains the
            authoritative sync anchor.
        section_id: The id of the enclosing :class:`ScriptSection`, or ``None``
            for turns before the first ``## Section:`` header (the cold open).
    """

    index: int
    speaker: str
    text: str
    visual_mode: VisualMode
    repo_url: str | None = None
    additional_repo_urls: tuple[str, ...] = field(default_factory=tuple)
    section_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "speaker": self.speaker,
            "text": self.text,
            "visual_mode": self.visual_mode.value,
            "repo_url": self.repo_url,
            "additional_repo_urls": list(self.additional_repo_urls),
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
            additional_repo_urls=tuple(str(url) for url in data.get("additional_repo_urls", [])),
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
        segments = tuple(ScriptPlanSegment.from_dict(seg) for seg in data.get("segments", []))
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


def _first_repo_root(text: str) -> str | None:
    """Return the first canonical ``https://github.com/owner/repo`` root in *text*.

    Lenient extraction (mirrors :data:`_GITHUB_URL_RE`) normalised to a clean repo
    root so the synthesized marker passes :data:`_GITHUB_REPO_ROOT_RE` validation.
    Returns ``None`` when *text* has no GitHub repo URL.
    """
    match = _GITHUB_URL_RE.search(text or "")
    if match is None:
        return None
    owner, repo = match.group(1), match.group(2)
    repo = repo[:-4] if repo.lower().endswith(".git") else repo
    return f"https://github.com/{owner}/{repo}"


def _known_repo_urls(script: str) -> dict[str, str]:
    """Map ``owner/repo`` (lowercased) → canonical URL for every repo in *script*.

    The authoritative repo set is harvested from the full ``github.com/owner/repo``
    URLs that appear anywhere in the script — including the non-spoken
    ``Repos featured:`` header — so the spoken dialogue can be matched against
    real repos even when it names them as bare ``owner/repo`` slugs (#558).
    First occurrence wins, preserving the canonical casing of the URL.
    """
    mapping: dict[str, str] = {}
    for match in _GITHUB_URL_RE.finditer(script or ""):
        owner, repo = match.group(1), match.group(2)
        repo = repo[:-4] if repo.lower().endswith(".git") else repo
        repo = repo.rstrip(".")
        if not repo:
            continue
        key = f"{owner.lower()}/{repo.lower()}"
        mapping.setdefault(key, f"https://github.com/{owner}/{repo}")
    return mapping


# A cue-matchable spoken name must be specific enough that finding it in host
# prose reliably means "the host just named this repo" rather than an incidental
# common word. Short/blank names are dropped (matching by the ``owner/repo`` slug
# still covers them when the hosts happen to read the slug aloud).
_MIN_SPOKEN_MATCH_CHARS = 3


def _spoken_name_matchers(script: str) -> dict[str, str]:
    """Map each repo's **spoken natural name** (lowercased) → canonical repo URL.

    #627/#628 made the hosts say a repo's natural product name ("DeepSpec")
    instead of its raw ``owner/repo`` slug. Cue detection
    (:func:`_first_named_repo`) previously only matched the slug, so once the
    spoken script no longer contained the slug it could not find where a host
    first names a repo — every repo window collapsed and the article segment
    never got truncated by a repo cue (#631). This reconstructs the same
    network-free natural name the hosts now say
    (:func:`podcaster.repo_naming.naturalize_name` of the repo name after the
    ``/``) for every known repo so those cues resolve again.

    Names shorter than :data:`_MIN_SPOKEN_MATCH_CHARS` are skipped to avoid
    matching incidental prose. If several repos naturalize to the same spoken
    name, first occurrence wins so the earliest canonical URL is retained.
    """
    slugs = _known_repo_urls(script)
    matchers: dict[str, str] = {}
    for key, url in slugs.items():
        slug = key.split("/", 1)[-1]
        spoken = naturalize_name(repo_name_from_slug(slug)).lower()
        if len(spoken) < _MIN_SPOKEN_MATCH_CHARS:
            continue
        matchers.setdefault(spoken, url)
    return matchers


def _boundary_before(text: str, idx: int) -> bool:
    """True if the slug match starting at *idx* is not glued to a preceding token.

    Alphanumerics always continue a token. A ``.``/``-``/``_`` only continues the
    token when itself preceded by another word character (e.g. ``foo-acme/eve``);
    leading punctuation is treated as a boundary.
    """
    if idx <= 0:
        return True
    ch = text[idx - 1]
    if ch.isalnum():
        return False
    if ch in "._-":
        prev = text[idx - 2] if idx - 2 >= 0 else ""
        return not prev.isalnum()
    return True


def _boundary_after(text: str, pos: int) -> bool:
    """True if a slug match ending just before *pos* is not glued to a following token.

    Alphanumerics always continue a token. A ``.``/``-``/``_`` only continues the
    token when itself followed by an alphanumeric (e.g. ``owner/repo-old``,
    ``owner/next.js``); trailing punctuation such as an end-of-sentence period is
    a boundary, so ``vercel/eve.`` still matches ``vercel/eve`` (#558).
    """
    if pos >= len(text):
        return True
    ch = text[pos]
    if ch.isalnum():
        return False
    if ch in "._-":
        nxt = text[pos + 1] if pos + 1 < len(text) else ""
        return not nxt.isalnum()
    return True


def _first_named_repo(text: str, known: dict[str, str]) -> str | None:
    """Return the canonical URL of the first *known* repo named in *text*.

    "Named" means an inline full GitHub URL, a bare ``owner/repo`` slug, or the
    repo's spoken natural name (#631) — any match key in the authoritative
    *known* set (case-insensitive), bounded so a key is not matched inside a
    longer token (e.g. ``owner/repo`` must not match ``owner/repo-old``). When
    several repos are named, the earliest by character position wins; ties break
    toward the longer (more specific) then lexicographically smaller key for
    determinism. Returns ``None`` when no known repo is named.
    """
    if not text or not known:
        return None
    lowered = text.lower()
    best: tuple[int, int, str, str] | None = None
    for key, url in known.items():
        start = 0
        while True:
            idx = lowered.find(key, start)
            if idx < 0:
                break
            after_pos = idx + len(key)
            if _boundary_before(lowered, idx) and _boundary_after(lowered, after_pos):
                candidate = (idx, -len(key), key, url)
                if best is None or candidate < best:
                    best = candidate
                break  # earliest occurrence of this slug is enough
            start = idx + 1
    return best[3] if best is not None else None


def _all_named_repos(text: str, known: dict[str, str]) -> tuple[str, ...]:
    """Return all known repos named in *text*, ordered by first mention.

    This reuses the same bounded matching semantics as :func:`_first_named_repo`
    but preserves every distinct canonical URL. It is intentionally auxiliary:
    callers still use :func:`_first_named_repo` for the first visual cue so the
    existing first-spoken-cue synchronization remains unchanged.
    """
    if not text or not known:
        return ()
    lowered = text.lower()
    matches: list[tuple[int, int, str, str]] = []
    for key, url in known.items():
        start = 0
        while True:
            idx = lowered.find(key, start)
            if idx < 0:
                break
            after_pos = idx + len(key)
            if _boundary_before(lowered, idx) and _boundary_after(lowered, after_pos):
                matches.append((idx, -len(key), key, url))
                break
            start = idx + 1

    ordered: list[str] = []
    seen: set[str] = set()
    for _, _, _, url in sorted(matches):
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(url)
    return tuple(ordered)


def infer_repo_visual_markers(script: str, podcast_config: Any = None) -> str:
    """Backfill ``## Visual: repo <url>`` markers from inline GitHub links.

    The script-generation prompt asks the model to declare explicit ``## Visual:``
    markers, but models routinely express repositories as inline links
    (``[owner/repo](https://github.com/owner/repo)``) or, very commonly, as bare
    ``owner/repo`` slugs in the spoken prose (``vercel/eve is the cleanest
    anchor``) with the full URLs living only in a non-spoken ``Repos featured:``
    header. The video pipeline derives repo cards *only* from explicit markers
    (see :func:`parse_script_plan`), so an otherwise good dialogue renders with
    **no** repo visuals — or, worse, falls back to mention/header-order timing
    that shows each repo far from when the hosts actually name it (#558).

    This deterministic pass first harvests the authoritative repo set from every
    ``github.com/owner/repo`` URL in the script (header included), then walks the
    dialogue body in order and, whenever a host turn **first names** one of those
    repos — by inline URL or bare ``owner/repo`` slug — that is not already the
    repo shown by the in-effect ``## Visual: repo`` marker, injects the marker
    just before that turn. Anchoring the marker to the first spoken naming makes
    the repo's Layer-2 topic (and therefore its on-screen window) start at the
    measured audio time of that turn — audio as the master timeline.

    When a single turn names several repos, only the first-named repo gets the
    marker for that turn; the others get their own window if/when the discussion
    narrows to them in a later turn. The article/weekly lead-in naturally fills
    everything before the first repo is named (those turns stay ``article``).

    It is **idempotent**: turns already covered by a matching marker are left
    untouched, so scripts where the model *did* emit markers are unchanged.
    """
    if not script or not script.strip():
        return script

    known = _known_repo_urls(script)
    spoken_names = _spoken_name_matchers(script)
    host_labels = _host_labels(script, podcast_config)
    header, separator, body = script.partition("\n---")
    prefix = header + separator if separator else ""
    marker_source = body if separator else script
    effective_repo_url: str | None = None
    # Repos whose window has already been established (by an explicit marker or a
    # prior cue). A repo's spoken natural name — a weaker signal that recurs in
    # ordinary prose — only anchors its *first* window; it never re-opens an
    # already-shown repo, so passing mentions ("how DeepSpec will evolve") don't
    # spuriously flip the on-screen focus (#631).
    seen_repo_urls: set[str] = set()
    out: list[str] = []
    for raw_line in marker_source.splitlines():
        line = raw_line.strip()

        marker = match_visual_marker(line) if line else None
        if marker is not None:
            mode, url = marker
            effective_repo_url = url if mode is VisualMode.REPO else None
            if effective_repo_url is not None:
                seen_repo_urls.add(effective_repo_url)
            out.append(raw_line)
            continue

        speaker_text = _split_speaker(line, host_labels) if line else None
        if speaker_text is not None:
            spoken = speaker_text[1]
            # Strong signal first: an inline full URL or a bare ``owner/repo``
            # slug the host actually reads aloud. This keeps the pre-#628
            # behaviour (and its re-open semantics) exactly.
            repo_url = _first_named_repo(spoken, known) if known else None
            if repo_url is None and not known:
                repo_url = _first_repo_root(spoken)
            # Weaker signal: the repo's spoken natural name ("DeepSpec"). Only
            # used to anchor a repo the first time it is named, so it restores
            # cue detection for #627/#628 scripts without re-opening windows.
            if repo_url is None and spoken_names:
                candidate = _first_named_repo(spoken, spoken_names)
                if candidate is not None and candidate not in seen_repo_urls:
                    repo_url = candidate
            if repo_url is not None:
                seen_repo_urls.add(repo_url)
                if repo_url != effective_repo_url:
                    out.append(f"{VISUAL_MARKER_PREFIX} repo {repo_url}")
                    effective_repo_url = repo_url

        out.append(raw_line)

    result = prefix + "\n".join(out)
    if script.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


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
    if not script or not script.strip():
        return ScriptPlan(sections=tuple(parse_script_sections(script, podcast_config)))

    # Be defensive at parse time, not only at generation time. Some persisted
    # scripts contain one explicit repo marker followed by later bare
    # ``owner/repo`` mentions; without this backfill the first marker stays in
    # effect and Layer 2 collapses all subsequent repo cues into that first repo.
    script = infer_repo_visual_markers(script, podcast_config)

    plan_sections = tuple(parse_script_sections(script, podcast_config))
    host_labels = _host_labels(script, podcast_config)
    body = _script_body(script)
    known = _known_repo_urls(script)
    spoken_names = _spoken_name_matchers(script)

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
        additional_repo_urls: tuple[str, ...] = ()
        if current_mode is VisualMode.REPO and current_repo_url:
            named = list(_all_named_repos(text, known))
            named.extend(
                url
                for url in _all_named_repos(text, spoken_names)
                if url.lower() not in {existing.lower() for existing in named}
            )
            named_keys = [url.lower() for url in named]
            current_key = current_repo_url.lower()
            if current_key in named_keys:
                pos = named_keys.index(current_key)
                additional_repo_urls = tuple(named[pos + 1 :])
        segments.append(
            ScriptPlanSegment(
                index=index,
                speaker=speaker,
                text=text,
                visual_mode=current_mode,
                repo_url=current_repo_url if current_mode is VisualMode.REPO else None,
                additional_repo_urls=additional_repo_urls,
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
        "- Before the host turns that discuss a specific GitHub repository, emit a non-spoken line:\n"  # noqa: E501
        '  "## Visual: repo https://github.com/<owner>/<repo>" (use the real repo URL).\n'
        '- When the hosts step back to the weekly rundown or source article, emit "## Visual: article".\n'  # noqa: E501
        '- For an intentional breather between topic clusters, emit "## Visual: intermission".\n'
        '- A "## Visual:" marker stays in effect for every following host turn until the next marker.\n'  # noqa: E501
        "- Place a marker whenever the on-screen focus changes; every repo you discuss MUST have its own\n"  # noqa: E501
        '  "## Visual: repo <url>" marker — never rely on the URL merely appearing in the dialogue.\n'  # noqa: E501
        "- Intermission is an EXPLICIT choice, never just the absence of a repo. Only use it deliberately.\n"  # noqa: E501
        '- The "## Visual:" lines are NON-SPOKEN and are stripped before audio synthesis.\n'
    )
