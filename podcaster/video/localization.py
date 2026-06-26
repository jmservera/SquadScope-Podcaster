"""Localized copy for video overlays — intro/outro & section cards (#437).

All on-screen text in the video overlay path is parameterized per locale so a
Spanish or French episode renders Spanish/French intro, outro, CTA, and section
cards while reusing the language-independent browser recordings unchanged.

The show name comes from the per-language config (#432, ``LanguageConfig``); the
remaining overlay copy (outro CTA, default episode title, section-card names) is
Farnsworth-supplied translated/rewritten copy and lives here as the single
source of truth. English copy is identical to the previous hard-coded values so
existing English renders are byte-for-byte unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_LOCALE = "en"

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_section_name(name: str) -> str:
    """Lower-case, collapse whitespace, and spell ``&`` as ``and`` for lookup."""

    collapsed = _WHITESPACE_RE.sub(" ", (name or "").strip().lower())
    return collapsed.replace(" & ", " and ")


# Canonical English section display names (match section_cards.KNOWN_SECTIONS),
# with native translations for es/fr. Keyed by normalized English name.
_SECTION_NAME_TRANSLATIONS: dict[str, dict[str, str]] = {
    "trends": {"es": "Tendencias", "fr": "Tendances"},
    "industry": {"es": "Industria", "fr": "Industrie"},
    "signal and noise": {"es": "Señal y Ruido", "fr": "Signal et Bruit"},
    "blind spots": {"es": "Puntos Ciegos", "fr": "Angles Morts"},
    "deep dive": {"es": "Análisis Profundo", "fr": "Plongée en Profondeur"},
    "hot off the press": {"es": "Recién Salido", "fr": "À la Une"},
    "breaking news": {"es": "Última Hora", "fr": "Dernière Minute"},
}


@dataclass(frozen=True)
class OverlayCopy:
    """Localized strings for the overlay path (one locale)."""

    locale: str
    show_name: str
    outro_cta: str
    default_episode_title: str
    section_names: dict[str, str] = field(default_factory=dict)

    def section_name(self, english_name: str) -> str:
        """Localize a section display name, falling back to the original."""

        return self.section_names.get(_normalize_section_name(english_name), english_name)


def _section_names_for(language: str) -> dict[str, str]:
    return {
        key: translations[language]
        for key, translations in _SECTION_NAME_TRANSLATIONS.items()
        if language in translations
    }


# English copy reproduces the previous hard-coded overlay values exactly.
OVERLAY_COPY: dict[str, OverlayCopy] = {
    "en": OverlayCopy(
        locale="en-US",
        show_name="Claracle Weekly",
        outro_cta="Subscribe & Follow",
        default_episode_title="Untitled Episode",
        section_names={},  # English names are canonical; no translation needed.
    ),
    "es": OverlayCopy(
        locale="es-419",
        show_name="Claracle Semanal",
        outro_cta="Suscríbete y síguenos",
        default_episode_title="Episodio sin título",
        section_names=_section_names_for("es"),
    ),
    "fr": OverlayCopy(
        locale="fr-FR",
        show_name="Claracle Hebdo",
        outro_cta="Abonnez-vous et suivez-nous",
        default_episode_title="Épisode sans titre",
        section_names=_section_names_for("fr"),
    ),
}


def _language_key(locale: str | None) -> str:
    return (locale or "").split("-", 1)[0].strip().lower()


def overlay_copy_for(locale: str | None) -> OverlayCopy:
    """Return localized overlay copy for a language code or full locale.

    Unknown locales fall back to English so a new language never breaks rendering.
    """

    key = _language_key(locale)
    return OVERLAY_COPY.get(key, OVERLAY_COPY[DEFAULT_LOCALE])


def localize_section_name(english_name: str, locale: str | None) -> str:
    """Localize a section card display name for the given locale."""

    return overlay_copy_for(locale).section_name(english_name)
