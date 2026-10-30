"""YouTube title budget that never cuts an existing weekly identity suffix."""

from __future__ import annotations

import re

MAX_TITLE_CHARS = 100
_WEEK_MARKER = re.compile(r"\s*[|—–-]\s*(?:\d{4}-)?W\d{2}(?=\s|$)")


def clamp_youtube_title(title: str) -> str:
    cleaned = "".join(ch for ch in (title or "") if ch not in "<>" and ch.isprintable())
    if len(cleaned) <= MAX_TITLE_CHARS:
        return cleaned
    matches = list(_WEEK_MARKER.finditer(cleaned))
    if not matches:
        return cleaned[:MAX_TITLE_CHARS]
    suffix = cleaned[matches[-1].start() :]
    if len(suffix) >= MAX_TITLE_CHARS:
        raise ValueError("YouTube weekly identity suffix exceeds title budget")
    return cleaned[: MAX_TITLE_CHARS - len(suffix)].rstrip() + suffix
