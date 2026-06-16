from __future__ import annotations

import logging
import re
from typing import Iterable
from urllib.parse import unquote, urlparse

from podcaster.storage import StorageBackend

logger = logging.getLogger(__name__)

_JOB_PREFIX = "jobs/podcast-"
_SCRIPT_SUFFIX = "/script.txt"
_MAX_BLOB_LIST = 64
_MAX_SCRIPTS = 3
_MAX_THEMES = 8
_MAX_THEME_CHARS = 100
_JOB_PATH_RE = re.compile(r"^jobs/(podcast-[^/]+)/")
_DIALOGUE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .'\-]{0,40}:\s*(.+)$")


def fetch_prior_episode_themes(storage: StorageBackend, current_job_id: str) -> tuple[str, ...]:
    try:
        blob_names = storage.list_blobs(_JOB_PREFIX, limit=_MAX_BLOB_LIST)
    except Exception:
        logger.exception("prior episode blob listing failed for job_id=%s", current_job_id)
        return ()

    prior_job_ids = _prior_job_ids(blob_names, current_job_id=current_job_id)
    if not prior_job_ids:
        return ()

    themes: list[str] = []
    for job_id in prior_job_ids[:_MAX_SCRIPTS]:
        try:
            script_bytes = storage.get_bytes(f"jobs/{job_id}/script.txt")
        except Exception:
            logger.exception("prior episode script read failed for job_id=%s prior_job_id=%s", current_job_id, job_id)
            continue
        if not script_bytes:
            continue
        for theme in _extract_script_themes(script_bytes.decode("utf-8", errors="replace")):
            if theme not in themes:
                themes.append(theme)
            if len(themes) >= _MAX_THEMES:
                return tuple(themes)
    return tuple(themes)


def _prior_job_ids(blob_names: Iterable[str], *, current_job_id: str) -> list[str]:
    job_ids = {
        match.group(1)
        for blob_name in blob_names
        if (match := _JOB_PATH_RE.match(blob_name))
    }
    job_ids.discard(current_job_id)
    return sorted(job_ids, reverse=True)


def _extract_script_themes(script: str) -> tuple[str, ...]:
    header, _, body = script.partition("\n---")
    themes: list[str] = []
    for theme in (
        _theme_from_explicit_topic(body),
        _theme_from_source_url(header),
        *_themes_from_dialogue(body),
    ):
        normalized = _normalize_theme(theme)
        if normalized and normalized not in themes:
            themes.append(normalized)
        if len(themes) >= _MAX_THEMES:
            break
    return tuple(themes)


def _theme_from_explicit_topic(body: str) -> str:
    for raw_line in body.splitlines():
        line = raw_line.strip()
        match = re.search(r"talk about:\s*(.+)$", line, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _theme_from_source_url(header: str) -> str:
    for raw_line in header.splitlines():
        if not raw_line.startswith("Source URL:"):
            continue
        source_url = raw_line.replace("Source URL:", "", 1).strip()
        parsed = urlparse(source_url)
        segments = [segment for segment in parsed.path.split("/") if segment]
        slug = unquote(segments[-1] if segments else parsed.netloc)
        words = [
            word
            for word in re.split(r"[^A-Za-z0-9]+", slug)
            if word and not word.isdigit() and word.lower() not in {"www", "html", "htm", "index"}
        ]
        if words:
            return " ".join(words[:8])
    return ""


def _themes_from_dialogue(body: str) -> tuple[str, ...]:
    themes: list[str] = []
    for raw_line in body.splitlines():
        match = _DIALOGUE_RE.match(raw_line.strip())
        if not match:
            continue
        content = match.group(1).strip()
        if _is_non_theme_line(content):
            continue
        themes.append(content)
        if len(themes) >= 2:
            break
    return tuple(themes)


def _is_non_theme_line(content: str) -> bool:
    lowered = content.lower()
    return any(
        marker in lowered
        for marker in (
            "talk about:",
            "welcome to",
            "if you're new here",
            "ai-generated synthetic voices",
            "manual review is required",
            "dry-run-safe",
            "[editorial",
            "www.",
        )
    )


def _normalize_theme(theme: str) -> str:
    cleaned = re.sub(r"\s+", " ", theme).strip(" -:.;,[]")
    cleaned = re.sub(r"^(?:in this episode we will talk about|we will talk about)\s*", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return ""
    if len(cleaned) > _MAX_THEME_CHARS:
        truncated = cleaned[: _MAX_THEME_CHARS - 1].rsplit(" ", 1)[0].strip()
        cleaned = truncated or cleaned[: _MAX_THEME_CHARS - 1].strip()
        cleaned = f"{cleaned}…"
    return cleaned
