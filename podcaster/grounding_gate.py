"""Deterministic, pure, network-free grounding gate over a finished podcast script.

The gate decides in code (never from a model verdict) whether each spoken turn is
tied to the bounded article / breaking-news evidence. It reuses the span resolver
and regexes from ``claim_extraction`` so the rules stay identical to the audit's
code predicates, but removes the LLM's supported/qualified/unsupported judgment.

Per spoken turn (after the code-inserted AI disclosure and host/show names are set aside):

* format: one line, no URL, no control characters, bounded length;
* ownership voice: ``find_violations`` must be empty (never "the article");
* empirical/absence/novelty assertions (``_EMPIRICAL_ASSERTION``) are rejected everywhere;
* chat turns (greeting, question, transition) must carry no number, no unsourced
  entity and no factual cue;
* every other turn is factual: it must open with authors-attribution
  (``_QUALIFICATION``), any quoted text must be a verbatim, unique evidence span, and the
  turn must resolve (``_resolve_span_for_turn``) to a single article OR breaking-news
  span containing all of its numbers and capitalised entities.

Failing turns are repaired a bounded number of times by the caller-supplied repairer and
then replaced in code by a verbatim quote-rendered turn so the loop always terminates.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from podcaster.claim_extraction import (
    _EMPIRICAL_ASSERTION,
    _QUALIFICATION,
    _TOKEN,
    REPAIR_CLASS_INSTRUCTIONS,
    Claim,
    ClaimGroundingError,
    _content_tokens,
    _exact_span_for_quote,
    _resolve_span_for_turn,
    _stem,
)
from podcaster.config import PodcastConfig
from podcaster.grounding_spans import Span, build_span_table, quote_text, spans_overlapping
from podcaster.ownership_tone import find_violations
from podcaster.provider_filter import FilterSafetyError
from podcaster.sanitization import FIELD_LIMITS, neutralize
from podcaster.script_gen import (
    TURN_REPAIR_MAX_CHARS,
    ScriptDirections,
    _normalize_spoken_markdown,
    bounded_source_evidence,
    match_section_header,
    requested_word_range,
    spoken_turns,
)

logger = logging.getLogger("podcaster.grounding_gate")

GROUNDING_MODE_ENV = "GROUNDING_MODE"
MODE_AUDIT, MODE_BOTH, MODE_CONSTRUCT = "audit", "both", "construct"
MAX_GATE_REPAIRS = 3
GATE_BUDGET_SECONDS = 200.0
ADVISORY_AUDIT_SECONDS = 45.0
ADVISORY_GRACE_SECONDS = 5.0
MAX_RENDER_CANDIDATES = 12
MAX_CHAT_WORDS = 20
MAX_RENDERED_QUOTE_CHARS = 1000

_LEAD_INS = {
    "en": 'According to the authors, "{q}"',
    "es": 'Según los autores, "{q}"',
    "fr": 'Selon les auteurs, "{q}"',
}
_FILLERS = {
    "en": "Let's move on to the next point.",
    "es": "Pasemos al siguiente punto.",
    "fr": "Passons au point suivant.",
}
_QUOTE_CHARS = '"\u201c\u201d\u00ab\u00bb'
_QUOTE_PAIRS = {'"': '"', "\u201c": "\u201d", "\u00ab": "\u00bb"}
_URL_OR_CONTROL = re.compile(r"https?://|www\.|[\x00-\x1f\x7f]", re.IGNORECASE)
_FACTUAL_CUE = re.compile(
    r"(?:\b(?:because|leads? to|led to|results? in|caused?|(?:faster|slower|better|worse|bigger|"
    r"smaller|cheaper|more|less|fewer) than|outperform\w*|percent|million|billion|thousand|"
    r"doubl\w+|tripl\w+|largest|biggest|fastest|porque|por ciento|millones|mill[oó]n|"
    r"parce que|pour cent|millions|milliards?)\b|%)",
    re.IGNORECASE,
)
_GENERIC_ENTITIES = frozenset({"AI", "I", "I'm", "I'll", "I've", "I'd", "OK"})
_DIGIT = re.compile(r"\d")

REPAIR_INSTRUCTIONS = {
    **REPAIR_CLASS_INSTRUCTIONS,
    "violation": "never say 'the article' or speak as the reporter; use 'the authors'",
    "format": "one plain spoken line: no URLs, no control characters, under 1500 characters",
    "chat": (
        "this turn is conversational: remove numbers, names and factual claims, or restate "
        "one source sentence attributed to the authors"
    ),
}


def grounding_mode() -> str:
    raw = os.environ.get(GROUNDING_MODE_ENV, "").strip().lower()
    if raw in {MODE_AUDIT, MODE_BOTH, MODE_CONSTRUCT}:
        return raw
    if raw:
        logger.warning("unknown %s value; using audit", GROUNDING_MODE_ENV)
    return MODE_AUDIT


def requested_word_bounds(directions: ScriptDirections | None) -> tuple[int, int | None] | None:
    """(minimum, maximum|None) from directions; None when no word count is stated.

    A min-only request (``at least N words``, ``N+ words``) enforces the minimum only and
    never invents a maximum. ``requested_word_range`` ignores min-only phrasing entirely.
    """

    both = requested_word_range(directions)
    if both is not None:
        return both
    if directions is None:
        return None
    text = directions.episode_style.format
    for pattern in (
        r"(?:at least|minimum(?: of)?|min\.?|no fewer than|no less than)\s*(\d[\d,]*)\s*"
        r"(?:spoken\s+)?words?\b",
        r"(?<![\d,])(\d[\d,]*)\s*\+\s*(?:spoken\s+)?words?\b",
        r"(?<![\d,])(\d[\d,]*)\s*(?:spoken\s+)?words?\s*(?:minimum|or more)\b",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            minimum = int(match.group(1).replace(",", ""))
            if minimum > 0:
                return minimum, None
    return None


@dataclass(frozen=True)
class TurnResult:
    index: int
    text: str
    kind: str  # fact | quote | chat | disclosure
    failure: str | None = None
    source_kind: str = "article"
    quote: str | None = None
    span_ids: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.failure is None


@dataclass(frozen=True)
class GateResult:
    turns: tuple[TurnResult, ...]
    global_failures: tuple[str, ...] = ()
    words: int = 0

    @property
    def failures(self) -> dict[int, str]:
        return {t.index: t.failure for t in self.turns if t.failure}

    @property
    def ok(self) -> bool:
        return not self.failures and not self.global_failures

    def summary(self) -> str:
        """Content-free summary (classes and indices only)."""

        parts = [f"turn {i}: {c}" for i, c in sorted(self.failures.items())]
        parts.extend(self.global_failures)
        return "deterministic grounding gate failed (" + "; ".join(parts) + ")"


@dataclass
class _Context:
    article: str
    news: str
    table: list[Span]
    podcast_config: PodcastConfig
    language: str
    disclosure: str
    allow: re.Pattern[str] | None
    source_stems: frozenset[str]
    hosts: tuple[str, str]


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _lang(language: str) -> str:
    code = (language or "en").lower()[:2]
    return code if code in _LEAD_INS else "en"


def _build_context(
    *,
    article_content: str,
    breaking_news: str | None,
    podcast_config: PodcastConfig,
    language: str,
    week: str,
) -> _Context:
    article, news = bounded_source_evidence(article_content, breaking_news)
    table = build_span_table(article, news)
    if not table:
        raise ClaimGroundingError("deterministic grounding gate: empty source span table")
    names = {
        podcast_config.host_a.name,
        podcast_config.host_b.name,
        podcast_config.name,
        podcast_config.spoken_site,
        week,
    }
    names = {n.strip() for n in names if n and n.strip()}
    allow = (
        re.compile(
            r"(?<!\w)(?:"
            + "|".join(re.escape(n) for n in sorted(names, key=lambda n: (-len(n), n)))
            + r")(?!\w)",
            re.IGNORECASE,
        )
        if names
        else None
    )
    stems = frozenset(_stem(m.group(0)) for m in _TOKEN.finditer(f"{article}\n{news}"))
    return _Context(
        article=article,
        news=news,
        table=table,
        podcast_config=podcast_config,
        language=_lang(language),
        disclosure=_norm(podcast_config.ai_voice_disclosure),
        allow=allow,
        source_stems=stems,
        hosts=(podcast_config.host_a.name, podcast_config.host_b.name),
    )


def _strip_disclosure(text: str, ctx: _Context) -> tuple[str, bool]:
    if not ctx.disclosure or ctx.disclosure not in _norm(text):
        return text, False
    pattern = r"\s+".join(re.escape(w) for w in ctx.podcast_config.ai_voice_disclosure.split())
    return re.sub(pattern, " ", text, flags=re.IGNORECASE), True


def _chat_entities(text: str) -> list[str]:
    """Capitalised mid-sentence or mixed-case tokens (sentence openers are ignored)."""

    found: list[str] = []
    for match in _TOKEN.finditer(text):
        raw = match.group(0)
        if raw in _GENERIC_ENTITIES or not raw[0].isalpha():
            continue
        prior = text[: match.start()].rstrip()
        sentence_start = not prior or prior[-1] in ".!?\n:\"'\u201c(\u00bf\u00a1"
        mixed = any(c.isupper() for c in raw[1:])
        if (raw[0].isupper() and not sentence_start) or mixed:
            found.append(_stem(raw))
    return found


def _quotes(text: str) -> list[str]:
    """Every quoted passage, of any length, found by a linear scan (no backtracking regex)."""

    out: list[str] = []
    missing: set[str] = set()
    i = 0
    while i < len(text):
        close = _QUOTE_PAIRS.get(text[i])
        if close is not None and close not in missing:
            end = text.find(close, i + 1)
            if end < 0:
                missing.add(close)
            else:
                quote = text[i + 1 : end].strip()
                if quote:
                    out.append(quote)
                i = end + 1
                continue
        i += 1
    return out


def _locate(ctx: _Context, quote: str) -> tuple[str, str] | None:
    for kind, evidence in (("article", ctx.article), ("breaking_news", ctx.news)):
        if evidence:
            span = _exact_span_for_quote(quote, evidence)
            if span is not None:
                return kind, span
    return None


def _ids(ctx: _Context, kind: str, span: str) -> tuple[str, ...]:
    evidence = ctx.article if kind == "article" else ctx.news
    start = evidence.find(span)
    if start < 0:
        return ()
    return tuple(spans_overlapping(ctx.table, kind, start, start + len(span)))


def _check_turn(index: int, text: str, ctx: _Context) -> TurnResult:
    def fail(kind: str, cls: str, **extra: Any) -> TurnResult:
        return TurnResult(index, text, kind, failure=cls, **extra)

    host = ctx.hosts[0]
    if (
        not text
        or len(text) > TURN_REPAIR_MAX_CHARS
        or _URL_OR_CONTROL.search(text)
        or len(text.splitlines()) != 1
    ):
        return fail("fact", "format")
    if find_violations(f"{host}: {text}"):
        return fail("fact", "violation")
    if _EMPIRICAL_ASSERTION.search(text):
        return fail("fact", "empirical")
    plain, had_disclosure = _strip_disclosure(text, ctx)
    plain = " ".join(plain.split())
    # Name exemptions apply to the conversational checks only; quotations and factual
    # spans are matched against the original wording.
    body = ctx.allow.sub(" ", plain) if ctx.allow is not None else plain
    body = " ".join(body.split())
    if not re.search(r"\w", body):
        return TurnResult(index, text, "disclosure" if had_disclosure else "chat")

    quotes = _quotes(plain)
    digits = bool(_DIGIT.search(body))
    entities = _chat_entities(body)
    cue = bool(_FACTUAL_CUE.search(body))
    question = body.rstrip().endswith("?")
    words = len(body.split())
    if not quotes and not cue:
        if question and all(e in ctx.source_stems for e in entities):
            numbers = [
                _stem(m.group(0)) for m in _TOKEN.finditer(body) if _DIGIT.search(m.group(0))
            ]
            if all(n in ctx.source_stems for n in numbers):
                return TurnResult(index, text, "chat")
        elif not digits and not entities and words <= MAX_CHAT_WORDS:
            # Short lowercase declaratives that mirror a source passage are still factual.
            if _resolve_span_for_turn(None, body, ctx.article) is None and (
                not ctx.news or _resolve_span_for_turn(None, body, ctx.news) is None
            ):
                return TurnResult(index, text, "chat")

    # Factual turn: mandatory authors-attribution, verbatim quotes, one-source resolution.
    if not _QUALIFICATION.search(text):
        return fail("fact", "qualification")
    quote_hits = []
    for quote in quotes:
        hit = _locate(ctx, quote)
        if hit is None:
            return fail("fact", "quote")
        quote_hits.append(hit)
    if len({kind for kind, _ in quote_hits}) > 1:
        return fail("fact", "quote")
    core = _QUALIFICATION.sub(" ", plain)
    if len(quotes) == 1:
        # Shortcut only for one quotation with nothing but punctuation/attribution around it.
        remainder = core.replace(quotes[0], " ")
        remainder = re.sub(f"[{re.escape(_QUOTE_CHARS)}]", " ", remainder)
        if not re.search(r"\w", remainder):
            kind, span = quote_hits[0]
            return TurnResult(
                index, text, "quote", source_kind=kind, quote=span, span_ids=_ids(ctx, kind, span)
            )
    core_text = " ".join(core.split())
    for kind, evidence in (("article", ctx.article), ("breaking_news", ctx.news)):
        if not evidence or (quote_hits and quote_hits[0][0] != kind):
            continue
        span = _resolve_span_for_turn(None, core_text, evidence)
        if span is not None:
            return TurnResult(
                index,
                text,
                "fact",
                source_kind=kind,
                quote=span,
                span_ids=_ids(ctx, kind, span),
            )
    return fail("fact", "unsupported")


def _disclosure_holders(turns: list[str], ctx: _Context) -> list[int]:
    return [i for i, t in enumerate(turns, 1) if ctx.disclosure in _norm(t)]


def _disclosure_placement_ok(holders: list[int], total: int) -> bool:
    # _format_script closes the show with a second disclosure; any other repeat is a defect.
    return bool(holders) and len(holders) <= 2 and (len(holders) == 1 or holders[1] == total)


def normalize_disclosure(script: str, *, ctx: _Context) -> str:
    """Place the approved AI disclosure deterministically (pure, idempotent).

    Only runs when the placement rule is violated. Disclosure text is removed from every turn
    other than the first and last (turns left without words are dropped), then the exact
    configured disclosure is inserted as a host_b turn at the start and/or end if missing.
    """

    config = ctx.podcast_config
    approved = config.ai_voice_disclosure.strip()
    if not ctx.disclosure or not approved:
        return script
    turns = spoken_turns(script, config)
    if not turns or _disclosure_placement_ok(_disclosure_holders(turns, ctx), len(turns)):
        return script
    header, lines = _split_body(script)
    positions = _spoken_positions(lines, config)
    if len(positions) != len(turns):
        return script
    total = len(positions)
    drop: list[int] = []
    for index, (position, label) in enumerate(positions, 1):
        if index in (1, total) or ctx.disclosure not in _norm(turns[index - 1]):
            continue
        stripped, _ = _strip_disclosure(turns[index - 1], ctx)
        stripped = " ".join(stripped.split())
        if ctx.disclosure in _norm(stripped):
            return script
        if re.search(r"\w", stripped):
            ending = "\n" if lines[position].endswith("\n") else ""
            lines[position] = f"{label}: {stripped}{ending}"
        else:
            drop.append(position)
    for position in sorted(drop, reverse=True):
        del lines[position]
    positions = _spoken_positions(lines, config)
    kept = [t for t in spoken_turns(header + "".join(lines), config)]
    if not positions or len(positions) != len(kept):
        return script
    host_b = config.host_b.name
    if ctx.disclosure not in _norm(kept[-1]):
        last = positions[-1][0]
        if not lines[last].endswith(("\n", "\r")):
            lines[last] += "\n"
        lines.insert(last + 1, f"{host_b}: {approved}\n")
    if ctx.disclosure not in _norm(kept[0]):
        lines.insert(positions[0][0], f"{host_b}: {approved}\n")
    return header + "".join(lines)


def _word_count(turns: list[str]) -> int:
    return sum(len(t.split()) for t in turns)


def _bounds_ok(words: int, bounds: tuple[int, int | None] | None) -> bool:
    return bounds is None or (words >= bounds[0] and (bounds[1] is None or words <= bounds[1]))


def check_script(
    script: str,
    *,
    article_content: str,
    breaking_news: str | None,
    podcast_config: PodcastConfig,
    language: str = "en",
    week: str = "",
    word_bounds: tuple[int, int | None] | None = None,
    _ctx: _Context | None = None,
) -> GateResult:
    """Pure deterministic check; raises ClaimGroundingError on an empty span table."""

    ctx = _ctx or _build_context(
        article_content=article_content,
        breaking_news=breaking_news,
        podcast_config=podcast_config,
        language=language,
        week=week,
    )
    turns = spoken_turns(script, podcast_config)
    if not turns:
        raise ClaimGroundingError("canonical script has no configured-host spoken turns")
    results = tuple(_check_turn(i, t, ctx) for i, t in enumerate(turns, 1))
    problems: list[str] = []
    if ctx.disclosure:
        if not _disclosure_placement_ok(_disclosure_holders(turns, ctx), len(turns)):
            problems.append("ai_disclosure_count")
    words = _word_count(turns)
    if not _bounds_ok(words, word_bounds):
        problems.append("word_range")
    return GateResult(results, tuple(problems), words)


def failure_findings(result: GateResult) -> str:
    lines = [result.summary()]
    lines.extend(
        f"turn {i} repair ({c}): {REPAIR_INSTRUCTIONS.get(c, REPAIR_INSTRUCTIONS['unsupported'])}"
        for i, c in sorted(result.failures.items())
    )
    return "\n".join(lines)


def to_grounding_error(result: GateResult) -> ClaimGroundingError:
    """Adapter so ``both`` mode can feed gate failures into the existing repair loop."""

    texts = {t.index: t.text for t in result.turns if t.failure}
    classes = {
        i: c if c in REPAIR_CLASS_INSTRUCTIONS else "unsupported"
        for i, c in result.failures.items()
    }
    return ClaimGroundingError(
        failure_findings(result), repair_turns=texts, failure_classes=classes
    )


def _replace_turn_lines(script: str, replacements: Mapping[int, str], config: PodcastConfig) -> str:
    host_a, host_b = config.host_a.name, config.host_b.name
    header, separator, body = script.partition("\n---")
    lines = (body if separator else script).splitlines(keepends=True)
    segment = 0
    for position, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or match_section_header(line) is not None:
            continue
        label = next((n for n in (host_a, host_b) if line.startswith(n + ":")), None)
        if label is None or not line[len(label) + 1 :].strip():
            continue
        segment += 1
        if segment in replacements:
            start = raw_line.index(label + ":") + len(label) + 1
            ending = raw_line[len(raw_line.rstrip("\r\n")) :]
            lines[position] = f"{raw_line[:start]} {replacements[segment]}{ending}"
    return "".join(lines) if not separator else header + separator + "".join(lines)


def render_quote_turn(span: Span, language: str) -> str:
    text = _normalize_spoken_markdown(_LEAD_INS[_lang(language)].format(q=quote_text(span)))
    return " ".join(text.split())


def downgrade_turns(
    script: str,
    failing: Mapping[int, str],
    *,
    ctx: _Context,
    word_bounds: tuple[int, int | None] | None,
) -> str:
    """Replace each failing turn by a verbatim quote-rendered turn (pure, deterministic).

    Candidates are ranked by token coverage of the failing turn (ties: source order); the
    first rendering that passes the gate, reuses no earlier quote and keeps the word range
    wins. Other lines stay byte-identical.
    """

    turns = spoken_turns(script, ctx.podcast_config)
    current = list(turns)
    used = {_norm(t) for i, t in enumerate(turns, 1) if i not in failing}
    replacements: dict[int, str] = {}
    for index in sorted(failing):
        want = _content_tokens(failing[index])
        total = sum(want.values()) or 1.0

        def rank(span: Span) -> tuple[float, str]:
            have = _content_tokens(span.text)
            return (-sum(w for t, w in want.items() if t in have) / total, span.span_id)

        candidates = sorted(
            (s for s in ctx.table if len(s.text) <= MAX_RENDERED_QUOTE_CHARS), key=rank
        )
        chosen = None
        for span in candidates[: MAX_RENDER_CANDIDATES * 4]:
            text = render_quote_turn(span, ctx.language)
            if _norm(text) in used:
                continue
            outcome = _check_turn(index, text, ctx)
            if not outcome.ok or outcome.kind != "quote":
                continue
            trial = list(current)
            trial[index - 1] = text
            if not _bounds_ok(_word_count(trial), word_bounds) and _bounds_ok(
                _word_count(current), word_bounds
            ):
                continue
            chosen = text
            break
        if chosen is None:
            chosen = _FILLERS[ctx.language]
        current[index - 1] = chosen
        used.add(_norm(chosen))
        replacements[index] = chosen
    return _replace_turn_lines(script, replacements, ctx.podcast_config)


def _spoken_positions(lines: list[str], config: PodcastConfig) -> list[tuple[int, str]]:
    """(line position, host label) for each spoken turn, mirroring ``spoken_turns``."""

    labels = (config.host_a.name, config.host_b.name)
    out: list[tuple[int, str]] = []
    for position, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or match_section_header(line) is not None:
            continue
        label = next((n for n in labels if line.startswith(n + ":")), None)
        if label is not None and line[len(label) + 1 :].strip():
            out.append((position, label))
    return out


def _split_body(script: str) -> tuple[str, list[str]]:
    header, separator, body = script.partition("\n---")
    if not separator:
        return "", script.splitlines(keepends=True)
    return header + separator, body.splitlines(keepends=True)


def _has_disclosure(result: TurnResult, ctx: _Context) -> bool:
    return result.kind == "disclosure" or bool(
        ctx.disclosure and ctx.disclosure in _norm(result.text)
    )


def _closing_start(results: tuple[TurnResult, ...], ctx: _Context) -> int:
    """0-based index of the first closing turn: trailing disclosures plus one sign-off."""

    start = len(results)
    while start > 0 and _has_disclosure(results[start - 1], ctx):
        start -= 1
    if 0 < start < len(results) and results[start - 1].kind == "chat":
        start -= 1
    return start


def top_up_words(script: str, *, ctx: _Context, minimum: int, maximum: int | None) -> str:
    """Insert verbatim quote turns until the word floor is met (pure, deterministic).

    Each added turn is ``According to the authors, "<span>"`` rendered from the source span
    table: unused spans first, longest first, no span more than twice overall, never past
    ``maximum``. Turns go before the closing sign-off/disclosure with alternating hosts.
    Returns the script unchanged when the pool cannot reach the floor.
    """

    config = ctx.podcast_config
    turns = spoken_turns(script, config)
    words = _word_count(turns)
    if words >= minimum:
        return script
    results = tuple(_check_turn(i, t, ctx) for i, t in enumerate(turns, 1))
    uses: dict[str, int] = {}
    for result in results:
        for span_id in result.span_ids:
            uses[span_id] = uses.get(span_id, 0) + 1
    pool: list[tuple[str, int, str]] = []
    for span in ctx.table:
        if len(span.text) > MAX_RENDERED_QUOTE_CHARS:
            continue
        text = render_quote_turn(span, ctx.language)
        checked = _check_turn(1, text, ctx)
        if checked.ok and checked.kind == "quote":
            pool.append((text, len(text.split()), span.span_id))
    header, lines = _split_body(script)
    positions = _spoken_positions(lines, config)
    if not positions or not pool:
        return script
    anchor_position, anchor_label = positions[max(_closing_start(results, ctx), 1) - 1]
    labels = (config.host_a.name, config.host_b.name)
    label = labels[1] if anchor_label == labels[0] else labels[0]
    added: list[str] = []
    for _ in range(2 * len(pool)):
        if words >= minimum:
            break
        choices = [
            (uses.get(span_id, 0), -count, text, count, span_id)
            for text, count, span_id in pool
            if uses.get(span_id, 0) < 2 and (maximum is None or words + count <= maximum)
        ]
        if not choices:
            return script
        _, _, text, count, span_id = min(choices)
        uses[span_id] = uses.get(span_id, 0) + 1
        words += count
        added.append(f"{label}: {text}\n")
        label = labels[1] if label == labels[0] else labels[0]
    if words < minimum:
        return script
    anchor = lines[anchor_position]
    if not anchor.endswith(("\n", "\r")):
        lines[anchor_position] = anchor + "\n"
    lines[anchor_position + 1 : anchor_position + 1] = added
    return header + "".join(lines)


def trim_trailing_chat(script: str, *, ctx: _Context, maximum: int, minimum: int) -> str:
    """Drop whole chat turns, last first, until within ``maximum`` (never below ``minimum``)."""

    config = ctx.podcast_config
    turns = spoken_turns(script, config)
    words = _word_count(turns)
    if words <= maximum:
        return script
    results = tuple(_check_turn(i, t, ctx) for i, t in enumerate(turns, 1))
    drop: list[int] = []
    for result in reversed(results):
        count = len(result.text.split())
        if (
            words > maximum
            and result.kind == "chat"
            and not _has_disclosure(result, ctx)
            and result.index > 1
            and words - count >= minimum
        ):
            drop.append(result.index)
            words -= count
    if words > maximum:
        return script
    header, lines = _split_body(script)
    positions = _spoken_positions(lines, config)
    for index in sorted(drop, reverse=True):
        del lines[positions[index - 1][0]]
    return header + "".join(lines)


@dataclass
class GateOutcome:
    script: str
    result: GateResult
    repair_rounds: int = 0
    downgraded: tuple[int, ...] = ()


Repairer = Callable[[str, dict[int, str], str], "str | None"]


def enforce(
    script: str,
    *,
    article_content: str,
    breaking_news: str | None,
    podcast_config: PodcastConfig,
    language: str = "en",
    week: str = "",
    word_bounds: tuple[int, int | None] | None = None,
    repairer: Repairer | None = None,
    started: float | None = None,
    budget_seconds: float = GATE_BUDGET_SECONDS,
    max_repairs: int = MAX_GATE_REPAIRS,
) -> GateOutcome:
    """Gate, repair a bounded number of times, then downgrade in code. Always terminates.

    Passing turns are never rewritten: a repaired script that alters any previously passing
    turn is discarded. ``FilterSafetyError`` and provider errors from the repairer propagate
    (terminal, fail closed). The wall clock is checked before every repairer call.
    """

    started = time.monotonic() if started is None else started
    ctx = _build_context(
        article_content=article_content,
        breaking_news=breaking_news,
        podcast_config=podcast_config,
        language=language,
        week=week,
    )

    def check(text: str) -> GateResult:
        return check_script(
            text,
            article_content=article_content,
            breaking_news=breaking_news,
            podcast_config=podcast_config,
            word_bounds=word_bounds,
            _ctx=ctx,
        )

    script = normalize_disclosure(script, ctx=ctx)
    result = check(script)
    initial_words = result.words
    rounds = 0
    while (
        repairer is not None
        and result.failures
        and rounds < max_repairs
        and time.monotonic() - started < budget_seconds
    ):
        rounds += 1
        failing = {t.index: t.text for t in result.turns if t.failure}
        before = spoken_turns(script, podcast_config)
        candidate = repairer(script, failing, failure_findings(result))
        if candidate is None:
            continue
        after = spoken_turns(candidate, podcast_config)
        if len(after) != len(before) or any(
            a != b for i, (a, b) in enumerate(zip(before, after), 1) if i not in failing
        ):
            logger.warning("gate repair discarded: passing turns changed")
            continue
        script, result = candidate, check(candidate)
    downgraded: tuple[int, ...] = ()
    if result.failures:
        downgraded = tuple(sorted(result.failures))
        failing = {t.index: t.text for t in result.turns if t.failure}
        script = downgrade_turns(script, failing, ctx=ctx, word_bounds=word_bounds)
        result = check(script)
    normalized = normalize_disclosure(script, ctx=ctx)
    if normalized != script:
        script = normalized
        result = check(script)
    words_after_downgrade = result.words
    if "word_range" in result.global_failures and not result.failures and word_bounds:
        low, high = word_bounds
        if result.words < low:
            script = top_up_words(script, ctx=ctx, minimum=low, maximum=high)
        elif high is not None and result.words > high:
            script = trim_trailing_chat(script, ctx=ctx, minimum=low, maximum=high)
        result = check(script)
    logger.info(
        "grounding gate done ok=%s repairs=%d downgraded=%d global=%s words_initial=%d "
        "words_after_downgrade=%d words_final=%d min=%s max=%s",
        result.ok,
        rounds,
        len(downgraded),
        ",".join(result.global_failures) or "-",
        initial_words,
        words_after_downgrade,
        result.words,
        word_bounds[0] if word_bounds else "-",
        word_bounds[1] if word_bounds else "-",
    )
    if not result.ok:
        detail = ""
        if word_bounds and "word_range" in result.global_failures:
            low, high = word_bounds
            if result.words < low:
                detail = f" words={result.words} min={low} shortfall={low - result.words};"
            elif high is not None:
                detail = f" words={result.words} max={high} excess={result.words - high};"
        raise ClaimGroundingError(f"{result.summary()};{detail} exhausted deterministic downgrade")
    return GateOutcome(script, result, rounds, downgraded)


def claims_from_result(result: GateResult, article_url: str, mode: str) -> list[Claim]:
    """One ledger row per spoken turn (so TTS segment count == ledger rows)."""

    safe_url = neutralize(article_url, limit=FIELD_LIMITS["reference"])
    claims = []
    for turn in result.turns:
        claims.append(
            Claim(
                claim_id=f"claim_{turn.index:03d}",
                script_excerpt=turn.text,
                source_url=safe_url if turn.source_kind == "article" else "",
                source_quote=turn.quote,
                source_paragraph=None,
                verified=False,
                editor_notes=(
                    f"Deterministic grounding gate ({mode}): {turn.kind}; code-resolved span, "
                    "not factual verification or human approval."
                ),
                source_kind=turn.source_kind,
                span_ids=turn.span_ids,
                grounding_mode=mode,
            )
        )
    return claims


def apply_gate_provenance(
    claims: list[Claim], result: GateResult, article_url: str, mode: str
) -> list[Claim]:
    """Overlay the gate's evidence on audit rows: ids, kind, quote and url move together."""

    safe_url = neutralize(article_url, limit=FIELD_LIMITS["reference"])
    return [
        replace(
            c,
            span_ids=t.span_ids,
            source_kind=t.source_kind,
            source_quote=t.quote,
            source_url=safe_url if t.source_kind == "article" else "",
            grounding_mode=mode,
        )
        for c, t in zip(claims, result.turns)
    ]


