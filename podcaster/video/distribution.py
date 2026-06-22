"""Video podcast distribution to YouTube and Spotify (#242).

Handles uploading finished video podcasts (MP4) to:
- YouTube via the YouTube Data API v3 (resumable upload)
- Spotify via RSS feed update with video enclosure
- Azure Blob for archival storage

Authentication:
- YouTube: OAuth2 service account or user credentials via environment
- Spotify: Reuses the existing Spotify for Creators integration from podcaster.publish

Security:
- Credentials read from environment variables, never logged or committed.
- Dry-run mode (VIDEO_DISTRIBUTE_DRY_RUN=true) simulates all steps.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# --- Configuration ---

VIDEO_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-video-queue-v1"

_YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
_YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"
_YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2.0

# Minimum valid MP4 size (header alone is ~30 bytes, real video much larger)
_MIN_VALID_MP4_BYTES = 1024


@dataclass(frozen=True)
class VideoDistributionConfig:
    """Configuration for video distribution targets."""

    youtube_enabled: bool = False
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_refresh_token: str = ""
    youtube_category_id: str = "28"  # Science & Technology
    youtube_privacy: str = "unlisted"

    spotify_rss_enabled: bool = False
    spotify_rss_feed_path: str = ""

    spotify_upload_enabled: bool = False

    blob_archive_enabled: bool = True
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "VideoDistributionConfig":
        """Load configuration from environment variables."""
        return cls(
            youtube_enabled=os.environ.get("VIDEO_YOUTUBE_ENABLED", "").lower() == "true",
            youtube_client_id=os.environ.get("VIDEO_YOUTUBE_CLIENT_ID", ""),
            youtube_client_secret=os.environ.get("VIDEO_YOUTUBE_CLIENT_SECRET", ""),
            youtube_refresh_token=os.environ.get("VIDEO_YOUTUBE_REFRESH_TOKEN", ""),
            youtube_category_id=os.environ.get("VIDEO_YOUTUBE_CATEGORY_ID", "28"),
            youtube_privacy=os.environ.get("VIDEO_YOUTUBE_PRIVACY", "unlisted"),
            spotify_rss_enabled=os.environ.get("VIDEO_SPOTIFY_RSS_ENABLED", "").lower() == "true",
            spotify_rss_feed_path=os.environ.get("VIDEO_SPOTIFY_RSS_FEED_PATH", ""),
            spotify_upload_enabled=os.environ.get("VIDEO_SPOTIFY_UPLOAD_ENABLED", "").lower() == "true",
            blob_archive_enabled=os.environ.get("VIDEO_BLOB_ARCHIVE_ENABLED", "true").lower() == "true",
            dry_run=os.environ.get("VIDEO_DISTRIBUTE_DRY_RUN", "").lower() == "true",
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "VideoDistributionConfig":
        """Load from a request payload dict (subset of fields)."""
        return cls(
            youtube_enabled=bool(payload.get("youtube_enabled", False)),
            youtube_category_id=str(payload.get("youtube_category_id", "28")),
            youtube_privacy=str(payload.get("youtube_privacy", "unlisted")),
            spotify_rss_enabled=bool(payload.get("spotify_rss_enabled", False)),
            spotify_rss_feed_path=str(payload.get("spotify_rss_feed_path", "")),
            spotify_upload_enabled=bool(payload.get("spotify_upload_enabled", False)),
            blob_archive_enabled=bool(payload.get("blob_archive_enabled", True)),
            dry_run=bool(payload.get("dry_run", False)),
        )


@dataclass
class DistributionResult:
    """Result of distributing a video to one or more targets."""

    status: str = "pending"  # pending, completed, partial, failed
    youtube_id: str | None = None
    youtube_url: str | None = None
    spotify_rss_updated: bool = False
    spotify_upload_updated: bool = False
    blob_path: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status in ("completed", "partial")


class HttpTransport(Protocol):
    """Protocol for HTTP requests (allows mocking)."""

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> tuple[int, bytes]:
        ...

    def request_with_headers(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Like request() but also returns response headers."""
        ...


