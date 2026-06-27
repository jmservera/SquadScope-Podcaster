"""YouTube resumable chunked upload (#442).

The existing :func:`podcaster.video.distribution.upload_to_youtube` performs a
single-request upload and explicitly refuses files larger than 128 MB. Finished
video podcasts are 200–300 MB, so this module implements the **resumable chunked
upload** flow of the YouTube Data API v3 ``videos.insert`` endpoint:

1. Initiate a resumable session (``uploadType=resumable``) and read the session
   URI from the ``Location`` header.
2. ``PUT`` the file in fixed-size chunks with a ``Content-Range`` header.
3. On ``308 Resume Incomplete`` continue from the byte offset the server
   acknowledges in its ``Range`` header.
4. On transient failures (5xx / 429 / network) re-query the server offset and
   resume from there instead of restarting the whole upload.
5. On ``200``/``201`` parse the JSON body and return the new video id.

The flow reuses the package's :class:`HttpTransport` abstraction (no Google SDK
dependency) so it is fully unit-testable with a fake transport. Chunks are read
from disk on demand, so a 300 MB upload never loads the whole file into memory.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from podcaster.video.distribution import (
    _MIN_VALID_MP4_BYTES,
    HttpTransport,
    VideoDistributionConfig,
    _DefaultTransport,
    _get_youtube_access_token,
)

logger = logging.getLogger(__name__)

_YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

#: Resumable chunk size. Must be a multiple of 256 KiB per the Google spec.
_CHUNK_GRANULARITY = 256 * 1024
RESUMABLE_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB (multiple of 256 KiB)

MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 5000

_DEFAULT_TAGS = ["podcast", "tech", "open-source"]
_TRANSIENT_STATUSES = {500, 502, 503, 504, 429}
_MAX_TRANSIENT_RETRIES = 5
_RETRY_BACKOFF_BASE = 2.0


@dataclass
class YouTubeUploadResult:
    """Outcome of a resumable upload."""

    status: str  # "completed" | "failed" | "dry_run" | "disabled"
    video_id: str | None = None
    video_url: str | None = None
    bytes_uploaded: int = 0
    error: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status in ("completed", "dry_run")


# --- metadata -----------------------------------------------------------------


def build_snippet(
    title: str,
    description: str,
    *,
    tags: list[str] | None = None,
    category_id: str = "28",
) -> dict[str, object]:
    """Build the ``snippet`` part (title/description/tags/categoryId)."""

    return {
        "title": (title or "")[:MAX_TITLE_CHARS],
        "description": (description or "")[:MAX_DESCRIPTION_CHARS],
        "tags": tags if tags is not None else list(_DEFAULT_TAGS),
        "categoryId": category_id,
    }


def build_status(
    privacy_status: str = "unlisted",
    *,
    made_for_kids: bool = False,
) -> dict[str, object]:
    """Build the ``status`` part. Defaults to an unlisted, not-for-kids draft."""

    return {
        "privacyStatus": privacy_status or "unlisted",
        "selfDeclaredMadeForKids": made_for_kids,
    }


def build_video_metadata(
    title: str,
    description: str,
    *,
    tags: list[str] | None = None,
    category_id: str = "28",
    privacy_status: str = "unlisted",
    made_for_kids: bool = False,
) -> dict[str, object]:
    """Build the full ``videos.insert`` request body (snippet + status)."""

    return {
        "snippet": build_snippet(title, description, tags=tags, category_id=category_id),
        "status": build_status(privacy_status, made_for_kids=made_for_kids),
    }


# --- chunking helpers ---------------------------------------------------------


def align_chunk_size(size: int) -> int:
    """Round *size* down to a multiple of 256 KiB (minimum one granule)."""

    if size < _CHUNK_GRANULARITY:
        return _CHUNK_GRANULARITY
    return (size // _CHUNK_GRANULARITY) * _CHUNK_GRANULARITY


def parse_range_end(range_header: str | None) -> int | None:
    """Return the last acknowledged byte from a ``Range: bytes=0-N`` header."""

    if not range_header:
        return None
    try:
        # Format is ``bytes=0-262143`` (or sometimes ``0-262143``).
        tail = range_header.split("=", 1)[-1]
        return int(tail.split("-")[-1])
    except (ValueError, IndexError):
        return None


# --- resumable upload ---------------------------------------------------------


def initiate_resumable_session(
    http: HttpTransport,
    access_token: str,
    metadata: dict[str, object],
    *,
    file_size: int,
    content_type: str = "video/mp4",
) -> str:
    """Start a resumable session and return the session URI.

    Raises RuntimeError if the API does not return a session URI.
    """

    params = urlencode({"uploadType": "resumable", "part": "snippet,status"})
    init_url = f"{_YOUTUBE_UPLOAD_URL}?{params}"
    status, resp_headers, _ = http.request_with_headers(
        init_url,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": content_type,
        },
        data=json.dumps(metadata).encode("utf-8"),
    )
    if status not in (200, 308):
        raise RuntimeError(f"YouTube resumable init failed: HTTP {status}")

    session_uri = resp_headers.get("location")
    if not session_uri:
        raise RuntimeError("YouTube resumable init returned no session URI")
    return session_uri


def _query_resume_offset(
    http: HttpTransport,
    session_uri: str,
    access_token: str,
    total_size: int,
) -> tuple[int, str | None]:
    """Ask the server how many bytes it has. Returns (next_offset, completed_id).

    ``next_offset == total_size`` with a ``completed_id`` means the upload
    already finished server-side.
    """

    status, headers, body = http.request_with_headers(
        session_uri,
        method="PUT",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Length": "0",
            "Content-Range": f"bytes */{total_size}",
        },
        data=b"",
    )
    if status in (200, 201):
        video_id = ""
        try:
            video_id = json.loads(body).get("id", "")
        except (ValueError, AttributeError):
            pass
        return total_size, video_id or None
    if status == 308:
        end = parse_range_end(headers.get("range"))
        return (end + 1 if end is not None else 0), None
    raise RuntimeError(f"YouTube resume query failed: HTTP {status}")


def upload_chunked(
    http: HttpTransport,
    session_uri: str,
    access_token: str,
    video_path: Path,
    total_size: int,
    *,
    chunk_size: int = RESUMABLE_CHUNK_SIZE,
    content_type: str = "video/mp4",
    max_retries: int = _MAX_TRANSIENT_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> YouTubeUploadResult:
    """Upload a file in resumable chunks, resuming over transient failures."""

    chunk_size = align_chunk_size(chunk_size)
    start = 0
    transient_retries = 0

    with video_path.open("rb") as fh:
        while start < total_size:
            fh.seek(start)
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            end = start + len(chunk) - 1
            try:
                status, headers, body = http.request_with_headers(
                    session_uri,
                    method="PUT",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": content_type,
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{total_size}",
                    },
                    data=chunk,
                )
            except Exception as exc:  # noqa: BLE001 - network error → resume
                transient_retries += 1
                if transient_retries > max_retries:
                    return YouTubeUploadResult(
                        status="failed",
                        bytes_uploaded=start,
                        error=f"network error after {max_retries} retries: {exc}",
                    )
                sleep(_RETRY_BACKOFF_BASE ** (transient_retries - 1))
                start = _resume_after_failure(
                    http, session_uri, access_token, total_size, fallback=start
                )
                continue

            if status in (200, 201):
                return _finalize(body, total_size)
            if status == 308:
                end_ack = parse_range_end(headers.get("range"))
                if end_ack is not None:
                    start = end_ack + 1
                else:
                    # No Range header means the server's acknowledged offset is
                    # unknown (could be 0).  Query the real offset rather than
                    # blindly advancing past the chunk we just sent.
                    start = _resume_after_failure(
                        http, session_uri, access_token, total_size, fallback=start
                    )
                transient_retries = 0
                continue
            if status in _TRANSIENT_STATUSES:
                transient_retries += 1
                if transient_retries > max_retries:
                    return YouTubeUploadResult(
                        status="failed",
                        bytes_uploaded=start,
                        error=f"HTTP {status} after {max_retries} retries",
                    )
                sleep(_RETRY_BACKOFF_BASE ** (transient_retries - 1))
                start = _resume_after_failure(
                    http, session_uri, access_token, total_size, fallback=start
                )
                continue

            return YouTubeUploadResult(
                status="failed",
                bytes_uploaded=start,
                error=f"non-retryable HTTP {status}",
            )

    # Loop completed without a 200/201 — query the server for a final id.
    offset, completed_id = _query_resume_offset(http, session_uri, access_token, total_size)
    if completed_id:
        return _success_result(completed_id, total_size)
    return YouTubeUploadResult(
        status="failed",
        bytes_uploaded=offset,
        error="upload ended without a completion response",
    )


def _resume_after_failure(
    http: HttpTransport,
    session_uri: str,
    access_token: str,
    total_size: int,
    *,
    fallback: int,
) -> int:
    try:
        offset, _ = _query_resume_offset(http, session_uri, access_token, total_size)
        return offset
    except Exception as exc:  # noqa: BLE001 - keep retrying from last offset
        logger.warning("Resume-offset query failed, retrying from %d: %s", fallback, exc)
        return fallback


def _finalize(body: bytes, total_size: int) -> YouTubeUploadResult:
    try:
        video_id = json.loads(body).get("id", "")
    except (ValueError, AttributeError):
        video_id = ""
    if not video_id:
        return YouTubeUploadResult(
            status="failed",
            bytes_uploaded=total_size,
            error="upload completed but response had no video id",
        )
    return _success_result(video_id, total_size)


def _success_result(video_id: str, total_size: int) -> YouTubeUploadResult:
    url = f"https://youtube.com/watch?v={video_id}"
    logger.info("YouTube resumable upload succeeded: %s", url)
    return YouTubeUploadResult(
        status="completed",
        video_id=video_id,
        video_url=url,
        bytes_uploaded=total_size,
    )


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    config: VideoDistributionConfig,
    *,
    tags: list[str] | None = None,
    privacy_status: str | None = None,
    made_for_kids: bool = False,
    transport: HttpTransport | None = None,
    chunk_size: int = RESUMABLE_CHUNK_SIZE,
    sleep: Callable[[float], None] = time.sleep,
) -> YouTubeUploadResult:
    """Upload *video_path* to YouTube via resumable chunked upload.

    Metadata (title/description/tags) is supplied by the caller (the metadata
    task, #445). Privacy defaults to the configured value (unlisted) so uploads
    land as drafts, never public.
    """

    if not config.youtube_enabled:
        logger.info("YouTube upload disabled")
        return YouTubeUploadResult(status="disabled")

    if config.dry_run:
        logger.info("YouTube resumable upload dry-run: %s", title)
        return YouTubeUploadResult(
            status="dry_run",
            video_id="dry-run-id",
            video_url="https://youtube.com/watch?v=dry-run-id",
            details={"title": title, "privacy": privacy_status or config.youtube_privacy},
        )

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    file_size = video_path.stat().st_size
    if file_size < _MIN_VALID_MP4_BYTES:
        raise ValueError(f"Video file too small ({file_size} bytes), likely corrupt")

    http = transport or _DefaultTransport()
    access_token = _get_youtube_access_token(config, http)

    metadata = build_video_metadata(
        title,
        description,
        tags=tags,
        category_id=config.youtube_category_id,
        privacy_status=privacy_status or config.youtube_privacy,
        made_for_kids=made_for_kids,
    )

    try:
        session_uri = initiate_resumable_session(http, access_token, metadata, file_size=file_size)
    except RuntimeError as exc:
        logger.error("YouTube resumable init failed: %s", exc)
        return YouTubeUploadResult(status="failed", error=str(exc))

    return upload_chunked(
        http,
        session_uri,
        access_token,
        video_path,
        file_size,
        chunk_size=chunk_size,
        max_retries=_MAX_TRANSIENT_RETRIES,
        sleep=sleep,
    )
