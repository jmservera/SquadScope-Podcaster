from __future__ import annotations

import pytest

from podcaster.config import (
    DEFAULT_LANGUAGE,
    LanguageConfig,
    PodcastConfig,
    validate_language_block,
)
from podcaster.generation import (
    AI_VOICE_DISCLOSURE,
    HOST_A_NAME,
    HOST_A_VOICE,
    PODCAST_NAME,
)


def test_default_languages_include_en_es_fr_with_specific_locales():
    config = PodcastConfig()
    assert set(config.languages) == {"en", "es", "fr"}
    assert config.languages["en"].locale == "en-US"
    # Locale is specific (es-419 vs es-ES) so downstream selection can branch.
    assert config.languages["es"].locale == "es-419"
    assert config.languages["fr"].locale == "fr-FR"


def test_default_language_voices_are_bakeoff_pairs():
    config = PodcastConfig()
    es = config.languages["es"]
    fr = config.languages["fr"]
    assert es.host_a.voice == "es-MX-JorgeMultilingualNeural"
    assert es.host_b.voice == "es-MX-DaliaMultilingualNeural"
    assert fr.host_a.voice == "fr-FR-RemyMultilingualNeural"
    assert fr.host_b.voice == "fr-FR-VivienneMultilingualNeural"
    # English keeps the existing production voices.
    assert config.languages["en"].host_a.voice == HOST_A_VOICE


def test_brand_is_universal_disclosure_and_cta_are_localized():
    config = PodcastConfig()
    for block in config.languages.values():
        assert block.show_name == PODCAST_NAME  # brand stays universal
    assert config.languages["en"].disclosure == AI_VOICE_DISCLOSURE
    assert "inteligencia artificial" in config.languages["es"].disclosure
    assert "intelligence artificielle" in config.languages["fr"].disclosure
    assert config.languages["es"].cta == "Lee más en www.claracle.com"


def test_language_for_accepts_code_locale_and_falls_back():
    config = PodcastConfig()
    assert config.language_for("es").locale == "es-419"
    assert config.language_for("es-419").locale == "es-419"  # full locale
    assert config.language_for("fr-FR").locale == "fr-FR"
    # Unknown language falls back to the documented English default.
    assert config.language_for("de").locale == "en-US"
    assert config.language_for(None).locale == "en-US"
    assert DEFAULT_LANGUAGE == "en"


def test_language_block_missing_fields_fall_back_to_defaults():
    config = PodcastConfig.from_payload(
        {"podcast_config": {"languages": {"es": {"show_name": "Claracle ES"}}}}
    )
    es = config.languages["es"]
    assert es.show_name == "Claracle ES"
    # Unspecified fields keep documented defaults.
    assert es.locale == "es-419"
    assert es.host_a.voice == "es-MX-JorgeMultilingualNeural"
    # Other languages remain present.
    assert config.languages["fr"].locale == "fr-FR"


def test_language_block_overrides_voices_prompts_and_locale():
    payload = {
        "podcast_config": {
            "languages": {
                "es": {
                    "locale": "es-ES",
                    "voices": {"host_a": "es-ES-AlvaroNeural", "host_b": "es-ES-ElviraNeural"},
                    "prompts": {"script_system": "Habla en español de España."},
                    "disclosure": "Voces sintéticas.",
                    "cta": "Visita la web.",
                    "enabled": False,
                }
            }
        }
    }
    es = PodcastConfig.from_payload(payload).languages["es"]
    assert es.locale == "es-ES"
    assert es.host_a.voice == "es-ES-AlvaroNeural"
    assert es.host_b.voice == "es-ES-ElviraNeural"
    assert es.prompts["script_system"] == "Habla en español de España."
    assert es.disclosure == "Voces sintéticas."
    assert es.enabled is False


def test_hosts_array_format_supported_in_language_block():
    payload = {
        "podcast_config": {
            "languages": {
                "fr": {
                    "hosts": [
                        {"name": "Léo", "voice": "fr-FR-HenriNeural", "style": "calm"},
                        {"name": "Vera", "voice": "fr-FR-DeniseNeural", "style": "dry"},
                    ]
                }
            }
        }
    }
    fr = PodcastConfig.from_payload(payload).languages["fr"]
    assert fr.host_a.name == "Léo"
    assert fr.host_a.voice == "fr-FR-HenriNeural"
    assert fr.host_b.voice == "fr-FR-DeniseNeural"


def test_validate_language_block_rejects_malformed():
    # Well-formed block does not raise.
    validate_language_block("es", {"locale": "es-419", "voices": {"host_a": "v"}})
    with pytest.raises(ValueError, match="must be an object"):
        validate_language_block("es", ["not", "a", "mapping"])
    with pytest.raises(ValueError, match="locale"):
        validate_language_block("es", {"locale": 42})
    with pytest.raises(ValueError, match="hosts"):
        validate_language_block("es", {"hosts": {"not": "an array"}})
    with pytest.raises(ValueError, match="voices"):
        validate_language_block("es", {"voices": ["bad"]})
    with pytest.raises(ValueError, match="enabled"):
        validate_language_block("es", {"enabled": "yes"})
    with pytest.raises(ValueError, match="non-empty string"):
        validate_language_block("", {})


def test_default_for_unknown_language_uses_english_voices_and_code_locale():
    block = LanguageConfig.default_for("pt")
    assert block.locale == "pt"  # unknown code: locale defaults to the code
    assert block.host_a.voice == HOST_A_VOICE  # falls back to English voices
    assert block.host_a.name == HOST_A_NAME


def test_existing_podcast_config_payload_unaffected():
    # Payloads without a languages block keep full default language set.
    config = PodcastConfig.from_payload({"podcast_config": {"name": "Claracle"}})
    assert set(config.languages) == {"en", "es", "fr"}


def test_api_validation_accepts_well_formed_languages():
    from podcaster.validation import validate_payload

    errors = validate_payload(
        {
            "podcast_config": {
                "name": "Claracle",
                "languages": {"es": {"locale": "es-419", "voices": {"host_a": "v"}}},
            }
        }
    )
    assert not any("languages" in e for e in errors)


def test_api_validation_rejects_malformed_language_block():
    from podcaster.validation import validate_payload

    errors = validate_payload(
        {"podcast_config": {"name": "Claracle", "languages": {"es": {"locale": 42}}}}
    )
    assert any("language 'es'" in e and "locale" in e for e in errors)
