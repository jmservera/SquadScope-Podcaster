# Claracle episode audio assets

Short intro/outro **music stingers** mixed into each episode (intro stinger →
speech → outro stinger) by `podcaster/audio.py`.

## Licensing policy (mirrors the image attribution policy)

Only **CC0 / royalty-free / public-domain** music may live here. **No copyrighted
music, ever.** Every asset is recorded in [`asset-registry.json`](./asset-registry.json)
with its source, license, attribution, and SHA-256. `podcaster/music.py` verifies
the license and integrity of each asset before it is used, failing closed if an
asset is unregistered, wrongly licensed, or modified.

## Current assets

| File | Role | License | Source |
| --- | --- | --- | --- |
| `intro_stinger.mp3` | intro | CC0-1.0 | Original synthesis (`scripts/generate_stingers.py`) |
| `outro_stinger.mp3` | outro | CC0-1.0 | Original synthesis (`scripts/generate_stingers.py`) |

These are **original, royalty-free jingles** synthesized from scratch with ffmpeg
sine oscillators (a simple major chord with a gentle baked-in fade), so they carry
no third-party copyright and are released under
[CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) (public domain).

> **Operator note:** these are intentionally placeholder-quality jingles, chosen to
> avoid any copyright risk. A professionally produced, clearly-licensed
> (CC0 / public-domain) track can be substituted later by replacing the file and
> updating `asset-registry.json` (re-running `scripts/generate_stingers.py`
> regenerates both).

## Regenerating

```bash
python scripts/generate_stingers.py
```

This rewrites the stinger MP3s and the registry (with fresh SHA-256 digests).
