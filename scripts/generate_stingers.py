"""Generate the Claracle intro/outro music stingers (v3, operator feedback #34).

The stingers are short (~4s) ORIGINAL, royalty-free jingles synthesized from
scratch with ffmpeg sine oscillators (a simple major chord with a gentle baked-in
fade in/out). Because they are generated here from first principles and contain
no third-party material, they are released into the public domain under CC0-1.0 —
there is no copyright risk.

This is intentionally a placeholder-quality jingle: the operator can later swap in
a professionally produced, clearly-licensed (CC0 / public-domain) track by replacing
the files under ``assets/audio/`` and re-running this script (or updating the
registry). Running this script is reproducible and rewrites the asset registry with
fresh SHA-256 digests.

Usage:
    python scripts/generate_stingers.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = REPO_ROOT / "assets" / "audio"
REGISTRY_PATH = ASSET_DIR / "asset-registry.json"

SAMPLE_RATE = 44_100
BITRATE = "96k"

# Two warm major chords. Intro is brighter/higher (an energetic open), outro is a
# lower, resolving chord (a calm close). Frequencies are equal-tempered note pitches.
_INTRO = {
    "name": "intro_stinger.mp3",
    "duration": 4.0,
    "freqs": (392.00, 493.88, 587.33, 783.99),  # G4 B4 D5 G5 — bright open chord
    "fade_in": 0.25,
    "fade_out": 1.2,
    "role": "intro",
    "description": "Bright G-major chord open stinger with gentle fade.",
}
_OUTRO = {
    "name": "outro_stinger.mp3",
    "duration": 4.0,
    "freqs": (261.63, 329.63, 392.00, 523.25),  # C4 E4 G4 C5 — resolving close chord
    "fade_in": 0.4,
    "fade_out": 1.6,
    "role": "outro",
    "description": "Warm C-major resolving close stinger with gentle fade.",
}


def _synth(spec: dict, out_path: Path) -> None:
    inputs: list[str] = []
    labels: list[str] = []
    for i, freq in enumerate(spec["freqs"]):
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={spec['duration']}"]
        labels.append(f"[{i}:a]")
    fade_out_start = max(0.0, float(spec["duration"]) - float(spec["fade_out"]))
    filter_complex = (
        "".join(labels)
        + f"amix=inputs={len(spec['freqs'])}:normalize=1,"
        + "vibrato=f=5:d=0.15,"
        + f"afade=t=in:st=0:d={spec['fade_in']},"
        + f"afade=t=out:st={fade_out_start:.3f}:d={spec['fade_out']},"
        + "volume=2.5[out]"
    )
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        BITRATE,
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nk=1:nw=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return round(float(result.stdout.strip()), 3)


def main() -> int:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    assets = []
    for spec in (_INTRO, _OUTRO):
        out_path = ASSET_DIR / spec["name"]
        _synth(spec, out_path)
        assets.append(
            {
                "id": spec["role"],
                "file": spec["name"],
                "role": spec["role"],
                "description": spec["description"],
                "sha256": _sha256(out_path),
                "duration_seconds": _probe_duration(out_path),
                "byte_length": out_path.stat().st_size,
                "content_type": "audio/mpeg",
                "sample_rate_hz": SAMPLE_RATE,
                "channels": 1,
                "source": "original_synthesis",
                "generator": "scripts/generate_stingers.py (ffmpeg sine oscillators)",
                "license": "CC0-1.0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "attribution": "Original royalty-free jingle generated for the Claracle podcast; released under CC0-1.0 (public domain).",
                "third_party_material": False,
                "operator_note": "Placeholder jingle. A professionally produced CC0/public-domain track may be substituted later by replacing this file and updating the registry.",
            }
        )

    registry = {
        "schema_version": "squadscope-podcaster-audio-asset-registry-v1",
        "purpose": "Registry of music/audio assets bundled with episodes, mirroring the image attribution policy. Only CC0 / royalty-free / public-domain assets are permitted.",
        "policy": {
            "allowed_licenses": ["CC0-1.0", "public-domain", "royalty-free-with-attribution"],
            "prohibited": "No copyrighted music. Every asset must record its source, license, and attribution.",
        },
        "generated_at": generated_at,
        "assets": assets,
    }
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")

    print("Generated stingers and registry:")
    for asset in assets:
        print(f"  {asset['file']}: {asset['duration_seconds']}s, sha256={asset['sha256'][:12]}…, {asset['license']}")
    print(f"  registry: {REGISTRY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
