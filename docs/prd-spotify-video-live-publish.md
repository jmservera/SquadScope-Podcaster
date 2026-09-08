# PRD: Safe Spotify Video Draft Promotion

**Status:** Draft  
**Owner:** Platform/Backend  
**Repository:** `jmservera/SquadScope-Podcaster`  
**Last updated:** 2026-09-08

## Executive Summary

The video distribution job successfully uploads each MP4 to Spotify as a new,
separate draft episode, but it has no automated path to promote that existing
video draft to live. An operator must therefore publish every video episode
manually after upload.

This PRD defines only the post-upload promotion step for the exact Spotify video
`anchor_id` produced by the current job. The step must fail closed, require
video-specific publication intent and authorization, preserve the permanent W35
draft exceptions, and emit durable terminal-state evidence. It must not reuse
the audio live-publish authorization.

The repository contains a working call to
`POST /v3/episodes/{anchor_id}/publish?isMumsCompatible=true` for episodes
created by the synthesis publisher. However, neither the repository nor
Spotify's public documentation proves that this unofficial endpoint can promote
a previously uploaded video draft, and its retry/idempotency behavior is
undocumented. Automated video promotion must remain disabled until a controlled
real-platform canary proves that exact transition. If the platform rejects it,
manual Spotify for Creators publication remains the required handoff.

## Problem Statement

`podcaster.video.distribution.upload_to_spotify_episode()` delegates to
`podcaster.publish.upload_video_to_episode()`. That upload flow:

1. creates or reconciles a new, separate Spotify episode;
2. uploads and processes the MP4;
3. sets metadata with `publish_behavior="draft"`; and
4. returns the new video episode's `anchor_episode_id`.

Spotify rejects attaching video to the existing audio episode, so the separate
video episode is intentional. The gap occurs after the upload: no caller accepts
the returned video `anchor_id` and promotes that already-created draft to live.
The result is a recurring manual publication task after every successful video
job.

The caller's `publish_mode: live`, merged in
`jmservera/SquadScope#746` at `c4d8b33`, authorizes the audio synthesis path
only. `SPOTIFY_ALLOW_LIVE_PUBLISH`, wired by #663, also governs audio
publication only. Neither setting authorizes video promotion.

## Goals

- Promote the exact, already-uploaded Spotify video draft from the current
  video job without re-uploading it.
- Require explicit video publication intent and a separate operator
  authorization before any promote request.
- Prevent promotion of the paired audio episode, historical drafts, or any
  episode found through a broad title/list search.
- Make retries safe without assuming the unofficial Spotify endpoint is
  idempotent.
- Record durable evidence of the final Spotify publication state.
- Preserve a precise manual handoff when automation is unavailable or withheld.

## Scope

This PRD covers one transition:

```text
current job's processed Spotify video draft anchor_id -> published video episode
```

The promote step begins only after the existing video upload has completed,
server-side media processing has succeeded, metadata has been saved, and the
new video `anchor_id` has been returned.

## Non-Goals

- Uploading or re-uploading video.
- Creating a replacement Spotify episode.
- Publishing, deleting, or otherwise changing the audio episode.
- Publishing or changing YouTube content.
- Dispatching podcast or video jobs; auto-dispatch remains owned by
  `jmservera/SquadScope#743`.
- Retrospective cleanup or bulk publication of historical drafts.
- Replacing Spotify for Creators with a supported public Spotify API. No such
  creator publication API is currently documented.

## Current Production State

