from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcaster import music  # noqa: E402


def test_registry_maps_intro_and_outro_to_claracle_theme():
    registry = music.load_registry()
    assert registry["assets"], "registry must list bundled music assets"
    roles = {entry["role"]: entry for entry in registry["assets"]}
    assert roles["intro"]["file"] == "claracle-theme.mp3"
    assert roles["outro"]["file"] == "claracle-theme.mp3"
    assert roles["intro"]["license"] in music.ALLOWED_LICENSES
    assert roles["outro"]["license"] in music.ALLOWED_LICENSES


def test_get_stingers_returns_same_music_file_for_both_roles():
    intro, outro = music.get_stingers()
    assert intro.id == "intro"
    assert outro.id == "outro"
    assert intro.path == outro.path == music.TRACK_PATH
    assert intro.path.exists()
    assert intro.path.suffix == ".mp3"
    assert intro.license in music.ALLOWED_LICENSES
    assert intro.duration_seconds >= 80.0
    assert intro.sha256 == outro.sha256


def test_get_asset_requires_known_role():
    with pytest.raises(KeyError):
        music.get_asset("bridge")


def test_get_asset_fails_when_track_is_missing(tmp_path, monkeypatch):
    missing_track = tmp_path / "missing.mp3"
    monkeypatch.setattr(music, "TRACK_PATH", missing_track)
    with pytest.raises(FileNotFoundError):
        music.get_asset("intro")


def test_attribution_lines_cover_every_asset():
    lines = music.attribution_lines()
    assert len(lines) == len(music.load_registry()["assets"])
    assert all("claracle-theme.mp3" in line for line in lines)
    assert all("license:" in line for line in lines)


def test_track_attribution_names_owner_and_copyright():
    """TRACK_ATTRIBUTION must name the owner-composed track and not contain
    the superseded third-party CC BY-SA 3.0 notice (issue #412 follow-up)."""
    assert "jmservera" in music.TRACK_ATTRIBUTION
    assert "Creative Commons" not in music.TRACK_ATTRIBUTION
    assert "AudioCoffee" not in music.TRACK_ATTRIBUTION
