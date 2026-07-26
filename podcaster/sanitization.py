"""Untrusted-content sanitization for the article-to-script generation path.

Coordinator security rule: treat every externally sourced text field as
untrusted by default (crawled repo metadata, press articles, prior AI
summaries, PRD text, issue/PR comments, tool output, article content, and
source artifacts). Such text must be fenced, length-capped, and scanned for
indirect prompt-injection markers before it is embedded into any generated
artifact (script, show notes, transcript, review checklist, packet) or, in
future, into an LLM/TTS prompt.

This module never *obeys* embedded instructions. It neutralizes structure-
breaking characters, caps length, wraps untrusted text in an explicit fence so
human and machine reviewers can see it is data, and reports a neutral list of
detected injection markers for observability.
"""

from __future__ import annotations

import re
import unicodedata

# Inline fence delimiters. Untrusted text is wrapped so downstream readers
# (humans, LLMs, TTS) treat it as quoted data, never as instructions.
FENCE_OPEN = "\u300aUNTRUSTED\u300b"  # 《UNTRUSTED》
FENCE_CLOSE = "\u300a/UNTRUSTED\u300b"  # 《/UNTRUSTED》

# Conservative per-field length caps for echoed source-artifact metadata.
FIELD_LIMITS = {
    "reference": 512,
    "role": 64,
    "name": 256,
    "sha256": 64,
    "text": 2048,
}

_TRUNCATION_MARKER = "\u2026[truncated]"

# Characters that could break artifact structure (inject new script/markdown
# lines, smuggle control sequences, or hide payloads with zero-width glyphs).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_WHITESPACE_RE = re.compile(r"\s+")

# Indirect prompt-injection markers. Detection is for *flagging only*; matched
# text is still neutralized and never acted upon.
_INJECTION_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"\bignore\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,20}\binstructions?\b",
            re.IGNORECASE,
        ),
    ),  # noqa: E501
    (
        "disregard_instructions",
        re.compile(r"\bdisregard\b[^.\n]{0,40}\binstructions?\b", re.IGNORECASE),
    ),  # noqa: E501
    (
        "override_directive",
        re.compile(
            r"\b(?:override|forget|reset|bypass)\b[^.\n]{0,30}\b(?:instructions?|prompt|rules?|guardrails?)\b",
            re.IGNORECASE,
        ),
    ),  # noqa: E501
    ("role_injection", re.compile(r"\b(?:system|assistant|developer)\s*:", re.IGNORECASE)),
    ("identity_override", re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE)),
    ("new_instructions", re.compile(r"\bnew\s+instructions?\b", re.IGNORECASE)),
    ("prompt_reference", re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE)),
    ("jailbreak", re.compile(r"\b(?:jailbreak|do\s+anything\s+now|DAN\b)", re.IGNORECASE)),
    (
        "publish_directive",
        re.compile(
            r"\b(?:publish|deploy|exfiltrate|leak|send)\b[^.\n]{0,30}\b(?:secret|token|key|credential|now)\b",
            re.IGNORECASE,
        ),
    ),  # noqa: E501
)

# Long opaque tokens are a common carrier for encoded injection payloads
# (e.g. base64 of "Ignore previous instructions").
_ENCODED_BLOB_RE = re.compile(
    r"(?:[A-Za-z0-9+/]{24,}={0,2}|(?:%[0-9A-Fa-f]{2}){8,}|(?:\\u[0-9A-Fa-f]{4}){6,})"
)  # noqa: E501

# A hex digest may be echoed plainly; anything else is fenced as untrusted.
_HEX_RE = re.compile(r"[0-9a-fA-F]{1,64}")


def _strip_control_chars(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    normalized = _CONTROL_RE.sub(" ", normalized)
    # Collapse all whitespace (including newlines/tabs) to single spaces so
    # untrusted text cannot inject new structural lines into artifacts.
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def cap_length(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - len(_TRUNCATION_MARKER))] + _TRUNCATION_MARKER


def flag_injection(value: str) -> list[str]:
    """Return a sorted, de-duplicated list of injection marker names found.

    Used for observability only. Presence of a marker never changes control
    flow toward obeying the embedded text.
    """
    if not isinstance(value, str) or not value:
        return []
    flags: set[str] = set()
    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(value):
            flags.add(name)
    if _ENCODED_BLOB_RE.search(value):
        flags.add("encoded_blob")
    return sorted(flags)


def neutralize(value: object, *, limit: int = FIELD_LIMITS["text"]) -> str:
    """Strip control/zero-width chars, collapse whitespace, and length-cap."""
    text = value if isinstance(value, str) else str(value)
    return cap_length(_strip_control_chars(text), limit)


