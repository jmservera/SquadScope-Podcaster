"""Tests for localized video overlays — intro/outro & section cards (#437)."""

from __future__ import annotations

from pathlib import Path

from podcaster.video.intro_outro import (
    IntroConfig,
    OutroConfig,
    _render_intro_html,
    _render_outro_html,
)
from podcaster.video.localization import (
    OVERLAY_COPY,
    localize_section_name,
    overlay_copy_for,
)
from podcaster.video.section_cards import (
    SectionCardConfig,
    SectionMarker,
    _build_section_card_cmd,
)

# --- overlay_copy_for ---------------------------------------------------------


def test_overlay_copy_for_known_languages():
    assert overlay_copy_for("en").show_name == "Claracle Weekly"
    assert overlay_copy_for("es").show_name == "Claracle Semanal"
    assert overlay_copy_for("fr").show_name == "Claracle Hebdo"


def test_overlay_copy_accepts_full_locale_codes():
    assert overlay_copy_for("es-419").show_name == "Claracle Semanal"
    assert overlay_copy_for("fr-FR").show_name == "Claracle Hebdo"


def test_overlay_copy_unknown_falls_back_to_english():
    assert overlay_copy_for("de").show_name == OVERLAY_COPY["en"].show_name
    assert overlay_copy_for(None).show_name == OVERLAY_COPY["en"].show_name


# --- localize_section_name ----------------------------------------------------


def test_localize_section_name_spanish_and_french():
    assert localize_section_name("Signal & Noise", "es") == "Señal y Ruido"
    assert localize_section_name("Signal & Noise", "fr") == "Signal et Bruit"
    assert localize_section_name("Blind Spots", "es") == "Puntos Ciegos"
    assert localize_section_name("Deep Dive", "fr") == "Plongée en Profondeur"


def test_localize_section_name_english_unchanged():
    for name in ("Trends", "Signal & Noise", "Blind Spots", "Breaking News"):
        assert localize_section_name(name, "en") == name


def test_localize_section_name_unknown_section_falls_back():
    assert localize_section_name("Mystery Hour", "es") == "Mystery Hour"


# --- IntroConfig / OutroConfig localized factories ----------------------------


def test_intro_config_for_locale_uses_localized_defaults():
    cfg = IntroConfig.for_locale("es")
    assert cfg.show_name == "Claracle Semanal"
    assert cfg.episode_title == "Episodio sin título"
    assert cfg.locale == "es"


def test_intro_config_for_locale_allows_overrides():
    cfg = IntroConfig.for_locale("fr", episode_title="Mon Épisode", show_name="Custom")
    assert cfg.show_name == "Custom"
    assert cfg.episode_title == "Mon Épisode"


def test_outro_config_for_locale_uses_localized_cta():
    assert OutroConfig.for_locale("es").cta == "Suscríbete y síguenos"
    assert OutroConfig.for_locale("fr").cta == "Abonnez-vous et suivez-nous"
    assert OutroConfig.for_locale("en").cta == "Subscribe & Follow"


def test_outro_config_defaults_remain_english():
    # Default construction (no locale) preserves the original English behavior.
    cfg = OutroConfig()
    assert cfg.show_name == "Claracle Weekly"
    assert cfg.cta == "Subscribe & Follow"
    assert cfg.locale == "en"


# --- HTML rendering -----------------------------------------------------------


def test_render_outro_html_english_unchanged():
    html = _render_outro_html(OutroConfig())
    assert "Subscribe &amp; Follow" in html
    assert "Claracle Weekly" in html


def test_render_outro_html_localized_cta_present_no_english():
    html = _render_outro_html(OutroConfig.for_locale("fr"))
    assert "Abonnez-vous et suivez-nous" in html
    assert "Subscribe &amp; Follow" not in html
    assert "Subscribe & Follow" not in html


def test_render_intro_html_localized_show_name():
    html = _render_intro_html(IntroConfig.for_locale("es"))
    assert "Claracle Semanal" in html
    assert "Episodio sin t" in html  # accented title rendered


# --- section card ffmpeg command ----------------------------------------------


def test_section_card_cmd_localizes_text_spanish():
    marker = SectionMarker(name="Signal & Noise", position=0)
    cmd = _build_section_card_cmd(
        marker, Path("/tmp/card.mp4"), SectionCardConfig(locale="es")
    )
    joined = " ".join(cmd)
    assert "Se" in joined and "al y Ruido" in joined  # Señal y Ruido split across join boundaries
    assert "Signal & Noise" not in joined


def test_section_card_cmd_english_unchanged():
    marker = SectionMarker(name="Signal & Noise", position=0)
    cmd = _build_section_card_cmd(
        marker, Path("/tmp/card.mp4"), SectionCardConfig()
    )
    joined = " ".join(cmd)
    assert "Signal & Noise" in joined
