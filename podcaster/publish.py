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
from podcaster.config import MAX_SPOTIFY_DESCRIPTION_CHARS, SpotifyPublishConfig

try:
    from spotifyconnector import SpotifyConnector
except ModuleNotFoundError:  # pragma: no cover - exercised via monkeypatch in tests
    SpotifyConnector = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Spotify for Creators internal API base
_BASE_URL = "https://api-v5.anchor.fm"
_SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "05a1371ee5194c27860b3ff3ff3979d2")
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
    if SpotifyConnector is None:
        raise SpotifyPublishError(
            "spotifyconnector is not installed. Install requirements.txt to enable Spotify publishing."
        )
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
                if exc.response is not None:
                    body_snippet = exc.response.text[:500] if exc.response.text else "(empty)"
                    logger.error(
                        "Spotify API %s %s final failure body: %s",
                        method,
                        log_url,
                        body_snippet,
                    )
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


_VIDEO_CHUNK_SIZE = 30 * 1024 * 1024  # 30MB per chunk for video multipart


def _get_upload_url(
    session: requests.Session,
    anchor_id: int,
    *,
    filename: str,
    content_type: str,
    is_video: bool = False,
    file_size: int = 0,
) -> "tuple[str, str] | tuple[list[dict], str]":
    """Step 3: Get signed upload URL(s). Returns (signed_url, upload_id) for
    audio or (signed_url_parts, request_uuid) for video.

    Video uploads use multipart: each part gets its own signed GCS URL.
    Audio uses a single S3 signed URL.
    """
    url = f"{_BASE_URL}/v3/episodes/{anchor_id}/upload/signedUrl"
    params = _mums_params(filename=filename, type=content_type)
    if is_video:
        import math

        num_parts = max(1, math.ceil(file_size / _VIDEO_CHUNK_SIZE))
        params["uploadType"] = "video"
        params["isMultipartUpload"] = "true"
        params["numParts"] = str(num_parts)
    resp = _retry_request(
        session,
        "GET",
        url,
        params=params,
        timeout=15,
    )
    data = resp.json()
    upload_id = data.get("uploadId") or data["requestUuid"]
    if is_video and "signedUrlParts" in data:
        return data["signedUrlParts"], str(upload_id)
    return data.get("signedUrl") or data["url"], str(upload_id)


def _upload_audio(
    session: requests.Session,
    signed_url: str,
    audio_data: bytes,
    *,
    content_type: str,
) -> str:
    """Upload a single file to a signed URL (S3). Returns ETag."""
    resp = _retry_request(
        session,
        "PUT",
        signed_url,
        data=audio_data,
        headers={
            "Content-Type": content_type,
            "Authorization": None,  # strip bearer token
            **_MUTATION_HEADERS,
        },
        timeout=300,
    )
    etag = resp.headers.get("ETag", "").strip('"')
    logger.info("Uploaded audio (%d bytes, %s), ETag=%s", len(audio_data), content_type, etag)
    return etag


def _upload_video_multipart(
    session: requests.Session,
    signed_url_parts: list[dict],
    video_data: bytes,
) -> list[dict]:
    """Upload video in chunks to GCS multipart signed URLs.

    Returns list of {partNumber, etag} for process_upload.
    GCS signed URLs must NOT receive extra headers (Origin, Referer, Auth).
    """
    parts_etags = []
    for i, part_info in enumerate(signed_url_parts):
        start = i * _VIDEO_CHUNK_SIZE
        end = min(start + _VIDEO_CHUNK_SIZE, len(video_data))
        chunk = video_data[start:end]
        part_url = part_info["url"]

        resp = _retry_request(
            session,
            "PUT",
            part_url,
            data=chunk,
            headers={
                "Authorization": None,
                "Referer": "https://creators.spotify.com/",
            },
            timeout=300,
        )
        etag = resp.headers.get("ETag", "").strip('"')
        parts_etags.append({"partNumber": part_info["partNumber"], "etag": etag})
        logger.info(
            "Uploaded video part %d/%d (%d bytes), ETag=%s",
            part_info["partNumber"],
            len(signed_url_parts),
            len(chunk),
            etag,
        )
    return parts_etags


