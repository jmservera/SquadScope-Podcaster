from __future__ import annotations

from dataclasses import dataclass

import pytest

from podcaster.config import HostConfig, PodcastConfig
from podcaster.tts import TtsConfig, VoiceTurn
from podcaster.tts_providers import (
    DEFAULT_PROVIDER,
    PROVIDER_AZURE_NEURAL,
    PROVIDER_ELEVENLABS,
    PROVIDER_OPENAI,
    ProviderRouting,
    ProviderVoiceTurn,
    build_provider_plan,
    get_synthesizer,
    infer_provider,
    is_provider_wired,
    register_synthesizer,
)


def test_infer_provider_by_voice_shape():
    assert infer_provider("fable", "en-US") == PROVIDER_OPENAI
    assert infer_provider("alloy") == PROVIDER_OPENAI
    assert infer_provider("es-MX-JorgeMultilingualNeural", "es-419") == PROVIDER_AZURE_NEURAL
    assert infer_provider("fr-FR-Remy:DragonHDLatestNeural", "fr-FR") == PROVIDER_AZURE_NEURAL
    assert infer_provider("eleven_multilingual_v2", "es-419") == PROVIDER_ELEVENLABS
    assert infer_provider("elevenlabs:Rachel") == PROVIDER_ELEVENLABS


def test_infer_provider_falls_back_by_locale():
    # No recognizable voice id: non-English locale → native provider.
    assert infer_provider("", "es-419") == PROVIDER_AZURE_NEURAL
    assert infer_provider(None, "fr-FR") == PROVIDER_AZURE_NEURAL
    # English / unknown locale → default OpenAI.
    assert infer_provider("", "en-US") == DEFAULT_PROVIDER
    assert infer_provider(None, None) == DEFAULT_PROVIDER


def test_english_routes_to_openai_regression_safe():
    en = PodcastConfig().language_for("en") if hasattr(PodcastConfig(), "language_for") else None
    if en is None:
        # Fallback for a base without #432: simulate the English block.
        en = _Block("en", "en-US", "fable", "alloy")
    routing = ProviderRouting.for_language(en)
    assert routing.provider == PROVIDER_OPENAI
    assert routing.is_default_provider is True
    assert routing.voice_host_a == "fable"
    assert routing.voice_host_b == "alloy"


@dataclass
class _Block:
    language: str
    locale: str
    voice_a: str
    voice_b: str

    @property
    def host_a(self) -> HostConfig:
        return HostConfig("A", self.voice_a, "")

    @property
    def host_b(self) -> HostConfig:
        return HostConfig("B", self.voice_b, "")


def test_spanish_and_french_route_to_azure_neural():
    es = _Block("es", "es-419", "es-MX-JorgeMultilingualNeural", "es-MX-DaliaMultilingualNeural")
    fr = _Block("fr", "fr-FR", "fr-FR-RemyMultilingualNeural", "fr-FR-VivienneMultilingualNeural")
    es_routing = ProviderRouting.for_language(es)
    fr_routing = ProviderRouting.for_language(fr)
    assert es_routing.provider == PROVIDER_AZURE_NEURAL
    assert es_routing.is_default_provider is False
    assert fr_routing.provider == PROVIDER_AZURE_NEURAL
    assert fr_routing.voice_host_b == "fr-FR-VivienneMultilingualNeural"


def test_routing_describe_is_secret_safe():
    routing = ProviderRouting.for_language(
        _Block("es", "es-419", "es-MX-JorgeMultilingualNeural", "es-MX-DaliaMultilingualNeural")
    )
    summary = routing.describe()
    assert summary["provider"] == PROVIDER_AZURE_NEURAL
    assert summary["locale"] == "es-419"
    assert summary["voices"]["host_a"] == "es-MX-JorgeMultilingualNeural"
    # No tokens/endpoints leak into the summary.
    assert "token" not in summary and "endpoint" not in summary


