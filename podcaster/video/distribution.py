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
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from podcaster.publication_state import (
    DRAFT_CREATED,
    MANUAL_HANDOFF_REQUIRED,
    PUBLICATION_UNKNOWN,
    PUBLISHED,
    UPLOADED,
    outcome_from_spotify_terminal_state,
)
from podcaster.video.youtube_playlist import add_to_show_playlist as _add_to_show_playlist
from podcaster.video.youtube_playlist import resolve_playlist_id as _resolve_playlist_id
from podcaster.video.youtube_reconcile import (
    reconcile_youtube_upload,
    tags_with_identity,
    youtube_identity_tag,
)

logger = logging.getLogger(__name__)

# --- Configuration ---

VIDEO_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-video-queue-v1"

_YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
_YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"
# This module does not pass a scope: the refresh_token grant carries whatever
# scope was consented to at mint time (see scripts/youtube_oauth_setup.py).
# Canonical scope constant lives in podcaster.youtube_oauth.YOUTUBE_SCOPE
# (#649): the refresh token backing this pipeline must include playlist +
# read-back access, not just youtube.upload.
_TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
_OAUTH_IDENTIFIER_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_TRANSIENT_TRANSPORT_ERRORS = (ConnectionError, TimeoutError, URLError)

_MAX_RETRIES = 3
_MAX_SINGLE_UPLOAD_BYTES = 128 * 1024 * 1024
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
    youtube_playlist_id: str = ""
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_refresh_token: str = ""
    youtube_category_id: str = "28"  # Science & Technology
    youtube_privacy: str = "unlisted"
    youtube_required: bool = False

    spotify_rss_enabled: bool = False
    spotify_rss_feed_path: str = ""

    spotify_upload_enabled: bool = False
    spotify_video_publish_mode: str = "draft"

    blob_archive_enabled: bool = True
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "VideoDistributionConfig":
        """Load configuration from environment variables."""
        return cls(
            youtube_enabled=os.environ.get("VIDEO_YOUTUBE_ENABLED", "").lower() == "true",
            youtube_playlist_id=os.environ.get("VIDEO_YOUTUBE_PLAYLIST_ID", ""),
            youtube_client_id=os.environ.get("VIDEO_YOUTUBE_CLIENT_ID", ""),
            youtube_client_secret=os.environ.get("VIDEO_YOUTUBE_CLIENT_SECRET", ""),
            youtube_refresh_token=_load_youtube_refresh_token(),
            youtube_category_id=os.environ.get("VIDEO_YOUTUBE_CATEGORY_ID", "28"),
            youtube_privacy=os.environ.get("VIDEO_YOUTUBE_PRIVACY", "unlisted"),
            youtube_required=os.environ.get("VIDEO_YOUTUBE_REQUIRED", "").lower() == "true",
            spotify_rss_enabled=os.environ.get("VIDEO_SPOTIFY_RSS_ENABLED", "").lower() == "true",
            spotify_rss_feed_path=os.environ.get("VIDEO_SPOTIFY_RSS_FEED_PATH", ""),
            spotify_upload_enabled=(
                os.environ.get("VIDEO_SPOTIFY_UPLOAD_ENABLED", "").lower() == "true"
            ),
            spotify_video_publish_mode=os.environ.get("SPOTIFY_VIDEO_PUBLISH_MODE", "draft")
            .strip()
            .lower(),
            blob_archive_enabled=(
                os.environ.get("VIDEO_BLOB_ARCHIVE_ENABLED", "true").lower() == "true"
            ),
            dry_run=os.environ.get("VIDEO_DISTRIBUTE_DRY_RUN", "").lower() == "true",
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "VideoDistributionConfig":
        """Load from a request payload dict (subset of fields)."""
        youtube_playlist_id = payload.get("youtube_playlist_id")
        youtube_category_id = payload.get("youtube_category_id")
        youtube_privacy = payload.get("youtube_privacy")
        return cls(
            youtube_enabled=bool(payload.get("youtube_enabled", False)),
            youtube_playlist_id="" if youtube_playlist_id is None else str(youtube_playlist_id),
            youtube_category_id="28" if youtube_category_id is None else str(youtube_category_id),
            youtube_privacy="unlisted" if youtube_privacy is None else str(youtube_privacy),
            youtube_required=bool(payload.get("youtube_required", False)),
            spotify_rss_enabled=bool(payload.get("spotify_rss_enabled", False)),
            spotify_rss_feed_path=str(payload.get("spotify_rss_feed_path", "")),
            spotify_upload_enabled=bool(payload.get("spotify_upload_enabled", False)),
            spotify_video_publish_mode=(
                "draft"
                if payload.get("spotify_video_publish_mode") is None
                else str(payload.get("spotify_video_publish_mode")).strip().lower()
            ),
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
    spotify_video_promote_terminal_state: str | None = None
    spotify_video_is_published: bool | None = None
    blob_path: str | None = None
    errors: list[str] = field(default_factory=list)
    youtube_required_failed: bool = False
    youtube_failure_retryable: bool = False
    youtube_failure_code: str | None = None
    youtube_failure_stage: str | None = None
    youtube_failure_http_status: int | None = None
    youtube_oauth_error: str | None = None
    youtube_oauth_error_subtype: str | None = None
    youtube_playlist_id: str | None = None
    youtube_playlist_succeeded: bool = False
    publish_run_id: str | None = None
    provider_outcomes: dict[str, str] = field(default_factory=dict)
    provider_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    public_delivery_status: str = "pending"

    @property
    def succeeded(self) -> bool:
        return self.status in ("completed", "partial")


def _record_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    provider: str,
    provider_id_field: str,
) -> dict[str, Any]:
    outcome = str(snapshot.get("outcome") or DRAFT_CREATED)
    verification = str(snapshot.get("verification") or "none")
    derived_status = (
        "public"
        if outcome == PUBLISHED and verification == "external_verified"
        else "pending"
        if outcome in (PUBLISHED, UPLOADED)
        else "draft"
        if outcome == DRAFT_CREATED
        else "gated"
        if outcome == MANUAL_HANDOFF_REQUIRED
        else "unknown"
    )
    status = snapshot.get("provider_status")
    if (
        not isinstance(status, str)
        or status == "public"
        and not (outcome == PUBLISHED and verification == "external_verified")
    ):
        status = derived_status
    provider_id = snapshot.get("provider_id") or snapshot.get(provider_id_field)
    return {
        "provider": provider,
        "outcome": outcome,
        "status": status,
        "provider_id": str(provider_id) if provider_id is not None else None,
        "native_state": snapshot.get("native_state"),
        "transport_status": snapshot.get("transport_status", "previously_recorded"),
        "verification": verification,
        "checked_at": snapshot.get("checked_at") or snapshot.get("at"),
        "evidence_source": snapshot.get("evidence_source", "publication_snapshot"),
        "last_error_code": snapshot.get("last_error_code"),
        "retry_blocked": bool(snapshot.get("retry_blocked", True)),
    }


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


class YouTubeDeliveryError(RuntimeError):
    """Sanitized YouTube failure with retryability and optional OAuth subtype."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        stage: str,
        retryable: bool,
        http_status: int | None = None,
        oauth_error: str | None = None,
        oauth_error_subtype: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.http_status = http_status
        self.oauth_error = oauth_error
        self.oauth_error_subtype = oauth_error_subtype

    def as_sanitized_dict(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "code": self.code,
            "stage": self.stage,
            "retryable": self.retryable,
        }
        if self.http_status is not None:
            details["http_status"] = self.http_status
        if self.oauth_error:
            details["oauth_error"] = self.oauth_error
        if self.oauth_error_subtype:
            details["oauth_error_subtype"] = self.oauth_error_subtype
        return details


def _is_transient_http_status(status: int) -> bool:
    return status in _TRANSIENT_HTTP_STATUSES


def _decode_body_text(body: bytes | str) -> str:
    return body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")


def _sanitize_oauth_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    identifier = value.strip().lower()
    return identifier if _OAUTH_IDENTIFIER_RE.fullmatch(identifier) else None


def _extract_oauth_error_fields(body: bytes | str) -> tuple[str | None, str | None]:
    text = _decode_body_text(body)
    try:
        payload = json.loads(text)
    except ValueError:
        return None, None
    if not isinstance(payload, dict):
        return None, None

    error = payload.get("error")
    error_code: str | None = None
    error_subtype: str | None = None
    if isinstance(error, str):
        error_code = _sanitize_oauth_identifier(error)
    elif isinstance(error, dict):
        error_code = _sanitize_oauth_identifier(error.get("code"))
        error_subtype = _sanitize_oauth_identifier(error.get("error_subtype"))

    subtype = payload.get("error_subtype")
    if error_subtype is None:
        error_subtype = _sanitize_oauth_identifier(subtype)
    return error_code, error_subtype


def _notify_youtube_reauthorization() -> None:
    """Dispatch the existing sanitized invalid-grant operator alert."""
    try:
        from podcaster.credential_expiry import notify_youtube_credential_expiry

        notify_youtube_credential_expiry(
            "YouTube OAuth refresh token was revoked or expired (invalid_grant)."
        )
    except Exception:  # noqa: BLE001 - alerting must never replace the delivery failure
        logger.warning("failed to dispatch YouTube re-auth alert", exc_info=True)


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

    try:
        status, body = transport.request(
            "https://oauth2.googleapis.com/token",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
        )
    except _TRANSIENT_TRANSPORT_ERRORS as exc:
        raise YouTubeDeliveryError(
            "YouTube token refresh failed: network error",
            code="youtube_oauth_network_error",
            stage="oauth_token",
            retryable=True,
        ) from exc
    if status != 200:
        oauth_error, oauth_subtype = _extract_oauth_error_fields(body)
        if oauth_error == "invalid_grant":
            _notify_youtube_reauthorization()
        code = f"youtube_oauth_{oauth_error}" if oauth_error else f"youtube_oauth_http_{status}"
        raise YouTubeDeliveryError(
            f"YouTube token refresh failed: HTTP {status}",
            code=code,
            stage="oauth_token",
            retryable=_is_transient_http_status(status),
            http_status=status,
            oauth_error=oauth_error,
            oauth_error_subtype=oauth_subtype,
        )

    try:
        token_data = json.loads(body)
    except ValueError as exc:
        raise YouTubeDeliveryError(
            "YouTube token response was not valid JSON",
            code="youtube_oauth_invalid_json",
            stage="oauth_token",
            retryable=False,
        ) from exc
    access_token = token_data.get("access_token")
    if not access_token:
        raise YouTubeDeliveryError(
            "YouTube token response missing access_token",
            code="youtube_oauth_missing_access_token",
            stage="oauth_token",
            retryable=False,
        )
    return access_token


def _reconcile_unknown_youtube_upload(
    record: Mapping[str, Any],
    config: VideoDistributionConfig,
    result: DistributionResult,
    *,
    publication_identity_context: Any | None,
    transport: HttpTransport | None,
    on_published: Callable[[str, dict[str, Any]], None] | None,
    publish_run_id: str | None,
) -> bool:
    """Bind an ambiguous YouTube upload by its declared identity tag (#678).

    Only runs for a ``publication_unknown`` intent without a provider ID whose
    durable intent declared the exact identity tag for this publication. It
    never uploads: a non-match leaves the job fail-closed and returns False so
    the caller keeps the existing retry-blocked behaviour.
    """
    if config.dry_run or record.get("outcome") != PUBLICATION_UNKNOWN:
        return False
    if record.get("video_id") or record.get("provider_id"):
        return False
    expected_tag = youtube_identity_tag(publication_identity_context)
    declared_tag = record.get("identity_tag")
    if not expected_tag or declared_tag != expected_tag:
        return False
    try:
        http = transport or _DefaultTransport()
        access_token = _get_youtube_access_token(config, http)
        reconciled = reconcile_youtube_upload(expected_tag, access_token, http)
    except Exception as exc:
        logger.warning(
            "YouTube identity reconcile failed; retry remains blocked: %s",
            type(exc).__name__,
        )
        return False
    if not reconciled.matched or reconciled.video_id is None:
        logger.warning(
            "YouTube identity reconcile did not bind an upload status=%s code=%s; "
            "publication remains unknown",
            reconciled.status,
            reconciled.code,
        )
        return False

    video_id = reconciled.video_id
    privacy = reconciled.privacy_status or config.youtube_privacy
    checked_at = datetime.now(timezone.utc).isoformat()
    result.youtube_id = video_id
    result.youtube_url = f"https://youtube.com/watch?v={video_id}"
    result.provider_outcomes["youtube"] = DRAFT_CREATED
    result.provider_records["youtube"] = {
        "provider": "youtube",
        "outcome": DRAFT_CREATED,
        "status": "unlisted" if privacy == "unlisted" else "private",
        "provider_id": video_id,
        "native_state": privacy,
        "transport_status": "reconciled",
        "verification": "provider_readback",
        "checked_at": checked_at,
        "evidence_source": "youtube_identity_readback",
        "last_error_code": None,
        "retry_blocked": True,
    }
    logger.info("YouTube upload reconciled by identity tag: %s", result.youtube_url)
    if on_published is not None:
        on_published(
            "youtube",
            {
                "status": "published",
                "provider_status": result.provider_records["youtube"]["status"],
                "outcome": DRAFT_CREATED,
                "provider": "youtube",
                "provider_id": video_id,
                "native_state": privacy,
                "transport_status": "reconciled",
                "verification": "provider_readback",
                "evidence_source": "youtube_identity_readback",
                "retry_blocked": True,
                "video_id": video_id,
                "publish_run_id": publish_run_id,
                "at": checked_at,
            },
        )
    return True


def upload_to_youtube(
    video_path: Path,
    title: str,
    description: str,
    config: VideoDistributionConfig,
    *,
    tags: list[str] | None = None,
    transport: HttpTransport | None = None,
    raise_on_failure: bool = False,
    identity_tag: str | None = None,
) -> tuple[str | None, str | None]:
    """Upload a video to YouTube via the Data API v3.

    ``identity_tag`` stamps the upload with the deterministic publication
    identity tag so an ambiguous create can later be reconciled (#678).

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

    if config.youtube_privacy not in ("private", "unlisted"):
        raise ValueError("YouTube uploads must start as private or unlisted drafts")

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    file_size = video_path.stat().st_size
    if file_size < _MIN_VALID_MP4_BYTES:
        raise ValueError(f"Video file too small ({file_size} bytes), likely corrupt")

    # Resolve the chunked uploader BEFORE opening a session: the init POST
    # creates the YouTube video, so failing afterwards would orphan it (#698).
    chunked_uploader: Callable[..., Any] | None = None
    if file_size > _MAX_SINGLE_UPLOAD_BYTES:
        chunked_uploader = _load_chunked_uploader()
        if chunked_uploader is None:
            logger.error(
                "Video too large for single-request upload (%d bytes > %d) and the "
                "chunked resumable uploader is unavailable.",
                file_size,
                _MAX_SINGLE_UPLOAD_BYTES,
            )
            if raise_on_failure:
                raise YouTubeDeliveryError(
                    "YouTube chunked uploader unavailable for large video",
                    code="youtube_chunked_unavailable",
                    stage="upload_chunked",
                    retryable=False,
                )
            return None, None

    http = transport or _DefaultTransport()
    access_token = _get_youtube_access_token(config, http)

    metadata = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags_with_identity(tags or ["podcast", "tech", "open-source"], identity_tag),
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

    try:
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
    except _TRANSIENT_TRANSPORT_ERRORS as exc:
        if raise_on_failure:
            raise YouTubeDeliveryError(
                "YouTube resumable upload init failed: network error",
                code="youtube_upload_init_network_error",
                stage="upload_init",
                retryable=True,
            ) from exc
        logger.error("YouTube resumable upload init failed: network error")
        return None, None

    if status not in (200, 308):
        logger.error("YouTube resumable upload init failed: HTTP %s", status)
        if raise_on_failure:
            raise YouTubeDeliveryError(
                f"YouTube resumable upload init failed: HTTP {status}",
                code=f"youtube_upload_init_http_{status}",
                stage="upload_init",
                retryable=_is_transient_http_status(status),
                http_status=status,
            )
        return None, None

    # The init above is the ONE videos.insert for this attempt: YouTube creates
    # the video resource as soon as the session is opened (#698). Every later
    # request (single PUT, chunks, resume probes, retries) must reuse this
    # session URI; nothing below may open a second session.
    upload_url = resp_headers.get("location")
    if not upload_url:
        logger.error("YouTube resumable upload init returned no session URI")
        if raise_on_failure:
            raise YouTubeDeliveryError(
                "YouTube resumable upload init returned no session URI",
                code="youtube_upload_init_missing_session",
                stage="upload_init",
                retryable=False,
            )
        return None, None

    # Files above the single-request ceiling are uploaded in resumable chunks
    # (#442) over the session opened above.
    if chunked_uploader is not None:
        return _try_chunked_upload(
            video_path,
            session_uri=upload_url,
            access_token=access_token,
            file_size=file_size,
            transport=http,
            raise_on_failure=raise_on_failure,
            uploader=chunked_uploader,
        )

    video_bytes = video_path.read_bytes()
    last_status: int | None = None
    last_error: Exception | None = None

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
            last_status = upload_status
            logger.warning("YouTube upload attempt %d failed: HTTP %s", attempt + 1, upload_status)
            if not _is_transient_http_status(upload_status):
                break
        except _TRANSIENT_TRANSPORT_ERRORS as exc:
            last_error = exc
            logger.warning("YouTube upload attempt %d network error", attempt + 1)
        except Exception as exc:
            if raise_on_failure:
                raise YouTubeDeliveryError(
                    "YouTube upload failed: non-network error",
                    code="youtube_upload_error",
                    stage="upload_put",
                    retryable=False,
                ) from exc
            logger.error("YouTube upload failed: non-network error", exc_info=True)
            return None, None

        should_retry = last_error is not None or (
            last_status is not None and _is_transient_http_status(last_status)
        )
        if should_retry and attempt < _MAX_RETRIES - 1:
            time.sleep(_RETRY_BACKOFF_BASE**attempt)
        elif not should_retry:
            break

    logger.error("YouTube upload failed after %d attempts", _MAX_RETRIES)
    if raise_on_failure:
        if last_status is not None:
            raise YouTubeDeliveryError(
                f"YouTube upload failed after retries: HTTP {last_status}",
                code=f"youtube_upload_http_{last_status}",
                stage="upload_put",
                retryable=_is_transient_http_status(last_status),
                http_status=last_status,
            )
        if last_error is not None:
            raise YouTubeDeliveryError(
                "YouTube upload failed after retries: network error",
                code="youtube_upload_network_error",
                stage="upload_put",
                retryable=True,
            ) from last_error
        raise YouTubeDeliveryError(
            "YouTube upload failed after retries",
            code="youtube_upload_failed",
            stage="upload_put",
            retryable=False,
        )
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
    job_id: str | None = None,
    publish_run_id: str | None = None,
    publication_storage: Any | None = None,
    publication_identity_context: Any | None = None,
) -> bool | tuple[bool, int | None, str | None] | tuple[bool, int | None, str | None, str]:
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
        return (True, None, None) if return_episode_id else True

    try:
        from podcaster.publish import promote_spotify_video_draft, upload_video_to_episode

        promote_terminal_state: str | None = None
        upload_kwargs: dict[str, Any] = {
            "title": title,
            "description": description,
            "content_type": "video/mp4",
            "season_number": season_number,
            "episode_number": episode_number,
        }
        if publication_storage is not None and publication_identity_context is not None:
            upload_kwargs["publication_storage"] = publication_storage
            upload_kwargs["publication_identity_context"] = publication_identity_context
        result = upload_video_to_episode(video_path, anchor_id, **upload_kwargs)
        if result.status == "failed":
            logger.error("Spotify video upload failed: %s", result.error)
            return (
                (False, result.anchor_episode_id, None, result.outcome or PUBLICATION_UNKNOWN)
                if return_episode_id
                else False
            )
        if result.anchor_episode_id is not None:
            try:
                promote_result = promote_spotify_video_draft(
                    result.anchor_episode_id,
                    audio_anchor_id=anchor_id,
                    spotify_video_publish_mode=getattr(
                        config, "spotify_video_publish_mode", "draft"
                    ),
                    job_id=job_id,
                    run_id=publish_run_id,
                )
                promote_terminal_state = promote_result.terminal_state
                logger.info(
                    "Spotify video promote terminal_state=%s is_published=%s",
                    promote_result.terminal_state,
                    promote_result.is_published,
                )
            except Exception as promote_exc:  # noqa: BLE001
                promote_terminal_state = "failed"
                logger.warning(
                    "Spotify video promote raised unexpectedly (upload already succeeded, "
                    "publication not confirmed); anchorId=%s error=%s",
                    result.anchor_episode_id,
                    promote_exc,
                )
        logger.info(
            "Spotify video uploaded as new episode anchorId=%s "
            "(audio episode anchorId=%s untouched)",
            result.anchor_episode_id,
            anchor_id,
        )
        if return_episode_id:
            return True, result.anchor_episode_id, promote_terminal_state
        return True
    except Exception as exc:
        logger.error("Spotify video upload error: %s", exc)
        return (False, None, None, PUBLICATION_UNKNOWN) if return_episode_id else False


# --- Orchestrator ---


def _load_chunked_uploader() -> Callable[..., Any] | None:
    """Return :func:`podcaster.video.youtube.upload_chunked`, or None if unavailable."""
    try:
        from podcaster.video.youtube import upload_chunked
    except ImportError:  # noqa: BLE001 - optional module; degrade gracefully
        return None
    return upload_chunked


def _try_chunked_upload(
    video_path: Path,
    *,
    session_uri: str,
    access_token: str,
    file_size: int,
    transport: HttpTransport,
    raise_on_failure: bool = False,
    uploader: Callable[..., Any] | None = None,
) -> tuple[str | None, str | None]:
    """Upload *video_path* in chunks over an already-open resumable session.

    The caller has already opened the session (the videos.insert). This helper
    must never open another one, otherwise YouTube keeps an orphan zero-length
    video for the abandoned session (#698). Transient chunk failures resume
    the same ``session_uri`` from the server-acknowledged offset.

    Returns ``(video_id, video_url)`` on completion or ``(None, None)`` on a
    handled upload failure (raises instead when ``raise_on_failure``).
    """
    upload_chunked = uploader or _load_chunked_uploader()
    if upload_chunked is None:
        # upload_to_youtube resolves the uploader before init; this is a guard.
        raise RuntimeError("chunked uploader unavailable after session init")

    try:
        result = upload_chunked(
            transport,
            session_uri,
            access_token,
            video_path,
            file_size,
        )
    except _TRANSIENT_TRANSPORT_ERRORS as exc:
        if raise_on_failure:
            raise YouTubeDeliveryError(
                "YouTube chunked upload failed: network error",
                code="youtube_chunked_network_error",
                stage="upload_chunked",
                retryable=True,
            ) from exc
        logger.error("YouTube chunked upload failed: network error", exc_info=True)
        return None, None
    if result.succeeded:
        return result.video_id, result.video_url
    if raise_on_failure:
        error_text = (result.error or "").strip()
        lowered = error_text.lower()
        http_status: int | None = None
        if "http " in lowered:
            try:
                http_status = int(lowered.split("http ", 1)[1].split()[0])
            except (ValueError, IndexError):
                http_status = None
        retryable = (http_status is not None and _is_transient_http_status(http_status)) or (
            "network error" in lowered
        )
        code = (
            f"youtube_chunked_http_{http_status}"
            if http_status is not None
            else (
                "youtube_chunked_network_error"
                if "network error" in lowered
                else "youtube_chunked_failed"
            )
        )
        raise YouTubeDeliveryError(
            "YouTube chunked upload failed",
            code=code,
            stage="upload_chunked",
            retryable=retryable,
            http_status=http_status,
        )
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
    publish_run_id: str | None = None,
    publication_storage: Any | None = None,
    publication_identity_context: Any | None = None,
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
    provider side effects. A crash after a YouTube create but before
    ``on_published`` persists leaves the upload ``publication_unknown``; on
    redelivery the upload is bound read-only by its deterministic identity tag
    when exactly one match exists, otherwise it stays fail-closed (#678).
    Spotify closes that window by reconciling drafts before creating one.
    """
    result = DistributionResult(publish_run_id=publish_run_id)
    prior_published = published or {}
    youtube_required_failure: YouTubeDeliveryError | None = None

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
        and _reconcile_unknown_youtube_upload(
            youtube_record,
            config,
            result,
            publication_identity_context=publication_identity_context,
            transport=transport,
            on_published=on_published,
            publish_run_id=publish_run_id,
        )
    ):
        pass
    elif (
        youtube_active
        and isinstance(youtube_record, Mapping)
        and (
            youtube_record.get("status") == "published"
            or youtube_record.get("outcome")
            in (
                UPLOADED,
                DRAFT_CREATED,
                PUBLISHED,
                PUBLICATION_UNKNOWN,
                MANUAL_HANDOFF_REQUIRED,
            )
        )
    ):
        video_id = youtube_record.get("video_id")
        if video_id is not None:
            result.youtube_id = str(video_id)
            result.youtube_url = f"https://www.youtube.com/watch?v={result.youtube_id}"
        logger.info("YouTube upload skipped for job_id=%s: already published", job_id)
        result.provider_outcomes["youtube"] = str(youtube_record.get("outcome") or DRAFT_CREATED)
        result.provider_records["youtube"] = _record_from_snapshot(
            youtube_record,
            provider="youtube",
            provider_id_field="video_id",
        )
    elif youtube_active:
        identity_tag = youtube_identity_tag(publication_identity_context)
        upload_identity = {"identity_tag": identity_tag} if identity_tag else {}
        try:
            video_id, video_url = upload_to_youtube(
                video_path,
                title,
                description,
                config,
                tags=tags,
                transport=transport,
                raise_on_failure=config.youtube_required,
                **upload_identity,
            )
            result.youtube_id = video_id
            result.youtube_url = video_url
            if not video_id:
                result.errors.append("YouTube upload failed after retries")
                if config.youtube_required:
                    youtube_required_failure = YouTubeDeliveryError(
                        "Required YouTube upload failed after retries",
                        code="youtube_upload_failed",
                        stage="upload",
                        retryable=False,
                    )
            else:
                result.provider_outcomes["youtube"] = DRAFT_CREATED
                result.provider_records["youtube"] = {
                    "provider": "youtube",
                    "outcome": DRAFT_CREATED,
                    "status": "unlisted" if config.youtube_privacy == "unlisted" else "private",
                    "provider_id": video_id,
                    "native_state": config.youtube_privacy,
                    "transport_status": "accepted",
                    "verification": "none",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "evidence_source": "youtube_upload_response",
                    "last_error_code": None,
                    "retry_blocked": True,
                }
                if on_published is not None and not config.dry_run:
                    on_published(
                        "youtube",
                        {
                            "status": "published",
                            "provider_status": result.provider_records["youtube"]["status"],
                            "outcome": DRAFT_CREATED,
                            "provider": "youtube",
                            "provider_id": video_id,
                            "native_state": config.youtube_privacy,
                            "transport_status": "accepted",
                            "verification": "none",
                            "evidence_source": "youtube_upload_response",
                            "retry_blocked": True,
                            "video_id": video_id,
                            "publish_run_id": publish_run_id,
                            "at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
        except YouTubeDeliveryError as exc:
            result.errors.append(str(exc))
            if config.youtube_required:
                youtube_required_failure = exc
            logger.error(
                "YouTube distribution failed stage=%s code=%s retryable=%s",
                exc.stage,
                exc.code,
                exc.retryable,
            )
        except Exception as exc:
            result.errors.append(f"YouTube upload error: {exc}")
            logger.error("YouTube distribution failed: %s", exc)
            if config.youtube_required:
                youtube_required_failure = YouTubeDeliveryError(
                    "Required YouTube upload failed",
                    code="youtube_upload_exception",
                    stage="upload",
                    retryable=False,
                )

    # Reconcile playlist membership independently from upload state. The playlist
    # API is idempotent, so a retry can repair an upload that was persisted before
    # its playlist insertion completed.
    if (
        result.youtube_id is not None
        and not config.dry_run
        and _resolve_playlist_id(config, locale)
    ):
        try:
            playlist_http = transport or _DefaultTransport()
            playlist_token = _get_youtube_access_token(config, playlist_http)
            playlist_result = _add_to_show_playlist(
                config,
                locale,
                result.youtube_id,
                playlist_token,
                transport=transport,
            )
            result.youtube_playlist_id = playlist_result.playlist_id
            result.youtube_playlist_succeeded = playlist_result.succeeded
        except Exception as exc:
            logger.warning("Playlist add skipped for %s: %s", result.youtube_id, exc)

    if config.youtube_required and result.youtube_id is None:
        result.youtube_required_failed = True
        if youtube_required_failure is None:
            if not youtube_active:
                # Required delivery is configured but YouTube is not active for
                # this run (disabled outright, or the language is excluded from
                # VIDEO_YOUTUBE_LANGUAGES). Treat it as a terminal
                # misconfiguration so the job cannot silently complete without
                # the mandated YouTube upload.
                reason = (
                    "VIDEO_YOUTUBE_REQUIRED=true but VIDEO_YOUTUBE_ENABLED is not true"
                    if not config.youtube_enabled
                    else (
                        "VIDEO_YOUTUBE_REQUIRED=true but YouTube is not active for "
                        f"language={language} (excluded by VIDEO_YOUTUBE_LANGUAGES)"
                    )
                )
                logger.error(
                    "required YouTube delivery misconfigured job_id=%s: %s", job_id, reason
                )
                youtube_required_failure = YouTubeDeliveryError(
                    reason,
                    code="youtube_required_but_disabled",
                    stage="config",
                    retryable=False,
                )
            else:
                youtube_required_failure = YouTubeDeliveryError(
                    "Required YouTube upload failed",
                    code="youtube_upload_failed",
                    stage="upload",
                    retryable=False,
                )
        result.youtube_failure_retryable = youtube_required_failure.retryable
        result.youtube_failure_code = youtube_required_failure.code
        result.youtube_failure_stage = youtube_required_failure.stage
        result.youtube_failure_http_status = youtube_required_failure.http_status
        result.youtube_oauth_error = youtube_required_failure.oauth_error
        result.youtube_oauth_error_subtype = youtube_required_failure.oauth_error_subtype

    # 3. Update Spotify RSS
    if config.spotify_rss_enabled:
        rss_record = prior_published.get("spotify_rss")
        rss_outcome = (
            str(rss_record.get("outcome") or PUBLISHED) if isinstance(rss_record, Mapping) else None
        )
        if isinstance(rss_record, Mapping) and (
            rss_record.get("status") == "published"
            or rss_outcome
            in (
                UPLOADED,
                DRAFT_CREATED,
                PUBLISHED,
                PUBLICATION_UNKNOWN,
                MANUAL_HANDOFF_REQUIRED,
            )
        ):
            result.spotify_rss_updated = rss_outcome == PUBLISHED
            result.provider_outcomes["spotify_rss"] = rss_outcome
            result.provider_records["spotify_rss"] = _record_from_snapshot(
                rss_record,
                provider="spotify_rss",
                provider_id_field="provider_id",
            )
            logger.info(
                "Spotify RSS update skipped for job_id=%s: prior outcome=%s",
                job_id,
                rss_outcome,
            )
        else:
            # blob_path is now a full URL returned from storage.upload(); prefer it over YouTube URL
            enclosure_url = blob_path or result.youtube_url or ""

            if not enclosure_url:
                logger.warning("No enclosure URL available for Spotify RSS — skipping")
                result.errors.append("Spotify RSS skipped: no enclosure URL")
                result.provider_outcomes["spotify_rss"] = MANUAL_HANDOFF_REQUIRED
                result.provider_records["spotify_rss"] = {
                    "provider": "spotify_rss",
                    "outcome": MANUAL_HANDOFF_REQUIRED,
                    "status": "gated",
                    "provider_id": config.spotify_rss_feed_path or None,
                    "native_state": None,
                    "transport_status": "not_attempted",
                    "verification": "none",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "evidence_source": "distribution_precondition",
                    "last_error_code": "missing_enclosure_url",
                    "retry_blocked": False,
                }
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
                if rss_ok:
                    result.provider_outcomes["spotify_rss"] = PUBLISHED
                    result.provider_records["spotify_rss"] = {
                        "provider": "spotify_rss",
                        "outcome": PUBLISHED,
                        "status": "pending",
                        "provider_id": config.spotify_rss_feed_path or None,
                        "native_state": "feed_updated",
                        "transport_status": "accepted",
                        "verification": "none",
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                        "evidence_source": "rss_storage_update",
                        "last_error_code": None,
                        "retry_blocked": True,
                    }
                else:
                    result.provider_outcomes["spotify_rss"] = PUBLICATION_UNKNOWN
                    result.provider_records["spotify_rss"] = {
                        "provider": "spotify_rss",
                        "outcome": PUBLICATION_UNKNOWN,
                        "status": "unknown",
                        "provider_id": config.spotify_rss_feed_path or None,
                        "native_state": None,
                        "transport_status": "failed",
                        "verification": "none",
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                        "evidence_source": "rss_update_result",
                        "last_error_code": "spotify_rss_update_failed",
                        "retry_blocked": True,
                    }
                if rss_ok and on_published is not None and not config.dry_run:
                    on_published(
                        "spotify_rss",
                        {
                            "status": "published",
                            "provider_status": "pending",
                            "outcome": PUBLISHED,
                            "provider": "spotify_rss",
                            "provider_id": config.spotify_rss_feed_path or None,
                            "native_state": "feed_updated",
                            "transport_status": "accepted",
                            "verification": "none",
                            "evidence_source": "rss_storage_update",
                            "retry_blocked": True,
                            "publish_run_id": publish_run_id,
                            "at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                if not rss_ok:
                    result.errors.append("Spotify RSS update failed")

    # 4. Publish MP4 as a NEW separate Spotify episode draft (#340)
    if config.spotify_upload_enabled:
        spotify_upload_record = prior_published.get("spotify_upload")
        if isinstance(spotify_upload_record, Mapping) and (
            spotify_upload_record.get("status") == "published"
            or spotify_upload_record.get("outcome")
            in (
                UPLOADED,
                DRAFT_CREATED,
                PUBLISHED,
                PUBLICATION_UNKNOWN,
                MANUAL_HANDOFF_REQUIRED,
            )
        ):
            spotify_upload_outcome = str(spotify_upload_record.get("outcome") or DRAFT_CREATED)
            result.spotify_upload_updated = spotify_upload_outcome not in (
                UPLOADED,
                PUBLICATION_UNKNOWN,
                MANUAL_HANDOFF_REQUIRED,
            )
            logger.info("Spotify video upload skipped for job_id=%s: already published", job_id)
            result.provider_outcomes["spotify_upload"] = spotify_upload_outcome
            result.provider_records["spotify_video"] = _record_from_snapshot(
                spotify_upload_record,
                provider="spotify_video",
                provider_id_field="episode_id",
            )
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
                job_id=job_id,
                publish_run_id=publish_run_id,
                publication_storage=publication_storage,
                publication_identity_context=publication_identity_context,
            )
            upload_outcome = None
            if isinstance(upload_result, tuple) and len(upload_result) == 4:
                upload_ok, spotify_episode_id, promote_state, upload_outcome = upload_result
            elif isinstance(upload_result, tuple) and len(upload_result) == 3:
                upload_ok, spotify_episode_id, promote_state = upload_result
            elif isinstance(upload_result, tuple):
                upload_ok, spotify_episode_id = upload_result
                promote_state = None
            else:
                upload_ok = upload_result
                spotify_episode_id = None
                promote_state = None
            result.spotify_upload_updated = upload_ok
            result.spotify_video_promote_terminal_state = promote_state
            result.spotify_video_is_published = (
                promote_state in ("published", "already_published")
                if promote_state is not None
                else None
            )
            if not upload_ok:
                spotify_outcome = upload_outcome or PUBLICATION_UNKNOWN
            elif promote_state is not None:
                spotify_outcome = (
                    outcome_from_spotify_terminal_state(promote_state) or PUBLICATION_UNKNOWN
                )
            else:
                spotify_outcome = DRAFT_CREATED
            result.provider_outcomes["spotify_upload"] = spotify_outcome
            result.provider_records["spotify_video"] = {
                "provider": "spotify_video",
                "outcome": spotify_outcome,
                "status": (
                    "pending"
                    if spotify_outcome == PUBLISHED
                    else "draft"
                    if spotify_outcome == DRAFT_CREATED
                    else "gated"
                    if spotify_outcome == MANUAL_HANDOFF_REQUIRED
                    else "unknown"
                ),
                "provider_id": (
                    str(spotify_episode_id) if spotify_episode_id is not None else None
                ),
                "native_state": promote_state,
                "transport_status": "accepted" if upload_ok else "failed",
                "verification": ("provider_readback" if spotify_outcome == PUBLISHED else "none"),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "evidence_source": (
                    "spotify_episode_readback" if promote_state is not None else "upload_result"
                ),
                "last_error_code": (
                    None
                    if spotify_outcome in (DRAFT_CREATED, PUBLISHED)
                    else str(promote_state or "spotify_upload_failed")
                ),
                "retry_blocked": spotify_outcome
                in (DRAFT_CREATED, PUBLISHED, PUBLICATION_UNKNOWN, MANUAL_HANDOFF_REQUIRED),
            }
            if upload_ok and on_published is not None and not config.dry_run:
                on_published(
                    "spotify_upload",
                    {
                        **result.provider_records["spotify_video"],
                        "status": "published",
                        "provider_status": result.provider_records["spotify_video"]["status"],
                        "outcome": spotify_outcome,
                        "episode_id": spotify_episode_id,
                        "publish_run_id": publish_run_id,
                        "promote_terminal_state": promote_state,
                        "is_published": result.spotify_video_is_published,
                        "at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            if not upload_ok:
                result.errors.append("Spotify video upload failed")
            elif promote_state is not None and promote_state not in (
                "published",
                "already_published",
                "draft_gate_denied",
            ):
                result.errors.append(f"Spotify video promote: {promote_state}")
                logger.warning(
                    "Spotify video upload succeeded but promotion to live failed "
                    "(job_id=%s promote_state=%s); operator action required",
                    job_id,
                    promote_state,
                )

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
            (
                result.youtube_id is not None
                and result.provider_outcomes.get("youtube")
                not in (UPLOADED, PUBLICATION_UNKNOWN, MANUAL_HANDOFF_REQUIRED)
            )
            if youtube_active
            else False,
            result.spotify_rss_updated if config.spotify_rss_enabled else False,
            result.spotify_upload_updated if config.spotify_upload_enabled else False,
            result.blob_path is not None if config.blob_archive_enabled else False,
        ]
    )
    spotify_promote_failed = (
        config.spotify_upload_enabled
        and result.spotify_video_promote_terminal_state is not None
        and result.spotify_video_promote_terminal_state
        not in ("published", "already_published", "draft_gate_denied")
    )
    expected_public_providers = [
        *(["youtube"] if youtube_active else []),
        *(["spotify_rss"] if config.spotify_rss_enabled else []),
        *(["spotify_video"] if config.spotify_upload_enabled else []),
    ]
    public_records = [
        result.provider_records.get(key, {"status": "unknown"}) for key in expected_public_providers
    ]
    if not public_records:
        result.public_delivery_status = "not_requested"
    elif all(
        record.get("status") == "public"
        and record.get("outcome") == PUBLISHED
        and record.get("verification") == "external_verified"
        for record in public_records
    ):
        result.public_delivery_status = "completed"
    elif any(
        record.get("status") == "public"
        and record.get("outcome") == PUBLISHED
        and record.get("verification") == "external_verified"
        for record in public_records
    ):
        result.public_delivery_status = "partial"
    elif any(record.get("status") == "pending" for record in public_records):
        result.public_delivery_status = "pending"
    elif public_records:
        result.public_delivery_status = "failed"

    if result.youtube_required_failed:
        result.status = "failed"
    elif targets_succeeded == 0 and targets_attempted > 0:
        result.status = "failed"
    elif targets_succeeded < targets_attempted or spotify_promote_failed:
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
