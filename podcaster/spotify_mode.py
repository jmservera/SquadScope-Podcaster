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

import os
from typing import Any

AUDIO_PUBLISH_DISABLED_REASON = "spotify_audio_publish_disabled"
PUBLISH_STATUS_SKIPPED = "skipped"


def spotify_audio_publish_enabled() -> bool:
    """Whether the audio Spotify episode may be published at all."""
    return os.environ.get("SPOTIFY_PUBLISH_ENABLED", "").lower() == "true"


def review_publish_fields(publish_result: Any, *, audio_publish_skipped: bool) -> dict[str, Any]:
    """Publish fields for a review response; a video-only skip reports ``skipped``."""
    if publish_result is not None:
        return {"publish_status": publish_result.status, "publish_error": publish_result.error}
    if audio_publish_skipped:
        return {
            "publish_status": PUBLISH_STATUS_SKIPPED,
            "publish_error": None,
            "publish_skipped_reason": AUDIO_PUBLISH_DISABLED_REASON,
        }
    return {"publish_status": None, "publish_error": None}
