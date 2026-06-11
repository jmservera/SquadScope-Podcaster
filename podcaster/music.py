"""Locate and verify the bundled, royalty-free episode music stingers.

Audio assets follow the same attribution discipline as image assets: every
bundled track is recorded in :data:`assets/audio/asset-registry.json` with its
source, license, and attribution, and may only be CC0 / royalty-free /
public-domain. This module loads that registry, resolves the intro/outro stinger
paths, and verifies each file against its recorded SHA-256 so a swapped or
corrupted asset fails closed rather than shipping silently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = REPO_ROOT / "assets" / "audio"
REGISTRY_PATH = ASSET_DIR / "asset-registry.json"

ALLOWED_LICENSES = frozenset({"CC0-1.0", "public-domain", "royalty-free-with-attribution"})


@dataclass(frozen=True)
class MusicAsset:
    """One registered, license-checked audio asset."""

    id: str
    path: Path
    license: str
    attribution: str
    duration_seconds: float
    sha256: str


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        raise FileNotFoundError(
            f"audio asset registry not found at {REGISTRY_PATH}; run scripts/generate_stingers.py"
        )
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_asset(asset_id: str, *, verify: bool = True) -> MusicAsset:
    """Resolve a registered audio asset by id, verifying license and integrity.

    Raises if the asset is missing, its license is not allowed, or (when
    ``verify``) the on-disk SHA-256 does not match the registry.
    """

    registry = load_registry()
    for entry in registry.get("assets", []):
        if entry.get("id") != asset_id:
            continue
        license_id = str(entry.get("license", ""))
        if license_id not in ALLOWED_LICENSES:
            raise ValueError(
                f"audio asset '{asset_id}' license '{license_id}' is not in the allowed set; "
                "only CC0 / royalty-free / public-domain assets may be bundled"
            )
        path = ASSET_DIR / str(entry.get("file", ""))
        if not path.exists():
            raise FileNotFoundError(f"audio asset file missing: {path}")
        recorded = str(entry.get("sha256", ""))
        if verify and recorded and _sha256(path) != recorded:
            raise ValueError(
                f"audio asset '{asset_id}' failed integrity check; on-disk SHA-256 does not "
                "match the registry (re-run scripts/generate_stingers.py or restore the file)"
            )
        return MusicAsset(
            id=asset_id,
            path=path,
            license=license_id,
            attribution=str(entry.get("attribution", "")),
            duration_seconds=float(entry.get("duration_seconds", 0.0)),
            sha256=recorded,
        )
    raise KeyError(f"audio asset '{asset_id}' is not registered in {REGISTRY_PATH}")


def get_stingers(*, verify: bool = True) -> tuple[MusicAsset, MusicAsset]:
    """Return the ``(intro, outro)`` stinger assets, verified against the registry."""

    return get_asset("intro", verify=verify), get_asset("outro", verify=verify)


def attribution_lines() -> list[str]:
    """Human-readable attribution lines for every registered audio asset."""

    registry = load_registry()
    lines: list[str] = []
    for entry in registry.get("assets", []):
        lines.append(
            f"{entry.get('file')}: {entry.get('attribution')} "
            f"(license: {entry.get('license')}, {entry.get('license_url', '')})"
        )
    return lines