# Claracle weekly URLs are canonically lowercase in the week token
# (``/weekly/2026/w30/``); the uppercase ISO form (``/W30/``) that arrives from
# upstream article frontmatter 404s. Normalize only the week token of Claracle
# weekly URLs for user-facing links (show notes, packet, RSS) — other URLs are
# left untouched, and this is presentation-only so it never alters the request
# ``article_url`` that feeds the replay identity hash / job_id.
_WEEKLY_WEEK_TOKEN_RE = re.compile(r"(?i)(claracle\.com/weekly/\d{4}/)W(\d{1,2})(?=$|[/?#])")


def normalize_weekly_url(url: object) -> str:
    """Lowercase the week token of a Claracle weekly URL (``/W30/`` -> ``/w30/``).

    Non-string values are coerced to ``str``; non-weekly URLs pass through
    unchanged.
    """
    text = url if isinstance(url, str) else str(url)
    return _WEEKLY_WEEK_TOKEN_RE.sub(lambda m: f"{m.group(1)}w{m.group(2)}", text)


def fence(value: object, *, limit: int = FIELD_LIMITS["text"]) -> str:
    """Neutralize then wrap untrusted text in an explicit data fence.

    Any literal fence delimiters in the input are escaped so untrusted text
    cannot break out of, or forge, a fence boundary.
    """
    text = neutralize(value, limit=limit)
    text = text.replace(FENCE_OPEN, "U+300A").replace(FENCE_CLOSE, "U+300A")
    return f"{FENCE_OPEN}{text}{FENCE_CLOSE}"


# Source-artifact object fields that may be echoed into human/LLM-readable
# artifacts, with their length caps. Anything outside this allowlist is dropped.
_SOURCE_ARTIFACT_ECHO_FIELDS = {
    "role": FIELD_LIMITS["role"],
    "url": FIELD_LIMITS["reference"],
    "href": FIELD_LIMITS["reference"],
    "uri": FIELD_LIMITS["reference"],
    "path": FIELD_LIMITS["reference"],
    "name": FIELD_LIMITS["name"],
    "sha256": FIELD_LIMITS["sha256"],
}

_REFERENCE_PRIORITY = ("url", "href", "uri", "path", "name")


class SanitizedSourceArtifact:
    """A source-artifact reference reduced to allowlisted, sanitized fields."""

    __slots__ = ("role", "reference", "sha256", "flags")

    def __init__(self, role: str, reference: str, sha256: str, flags: list[str]) -> None:
        self.role = role
        self.reference = reference
        self.sha256 = sha256
        self.flags = flags


def sanitize_source_artifact(item: object) -> SanitizedSourceArtifact:
    """Reduce a string or object source artifact to safe, fenced fields.

    - Only allowlisted reference fields are considered.
    - Every echoed value is control-stripped, length-capped, and fenced.
    - Injection markers across the raw input are reported via ``flags``.
    """
    if isinstance(item, str):
        raw_for_flags = item
        role = ""
        reference = item
        sha256 = ""
    elif isinstance(item, dict):
        raw_for_flags = " ".join(
            str(value)
            for key, value in item.items()
            if isinstance(key, str) and key in _SOURCE_ARTIFACT_ECHO_FIELDS
        )
        role_value = item.get("role")
        role = role_value if isinstance(role_value, str) else ""
        reference = "unspecified"
        for field in _REFERENCE_PRIORITY:
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                reference = value
                break
        sha256_value = item.get("sha256")
        sha256 = sha256_value if isinstance(sha256_value, str) else ""
    else:
        raw_for_flags = str(item)
        role = ""
        reference = str(item)
        sha256 = ""

    flags = flag_injection(raw_for_flags)
    return SanitizedSourceArtifact(
        role=fence(role, limit=FIELD_LIMITS["role"]) if role.strip() else "",
        reference=fence(reference, limit=FIELD_LIMITS["reference"]),
        sha256=_safe_sha256(sha256),
        flags=flags,
    )


def _safe_sha256(value: str) -> str:
    """Echo a digest plainly only if it is pure hex; otherwise fence it.

    The digest is untrusted input; never emit it unfenced unless it matches the
    expected hex shape, so a forged fence delimiter or instruction text cannot
    escape the untrusted region.
    """
    if not value.strip():
        return ""
    candidate = neutralize(value, limit=FIELD_LIMITS["sha256"])
    if _HEX_RE.fullmatch(candidate):
        return candidate
    return fence(value, limit=FIELD_LIMITS["sha256"])


def assert_no_canary(value: str, canaries: "list[str] | tuple[str, ...]") -> None:
    """Raise if any canary token leaked into generated output.

    Used by tests/output checks to prove untrusted markers do not escape the
    fenced region into structural artifact lines.
    """
    for canary in canaries:
        if canary and canary in value:
            raise AssertionError(f"canary token leaked into generated output: {canary!r}")
