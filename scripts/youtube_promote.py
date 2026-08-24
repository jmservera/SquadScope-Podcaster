"""Phase 2 YouTube promotion: verify unlisted draft then promote to public (#652).

This is the explicit second phase of the two-phase YouTube publish workflow
described in ``docs/youtube-publish-workflow.md``. Phase 1 (upload as unlisted
draft + playlist insertion) is handled by the ACA video job. Phase 2 is this
script.

Usage::

    # Verify + promote immediately
    python3 scripts/youtube_promote.py \\
        --video-id <YOUTUBE_VIDEO_ID> \\
        --approved-by <github-actor> \\
        --playlist-id PLiZvxqBMVr8cwx6p0L8oOe9YydmCEuJuJ

    # Verify only (dry-run — do not flip to public)
    python3 scripts/youtube_promote.py \\
        --video-id <YOUTUBE_VIDEO_ID> \\
        --check-only \\
        --playlist-id PLiZvxqBMVr8cwx6p0L8oOe9YydmCEuJuJ

    # Schedule a future publish instead of going public now
    python3 scripts/youtube_promote.py \\
        --video-id <YOUTUBE_VIDEO_ID> \\
        --approved-by <github-actor> \\
        --publish-at 2026-08-30T18:00:00Z

Credentials are read from the same environment variables used by the ACA video
job: VIDEO_YOUTUBE_CLIENT_ID, VIDEO_YOUTUBE_CLIENT_SECRET,
VIDEO_YOUTUBE_REFRESH_TOKEN (or VIDEO_YOUTUBE_REFRESH_TOKEN_PATH for a file).

Exit codes:
  0 — success (or --check-only + no problems)
  1 — verification failed or promotion rejected/failed
  2 — credentials or argument error
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from podcaster.video.distribution import (  # noqa: E402
    VideoDistributionConfig,
    _DefaultTransport,
    _get_youtube_access_token,
)
from podcaster.video.youtube_publish import (  # noqa: E402
    PublishingPacket,
    approve_and_publish,
    build_publishing_packet,
    verify_draft_ready,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify and promote a YouTube unlisted draft to public (Phase 2).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--video-id", required=True, help="YouTube video ID of the unlisted draft.")
    parser.add_argument(
        "--approved-by",
        default="",
        help="Reviewer identity (GitHub actor). Required unless --check-only.",
    )
    parser.add_argument(
        "--playlist-id",
        default="",
        help="Playlist ID to verify membership before promoting.",
    )
    parser.add_argument(
        "--publish-at",
        default="",
        help="RFC-3339 UTC timestamp to schedule instead of going public immediately.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Verify metadata and playlist membership without promoting.",
    )
    args = parser.parse_args(argv)

    if not args.check_only and not args.approved_by:
        print(
            "error: --approved-by is required to promote a video. "
            "Use --check-only to verify without promoting.",
            file=sys.stderr,
        )
        return 2

    # Load credentials from env (same path as the ACA job's from_env()).
    config = VideoDistributionConfig.from_env()
    has_creds = (
        config.youtube_client_id and config.youtube_client_secret and config.youtube_refresh_token
    )
    if not has_creds:
        print(
            "error: VIDEO_YOUTUBE_CLIENT_ID, VIDEO_YOUTUBE_CLIENT_SECRET, and "
            "VIDEO_YOUTUBE_REFRESH_TOKEN must be set.",
            file=sys.stderr,
        )
        return 2

    print("Obtaining YouTube access token…")
    transport = _DefaultTransport()
    try:
        access_token = _get_youtube_access_token(config, transport)
    except Exception as exc:
        print(f"error: could not obtain access token: {exc}", file=sys.stderr)
        return 2

    # --- Phase 2a: verify draft is ready for promotion ---
    print(f"Verifying draft {args.video_id!r}…")
    problems = verify_draft_ready(
        args.video_id,
        access_token,
        playlist_id=args.playlist_id,
    )
    if problems:
        print("Verification FAILED — the following problems must be resolved before promoting:")
        for p in problems:
            print(f"  • {p}")
        return 1
    print("Verification passed: non-empty title/description, correct playlist membership.")

    if args.check_only:
        print("--check-only specified — skipping promotion.")
        return 0

    # --- Phase 2b: promote to public via the established gated entry point ---
    packet: PublishingPacket = build_publishing_packet(
        args.video_id,
        draft_privacy="unlisted",
        review_notes=f"Promoted by {args.approved_by}",
        scheduled_publish_at=args.publish_at or None,
    )
    print(f"Promoting {args.video_id!r} to public (approved-by={args.approved_by!r})…")
    result = approve_and_publish(packet, access_token, approved_by=args.approved_by)

    if result.succeeded:
        if result.scheduled_publish_at:
            print(f"Scheduled: video will go public at {result.scheduled_publish_at}.")
        else:
            print(f"Done: video {args.video_id!r} is now public.")
        return 0

    print(f"Promotion FAILED: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