def _process_upload(
    session: requests.Session,
    upload_id: str,
    etag: str,
    *,
    anchor_id: int,
    station_id: str,
    user_id: str,
    filename: str,
    content_type: str = "audio/mpeg",
    parts_etags: list[dict] | None = None,
) -> None:
    """Step 5: Trigger processing and poll until complete."""
    is_video = content_type.startswith("video/")
    # For video multipart: use the provided parts list
    # For audio: single part with the etag
    if parts_etags is None:
        parts_etags = [{"partNumber": 1, "etag": etag}]

    url = f"{_BASE_URL}/v3/upload/{upload_id}/process_upload"
    _retry_request(
        session,
        "POST",
        url,
        headers=_MUTATION_HEADERS,
        params=_mums_params(),
        json={
            "userId": int(user_id),
            "uploadType": "video" if is_video else "default",
            "origin": "episode-media:upload",
            "caption": filename,
            "isExtractedFromVideo": is_video,
            "isMultipartUpload": True,
            "parts": parts_etags,
            "uploadId": upload_id,
            "episodeId": anchor_id,
            "stationId": int(station_id),
        },
        timeout=30,
    )

    # Poll for completion; tolerate 404 (media may not be visible immediately)
    # Use exponential backoff for 404s (known Spotify transient quirk)
    status_url = f"{_BASE_URL}/v3/upload/media/{upload_id}"
    backoff = _POLL_INTERVAL
    for attempt in range(_POLL_MAX_ATTEMPTS):
        time.sleep(backoff)
        try:
            resp = session.request(
                "GET",
                status_url,
                params=_mums_params(includeMediaValidation="true"),
                timeout=15,
            )
            if resp.status_code == 404:
                logger.debug(
                    "Upload %s status poll 404 (not ready), attempt %d",
                    upload_id,
                    attempt + 1,
                )
                backoff = min(backoff * 1.5, 30)  # backoff up to 30s between polls
                continue
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise SpotifyPublishError(
                f"Upload {upload_id} status poll failed: {exc}"
            ) from exc
        backoff = _POLL_INTERVAL  # reset on success
        data = resp.json()
        # Status is in data.request.state (not top-level "status")
        request_data = data.get("request", data)
        status = request_data.get("state") or data.get("status", "")
        if status in ("processed", "completed"):
            logger.info("Upload %s processing completed (state=%s)", upload_id, status)
            # Check mediaValidation for video
            validation = data.get("mediaValidation", {})
            if validation.get("status") == "validation_failure":
                reasons = [r.get("reason", "unknown") for r in validation.get("failures", [])]
                raise SpotifyPublishError(
                    f"Upload {upload_id} media validation failed: {reasons}"
                )
            return
        elif status == "failed":
            reason = request_data.get("failureReason", "unknown")
            # Also check mediaValidation for details
            validation = data.get("mediaValidation", {})
            failures = [r.get("reason", "") for r in validation.get("failures", [])]
            detail = f"{reason}" + (f" (validation: {failures})" if failures else "")
            raise SpotifyPublishError(
                f"Upload {upload_id} processing failed: {detail}"
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
    max_length: int | None = None,
) -> str:
    """Append timestamps HTML to the episode description if within char limit.

    If the combined description would exceed ``max_length``, the original
    description is returned unchanged (timestamps are dropped rather than
    truncating the description body).
    """
    limit = max_length if max_length is not None else MAX_SPOTIFY_DESCRIPTION_CHARS
    if not timestamps_html:
        return description
    combined = f"{description}{timestamps_html}"
    if len(combined) > limit:
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

    # Detect video artifact — prefer MP4 when present and non-empty.
    video_path: Path | None = None
    if mp3_path is not None:
        candidate_mp4 = mp3_path.parent / (mp3_path.stem + ".mp4")
        if candidate_mp4.exists() and candidate_mp4.stat().st_size > 0:
            video_path = candidate_mp4
            logger.info(
                "Video artifact found (%s, %.1f MB) — preferring MP4 for Spotify upload.",
                candidate_mp4.name,
                candidate_mp4.stat().st_size / 1_048_576,
            )

    if video_path is not None:
        upload_path: Path | None = video_path
        content_type = "video/mp4"
        format_label = "MP4"
    else:
        upload_path = wav_path if upload_format == "wav" else mp3_path
        content_type = "audio/wav" if upload_format == "wav" else "audio/mpeg"
        format_label = "WAV" if upload_format == "wav" else "MP3"

    # Dry-run mode
    if _is_dry_run():
        logger.info(
            "DRY RUN: Would publish %s as '%s' (%s, format=%s, content_type=%s)",
            upload_path,
            resolved_title,
            publish_behavior,
            format_label,
            content_type,
        )
        return PublishResult(
            anchor_episode_id=None,
            status="draft" if publish_behavior == "draft" else ("scheduled" if resolved_publish_on else "published"),
            dry_run=True,
            details={
                "title": resolved_title,
                "mp3_path": str(mp3_path),
                "wav_path": str(wav_path) if wav_path else None,
                "upload_path": str(upload_path) if upload_path else None,
                "upload_format": format_label.lower(),
                "content_type": content_type,
                "publish_behavior": publish_behavior,
            },
        )

    # Resolve credentials
    try:
        env_show_id, env_sp_dc, env_sp_key = _get_credentials()
        show_id = show_id or env_show_id
        sp_dc = sp_dc or env_sp_dc
        sp_key = sp_key or env_sp_key
    except ValueError as exc:
        return PublishResult(status="failed", error=str(exc))


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

        # Step 3 & 4: Upload file (video uses multipart GCS, audio uses single S3)
        is_video = content_type.startswith("video/")
        file_data = upload_path.read_bytes()

        if is_video:
            upload_result = _get_upload_url(
                session,
                anchor_id,
                filename=upload_path.name,
                content_type=content_type,
                is_video=True,
                file_size=len(file_data),
            )
            signed_url_parts, upload_id = upload_result  # type: ignore[misc]
            parts_etags = _upload_video_multipart(session, signed_url_parts, file_data)
        else:
            signed_url, upload_id = _get_upload_url(
                session,
                anchor_id,
                filename=upload_path.name,
                content_type=content_type,
            )
            etag = _upload_audio(session, signed_url, file_data, content_type=content_type)
            parts_etags = [{"partNumber": 1, "etag": etag}]

        # Step 5: Process upload
        _process_upload(
            session,
            upload_id,
            parts_etags[0]["etag"] if len(parts_etags) == 1 else parts_etags[0]["etag"],
            anchor_id=anchor_id,
            station_id=station_id,
            user_id=user_id,
            filename=upload_path.name,
            content_type=content_type,
            parts_etags=parts_etags if is_video else None,
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
        if publish_behavior != "draft":
            _publish_episode_live(session, anchor_id, resolved_publish_on)

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