| Week | Artifact | Current state | Required action |
|------|----------|---------------|-----------------|
| W37 | Spotify video `anchorId=125401976` | Draft, recorded in production logs at `2026-09-08T14:30:49Z` | Publish manually now; see [W37 operator action](#w37-operator-action) |
| W37 | Spotify audio `anchorId=125398950` | Draft | Outside this PRD; do not select it as the video carrier |
| W37 | YouTube `un0L2oVBpBI` | Public | No action |
| W35 | Spotify audio `anchorId=124658107` | Protected historical draft | Never publish |
| W35 | Spotify audio `anchorId=124658398` | Protected historical draft | Never publish |
| W35 | Spotify video `anchorId=124662333` | Protected historical draft | Never publish |

## Platform Constraints and Evidence

### Verified repository behavior

- `podcaster.publish._publish_episode_live(session, anchor_id)` sends:

  ```http
  POST https://api-v5.anchor.fm/v3/episodes/{anchor_id}/publish?isMumsCompatible=true
  {}
  ```

- The synthesis publisher calls this function after creating an episode when
  its publish behavior is not `draft`.
- `podcaster.publish.upload_video_to_episode()` creates a separate video
  episode and deliberately stops after setting draft metadata.
- `docs/spotify-video-upload.md` documents the multipart video upload as
  real-platform validated, while section 9 still requires manual validation of
  a draft episode as the final gate.

### Unverified behavior that blocks immediate automation

Spotify does not publicly document the internal Anchor endpoint used by this
repository. Available evidence does **not** establish:

- that `POST /v3/episodes/{anchor_id}/publish` accepts a processed video episode
  created by the multipart upload flow;
- whether additional wizard, monetization, sponsored-content, or distribution
  state is required before a video can be published;
- the response and side effects when the target episode is already live; or
- whether repeated publish requests are idempotent.

The public Spotify Web API exposes catalog reads, not creator draft/publication
controls. The internal endpoint is cookie-authenticated, unsupported, and may
change without notice.

### Capability gate

Before automated video promotion can be enabled in production, an operator must
approve one controlled canary using a newly uploaded, non-protected video draft.
The canary must use the same processed-video path as production and must prove
all of the following:

1. The target is the exact video `anchor_id` returned by that canary upload.
2. The publish endpoint returns an accepted response.
3. Provider state subsequently reports `isPublished=true`.
4. Spotify provides a public episode URL that resolves to the video episode and
   plays video.
5. Re-running the promote workflow does not create another episode or change a
   different episode. If safe repeat behavior cannot be proven, preflight state
   detection must prevent the second POST.

If the endpoint rejects the video draft, requires an unmodeled UI-only step, or
cannot produce trustworthy publication confirmation, automated promotion is
not platform-compatible. The implementation must remain disabled and use the
manual handoff in this PRD. It must not work around the restriction by
re-uploading, editing the audio episode, or automating broad UI selection.

## Product Design

### Two-key authorization

Promotion requires both of these independent conditions:

1. **Video publication intent:** the current video job explicitly requests
   `live` through a video-specific field such as
   `spotify_video_publish_mode=live`. Its default and missing-value behavior is
   `draft`. The existing caller field `publish_mode` must not be interpreted as
   video intent because it controls audio publication.
2. **Operator authorization:**
   `SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH=true`. The environment variable defaults
   to `false`; missing, empty, malformed, or non-truthy values deny promotion.

`SPOTIFY_ALLOW_LIVE_PUBLISH` must not be reused. Audio and video are separate
episodes with different operational risks, so their authorization policies
must remain independent.

If either key is absent, the job leaves the video episode as a draft, reports a
successful upload with publication state `draft`, and emits a terminal event
explaining which gate denied promotion. A denied promote gate must not make the
completed upload look like a failed upload.

This PRD does not prescribe where a future caller obtains video publication
intent. Adding that field to caller configuration requires compatibility
coordination and must not be folded into the auto-dispatch work in
`jmservera/SquadScope#743`.

### Target binding and no-duplicate guarantee

The promote operation must accept one explicit input: the
`anchor_episode_id` returned by the successful video upload result in the same
job lineage.

Before any publish request, the implementation must verify:

- the ID is present and numeric;
- the video upload result succeeded and server-side processing completed;
- the ID equals the video result's `anchor_episode_id`;
- the ID does not equal the paired `audio_anchor_id`;
- the job/manifest identifies it as the Spotify video artifact for the current
  run; and
- the ID is not in the protected historical denylist.

The promote path must not:

- search by episode title;
- select the newest draft;
- list drafts and choose a candidate;
- accept the synthesis/audio `anchor_id`;
- create an episode;
- upload media; or
- process more than one ID.

These constraints make promotion incapable of creating a duplicate and prevent
an old draft from being selected accidentally.

### Permanent W35 exclusion

The following Anchor IDs are permanent automation exclusions:

```text
124658107
124658398
124662333
```

The implementation must enforce these IDs in a repository-owned, immutable
denylist checked before session construction or any Spotify mutation. Deployment
configuration may add further denied IDs but must not remove these three.

An attempted promote for a protected ID must:

- make no Spotify mutation request;
- return terminal state `blocked_protected_historical_draft`;
- emit a high-visibility structured log with the denied ID and job lineage; and
- fail the publication portion of the workflow without deleting or changing the
  draft.

Tests must assert each W35 ID is blocked even when both live-publish gates are
enabled. No migration, backfill, bulk command, or manual recovery instruction
may override this protection.

### State-based idempotency

The publish endpoint's idempotency is unknown and must not be assumed.
Idempotency is therefore defined by desired provider state and durable job
state:

1. If trusted provider state already reports `isPublished=true` for the exact
   video ID, skip the POST and record `already_published`.
2. If durable terminal telemetry for the same job lineage and video ID already
   records confirmed publication, skip the POST unless an operator explicitly
   starts a diagnostic verification-only action.
3. If state is draft or not readable but the current job proves ownership of a
   newly processed video draft, issue at most one publish request per workflow
   attempt.
4. After a timeout or ambiguous response, query publication state before any
   retry. Retry only when the provider conclusively reports the episode is still
   a draft and the retry policy explicitly allows it.
5. Treat a conflict or already-published response as success only after
   independent state confirmation.

A second call against an already-live episode must converge on
`already_published` without creating or selecting another episode. Until the
canary establishes the endpoint's repeat behavior, preflight/read-back controls
must prevent a blind second POST.

### Failure behavior

- Authorization denied: leave draft intact; record `draft_gate_denied`.
- Protected ID: do not call Spotify; record
  `blocked_protected_historical_draft`.
- Platform rejects video publication: leave draft intact; record
  `manual_handoff_required`.
- Ambiguous response: do not claim success; verify state, then record
  `published`, `draft`, or `publication_state_unknown`.
- Confirmation timeout: preserve the episode, avoid re-upload, and route to
  manual verification.
- Credential failure: expose the existing sanitized credential-expiry handling;
  never log cookies or authorization headers.

## Durable Terminal-State Telemetry

Every promote decision must emit exactly one durable terminal event after the
last confirmation attempt. A recommended event name is
`spotify_video_publication_terminal`.

The event must record:

- UTC timestamp;
- job/run ID and manifest correlation ID;
- video `anchor_id`;
- paired audio `anchor_id`;
- requested video publish mode;
- whether `SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH` authorized the action, without
  logging the raw environment value;
- W35/protected-ID check result;
- publish request outcome and sanitized HTTP status, if a request occurred;
- final normalized state: `draft_gate_denied`,
  `blocked_protected_historical_draft`, `published`, `already_published`,
  `manual_handoff_required`, or `publication_state_unknown`;
- confirmed provider `isPublished` value when available;
- Spotify public episode URL when available;
- distribution/RSS evidence when available; and
- evidence source and confirmation timestamp.

The event must be written to the normal production log stream and persisted in
the job's durable result/manifest so future operators and agents can establish
the terminal outcome without repeating the mutation or relying only on
ephemeral logs.

Messages such as `publish requested` or an HTTP 2xx alone are not terminal
publication evidence.

## Definition of `videoislive`

The video is considered live only when all required evidence is attached to the
terminal event:

1. **Identity:** the confirmed episode is the exact video `anchor_id` from the
   current upload lineage and is not the audio ID.
2. **Provider publication state:** a provider read reports
   `isPublished=true`, or an equivalent provider response is captured and
   independently confirmed by a subsequent read.
3. **Public artifact:** a Spotify episode URL is returned or resolved and is
   publicly accessible.
4. **Video correctness:** the public episode is identified as a video episode
   and video playback is available.

RSS/distribution status should also be recorded when Spotify exposes it. It is
supporting evidence and may lag publication; a lagging RSS index must be
reported as pending rather than used to publish another episode.

An upload success, processed media state, publish-request log, title match, or
YouTube publication does not satisfy `videoislive`.

## Manual Handoff Fallback

Use this procedure whenever automation is disabled, the capability canary has
not passed, Spotify rejects the promote endpoint, publication state remains
unknown, or an operator intentionally retains manual control.

1. Open `https://creators.spotify.com/` and select the intended show.
2. Go to **Episodes**, then filter or scan for **Drafts**. The legacy direct
   episodes route `https://podcasters.spotify.com/pod/dashboard/episodes`
   redirects to the same Spotify for Creators area.
3. Open the intended draft. Confirm its job/week title, upload time, and video
   preview or video badge. Cross-check the episode detail with the expected
   video `anchor_id`. If the UI does not provide enough evidence to distinguish
   the video draft from the audio draft, stop rather than guessing.
4. Verify the media preview is video and that the selected ID is not the paired
   audio ID and is not one of the W35 protected IDs.
5. Review the title, description, episode type, explicit-content setting, and
   any required monetization or sponsored-content declarations.
6. Click **Publish**. Choose **Publish now** rather than scheduling unless the
   release plan explicitly requires a schedule, then confirm the publication
   dialog.
7. Wait for the episode to show **Published**. Use **View on Spotify** or the
   episode share action to open the public Spotify episode URL.
8. Confirm that the URL loads without creator authentication and that the video
   plays.
9. Record the video `anchor_id`, public Spotify URL, publication timestamp,
   visible publication state, and operator identity in the job record or
   operational handoff.

Do not use manual publication as an excuse to alter, delete, or publish the
paired audio draft or any W35 draft.

## W37 Operator Action

W37 currently requires the manual fallback because the codebase has no
promote-existing-video-draft caller and the endpoint has not been validated for
that transition.

1. Open Spotify for Creators and select the Claracle show.
2. Go to **Episodes** and open the W37 draft corresponding to video
   `anchorId=125401976`.
3. Before publishing, verify that the draft has a video preview/badge and is not
   the W37 audio draft `anchorId=125398950`.
4. Confirm the ID is not any protected W35 ID: `124658107`, `124658398`, or
   `124662333`.
5. Click **Publish**, choose **Publish now**, and confirm.
6. Wait for **Published**, open **View on Spotify**, and verify public video
   playback.
7. Record the Spotify episode URL and confirmation time against the W37 job.

YouTube `un0L2oVBpBI` is already public and requires no action. This procedure
must not publish the W37 audio draft or touch any W35 draft.

## Functional Requirements

1. The system accepts only the current upload result's video
   `anchor_episode_id` as the promote target.
2. The system requires video-specific live intent and
   `SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH=true`.
3. Both gates default to draft/denied.
4. The system rejects the paired audio ID and all protected W35 IDs before any
   mutation.
5. The system performs no upload, episode creation, bulk lookup, or title-based
   selection during promotion.
6. The system confirms provider publication state and public video identity
   before reporting `published`.
7. The system emits and persists one terminal event for every promote decision.
8. The system provides a manual-handoff result when platform capability or
   confirmation is unavailable.

## Non-Functional Requirements

- **Fail-safe:** uncertainty leaves the episode as a draft or reports unknown;
  it never produces a success-shaped fallback.
- **Least privilege:** video authorization is independent from audio
  authorization.
- **Auditability:** operators can correlate upload, target ID, authorization,
  mutation, and terminal evidence.
- **Compatibility:** existing draft-only video upload behavior is unchanged
  unless both new gates explicitly allow promotion.
- **Security:** browser-session credentials and signed URLs are never logged.
- **Reliability:** ambiguous outcomes are verified before retry and never cause
  re-upload.

## Acceptance Criteria

- With video intent omitted or `draft`, no promote request occurs and the
  terminal state is `draft_gate_denied`.
- With `SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH` omitted, false, or malformed, no
  promote request occurs.
- `SPOTIFY_ALLOW_LIVE_PUBLISH=true` alone cannot authorize video promotion.
- `publish_mode: live` from the current audio caller cannot authorize video
  promotion.
- Each W35 protected ID is blocked before any Spotify mutation even when all
  live gates are enabled.
- Passing the paired audio ID is rejected before any Spotify mutation.
- Promotion uses only the exact video ID returned by the current successful
  upload and cannot create or upload another episode.
- An already-confirmed live video converges on `already_published` without a
  blind repeat POST.
- An HTTP success without provider/public-artifact confirmation does not report
  `published`.
- A confirmed publication persists a terminal event containing
  `isPublished=true`, the public Spotify episode URL, video identity, and job
  correlation.
- A failed capability canary leaves production automation disabled and produces
  the documented manual handoff.
- The W37 runbook publishes only video `125401976`; audio `125398950`, YouTube
  `un0L2oVBpBI`, and all W35 IDs remain unchanged.

## Rollout

1. **Specification:** approve this PRD and retain draft-only production
   behavior.
2. **Implementation behind disabled gates:** add the promote operation,
   protected-ID enforcement, state verification, telemetry, and tests. Keep
   `SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH=false`.
3. **Controlled canary:** manually approve one newly uploaded, non-protected
   video draft and collect the capability evidence defined above.
4. **Decision gate:** enable production authorization only if the canary proves
   compatible publication and safe retry behavior. Otherwise close the
   automation path as unsupported and retain manual handoff.
5. **Operational monitoring:** review terminal events for the first production
   releases and immediately disable the video gate on provider behavior drift.

## Risks

| Risk | Mitigation |
|------|------------|
| Unofficial API changes or rejects video drafts | Disabled-by-default capability gate and exact manual fallback |
| Audio/video ID confusion | Same-job video-result binding, explicit audio-ID rejection, video identity confirmation |
| Historical W35 publication | Immutable repository-owned denylist checked before any mutation |
| Duplicate episode creation | Promote path cannot create or upload and never searches for a target |
| Ambiguous timeout causes repeat publication | State read-back before retry; no assumed endpoint idempotency |
| False success from a 2xx response | Require `isPublished=true`, a public Spotify URL, and video identity |
| Audio policy silently enables video | Separate `SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH` gate and video-specific intent |

## References

- `docs/spotify-video-upload.md`
- `podcaster/publish.py`
  - `upload_video_to_episode()`
  - `_publish_episode_live()`
- `podcaster/video/distribution.py`
  - `upload_to_spotify_episode()`
- #663 — infrastructure wiring for the audio-only
  `SPOTIFY_ALLOW_LIVE_PUBLISH` gate
- `jmservera/SquadScope#743` — protected podcast auto-dispatch PRD
- `jmservera/SquadScope#746` — caller audio `publish_mode: live`
- [Spotify: Publishing a saved episode](https://support.spotify.com/tv/creators/article/publishing-a-saved-episode/)
- [Spotify video specifications](https://support.spotify.com/us/creators/article/video-specs/)