class _DefaultTransport:
    """Default HTTP transport using urllib."""

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> tuple[int, bytes]:
        req = Request(url, data=data, method=method, headers=headers or {})
        with urlopen(req, timeout=300) as resp:
            return resp.status, resp.read()

    def request_with_headers(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        req = Request(url, data=data, method=method, headers=headers or {})
        with urlopen(req, timeout=300) as resp:
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            return resp.status, resp_headers, resp.read()


class StorageUploader(Protocol):
    """Protocol for blob storage uploads."""

    def upload(self, path: str, content: bytes, content_type: str) -> str:
        """Upload bytes to blob storage, return the blob URL."""
        ...


# --- YouTube Upload ---


def _get_youtube_access_token(config: VideoDistributionConfig, transport: HttpTransport) -> str:
    """Exchange refresh token for a short-lived access token."""
    data = urlencode({
        "client_id": config.youtube_client_id,
        "client_secret": config.youtube_client_secret,
        "refresh_token": config.youtube_refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    status, body = transport.request(
        "https://oauth2.googleapis.com/token",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
    )
    if status != 200:
        raise RuntimeError(f"YouTube token refresh failed: HTTP {status}")

    token_data = json.loads(body)
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("YouTube token response missing access_token")
    return access_token


def upload_to_youtube(
    video_path: Path,
    title: str,
    description: str,
    config: VideoDistributionConfig,
    *,
    tags: list[str] | None = None,
    transport: HttpTransport | None = None,
) -> tuple[str | None, str | None]:
    """Upload a video to YouTube via the Data API v3.

    Returns (video_id, video_url) on success, (None, None) on failure.
    Raises RuntimeError on auth failures; returns None on upload failures
    after retries so distribution continues to other targets.
    """
    if not config.youtube_enabled:
        logger.info("YouTube upload disabled")
        return None, None

    if config.dry_run:
        logger.info("YouTube upload dry-run: %s", title)
        return "dry-run-id", "https://youtube.com/watch?v=dry-run-id"

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    file_size = video_path.stat().st_size
    if file_size < _MIN_VALID_MP4_BYTES:
        raise ValueError(f"Video file too small ({file_size} bytes), likely corrupt")

    http = transport or _DefaultTransport()
    access_token = _get_youtube_access_token(config, http)

    metadata = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags or ["podcast", "tech", "open-source"],
            "categoryId": config.youtube_category_id,
        },
        "status": {
            "privacyStatus": config.youtube_privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    # Initiate resumable upload
    params = urlencode({
        "uploadType": "resumable",
        "part": "snippet,status",
    })
    init_url = f"{_YOUTUBE_UPLOAD_URL}?{params}"
    metadata_bytes = json.dumps(metadata).encode("utf-8")

    status, resp_headers, body = http.request_with_headers(
        init_url,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": "video/mp4",
        },
        data=metadata_bytes,
    )

    if status not in (200, 308):
        logger.error("YouTube resumable upload init failed: HTTP %s", status)
        return None, None

    # Use the resumable session URI returned in the Location header
    upload_url = resp_headers.get("location", init_url)

    # Guard: single-request upload only suitable for files under 128 MB.
    # Larger files require chunked resumable upload (not yet implemented).
    _MAX_SINGLE_UPLOAD_BYTES = 128 * 1024 * 1024
    if file_size > _MAX_SINGLE_UPLOAD_BYTES:
        logger.error(
            "Video too large for single-request upload (%d bytes > %d). "
            "Chunked resumable upload not yet implemented.",
            file_size, _MAX_SINGLE_UPLOAD_BYTES,
        )
        return None, None

    video_bytes = video_path.read_bytes()

    for attempt in range(_MAX_RETRIES):
        try:
            upload_status, upload_body = http.request(
                upload_url,
                method="PUT",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(video_bytes)),
                },
                data=video_bytes,
            )
            if upload_status == 200:
                result = json.loads(upload_body)
                video_id = result.get("id", "")
                video_url = f"https://youtube.com/watch?v={video_id}"
                logger.info("YouTube upload succeeded: %s", video_url)
                return video_id, video_url
            else:
                logger.warning(
                    "YouTube upload attempt %d failed: HTTP %s", attempt + 1, upload_status
                )
        except Exception as exc:
            logger.warning("YouTube upload attempt %d error: %s", attempt + 1, exc)

        if attempt < _MAX_RETRIES - 1:
            time.sleep(_RETRY_BACKOFF_BASE ** attempt)

    logger.error("YouTube upload failed after %d attempts", _MAX_RETRIES)
    return None, None


