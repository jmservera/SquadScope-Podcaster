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

## Re-arming a fenced upload after a failed attempt

Before every YouTube upload the video job durably claims `upload_intent`
(`publication_unknown`, `retry_blocked=true`). If the attempt then fails without
persisting an outcome (for example a pre-network privacy rejection, or a crash
after the upload), the claim stays and every later video run skips YouTube, so
the job can never upload a second copy. To upload again, an operator must prove
that no video exists and record an explicit, single-use retry authorization.

Use `podcaster.provider_retry_rearm`. The same module also runs locally as
`scripts/rearm_provider_retry.py`, but the container image only ships the
`podcaster` package, so use the module form in-boundary:

```bash
# Dry-run (default): check preconditions and prove absence, write nothing
python -m podcaster.provider_retry_rearm \
  --job-id <JOB_ID> --provider youtube \
  --approved-by <github-actor> --reason "<why the fenced attempt did not upload>"

# Write exactly one retry_blocked=false authorization record
python -m podcaster.provider_retry_rearm ... --apply
```

What the command guarantees:

- **Preconditions.** The latest `youtube:video` evidence must be a retry-blocked
  `upload_intent` claim with no provider ID. No `youtube:video` record for the
  job may ever have carried a provider ID, and neither may the manifest
  (`video_runner.distribution.youtube_id` / `video_publish.youtube`). The claim
  must be at least 2 hours old (longer than the video job's 5400s replica
  timeout, so no attempt can still be uploading and the uploads listing has
  caught up; this age check is the liveness guarantee), and the manifest's
  `video_runner` state must be terminal (defence in depth only).
  Otherwise it refuses without calling YouTube.
- **Authoritative absence proof.** It uses the job's OAuth credentials
  (`VIDEO_YOUTUBE_CLIENT_ID`/`_CLIENT_SECRET`/`_REFRESH_TOKEN`) to read the
  channel's own uploads playlist (`channels?mine=true`) with full pagination.
  It resolves every upload with `videos.list` (`snippet,status,processingDetails`),
  so private, unlisted, public, processing, failed and rejected videos are all
  covered. A video is a candidate if any of these hold:
  - its title matches the expected title, including YouTube's 100-character
    truncation, or either title is a prefix (at least 20 characters) of the other;
  - its title or description contains the job ID;
  - its title carries the same `Wnn` week token;
  - it has no publish time;
  - it was uploaded after the claim (minus 30 minutes of slack), whatever its title.
- **Fail closed.** Any HTTP or transport error, invalid JSON, missing page field,
  scanned-count mismatch with `totalResults`, or unresolved video refuses with
  exit code 1. A candidate refuses with exit code 3 and prints the video ID(s);
  adopt that video instead of re-uploading.
- **Single use.** `--apply` appends one `operator_retry_rearm` record with
  `retry_blocked=false`, `approved_by`, `reason`, the absence-proof summary and
  timestamps. The append is conditional on the claim still being the latest
  record. The next video run's `upload_intent` claim consumes it exactly once. A
  second `--apply` before that is a no-op (`already_armed`). No token or secret is
  printed or stored.

After re-arming, re-drive only the video stage:
`POST /api/jobs/<JOB_ID>/video/generate` (header `X-Podcaster-Api-Key`). This
enqueues a `video-jobs` message and never re-runs synthesis or the audio
publish. When the new draft exists, promote it with `scripts/youtube_promote.py`
as above.

Spotify video is not supported by the re-arm command yet. Its absence proof
depends on the Anchor listing readback and must be added explicitly.

## Scheduled publishing

Provide `scheduled_publish_at` (a `datetime` or RFC-3339 string) when building
the packet to schedule instead of going public immediately. YouTube keeps the
video `private` until `publishAt`, then makes it public automatically.

## Multilanguage

The packet carries `locale` (`en` / `es` / `fr`) so the review notification and
playlist routing (#449) can target the right language show. Each language's
draft is reviewed and approved independently.

## Canonical delivery state and reconciliation

A successful unlisted/private upload is `draft_created`, while the existing
legacy per-platform `status: published` value remains temporarily as the
at-most-once compatibility marker. Promotion performs one `videos.update` and
then a `videos.list` read-back. Only `privacyStatus=public` is canonical
`published`; a transport loss, retryable HTTP response, unreadable response, or
failed/contradictory read-back is `publication_unknown` and must not be
automatically repeated. Scheduled private state remains `draft_created` until
public state is independently confirmed.

Accepted jobs persist bounded identity-bound evidence before provider mutation.
Existing blocking evidence takes precedence over the legacy snapshot.

### Identity-bound upload reconciliation (#678)

For canonical accepted jobs every upload carries a deterministic identity tag
(`sqpub-` + 32 hex characters of SHA-256 over the scheme, accepted job ID,
publish run ID, week and article SHA-256). The tag contains no raw identifiers
and is declared in the `upload_intent` evidence (`details.youtube_identity_tag`)
**before** the upload session is opened.

When a redelivered job finds that intent still `publication_unknown` without a
video ID, it reads back the channel's uploads playlist (`channels.list
mine=true`, at most four `playlistItems.list` pages of 50, then `videos.list`;
at most 9 quota units) and never opens a new upload session. The newest-first
listing order is verified, and the scan counts as exhaustive only when it
reaches the end of the playlist or an item older than the intent timestamp
minus 15 minutes of clock skew. Hitting the page bound first is treated as
contradictory, and so is a `videos.list` readback that omits, repeats, or adds
any requested ID. It binds the video
only if exactly one upload carries the exact tag, is still `private`/`unlisted`,
and has `uploadStatus` `uploaded` or `processed`. That records `draft_created`
with `verification=provider_readback` and
`evidence_source=youtube_identity_readback`, then the idempotent playlist step
runs. If persisting that evidence fails, the error is recorded and the bound
video still flows to the playlist step without a second upload. If no upload
matches, more than one does, a match is public or incomplete,
the page is malformed, the read fails, or the intent predates the tag, the job
stays `publication_unknown` and needs the manual re-arm path above. Title and
newest-upload matches are never used.

Rollback
retains provider artifacts and legacy fields; disable the additive projection
or revert the implementation rather than deleting uploaded videos.
