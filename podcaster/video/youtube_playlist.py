"""YouTube playlist management — auto-add uploaded videos to a show playlist (#449).

After a video is uploaded (#442) and integrated into ``distribute_video()``
(#444), it should appear in the right "Claracle Weekly" playlist — and in the
per-language playlist for es/fr multilanguage shows.

This module is self-contained and side-effect free at import time so it is fully
unit-testable in CI. It reuses the ``HttpTransport`` protocol from
``podcaster.video.distribution`` for the API calls, allowing a fake transport in
tests. Access tokens are only sent in the ``Authorization`` header, never logged.

Idempotency: before inserting, :func:`playlist_contains_video` queries
``playlistItems.list`` filtered by ``playlistId`` + ``videoId``. A retry that
re-adds the same video is a no-op (``skipped=True``) rather than a duplicate.

Locale routing: :func:`resolve_playlist_id` picks the playlist for a locale from
config / environment. The default (``en``) playlist applies when no per-locale
override is configured, so single-language behavior is unchanged.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

#: ``playlistItems.insert`` endpoint (POST, ``part=snippet``).
PLAYLIST_ITEMS_INSERT_URL = (
    "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet"
)

#: ``playlistItems.list`` base endpoint (GET).
PLAYLIST_ITEMS_LIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

#: Per-locale environment overrides. ``en`` falls back to the base var.
_PLAYLIST_ENV_BASE = "VIDEO_YOUTUBE_PLAYLIST_ID"
_DEFAULT_LOCALE = "en"
_SUPPORTED_LOCALES = ("en", "es", "fr")


def _normalize_locale(locale: str | None) -> str:
    """Reduce an arbitrary locale string to a supported base code (en/es/fr)."""
    if not locale:
        return _DEFAULT_LOCALE
    base = locale.strip().lower().replace("_", "-").split("-", 1)[0]
    return base if base in _SUPPORTED_LOCALES else _DEFAULT_LOCALE


def resolve_playlist_id(
    config: object | None,
    locale: str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> str:
    """Resolve the playlist id for ``locale`` from config / environment.

    Resolution order (first non-empty wins):

    1. ``config.youtube_playlist_ids`` mapping (``{"en": "PL..", "es": ".."}``),
       duck-typed so it works before #432's per-language config lands.
    2. ``config.youtube_playlist_id`` (single-playlist attribute, used for the
       default locale only).
    3. Env ``VIDEO_YOUTUBE_PLAYLIST_ID_<LOCALE>`` (e.g. ``..._ES``), and for the
       default locale ``VIDEO_YOUTUBE_PLAYLIST_ID``.

    Returns an empty string when no playlist is configured (callers then skip
    the playlist step — it is optional).
    """
    norm = _normalize_locale(locale)
    environ = env if env is not None else os.environ

    mapping = getattr(config, "youtube_playlist_ids", None)
    if isinstance(mapping, dict):
        for key in (norm, _normalize_locale(locale)):
            val = mapping.get(key) or mapping.get(key.upper())
            if val:
                return str(val).strip()

    if norm == _DEFAULT_LOCALE:
        single = getattr(config, "youtube_playlist_id", None)
        if single:
            return str(single).strip()

    env_key = (
        _PLAYLIST_ENV_BASE
        if norm == _DEFAULT_LOCALE
        else f"{_PLAYLIST_ENV_BASE}_{norm.upper()}"
    )
    return (environ.get(env_key, "") or "").strip()


# --- Results -----------------------------------------------------------------


@dataclass(frozen=True)
class PlaylistAddResult:
    """Outcome of an add-to-playlist attempt."""

    video_id: str
    playlist_id: str
    succeeded: bool
    skipped: bool = False
    playlist_item_id: str = ""
    error: str = ""


# --- API calls ---------------------------------------------------------------


def playlist_contains_video(
    playlist_id: str,
    video_id: str,
    access_token: str,
    *,
    transport: object | None = None,
) -> bool:
    """Return ``True`` if ``video_id`` is already an item of ``playlist_id``.

    Uses ``playlistItems.list`` filtered by ``playlistId`` + ``videoId``. On any
    HTTP/transport error returns ``False`` (so a failed check does not block the
    insert — at worst YouTube de-dupes or a duplicate is tolerated).
    """
    if not playlist_id or not video_id:
        return False
    http = transport if transport is not None else _default_transport()
    params = urlencode(
        {
            "part": "snippet",
            "playlistId": playlist_id,
            "videoId": video_id,
            "maxResults": "1",
        }
    )
    url = f"{PLAYLIST_ITEMS_LIST_URL}?{params}"
    try:
        status, body = http.request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except Exception as exc:  # pragma: no cover - network/transport failure
        logger.warning("playlistItems.list error for %s: %s", video_id, exc)
        return False
    if status != 200:
        return False
    try:
        data = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except (ValueError, AttributeError):
        return False
    return bool(data.get("items"))


def add_video_to_playlist(
    playlist_id: str,
    video_id: str,
    access_token: str,
    *,
    position: int | None = None,
    transport: object | None = None,
) -> PlaylistAddResult:
    """Insert ``video_id`` into ``playlist_id`` via ``playlistItems.insert``.

    Never raises on an HTTP/transport error — returns a failed
    :class:`PlaylistAddResult` so a playlist failure cannot abort the rest of
    distribution (the video itself is already uploaded). The token is only sent
    in the ``Authorization`` header and never logged.
    """
    if not playlist_id:
        raise ValueError("playlist_id is required")
    if not video_id:
        raise ValueError("video_id is required")

    snippet: dict[str, object] = {
        "playlistId": playlist_id,
        "resourceId": {"kind": "youtube#video", "videoId": video_id},
    }
    if position is not None:
        snippet["position"] = position
    payload = json.dumps({"snippet": snippet}).encode("utf-8")
    http = transport if transport is not None else _default_transport()

    try:
        status, body = http.request(
            PLAYLIST_ITEMS_INSERT_URL,
            method="POST",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
            data=payload,
        )
    except Exception as exc:  # pragma: no cover - network/transport failure
        logger.warning("playlistItems.insert error for %s: %s", video_id, exc)
        return PlaylistAddResult(
            video_id=video_id, playlist_id=playlist_id, succeeded=False, error=str(exc)
        )

    if status in (200, 201):
        item_id = ""
        try:
            data = json.loads(
                body.decode("utf-8") if isinstance(body, bytes) else body
            )
            item_id = str(data.get("id", ""))
        except (ValueError, AttributeError):
            pass
        logger.info("Added video %s to playlist %s", video_id, playlist_id)
        return PlaylistAddResult(
            video_id=video_id,
            playlist_id=playlist_id,
            succeeded=True,
            playlist_item_id=item_id,
        )
    logger.warning(
        "playlistItems.insert failed for %s -> %s: HTTP %s",
        video_id,
        playlist_id,
        status,
    )
    return PlaylistAddResult(
        video_id=video_id,
        playlist_id=playlist_id,
        succeeded=False,
        error=f"HTTP {status}",
    )


def add_to_show_playlist(
    config: object | None,
    locale: str | None,
    video_id: str,
    access_token: str,
    *,
    transport: object | None = None,
    position: int | None = None,
) -> PlaylistAddResult:
    """Resolve the locale's playlist and add ``video_id`` idempotently.

    - Resolves the playlist via :func:`resolve_playlist_id`. When no playlist is
      configured for the locale, returns ``skipped=True`` (playlist is optional).
    - Skips the insert when the video is already present (retry-safe), returning
      ``succeeded=True, skipped=True``.
    """
    if not video_id:
        raise ValueError("video_id is required")

    playlist_id = resolve_playlist_id(config, locale, env=None)
    if not playlist_id:
        logger.info(
            "No YouTube playlist configured for locale %s; skipping",
            _normalize_locale(locale),
        )
        return PlaylistAddResult(
            video_id=video_id, playlist_id="", succeeded=True, skipped=True
        )

    if playlist_contains_video(
        playlist_id, video_id, access_token, transport=transport
    ):
        logger.info(
            "Video %s already in playlist %s; skipping (idempotent)",
            video_id,
            playlist_id,
        )
        return PlaylistAddResult(
            video_id=video_id,
            playlist_id=playlist_id,
            succeeded=True,
            skipped=True,
        )

    return add_video_to_playlist(
        playlist_id,
        video_id,
        access_token,
        position=position,
        transport=transport,
    )


def _default_transport() -> object:
    """Lazily build the default urllib transport (reused from distribution)."""
    from podcaster.video.distribution import _DefaultTransport

    return _DefaultTransport()
