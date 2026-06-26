"""YouTube Data API quota monitoring & rate limiting (#447).

The YouTube Data API grants a default **10,000 units/day** per project. A
resumable video upload (``videos.insert``) costs **~1,600 units**, so only about
six uploads/day are possible before the quota is exhausted — and multilanguage
shows (en + es + fr) plus retries can approach the weekly ceiling quickly. Once
exhausted, every further call fails with HTTP 403 ``quotaExceeded`` until the
quota resets at **midnight US/Pacific** (the reset timezone Google uses for
YouTube quota).

This module tracks per-day quota consumption in a durable ledger (same
storage-backed, optimistic-concurrency pattern as ``podcaster.costs``) and
provides a **pre-flight reservation** so an upload is blocked/deferred *before*
it would exceed the daily quota, rather than failing mid-flight. It also exposes
a read-only :func:`quota_status` snapshot for monitoring/alerts.

The module is pure/side-effect-free except for :func:`reserve_quota` and
:func:`current_quota_status`, which take an explicit storage backend.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# --- Constants ---------------------------------------------------------------

#: Default daily quota granted by the YouTube Data API (units/day).
YOUTUBE_DAILY_QUOTA_UNITS = 10_000

#: Timezone the YouTube Data API uses to reset the daily quota.
QUOTA_RESET_TZ = ZoneInfo("America/Los_Angeles")

#: Quota cost (units) of each operation we perform. Sourced from Google's
#: published cost table.
QUOTA_COST_UPLOAD = 1_600  # videos.insert
QUOTA_COST_THUMBNAIL_SET = 50  # thumbnails.set
QUOTA_COST_PLAYLIST_INSERT = 50  # playlistItems.insert
QUOTA_COST_VIDEO_UPDATE = 50  # videos.update (unlisted -> public flip)
QUOTA_COST_LIST = 1  # any *.list read

QUOTA_COSTS: dict[str, int] = {
    "upload": QUOTA_COST_UPLOAD,
    "thumbnail_set": QUOTA_COST_THUMBNAIL_SET,
    "playlist_insert": QUOTA_COST_PLAYLIST_INSERT,
    "video_update": QUOTA_COST_VIDEO_UPDATE,
    "list": QUOTA_COST_LIST,
}

#: Safety buffer kept in reserve so a burst/retry near the ceiling still has
#: headroom for the cheaper follow-up calls (thumbnail/playlist).
QUOTA_SAFETY_RESERVE_UNITS = 200

#: Utilization fraction above which the quota is considered "near limit" for
#: alerting purposes.
QUOTA_NEAR_LIMIT_FRACTION = 0.85

_SCHEMA_VERSION = "squadscope-podcaster-youtube-quota-v1"


class QuotaExceeded(RuntimeError):
    """Raised when a pre-flight reservation would exceed the daily quota."""

    def __init__(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        reason = decision.get("reason", "quota_exceeded")
        super().__init__(
            f"YouTube quota would be exceeded ({reason}): "
            f"projected {decision.get('projected_units')} > "
            f"usable {decision.get('usable_units')} units for "
            f"{decision.get('day')}"
        )


# --- Pure helpers ------------------------------------------------------------


def quota_day(now: datetime | None = None) -> str:
    """Return the YouTube quota day (``YYYY-MM-DD``) in US/Pacific.

    The quota resets at midnight Pacific, so usage must be bucketed by the
    Pacific calendar date — not UTC — to align with Google's reset boundary.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(QUOTA_RESET_TZ).date().isoformat()


def operation_cost(op: str, count: int = 1) -> int:
    """Return the quota cost of ``count`` repetitions of operation ``op``."""
    if count < 0:
        raise ValueError("count must be non-negative")
    if op not in QUOTA_COSTS:
        raise ValueError(f"unknown YouTube operation: {op!r}")
    return QUOTA_COSTS[op] * count


def estimate_episode_units(
    languages: int = 1,
    *,
    thumbnail: bool = True,
    playlist: bool = True,
    retries: int = 0,
) -> int:
    """Estimate the quota units a full episode distribution consumes.

    One upload (+ optional thumbnail + playlist insert) per language, multiplied
    by ``1 + retries`` to account for re-attempts. Useful for capacity planning
    (e.g. confirming a 3-language weekly run fits the daily/weekly quota).
    """
    if languages < 0 or retries < 0:
        raise ValueError("languages and retries must be non-negative")
    per_language = QUOTA_COST_UPLOAD
    if thumbnail:
        per_language += QUOTA_COST_THUMBNAIL_SET
    if playlist:
        per_language += QUOTA_COST_PLAYLIST_INSERT
    return per_language * languages * (1 + retries)


def load_quota_ledger(content: bytes | None, *, day: str) -> dict[str, Any]:
    """Load (or initialise) the daily quota ledger for ``day``."""
    if content is None:
        return {
            "schema_version": _SCHEMA_VERSION,
            "day": day,
            "consumed_units": 0,
            "operations": [],
        }
    ledger = json.loads(content.decode("utf-8"))
    if not isinstance(ledger, dict):
        raise RuntimeError("youtube quota ledger was not a JSON object")
    if ledger.get("day") != day:
        raise RuntimeError("youtube quota ledger day did not match requested day")
    if not isinstance(ledger.get("consumed_units"), int):
        raise RuntimeError("youtube quota ledger consumed_units was not an integer")
    if not isinstance(ledger.get("operations"), list):
        raise RuntimeError("youtube quota ledger operations was not an array")
    return ledger


