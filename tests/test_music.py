from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcaster import music  # noqa: E402


def test_registry_exists_and_only_allows_clear_licenses():
    registry = music.load_registry()
    assert registry["assets"], "registry must list audio assets"
    for entry in registry["assets"]:
        assert entry["license"] in music.ALLOWED_LICENSES
        assert entry["attribution"]
        assert entry["third_party_material"] is False


def test_get_stingers_returns_verified_intro_and_outro():
    intro, outro = music.get_stingers()
    assert intro.id == "intro"
    assert outro.id == "outro"
    for asset in (intro, outro):
        assert asset.path.exists()
        assert asset.path.suffix == ".mp3"
        assert asset.license in music.ALLOWED_LICENSES
        assert 2.0 <= asset.duration_seconds <= 6.0


def test_get_asset_fails_closed_on_integrity_mismatch(tmp_path, monkeypatch):
    # Point the asset dir at a copy with a tampered file but the original sha.
    real_intro = music.get_asset("intro")
    tampered_dir = tmp_path / "audio"
    tampered_dir.mkdir()
    (tampered_dir / real_intro.path.name).write_bytes(b"tampered")
    registry = music.load_registry()
    monkeypatch.setattr(music, "ASSET_DIR", tampered_dir)
    monkeypatch.setattr(music, "load_registry", lambda: registry)
    with pytest.raises(ValueError):
        music.get_asset("intro")


def test_attribution_lines_cover_every_asset():
    lines = music.attribution_lines()
    assert len(lines) == len(music.load_registry()["assets"])
    assert all("license:" in line for line in lines)
