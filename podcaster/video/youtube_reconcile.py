"""Identity-bound YouTube upload reconciliation after an ambiguous create (#678).

The YouTube Data API has no idempotency key for ``videos.insert``. To prove
whether a resumable upload completed after transport loss or a worker crash,
every upload is stamped with a deterministic, non-sensitive identity tag derived
from the canonical publication identity (accepted job, publish run, week and
article SHA-256). The tag is declared in the durable ``upload_intent`` evidence
*before* the mutation, so a redelivered job can later read back the owner's
uploads and bind the exact artifact.

Reconciliation is read-only and fail-closed: only exactly one uploaded,
still-private/unlisted video carrying the exact tag is bound. Zero, multiple,
or contradictory candidates keep the job ``publication_unknown`` and never
authorize a second upload. No title or newest-result guessing is performed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

IDENTITY_SCHEME = "youtube-video-v1"
IDENTITY_TAG_PREFIX = "sqpub-"
IDENTITY_DETAIL_KEY = "youtube_identity_tag"

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

#: Uploads-playlist pages scanned (50 items each). Bounded so a reconcile costs
#: at most ``1 + 2 * MAX_PAGES`` quota units (read operations cost 1 unit).
MAX_PAGES = 4
#: Allowed clock skew between the worker's intent timestamp and YouTube's
#: ``publishedAt`` when deciding that the scan window is exhaustive.
CLOCK_SKEW = timedelta(minutes=15)
_PAGE_SIZE = 50
_MAX_TAGS_CHARS = 500

MATCHED = "matched"
ABSENT = "absent"
AMBIGUOUS = "ambiguous"
CONTRADICTORY = "contradictory"
ERROR = "error"

_SAFE_PRIVACY = ("private", "unlisted")
_COMPLETE_UPLOAD = ("uploaded", "processed")


@dataclass(frozen=True)
class YouTubeReconcileResult:
    status: str
    code: str
    video_id: str | None = None
    privacy_status: str | None = None

    @property
    def matched(self) -> bool:
        return self.status == MATCHED and bool(self.video_id)


def youtube_identity_tag(identity: Any) -> str | None:
    """Return the deterministic identity tag for a canonical publication identity."""
    fields = (
        getattr(identity, "accepted_job_id", None),
        getattr(identity, "publish_run_id", None),
        getattr(identity, "week", None),
        getattr(identity, "article_sha256", None),
    )
    if not all(isinstance(value, str) and value for value in fields):
        return None
    material = "|".join((IDENTITY_SCHEME, *fields)).encode("utf-8")
    return IDENTITY_TAG_PREFIX + hashlib.sha256(material).hexdigest()[:32]


def is_identity_tag(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(IDENTITY_TAG_PREFIX):
        return False
    suffix = value[len(IDENTITY_TAG_PREFIX) :]
    return len(suffix) == 32 and all(ch in "0123456789abcdef" for ch in suffix)


def tags_with_identity(tags: list[str], identity_tag: str | None) -> list[str]:
    """Append ``identity_tag`` while keeping YouTube's 500-character tag budget."""
    if not identity_tag:
        return list(tags)
    base = [tag for tag in tags if tag != identity_tag]

    def _size(values: list[str]) -> int:
        return sum(len(v) for v in values) + max(len(values) - 1, 0)

    while base and _size([*base, identity_tag]) > _MAX_TAGS_CHARS:
        base.pop()
    return [*base, identity_tag]


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _get_json(transport: Any, url: str, access_token: str) -> tuple[int, Any]:
    status, body = transport.request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if status != 200:
        return status, None
    try:
        return status, json.loads(body)
    except (TypeError, ValueError):
        return status, None


