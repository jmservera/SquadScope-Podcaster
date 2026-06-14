"""Spotify for Creators auto-publish integration (#182).

Publishes generated Claracle episodes to Spotify for Creators using the
unofficial internal API. Publication is opt-in (``SPOTIFY_PUBLISH_ENABLED=true``)
and **never blocks** the generation pipeline — publish failures are logged and
reported but do not fail the overall episode workflow.

Authentication uses browser session cookies (``SP_DC`` + ``SP_KEY``) to obtain
a short-lived Bearer token via ``spotifyconnector``. The module provides a
health-check function to verify auth status without side effects.

Security:
- Cookies are read from environment variables, never logged or committed.
- Dry-run mode (``SPOTIFY_PUBLISH_DRY_RUN=true``) simulates all steps without
  making real API calls.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from podcaster.config import SpotifyPublishConfig
from spotifyconnector import SpotifyConnector

logger = logging.getLogger(__name__)

# Spotify for Creators internal API base
_BASE_URL = "https://api-v5.anchor.fm"
_SPOTIFY_CLIENT_ID = "05a1371ee5194c27860b3ff3ff3979d2"
_SPOTIFY_CONNECTOR_BASE_URL = "https://generic.wg.spotify.com/podcasters/v0"

# Required headers for mutation requests
_MUTATION_HEADERS = {
    "Origin": "https://creators.spotify.com",
    "Referer": "https://creators.spotify.com/",
}

# Retry configuration
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2.0
_POLL_INTERVAL = 5
_POLL_MAX_ATTEMPTS = 60


@dataclass
class PublishResult:
    """Result of an episode publish attempt."""

    anchor_episode_id: int | None = None
    status: str = "failed"  # "published" | "scheduled" | "draft" | "failed"
    error: str | None = None
    dry_run: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class SpotifyPublishError(Exception):
    """Raised when a Spotify API call fails."""


def _is_enabled() -> bool:
    """Check if Spotify publishing is enabled."""
    return os.environ.get("SPOTIFY_PUBLISH_ENABLED", "").lower() == "true"


def _is_dry_run() -> bool:
    """Check if dry-run mode is active."""
    return os.environ.get("SPOTIFY_PUBLISH_DRY_RUN", "").lower() == "true"


def _get_credentials() -> tuple[str, str, str]:
    """Return (show_id, sp_dc, sp_key) from environment.

    Raises ValueError if any credential is missing.
    """
    show_id = os.environ.get("SPOTIFY_SHOW_ID", "")
    sp_dc = os.environ.get("SP_DC", "")
    sp_key = os.environ.get("SP_KEY", "")

    missing = []
    if not show_id:
        missing.append("SPOTIFY_SHOW_ID")
    if not sp_dc:
        missing.append("SP_DC")
    if not sp_key:
        missing.append("SP_KEY")

    if missing:
        raise ValueError(
            f"Missing Spotify credentials: {', '.join(missing)}. "
            "Set these environment variables to enable publishing."
        )
    return show_id, sp_dc, sp_key


def _build_session(sp_dc: str, sp_key: str, show_id: str) -> requests.Session:
    """Build a requests session with Spotify bearer auth."""
    session = requests.Session()
    bearer = _request_bearer_token(sp_dc, sp_key, show_id)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer}",
        }
    )
    return session


def _mums_params(**kwargs: str) -> dict[str, str]:
    return {**kwargs, "isMumsCompatible": "true"}


def _request_bearer_token(sp_dc: str, sp_key: str, show_id: str) -> str:
    """Exchange browser cookies for a short-lived Spotify bearer token."""
    connector = SpotifyConnector(
        base_url=_SPOTIFY_CONNECTOR_BASE_URL,
        client_id=_SPOTIFY_CLIENT_ID,
        podcast_id=show_id,
        sp_dc=sp_dc,
        sp_key=sp_key,
    )
    try:
        connector._authenticate()
    except Exception as exc:
        message = str(exc)
        if "login required" in message.lower() or "credentials" in message.lower():
            raise SpotifyPublishError(
                "Spotify cookies expired — operator must refresh SP_DC/SP_KEY."
            ) from exc
        raise SpotifyPublishError(
            "Failed to exchange Spotify cookies for bearer token."
        ) from exc

    bearer = connector._bearer or ""
    if not bearer:
        raise SpotifyPublishError("Spotify auth flow returned no bearer token.")
    return bearer


def _safe_url(url: str) -> str:
    """Strip query parameters from a URL to avoid leaking signed tokens in logs."""
    parsed = urlparse(url)
    if parsed.query or parsed.fragment:
        return urlunparse(parsed._replace(query="[REDACTED]", fragment=""))
    return url


def _is_retryable(exc: requests.RequestException) -> bool:
    """Return True only for transient failures safe to retry."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    return False


