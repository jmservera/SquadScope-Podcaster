"""YouTube metadata + thumbnail generation for video episodes (#445).

Produces the YouTube ``videos.insert`` request body (``snippet`` + ``status``)
from the **same** shared title/description source already used for Spotify
(``podcaster.video.job_runner._build_video_description``), plus a thumbnail
image extracted from the composed MP4 and uploaded via the YouTube Data API
``thumbnails.set`` endpoint.

The module is intentionally self-contained and side-effect free at import time
so it is unit-testable in CI. It reuses the ``HttpTransport`` protocol from
``podcaster.video.distribution`` for the thumbnail upload, which lets tests
inject a fake transport. Wiring this into ``distribute_video()`` is handled by
the integration task (#444); this module only *produces* the metadata object and
a thumbnail that are *attachable* to an upload.

Locale awareness (#27 / multilanguage): ``build_youtube_metadata`` accepts a
``locale`` (``en`` / ``es`` / ``fr``) and emits a localized show label
(``Claracle Weekly`` / ``Claracle Semanal`` / ``Claracle Hebdo``) plus the
``defaultLanguage`` / ``defaultAudioLanguage`` snippet fields, so es/fr shows
publish native-language metadata rather than English.

Security: no credentials are logged. The access token is only placed in the
``Authorization`` header for the upload request.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

#: YouTube category for "Science & Technology".
YOUTUBE_CATEGORY_SCIENCE_TECH = "28"

#: Resumable/simple media upload endpoint for setting a video thumbnail.
THUMBNAIL_SET_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"

#: YouTube hard limits (chars). Title/description are truncated to fit.
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 5000

#: YouTube rejects the aggregate ``tags`` payload above ~500 characters.
MAX_TAGS_TOTAL_CHARS = 450

#: YouTube custom thumbnails must be <= 2 MB.
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024

#: Recommended thumbnail dimensions (16:9).
THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

_DEFAULT_TAGS = ["podcast", "tech", "open-source", "Claracle"]

#: Localized show label per language. ``Claracle Weekly`` and its translations.
_SHOW_LABEL_BY_LOCALE = {
    "en": "Claracle Weekly",
    "es": "Claracle Semanal",
    "fr": "Claracle Hebdo",
}

#: ISO-639-1 base codes used for the snippet language fields. YouTube accepts
#: BCP-47; plain language subtags are the safest interoperable choice.
_LANGUAGE_BY_LOCALE = {"en": "en", "es": "es", "fr": "fr"}

#: A short locale tag added to the YouTube tag list for non-English shows so the
#: video is discoverable in-language.
_LOCALE_TAG = {"es": "español", "fr": "français"}

_DEFAULT_LOCALE = "en"


def _normalize_locale(locale: str | None) -> str:
    """Reduce an arbitrary locale string to a supported base code (en/es/fr)."""
    if not locale:
        return _DEFAULT_LOCALE
    base = locale.strip().lower().replace("_", "-").split("-", 1)[0]
    return base if base in _SHOW_LABEL_BY_LOCALE else _DEFAULT_LOCALE


# --- Metadata ----------------------------------------------------------------


@dataclass(frozen=True)
class YouTubeMetadata:
    """Structured YouTube metadata for a single episode.

    ``to_request_body`` renders the ``videos.insert`` body (snippet + status).
    """

    title: str
    description: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    category_id: str = YOUTUBE_CATEGORY_SCIENCE_TECH
    privacy_status: str = "unlisted"
    default_language: str = "en"
    default_audio_language: str = "en"
    made_for_kids: bool = False

    def to_snippet(self) -> dict[str, object]:
        """Render the ``snippet`` part of the ``videos.insert`` body."""
        return {
            "title": self.title[:MAX_TITLE_CHARS],
            "description": self.description[:MAX_DESCRIPTION_CHARS],
            "tags": list(self.tags),
            "categoryId": self.category_id,
            "defaultLanguage": self.default_language,
            "defaultAudioLanguage": self.default_audio_language,
        }

    def to_status(self) -> dict[str, object]:
        """Render the ``status`` part. Defaults to an unlisted, not-for-kids draft."""
        return {
            "privacyStatus": self.privacy_status or "unlisted",
            "selfDeclaredMadeForKids": self.made_for_kids,
        }

    def to_request_body(self) -> dict[str, object]:
        """Render the full ``videos.insert`` request body (snippet + status)."""
        return {"snippet": self.to_snippet(), "status": self.to_status()}


def _clamp_tags(tags: list[str]) -> list[str]:
    """Drop trailing tags so the comma-joined payload stays within YouTube's cap."""
    clamped: list[str] = []
    total = 0
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        # +1 accounts for the separator YouTube counts between tags.
        cost = len(tag) + 1
        if total + cost > MAX_TAGS_TOTAL_CHARS:
            break
        clamped.append(tag)
        total += cost
    return clamped