# --- Spotify RSS Feed Update ---


def update_spotify_rss(
    video_url: str,
    title: str,
    description: str,
    duration_seconds: float,
    config: VideoDistributionConfig,
    *,
    pub_date: datetime | None = None,
    storage: StorageUploader | None = None,
) -> bool:
    """Update the podcast RSS feed with a video enclosure for Spotify.

    Spotify Video Podcasts require an <enclosure> element pointing to
    the video file URL. This appends a new <item> to the existing RSS feed.

    Returns True on success, False on failure.
    """
    if not config.spotify_rss_enabled:
        logger.info("Spotify RSS update disabled")
        return False

    if config.dry_run:
        logger.info("Spotify RSS update dry-run: %s", title)
        return True

    if not config.spotify_rss_feed_path:
        logger.error("Spotify RSS feed path not configured")
        return False

    pub = pub_date or datetime.now(timezone.utc)
    rfc2822_date = pub.strftime("%a, %d %b %Y %H:%M:%S +0000")

    # Build new RSS item with video enclosure
    item_xml = (
        "  <item>\n"
        f"    <title>{_escape_xml(title)}</title>\n"
        f"    <description>{_escape_xml(description)}</description>\n"
        f"    <enclosure url=\"{_escape_xml(video_url)}\" "
        f"type=\"video/mp4\" length=\"0\" />\n"
        f"    <pubDate>{rfc2822_date}</pubDate>\n"
        f"    <itunes:duration>{int(duration_seconds)}</itunes:duration>\n"
        f"    <itunes:episodeType>full</itunes:episodeType>\n"
        "  </item>\n"
    )

    logger.info("Spotify RSS item prepared for: %s", title)

    if storage is not None:
        try:
            # Read existing feed, insert item before </channel>
            existing = b""
            try:
                existing_result = getattr(storage, "get_bytes", lambda p: None)(
                    config.spotify_rss_feed_path
                )
                if existing_result:
                    existing = existing_result
            except Exception:
                pass

            if existing:
                feed_str = existing.decode("utf-8")
                insert_pos = feed_str.rfind("</channel>")
                if insert_pos >= 0:
                    updated_feed = feed_str[:insert_pos] + item_xml + feed_str[insert_pos:]
                else:
                    updated_feed = _create_rss_feed(item_xml)
            else:
                updated_feed = _create_rss_feed(item_xml)

            storage.upload(
                config.spotify_rss_feed_path,
                updated_feed.encode("utf-8"),
                "application/rss+xml",
            )
            logger.info("Spotify RSS feed updated at: %s", config.spotify_rss_feed_path)
            return True
        except Exception as exc:
            logger.error("Spotify RSS update failed: %s", exc)
            return False

    logger.warning("No storage backend for RSS update")
    return False


# --- Blob Archive ---


