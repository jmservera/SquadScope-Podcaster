"""TTS provider abstraction for native multilingual voices (#435).

OpenAI ``fable``/``alloy`` are English-optimized; Spanish and French need native
voices (Azure Neural / ElevenLabs, selected by the #436 bakeoff). This module
adds a thin routing + plan layer on top of :mod:`podcaster.tts` so the right
provider and voice pair are chosen *by locale*, while English stays on the
existing OpenAI path untouched (regression-safe).

Design:
- :func:`infer_provider` maps a voice id + locale to a provider id.
- :class:`ProviderRouting` resolves the provider + host voice pair for a
  language (duck-typed from the #432 ``LanguageConfig`` so there is no hard
  import dependency between the two features).
- :func:`build_provider_plan` produces a dry-run-safe, secret-free per-turn plan
  (the smoke path required by the issue). Actual native-voice synthesis is
  registered behind :class:`SynthesizerRegistry`; the OpenAI synthesizer wraps
  the existing path, and the native providers are gated until their credentials
  and synth wiring land (mirrors the bakeoff provider gates).

No secrets, tokens, or endpoints are placed in code or in the plan output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from podcaster.tts import (
    HOST_A_ROLE,
    HOST_B_ROLE,
    TtsConfig,
    VoiceTurn,
    normalize_role,
    synthesize_turn,
)

PROVIDER_OPENAI = "openai-tts"
PROVIDER_AZURE_NEURAL = "azure-speech"
PROVIDER_ELEVENLABS = "elevenlabs"

KNOWN_PROVIDERS = frozenset({PROVIDER_OPENAI, PROVIDER_AZURE_NEURAL, PROVIDER_ELEVENLABS})

# The default language stays on OpenAI; only non-English locales route to a
# native-voice provider.
DEFAULT_PROVIDER = PROVIDER_OPENAI

# Short OpenAI voice names (English-optimized) — the existing production voices.
_OPENAI_VOICES = frozenset(
    {"alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"}
)

# Azure Neural voice ids look like ``es-MX-JorgeMultilingualNeural`` or
# ``fr-FR-Remy:DragonHDLatestNeural``.
_AZURE_NEURAL_RE = re.compile(r"^[a-z]{2}-[A-Za-z0-9]+-.*Neural", re.IGNORECASE)

# ElevenLabs model/voice ids are explicitly prefixed in config to disambiguate.
_ELEVENLABS_RE = re.compile(r"^(eleven_|elevenlabs:)", re.IGNORECASE)


def infer_provider(voice_id: str | None, locale: str | None = None) -> str:
    """Infer the TTS provider for a voice id (locale is a tie-breaker).

    Resolution order: explicit ElevenLabs prefix → Azure Neural id shape →
    short OpenAI voice name → fall back by locale (non-English → Azure Neural,
    English/unknown → OpenAI).
    """

    voice = (voice_id or "").strip()
    if voice:
        if _ELEVENLABS_RE.match(voice):
            return PROVIDER_ELEVENLABS
        if _AZURE_NEURAL_RE.match(voice):
            return PROVIDER_AZURE_NEURAL
        if voice.lower() in _OPENAI_VOICES:
            return PROVIDER_OPENAI

    short = (locale or "").split("-", 1)[0].lower()
    if short and short not in ("", "en"):
        return PROVIDER_AZURE_NEURAL
    return DEFAULT_PROVIDER


@dataclass(frozen=True)
class ProviderRouting:
    """Resolved provider + host voice pair for one language."""

    provider: str
    locale: str
    voice_host_a: str
    voice_host_b: str
    language: str = ""

    @property
    def is_default_provider(self) -> bool:
        return self.provider == DEFAULT_PROVIDER

    def voice_for(self, role: str) -> str:
        return self.voice_host_b if normalize_role(role) == HOST_B_ROLE else self.voice_host_a

    @classmethod
    def for_language(cls, block: Any) -> "ProviderRouting":
        """Build routing from a #432 ``LanguageConfig``-shaped object (duck-typed)."""

        locale = getattr(block, "locale", "") or ""
        language = getattr(block, "language", "") or ""
        host_a = getattr(block, "host_a", None)
        host_b = getattr(block, "host_b", None)
        voice_a = getattr(host_a, "voice", "") if host_a is not None else ""
        voice_b = getattr(host_b, "voice", "") if host_b is not None else ""
        # Infer provider from host-A voice; when host-B is also configured,
        # validate that both voices resolve to the same provider so a mixed
        # OpenAI + Azure config is caught at plan time, not synthesis time.
        provider = infer_provider(voice_a, locale)
        if voice_b:
            provider_b = infer_provider(voice_b, locale)
            if provider_b != provider:
                raise ValueError(
                    f"host_a voice {voice_a!r} maps to provider {provider!r} but "
                    f"host_b voice {voice_b!r} maps to {provider_b!r}; "
                    "both hosts must use the same TTS provider for a language block."
                )
        return cls(
            provider=provider,
            locale=locale,
            voice_host_a=voice_a,
            voice_host_b=voice_b,
            language=language,
        )

    def describe(self) -> dict[str, Any]:
        """Secret-safe routing summary for manifests / smoke output."""

        return {
            "provider": self.provider,
            "language": self.language,
            "locale": self.locale,
            "voices": {HOST_A_ROLE: self.voice_host_a, HOST_B_ROLE: self.voice_host_b},
            "is_default_provider": self.is_default_provider,
        }


