"""Localization QA + cultural-appropriateness validation gate (#440).

The multilanguage pipeline (jmservera/SquadScope-Coordinator#27) authors each
locale's script *directly* in the target language (#434) and fans out per
language (#439). Before a localized episode can publish, its output must pass a
quality gate so we never ship machine-awkward phrasing, untranslated English
leakage, or output missing the AI-voice disclosure / closing call-to-action.

This module provides that gate as pure functions so the engine, the job runner,
and tests all share the same checks:

* :func:`evaluate_localization` — run the per-locale QA checks against a
  generated script and its :class:`~podcaster.config.LanguageConfig` (duck-typed)
  / :class:`~podcaster.script_gen.GenerationContext`.
* :func:`localization_gate` — fold one-or-more per-locale results into a single
  publish-gate verdict (``blocked_by`` reasons mirror the review/publish gates).

Hard-fail (gate-blocking) checks, per the issue acceptance criteria:

* **Untranslated English leakage** — high-signal English function words / phrases
  appearing in a non-English locale's *spoken* dialogue. Technical English proper
  nouns (API, GitHub, CI/CD) are intentionally NOT flagged: the TTS bakeoff
  (#436) selected Azure multilingual voices precisely so embedded English terms
  are pronounced natively while the host speaks es/fr.
* **Missing AI-voice disclosure** — the localized disclosure must be spoken.
* **Missing closing CTA** — the localized call-to-action (Claracle site) must be
  present.

Advisory checks surface as ``warnings`` / ``flags`` for the human review gate and
never hard-fail on their own:

* **Host-persona consistency** — speaker labels match the configured hosts.
* **Cultural-appropriateness / RAI** — overgeneralization patterns and any
  caller-supplied risk terms are flagged for the documented RAI review
  (``docs/localization-rai-checklist.md``), coordinated with Hermes (safety).

The English (default) locale is unaffected: leakage detection is skipped (the
content *is* English) and the disclosure/CTA checks reuse the same English
defaults the show already ships.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_LANGUAGE = "en"

#: A reliable anchor for the closing CTA: every localized CTA points at the
#: (English) Claracle site, so the normalized URL token is present regardless of
#: how the surrounding sentence is phrased per language.
_CLARACLE_URL_ANCHOR = "claracle com"

#: Number of distinct English-leakage hits in a non-English locale before the
#: gate fails. A small threshold avoids a single ambiguous token tripping the
#: gate while still catching genuinely untranslated dialogue.
LEAKAGE_THRESHOLD = 2

#: How many trailing spoken turns count as the "closing" of an episode. The
#: closing CTA must appear here — an opening site mention (the required
#: disclosure/welcome already references the site) must NOT satisfy the closing
#: call-to-action requirement.
CLOSING_TURN_WINDOW = 4

#: High-signal English function words that do not occur as standalone words in
#: genuine Spanish/French copy. Deliberately excludes tokens that collide with
#: es/fr vocabulary (``on``/``but`` in French; ``son``/``no``/``a``/``y``/``o``
#: in Spanish) and excludes technical proper nouns, which are expected to stay
#: English (see module docstring).
_ENGLISH_LEAKAGE_WORDS = frozenset(
    {
        "the",
        "and",
        "with",
        "this",
        "that",
        "these",
        "those",
        "they",
        "them",
        "their",
        "there",
        "what",
        "which",
        "when",
        "where",
        "because",
        "would",
        "could",
        "should",
        "about",
        "however",
        "your",
        "you",
        "we",
        "here",
        "into",
        "through",
        "between",
        "while",
        "being",
        "does",
        "have",
        "has",
        "had",
        "will",
        "can",
        "not",
        "from",
        "for",
        "are",
        "was",
        "is",
        "of",
        "to",
    }
)

#: Multi-word English markers (normalized, space-separated) that strongly signal
#: untranslated boilerplate when found in a non-English locale.
_ENGLISH_LEAKAGE_PHRASES = (
    "this week",
    "let us",
    "welcome back",
    "stay tuned",
    "read more at",
    "in this episode",
    "we found",
    "check out",
    "wrap up",
    "that is all",
    "thanks for listening",
)

#: Overgeneralization templates flagged for RAI review (warnings only). These
#: catch sweeping "all <group> ..." statements that often precede stereotyping;
#: the human checklist decides appropriateness.
_OVERGENERALIZATION_RES = (
    re.compile(r"\btodos los \w+", re.IGNORECASE),
    re.compile(r"\btodas las \w+", re.IGNORECASE),
    re.compile(r"\btous les \w+", re.IGNORECASE),
    re.compile(r"\btoutes les \w+", re.IGNORECASE),
    re.compile(r"\ball \w+ are\b", re.IGNORECASE),
)

_SECTION_HEADER_RE = re.compile(r"^\s*#{1,6}\s*section\s*[:\-]", re.IGNORECASE)
_SPEAKER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _'.-]{0,40}):\s*(.+)$")


@dataclass(frozen=True)
class LocalizationQAResult:
    """Per-locale localization QA verdict.

    ``passed`` is ``True`` only when no hard-fail check produced an error.
    ``warnings`` and ``flags`` are advisory (host-persona / RAI) and never set
    ``passed`` to ``False`` on their own.
    """

    language: str
    locale: str
    passed: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    checks: Mapping[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "locale": self.locale,
            "passed": self.passed,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "flags": list(self.flags),
            "checks": dict(self.checks),
        }


def _normalize(text: str) -> str:
    """Lowercase, strip accents, and reduce to space-separated alphanumerics.

    The result is wrapped in single spaces so single-word membership and phrase
    substring tests both work with simple ``in`` checks. Accents are stripped so
    disclosure/CTA matching is robust to minor punctuation/diacritic drift while
    English-leakage words (which carry no accents) still match cleanly.
    """

    lowered = text.lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    collapsed = re.sub(r"[^a-z0-9]+", " ", stripped).strip()
    return f" {collapsed} "


def _is_default_language(language: str) -> bool:
    return (language or "").split("-", 1)[0].strip().lower() in ("", "en")


def extract_spoken_text(
    script_text: str,
    host_labels: Sequence[str] | None = None,
) -> list[tuple[str, str]]:
    """Return ``(speaker_label, spoken_text)`` pairs from a generated script.

    Non-spoken ``## Section:`` headers and the leading ``---`` metadata block are
    ignored; every ``Speaker: text`` dialogue line is captured generically so QA
    works regardless of whether the script labels hosts by configured name or by
    ``HOST_A``/``HOST_B``. When *host_labels* is given, the result is filtered to
    those speakers — but only if at least one such turn exists, so a label
    mismatch falls back to the full set rather than dropping all dialogue.
    """

    _, sep, after = script_text.partition("\n---")
    body = after if sep else script_text
    turns: list[tuple[str, str]] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or _SECTION_HEADER_RE.match(line):
            continue
        match = _SPEAKER_RE.match(line)
        if match:
            spoken = match.group(2).strip()
            if spoken:
                turns.append((match.group(1).strip(), spoken))

    labels = {label.strip() for label in (host_labels or []) if label and label.strip()}
    if labels:
        filtered = [(label, text) for label, text in turns if label in labels]
        if filtered:
            return filtered
    return turns


def _detect_leakage(spoken_normalized: str) -> list[str]:
    """Return the distinct English-leakage markers found in normalized text."""

    hits: list[str] = []
    seen: set[str] = set()
    for phrase in _ENGLISH_LEAKAGE_PHRASES:
        if f" {phrase} " in spoken_normalized and phrase not in seen:
            seen.add(phrase)
            hits.append(phrase)
    for token in spoken_normalized.split():
        if token in _ENGLISH_LEAKAGE_WORDS and token not in seen:
            seen.add(token)
            hits.append(token)
    return hits


def _resolve(value: Any, *attrs: str) -> Any:
    for attr in attrs:
        if isinstance(value, Mapping) and attr in value:
            return value[attr]
        if hasattr(value, attr):
            return getattr(value, attr)
    return None


def evaluate_localization(
    script_text: str,
    *,
    config: Any = None,
    language: str | None = None,
    locale: str | None = None,
    disclosure: str | None = None,
    cta: str | None = None,
    host_a_name: str | None = None,
    host_b_name: str | None = None,
    flag_terms: Iterable[str] = (),
) -> LocalizationQAResult:
    """Run the per-locale localization QA gate against a generated script.

    *config* may be any object exposing ``language``/``locale``/``disclosure``/
    ``cta``/``host_a``/``host_b`` (a :class:`~podcaster.config.LanguageConfig` or
    :class:`~podcaster.script_gen.GenerationContext`); explicit keyword arguments
    take precedence so callers can override per call.
    """

    if config is not None:
        language = language if language is not None else _resolve(config, "language")
        locale = locale if locale is not None else _resolve(config, "locale")
        disclosure = disclosure if disclosure is not None else _resolve(config, "disclosure")
        cta = cta if cta is not None else _resolve(config, "cta")
        if host_a_name is None:
            host_a_name = _resolve(_resolve(config, "host_a"), "name")
        if host_b_name is None:
            host_b_name = _resolve(_resolve(config, "host_b"), "name")

    language = (language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE
    locale = (locale or language).strip() or language
    disclosure = (disclosure or "").strip()
    cta = (cta or "").strip()

    errors: list[str] = []
    warnings: list[str] = []
    flags: list[str] = []
    checks: dict[str, bool] = {}

    host_labels = [name for name in (host_a_name, host_b_name) if name and name.strip()]
    all_turns = extract_spoken_text(script_text)
    turns = extract_spoken_text(script_text, host_labels or None)
    spoken_text = " ".join(text for _, text in turns)
    spoken_normalized = _normalize(spoken_text)
    full_normalized = _normalize(script_text)

    # --- Hard-fail: untranslated English leakage (non-English locales only) ----
    is_default = _is_default_language(language)
    if is_default:
        checks["no_english_leakage"] = True
    else:
        leakage = _detect_leakage(spoken_normalized)
        if len(leakage) >= LEAKAGE_THRESHOLD:
            checks["no_english_leakage"] = False
            errors.append(
                f"{locale}: untranslated English leakage detected in dialogue "
                f"(markers: {', '.join(leakage[:8])})"
            )
        else:
            checks["no_english_leakage"] = True

    # --- Hard-fail: AI-voice disclosure present --------------------------------
    if disclosure:
        disclosure_present = _normalize(disclosure).strip() in full_normalized
        checks["disclosure_present"] = disclosure_present
        if not disclosure_present:
            errors.append(f"{locale}: AI-voice disclosure not found in the script")
    else:
        checks["disclosure_present"] = False
        errors.append(f"{locale}: no AI-voice disclosure configured for this locale")

    # --- Hard-fail: closing CTA present ----------------------------------------
    # The CTA must appear in the *closing* of the episode, not merely anywhere in
    # the script. The required opening disclosure/welcome already mentions the
    # site (see script_gen.py / episode.py), so an intro mention must not satisfy
    # the "closing call-to-action" requirement. Restrict the search to the last
    # few spoken turns.
    closing_turns = turns[-CLOSING_TURN_WINDOW:] if turns else []
    closing_normalized = _normalize(" ".join(text for _, text in closing_turns))
    cta_anchor = _CLARACLE_URL_ANCHOR in closing_normalized
    cta_text_present = bool(cta) and _normalize(cta).strip() in closing_normalized
    cta_present = cta_anchor or cta_text_present
    checks["cta_present"] = cta_present
    if not cta_present:
        errors.append(
            f"{locale}: closing call-to-action (Claracle site) not found in the "
            f"closing turns of the script"
        )

    # --- Advisory: host-persona consistency ------------------------------------
    if host_labels:
        spoken_speakers = {label for label, _ in all_turns}
        missing = [name for name in host_labels if name not in spoken_speakers]
        unexpected = sorted(spoken_speakers - set(host_labels))
        consistent = not missing and not unexpected
        checks["host_persona_consistent"] = consistent
        if missing:
            warnings.append(f"{locale}: configured host(s) never speak: {', '.join(missing)}")
        if unexpected:
            warnings.append(
                f"{locale}: unexpected speaker label(s) not in configured hosts: "
                f"{', '.join(unexpected)}"
            )
    else:
        checks["host_persona_consistent"] = True

    # --- Advisory: cultural-appropriateness / RAI flags ------------------------
    rai_hits: list[str] = []
    for pattern in _OVERGENERALIZATION_RES:
        for match in pattern.findall(spoken_text):
            snippet = match if isinstance(match, str) else " ".join(match)
            rai_hits.append(snippet.strip())
    extra_terms = [term.strip() for term in flag_terms if term and term.strip()]
    if extra_terms:
        for term in extra_terms:
            if f" {_normalize(term).strip()} " in full_normalized:
                rai_hits.append(term)
    if rai_hits:
        checks["rai_review_clear"] = False
        deduped = list(dict.fromkeys(rai_hits))
        flags.append(
            f"{locale}: cultural-appropriateness review required for "
            f"{len(deduped)} pattern(s) — see docs/localization-rai-checklist.md "
            f"(e.g. {', '.join(repr(s) for s in deduped[:5])})"
        )
    else:
        checks["rai_review_clear"] = True

    passed = not errors
    return LocalizationQAResult(
        language=language,
        locale=locale,
        passed=passed,
        errors=tuple(errors),
        warnings=tuple(warnings),
        flags=tuple(flags),
        checks=checks,
    )


def localization_gate(results: Iterable[LocalizationQAResult]) -> dict[str, Any]:
    """Fold per-locale QA results into a single publish-gate verdict.

    Returns a manifest-friendly dict: ``passed`` is ``True`` only when every
    locale passed; ``blocked_by`` lists ``localization_qa_failed:<locale>``
    reasons mirroring the review/publish gate vocabulary so the orchestrator can
    treat it like the other blockers.
    """

    per_locale: dict[str, Any] = {}
    blocked_by: list[str] = []
    warnings: list[str] = []
    flags: list[str] = []
    for result in results:
        per_locale[result.locale] = result.to_dict()
        if not result.passed:
            reason = f"localization_qa_failed:{result.locale}"
            if reason not in blocked_by:
                blocked_by.append(reason)
        warnings.extend(result.warnings)
        flags.extend(result.flags)
    return {
        "passed": not blocked_by,
        "blocked_by": blocked_by,
        "per_locale": per_locale,
        "warnings": warnings,
        "flags": flags,
    }