def archive_to_blob(
    video_path: Path,
    job_id: str,
    *,
    storage: StorageUploader | None = None,
    config: VideoDistributionConfig | None = None,
) -> str | None:
    """Archive the finished video to Azure Blob storage.

    Returns the full blob URL on success, None on failure.
    """
    if config and not config.blob_archive_enabled:
        logger.info("Blob archive disabled")
        return None

    if config and config.dry_run:
        blob_path = f"jobs/{job_id}/video/{job_id}.mp4"
        dry_run_url = f"https://dry-run.blob.core.windows.net/{blob_path}"
        logger.info("Blob archive dry-run: %s", dry_run_url)
        return dry_run_url

    if storage is None:
        logger.warning("No storage backend for blob archive")
        return None

    if not video_path.exists():
        logger.error("Video file not found for archival: %s", video_path)
        return None

    blob_path = f"jobs/{job_id}/video/{job_id}.mp4"
    video_bytes = video_path.read_bytes()

    try:
        blob_url = storage.upload(blob_path, video_bytes, "video/mp4")
        logger.info("Video archived to blob: %s (%d bytes)", blob_url, len(video_bytes))
        return blob_url
    except Exception as exc:
        logger.error("Blob archive failed: %s", exc)
        return None


# --- Spotify Episode Upload (#340) ---


def upload_to_spotify_episode(
    video_path: Path,
    anchor_id: int | None,
    config: VideoDistributionConfig,
    *,
    title: str | None = None,
    description: str | None = None,
) -> bool:
    """Publish the MP4 as a NEW separate Spotify episode draft (#340).

    Spotify rejects attaching a video to an episode that already holds audio, so
    the video is published as its own brand-new draft episode. The audio episode
    (``anchor_id``, resolved by the caller from
    ``generation.publish_result.anchor_id``) is never modified — it is passed
    only for reference/logging. Reuses the multipart video upload path in
    ``podcaster.publish``. Returns True on success, False otherwise.
    """
    if not anchor_id:
        logger.warning("Spotify video upload skipped: no anchor episode id available")
        return False

    if config.dry_run:
        logger.info("Spotify video upload dry-run: audio_anchor=%s", anchor_id)
        return True

    try:
        from podcaster.publish import upload_video_to_episode

        result = upload_video_to_episode(
            video_path,
            int(anchor_id),
            title=title,
            description=description,
            content_type="video/mp4",
        )
        if result.status == "failed":
            logger.error("Spotify video upload failed: %s", result.error)
            return False
        logger.info(
            "Spotify video published as new episode draft anchorId=%s "
            "(audio episode anchorId=%s untouched)",
            result.anchor_episode_id,
            anchor_id,
        )
        return True
    except Exception as exc:
        logger.error("Spotify video upload error: %s", exc)
        return False


# --- Orchestrator ---


