# Video-only Spotify mode

Video-only mode publishes each weekly episode as a **Spotify video episode**
and on **YouTube**. No audio-only Spotify episode is created. Production has
run in this mode since 2026-09-24.

## Configuration

Three GitHub `prod` environment variables control it. The deploy workflow
(`.github/workflows/reusable-deploy-azure.yml`) copies them into the Azure
Container Apps settings.

| Variable | Value | Applies to | Effect |
|---|---|---|---|
| `SPOTIFY_PUBLISH_ENABLED` | `false` | synthesis job, API | Turns off the **audio** Spotify publish, including auto-publish (`PODCAST_AUTO_PUBLISH` also requires this to be `true`). |
| `SPOTIFY_VIDEO_PUBLISH_MODE` | `live` | video job | Asks for the Spotify video episode to go live instead of staying a draft. |
| `SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH` | `true` | video job | The operator's opt-in for making the video episode public. Without it, `live` falls back to a draft. |

`VIDEO_SPOTIFY_UPLOAD_ENABLED` is always `true` in `infra/modules/aca-video.bicep`.
YouTube has its own controls: `VIDEO_YOUTUBE_ENABLED`, `VIDEO_YOUTUBE_PRIVACY` and
related variables.

The Spotify video is always published as a **separate** Spotify episode. This is
true whether or not an audio episode exists, because Spotify rejects adding a
video to an episode that already has audio. The video path never reads
`SPOTIFY_PUBLISH_ENABLED`, and it doesn't need an audio anchor episode.

## Behavior with `SPOTIFY_PUBLISH_ENABLED=false`

- **After synthesis:** the runner does not call the Spotify audio publisher.
  It records `generation.publish_result.status = "skipped"` with
  `details.reason = "spotify_audio_publish_disabled"` and `outcome: null`. A
  skip is never recorded as `failed` or `manual_handoff_required`. The runner
  logs at INFO that publication is left to the video pipeline, and then queues
  the video job as usual.
- **Missing-target warning:** "no listener-facing publish target" is logged
  only when audio Spotify, YouTube and video generation are *all* disabled. The
  video job checks its own targets.
- **Review approval** (`/api/review`, `podcast-review-gate.yml`): the approval
  is recorded, but no audio publish is attempted. The job becomes
  `review_approved`, not `publish_failed`. The response contains
  `publish_status: "skipped"` and `publish_skipped_reason:
  "spotify_audio_publish_disabled"`. `publishing.eligible` stays `true`.
- **Weekly success:** a video-only week is healthy when YouTube is public and
  the Spotify video episode is live, as confirmed by provider readback
  (`/overview`). A missing audio episode is expected.

## Re-enabling audio

1. Set `SPOTIFY_PUBLISH_ENABLED=true` in the `prod` environment and redeploy.
   This changes production, so it needs operator approval.
2. To publish audio for a week that was approved while audio was off, submit
   the review approval again through `podcast-review-gate.yml`. Because the job
   is still `eligible`, the approval now runs the normal audio publish.
   Spotify's own reconcile/identity guards still apply.

## Go-live classification

For both audio and video, Spotify go-live is a `POST /v3/episodes/{id}/update`
with `isPublished: true`. The result is decided by reading back
`/v3/episodes/{id}/overview`, not by the HTTP status of the update. The old
`/v3/episodes/{id}/publish` endpoint (HTTP 404) is no longer called. See
[spotify-video-upload.md](spotify-video-upload.md).
