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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from podcaster.video.youtube_playlist import add_to_show_playlist as _add_to_show_playlist

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


def _load_youtube_refresh_token() -> str:
    """Resolve the YouTube refresh token from env or Azure Key Vault (#443).

    Best-effort: falls back to the bare ``VIDEO_YOUTUBE_REFRESH_TOKEN`` env var
    and never raises, so a misconfigured/unavailable vault degrades gracefully
    (identical to the previous env-only behavior when Key Vault is not set up).
    """
    try:
        from podcaster.youtube_credentials import load_youtube_refresh_token

        return load_youtube_refresh_token()
    except Exception:  # noqa: BLE001 - never break config loading on token fetch
        logger.warning("YouTube refresh-token load failed; falling back to env", exc_info=True)
        return os.environ.get("VIDEO_YOUTUBE_REFRESH_TOKEN", "")


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
            youtube_refresh_token=_load_youtube_refresh_token(),
            youtube_category_id=os.environ.get("VIDEO_YOUTUBE_CATEGORY_ID", "28"),
            youtube_privacy=os.environ.get("VIDEO_YOUTUBE_PRIVACY", "unlisted"),
            spotify_rss_enabled=os.environ.get("VIDEO_SPOTIFY_RSS_ENABLED", "").lower() == "true",
            spotify_rss_feed_path=os.environ.get("VIDEO_SPOTIFY_RSS_FEED_PATH", ""),
            spotify_upload_enabled=(
                os.environ.get("VIDEO_SPOTIFY_UPLOAD_ENABLED", "").lower() == "true"
            ),
            blob_archive_enabled=(
                os.environ.get("VIDEO_BLOB_ARCHIVE_ENABLED", "true").lower() == "true"
            ),
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
    ) -> tuple[int, bytes]: ...

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


