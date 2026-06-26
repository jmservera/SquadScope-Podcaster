"""Host ownership-tone enforcement for generated scripts (#418).

The hosts of "Claracle" are the people who *wrote* the analysis — Claracle is
their own publication. They must speak as experts sharing their own findings,
never as reporters narrating an external "article", "report", or "roundup".

This module provides:

* :data:`OWNERSHIP_TONE_PROMPT` — guidance injected into the script-generation
  system prompt so the model authors in the correct voice from the start.
* :func:`find_violations` — hard validation that flags banned reporter-voice
  phrases on spoken dialogue lines (used to gate a script before TTS).
* :func:`find_soft_flags` — phrases that *may* be valid in context and are only
  surfaced as warnings.
* :func:`build_repair_instruction` — an LLM repair message that asks the model
  to rewrite *only* the offending lines using ownership language while
  preserving the rest of the script (including ``## Section:`` headers) exactly.

Validation runs only on spoken host turns. Non-spoken lines — ``## Section:``
headers, header metadata, ``---`` separators and blank lines — are ignored, so
the intro can still reference "Claracle" as a brand name without tripping the
hard rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Guidance injected into the system prompt. Keeps the model in the correct voice
# from the first generation, minimising the need for a repair round-trip.
OWNERSHIP_TONE_PROMPT = (
    "\nOWNERSHIP TONE (REQUIRED):\n"
    "Claracle is the hosts' OWN publication and analysis platform. The hosts are "
    "not reporting on an external article — they ARE the people who wrote the "
    "research. They speak as experts sharing their own findings.\n"
    "Use ownership language: \"We found...\", \"Our analysis shows...\", "
    "\"This week we noticed...\", \"On Claracle, we're tracking...\", "
    "\"What stood out to us...\".\n"
    "Do NOT refer to the source as an external \"article\", \"report\", "
    "\"roundup\", or \"analysis\". Never say \"the article mentions\", "
    "\"according to the report\", \"the roundup says\", \"as the article notes\", "
    "or similar reporter-voice phrasing. Referring to \"Claracle\" as the brand "
    "name is fine; treating it as an outside source is not.\n"
)

# Hard-banned reporter-voice phrases. Each alternative is anchored on word
# boundaries and tolerant of arbitrary internal whitespace. A single combined
# pattern keeps overlapping phrases (e.g. "in the article" vs "the article")
# from being double-counted: ``finditer`` returns non-overlapping matches.
_BANNED_RE = re.compile(
    r"\b(?:"
    r"(?:the|this)\s+article"  # "the article" / "this article"
    r"|the\s+report\s+(?:says|mentions)"  # "the report says/mentions"
    r"|according\s+to\s+(?:the|this)\s+(?:article|report|roundup|analysis)"
    r"|the\s+roundup\s+(?:says|mentions)"  # "the roundup says/mentions"
    r"|in\s+(?:the\s+article|this\s+report)"  # "in the article" / "in this report"
    r"|as\s+the\s+article\s+notes"  # "as the article notes"
    r")\b",
    re.IGNORECASE,
)

# Soft-flag phrases — may be legitimate in context (e.g. "according to GitHub
# stars", "was mentioned at the conference"). Surfaced as warnings only.
# "according to" is excluded when it is already part of a hard-banned phrase.
_SOFT_RE = re.compile(
    r"\b(?:"
    r"according\s+to(?!\s+(?:the|this)\s+(?:article|report|roundup|analysis)\b)"
    r"|was\s+mentioned"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OwnershipViolation:
    """A reporter-voice phrase found on a spoken dialogue line.

    Attributes:
        line_number: 1-based line number within the scanned text.
        line: The full dialogue line containing the phrase.
        phrase: The exact substring that matched (normalised whitespace).
    """

    line_number: int
    line: str
    phrase: str


def _is_spoken_line(line: str) -> bool:
    """Return True when ``line`` is a spoken host turn worth scanning.

    Non-spoken lines — section headers, header metadata, ``---`` separators and
    blanks — are skipped so brand references in structural lines never trip the
    hard rule.
    """

    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("## Section:"):
        return False
    if stripped.startswith("#"):  # markdown headers / title metadata
        return False
    if set(stripped) <= {"-"}:  # "---" style separators
        return False
    # Header metadata lines such as "Week: 2026-W26" or "Source: https://...".
    # Spoken turns are "Host: <text>"; metadata keys are single tokens. We only
    # skip a small set of known metadata keys to avoid hiding real dialogue.
    head, sep, _ = stripped.partition(":")
    if sep and re.sub(r"\s+", " ", head.strip()).lower() in _METADATA_KEYS:
        return False
    return True


# Known header metadata keys. These mirror the header emitted by
# ``script_gen._format_script`` so ``find_violations()`` stays correct even when
# run on a full formatted script (header + dialogue), not just raw dialogue.
# Keys are compared case-insensitively with internal whitespace collapsed, so
# multi-word keys like ``Source URL:`` / ``Source SHA256:`` match.
_METADATA_KEYS = {
    "week",
    "source",
    "source url",
    "source sha256",
    "article",
    "article_url",
    "article_sha256",
    "sha256",
    "title",
    "episode",
    "podcast",
    "voices",
    "safety",
    "generator",
    "generated",
    "generated_at",
}


def _spoken_text(line: str) -> str:
    """Return only the spoken portion of a ``Host: text`` line.

    The speaker label is excluded so a host literally named "Article" (unlikely,
    but possible from config) cannot cause false positives, and so phrases are
    only matched in actual speech.
    """

    _, sep, rest = line.partition(":")
    return rest if sep else line


def _scan(text: str, pattern: re.Pattern[str]) -> list[OwnershipViolation]:
    violations: list[OwnershipViolation] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not _is_spoken_line(line):
            continue
        spoken = _spoken_text(line)
        for match in pattern.finditer(spoken):
            phrase = re.sub(r"\s+", " ", match.group(0)).strip()
            violations.append(
                OwnershipViolation(line_number=index, line=line.strip(), phrase=phrase)
            )
    return violations


def find_violations(text: str) -> list[OwnershipViolation]:
    """Return hard-banned reporter-voice violations on spoken lines."""

    return _scan(text, _BANNED_RE)


def find_soft_flags(text: str) -> list[OwnershipViolation]:
    """Return soft-flag phrases (warnings only) on spoken lines."""

    return _scan(text, _SOFT_RE)


def has_violations(text: str) -> bool:
    """Return True when ``text`` contains any hard-banned phrase."""

    for line in text.splitlines():
        if _is_spoken_line(line) and _BANNED_RE.search(_spoken_text(line)):
            return True
    return False


def build_repair_instruction(violations: list[OwnershipViolation]) -> str:
    """Build an LLM repair message targeting only the offending lines.

    The instruction lists each violating line verbatim and asks the model to
    rewrite ONLY those lines using ownership language, returning the full script
    with every other line — including ``## Section:`` headers — unchanged.
    """

    bullets = "\n".join(
        f'- Line {v.line_number} contains banned phrase "{v.phrase}":\n    {v.line}'
        for v in violations
    )
    return (
        "The script you produced uses reporter-voice phrasing that treats Claracle "
        "as an external source. Claracle is the hosts' OWN publication; they wrote "
        "the analysis and must speak as experts sharing their own findings.\n\n"
        "Rewrite ONLY the offending lines below using ownership language "
        "(\"We found...\", \"Our analysis shows...\", \"This week we noticed...\", "
        "\"What stood out to us...\"). Preserve every other line EXACTLY as-is, "
        "including all \"## Section:\" headers, speaker labels, and ordering. "
        "Return the COMPLETE script.\n\n"
        "Offending lines:\n"
        f"{bullets}\n"
    )
