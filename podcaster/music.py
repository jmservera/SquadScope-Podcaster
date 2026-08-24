"""Resolve the bundled Claracle theme music bed for intro and outro playback."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = REPO_ROOT / "assets" / "music"
TRACK_PATH = ASSET_DIR / "claracle-theme.mp3"
ATTRIBUTION_PATH = ASSET_DIR / "ATTRIBUTION.md"

TRACK_LICENSE = "Proprietary"
TRACK_ATTRIBUTION = (
    "Claracle theme \u2014 original composition by jmservera | "
    "Copyright \u00a9 jmservera. All rights reserved."
)
TRACK_DURATION_SECONDS = 85.4

ALLOWED_LICENSES = frozenset({TRACK_LICENSE})


@dataclass(frozen=True)
class MusicAsset:
    id: str
    path: Path
    license: str
    attribution: str
    duration_seconds: float
    sha256: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_registry() -> dict[str, object]:
    """Return the bundled music metadata for intro/outro roles."""

    assets = [
        {
            "id": asset_id,
            "file": TRACK_PATH.name,
            "role": asset_id,
            "license": TRACK_LICENSE,
            "attribution": TRACK_ATTRIBUTION,
            "duration_seconds": TRACK_DURATION_SECONDS,
            "third_party_material": False,
        }
        for asset_id in ("intro", "outro")
    ]
    return {
        "schema_version": "squadscope-podcaster-music-assets-v1",
        "purpose": "Bundled episode music metadata for the Claracle theme intro/outro bed.",
        "attribution_path": str(ATTRIBUTION_PATH.relative_to(REPO_ROOT)),
        "assets": assets,
    }


def get_asset(asset_id: str, *, verify: bool = True) -> MusicAsset:
    """Resolve the Claracle theme music file for the requested intro/outro role."""

    if asset_id not in {"intro", "outro"}:
        raise KeyError(f"unknown music asset '{asset_id}'")
    if not TRACK_PATH.exists():
        raise FileNotFoundError(f"music asset file missing: {TRACK_PATH}")
    if verify and not ATTRIBUTION_PATH.exists():
        raise FileNotFoundError(f"music attribution file missing: {ATTRIBUTION_PATH}")
    return MusicAsset(
        id=asset_id,
        path=TRACK_PATH,
        license=TRACK_LICENSE,
        attribution=TRACK_ATTRIBUTION,
        duration_seconds=TRACK_DURATION_SECONDS,
        sha256=_sha256(TRACK_PATH),
    )


def get_stingers(*, verify: bool = True) -> tuple[MusicAsset, MusicAsset]:
    """Return the intro/outro music assets for compatibility with existing callers."""

    return get_asset("intro", verify=verify), get_asset("outro", verify=verify)


def attribution_lines() -> list[str]:
    """Human-readable attribution lines for the bundled Summer Sport track."""

    return [
        f"{entry['role']}: {entry['file']} — {entry['attribution']} (license: {entry['license']})"
        for entry in load_registry()["assets"]
    ]