def _read_http_error_body(exc: HTTPError) -> bytes:
    """Read an HTTPError body, tolerating instances with no underlying stream.

    HTTPError can be raised/constructed with ``fp=None`` (urllib does this for
    some responses, and tests construct them this way), in which case
    ``read()`` is unavailable or raises. Return ``b""`` in that case so the
    transport always yields a usable ``(status, body)`` tuple.
    """
    try:
        return exc.read()
    except (AttributeError, ValueError, OSError):
        return b""


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
        try:
            with urlopen(req, timeout=300) as resp:
                return resp.status, resp.read()
        except HTTPError as exc:
            # Non-2xx responses (e.g. 308 "Resume Incomplete" during a resumable
            # chunked upload) are surfaced by urllib as exceptions. Return them as
            # ordinary (status, body) results so callers can act on the status.
            return exc.code, _read_http_error_body(exc)

    def request_with_headers(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        req = Request(url, data=data, method=method, headers=headers or {})
        try:
            with urlopen(req, timeout=300) as resp:
                resp_headers = {k.lower(): v for k, v in resp.getheaders()}
                return resp.status, resp_headers, resp.read()
        except HTTPError as exc:
            # See request(): 308 and other non-2xx codes arrive as HTTPError but
            # are an expected part of the resumable upload protocol.
            resp_headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
            return exc.code, resp_headers, _read_http_error_body(exc)


class StorageUploader(Protocol):
    """Protocol for blob storage uploads."""

    def upload(self, path: str, content: bytes, content_type: str) -> str:
        """Upload bytes to blob storage, return the blob URL."""
        ...


# --- YouTube Upload ---


def _get_youtube_access_token(config: VideoDistributionConfig, transport: HttpTransport) -> str:
    """Exchange refresh token for a short-lived access token."""
    data = urlencode(
        {
            "client_id": config.youtube_client_id,
            "client_secret": config.youtube_client_secret,
            "refresh_token": config.youtube_refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode()

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
    params = urlencode(
        {
            "uploadType": "resumable",
            "part": "snippet,status",
        }
    )
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

    # Files above the single-request ceiling are uploaded via the resumable
    # chunked uploader (#442). Small files use the single-request path below.
    _MAX_SINGLE_UPLOAD_BYTES = 128 * 1024 * 1024
    if file_size > _MAX_SINGLE_UPLOAD_BYTES:
        chunked = _try_chunked_upload(
            video_path,
            title,
            description,
            config,
            tags=tags,
            transport=http,
        )
        if chunked is not None:
            return chunked
        logger.error(
            "Video too large for single-request upload (%d bytes > %d) and the "
            "chunked resumable uploader is unavailable.",
            file_size,
            _MAX_SINGLE_UPLOAD_BYTES,
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
            time.sleep(_RETRY_BACKOFF_BASE**attempt)

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
        f'    <enclosure url="{_escape_xml(video_url)}" '
        f'type="video/mp4" length="0" />\n'
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
    season_number: int | None = None,
    episode_number: int | None = None,
    return_episode_id: bool = False,
) -> bool | tuple[bool, int | None]:
    """Publish the MP4 as a NEW separate Spotify episode draft (#340).

    Spotify rejects attaching a video to an episode that already holds audio, so
    the video is published as its own brand-new draft episode. The audio episode
    (``anchor_id``, resolved by the caller from
    ``generation.publish_result.anchor_id``) is never modified — it is passed
    only for reference/logging. Reuses the multipart video upload path in
    ``podcaster.publish``. Returns True on success, False otherwise.
    """

    if config.dry_run:
        logger.info("Spotify video upload dry-run: audio_anchor=%s", anchor_id)
        return (True, None) if return_episode_id else True

    try:
        from podcaster.publish import upload_video_to_episode

        result = upload_video_to_episode(
            video_path,
            anchor_id,
            title=title,
            description=description,
            content_type="video/mp4",
            season_number=season_number,
            episode_number=episode_number,
        )
        if result.status == "failed":
            logger.error("Spotify video upload failed: %s", result.error)
            return (False, None) if return_episode_id else False
        logger.info(
            "Spotify video published as new episode draft anchorId=%s "
            "(audio episode anchorId=%s untouched)",
            result.anchor_episode_id,
            anchor_id,
        )
        if return_episode_id:
            return True, result.anchor_episode_id
        return True
    except Exception as exc:
        logger.error("Spotify video upload error: %s", exc)
        return (False, None) if return_episode_id else False


# --- Orchestrator ---


def _try_chunked_upload(
    video_path: Path,
    title: str,
    description: str,
    config: VideoDistributionConfig,
    *,
    tags: list[str] | None,
    transport: HttpTransport,
) -> tuple[str | None, str | None] | None:
    """Delegate to the resumable chunked uploader (#442) when available.

    Returns ``(video_id, video_url)`` on completion, ``(None, None)`` on a
    handled upload failure, or ``None`` when the chunked module is unavailable
    (caller falls back to single-request behavior).
    """
    try:
        from podcaster.video.youtube import upload_video
    except ImportError:  # noqa: BLE001 - optional module; degrade gracefully
        return None

    result = upload_video(
        video_path,
        title,
        description,
        config,
        tags=tags,
        transport=transport,
    )
    if result.succeeded:
        return result.video_id, result.video_url
    logger.error("YouTube chunked upload failed: %s", result.error)
    return None, None


def youtube_enabled_for_language(
    config: VideoDistributionConfig,
    language: str = "en",
    *,
    env: dict[str, str] | None = None,
) -> bool:
    """Whether YouTube upload is enabled for a given language/locale (#444).

    YouTube can be gated per show/locale via ``VIDEO_YOUTUBE_LANGUAGES`` (a
    comma-separated allow-list of language codes). When unset, YouTube applies to
    all languages (back-compatible). A language is matched on its base code
    (``fr-FR`` → ``fr``).
    """
    if not config.youtube_enabled:
        return False
    source = os.environ if env is None else env
    raw = source.get("VIDEO_YOUTUBE_LANGUAGES", "")
    allow = {item.strip().lower().split("-", 1)[0] for item in raw.split(",") if item.strip()}
    if not allow:
        return True
    return (language or "en").split("-", 1)[0].lower() in allow


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
    season_number: int | None = None,
    episode_number: int | None = None,
    locale: str | None = None,
    language: str = "en",
    published: Mapping[str, Any] | None = None,
    on_published: Callable[[str, dict[str, Any]], None] | None = None,
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

    ``season_number`` and ``episode_number`` are passed to the Spotify video
    episode upload so the video episode carries the same numbering as the audio
    episode (season = year, episode = ISO week number).

    ``locale`` is forwarded to :func:`~podcaster.video.youtube_playlist.add_to_show_playlist`
    to select the per-language playlist after a successful YouTube upload (#449).

    Per-platform ``published`` state is the durable at-most-once guard for
    provider side effects. Residual risk: a crash after a provider create but
    before ``on_published`` persists is still at-least-once for YouTube (no cheap
    reconcile key); Spotify closes that window by reconciling drafts by title
    before creating one.
    """
    result = DistributionResult()
    prior_published = published or {}

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
        logger.error("video distribution aborted job_id=%s: no target configured", job_id)
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

    # 2. Upload to YouTube (config-gated, and optionally per show/locale, #444)
    youtube_active = youtube_enabled_for_language(config, language)
    if config.youtube_enabled and not youtube_active:
        logger.info(
            "YouTube upload skipped for language=%s (not in VIDEO_YOUTUBE_LANGUAGES)",
            language,
        )
    youtube_record = prior_published.get("youtube")
    if (
        youtube_active
        and isinstance(youtube_record, Mapping)
        and youtube_record.get("status") == "published"
    ):
        video_id = youtube_record.get("video_id")
        if video_id is not None:
            result.youtube_id = str(video_id)
            result.youtube_url = f"https://www.youtube.com/watch?v={result.youtube_id}"
        logger.info("YouTube upload skipped for job_id=%s: already published", job_id)
    elif youtube_active:
        try:
            video_id, video_url = upload_to_youtube(
                video_path,
                title,
                description,
                config,
                tags=tags,
                transport=transport,
            )
            result.youtube_id = video_id
            result.youtube_url = video_url
            if not video_id:
                result.errors.append("YouTube upload failed after retries")
            else:
                if on_published is not None and not config.dry_run:
                    on_published(
                        "youtube",
                        {
                            "status": "published",
                            "video_id": video_id,
                            "at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                if not config.dry_run:
                    # 2a. Add to show playlist — idempotent, failure is non-fatal (#449)
                    try:
                        _http = transport or _DefaultTransport()
                        _token = _get_youtube_access_token(config, _http)
                        _add_to_show_playlist(config, locale, video_id, _token, transport=transport)
                    except Exception as exc:
                        logger.warning("Playlist add skipped for %s: %s", video_id, exc)
        except Exception as exc:
            result.errors.append(f"YouTube upload error: {exc}")
            logger.error("YouTube distribution failed: %s", exc)

    # 3. Update Spotify RSS
    if config.spotify_rss_enabled:
        rss_record = prior_published.get("spotify_rss")
        if isinstance(rss_record, Mapping) and rss_record.get("status") == "published":
            result.spotify_rss_updated = True
            logger.info("Spotify RSS update skipped for job_id=%s: already published", job_id)
        else:
            # blob_path is now a full URL returned from storage.upload(); prefer it over YouTube URL
            enclosure_url = blob_path or result.youtube_url or ""

            if not enclosure_url:
                logger.warning("No enclosure URL available for Spotify RSS — skipping")
                result.errors.append("Spotify RSS skipped: no enclosure URL")
            else:
                rss_ok = update_spotify_rss(
                    enclosure_url,
                    title,
                    description,
                    duration_seconds,
                    config,
                    storage=storage,
                )
                result.spotify_rss_updated = rss_ok
                if rss_ok and on_published is not None and not config.dry_run:
                    on_published(
                        "spotify_rss",
                        {
                            "status": "published",
                            "at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                if not rss_ok:
                    result.errors.append("Spotify RSS update failed")

    # 4. Publish MP4 as a NEW separate Spotify episode draft (#340)
    if config.spotify_upload_enabled:
        spotify_upload_record = prior_published.get("spotify_upload")
        if (
            isinstance(spotify_upload_record, Mapping)
            and spotify_upload_record.get("status") == "published"
        ):
            result.spotify_upload_updated = True
            logger.info("Spotify video upload skipped for job_id=%s: already published", job_id)
        else:
            upload_result = upload_to_spotify_episode(
                video_path,
                spotify_anchor_id,
                config,
                title=title,
                description=description,
                season_number=season_number,
                episode_number=episode_number,
                return_episode_id=True,
            )
            if isinstance(upload_result, tuple):
                upload_ok, spotify_episode_id = upload_result
            else:
                upload_ok = upload_result
                spotify_episode_id = None
            result.spotify_upload_updated = upload_ok
            if upload_ok and on_published is not None and not config.dry_run:
                on_published(
                    "spotify_upload",
                    {
                        "status": "published",
                        "episode_id": spotify_episode_id,
                        "at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            if not upload_ok:
                result.errors.append("Spotify video upload failed")

    # Determine overall status
    targets_attempted = sum(
        [
            youtube_active,
            config.spotify_rss_enabled,
            config.spotify_upload_enabled,
            config.blob_archive_enabled,
        ]
    )
    targets_succeeded = sum(
        [
            result.youtube_id is not None if youtube_active else False,
            result.spotify_rss_updated if config.spotify_rss_enabled else False,
            result.spotify_upload_updated if config.spotify_upload_enabled else False,
            result.blob_path is not None if config.blob_archive_enabled else False,
        ]
    )

    if targets_succeeded == 0 and targets_attempted > 0:
        result.status = "failed"
    elif targets_succeeded < targets_attempted:
        result.status = "partial"
    else:
        result.status = "completed"

    logger.info(
        "video distribution job_id=%s status=%s youtube=%s rss=%s spotify_upload=%s blob=%s",
        job_id,
        result.status,
        result.youtube_id,
        result.spotify_rss_updated,
        result.spotify_upload_updated,
        result.blob_path,
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
