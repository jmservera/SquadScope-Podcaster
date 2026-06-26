"""Tests for per-language Spotify show targets (#438)."""

from __future__ import annotations

import pytest

from podcaster.spotify_shows import (
    SPOTIFY_LANGUAGE_TAGS,
    ShowTarget,
    build_show_targets,
    resolve_show_target,
)

# --- resolve_show_target: env resolution --------------------------------------


def test_english_reads_legacy_show_id_env():
    target = resolve_show_target("en", env={"SPOTIFY_SHOW_ID": "show-en"})
    assert target.show_id == "show-en"
    assert target.language == "en"
    assert target.language_tag == "en"
    assert target.env_var == "SPOTIFY_SHOW_ID"
    assert target.is_resolved


def test_spanish_reads_per_language_env():
    env = {"SPOTIFY_SHOW_ID": "show-en", "SPOTIFY_SHOW_ID_ES": "show-es"}
    target = resolve_show_target("es", env=env)
    assert target.show_id == "show-es"
    assert target.language == "es"
    assert target.language_tag == "es"
    assert target.env_var == "SPOTIFY_SHOW_ID_ES"


def test_french_full_locale_code():
    target = resolve_show_target("fr-FR", env={"SPOTIFY_SHOW_ID_FR": "show-fr"})
    assert target.show_id == "show-fr"
    assert target.language == "fr"
    assert target.show_name == "Claracle Hebdo"


def test_non_english_unresolved_when_specific_env_missing():
    """Missing per-language env var leaves show_id empty; no cross-language fallback."""
    target = resolve_show_target("es", env={"SPOTIFY_SHOW_ID": "show-en"})
    assert target.show_id == ""
    assert not target.is_resolved
    # env_var still points to the per-language name for accurate error messages.
    assert target.env_var == "SPOTIFY_SHOW_ID_ES"


def test_unresolved_when_no_env():
    target = resolve_show_target("es", env={})
    assert target.show_id == ""
    assert not target.is_resolved


# --- resolve_show_target: config-driven (#432 duck-typed) ----------------------


class _FakeLanguageConfig:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_config_show_id_overrides_env():
    cfg = _FakeLanguageConfig(spotify_show_id="cfg-show", locale="es-419")
    target = resolve_show_target(
        "es", language_config=cfg, env={"SPOTIFY_SHOW_ID_ES": "env-show"}
    )
    assert target.show_id == "cfg-show"
    assert target.language_tag == "es"


def test_config_language_tag_and_show_name():
    cfg = _FakeLanguageConfig(
        spotify_show_id="cfg-show",
        spotify_language_tag="es-MX",
        show_name="Mi Show",
    )
    target = resolve_show_target("es", language_config=cfg, env={})
    assert target.language_tag == "es-MX"
    assert target.show_name == "Mi Show"


# --- defaults / fallbacks -----------------------------------------------------


def test_unknown_language_tag_defaults_to_code():
    target = resolve_show_target("de", env={"SPOTIFY_SHOW_ID_DE": "x"})
    assert target.language_tag == "de"
    assert "de" not in SPOTIFY_LANGUAGE_TAGS


def test_default_show_names():
    assert resolve_show_target("en", env={}).show_name == "Claracle Weekly"
    assert resolve_show_target("es", env={}).show_name == "Claracle Semanal"
    assert resolve_show_target("fr", env={}).show_name == "Claracle Hebdo"


# --- build_show_targets -------------------------------------------------------


def test_build_show_targets_map():
    env = {
        "SPOTIFY_SHOW_ID": "show-en",
        "SPOTIFY_SHOW_ID_ES": "show-es",
        "SPOTIFY_SHOW_ID_FR": "show-fr",
    }
    targets = build_show_targets(["en", "es", "fr"], env=env)
    assert set(targets) == {"en", "es", "fr"}
    assert targets["es"].show_id == "show-es"
    assert all(isinstance(t, ShowTarget) for t in targets.values())


# --- publish._get_credentials integration -------------------------------------


def test_get_credentials_english_unchanged(monkeypatch):
    from podcaster import publish

    monkeypatch.setenv("SPOTIFY_SHOW_ID", "show-en")
    monkeypatch.setenv("SP_DC", "dc")
    monkeypatch.setenv("SP_KEY", "key")
    show_id, sp_dc, sp_key = publish._get_credentials()
    assert (show_id, sp_dc, sp_key) == ("show-en", "dc", "key")


def test_get_credentials_per_language(monkeypatch):
    from podcaster import publish

    monkeypatch.setenv("SPOTIFY_SHOW_ID", "show-en")
    monkeypatch.setenv("SPOTIFY_SHOW_ID_FR", "show-fr")
    monkeypatch.setenv("SP_DC", "dc")
    monkeypatch.setenv("SP_KEY", "key")
    show_id, _, _ = publish._get_credentials("fr")
    assert show_id == "show-fr"


def test_get_credentials_missing_reports_per_language_env(monkeypatch):
    from podcaster import publish

    monkeypatch.delenv("SPOTIFY_SHOW_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_SHOW_ID_ES", raising=False)
    monkeypatch.setenv("SP_DC", "dc")
    monkeypatch.setenv("SP_KEY", "key")
    with pytest.raises(ValueError, match="SPOTIFY_SHOW_ID_ES"):
        publish._get_credentials("es")