def reconcile_youtube_upload(
    identity_tag: str,
    access_token: str,
    transport: Any,
    *,
    not_before: datetime | None = None,
    max_pages: int = MAX_PAGES,
) -> YouTubeReconcileResult:
    """Read back the owner's recent uploads and bind the exact tagged video.

    The uploads playlist is scanned newest-first (ordering is verified). The
    scan is exhaustive only when it reaches the end of the playlist or an item
    older than ``not_before - CLOCK_SKEW`` (the durable intent time, before
    which the tagged upload cannot exist). Hitting the page bound first is
    contradictory: an unseen duplicate could exist, so nothing is bound.
    """
    if not is_identity_tag(identity_tag):
        return YouTubeReconcileResult(ERROR, "youtube_reconcile_invalid_identity")

    status, channels = _get_json(
        transport,
        f"{CHANNELS_URL}?{urlencode({'part': 'contentDetails', 'mine': 'true'})}",
        access_token,
    )
    items = channels.get("items") if isinstance(channels, dict) else None
    if status != 200 or not isinstance(items, list):
        return YouTubeReconcileResult(ERROR, f"youtube_reconcile_channels_http_{status}")
    if len(items) != 1:
        return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_channel_not_unique")
    uploads = (
        items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if isinstance(items[0], dict)
        else None
    )
    if not isinstance(uploads, str) or not uploads:
        return YouTubeReconcileResult(ERROR, "youtube_reconcile_uploads_playlist_missing")

    cutoff = not_before - CLOCK_SKEW if not_before is not None else None
    seen: set[str] = set()
    video_ids: list[str] = []
    previous_at: datetime | None = None
    exhausted = False
    page_token: str | None = None
    for _ in range(max(1, max_pages)):
        params = {
            "part": "snippet,contentDetails",
            "playlistId": uploads,
            "maxResults": str(_PAGE_SIZE),
        }
        if page_token:
            params["pageToken"] = page_token
        status, page = _get_json(
            transport, f"{PLAYLIST_ITEMS_URL}?{urlencode(params)}", access_token
        )
        page_items = page.get("items") if isinstance(page, dict) else None
        if status != 200 or not isinstance(page_items, list):
            return YouTubeReconcileResult(ERROR, f"youtube_reconcile_uploads_http_{status}")
        for item in page_items:
            if not isinstance(item, dict):
                return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_malformed_item")
            video_id = item.get("contentDetails", {}).get("videoId")
            added_at = parse_timestamp(item.get("snippet", {}).get("publishedAt"))
            if not isinstance(video_id, str) or not video_id or added_at is None:
                return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_malformed_item")
            if video_id in seen:
                return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_repeated_item")
            if previous_at is not None and added_at > previous_at:
                return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_unordered_uploads")
            seen.add(video_id)
            previous_at = added_at
            if cutoff is not None and added_at < cutoff:
                # Keep validating the rest of this page before stopping.
                exhausted = True
                continue
            video_ids.append(video_id)
        if exhausted:
            break
        next_token = page.get("nextPageToken")
        if next_token is None or next_token == "":
            exhausted = True
            break
        if not isinstance(next_token, str):
            return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_malformed_page_token")
        page_token = next_token
    if not exhausted:
        return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_window_not_exhausted")

    candidates: list[dict[str, Any]] = []
    for start in range(0, len(video_ids), _PAGE_SIZE):
        batch = video_ids[start : start + _PAGE_SIZE]
        query = urlencode({"part": "snippet,status", "id": ",".join(batch)})
        status, videos = _get_json(transport, f"{VIDEOS_URL}?{query}", access_token)
        video_items = videos.get("items") if isinstance(videos, dict) else None
        if status != 200 or not isinstance(video_items, list):
            return YouTubeReconcileResult(ERROR, f"youtube_reconcile_videos_http_{status}")
        # The readback must cover exactly the requested IDs: an omitted ID could
        # be another tagged candidate, so incomplete readback stays fail-closed.
        returned: set[str] = set()
        for video in video_items:
            video_id = video.get("id") if isinstance(video, dict) else None
            if not isinstance(video_id, str) or video_id not in batch or video_id in returned:
                return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_malformed_video")
            returned.add(video_id)
        if returned != set(batch):
            return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_incomplete_readback")
        for video in video_items:
            snippet = video.get("snippet")
            tags = snippet.get("tags") if isinstance(snippet, dict) else None
            if isinstance(tags, list) and identity_tag in tags:
                candidates.append(video)

    if not candidates:
        return YouTubeReconcileResult(ABSENT, "youtube_reconcile_no_identity_match")
    if len(candidates) > 1:
        return YouTubeReconcileResult(AMBIGUOUS, "youtube_reconcile_multiple_identity_matches")

    video = candidates[0]
    video_id = video.get("id")
    video_status = video.get("status") if isinstance(video.get("status"), dict) else {}
    privacy = video_status.get("privacyStatus")
    upload_status = video_status.get("uploadStatus")
    if not isinstance(video_id, str) or video_id not in video_ids:
        return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_identity_id_mismatch")
    if privacy not in _SAFE_PRIVACY:
        return YouTubeReconcileResult(
            CONTRADICTORY, "youtube_reconcile_unexpected_privacy", privacy_status=privacy
        )
    if upload_status not in _COMPLETE_UPLOAD:
        return YouTubeReconcileResult(CONTRADICTORY, "youtube_reconcile_upload_incomplete")
    return YouTubeReconcileResult(
        MATCHED, "youtube_reconcile_identity_match", video_id=video_id, privacy_status=privacy
    )