@dataclass
class AdvisoryOutcome:
    status: str = "skipped"
    detail: dict[str, Any] = field(default_factory=dict)


class AdvisoryCancelled(TimeoutError):
    """Raised by the advisory transport once the cutoff passed; no request is sent."""


def run_advisory(
    fn: Callable[[Callable[[Any], bytes]], Any],
    timeout: float = ADVISORY_AUDIT_SECONDS,
    *,
    grace: float = ADVISORY_GRACE_SECONDS,
) -> AdvisoryOutcome:
    """Run the non-gating LLM audit with a hard, cancellable wall-clock cutoff.

    ``fn`` receives a transport it must use for every provider request. The transport
    refuses to send once the cutoff passed or the run was cancelled and bounds each
    in-flight request by the time left, so the worker cannot keep calling the provider
    after the cutoff. After the cutoff the worker is joined for a short grace so a late
    ``FilterSafetyError`` still reaches the caller. Every other failure (including
    timeout) is logged content-free and swallowed; ``FilterSafetyError`` stays terminal.
    """

    from urllib.request import urlopen

    box: dict[str, Any] = {}
    cancel = threading.Event()
    deadline = time.monotonic() + timeout

    def transport(request: Any) -> bytes:
        remaining = deadline - time.monotonic()
        if cancel.is_set() or remaining <= 0:
            raise AdvisoryCancelled("advisory audit cutoff reached")
        with urlopen(request, timeout=remaining) as response:
            return response.read()

    def target() -> None:
        try:
            box["value"] = fn(transport)
        except BaseException as error:  # noqa: BLE001 - advisory must never fail the job
            box["error"] = error

    worker = threading.Thread(target=target, daemon=True, name="advisory-audit")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        cancel.set()
        worker.join(grace)
    if worker.is_alive():
        logger.warning("advisory audit timed out after %.0fs (non-gating)", timeout)
        return AdvisoryOutcome("timeout")
    error = box.get("error")
    if isinstance(error, AdvisoryCancelled):
        logger.warning("advisory audit cancelled at cutoff (non-gating)")
        return AdvisoryOutcome("timeout")
    if isinstance(error, FilterSafetyError):
        raise error
    if error is not None:
        logger.warning("advisory audit failed class=%s (non-gating)", type(error).__name__)
        failures = getattr(error, "failure_classes", None)
        return AdvisoryOutcome(
            "failed", {"failed_turns": len(failures)} if isinstance(failures, dict) else {}
        )
    logger.info("advisory audit ok rows=%s (non-gating)", len(box.get("value") or []))
    return AdvisoryOutcome("ok", {"rows": len(box.get("value") or [])})