def test_build_provider_plan_assigns_provider_and_voice_per_turn():
    routing = ProviderRouting.for_language(
        _Block("es", "es-419", "es-MX-JorgeMultilingualNeural", "es-MX-DaliaMultilingualNeural")
    )
    segments = [("host_a", "Hola"), ("host_b", "Qué tal"), ("A", "Sigamos")]
    plan = build_provider_plan(segments, routing)
    assert all(isinstance(t, ProviderVoiceTurn) for t in plan)
    assert [t.role for t in plan] == ["host_a", "host_b", "host_a"]
    assert plan[0].voice == "es-MX-JorgeMultilingualNeural"
    assert plan[1].voice == "es-MX-DaliaMultilingualNeural"
    assert all(t.provider == PROVIDER_AZURE_NEURAL for t in plan)
    # Raw text is excluded; only char_count is recorded for smoke/manifest safety.
    assert plan[0].char_count == len("Hola")
    assert plan[1].char_count == len("Qué tal")
    assert not hasattr(plan[0], "text")


def test_build_provider_plan_validates_inputs():
    routing = ProviderRouting(provider=PROVIDER_OPENAI, locale="en-US", voice_host_a="fable", voice_host_b="alloy")
    with pytest.raises(ValueError, match="at least one"):
        build_provider_plan([], routing)
    bad = ProviderRouting(provider="bogus", locale="x", voice_host_a="a", voice_host_b="b")
    with pytest.raises(ValueError, match="unknown TTS provider"):
        build_provider_plan([("a", "x")], bad)
    missing = ProviderRouting(provider=PROVIDER_OPENAI, locale="en-US", voice_host_a="", voice_host_b="alloy")
    with pytest.raises(ValueError, match="both host voices"):
        build_provider_plan([("a", "x")], missing)


def test_for_language_rejects_mixed_providers():
    """host_a on OpenAI and host_b on Azure must fail at routing time."""
    mixed = _Block("xx", "xx-XX", "fable", "es-MX-DaliaMultilingualNeural")
    with pytest.raises(ValueError, match="same TTS provider"):
        ProviderRouting.for_language(mixed)



def test_openai_synthesizer_is_wired_natives_are_gated():
    assert is_provider_wired(PROVIDER_OPENAI) is True
    assert is_provider_wired(PROVIDER_AZURE_NEURAL) is False
    assert is_provider_wired(PROVIDER_ELEVENLABS) is False
    with pytest.raises(NotImplementedError, match="not yet wired"):
        get_synthesizer(PROVIDER_AZURE_NEURAL)(
            VoiceTurn(role="host_a", voice="v", deployment="d", text="t"),
            TtsConfig(None, None, None, None, None, None),
        )


def test_openai_synthesizer_delegates_to_existing_path(monkeypatch):
    captured = {}

    def fake_synth(turn, config, **kwargs):
        captured["voice"] = turn.voice
        return b"ID3-audio"

    import podcaster.tts_providers as mod

    monkeypatch.setattr(mod, "synthesize_turn", fake_synth)
    audio = get_synthesizer(PROVIDER_OPENAI)(
        VoiceTurn(role="host_a", voice="fable", deployment="dep", text="hi"),
        TtsConfig("https://e", "dep", None, "fable", "alloy", "managed_identity"),
    )
    assert audio == b"ID3-audio"
    assert captured["voice"] == "fable"


def test_register_synthesizer_allows_wiring_native_provider():
    sentinel = lambda *a, **k: b"native-audio"  # noqa: E731
    register_synthesizer(PROVIDER_AZURE_NEURAL, sentinel)
    try:
        assert get_synthesizer(PROVIDER_AZURE_NEURAL) is sentinel
        assert is_provider_wired(PROVIDER_AZURE_NEURAL) is True
    finally:
        # Restore the gated stub so other tests see the default state.
        from podcaster.tts_providers import _gated_synthesizer

        register_synthesizer(PROVIDER_AZURE_NEURAL, _gated_synthesizer(PROVIDER_AZURE_NEURAL))
    with pytest.raises(ValueError, match="provider id is required"):
        register_synthesizer("", sentinel)
