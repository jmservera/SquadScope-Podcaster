"""Spotify publishing mode helpers.

Spotify publishing is controlled per artifact:

* ``SPOTIFY_PUBLISH_ENABLED`` (synthesis job + API) gates the **audio** episode.
* ``SPOTIFY_VIDEO_PUBLISH_MODE`` / ``SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH`` (video
  job) gate the **video** episode, which is always a separate Spotify episode.

With ``SPOTIFY_PUBLISH_ENABLED=false`` and the video variables set to ``live`` /
``true`` the pipeline runs in *video-only* mode: the audio publish is a
deliberate skip (recorded as ``skipped``, never as ``failed``) and the video
pipeline remains the listener-facing path.
"""

from __future__ import annotations

import json
import os
from typing import Any

AUDIO_PUBLISH_DISABLED_REASON = "spotify_audio_publish_disabled"
PUBLISH_STATUS_SKIPPED = "skipped"


def spotify_audio_publish_enabled() -> bool:
    """Whether the audio Spotify episode may be published at all."""
    return os.environ.get("SPOTIFY_PUBLISH_ENABLED", "").lower() == "true"


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def spotify_video_live_publish_configured() -> bool:
    """Whether the video job is configured to take the Spotify video episode live."""
    mode = os.environ.get("SPOTIFY_VIDEO_PUBLISH_MODE", "draft").strip().lower()
    allowed = os.environ.get("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "").strip().lower() in _TRUTHY
    return mode == "live" and allowed


def review_publish_fields(
    publish_result: Any,
    *,
    audio_publish_skipped: bool,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish fields for a review response; a video-only skip reports ``skipped``.

    A skip is only reported for a job that would otherwise have been published;
    a job with publish blockers reports ``blocked`` with its blockers instead.
    """
    if publish_result is not None:
        return {"publish_status": publish_result.status, "publish_error": publish_result.error}
    publishing = manifest.get("publishing") if isinstance(manifest, dict) else None
    publishing = publishing if isinstance(publishing, dict) else {}
    if audio_publish_skipped and publishing.get("eligible") is not True:
        return {
            "publish_status": "blocked",
            "publish_error": None,
            "publish_blocked_by": list(publishing.get("blocked_by") or []),
        }
    if audio_publish_skipped:
        return {
            "publish_status": PUBLISH_STATUS_SKIPPED,
            "publish_error": None,
            "publish_skipped_reason": AUDIO_PUBLISH_DISABLED_REASON,
        }
    return {"publish_status": None, "publish_error": None}


# Error text of the pre-video-only behavior, when a disabled audio publish was
# recorded as a failure. Such records are normalized to ``skipped``.
_LEGACY_DISABLED_ERROR_PREFIX = "Spotify publishing disabled"


def _is_replaceable_result(result: Any) -> bool:
    """Absent results and legacy "disabled" failures may become ``skipped``.

    Any other result is real provider history (for example a draft, a live
    episode, or an unknown/retry-blocked outcome) and is preserved.
    """
    if result is None:
        return True
    return (
        isinstance(result, dict)
        and result.get("status") == "failed"
        and str(result.get("error") or "").startswith(_LEGACY_DISABLED_ERROR_PREFIX)
    )


def apply_review_audio_skip(manifest: dict[str, Any], *, reviewed_at: str) -> dict[str, Any]:
    """Record a video-only audio skip on an approved manifest (in place)."""
    publishing = manifest.setdefault("publishing", {})
    if isinstance(publishing, dict) and _is_replaceable_result(publishing.get("result")):
        publishing["result"] = {
            "status": PUBLISH_STATUS_SKIPPED,
            "completed_at": reviewed_at,
            "anchor_episode_id": None,
            "dry_run": False,
            "error": None,
            "details": {"reason": AUDIO_PUBLISH_DISABLED_REASON},
            "outcome": None,
            "publish_run_id": None,
        }
    generation = manifest.get("generation")
    if isinstance(generation, dict):
        direct = generation.get("publish_result")
        if direct is not None and _is_replaceable_result(direct):
            generation["publish_result"] = {
                **direct,
                "status": PUBLISH_STATUS_SKIPPED,
                "outcome": None,
                "error": None,
                "details": {"reason": AUDIO_PUBLISH_DISABLED_REASON},
            }
    return manifest


def record_review_audio_skip(
    storage: Any, job_id: str, *, reviewed_at: str
) -> dict[str, Any] | None:
    """Persist :func:`apply_review_audio_skip`; returns the updated manifest."""
    from podcaster.generation import manifest_bytes
    from podcaster.orchestration import manifest_path

    updated: dict[str, Any] = {}

    def _apply(content: bytes | None) -> bytes:
        if content is None:
            raise ValueError(f"no manifest found for job_id={job_id}")
        document = json.loads(content.decode("utf-8"))
        apply_review_audio_skip(document, reviewed_at=reviewed_at)
        updated.update(document)
        return manifest_bytes(document)

    storage.update_bytes(manifest_path(job_id), "application/json; charset=utf-8", _apply)
    return updated or None


def record_review_skip_outcome(
    storage: Any,
    job_id: str,
    outcome: Any,
    reviewed_at: str,
    audio_publish_skipped: bool,
) -> Any:
    """Persist a review-time audio skip for an eligible approval, if any.

    Returns ``outcome`` with its manifest replaced by the persisted one.
    """
    publishing = outcome.manifest.get("publishing") if isinstance(outcome.manifest, dict) else None
    eligible = isinstance(publishing, dict) and publishing.get("eligible") is True
    if not audio_publish_skipped or outcome.publish_result is not None or not eligible:
        return outcome
    # Persistence failures propagate: the endpoint must not report ``skipped``
    # unless storage agrees. Re-submitting the approval is safe.
    updated = record_review_audio_skip(storage, job_id, reviewed_at=reviewed_at)
    if updated is None:
        return outcome
    return type(outcome)(updated, outcome.publish_result)
