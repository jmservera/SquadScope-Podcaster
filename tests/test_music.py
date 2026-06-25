from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcaster import music  # noqa: E402


def test_registry_maps_intro_and_outro_to_summer_sport():
    registry = music.load_registry()
    assert registry["assets"], "registry must list bundled music assets"
    roles = {entry["role"]: entry for entry in registry["assets"]}
    assert roles["intro"]["file"] == "summer-sport.mp3"
    assert roles["outro"]["file"] == "summer-sport.mp3"
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
    assert intro.duration_seconds >= 100.0
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
    assert all("summer-sport.mp3" in line for line in lines)
    assert all("license:" in line for line in lines)


def test_track_attribution_names_creative_commons_license():
    """TRACK_ATTRIBUTION must carry the human-readable CC BY-SA 3.0 license text
    mandated by assets/music/ATTRIBUTION.md (issue #412)."""
    assert "Creative Commons CC BY-SA 3.0" in music.TRACK_ATTRIBUTION
    assert "https://creativecommons.org/licenses/by-sa/3.0/" in music.TRACK_ATTRIBUTION

