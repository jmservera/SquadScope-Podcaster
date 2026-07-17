from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
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
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_DIALOGUE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .'\-]{0,40}:\s*(.+)$")


def fetch_prior_episode_themes(
    storage: StorageBackend,
    current_job_id: str,
    *,
    current_week: str | None = None,
    current_created_at: datetime | None = None,
) -> tuple[str, ...]:
    try:
        blob_names = storage.list_blobs(_JOB_PREFIX, limit=_MAX_BLOB_LIST)
    except Exception:
        logger.exception("prior episode blob listing failed for job_id=%s", current_job_id)
        return ()

    prior_job_ids = _prior_job_ids(
        storage,
        blob_names,
        current_job_id=current_job_id,
        current_week=current_week,
        current_created_at=current_created_at,
    )
    if not prior_job_ids:
        return ()

    themes: list[str] = []
    for job_id in prior_job_ids[:_MAX_SCRIPTS]:
        try:
            script_bytes = storage.get_bytes(f"jobs/{job_id}/script.txt")
        except Exception:
            logger.exception(
                "prior episode script read failed for job_id=%s prior_job_id=%s",
                current_job_id,
                job_id,
            )
            continue
        if not script_bytes:
            continue
        for theme in _extract_script_themes(script_bytes.decode("utf-8", errors="replace")):
            if theme not in themes:
                themes.append(theme)
            if len(themes) >= _MAX_THEMES:
                return tuple(themes)
    return tuple(themes)


def _parse_week(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = _WEEK_RE.match(value)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _parse_created_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _prior_job_ids(
    storage: StorageBackend,
    blob_names: Iterable[str],
    *,
    current_job_id: str,
    current_week: str | None,
    current_created_at: datetime | None,
) -> list[str]:
    job_ids = {
        match.group(1) for blob_name in blob_names if (match := _JOB_PATH_RE.match(blob_name))
    }
    job_ids.discard(current_job_id)
    current_week_key = _parse_week(current_week)
    if current_created_at is not None:
        current_created_at = current_created_at.astimezone(timezone.utc)

    candidates: list[tuple[tuple[int, int], datetime, str]] = []
    for job_id in job_ids:
        try:
            raw_manifest = storage.get_bytes(f"jobs/{job_id}/manifest.json")
        except Exception:
            logger.exception(
                "prior episode manifest read failed for job_id=%s prior_job_id=%s",
                current_job_id,
                job_id,
            )
            continue
        if not raw_manifest:
            continue
        try:
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            logger.warning("invalid prior episode manifest for prior_job_id=%s", job_id)
            continue
        if not isinstance(manifest, dict) or manifest.get("status") != "accepted":
            continue
        request = manifest.get("request")
        candidate_week = _parse_week(request.get("week") if isinstance(request, dict) else None)
        candidate_created_at = _parse_created_at(manifest.get("created_at"))
        if candidate_week is None or candidate_created_at is None:
            continue
        if current_week_key is not None:
            if candidate_week > current_week_key:
                continue
            if (
                candidate_week == current_week_key
                and current_created_at is not None
                and candidate_created_at >= current_created_at
            ):
                continue
        candidates.append((candidate_week, candidate_created_at, job_id))

    candidates.sort(reverse=True)
    return [job_id for _week, _created_at, job_id in candidates]


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
    cleaned = re.sub(
        r"^(?:in this episode we will talk about|we will talk about)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    if not cleaned:
        return ""
    if len(cleaned) > _MAX_THEME_CHARS:
        truncated = cleaned[: _MAX_THEME_CHARS - 1].rsplit(" ", 1)[0].strip()
        cleaned = truncated or cleaned[: _MAX_THEME_CHARS - 1].strip()
        cleaned = f"{cleaned}…"
    return cleaned