def _format_title(raw_title: str, *, show_label: str, week: int | None) -> str:
    """Compose ``"<show_label> — W<week>: <raw_title>"`` (locale-aware label).

    If a week is given and the raw title does not already carry the show label,
    the label/week prefix is prepended. The result is truncated to YouTube's
    100-character limit.
    """
    topic = (raw_title or "").strip()
    if topic.startswith(show_label):
        return topic[:MAX_TITLE_CHARS]
    if week is not None:
        prefix = f"{show_label} — W{week:02d}: "
    else:
        prefix = f"{show_label} — "
    return (prefix + topic)[:MAX_TITLE_CHARS]


def build_youtube_metadata(
    title: str,
    description: str,
    *,
    week: int | None = None,
    locale: str | None = None,
    tags: list[str] | None = None,
    category_id: str = YOUTUBE_CATEGORY_SCIENCE_TECH,
    privacy_status: str = "unlisted",
    show_label: str | None = None,
    made_for_kids: bool = False,
) -> YouTubeMetadata:
    """Build :class:`YouTubeMetadata` from the shared title/description source.

    Args:
        title: Episode topic / article title (the same value passed to Spotify).
        description: Episode show-notes description (shared with Spotify).
        week: ISO week number used in the title (``W26``). Optional.
        locale: ``en`` / ``es`` / ``fr`` (case/region-insensitive). Drives the
            localized show label and the snippet language fields.
        tags: Explicit tags. When omitted a sensible default set is used; a
            locale tag is appended for es/fr shows.
        category_id: YouTube category. Defaults to Science & Technology (28).
        privacy_status: ``unlisted`` (default), ``private`` or ``public``.
        show_label: Override the localized show label (e.g. a per-show name).
        made_for_kids: ``selfDeclaredMadeForKids`` flag.

    Returns:
        A :class:`YouTubeMetadata` ready to render into a ``videos.insert`` body.
    """
    norm_locale = _normalize_locale(locale)
    label = show_label or _SHOW_LABEL_BY_LOCALE[norm_locale]
    language = _LANGUAGE_BY_LOCALE[norm_locale]

    final_title = _format_title(title, show_label=label, week=week)

    if tags is None:
        resolved_tags = list(_DEFAULT_TAGS)
        locale_tag = _LOCALE_TAG.get(norm_locale)
        if locale_tag:
            resolved_tags.append(locale_tag)
    else:
        resolved_tags = list(tags)
    resolved_tags = _clamp_tags(resolved_tags)

    return YouTubeMetadata(
        title=final_title,
        description=(description or "").strip(),
        tags=tuple(resolved_tags),
        category_id=category_id,
        privacy_status=privacy_status or "unlisted",
        default_language=language,
        default_audio_language=language,
        made_for_kids=made_for_kids,
    )


# --- Thumbnail generation ----------------------------------------------------


_CONTENT_TYPE_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def thumbnail_content_type(image_path: Path) -> str:
    """Map a thumbnail file suffix to its MIME type (defaults to JPEG)."""
    return _CONTENT_TYPE_BY_SUFFIX.get(image_path.suffix.lower(), "image/jpeg")


