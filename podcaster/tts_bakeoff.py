"""Pure logic for the TTS bakeoff sample generation (issue #41).

This module contains no network or provider calls so it can be unit tested.
The CLI in ``scripts/tts_bakeoff_synthesize.py`` wires these helpers to an
Azure Speech synthesizer and the existing private storage backend.

Hard rules encoded here:
- Generated audio is never committed to git (``.gitignore`` excludes audio).
- Provider keys and SAS query strings are never placed in the manifest.
- Script text is XML-escaped before it is embedded in SSML.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

SPEAKER_LABELS = {
    "NARRATOR": "narrator",
    "GUEST": "guest",
}

_SPEAKER_LINE_RE = re.compile(r"^(?P<label>[A-Z]{3,12}):\s?(?P<text>.*)$")
_PATH_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Segment:
    """A contiguous block of script text spoken by one role."""

    role: str
    text: str


@dataclass(frozen=True)
class BakeoffCandidate:
    """A provider/voice configuration to synthesize the shared test script."""

    provider: str
    narrator_voice: str
    locale: str = "en-US"
    guest_voice: str | None = None
    enabled: bool = True
    notes: str = ""

    def voice_for(self, role: str) -> str:
        if role == "guest" and self.guest_voice:
            return self.guest_voice
        return self.narrator_voice


@dataclass(frozen=True)
class SampleSpec:
    """A single planned synthesis: one candidate rendering the whole script."""

    candidate: BakeoffCandidate
    segments: tuple[Segment, ...]
    blob_path: str
    ssml: str


@dataclass
class SampleResult:
    """Outcome of one synthesis attempt, used to build the manifest."""

    provider: str
    narrator_voice: str
    guest_voice: str | None
    blob_path: str
    status: str
    size_bytes: int | None = None
    content_type: str | None = None
    url: str | None = None
    error: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def script_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_segments(script_text: str) -> list[Segment]:
    """Parse speaker-labelled script lines into ordered segments.

    Front-matter before the first recognised speaker label is ignored so the
    file header never leaks into narration. Continuation lines (no label) are
    appended to the current segment.
    """

    segments: list[Segment] = []
    current_role: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        if current_role is not None:
            text = " ".join(part.strip() for part in current_lines if part.strip()).strip()
            if text:
                segments.append(Segment(role=current_role, text=text))
        current_lines = []

    for raw_line in script_text.splitlines():
        line = raw_line.rstrip()
        match = _SPEAKER_LINE_RE.match(line)
        if match and match.group("label") in SPEAKER_LABELS:
            flush()
            current_role = SPEAKER_LABELS[match.group("label")]
            current_lines = [match.group("text")]
        elif current_role is not None:
            if line.strip():
                current_lines.append(line)
            else:
                flush()  # blank line ends a paragraph; same speaker continues
    flush()
    return segments


def default_candidates() -> list[BakeoffCandidate]:
    """Candidate voices aligned with backlog/tts-bakeoff.md.

    Only the Azure Speech Standard path is enabled by default; OpenAI/Foundry
    voices stay disabled until region availability and retention terms are
    reviewed (Hermes gate).
    """

    return [
        BakeoffCandidate(
            provider="azure-speech-standard",
            narrator_voice="en-US-AndrewMultilingualNeural",
            guest_voice="en-US-AvaMultilingualNeural",
            notes="Preferred Azure-first candidate; multi-voice per-segment control.",
        ),
        BakeoffCandidate(
            provider="azure-speech-batch",
            narrator_voice="en-US-BrianMultilingualNeural",
            guest_voice="en-US-EmmaMultilingualNeural",
            notes="Long-form async batch synthesis path.",
        ),
        BakeoffCandidate(
            provider="openai-tts",
            narrator_voice="alloy",
            guest_voice="verse",
            enabled=False,
            notes="Conditional: enable only after privacy/retention review for #4.",
        ),
    ]


def _path_token(value: str) -> str:
    token = _PATH_TOKEN_RE.sub("-", value.strip().lower()).strip("-")
    return token or "unknown"


def blob_path_for(week: str, candidate: BakeoffCandidate) -> str:
    return f"bakeoff/{_path_token(week)}/{_path_token(candidate.provider)}/{_path_token(candidate.narrator_voice)}.mp3"


def escape_ssml_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def build_ssml(segments: list[Segment], candidate: BakeoffCandidate) -> str:
    lines = [
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{escape_ssml_text(candidate.locale)}">'
    ]
    for segment in segments:
        voice = escape_ssml_text(candidate.voice_for(segment.role))
        lines.append(f'<voice name="{voice}"><p>{escape_ssml_text(segment.text)}</p></voice>')
    lines.append("</speak>")
    return "".join(lines)


def build_plan(
    script_text: str,
    week: str,
    candidates: list[BakeoffCandidate] | None = None,
    include_disabled: bool = False,
) -> list[SampleSpec]:
    segments = parse_segments(script_text)
    if not segments:
        raise ValueError("script contains no speaker-labelled segments")
    chosen = candidates if candidates is not None else default_candidates()
    plan: list[SampleSpec] = []
    for candidate in chosen:
        if not candidate.enabled and not include_disabled:
            continue
        plan.append(
            SampleSpec(
                candidate=candidate,
                segments=tuple(segments),
                blob_path=blob_path_for(week, candidate),
                ssml=build_ssml(segments, candidate),
            )
        )
    return plan


def redact_url(value: str | None) -> str | None:
    """Drop query (SAS token) and fragment from a URL for safe logging."""

    if not value:
        return value
    parsed = urlsplit(value)
    if not parsed.query and not parsed.fragment:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "[redacted-query]", ""))


def build_manifest(
    week: str,
    script_path: str,
    script_text: str,
    results: list[SampleResult],
    mode: str,
) -> dict:
    return {
        "schema": "podcaster.tts-bakeoff.manifest/v1",
        "purpose": "private TTS bakeoff comparison (issue #41); not for publication",
        "week": week,
        "mode": mode,
        "script": {
            "path": script_path,
            "sha256": script_sha256(script_text),
            "segments": len(parse_segments(script_text)),
        },
        "samples": [
            {
                "provider": result.provider,
                "narrator_voice": result.narrator_voice,
                "guest_voice": result.guest_voice,
                "blob_path": result.blob_path,
                "status": result.status,
                "size_bytes": result.size_bytes,
                "content_type": result.content_type,
                "url": redact_url(result.url),
                "error": result.error,
                **({"extra": result.extra} if result.extra else {}),
            }
            for result in results
        ],
    }