def quota_preflight(
    ledger: dict[str, Any],
    planned_units: int,
    *,
    daily_quota: int = YOUTUBE_DAILY_QUOTA_UNITS,
    reserve: int = QUOTA_SAFETY_RESERVE_UNITS,
) -> dict[str, Any]:
    """Decide whether ``planned_units`` may be consumed today.

    ``usable_units`` is ``daily_quota - reserve`` so a safety buffer is always
    kept free. The reservation is *allowed* only when the projected total stays
    within the usable budget. Returns a decision dict; never raises.
    """
    if planned_units < 0:
        raise ValueError("planned_units must be non-negative")
    consumed = int(ledger.get("consumed_units", 0))
    usable = max(daily_quota - max(reserve, 0), 0)
    projected = consumed + planned_units
    allowed = projected <= usable
    remaining = max(usable - consumed, 0)
    if allowed:
        reason = "within_quota"
    elif consumed >= usable:
        reason = "quota_exhausted"
    else:
        reason = "would_exceed_quota"
    return {
        "allowed": allowed,
        "day": ledger.get("day"),
        "daily_quota": daily_quota,
        "reserve": reserve,
        "usable_units": usable,
        "consumed_units": consumed,
        "planned_units": planned_units,
        "projected_units": projected,
        "remaining_units": remaining,
        "reason": reason,
    }


def record_quota_usage(
    ledger: dict[str, Any],
    units: int,
    *,
    op: str,
    job_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a new ledger with ``units`` added and an operation entry appended."""
    if units < 0:
        raise ValueError("units must be non-negative")
    updated = json.loads(json.dumps(ledger))
    updated["consumed_units"] = int(updated.get("consumed_units", 0)) + units
    operations = updated.setdefault("operations", [])
    if not isinstance(operations, list):
        raise RuntimeError("youtube quota ledger operations was not an array")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    operations.append(
        {
            "op": op,
            "units": units,
            "job_id": job_id,
            "at": moment.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )
    return updated


def quota_status(
    ledger: dict[str, Any],
    *,
    daily_quota: int = YOUTUBE_DAILY_QUOTA_UNITS,
) -> dict[str, Any]:
    """Build a read-only quota snapshot for monitoring/alerting."""
    consumed = int(ledger.get("consumed_units", 0))
    remaining = max(daily_quota - consumed, 0)
    utilization = (consumed / daily_quota) if daily_quota > 0 else 1.0
    return {
        "day": ledger.get("day"),
        "daily_quota": daily_quota,
        "consumed_units": consumed,
        "remaining_units": remaining,
        "utilization": round(utilization, 4),
        "utilization_pct": round(utilization * 100, 1),
        "near_limit": utilization >= QUOTA_NEAR_LIMIT_FRACTION,
        "exhausted": consumed >= daily_quota,
        "operation_count": len(ledger.get("operations", []))
        if isinstance(ledger.get("operations"), list)
        else 0,
    }


def quota_ledger_path(day: str) -> str:
    """Storage path for the daily quota ledger."""
    return f"quota/youtube/{day}.json"


# --- Storage-backed reservation ----------------------------------------------


def reserve_quota(
    storage: Any,
    planned_units: int,
    *,
    op: str = "upload",
    job_id: str | None = None,
    now: datetime | None = None,
    daily_quota: int = YOUTUBE_DAILY_QUOTA_UNITS,
    reserve: int = QUOTA_SAFETY_RESERVE_UNITS,
) -> dict[str, Any]:
    """Atomically pre-flight and reserve ``planned_units`` for today.

    Loads today's ledger, checks the pre-flight decision, and — only when
    allowed — records the usage, all under the storage backend's optimistic
    concurrency (``update_bytes``) so concurrent jobs cannot both slip past the
    ceiling. Raises :class:`QuotaExceeded` when the reservation is denied so the
    caller can defer the upload to the next quota day.

    Returns the pre-flight decision dict on success.
    """
    day = quota_day(now)
    path = quota_ledger_path(day)
    decision_holder: dict[str, Any] = {}

    def _apply(content: bytes | None) -> bytes:
        ledger = load_quota_ledger(content, day=day)
        decision = quota_preflight(
            ledger, planned_units, daily_quota=daily_quota, reserve=reserve
        )
        decision_holder.clear()
        decision_holder.update(decision)
        if not decision["allowed"]:
            # Persist nothing — re-raise after update_bytes returns the original.
            raise QuotaExceeded(decision)
        updated = record_quota_usage(
            ledger, planned_units, op=op, job_id=job_id, now=now
        )
        return json.dumps(updated, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )

    try:
        storage.update_bytes(path, "application/json; charset=utf-8", _apply)
    except QuotaExceeded:
        raise
    return dict(decision_holder)


def current_quota_status(
    storage: Any,
    *,
    now: datetime | None = None,
    daily_quota: int = YOUTUBE_DAILY_QUOTA_UNITS,
) -> dict[str, Any]:
    """Read today's quota ledger from storage and return a status snapshot."""
    day = quota_day(now)
    content = storage.get_bytes(quota_ledger_path(day))
    ledger = load_quota_ledger(content, day=day)
    return quota_status(ledger, daily_quota=daily_quota)
