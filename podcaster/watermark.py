"""Resolve the bundled Claracle DOG (Digital On-Screen Graphic) watermark asset.

Mirrors :mod:`podcaster.music`: the show's own branding ships *inside* the
synthesis image instead of being fetched from a mutable public URL at
composition time.

Why this module exists
----------------------
The SquadScope handoff (``request.podcast_config.dog_logo.url``) pointed at
``https://www.claracle.com/images/claracle.jpeg``.  That path is never emitted
by the Claracle Hugo site — the logo lives in ``assets/images/`` and Hugo's
image pipeline publishes fingerprinted derivatives (``claracle_hu_<hash>.webp``)
instead — so the URL 301'd to the apex host and then returned ``404``.
:func:`podcaster.video.video_compose._fetch_dog_logo` treated that as a
recoverable download error and composed *without* a watermark, which produced a
successful-looking but unbranded production video (W36).

The durable fix is to treat the bundled file as the canonical asset: known
Claracle logo URLs resolve to it locally with no network access at all, and any
remote fetch failure falls back to it rather than silently dropping branding.
"""

from __future__ import annotations

import hashlib
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = REPO_ROOT / "assets" / "images"
LOGO_PATH = ASSET_DIR / "claracle.jpeg"
ATTRIBUTION_PATH = ASSET_DIR / "ATTRIBUTION.md"

LOGO_LICENSE = "Proprietary"
LOGO_ATTRIBUTION = (
    "Claracle logo \u2014 original artwork by jmservera | "
    "Copyright \u00a9 jmservera. All rights reserved."
)

# Host/path pairs that all denote the *same* project-owned Claracle logo.  Hosts
# are stored without a leading ``www.`` and paths are compared case-insensitively
# after collapsing a trailing slash.  Anything not listed here is treated as a
# genuinely external logo and is fetched over the network as before.
_CANONICAL_LOGO_LOCATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        # Claracle site (the W36 handoff value; never published by Hugo).
        ("claracle.com", "/images/claracle.jpeg"),
        ("claracle.com", "/assets/images/claracle.jpeg"),
        # SquadScope repository raw/blob URLs (the historical default).
        (
            "raw.githubusercontent.com",
            "/jmservera/squadscope/main/assets/images/claracle.jpeg",
        ),
        ("github.com", "/jmservera/squadscope/raw/main/assets/images/claracle.jpeg"),
        ("github.com", "/jmservera/squadscope/blob/main/assets/images/claracle.jpeg"),
    }
)


def canonical_logo_path() -> Path | None:
    """Return the bundled Claracle logo path, or ``None`` when it is missing.

    ``None`` means the asset was not packaged into the running image, which is a
    build/packaging defect rather than a runtime condition.
    """
    if LOGO_PATH.exists() and LOGO_PATH.stat().st_size > 0:
        return LOGO_PATH
    return None


def is_canonical_logo_url(url: str) -> bool:
    """Return ``True`` when *url* denotes the project-owned Claracle logo.

    Matching is deliberately narrow — an explicit host/path allowlist — so a
    third-party logo URL is never silently served from the bundled asset.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urllib.parse.urlparse(url.strip())
        host = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https") or not host:
        return False
    host = host.lower().removeprefix("www.")
    path = parsed.path.lower().rstrip("/") or "/"
    return (host, path) in _CANONICAL_LOGO_LOCATIONS


def logo_sha256() -> str:
    """SHA-256 of the bundled logo, for provenance logging and packaging tests."""
    path = canonical_logo_path()
    if path is None:
        raise FileNotFoundError(f"bundled Claracle watermark missing: {LOGO_PATH}")
    return hashlib.sha256(path.read_bytes()).hexdigest()
