"""Tests for branded intro/outro seeding into storage (#586).

These exercise ``ensure_branded_intro_outro`` without Playwright or ffmpeg: the
function only reads local asset bytes, uploads them via the storage backend, and
clears the on-disk fetch cache.
"""

from __future__ import annotations

from pathlib import Path

from podcaster.video.intro_outro import (
    INTRO_OUTRO_ASSET_DIR_ENV,
    branded_intro_outro_asset_dir,
    ensure_branded_intro_outro,
)
from podcaster.video.video_compose import (
    INTRO_BLOB_PATH,
    OUTRO_BLOB_PATH,
    _default_intro_outro_cache_dir,
)


class _RecordingStorage:
    """Captures put_bytes(path, data, content_type) calls."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []

    def put_bytes(self, path: str, data: bytes, content_type: str) -> None:
        self.puts.append((path, data, content_type))


def _write_branded_assets(asset_dir: Path) -> tuple[bytes, bytes]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    intro_bytes = b"BRANDED-INTRO-CLIP"
    outro_bytes = b"BRANDED-OUTRO-CLIP"
    (asset_dir / "intro.mp4").write_bytes(intro_bytes)
    (asset_dir / "outro.mp4").write_bytes(outro_bytes)
    return intro_bytes, outro_bytes


class TestEnsureBrandedIntroOutro:
    def test_seeds_branded_clips_to_canonical_blob_paths(self, tmp_path):
        asset_dir = tmp_path / "branded"
        intro_bytes, outro_bytes = _write_branded_assets(asset_dir)
        storage = _RecordingStorage()

        seeded = ensure_branded_intro_outro(storage, asset_dir=asset_dir)

        assert seeded is True
        by_path = {p: (data, ct) for p, data, ct in storage.puts}
        assert by_path[INTRO_BLOB_PATH] == (intro_bytes, "video/mp4")
        assert by_path[OUTRO_BLOB_PATH] == (outro_bytes, "video/mp4")

    def test_clears_stale_fetch_cache(self, tmp_path, monkeypatch):
        # Point the cache dir at a temp location holding a stale (title-card) clip.
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        stale_intro = cache_dir / "intro.mp4"
        stale_outro = cache_dir / "outro.mp4"
        stale_intro.write_bytes(b"STALE-TITLE-CARD")
        stale_outro.write_bytes(b"STALE-TITLE-CARD")
        monkeypatch.setattr(
            "podcaster.video.video_compose._default_intro_outro_cache_dir",
            lambda: cache_dir,
        )

        asset_dir = tmp_path / "branded"
        _write_branded_assets(asset_dir)
        ensure_branded_intro_outro(_RecordingStorage(), asset_dir=asset_dir)

        assert not stale_intro.exists()
        assert not stale_outro.exists()

    def test_noop_when_assets_absent(self, tmp_path):
        storage = _RecordingStorage()
        seeded = ensure_branded_intro_outro(storage, asset_dir=tmp_path / "missing")
        assert seeded is False
        assert storage.puts == []

    def test_noop_when_only_one_clip_present(self, tmp_path):
        asset_dir = tmp_path / "partial"
        asset_dir.mkdir()
        (asset_dir / "intro.mp4").write_bytes(b"only-intro")
        storage = _RecordingStorage()

        seeded = ensure_branded_intro_outro(storage, asset_dir=asset_dir)

        assert seeded is False
        assert storage.puts == []


class TestBrandedAssetDirResolution:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv(INTRO_OUTRO_ASSET_DIR_ENV, str(tmp_path / "ops-staged"))
        assert branded_intro_outro_asset_dir() == tmp_path / "ops-staged"

    def test_default_points_at_scripts_intro_outro_output(self, monkeypatch):
        monkeypatch.delenv(INTRO_OUTRO_ASSET_DIR_ENV, raising=False)
        resolved = branded_intro_outro_asset_dir()
        assert resolved.parts[-3:] == ("scripts", "intro-outro", "output")

    def test_default_uses_resolved_asset_dir_when_no_explicit_dir(self, tmp_path, monkeypatch):
        # When no asset_dir is passed, the env-configured dir is used.
        asset_dir = tmp_path / "env-branded"
        _write_branded_assets(asset_dir)
        monkeypatch.setenv(INTRO_OUTRO_ASSET_DIR_ENV, str(asset_dir))
        storage = _RecordingStorage()

        seeded = ensure_branded_intro_outro(storage)

        assert seeded is True
        assert {p for p, _, _ in storage.puts} == {INTRO_BLOB_PATH, OUTRO_BLOB_PATH}


def test_default_cache_dir_is_importable():
    # Guards the import surface the seed relies on.
    assert isinstance(_default_intro_outro_cache_dir(), Path)
