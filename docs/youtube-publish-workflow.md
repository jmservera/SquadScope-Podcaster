# YouTube Unlisted-Draft → Manual Publish Workflow (#446)

This document describes the human review gate every episode passes through
before it becomes public on YouTube. It pairs with the upload module (#442),
metadata/thumbnail generation (#445), and playlist management (#449).

## Principle: never publish straight to public

Fresh uploads are **always** drafts. The upload request sets
`status.privacyStatus = unlisted` (or `private`) — never `public`
(`podcaster/video/distribution.py`, `VideoDistributionConfig.youtube_privacy`,
default `unlisted`). The publish module
(`podcaster/video/youtube_publish.py`) refuses to construct a packet whose
`draft_privacy` is `public`.

## The lifecycle

```
upload (unlisted/private draft)
        │
        ▼
build_publishing_packet()   ← explicit review gate (approved=False)
        │
   human review  ───────────►  approve(by="<reviewer>")
        │
        ▼
approve_and_publish()       ← refuses unless approved
        │
   ┌────┴─────────────┐
   ▼                  ▼
publish now        schedule (privacyStatus=private + publishAt)
(public)           (YouTube flips it public at publishAt)
```

## Components

### `PublishingPacket`
A serializable record (`to_json()` / `to_dict()`) that travels with an episode
between "uploaded" and "approved & public". Key fields:

| Field                  | Meaning                                                |
| ---------------------- | ------------------------------------------------------ |
| `video_id`             | The uploaded draft's YouTube id.                       |
| `draft_privacy`        | `unlisted` (default) or `private`. Never `public`.     |
| `review_url`           | Deep link to YouTube Studio for the reviewer.          |
| `review_notes`         | Free-form notes for the reviewer.                      |
| `approved` / `approved_by` | The gate. Starts `False`.                          |
| `scheduled_publish_at` | Optional RFC-3339 publish time.                        |

`is_public_ready` is `True` only after `approve()`.

### `publish_video(video_id, token, *, privacy_status=..., publish_at=...)`
Calls the YouTube `videos.update` endpoint (`part=status`, `PUT`). With
`publish_at` it sets `privacyStatus=private` + `publishAt` (a scheduled
publish). Never raises on an HTTP/transport error — returns a failed
`PublishResult` so one failure can't abort a batch. The access token is only
sent in the `Authorization` header and is never logged.

### `approve_and_publish(packet, token, *, approved_by=...)`
The **gated** entry point. It refuses to call the API unless the packet is
approved; passing `approved_by` approves it inline (recording who did so). It
honors `packet.scheduled_publish_at` (schedule vs. publish-now).

## The review gate (human or automated)

The gate is explicit and auditable:

1. After upload, automation builds a `PublishingPacket` and surfaces
   `review_url` + `review_notes` to a reviewer (e.g. a Slack/issue notification).
2. A reviewer checks the unlisted draft (audio, captions, thumbnail, metadata,
   cultural/localization correctness for es/fr shows).
3. Approval is recorded by calling `approve(by="<reviewer>")` (or passing
   `approved_by` to `approve_and_publish`). Only then can the video go public.

Automation **may** set `approved=True` programmatically, but only as the
explicit final step of a documented approval flow — never implicitly on upload.

## Phase 2: Promotion to public (the explicit second phase)

After the human review gate is satisfied, use `scripts/youtube_promote.py` to
verify the draft and promote it to public. This is the **canonical Phase 2
command** — it reads back the video's metadata from the YouTube API, checks
that the title and description are non-empty, optionally verifies playlist
membership, and only then calls `approve_and_publish()`.

```bash
# Dry-run: verify readiness without promoting
python3 scripts/youtube_promote.py \
  --video-id <YOUTUBE_VIDEO_ID> \
  --check-only \
  --playlist-id PLiZvxqBMVr8cwx6p0L8oOe9YydmCEuJuJ

# Promote to public now (approved-by recorded in the audit log)
python3 scripts/youtube_promote.py \
  --video-id <YOUTUBE_VIDEO_ID> \
  --approved-by <github-actor> \
  --playlist-id PLiZvxqBMVr8cwx6p0L8oOe9YydmCEuJuJ

# Schedule a future publish instead of going public immediately
python3 scripts/youtube_promote.py \
  --video-id <YOUTUBE_VIDEO_ID> \
  --approved-by <github-actor> \
  --publish-at 2026-09-01T18:00:00Z
```

Credentials are read from the standard environment variables:
`VIDEO_YOUTUBE_CLIENT_ID`, `VIDEO_YOUTUBE_CLIENT_SECRET`,
`VIDEO_YOUTUBE_REFRESH_TOKEN`.

The script exits 0 on success, 1 on verification failure or promotion error,
and 2 on credential/argument error. It never prints or logs the access token.

## Scheduled publishing

Provide `scheduled_publish_at` (a `datetime` or RFC-3339 string) when building
the packet to schedule instead of going public immediately. YouTube keeps the
video `private` until `publishAt`, then makes it public automatically.

## Multilanguage

The packet carries `locale` (`en` / `es` / `fr`) so the review notification and
playlist routing (#449) can target the right language show. Each language's
draft is reviewed and approved independently.