def distribute_video(
    video_path: Path,
    job_id: str,
    title: str,
    description: str,
    duration_seconds: float,
    config: VideoDistributionConfig,
    *,
    tags: list[str] | None = None,
    transport: HttpTransport | None = None,
    storage: StorageUploader | None = None,
    spotify_anchor_id: int | None = None,
) -> DistributionResult:
    """Distribute a finished video podcast to all configured targets.

    Attempts all configured targets; failures on one target do not block others.
    Returns a DistributionResult summarizing outcomes across all targets.

    The video is always archived to blob when blob archive is enabled (#337);
    blob archive alone is a sufficient distribution target. Distribution only
    aborts if no target whatsoever (YouTube, Spotify RSS, Spotify upload, or
    blob archive) is enabled.

    ``spotify_anchor_id`` is the anchor episode id (resolved by the caller from
    ``generation.publish_result.anchor_id``) used to create a NEW separate video
    draft episode on Spotify (#340).
    """
    result = DistributionResult()

    # Abort only if no distribution target at all is enabled (#337)
    if not (
        config.youtube_enabled
        or config.spotify_rss_enabled
        or config.spotify_upload_enabled
        or config.blob_archive_enabled
    ):
        result.status = "failed"
        result.errors.append(
            "No distribution target configured. Enable at least one of: "
            "VIDEO_YOUTUBE_ENABLED, VIDEO_SPOTIFY_RSS_ENABLED, "
            "VIDEO_SPOTIFY_UPLOAD_ENABLED, VIDEO_BLOB_ARCHIVE_ENABLED."
        )
        logger.error(
            "video distribution aborted job_id=%s: no target configured", job_id
        )
        return result

    if not video_path.exists():
        result.status = "failed"
        result.errors.append(f"Video file not found: {video_path}")
        return result

    file_size = video_path.stat().st_size
    if file_size < _MIN_VALID_MP4_BYTES:
        result.status = "failed"
        result.errors.append(f"Video file too small ({file_size} bytes)")
        return result

    # 1. Archive to blob — always done first so the video is stored even when no
    #    listener-facing target succeeds (#337). Also provides the RSS enclosure URL.
    blob_path = archive_to_blob(video_path, job_id, storage=storage, config=config)
    result.blob_path = blob_path

    # 2. Upload to YouTube
    if config.youtube_enabled:
        try:
            video_id, video_url = upload_to_youtube(
                video_path, title, description, config,
                tags=tags, transport=transport,
            )
            result.youtube_id = video_id
            result.youtube_url = video_url
            if not video_id:
                result.errors.append("YouTube upload failed after retries")
        except Exception as exc:
            result.errors.append(f"YouTube upload error: {exc}")
            logger.error("YouTube distribution failed: %s", exc)

    # 3. Update Spotify RSS
    if config.spotify_rss_enabled:
        # blob_path is now a full URL returned from storage.upload(); prefer it over YouTube URL
        enclosure_url = blob_path or result.youtube_url or ""

        if not enclosure_url:
            logger.warning("No enclosure URL available for Spotify RSS — skipping")
            result.errors.append("Spotify RSS skipped: no enclosure URL")
        else:
            rss_ok = update_spotify_rss(
                enclosure_url, title, description, duration_seconds, config,
                storage=storage,
            )
            result.spotify_rss_updated = rss_ok
            if not rss_ok:
                result.errors.append("Spotify RSS update failed")

    # 4. Publish MP4 as a NEW separate Spotify episode draft (#340)
    if config.spotify_upload_enabled:
        upload_ok = upload_to_spotify_episode(
            video_path, spotify_anchor_id, config,
            title=title, description=description,
        )
        result.spotify_upload_updated = upload_ok
        if not upload_ok:
            result.errors.append("Spotify video upload failed")

    # Determine overall status
    targets_attempted = sum([
        config.youtube_enabled,
        config.spotify_rss_enabled,
        config.spotify_upload_enabled,
        config.blob_archive_enabled,
    ])
    targets_succeeded = sum([
        result.youtube_id is not None if config.youtube_enabled else False,
        result.spotify_rss_updated if config.spotify_rss_enabled else False,
        result.spotify_upload_updated if config.spotify_upload_enabled else False,
        result.blob_path is not None if config.blob_archive_enabled else False,
    ])

    if targets_succeeded == 0 and targets_attempted > 0:
        result.status = "failed"
    elif targets_succeeded < targets_attempted:
        result.status = "partial"
    else:
        result.status = "completed"

    logger.info(
        "video distribution job_id=%s status=%s youtube=%s rss=%s spotify_upload=%s blob=%s",
        job_id, result.status, result.youtube_id, result.spotify_rss_updated,
        result.spotify_upload_updated, result.blob_path,
    )
    return result


# --- Helpers ---


def _escape_xml(text: str) -> str:
    """Escape text for safe XML inclusion."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _create_rss_feed(item_xml: str) -> str:
    """Create a minimal RSS 2.0 feed with podcast namespace."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">\n'
        "<channel>\n"
        "  <title>SquadScope Video Podcast</title>\n"
        "  <description>AI-generated video podcast about open-source projects</description>\n"
        f"{item_xml}"
        "</channel>\n"
        "</rss>\n"
    )
