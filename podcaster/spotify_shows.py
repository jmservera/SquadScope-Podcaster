"""Per-language Spotify show targets (#438).

Spotify language is a *show-level* property, so each language publishes to its
**own** Spotify show — Claracle Weekly (en), Claracle Semanal (es), Claracle
Hebdo (fr) — rather than mixing languages in one feed (mixed feeds hurt
discovery). This module resolves, per locale, which Spotify show to publish to
and the show-level language tag, in a config-driven (#432), network-free way.

Resolution order for a language's show id:
1. ``language_config.spotify_show_id`` (duck-typed per-language config, #432)
2. ``SPOTIFY_SHOW_ID_<LANG>`` environment variable (e.g. ``SPOTIFY_SHOW_ID_ES``)
3. English only: ``SPOTIFY_SHOW_ID`` (preserves the existing single-show env)

English behavior is unchanged: ``resolve_show_target("en")`` reads
``SPOTIFY_SHOW_ID`` exactly as before.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_LANGUAGE = "en"

#: Show-level language tag Spotify expects per language. Fallback is the bare
#: language code so a new locale still produces a sensible tag.
SPOTIFY_LANGUAGE_TAGS: dict[str, str] = {
    "en": "en",
    "es": "es",
    "fr": "fr",
}

#: Default branding per language (mirrors video overlay copy, #437).
_DEFAULT_SHOW_NAMES: dict[str, str] = {
    "en": "Claracle Weekly",
    "es": "Claracle Semanal",
    "fr": "Claracle Hebdo",
}


@dataclass(frozen=True)
class ShowTarget:
    """The resolved Spotify show to publish a given language to."""

    language: str
    show_id: str
    language_tag: str
    show_name: str
    env_var: str  # primary env var consulted (for accurate error messages)

    @property
    def is_resolved(self) -> bool:
        return bool(self.show_id)


def _language_key(locale: str | None) -> str:
    return (locale or "").split("-", 1)[0].strip().lower() or DEFAULT_LANGUAGE


def _show_id_env_var(language: str) -> str:
    if language == DEFAULT_LANGUAGE:
        return "SPOTIFY_SHOW_ID"
    return f"SPOTIFY_SHOW_ID_{language.upper()}"


def _getattr_str(obj: object, name: str) -> str:
    value = getattr(obj, name, None)
    return value.strip() if isinstance(value, str) else ""


def resolve_show_target(
    language: str | None,
    *,
    language_config: object | None = None,
    env: Mapping[str, str] | None = None,
) -> ShowTarget:
    """Resolve the Spotify show target for a language code or full locale.

    Args:
        language: Language code (``"en"``) or locale (``"es-419"``).
        language_config: Optional per-language config object (#432). Duck-typed:
            ``spotify_show_id``, ``spotify_language_tag``, ``locale`` and
            ``show_name`` attributes are consulted when present.
        env: Environment mapping (defaults to ``os.environ``).
    """

    env = os.environ if env is None else env
    key = _language_key(language)
    env_var = _show_id_env_var(key)

    # 1) explicit per-language config show id (duck-typed, #432)
    show_id = _getattr_str(language_config, "spotify_show_id")
    # 2) per-language env var
    if not show_id:
        show_id = (env.get(env_var) or "").strip()
    # 3) English-only fallback to the legacy single-show env var
    if not show_id and key != DEFAULT_LANGUAGE:
        show_id = (env.get("SPOTIFY_SHOW_ID") or "").strip()

    language_tag = _getattr_str(language_config, "spotify_language_tag")
    if not language_tag:
        config_locale = _getattr_str(language_config, "locale")
        if config_locale:
            language_tag = _language_key(config_locale)
    if not language_tag:
        language_tag = SPOTIFY_LANGUAGE_TAGS.get(key, key)

    show_name = (
        _getattr_str(language_config, "show_name")
        or _DEFAULT_SHOW_NAMES.get(key, _DEFAULT_SHOW_NAMES[DEFAULT_LANGUAGE])
    )

    return ShowTarget(
        language=key,
        show_id=show_id,
        language_tag=language_tag,
        show_name=show_name,
        env_var=env_var,
    )


def build_show_targets(
    languages: object,
    *,
    language_configs: Mapping[str, object] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, ShowTarget]:
    """Resolve a ``{language: ShowTarget}`` map for an iterable of languages.

    ``language_configs`` is an optional ``{language: per-language config}`` map
    (#432); each entry is duck-typed via :func:`resolve_show_target`.
    """

    configs = language_configs or {}
    targets: dict[str, ShowTarget] = {}
    for raw in languages:
        key = _language_key(raw if isinstance(raw, str) else getattr(raw, "language", None))
        targets[key] = resolve_show_target(
            key, language_config=configs.get(key), env=env
        )
    return targets