@dataclass(frozen=True)
class ProviderVoiceTurn:
    """A turn assigned to a provider + voice (dry-run-safe; no audio, no raw text).

    Only metadata needed for smoke/validation is kept here — role, provider,
    voice, and character count.  Raw script text is intentionally omitted so
    this plan structure stays safe for manifests and logging output (consistent
    with the "secret-free" design stated in the module docstring).
    """

    provider: str
    role: str
    voice: str
    char_count: int


def build_provider_plan(
    segments: Sequence[tuple[str, str]],
    routing: ProviderRouting,
) -> list[ProviderVoiceTurn]:
    """Assign each ``(speaker_label, text)`` segment to a provider + voice.

    Network-free smoke/dry-run path: validates the routing and produces the
    per-turn provider/voice assignment without synthesizing audio.
    """

    if routing.provider not in KNOWN_PROVIDERS:
        raise ValueError(f"unknown TTS provider: {routing.provider!r}")
    if not routing.voice_host_a or not routing.voice_host_b:
        raise ValueError("provider plan requires both host voices configured")
    if not segments:
        raise ValueError("provider plan requires at least one speaker-labelled segment")

    plan: list[ProviderVoiceTurn] = []
    for label, text in segments:
        role = HOST_B_ROLE if normalize_role(label) == HOST_B_ROLE else HOST_A_ROLE
        plan.append(
            ProviderVoiceTurn(
                provider=routing.provider,
                role=role,
                voice=routing.voice_for(role),
                char_count=len(text),
            )
        )
    return plan


# --- Synthesizer registry --------------------------------------------------
#
# Each provider supplies a callable ``(VoiceTurn, TtsConfig, *, token_provider,
# transport) -> bytes``. Only OpenAI is wired to a real path today; the native
# providers are registered as gated stubs so es/fr stay publication-blocked
# until their synth + credentials land (consistent with the bakeoff gates).

Synthesizer = Callable[..., bytes]


def _openai_synthesizer(turn: VoiceTurn, config: TtsConfig, **kwargs: Any) -> bytes:
    return synthesize_turn(turn, config, **kwargs)


def _gated_synthesizer(provider: str) -> Synthesizer:
    def _synth(*_args: Any, **_kwargs: Any) -> bytes:
        raise NotImplementedError(
            f"{provider} synthesis is not yet wired; the provider abstraction selects "
            f"it (#435) but native-voice synthesis + credentials land in a follow-up."
        )

    _synth._is_gated = True  # type: ignore[attr-defined]
    return _synth


_REGISTRY: dict[str, Synthesizer] = {
    PROVIDER_OPENAI: _openai_synthesizer,
    PROVIDER_AZURE_NEURAL: _gated_synthesizer(PROVIDER_AZURE_NEURAL),
    PROVIDER_ELEVENLABS: _gated_synthesizer(PROVIDER_ELEVENLABS),
}


def get_synthesizer(provider: str) -> Synthesizer:
    """Return the registered synthesizer for a provider id."""

    try:
        return _REGISTRY[provider]
    except KeyError:
        raise ValueError(f"unknown TTS provider: {provider!r}") from None


def register_synthesizer(provider: str, synthesizer: Synthesizer) -> None:
    """Register/override a provider synthesizer (used when native paths land)."""

    if not provider or not provider.strip():
        raise ValueError("provider id is required")
    _REGISTRY[provider.strip()] = synthesizer


def is_provider_wired(provider: str) -> bool:
    """True when a provider has a real (non-gated) synthesizer registered."""

    synth = _REGISTRY.get(provider)
    return synth is not None and not getattr(synth, "_is_gated", False)