def build_thumbnail_command(
    video_path: Path,
    output_path: Path,
    *,
    timestamp_seconds: float = 3.0,
    width: int = THUMBNAIL_WIDTH,
    height: int = THUMBNAIL_HEIGHT,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build the ffmpeg command that grabs a single 16:9 frame for the thumbnail.

    The frame is scaled to cover ``width``x``height`` and centre-cropped so the
    output always has the recommended YouTube thumbnail dimensions regardless of
    the source aspect ratio.
    """
    scale_crop = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )
    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(timestamp_seconds, 0):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        scale_crop,
        "-q:v",
        "2",
        str(output_path),
    ]


def extract_thumbnail(
    video_path: Path,
    output_path: Path,
    *,
    timestamp_seconds: float = 3.0,
    width: int = THUMBNAIL_WIDTH,
    height: int = THUMBNAIL_HEIGHT,
    ffmpeg_bin: str | None = None,
) -> Path:
    """Extract a single frame from ``video_path`` as a YouTube-ready thumbnail.

    Returns the path to the written image. Raises ``FileNotFoundError`` when the
    source video is missing, ``RuntimeError`` when ffmpeg is unavailable or the
    extraction fails, and ``ValueError`` when the produced image exceeds the 2 MB
    YouTube limit (callers can re-encode at a lower quality if needed).
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    resolved_bin = ffmpeg_bin or shutil.which("ffmpeg")
    if not resolved_bin:
        raise RuntimeError("ffmpeg is not available; cannot extract thumbnail")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_thumbnail_command(
        video_path,
        output_path,
        timestamp_seconds=timestamp_seconds,
        width=width,
        height=height,
        ffmpeg_bin=resolved_bin,
    )
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ffmpeg thumbnail extraction failed (exit {exc.returncode}): "
            f"{(exc.stderr or '').strip()[:500]}"
        ) from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("ffmpeg did not produce a thumbnail image")

    size = output_path.stat().st_size
    if size > THUMBNAIL_MAX_BYTES:
        raise ValueError(
            f"Thumbnail too large ({size} bytes > {THUMBNAIL_MAX_BYTES}); "
            "re-encode at lower quality"
        )
    logger.info("Thumbnail extracted: %s (%d bytes)", output_path.name, size)
    return output_path


# --- Thumbnail upload --------------------------------------------------------


def upload_thumbnail(
    video_id: str,
    image_path: Path,
    access_token: str,
    *,
    transport: object | None = None,
) -> bool:
    """Attach ``image_path`` to ``video_id`` via the YouTube ``thumbnails.set`` API.

    Returns ``True`` on success (HTTP 200), ``False`` otherwise. Never raises on
    an HTTP error so a thumbnail failure cannot abort the rest of distribution
    (the video itself is already uploaded). The access token is only sent in the
    ``Authorization`` header and never logged.
    """
    if not video_id:
        raise ValueError("video_id is required")
    if not image_path.exists():
        raise FileNotFoundError(f"Thumbnail not found: {image_path}")

    size = image_path.stat().st_size
    if size == 0:
        raise ValueError("Thumbnail image is empty")
    if size > THUMBNAIL_MAX_BYTES:
        raise ValueError(
            f"Thumbnail too large ({size} bytes > {THUMBNAIL_MAX_BYTES})"
        )

    http = transport if transport is not None else _default_transport()

    params = urlencode({"videoId": video_id, "uploadType": "media"})
    url = f"{THUMBNAIL_SET_URL}?{params}"
    image_bytes = image_path.read_bytes()

    try:
        status, _body = http.request(
            url,
            method="POST",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": thumbnail_content_type(image_path),
                "Content-Length": str(len(image_bytes)),
            },
            data=image_bytes,
        )
    except Exception as exc:  # pragma: no cover - network/transport failure
        logger.warning("Thumbnail upload error for video %s: %s", video_id, exc)
        return False

    if status == 200:
        logger.info("Thumbnail set for video %s", video_id)
        return True
    logger.warning(
        "Thumbnail upload failed for video %s: HTTP %s", video_id, status
    )
    return False


def generate_and_set_thumbnail(
    video_path: Path,
    video_id: str,
    access_token: str,
    output_path: Path,
    *,
    timestamp_seconds: float = 3.0,
    transport: object | None = None,
    ffmpeg_bin: str | None = None,
) -> bool:
    """Extract a frame from ``video_path`` and attach it to ``video_id``.

    Convenience wrapper combining :func:`extract_thumbnail` and
    :func:`upload_thumbnail`. Returns ``True`` only when both steps succeed.
    Extraction failures are caught and logged so distribution continues.
    """
    try:
        extract_thumbnail(
            video_path,
            output_path,
            timestamp_seconds=timestamp_seconds,
            ffmpeg_bin=ffmpeg_bin,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        logger.warning("Thumbnail generation skipped for %s: %s", video_id, exc)
        return False
    return upload_thumbnail(
        video_id, output_path, access_token, transport=transport
    )


def _default_transport() -> object:
    """Lazily build the default urllib transport (reused from distribution)."""
    from podcaster.video.distribution import _DefaultTransport

    return _DefaultTransport()
