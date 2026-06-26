"""Unlisted-draft -> manual publish workflow for YouTube episodes (#446).

New uploads are **never** immediately public. They land as ``unlisted`` (or
``private``) drafts (the upload module #442 / metadata #445 already default to
``unlisted``). This module owns the *second* leg of the lifecycle:

1. :func:`build_publishing_packet` records an **explicit review gate** — a
   serializable artifact describing the draft, where to review it, and whether a
   human has approved it. The packet starts ``approved=False``; nothing here can
   flip a video public until it is approved.
2. :func:`publish_video` calls the YouTube ``videos.update`` endpoint
   (``part=status``) to flip an approved draft to ``public`` *or* to schedule a
   future publish (``privacyStatus=private`` + ``publishAt`` RFC-3339).
3. :func:`approve_and_publish` is the gated convenience wrapper: it refuses to
   act unless the packet is approved, then publishes (immediately or scheduled).

The module is self-contained and side-effect free at import time so it is fully
unit-testable in CI. It reuses the ``HttpTransport`` protocol from
``podcaster.video.distribution`` for the API call, allowing a fake transport in
tests. Access tokens are only sent in the ``Authorization`` header, never logged.

Security: this is a *gate*, not a rubber stamp. Automation may set
``approved=True`` only after the documented review step (see
``docs/youtube-publish-workflow.md``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

#: ``videos.update`` endpoint. ``part=status`` updates only the status object.
VIDEOS_UPDATE_URL = "https://www.googleapis.com/youtube/v3/videos?part=status"

#: Allowed privacy states.
PRIVACY_PUBLIC = "public"
PRIVACY_UNLISTED = "unlisted"
PRIVACY_PRIVATE = "private"
VALID_PRIVACY = frozenset({PRIVACY_PUBLIC, PRIVACY_UNLISTED, PRIVACY_PRIVATE})

#: Draft privacy used for fresh uploads. Never ``public``.
DEFAULT_DRAFT_PRIVACY = PRIVACY_UNLISTED


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_rfc3339(value: datetime | str) -> str:
    """Render a datetime as RFC-3339 UTC (``...Z``), or pass a string through unchanged.

    When a ``str`` is provided it is returned as-is — callers are responsible
    for supplying a valid RFC-3339 / ISO-8601 string (e.g. ``"2025-01-01T00:00:00Z"``).
    """
    if isinstance(value, str):
        return value
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    # YouTube wants ISO-8601/RFC-3339 with a trailing Z, second precision is fine.
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Review gate -------------------------------------------------------------


@dataclass
class PublishingPacket:
    """An explicit human-review gate for a freshly uploaded draft.

    The packet is the serializable record that travels with an episode between
    "uploaded as draft" and "approved & public". It deliberately starts
    ``approved=False``; :func:`approve_and_publish` refuses to publish until a
    reviewer (or an automated approval step) sets it ``True``.
    """

    video_id: str
    video_url: str = ""
    title: str = ""
    locale: str = "en"
    draft_privacy: str = DEFAULT_DRAFT_PRIVACY
    review_url: str = ""
    review_notes: str = ""
    approved: bool = False
    approved_by: str = ""
    scheduled_publish_at: str = ""
    created_at: str = field(default_factory=lambda: _to_rfc3339(_utc_now()))

    def __post_init__(self) -> None:
        if not self.video_id:
            raise ValueError("video_id is required for a publishing packet")
        if self.draft_privacy == PRIVACY_PUBLIC:
            raise ValueError("draft_privacy must never be 'public' (#446 gate)")
        if self.draft_privacy not in VALID_PRIVACY:
            raise ValueError(f"invalid draft_privacy: {self.draft_privacy!r}")
        if not self.review_url and self.video_id:
            self.review_url = (
                f"https://studio.youtube.com/video/{self.video_id}/edit"
            )

    @property
    def is_scheduled(self) -> bool:
        return bool(self.scheduled_publish_at)

    @property
    def is_public_ready(self) -> bool:
        """True only when a reviewer has approved the draft for publishing."""
        return bool(self.approved)

    def approve(self, by: str = "") -> "PublishingPacket":
        """Mark the packet approved (the explicit review gate passing)."""
        self.approved = True
        self.approved_by = by
        logger.info(
            "Publishing packet approved for video %s by %s",
            self.video_id,
            by or "<unspecified>",
        )
        return self

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def build_publishing_packet(
    video_id: str,
    *,
    video_url: str = "",
    title: str = "",
    locale: str = "en",
    draft_privacy: str = DEFAULT_DRAFT_PRIVACY,
    review_notes: str = "",
    scheduled_publish_at: datetime | str | None = None,
) -> PublishingPacket:
    """Build a :class:`PublishingPacket` describing the review gate for a draft.

    Args:
        video_id: The uploaded YouTube video id (already a draft).
        video_url: Public watch URL (unlisted/private until published).
        title: Episode title, for the review packet.
        locale: ``en`` / ``es`` / ``fr`` — the show language.
        draft_privacy: Current draft privacy (``unlisted`` or ``private``,
            never ``public``).
        review_notes: Free-form notes for the reviewer.
        scheduled_publish_at: Optional desired publish time. When set,
            :func:`approve_and_publish` schedules instead of going public now.
    """
    scheduled = (
        _to_rfc3339(scheduled_publish_at)
        if scheduled_publish_at is not None
        else ""
    )
    return PublishingPacket(
        video_id=video_id,
        video_url=video_url,
        title=title,
        locale=locale,
        draft_privacy=draft_privacy,
        review_notes=review_notes,
        scheduled_publish_at=scheduled,
    )


# --- Status update (videos.update) -------------------------------------------


@dataclass(frozen=True)
class PublishResult:
    """Outcome of a publish / schedule attempt."""

    video_id: str
    succeeded: bool
    privacy_status: str = ""
    scheduled_publish_at: str = ""
    error: str = ""


def _build_status_body(
    video_id: str,
    *,
    privacy_status: str,
    publish_at: str = "",
    made_for_kids: bool = False,
) -> dict[str, object]:
    """Build the ``videos.update`` request body for the ``status`` part."""
    status: dict[str, object] = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": made_for_kids,
    }
    if publish_at:
        # A scheduled publish requires privacyStatus=private + publishAt.
        status["privacyStatus"] = PRIVACY_PRIVATE
        status["publishAt"] = publish_at
    return {"id": video_id, "status": status}


def publish_video(
    video_id: str,
    access_token: str,
    *,
    privacy_status: str = PRIVACY_PUBLIC,
    publish_at: datetime | str | None = None,
    made_for_kids: bool = False,
    transport: object | None = None,
) -> PublishResult:
    """Flip ``video_id`` to ``privacy_status`` (or schedule a future publish).

    When ``publish_at`` is provided the video is set ``private`` with a
    ``publishAt`` time; YouTube makes it public at that moment. Otherwise the
    video is updated to ``privacy_status`` (default ``public``).

    Never raises on an HTTP/transport error — returns a failed
    :class:`PublishResult` so a publish failure cannot abort a batch. The access
    token is only sent in the ``Authorization`` header and never logged.
    """
    if not video_id:
        raise ValueError("video_id is required")
    if privacy_status not in VALID_PRIVACY:
        raise ValueError(f"invalid privacy_status: {privacy_status!r}")

    scheduled = _to_rfc3339(publish_at) if publish_at is not None else ""
    body = _build_status_body(
        video_id,
        privacy_status=privacy_status,
        publish_at=scheduled,
        made_for_kids=made_for_kids,
    )
    payload = json.dumps(body).encode("utf-8")
    http = transport if transport is not None else _default_transport()

    try:
        status, _resp = http.request(
            VIDEOS_UPDATE_URL,
            method="PUT",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
            data=payload,
        )
    except Exception as exc:  # pragma: no cover - network/transport failure
        logger.warning("Publish update error for video %s: %s", video_id, exc)
        return PublishResult(video_id=video_id, succeeded=False, error=str(exc))

    if status == 200:
        effective = PRIVACY_PRIVATE if scheduled else privacy_status
        logger.info(
            "Video %s updated: privacy=%s%s",
            video_id,
            effective,
            f" publishAt={scheduled}" if scheduled else "",
        )
        return PublishResult(
            video_id=video_id,
            succeeded=True,
            privacy_status=effective,
            scheduled_publish_at=scheduled,
        )
    logger.warning("Publish update failed for video %s: HTTP %s", video_id, status)
    return PublishResult(
        video_id=video_id, succeeded=False, error=f"HTTP {status}"
    )


def approve_and_publish(
    packet: PublishingPacket,
    access_token: str,
    *,
    approved_by: str = "",
    transport: object | None = None,
) -> PublishResult:
    """Gated publish: refuse unless ``packet`` is approved, then publish.

    If the packet is not yet approved and ``approved_by`` is provided, the gate
    is passed (recording who approved). If neither is true, the publish is
    refused with a failed :class:`PublishResult` and the video stays a draft.

    Honors ``packet.scheduled_publish_at`` — when set, schedules instead of
    going public immediately.
    """
    if not packet.approved:
        if approved_by:
            packet.approve(approved_by)
        else:
            logger.warning(
                "Refusing to publish video %s: review gate not approved",
                packet.video_id,
            )
            return PublishResult(
                video_id=packet.video_id,
                succeeded=False,
                error="review gate not approved",
            )

    if packet.is_scheduled:
        return publish_video(
            packet.video_id,
            access_token,
            publish_at=packet.scheduled_publish_at,
            transport=transport,
        )
    return publish_video(
        packet.video_id,
        access_token,
        privacy_status=PRIVACY_PUBLIC,
        transport=transport,
    )


def _default_transport() -> object:
    """Lazily build the default urllib transport (reused from distribution)."""
    from podcaster.video.distribution import _DefaultTransport

    return _DefaultTransport()
