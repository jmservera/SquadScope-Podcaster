"""YouTube title budget that never cuts an existing weekly identity suffix."""

from __future__ import annotations

import re

MAX_TITLE_CHARS = 100
_WEEK_SUFFIX = re.compile(r"(\s*[|—–-]\s*(?:\d{4}-)?W\d{2}(?:\s.*)?)$")


def clamp_youtube_title(title: str) -> str:
    cleaned = "".join(ch for ch in (title or "") if ch not in "<>" and ch.isprintable())
    if len(cleaned) <= MAX_TITLE_CHARS:
        return cleaned
    match = _WEEK_SUFFIX.search(cleaned)
    if match is None:
        return cleaned[:MAX_TITLE_CHARS]
    suffix = match.group(1)
    if len(suffix) >= MAX_TITLE_CHARS:
        raise ValueError("YouTube weekly identity suffix exceeds title budget")
    return cleaned[: MAX_TITLE_CHARS - len(suffix)].rstrip() + suffix