def _retry_request(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> requests.Response:
    """Execute an HTTP request with exponential backoff retry.

    Only retries on transient errors (5xx, 408, 429, timeouts, connection
    errors). Client errors (4xx) are raised immediately to avoid duplicating
    state-mutating requests.
    """
    last_exc: Exception | None = None
    log_url = _safe_url(url)
    for attempt in range(_MAX_RETRIES):
        try:
            resp = session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt >= _MAX_RETRIES - 1:
                break
            wait = _RETRY_BACKOFF_BASE ** attempt
            safe_reason = type(exc).__name__
            if exc.response is not None:
                safe_reason += f" (HTTP {exc.response.status_code})"
            logger.warning(
                "Spotify API %s %s failed (attempt %d/%d): %s — retrying in %.1fs",
                method,
                log_url,
                attempt + 1,
                _MAX_RETRIES,
                safe_reason,
                wait,
            )
            time.sleep(wait)
    raise SpotifyPublishError(
        f"Spotify API {method} {log_url} failed after {attempt + 1} attempt(s)"
    ) from last_exc


def verify_spotify_auth() -> tuple[bool, str]:
    """Health-check: verify Spotify auth is valid without side effects.

    Returns (is_valid, message).
    """
    try:
        show_id, sp_dc, sp_key = _get_credentials()
    except ValueError as exc:
        return False, str(exc)

    if _is_dry_run():
        return True, "Dry-run mode — credentials present, skipping live check."

    try:
        session = _build_session(sp_dc, sp_key, show_id)
        url = f"{_BASE_URL}/v3/shows/{show_id}/legacyIds"
        resp = session.get(url, params=_mums_params(), timeout=10)
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                return False, "Spotify auth invalid — legacyIds response is not valid JSON."
            if data.get("stationId") and data.get("userId"):
                return True, "Spotify auth valid."
            return False, "Spotify auth invalid — legacyIds response missing IDs."
        elif resp.status_code == 401:
            return False, "Spotify cookies expired — operator must refresh SP_DC/SP_KEY."
        else:
            return False, f"Unexpected status {resp.status_code} from Spotify."
    except SpotifyPublishError as exc:
        return False, str(exc)
    except requests.RequestException as exc:
        return False, f"Spotify connectivity error: {exc}"


def _resolve_legacy_ids(
    session: requests.Session, show_id: str
) -> tuple[str, str]:
    """Step 1: Resolve show_id to stationId + userId."""
    url = f"{_BASE_URL}/v3/shows/{show_id}/legacyIds"
    resp = _retry_request(session, "GET", url, params=_mums_params(), timeout=15)
    data = resp.json()
    station_id = str(data["stationId"])
    user_id = str(data["userId"])
    logger.info("Resolved show %s → station=%s user=%s", show_id, station_id, user_id)
    return station_id, user_id


def _create_episode(session: requests.Session, station_id: str) -> int:
    """Step 2: Create a draft episode, returns anchorId."""
    url = f"{_BASE_URL}/v3/stations/{station_id}/episodes"
    resp = _retry_request(
        session,
        "POST",
        url,
        headers=_MUTATION_HEADERS,
        params=_mums_params(),
        json={"hourOffset": 0},
        timeout=15,
    )
    data = resp.json()
    anchor_id = int(data.get("episodeId") or data["id"])
    logger.info("Created draft episode anchorId=%d", anchor_id)
    return anchor_id


def _get_upload_url(
    session: requests.Session,
    anchor_id: int,
    *,
    filename: str,
    content_type: str,
) -> tuple[str, str]:
    """Step 3: Get a signed upload URL. Returns (signed_url, upload_id)."""
    url = f"{_BASE_URL}/v3/episodes/{anchor_id}/upload/signedUrl"
    resp = _retry_request(
        session,
        "GET",
        url,
        params=_mums_params(filename=filename, type=content_type),
        timeout=15,
    )
    data = resp.json()
    upload_id = data.get("uploadId") or data["requestUuid"]
    return data.get("signedUrl") or data["url"], str(upload_id)


def _upload_audio(
    session: requests.Session,
    signed_url: str,
    audio_data: bytes,
    *,
    content_type: str,
) -> str:
    """Step 4: Upload audio to GCS, returns ETag (stripped of quotes).

    Strips the Authorization header to avoid leaking the Spotify bearer
    token to the external GCS upload host.
    """
    resp = _retry_request(
        session,
        "PUT",
        signed_url,
        data=audio_data,
        headers={
            "Content-Type": content_type,
            "Authorization": None,
            **_MUTATION_HEADERS,
        },
        timeout=120,
    )
    etag = resp.headers.get("ETag", "").strip('"')
    logger.info("Uploaded audio (%d bytes, %s), ETag=%s", len(audio_data), content_type, etag)
    return etag


def _process_upload(
    session: requests.Session,
    upload_id: str,
    etag: str,
    *,
    anchor_id: int,
    station_id: str,
    user_id: str,
    filename: str,
) -> None:
    """Step 5: Trigger processing and poll until complete."""
    url = f"{_BASE_URL}/v3/upload/{upload_id}/process_upload"
    _retry_request(
        session,
        "POST",
        url,
        headers=_MUTATION_HEADERS,
        params=_mums_params(),
        json={
            "userId": int(user_id),
            "uploadType": "default",
            "origin": "episode-media:upload",
            "caption": filename,
            "isExtractedFromVideo": False,
            "isMultipartUpload": True,
            "parts": [{"partNumber": 1, "etag": etag}],
            "uploadId": upload_id,
            "episodeId": anchor_id,
            "stationId": int(station_id),
        },
        timeout=30,
    )

    # Poll for completion
    status_url = f"{_BASE_URL}/v3/upload/media/{upload_id}"
    for attempt in range(_POLL_MAX_ATTEMPTS):
        time.sleep(_POLL_INTERVAL)
        resp = _retry_request(
            session,
            "GET",
            status_url,
            params=_mums_params(includeMediaValidation="true"),
            timeout=15,
        )
        data = resp.json()
        status = data.get("status", "")
        if status == "completed":
            logger.info("Upload %s processing completed", upload_id)
            return
        elif status == "failed":
            raise SpotifyPublishError(
                f"Upload {upload_id} processing failed: {data}"
            )
        logger.debug("Upload %s status: %s (attempt %d)", upload_id, status, attempt + 1)

    raise SpotifyPublishError(
        f"Upload {upload_id} processing timed out after {_POLL_MAX_ATTEMPTS * _POLL_INTERVAL}s"
    )


def _set_metadata(
    session: requests.Session,
    anchor_id: int,
    user_id: str,
    title: str,
    description: str,
    publish_behavior: str,
    publish_on: datetime | None,
    season_number: int | None = None,
    episode_number: int | None = None,
    episode_type: str = "full",
    explicit: bool = False,
) -> None:
    """Step 6: Set episode metadata."""
    url = f"{_BASE_URL}/v3/episodes/{anchor_id}/update"
    payload: dict[str, Any] = {
        "userId": int(user_id),
        "title": title,
        "description": description,
        "episodeType": episode_type,
        "isPublished": publish_behavior == "immediate",
        "podcastEpisodeIsExplicit": explicit,
    }
    if publish_behavior == "scheduled" and publish_on is not None:
        publish_on_utc = publish_on.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        payload["publishOn"] = publish_on_utc
        payload["wizardDraftedToPublishOn"] = publish_on_utc
    if season_number is not None:
        payload["seasonNumber"] = season_number
    if episode_number is not None:
        payload["episodeNumber"] = episode_number
    _retry_request(
        session,
        "POST",
        url,
        headers=_MUTATION_HEADERS,
        params=_mums_params(),
        json=payload,
        timeout=15,
    )
    logger.info(
        "Metadata set for episode %d: %s",
        anchor_id,
        title,
    )


def _publish_episode_live(
    session: requests.Session,
    anchor_id: int,
    publish_on: datetime | None = None,
) -> None:
    """Step 7: Publish or schedule an episode."""
    url = f"{_BASE_URL}/v3/episodes/{anchor_id}/publish?isMumsCompatible=true"
    payload: dict[str, Any] = {}
    if publish_on:
        payload["publishOn"] = publish_on.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    _retry_request(
        session,
        "POST",
        url,
        headers=_MUTATION_HEADERS,
        json=payload,
        timeout=15,
    )
    logger.info(
        "Episode %d publish requested (%s)",
        anchor_id,
        publish_on or "immediate",
    )


def _safe_resolve_number(
    label: str,
    resolver,
    *,
    year: int,
    week: int,
    fallback: int | None,
) -> int | None:
    try:
        return int(resolver(year=year, week=week))
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        logger.warning("Spotify publish %s template failed; using fallback: %s", label, exc)
        return fallback


def _resolve_publish_inputs(
    title: str,
    description: str,
    publish_on: datetime | None,
    spotify_publish_config: SpotifyPublishConfig | None,
    *,
    year: int | None,
    week: int | None,
    article_title: str | None,
    article_summary: str | None,
) -> tuple[str, str, int | None, int | None, str, datetime | None, str]:
    if spotify_publish_config is None:
        return title, description, None, None, "immediate", None, "wav"

    resolved_title = spotify_publish_config.title or title
    resolved_description = spotify_publish_config.description or description
    resolved_season: int | None = None
    resolved_episode: int | None = None

    if year is not None and week is not None:
        resolved_season = _safe_resolve_number(
            "season",
            spotify_publish_config.resolve_season,
            year=year,
            week=week,
            fallback=year,
        )
        resolved_episode = _safe_resolve_number(
            "episode",
            spotify_publish_config.resolve_episode,
            year=year,
            week=week,
            fallback=week,
        )

    publish_mode_raw = spotify_publish_config.publish_mode.strip()
    publish_mode = publish_mode_raw.lower()
    if publish_mode == "draft":
        return resolved_title, resolved_description, resolved_season, resolved_episode, "draft", None, spotify_publish_config.upload_format
    if publish_mode == "immediate":
        return resolved_title, resolved_description, resolved_season, resolved_episode, "immediate", None, spotify_publish_config.upload_format

    try:
        parsed_publish_on = datetime.fromisoformat(publish_mode_raw.replace("Z", "+00:00"))
        if parsed_publish_on.tzinfo is None:
            parsed_publish_on = parsed_publish_on.replace(tzinfo=timezone.utc)
        return (
            resolved_title,
            resolved_description,
            resolved_season,
            resolved_episode,
            "scheduled",
            parsed_publish_on,
            spotify_publish_config.upload_format,
        )
    except ValueError as exc:
        logger.warning(
            "Spotify publish_mode %r is invalid; using fallback publish behavior: %s",
            spotify_publish_config.publish_mode,
            exc,
        )
        if publish_on is not None:
            return (
                resolved_title,
                resolved_description,
                resolved_season,
                resolved_episode,
                "scheduled",
                publish_on,
                spotify_publish_config.upload_format,
            )
        return resolved_title, resolved_description, resolved_season, resolved_episode, "immediate", None, spotify_publish_config.upload_format


def inject_timestamps_into_description(
    description: str,
    timestamps_html: str,
    max_length: int = 4_000,
) -> str:
    """Append timestamps HTML to the episode description if within char limit.

    If the combined description would exceed ``max_length``, the original
    description is returned unchanged (timestamps are dropped rather than
    truncating the description body).
    """
    if not timestamps_html:
        return description
    combined = f"{description}{timestamps_html}"
    if len(combined) > max_length:
        return description
    return combined


def publish_episode(
    mp3_path: Path,
    title: str,
    description: str,
    show_id: str | None = None,
    sp_dc: str | None = None,
    sp_key: str | None = None,
    publish_on: datetime | None = None,
    episode_type: str = "full",
    explicit: bool = False,
    spotify_publish_config: SpotifyPublishConfig | None = None,
    year: int | None = None,
    week: int | None = None,
    article_title: str | None = None,
    article_summary: str | None = None,
    *,
    wav_path: Path | None = None,
    timestamps_html: str = "",
) -> PublishResult:
    """Publish an episode to Spotify for Creators.

    This function is designed to never raise — it catches all exceptions and
    returns a :class:`PublishResult` with status="failed" and the error message.
    This ensures publish failures never break the generation pipeline.

    Args:
        mp3_path: Path to the distribution MP3 artifact.
        title: Episode title.
        description: Episode description (HTML format).
        show_id: Spotify show ID (defaults to SPOTIFY_SHOW_ID env).
        sp_dc: Session cookie (defaults to SP_DC env).
        sp_key: Session cookie (defaults to SP_KEY env).
        publish_on: Schedule for this datetime (None = immediate).
        episode_type: "full", "trailer", or "bonus".
        explicit: Whether the episode has explicit content.
        spotify_publish_config: Optional Spotify metadata/publish config.
        year: Episode year context for config template resolution.
        week: Episode ISO week context for config template resolution.
        article_title: Source article title for config template resolution.
        article_summary: Source article summary for config template resolution.
        wav_path: Optional WAV artifact path for Spotify upload.
        timestamps_html: Pre-formatted HTML timestamps block to append to
            the episode description (from :func:`~podcaster.episode.format_timestamps_html`).

    Returns:
        PublishResult with status and any error details.
    """
    if not _is_enabled():
        return PublishResult(
            status="failed",
            error="Spotify publishing disabled (SPOTIFY_PUBLISH_ENABLED != true).",
        )

    resolved_title, resolved_description, season_number, episode_number, publish_behavior, resolved_publish_on, upload_format = (
        _resolve_publish_inputs(
            title,
            description,
            publish_on,
            spotify_publish_config,
            year=year,
            week=week,
            article_title=article_title,
            article_summary=article_summary,
        )
    )

    # Append timestamps to description if provided and within Spotify's limit
    if timestamps_html:
        resolved_description = inject_timestamps_into_description(
            resolved_description, timestamps_html
        )

    # Resolve credentials
    try:
        env_show_id, env_sp_dc, env_sp_key = _get_credentials()
        show_id = show_id or env_show_id
        sp_dc = sp_dc or env_sp_dc
        sp_key = sp_key or env_sp_key
    except ValueError as exc:
        return PublishResult(status="failed", error=str(exc))

    # Dry-run mode
    if _is_dry_run():
        selected_path = wav_path if upload_format == "wav" else mp3_path
        logger.info(
            "DRY RUN: Would publish %s as '%s' (%s, format=%s)",
            selected_path,
            resolved_title,
            publish_behavior,
            upload_format,
        )
        return PublishResult(
            anchor_episode_id=None,
            status="draft" if publish_behavior == "draft" else ("scheduled" if resolved_publish_on else "published"),
            dry_run=True,
            details={
                "title": resolved_title,
                "mp3_path": str(mp3_path),
                "wav_path": str(wav_path) if wav_path else None,
                "upload_path": str(selected_path) if selected_path else None,
                "upload_format": upload_format,
                "publish_behavior": publish_behavior,
            },
        )

    upload_path = wav_path if upload_format == "wav" else mp3_path
    content_type = "audio/wav" if upload_format == "wav" else "audio/mpeg"
    format_label = "WAV" if upload_format == "wav" else "MP3"

    if upload_path is None or not upload_path.exists():
        return PublishResult(
            status="failed", error=f"{format_label} file not found: {upload_path}"
        )

    try:
        session = _build_session(sp_dc, sp_key, show_id)

        # Step 1: Resolve IDs
        station_id, user_id = _resolve_legacy_ids(session, show_id)

        # Step 2: Create draft episode
        anchor_id = _create_episode(session, station_id)

        # Step 3: Get upload URL
        signed_url, upload_id = _get_upload_url(
            session,
            anchor_id,
            filename=upload_path.name,
            content_type=content_type,
        )

        # Step 4: Upload audio
        audio_data = upload_path.read_bytes()
        etag = _upload_audio(session, signed_url, audio_data, content_type=content_type)

        # Step 5: Process upload
        _process_upload(
            session,
            upload_id,
            etag,
            anchor_id=anchor_id,
            station_id=station_id,
            user_id=user_id,
            filename=upload_path.name,
        )

        # Step 6: Set metadata
        _set_metadata(
            session,
            anchor_id,
            user_id,
            title=resolved_title,
            description=resolved_description,
            publish_behavior=publish_behavior,
            publish_on=resolved_publish_on,
            season_number=season_number,
            episode_number=episode_number,
            episode_type=episode_type,
            explicit=explicit,
        )

        status = "draft" if publish_behavior == "draft" else ("scheduled" if resolved_publish_on else "published")
        logger.info(
            "Episode published to Spotify: anchorId=%d status=%s",
            anchor_id,
            status,
        )
        return PublishResult(
            anchor_episode_id=anchor_id,
            status=status,
            details={"station_id": station_id, "upload_id": upload_id},
        )

    except SpotifyPublishError as exc:
        logger.error("Spotify publish failed: %s", exc)
        return PublishResult(status="failed", error=str(exc))
    except Exception as exc:
        safe_msg = re.sub(r"https?://\S+", lambda m: _safe_url(m.group()), str(exc))
        logger.error("Unexpected error during Spotify publish: %s", safe_msg)
        return PublishResult(status="failed", error=f"Unexpected: {safe_msg}")
