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

# Operator-selected production text-to-speech config (#60). OpenAI TTS with
# ``fable`` for host A (narrator) and ``alloy`` for host B (guest). This is the
# single source of truth for the production voices the generation path consumes;
# it supersedes the OpenAI-disabled bakeoff gate for *production* use. The
# private bakeoff comparison candidate (see ``default_candidates``) stays
# disabled to avoid unreviewed private spend during the #4/#41 spike.
PRODUCTION_PROVIDER = "openai-tts"
PRODUCTION_NARRATOR_VOICE = "fable"
PRODUCTION_GUEST_VOICE = "alloy"


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
    is_production: bool = False
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
            notes="Private bakeoff comparison sample only; stays disabled to avoid "
            "unreviewed spend. The authoritative production voices are fable+alloy "
            "via production_candidate() (operator decision #60).",
        ),
    ]


def production_candidate() -> BakeoffCandidate:
    """Operator-selected production TTS config for #60.

    OpenAI TTS with ``fable`` for host A (narrator) and ``alloy`` for host B
    (guest). This is the single source of truth for the production voices; the
    generation path reads these constants so the manifest, cost ledger, and any
    future synthesis stay consistent. It is enabled and marked ``is_production``
    so it is never mistaken for a private bakeoff comparison sample.
    """

    return BakeoffCandidate(
        provider=PRODUCTION_PROVIDER,
        narrator_voice=PRODUCTION_NARRATOR_VOICE,
        guest_voice=PRODUCTION_GUEST_VOICE,
        enabled=True,
        is_production=True,
        notes="Operator-selected production config (#60): Claracle two-voice "
        "conversation; AI-voice disclosure required in first 60s and show notes; "
        "publication stays human-gated.",
    )


# --- Multilanguage native-voice bakeoff (#436) ------------------------------
#
# The multilanguage epic (parent: SquadScope-Coordinator#27) needs native es and
# fr host voice pairs. Azure *multilingual* neural voices are the preferred
# candidates because they pronounce embedded English proper nouns and tech terms
# (API, CI/CD, OIDC, GitHub, Azure, OpenAI) natively while speaking the target
# language — a hard requirement for SquadScope tech podcasts. DragonHD variants
# are added as higher-fidelity comparison candidates. ElevenLabs stays disabled
# until voice rights, data retention, and per-minute spend are reviewed (Hermes
# gate), mirroring the OpenAI gate in ``default_candidates``.
#
# Azure has no ``es-419`` voice; ``es-MX`` is the agreed Latin-American proxy and
# is broadly intelligible across LatAm. The selected pairs feed the per-language
# config voice fields in #432 via ``recommended_voice_pair``.

# Latin-American Spanish (es-419 proxy: es-MX) and France French (fr-FR) locales.
ES_LOCALE = "es-MX"
FR_LOCALE = "fr-FR"

# Operator-selectable recommendation from the bakeoff (#436). Host A = narrator
# (male), Host B = guest (female), matching the Claracle two-voice format. These
# IDs are the single source of truth consumed by the per-language config (#432).
RECOMMENDED_VOICE_PAIRS: dict[str, dict[str, str]] = {
    "es": {
        "locale": ES_LOCALE,
        "provider": "azure-speech-standard",
        "narrator_voice": "es-MX-JorgeMultilingualNeural",
        "guest_voice": "es-MX-DaliaMultilingualNeural",
    },
    "fr": {
        "locale": FR_LOCALE,
        "provider": "azure-speech-standard",
        "narrator_voice": "fr-FR-RemyMultilingualNeural",
        "guest_voice": "fr-FR-VivienneMultilingualNeural",
    },
}


def spanish_candidates() -> list[BakeoffCandidate]:
    """Native Latin-American Spanish (es-MX) host-pair candidates for #436."""

    return [
        BakeoffCandidate(
            provider="azure-speech-standard",
            locale=ES_LOCALE,
            narrator_voice="es-MX-JorgeMultilingualNeural",
            guest_voice="es-MX-DaliaMultilingualNeural",
            notes="Preferred es-419 pair; multilingual neural keeps English tech "
            "terms and proper nouns native while speaking Spanish.",
        ),
        BakeoffCandidate(
            provider="azure-speech-dragonhd",
            locale=ES_LOCALE,
            narrator_voice="es-MX-Tristan:DragonHDLatestNeural",
            guest_voice="es-MX-Ximena:DragonHDLatestNeural",
            notes="DragonHD higher-fidelity comparison pair for es-MX.",
        ),
        BakeoffCandidate(
            provider="elevenlabs",
            locale=ES_LOCALE,
            narrator_voice="eleven_multilingual_v2",
            guest_voice="eleven_multilingual_v2",
            enabled=False,
            notes="Comparison only; disabled pending voice-rights, retention, and "
            "spend review (Hermes gate). Voice IDs assigned at consent time.",
        ),
    ]


def french_candidates() -> list[BakeoffCandidate]:
    """Native France French (fr-FR) host-pair candidates for #436."""

    return [
        BakeoffCandidate(
            provider="azure-speech-standard",
            locale=FR_LOCALE,
            narrator_voice="fr-FR-RemyMultilingualNeural",
            guest_voice="fr-FR-VivienneMultilingualNeural",
            notes="Preferred fr-FR pair; multilingual neural keeps English tech "
            "terms and proper nouns native while speaking French.",
        ),
        BakeoffCandidate(
            provider="azure-speech-standard",
            locale=FR_LOCALE,
            narrator_voice="fr-FR-LucienMultilingualNeural",
            guest_voice="fr-FR-VivienneMultilingualNeural",
            notes="Alternate male narrator for host-pair contrast.",
        ),
        BakeoffCandidate(
            provider="azure-speech-dragonhd",
            locale=FR_LOCALE,
            narrator_voice="fr-FR-Remy:DragonHDLatestNeural",
            guest_voice="fr-FR-Vivienne:DragonHDLatestNeural",
            notes="DragonHD higher-fidelity comparison pair for fr-FR.",
        ),
        BakeoffCandidate(
            provider="elevenlabs",
            locale=FR_LOCALE,
            narrator_voice="eleven_multilingual_v2",
            guest_voice="eleven_multilingual_v2",
            enabled=False,
            notes="Comparison only; disabled pending voice-rights, retention, and "
            "spend review (Hermes gate). Voice IDs assigned at consent time.",
        ),
    ]


def native_voice_candidates(language: str) -> list[BakeoffCandidate]:
    """Return native-voice bakeoff candidates for ``es`` or ``fr`` (#436)."""

    builders = {"es": spanish_candidates, "fr": french_candidates}
    try:
        return builders[language]()
    except KeyError:
        raise ValueError(f"unsupported bakeoff language: {language!r}") from None


def recommended_voice_pair(language: str) -> dict[str, str]:
    """Bakeoff-selected voice pair for ``language`` (feeds per-language config #432)."""

    try:
        return dict(RECOMMENDED_VOICE_PAIRS[language])
    except KeyError:
        raise ValueError(f"no recommended voice pair for language: {language!r}") from None


def _path_token(value: str) -> str:
    token = _PATH_TOKEN_RE.sub("-", value.strip().lower()).strip("-")
    return token or "unknown"


def blob_path_for(week: str, candidate: BakeoffCandidate) -> str:
    return (
        f"bakeoff/{_path_token(week)}/{_path_token(candidate.provider)}/"
        f"{_path_token(candidate.locale)}/{_path_token(candidate.narrator_voice)}.mp3"
    )


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
