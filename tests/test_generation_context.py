from __future__ import annotations

from dataclasses import dataclass

from podcaster.config import HostConfig, PodcastConfig
from podcaster.script_gen import (
    GenerationContext,
    _build_system_prompt,
    language_display_name,
)


def _es_context() -> GenerationContext:
    return GenerationContext(
        language="es",
        locale="es-419",
        host_a=HostConfig("Mateo", "es-MX-JorgeMultilingualNeural", "bright"),
        host_b=HostConfig("Lucía", "es-MX-DaliaMultilingualNeural", "dry"),
        disclosure="Ambas voces de este programa son generadas por IA.",
        cta="Más detalles en claracle.com",
    )


def test_default_context_is_english_and_adds_no_directive():
    prompt = _build_system_prompt(PodcastConfig(), generation_context=GenerationContext())
    assert "ORIGINALLY in" not in prompt
    assert "NOT a translation" not in prompt


def test_no_context_behaviour_unchanged():
    # Passing no generation_context must be identical to the legacy call.
    pc = PodcastConfig()
    assert _build_system_prompt(pc) == _build_system_prompt(pc, generation_context=None)


def test_spanish_context_instructs_original_authoring_not_translation():
    prompt = _build_system_prompt(PodcastConfig(), generation_context=_es_context())
    assert "ORIGINALLY in Spanish (Latin American)" in prompt
    assert "NOT a translation" in prompt
    # Entities preserved verbatim.
    assert "GitHub" in prompt and "OIDC" in prompt
    # Localized CTA threaded through; site noted as English.
    assert "Más detalles en claracle.com" in prompt
    assert "is English" in prompt


def test_context_overlays_localized_hosts_and_disclosure():
    ctx = _es_context()
    overlaid = ctx.apply_to(PodcastConfig())
    assert overlaid.host_a.name == "Mateo"
    assert overlaid.host_b.voice == "es-MX-DaliaMultilingualNeural"
    assert overlaid.ai_voice_disclosure == "Ambas voces de este programa son generadas por IA."
    # The localized disclosure appears in the prompt's required-statement rule.
    prompt = _build_system_prompt(overlaid, generation_context=ctx)
    assert "Ambas voces de este programa son generadas por IA." in prompt


def test_apply_to_is_noop_for_empty_context():
    pc = PodcastConfig()
    assert GenerationContext().apply_to(pc) is pc


def test_language_display_name_resolves_code_locale_and_fallback():
    assert language_display_name("fr", "fr-FR") == "French (France)"
    assert language_display_name("es", "es-ES") == "Spanish (Spain)"
    assert language_display_name("es", "es-419") == "Spanish (Latin American)"
    # Unknown falls back to the locale string.
    assert language_display_name("pt", "pt-BR") == "pt-BR"


def test_is_default_language_detects_english_variants():
    assert GenerationContext(language="en", locale="en-US").is_default_language
    assert GenerationContext(language="en-GB", locale="en-GB").is_default_language
    assert not GenerationContext(language="es", locale="es-419").is_default_language
    assert not GenerationContext(language="fr", locale="fr-FR").is_default_language


def test_from_language_config_duck_types_a_block():
    @dataclass
    class FakeBlock:
        language: str
        locale: str
        host_a: HostConfig
        host_b: HostConfig
        disclosure: str
        cta: str

    block = FakeBlock(
        language="fr",
        locale="fr-FR",
        host_a=HostConfig("Léo", "fr-FR-RemyMultilingualNeural", "calm"),
        host_b=HostConfig("Véra", "fr-FR-VivienneMultilingualNeural", "dry"),
        disclosure="Les deux voix sont générées par IA.",
        cta="Plus d'infos sur claracle.com",
    )
    ctx = GenerationContext.from_language_config(block)
    assert ctx.language == "fr"
    assert ctx.locale == "fr-FR"
    assert ctx.host_a.voice == "fr-FR-RemyMultilingualNeural"
    assert ctx.disclosure == "Les deux voix sont générées par IA."
    assert ctx.display_name == "French (France)"


def test_generation_context_composes_with_language_config_from_432():
    # Forward-compatible: when #432 lands, LanguageConfig blocks plug straight in.
    try:
        from podcaster.config import PodcastConfig as _PC

        block = _PC().languages["es"] if hasattr(_PC(), "languages") else None
    except Exception:
        block = None
    if block is None:
        return  # #432 not present on this base; nothing to assert
    ctx = GenerationContext.from_language_config(block)
    assert ctx.locale == "es-419"
    assert not ctx.is_default_language
