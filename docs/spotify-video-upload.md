# Spotify Video Pipeline — Working Specs & Rollback Reference

> **Status:** Proven working (June 2026). This document is the **rollback
> reference** for the complete video podcast pipeline — from screen recording
> through Spotify upload. If a future change breaks video, revert to the
> known-good state below and consult these specs.
>
> **Known-good commit:** [`5a7abbc`](https://github.com/jmservera/SquadScope-Podcaster/commit/5a7abbc)
> — *"Fix #355: Video composition sync to audio, fit-to-window trim, and
> recording quality (#356)"*. If video regresses, this is the commit to roll
> back to (or diff against). The Spotify upload API behaviour was last validated
> at `c3b4ba4`/`5a7abbc`.

All code paths cited below live in `podcaster/video/` (composition, recording,
distribution) and `podcaster/publish.py` (Spotify upload API). Constants are in
`podcaster/video/video_compose.py`.

---

## 1. Pipeline Flow

The end-to-end flow that turns recorded repo footage into a Spotify-ready MP4:

```
record → normalize → fit-to-window → pairwise compose → join (intro/content/outro)
       → audio overlay → h264_metadata BSF → upload
```

| Stage | Where | What happens |
|-------|-------|--------------|
| **record** | `video_gen.record_episode` | Captures each repo page (and generic segments) at 1920×1080. **Default (issue #387): screenshot/hyperframe mode** — sequential lossless PNG screenshots taken while scrolling are composed by ffmpeg into an H.264 `.mp4` segment (`-framerate {SCREENSHOT_CAPTURE_FPS}`), giving pixel-perfect quality with no VP8 capture-time artefacts. Set `VIDEO_SCREENSHOT_CAPTURE=false` to fall back to the legacy Playwright screencast (1920×1080 WebM via `record_video_size`/`viewport`). |
| **(sync)** | `video_compose.build_sync_map` / `apply_sync` | Optional: matches recordings to the timed episode plan and stream-copy trims overlong recordings to their target window (`-c copy`, no re-encode). |
| **normalize / fit** | `_build_normalize_cmd` / `_build_fit_segment_cmd` | Each segment is scaled+padded to 1920×1080@30fps, bt709, yuv420p. When `audio_duration` is known, segments are **fit** to exact target durations (trim or freeze-extend) — see §4. |
| **pairwise compose** | `_compose_pairwise` / `_build_xfade_step_cmd` | Segments are crossfaded **two at a time** (`xfade`), accumulating into one video. Pairwise (not N-input `filter_complex`) keeps memory constant — the old N-input filter OOMed at ~18 segments. Lower-thirds and the DOG watermark are baked in here. |
| **join bookends** | `_join_intro_outro` | Intro and outro clips (fetched from blob storage, cached) are canonicalized to a uniform video-only AV layout and concat-demuxed as `intro → content → outro`. Their source audio is always stripped. |
| **audio overlay** | `_build_audio_overlay_cmd` | The podcast MP3 is re-encoded to AAC and mapped as the **sole** audio track across the whole joined video. Never `-shortest` (outro audio must play in full). Video/audio duration are reconciled here — see §6. |
| **h264_metadata BSF** | `_build_h264_metadata_cmd` | Final stream-copy pass rewriting H.264 VUI colour metadata to one consistent BT.709 set. Always runs — see §3. |
| **upload** | `publish.upload_video_to_episode` via `distribution.upload_to_spotify_episode` | Multipart chunked upload to Spotify/GCS as a **new** draft episode — see §5. |

The orchestration is `compose_video()`; the output is always `output_path`,
written only by the final BSF pass (which requires a distinct input/output file,
so the composed content is never written directly to `output_path`).

### Canonical output format (constants)

| Constant | Value | Notes |
|----------|-------|-------|
| `OUTPUT_WIDTH × OUTPUT_HEIGHT` | `1920 × 1080` | 1080p |
| `OUTPUT_FPS` | `30` | |
| `ENCODE_PRESET` | `slow` | final encodes; intermediates use `ultrafast` |
| `ENCODE_CRF` | `18` | |
| `ENCODE_PIX_FMT` | `yuv420p` | |
| `ENCODE_AUDIO_BITRATE` | `192k` | |
| `CONCAT_AUDIO_SAMPLE_RATE / CHANNELS` | `48000` / `2` | stereo AAC |
| `TRANSITION_DURATION` | `1.0 s` | one xfade per boundary |
| `MIN_CONTENT_WINDOW_SECONDS` | `1.0` | fit-to-window floor |
| `OUTRO_VIDEO_FADE_SECONDS` | `2.0` | fade-to-black when video is freeze-extended |
| `INTRO_BLOB_PATH / OUTRO_BLOB_PATH` | `assets/video/intro.mp4` / `assets/video/outro.mp4` | |

Every encode pass (`_build_normalize_cmd`, `_build_fit_segment_cmd`,
`_build_canonical_av_cmd`, `_encode_tail`/`_BT709_FLAGS`,
`_build_audio_overlay_cmd`) sets the same colour flags:
`-colorspace bt709 -color_trc bt709 -color_primaries bt709 -color_range tv`.

---

## 2. Spotify Requirements

These are the server-side constraints Spotify's validation enforces. Each is
prevented by a specific stage above.

> 📄 **Official reference:** [Spotify video specs](https://support.spotify.com/us/creators/article/video-specs/)

| Requirement | Why | Enforced by |
|-------------|-----|-------------|
| **BT.709 colour, consistent across all NAL units** | Mixed/inconsistent SPS VUI colour → `INCONSISTENT_COLOR_DETAILS`. | Every encode sets bt709 flags **and** the final `h264_metadata` BSF normalises VUI (§3). |
| **Audio duration ≥ video duration** | If video outlasts audio → `VIDEO_DURATION_LONGER_THAN_AUDIO`. | `_build_audio_overlay_cmd` pads audio with `apad=whole_dur` to the video length (§6). |
| **Audio not longer than video** (legacy audio-on-video flow) | Older constraint: `AUDIO_DURATION_LONGER_THAN_VIDEO`. | When audio outlasts video, the final frame is freeze-extended (`tpad=stop_mode=clone`) + fade-to-black, so video ≥ audio. |
| **Keyframes / regular GOP** | Spotify needs seekable keyframes; clips are re-encoded with libx264 defaults (regular GOP/IDR cadence at 30fps). | All re-encodes use `libx264` (not stream-copy of arbitrary GOPs); the BSF pass is copy-only and preserves the encoder's keyframes. |
| **PTS offset / monotonic timestamps** | Crossfades and concat must not produce negative/overlapping PTS. | `xfade` uses an explicit `offset = cumulative − transition_duration` per pass; `tpad` extends PTS forward; `-movflags +faststart` moves the moov atom to the front. |
| **MP4 H.264 + stereo AAC, faststart** | Required container/codec. | Canonical encode (yuv420p H.264 + 48 kHz stereo AAC) with `-movflags +faststart`. |
| **File size / chunking** | Large files must be multipart. | 30 MB chunks; `numParts = ceil(filesize / 30 MB)` (§5). |

> **PTS offset detail:** in `_compose_pairwise`, each xfade pass sets
> `offset = cumulative - transition_duration`, where `cumulative` accumulates
> `durations[i] - transition_duration`. This places each crossfade exactly one
> transition-length before the running end of the accumulator, keeping the
> composed timeline's presentation timestamps continuous.

---

## 3. h264_metadata Bitstream Filter

**Command** (`_build_h264_metadata_cmd`, stream-copy — no re-encode):

```bash
ffmpeg -hide_banner -loglevel warning -y \
  -i pre_final.mp4 \
  -c:v copy \
  -bsf:v h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0 \
  -c:a copy \
  -movflags +faststart \
  output.mp4
```

**Why it is required (issue #353):** The concat demuxer copies H.264 NAL units
from independently-encoded clips (intro, content, outro) whose SPS VUI colour
data can disagree — even when every encode passed identical bt709 flags, the
muxed stream can carry inconsistent `colour_primaries` /
`transfer_characteristics` / `matrix_coefficients`. Spotify's validator then
fails with `INCONSISTENT_COLOR_DETAILS`.

The `h264_metadata` BSF rewrites those three VUI fields to BT.709 (value `1`)
and sets `video_full_range_flag=0` (limited/`tv` range) **in the bitstream
itself**, without re-encoding. This guarantees a single consistent colour
description across the whole file. The pass **always runs** as the last step of
`compose_video`, which is why composition never writes directly to
`output_path` (the BSF needs a distinct input and output file).

---

## 4. Fit-to-Window Logic

When `compose_video` receives a positive `audio_duration`, content segments are
**fit to the audio timeline** (issue #355) so the right repo is on screen while
the hosts discuss it, and the intro/outro bumpers always play in full.

### Formulas

```
content_window = max(audio_duration − intro_duration − outro_duration,
                     MIN_CONTENT_WINDOW_SECONDS)         # 1.0 s floor

# Because adjacent segments overlap by one transition per boundary, the
# per-segment durations must sum to slightly MORE than the window:
overlap_total = transition_duration × (n − 1)
target_sum    = content_window + overlap_total

# Proportional scaling preserves each segment's share of the timeline:
scale     = target_sum / sum(plan_durations)
scaled[i] = plan_durations[i] × scale

# Each xfade boundary needs both clips strictly longer than the transition:
floor     = transition_duration + 0.5
floored[i]= max(scaled[i], floor)

# Flooring can overshoot target_sum; redistribute the excess across the
# headroom each segment has above `floor` so the sum returns to target_sum:
headroom[i] = floored[i] − floor
floored[i] -= excess × headroom[i] / sum(headroom)      # when feasible
```

(`_fit_target_durations` in `video_compose.py`.)

### Per-segment fitting (`_build_fit_segment_cmd`)

Each segment is forced to its exact `target_duration` in a single pass:

* **Source longer than target** → trimmed by `-t target`.
* **Source shorter than target** → its final frame is held
  (`tpad=stop_mode=clone:stop_duration={target}`) and then cut to `-t target`.

`tpad` appends up to `target` extra seconds of cloned frames *after* the source
ends, so `-t target` always has enough material regardless of how short the
recording is — one pass that both trims and freeze-extends without probing the
source length.

The composed content video duration is
`sum(durations) − transition_duration × (n − 1)`, i.e. exactly
`content_window`; `_join_intro_outro` then adds the real intro/outro lengths
back so the final video matches the audio.

---

## 5. Upload Flow

> **Status:** Proven working (June 2026). Documented from reverse-engineering the
> Spotify for Creators web app and validated with real uploads. In the engine
> this is `podcaster.publish.upload_video_to_episode`, invoked by
> `distribution.upload_to_spotify_episode` when `VIDEO_SPOTIFY_UPLOAD_ENABLED`.

Video podcast episodes on Spotify use a **multipart chunked upload to Google Cloud
Storage (GCS)** — different from the simpler single-PUT audio upload to S3. The
video is published as a **new, separate draft episode** (issue #340): Spotify
rejects attaching a video to an episode that already holds audio, so the audio
episode (`anchor_id`) is referenced only for logging and never modified.

The flow has these steps:

1. Create a new draft episode (never reuse the audio episode)
2. Request per-part signed URLs (`uploadType=video`, `isMultipartUpload=true`,
   `numParts = ceil(filesize / 30 MB)`)
3. Upload each **30 MB chunk** (PUT) to its GCS signed URL, collecting ETags
4. Notify Spotify that all parts are uploaded (`process_upload`)
5. Poll until server-side processing completes (`state=processed`)
6. Set episode metadata (title, description)

Chunk size is `_VIDEO_CHUNK_SIZE = 30 * 1024 * 1024` (30 MB) in
`podcaster/publish.py`.

### Reconcile before create (step 1)

Before creating the video draft, `_reconcile_or_create_draft` lists the station's
episodes and reuses an exact-title draft if one already exists, so a retry after
a mid-flight crash does not leave duplicate drafts behind (#564). The audio
episode (`anchor_id`) is always excluded from the match.

> **Invariant.** With reconcile enabled, a video publish sends at most one
> *effective* draft create per (station, title): the create POST is never
> retried blindly, and an ambiguous create is resolved against the listing
> before any further POST. Every subsequent attempt reuses the draft. The
> invariant holds against listing schemas this code understands; anything it
> cannot read fails the publish closed instead of guessing.

The current readback listing contract is the Spotify for Creators GraphQL
persisted-query endpoint:

```text
POST https://creators-graph.spotify.com/v2/graph-pq
operationName=WebGetIndexedEpisodeList
variables.showUri=spotify:show:{SPOTIFY_SHOW_ID}
variables.currentPage=1
variables.pageSize=50
variables.filter=DRAFT_EPISODES
extensions.persistedQuery.sha256Hash=da95dd0d…c9e98
x-creator-client=public-website
```

This persisted-query request and response shape was observed in one successful
read-only probe on 2026-09-23. That observation establishes the schema used by
the integration; it is not a guarantee that the endpoint is continuously
available to every deployed account or credential. The earlier request failed
because it omitted the persisted-query hash and creator client header, used
cursor variables that the operation does not accept, and sent an empty `query`
field. The observed response path is
`data.showByShowUri.episodesV2`; the index must report `COMPLETED`, and numeric
`currentPage`/`pageSize`/`totalItems`/`totalPages` metadata drives pagination.
The implementation requires the page count to equal the ceiling implied by
`totalItems` and the fixed page size, requires each page to contain exactly its
declared share of those items, requires count metadata to remain unchanged
across pages, and rejects repeated canonical episode identities. Reconciliation
defaults on and fails closed if the readback is unavailable or inconsistent.
If `PODCASTER_SPOTIFY_RECONCILE=0` disables this lookup, video draft creation
fails closed instead of blind-creating.

The older Anchor REST station listing (`GET /v3/stations/{stationId}/episodes`,
with or without `userId`) is stale for this workflow and must not be treated as
proof of absence when it errors.

A lookup that fails (HTTP error, transport error, malformed JSON, missing
identity) raises `SpotifyDraftReconcileError` and fails the publish. So does a
listing whose *schema* this code cannot read — an unknown container, incomplete
index, malformed pagination, non-array episode field, renamed title/id field, or
non-object entry. `None` ("no draft exists") is only sound when every entry of
the provider-filtered draft collection was understood and all numbered pages
were fetched. A recognised empty draft page is still a legitimate no-match. A
failed later page or a changing total-page count fails closed rather than
returning a partial list. An entry whose title is present but null is understood
as an untitled draft (no match). Entries whose id is the excluded audio anchor
are skipped before title classification. More than one reusable draft with the
exact target title is ambiguous identity and fails closed; no candidate is
selected and no create or upload follows.
#### Emergency unreconciled-create override

Default behaviour is fail-closed: no configuration, a broken listing endpoint,
nor `PODCASTER_SPOTIFY_RECONCILE=0` alone authorizes an unreconciled draft
create. During a confirmed Spotify listing outage (for example
jmservera/SquadScope-Podcaster#688), an operator may make a deliberate,
bounded availability exception by setting the separate affirmative opt-in:

```bash
PODCASTER_SPOTIFY_ALLOW_UNRECONCILED_CREATE=1
```

The override uses the repository truthy set: `1`, `true`, `yes`, or `on`
(case-insensitive after trimming). This variable is intentionally distinct from
`PODCASTER_SPOTIFY_RECONCILE=0`; existing deployments that disabled reconcile
are **not** grandfathered into blind creation. The accepted risk is that the
code cannot prove whether a same-title Spotify draft already exists, so one
duplicate draft may be created.

The override is capped by durable publication evidence, keyed by the canonical
publication identity. It requires publication evidence storage and writes
`operation=unreconciled_create_intent` before the provider mutation with
`create_provenance=blind_unreconciled`,
`snapshot_completeness=absent`, and `mutation_possibility=possible`. Once
Spotify returns the new draft id, it writes `operation=unreconciled_create`
with `provider_artifact_id` and `mutation_possibility=confirmed`. A second
container or retry for the same identity sees the existing durable claim and
fails before another create POST. Process-local state is not used as the
concurrency bound.

When used, the publisher logs a `WARNING` naming the safe publication identity
(title, audio anchor id, station id, show id, publish run id) and the reason
reconcile was bypassed. It never logs cookie, bearer-token, signed-URL, or body
values.

Operator procedure:

1. Confirm in the Spotify creator console that no draft already exists for the
   target title/week; do not use this before that manual check.
2. Set `PODCASTER_SPOTIFY_ALLOW_UNRECONCILED_CREATE` to one truthy value only
   for the single run that needs the exception (with
   `PODCASTER_SPOTIFY_RECONCILE=0` only if the listing endpoint itself is the
   known blocker).
3. After the run, remove the override and verify durable publication evidence
   contains the `unreconciled_create` marker with the expected provider
   artifact id. Cross-check the Spotify console for exactly one new draft and
   delete/resolve any unexpected duplicate before retrying.

Episode ids are read from `episodeId`, `id` and `anchorId`. Every key is
inspected, and the entry only yields an id when at least one canonical identity
alias is present with a valid non-boolean identifier; all aliases that are
present must be valid non-boolean identifiers and agree on one value. A
malformed, boolean, or conflicting alias is a contradictory identity: the entry
is treated as having no usable id (logged, never silent), which every caller
already handles fail-closed.

##### Draft state is read from evidence, never from truthiness

For the entry that *matches the target title*, the draft/published state must be
established explicitly, because both possible guesses are damaging: guessing
"draft" reuses (and overwrites) a live episode, guessing "not a draft" creates a
duplicate.

| Field | Accepted | Rejected |
|-------|----------|----------|
| `isDraft` | JSON `true`/`false` (`true` ⇒ draft) | any non-boolean: `"false"`, `"true"`, `0`, `1`, `1.0`, `{}`, `[]` |
| `isPublished` | JSON `true`/`false`. `true` ⇒ **not** a draft; `false` alone is **not** evidence of a draft and needs a corroborating `isDraft`/status signal | any non-boolean |
| `status` / `state` / `publishStatus` / `publishState` | `"draft"`, `"published"` (trimmed, case-insensitive). `"scheduled"` and `"unpublished"` are understood only as non-public, never as reusable-draft evidence | any other token, and any non-string |

`isPublished` is asymmetric on purpose: it is the field this integration itself
writes, so `true` reliably means "not a draft", but `false` only means "not
published" — a scheduled, processing or errored episode is unpublished without
being a draft, and reusing one as the video draft would overwrite it. An entry
whose *only* state signal is `isPublished: false` therefore fails closed.

`bool("false")` is `True`, so a string is *never* truth-tested — it is schema
drift. A non-public token (`"scheduled"` or `"unpublished"`) does not permit
reuse without explicit draft evidence. An **unknown** token (`"processing"`,
anything a future API version invents) is an error, not an implied state; the
allow-list is deliberately minimal and is only extended from observed evidence.
An explicit `null` carries no state and is skipped, exactly like an absent
field; if nothing is left, or if two fields disagree, the entry fails closed.
Entries whose title does **not** match are never state-checked.

The live query requests `DRAFT_EPISODES`, so an item without an individual state
field is draft evidence only after the exact response path, completed index, and
pagination contract have been validated. If Spotify later includes explicit
state fields, contradictory or unknown values still fail closed. Verification is
limited to this strict persisted-query request and envelope; aliases, alternate
nesting, arrays, REST response shapes, omitted identities, and undocumented
state values remain intentionally unsupported rather than being guessed.

#### Titling the new draft immediately (idempotency)

`_create_episode` posts `{"hourOffset": 0}` and Spotify returns an **untitled**
draft; the title is only applied by the final `_set_metadata` call, minutes later
(signed URLs → multipart upload → processing poll). A crash anywhere in that
window left a draft that reconcile — which matches on title — could never find,
so the next attempt created another one. That was the dominant duplicate window.

`_claim_draft_title` now titles the new draft with the *same*
`/v3/episodes/{id}/update` request the final metadata step already uses (no new
or guessed fields) before any upload begins, narrowing the window to a single
request. It sends the **real** title, description and numbering that the final
metadata call would apply anyway — not an empty description — because that
endpoint always sends a `description`, so claiming with `""` would clear
metadata rather than only add a title. If the claim fails the publish aborts
before uploading and names the orphan draft id so an operator can delete it.

Only drafts known to be *untitled* are claimed: `_reconcile_or_create_draft`
returns `(anchor_id, needs_title)` and `needs_title` is `False` for a
reconciled draft **and** for a draft adopted during ambiguous-create recovery
because it already carried the target title. An authorized unreconciled create
is also claimed immediately before upload; `PODCASTER_SPOTIFY_RECONCILE=0`
alone fails before create.

#### The create POST is never retried blindly

`POST /v3/stations/{id}/episodes` is state-mutating and the Anchor v5 API
exposes no idempotency key, so a 408/429/5xx/timeout is indistinguishable from
"the draft was created and the response was lost". The generic
retry-with-backoff must therefore not be applied to it: three attempts could
leave two extra *untitled* drafts, which title-based reconcile can never find or
clean up. `_create_episode` sends **exactly one** POST (`max_attempts=1`) and
raises `SpotifyDraftCreateAmbiguousError` when the outcome is unknown — either a
transient failure, or a `2xx` whose body does not yield an episode id (the draft
exists; only its identifier was lost). A deterministic `4xx` is *not* ambiguous:
nothing was created.

When reconcile is enabled, `_reconcile_or_create_draft` resolves that ambiguity
with evidence rather than a retry. The listing read that looked for an existing
draft doubles as a **pre-create snapshot** of episode ids (no extra request), and
after an ambiguous create the listing is re-read:

| Evidence in the re-listing | Action | Create POSTs sent |
|---|---|---|
| A draft now carries the target title | reuse it as-is (already titled, so it is *not* re-claimed) | 1 |
| Exactly one *new* untitled draft, no unclassifiable entries | adopt it (the caller then titles it) | 1 |
| No new entry, from a snapshot that yielded an id for every entry | not yet proof — wait `_AMBIGUOUS_CREATE_SETTLE_SECONDS` and re-read; only if the settled read is *still* unchanged, send it once more | 2 |
| Several new untitled drafts, any unclassifiable entry, or an incomplete snapshot | raise, naming the candidate ids for operator cleanup | 1 |
| The re-listing itself is unreadable | raise (fail closed) | 1 |

An *immediately* unchanged listing is deliberately not treated as proof. A
client-side timeout or a reset connection says nothing about whether the server
is still committing the create, and this API offers no read-your-writes
guarantee, so a single sample taken microseconds after the failure can be stale.
The listing is therefore read `_AMBIGUOUS_CREATE_READS` (2) times, spaced by a
bounded settling delay, before a second POST is even considered; if the settled
read surfaces a draft it is adopted instead. Evidence that is *already*
ambiguous (several candidates, unclassifiable entries, an unusable snapshot)
skips the settling read and fails closed immediately, because waiting cannot
make it provable.

At most **two** create POSTs are ever sent for one publish attempt, and the
second only after a settled, twice-observed listing that still shows nothing the
first create could have produced. A second ambiguous create is not recovered
again. With `PODCASTER_SPOTIFY_RECONCILE=0` (and on the audio path in
`publish_episode`, which never reconciles) there is no listing to reason from,
so video create fails before any POST unless the durable emergency override is
set. The audio path remains a single non-reconciled POST.

Residual, irreducible windows — stated precisely, because neither one loses the
draft server-side:

1. **Client dies between the POST and the recovery listing** (SIGKILL, node
   loss). The server may well hold a created draft and its id; it is only the
   *client* that never observed the id, so this process can no longer act on it.
   The draft is untitled, so a later attempt cannot match it by title. It is
   still visible in the listing, so the next attempt sees it as a pre-existing
   untitled entry: it is in that run's pre-create snapshot, so it is never
   adopted as evidence of that run's own create, and it stays as an orphan for
   an operator to delete from the creator UI.
2. **Create succeeds after the recovery gave up.** If the settled re-reads never
   showed the draft and a second POST was sent, a late-committing first create
   can still land, leaving two untitled drafts. Both are then untitled orphans
   — the next attempt's snapshot contains both, so neither is mistaken for its
   own create, and the run either adopts nothing or fails closed naming them.

Neither window silently corrupts a *published* episode, and neither is closable
client-side: the Anchor v5 API exposes no idempotency key. What is closed is the
common case — a crash during the multi-minute upload — because the draft is
titled before the upload starts and reconcile finds it on the next run.

Later upload, processing or metadata failures are not claimed as a zero-risk
window. On the video path with reconciliation enabled, create intent evidence
is written only after the listing has proven there is no reusable draft and
immediately before a create POST is attempted. That intent records the
pre-create episode-id snapshot and is explicitly non-terminal
(`publication_unknown`, `retry_blocked=false`): if credentials expire while the
create outcome is unknown, a later run reuses the snapshot to adopt exactly one
new untitled draft, persist its provider id, title it, and continue without a
duplicate create. If the snapshot is incomplete, the follow-up listing is
unreadable, or more than one candidate appears, the retry fails closed for
manual cleanup instead of guessing. With the unreconciled-create override, the
intent is written as `unreconciled_create_intent` with an absent snapshot and
possible mutation state; no listing-based absence proof is claimed, and the
durable claim blocks a second create for the same publication identity if the
process dies before the provider id is recorded. Once this client has observed
a video draft id — from a normal create response, from recovery of an ambiguous
create, from a later run resolving a durable create intent, or from an
authorized unreconciled create — the provider id is durably written as
`create_episode`/`reconcile_episode`/`unreconciled_create` evidence before
upload work continues when publication evidence storage is available. If that
write fails, the call fails closed with
`publication_unknown`, `retry_blocked=true`, and the observed
`anchor_episode_id`. Subsequent failures after that point also return
`publication_unknown` with `retry_blocked=true` and the observed id. That
preserves duplicate safety by forcing reconciliation/manual handoff instead of
presenting the failed call as "no draft was created"; the remaining risk is
operational recovery of a known draft or of an ambiguous candidate set, not
blind re-create permission.

A *definite* rejection of the create POST itself is different from an unknown
outcome, because it proves no draft was created. A 401 from `_create_episode`
writes `create_episode_failure` with `code=credentials_expired`,
`retry_blocked=false` (outcome `manual_handoff_required`), which re-arms exactly
one corrected-credential create for the identity (#693). A deterministic 4xx
writes `create_episode_failure` with `code=create_rejected`, `retry_blocked=true`,
because the same request would be rejected again. Only these two codes resolve a
pending create intent. A resolved intent is never used for adoption, so an
unrelated untitled draft that appears later is not uploaded onto. If the rejection
record cannot be written, a retry-blocking `create_episode_failure_fence` is
attempted instead. If both writes fail, the intent stays unknown: retries reconcile
read-only and never create. Failures raised while recovering an ambiguous create,
or while resolving an earlier intent, stay unknown (`publication_unknown`,
`retry_blocked=false`, never "failed").

With `PODCASTER_SPOTIFY_RECONCILE=0` there is no listing check, so only a
definite rejection of the create that is written with `retry_blocked=false`
(today only `create_episode_failure` with `code=credentials_expired`) re-arms a
create without the override. Any other `retry_blocked=false` record, such as an
unknown outcome, fails closed as `unreconciled_create_not_authorized`.

When `publish_episode` routes an MP4 through this create-safe video flow, a
live `publish_mode` (`immediate`/`scheduled`, with `SPOTIFY_ALLOW_LIVE_PUBLISH`)
is applied by the final `/update` (`isPublished`/`publishOn`). The outcome is
then classified from `/overview` readback, exactly as on the audio path (#700),
and recorded as `publish`. An ambiguous `/update` failure that readback does not
confirm as live is recorded as `provider_mutation_failure` (`uploaded` or
`publication_unknown`, retry-blocked). The result is never reported as published
without readback confirmation.

Persisted create-safety fields (`create_provenance`, `snapshot_completeness`,
`mutation_possibility`, `snapshot_evidence_source`) are parsed through one
closed-set parser that accepts only an exact string naming a member. A present
field that is non-string, unknown, or carries look-alike or invisible characters
fails closed as an unresolved intent. Each intent operation accepts only its own
provenance (`create_episode_intent`: `reconciliation_backed` or
`upload_dispatch`; `unreconciled_create_intent`: `blind_unreconciled`;
`upload_intent`: `upload_dispatch`). One exception: an untrusted
`snapshot_evidence_source` on a present snapshot degrades that snapshot to
`absent`, with a single `WARNING`, and the intent stays blocking. An explicit
`absent` snapshot that still carries observed-snapshot fields
(`pre_create_episode_ids`, `pre_create_snapshot_complete`,
`snapshot_evidence_source`) is degraded the same way. A degraded state is
re-persisted with `snapshot_degraded: true`, so a deserialize/serialize cycle
keeps it blocking; a non-boolean marker, or one on a non-absent snapshot, fails
closed. Snapshot episode ids must be exact integers and are never coerced
(`1.5` is rejected, not read as `1`). A present `details` value that is not an
object fails closed; only an omitted or `null` `details` reads as a legacy
intent. Identity fields in these warnings are capped at 80 characters after
escaping. A non-reconciliation (`upload_dispatch`) create intent blocks only while it
is still the pending intent. A later provider id or definite rejection resolves
it, so repeated #693 credential re-arms each allow exactly one create. Observed-
snapshot fragments (`pre_create_episode_ids`, `snapshot_evidence_source`) with
no completeness claim are degraded and blocking, and a legacy
`pre_create_snapshot_complete` record that also carries a
`snapshot_evidence_source` is rejected. With reconciliation disabled, a definite
rejection re-arms a create only if a create claim precedes it for the same
identity.

#### Pagination

The production GraphQL listing uses numbered pages. `_fetch_episode_listing`
requests `currentPage=1` with the fixed `pageSize=50`, validates
`currentPage`/`pageSize`/`totalItems`/`totalPages`, and increments
`currentPage` until the declared final page. Every page must contain exactly
the share implied by the stable count metadata. Empty pages with remaining
items, contradictory totals/page counts, changed metadata, repeated canonical
episode identities, or any page-fetch error raise
`SpotifyDraftReconcileError`; a partial or identity-ambiguous read is never
returned as a complete empty listing.

#### Credential expiry

A 401 in the video path raises `SpotifyCredentialExpiredError`,
which `upload_video_to_episode` converts into an operator credential-expiry
notification (`notify_credential_expiry`, #364) and a result carrying
`details.credentials_expired` — the same handling the audio publish path has.

### Upload API reference (detailed)

> The subsections below are the low-level Spotify/Anchor API reference (the
> exact HTTP calls, headers, and a standalone working example) used by
> `podcaster/publish.py`. They are preserved verbatim as the authoritative
> protocol documentation.

#### Prerequisites

- A valid `sp_dc` cookie (from `https://creators.spotify.com`)
- The show's `webId` (a base62 ID like `033xdn5nDMoCWxB3bss2dB`)
- A video file (MP4, H.264 + AAC, ≤ ~200MB practical, `faststart` recommended)

#### Critical Constraints

##### Audio duration MUST be ≤ video duration

Spotify's server-side validation rejects videos where the audio stream is longer
than the video stream with error:

```
AUDIO_DURATION_LONGER_THAN_VIDEO (FAILURE_TYPE_MAL_REJECTION)
```

**Fix before upload:**

```bash
# Get video stream duration
VIDEO_DUR=$(ffprobe -v error -select_streams v:0 \
  -show_entries stream=duration -of csv=p=0 input.mp4)

# Re-encode audio trimmed to video duration
ffmpeg -y -i input.mp4 \
  -c:v copy \
  -c:a aac -ac 2 -b:a 128k \
  -t "$VIDEO_DUR" \
  -movflags +faststart \
  output.mp4
```

##### Video MUST go to GCS (not S3)

Without `uploadType=video` in the signedUrl request, the server routes the file
to S3 storage. Even if the upload succeeds, `process_upload` will reject it with:

```
"File is using invalid storage"
```

##### Multipart format is required

Even for files smaller than one chunk, the server expects the multipart flow
(`isMultipartUpload=true&numParts=1`). A direct single-PUT to the signed URL
will result in `process_upload` returning HTTP 500.

#### Step-by-Step Flow

##### Step 1: Resolve legacy IDs

```http
GET https://api-v5.anchor.fm/v3/profile/station/webStationId/{WEB_ID}
Cookie: sp_dc={SP_DC}; sp_key={SP_KEY}
```

Response (extract `stationId` and `userId`):
```json
{
  "station": { "stationId": "46150077", "userId": "46104253", ... }
}
```

##### Step 2: Create a draft episode

```http
POST https://api-v5.anchor.fm/v3/episodes
Cookie: sp_dc=...; sp_key=...
Content-Type: application/json
Origin: https://creators.spotify.com
Referer: https://creators.spotify.com/

{"stationId": "46150077", "title": "Untitled"}
```

Response:
```json
{"episodeId": 1234567890}
```

The `episodeId` is called `anchor_id` in subsequent requests.

##### Step 3: Request multipart signed URLs

```http
GET https://api-v5.anchor.fm/v3/episodes/{ANCHOR_ID}/upload/signedUrl
  ?filename=episode.mp4
  &type=video/mp4
  &isMumsCompatible=true
  &isMultipartUpload=true
  &numParts=2
  &uploadType=video
Cookie: sp_dc=...; sp_key=...
```

**Parameters:**
| Param | Required | Description |
|-------|----------|-------------|
| `filename` | Yes | Original filename |
| `type` | Yes | MIME type (`video/mp4`) |
| `isMumsCompatible` | Yes | Always `true` |
| `isMultipartUpload` | Yes | Must be `true` for video |
| `numParts` | Yes | Number of chunks (ceil(filesize / chunk_size)) |
| `uploadType` | Yes | Must be `video` — routes to GCS |

**Response:**
```json
{
  "requestUuid": "a1b2c3d4-e5f6-...",
  "signedUrlParts": [
    {"partNumber": 1, "url": "https://storage.googleapis.com/anchor-audio-upload/...?X-Goog-Signature=..."},
    {"partNumber": 2, "url": "https://storage.googleapis.com/anchor-audio-upload/...?X-Goog-Signature=..."}
  ],
  "fileKey": "anchor-audio-upload/...",
  "signedUrl": "https://storage.googleapis.com/..."
}
```

> **Note:** The response uses S3-era field names (`requestUuid`, `signedUrl`) even
> though storage is GCS. Use `signedUrlParts` for the actual upload — ignore
> `signedUrl` (it's the base URL without part suffixes).

##### Step 4: Upload each chunk

Split the file into chunks and PUT each one to its corresponding `signedUrlParts[i].url`:

```http
PUT {signedUrlParts[0].url}
Referer: https://creators.spotify.com/
Content-Length: 31457280

<binary chunk data>
```

**Headers:**
- `Referer: https://creators.spotify.com/` — required
- Do **NOT** send `Content-Type`, `Origin`, or `Authorization`
- The GCS signed URL has `X-Goog-SignedHeaders=host` (only host is verified)

**Response:** HTTP 200 with `ETag` header — save this for each part.

**Chunk size:** ~30MB recommended (browser uses this). Minimum 5MB except for the
last chunk.

##### Step 5: Notify upload complete (`process_upload`)

```http
POST https://api-v5.anchor.fm/v3/upload/{REQUEST_UUID}/process_upload
  ?isMumsCompatible=true
Cookie: sp_dc=...; sp_key=...
Content-Type: application/json
Origin: https://creators.spotify.com
Referer: https://creators.spotify.com/

{
  "userId": 46104253,
  "uploadType": "video",
  "origin": "episode-media:upload",
  "caption": "episode.mp4",
  "isExtractedFromVideo": true,
  "isMultipartUpload": true,
  "parts": [
    {"partNumber": 1, "etag": "abc123..."},
    {"partNumber": 2, "etag": "def456..."}
  ],
  "uploadId": "a1b2c3d4-e5f6-...",
  "episodeId": 1234567890,
  "stationId": 46150077
}
```

**Response:** HTTP 200 (no meaningful body — processing is async).

##### Step 6: Poll for processing completion

```http
GET https://api-v5.anchor.fm/v3/upload/media/{REQUEST_UUID}
  ?includeMediaValidation=true
  &isMumsCompatible=true
Cookie: sp_dc=...; sp_key=...
```

**Response (processing):**
```json
{
  "request": {
    "state": "uploaded",
    "requestUuid": "a1b2c3d4-..."
  }
}
```

**Response (success):**
```json
{
  "request": {
    "state": "processed",
    "requestUuid": "a1b2c3d4-..."
  },
  "mediaValidation": {
    "status": "validation_success"
  }
}
```

**Response (failure):**
```json
{
  "request": {
    "state": "failed",
    "failureReason": ""
  },
  "mediaValidation": {
    "status": "validation_failure",
    "failures": [
      {"reason": "AUDIO_DURATION_LONGER_THAN_VIDEO", "type": "FAILURE_TYPE_MAL_REJECTION"}
    ]
  }
}
```

**Terminal states:** `processed` = success, `failed` = failure.

> **Known quirk:** The poll endpoint sometimes returns HTTP 404. This is transient
> — retry with exponential backoff (up to 300s total wait). The media record
> appears within a few seconds.

#### Complete Working Python Example

```python
"""
Spotify Video Podcast Upload — Complete Working Example

Requires: requests, python-dotenv (optional)
Environment: SP_DC, SP_KEY, SPOTIFY_SHOW_ID
"""

import math
import os
import time
from pathlib import Path

import requests

BASE_URL = "https://api-v5.anchor.fm"
CHUNK_SIZE = 30 * 1024 * 1024  # 30MB per chunk


def upload_video_episode(
    video_path: str,
    title: str = "Untitled",
    sp_dc: str | None = None,
    sp_key: str | None = None,
    show_id: str | None = None,
) -> dict:
    """Upload a video file as a Spotify podcast episode draft.

    Returns dict with episode info on success, raises on failure.
    """
    sp_dc = sp_dc or os.environ["SP_DC"]
    sp_key = sp_key or os.environ["SP_KEY"]
    show_id = show_id or os.environ["SPOTIFY_SHOW_ID"]
    video_file = Path(video_path)
    video_data = video_file.read_bytes()
    file_size = len(video_data)
    num_parts = max(1, math.ceil(file_size / CHUNK_SIZE))

    # Build session
    session = requests.Session()
    session.cookies.set("sp_dc", sp_dc)
    session.cookies.set("sp_key", sp_key)
    session.headers.update({
        "Origin": "https://creators.spotify.com",
        "Referer": "https://creators.spotify.com/",
    })

    # 1. Resolve station/user IDs
    resp = session.get(f"{BASE_URL}/v3/profile/station/webStationId/{show_id}")
    resp.raise_for_status()
    station = resp.json()["station"]
    station_id = int(station["stationId"])
    user_id = int(station["userId"])
    print(f"Station: {station_id}, User: {user_id}")

    # 2. Create draft episode
    resp = session.post(
        f"{BASE_URL}/v3/episodes",
        json={"stationId": str(station_id), "title": title},
    )
    resp.raise_for_status()
    anchor_id = resp.json()["episodeId"]
    print(f"Created draft episode: {anchor_id}")

    # 3. Get multipart signed URLs
    resp = session.get(
        f"{BASE_URL}/v3/episodes/{anchor_id}/upload/signedUrl",
        params={
            "filename": video_file.name,
            "type": "video/mp4",
            "isMumsCompatible": "true",
            "isMultipartUpload": "true",
            "numParts": str(num_parts),
            "uploadType": "video",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    request_uuid = data["requestUuid"]
    signed_parts = data["signedUrlParts"]
    print(f"Got {len(signed_parts)} signed URLs, requestUuid={request_uuid}")

    # 4. Upload each chunk
    parts_etags = []
    for i, part in enumerate(signed_parts):
        start = i * CHUNK_SIZE
        end = min(start + CHUNK_SIZE, file_size)
        chunk = video_data[start:end]

        resp = requests.put(
            part["url"],
            data=chunk,
            headers={"Referer": "https://creators.spotify.com/"},
            timeout=300,
        )
        resp.raise_for_status()
        etag = resp.headers["ETag"].strip('"')
        parts_etags.append({"partNumber": part["partNumber"], "etag": etag})
        print(f"  Part {part['partNumber']}/{len(signed_parts)}: "
              f"{len(chunk)} bytes, ETag={etag[:12]}...")

    # 5. Process upload (notify all parts uploaded)
    resp = session.post(
        f"{BASE_URL}/v3/upload/{request_uuid}/process_upload",
        params={"isMumsCompatible": "true"},
        json={
            "userId": user_id,
            "uploadType": "video",
            "origin": "episode-media:upload",
            "caption": video_file.name,
            "isExtractedFromVideo": True,
            "isMultipartUpload": True,
            "parts": parts_etags,
            "uploadId": request_uuid,
            "episodeId": anchor_id,
            "stationId": station_id,
        },
    )
    resp.raise_for_status()
    print("process_upload accepted, polling...")

    # 6. Poll for completion (with backoff for 404s)
    poll_url = f"{BASE_URL}/v3/upload/media/{request_uuid}"
    backoff = 3
    for attempt in range(60):  # up to ~300s
        time.sleep(backoff)
        resp = session.get(
            poll_url,
            params={
                "includeMediaValidation": "true",
                "isMumsCompatible": "true",
            },
        )
        if resp.status_code == 404:
            backoff = min(backoff * 1.5, 30)
            print(f"  Poll 404 (transient), backing off to {backoff:.0f}s...")
            continue

        resp.raise_for_status()
        result = resp.json()
        req_data = result.get("request", result)
        state = req_data.get("state", "")

        if state == "processed":
            validation = result.get("mediaValidation", {})
            print(f"✅ Upload processed! Validation: {validation.get('status')}")
            return {
                "anchor_id": anchor_id,
                "request_uuid": request_uuid,
                "state": state,
                "validation": validation.get("status"),
            }
        elif state == "failed":
            validation = result.get("mediaValidation", {})
            failures = validation.get("failures", [])
            raise RuntimeError(
                f"Upload failed: {req_data.get('failureReason')} "
                f"Validation: {[f.get('reason') for f in failures]}"
            )
        else:
            backoff = 3  # reset on non-404
            print(f"  State: {state} (attempt {attempt + 1})")

    raise TimeoutError("Upload processing timed out after 300s")


if __name__ == "__main__":
    import sys

    video = sys.argv[1] if len(sys.argv) > 1 else "podcast_video.mp4"
    result = upload_video_episode(video, title="Test Video Episode")
    print(f"\nResult: {result}")
```

#### Known Quirks

| Quirk | Workaround |
|-------|------------|
| Poll returns 404 | Transient; retry with exponential backoff up to 300s |
| `failureReason` is empty string on validation failures | Check `mediaValidation.failures[]` for the real reason |
| `signedUrl` in response is not usable for multipart | Use `signedUrlParts[].url` instead |
| Response field names are S3-era (`requestUuid` not `uploadId`) | Handle both: `data.get("uploadId") or data["requestUuid"]` |
| Audio longer than video by even 0.01s → rejection | Always trim audio to exact video duration before upload |
| Files must use GCS for video | Always pass `uploadType=video` in signedUrl request |
| `state=processed` (not `completed`) is success for video | Check both states for compatibility |

#### Audio vs Video Upload Comparison

| Aspect | Audio (MP3) | Video (MP4) |
|--------|-------------|-------------|
| Storage | S3 | GCS |
| Upload method | Single PUT | Multipart chunked |
| `uploadType` param | (omitted) | `video` |
| `isMultipartUpload` | `false` or omitted | `true` |
| Signed URL response | `signedUrl` (single URL) | `signedUrlParts` (array) |
| PUT headers | Content-Type + Origin + Referer | Referer only |
| Success terminal state | `completed` | `processed` |
| `isExtractedFromVideo` | `false` | `true` |
| Max chunk size | N/A (single file) | ~30MB recommended (5MB minimum) |

#### Video File Recommendations

```bash
# Encode a video podcast-ready MP4:
ffmpeg -y -i raw_video.mp4 \
  -c:v libx264 -preset medium -crf 23 \
  -vf "scale=1280:720" \
  -c:a aac -ac 2 -b:a 128k \
  -t $(ffprobe -v error -select_streams v:0 \
       -show_entries stream=duration -of csv=p=0 raw_video.mp4) \
  -movflags +faststart \
  output.mp4
```

**Key settings:**
- **Resolution:** 720p or 1080p (720p keeps file sizes manageable)
- **Codec:** H.264 High profile + AAC stereo
- **`-movflags +faststart`:** Moves moov atom to start for streaming
- **`-t {video_duration}`:** Trims audio to match video exactly
- **Stereo audio required** (mono may work but stereo is what the web app sends)

---

## 6. Error Codes & Prevention

Spotify surfaces validation failures during the poll step (§5, Step 6). The
engine extracts the human reason from `mediaValidation.failures[].reason` **and**
the precise machine code from `mediaValidation.failureInfo.errorCode` (issue
#351 / `podcaster/publish.py` `_poll_upload_status`).

| Error code / reason | Cause | Prevented by |
|---------------------|-------|--------------|
| `INCONSISTENT_COLOR_DETAILS` | The concatenated H.264 stream carries disagreeing SPS VUI colour metadata across intro/content/outro NAL units. | (a) every encode pass sets `-colorspace/-color_trc/-color_primaries bt709 -color_range tv`; (b) the final `h264_metadata` BSF rewrites VUI to a single BT.709/limited-range set (§3). |
| `VIDEO_DURATION_LONGER_THAN_AUDIO` | The video stream outlasts the audio stream. | `_build_audio_overlay_cmd` pads the audio with `-af apad=whole_dur={video_duration}` when `0 < audio_duration < video_duration`, so audio ≥ video. |
| `AUDIO_DURATION_LONGER_THAN_VIDEO` (legacy) | The audio stream outlasts the video stream. | When `audio_duration > video_duration`, the final frame is held (`tpad=stop_mode=clone:stop_duration={pad}`) + faded to black (`fade=t=out`, `OUTRO_VIDEO_FADE_SECONDS`), extending video to ≥ audio. The outro audio is **never** truncated (no `-shortest`). |
| `"File is using invalid storage"` | The signed-URL request omitted `uploadType=video`, routing the file to S3 instead of GCS. | Always pass `uploadType=video` (and `isMultipartUpload=true`). |
| `process_upload` HTTP 500 | A single-PUT upload was used for video. | Always use the multipart flow (`numParts = ceil(filesize / 30 MB)`), even for one chunk. |

> **Audio/video duration reconciliation (the heart of error prevention).**
> `_build_audio_overlay_cmd` probes both durations and:
> - **video shorter than audio** → freeze-extend the last video frame and fade to
>   black (video stream re-encoded with bt709 flags);
> - **video longer than audio** → `apad=whole_dur` the audio with trailing silence;
> - **equal** → stream-copy the video, leave audio as-is.
>
> The output is never cut with `-shortest`, guaranteeing the outro plays fully
> while still satisfying both duration constraints above.

When a failure does occur, `SpotifyPublishError` includes the `errorCode` and
full `failureInfo` so logs pinpoint the exact rejection.

---

## 7. Known-Good Commit

See the banner at the top of this document: **`5a7abbc`**
("Fix #355: Video composition sync to audio, fit-to-window trim, and recording
quality (#356)"). Roll back to or diff against this commit if video regresses.
The Spotify multipart upload protocol (§5) was validated against real uploads at
`c3b4ba4` and remains unchanged through `5a7abbc`.

---

## 8. Environment Variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `VIDEO_SPOTIFY_UPLOAD_ENABLED` | `distribution.VideoDistributionConfig.from_env` | `"true"` enables publishing the MP4 as a new Spotify video episode draft (§5). |
| `SP_DC` | `publish._get_credentials` | Spotify `sp_dc` session cookie (auth). |
| `SP_KEY` | `publish._build_session` | Spotify `sp_key` session cookie (auth). |
| `SPOTIFY_SHOW_ID` | `publish._get_credentials` | The show's `webId` used to resolve legacy `stationId`/`userId`. |
| `PODCASTER_SPOTIFY_RECONCILE` | `publish._spotify_reconcile_enabled` | Defaults on with the observed persisted-query draft listing contract. `0`/`false`/`no`/`off` disables reconciliation and fails closed before a video create unless the separate unreconciled-create override is truthy (§5). |
| `PODCASTER_SPOTIFY_ALLOW_UNRECONCILED_CREATE` | `publish._spotify_unreconciled_create_allowed` | Emergency affirmative opt-in for one durable-evidence-keyed unreconciled video draft create. Accepts the repo truthy set (`1`/`true`/`yes`/`on`); `PODCASTER_SPOTIFY_RECONCILE=0` alone never satisfies it (§5). |
| `PODCASTER_STORAGE_ACCOUNT_URL` | `storage.py`, `video/job_runner.py` | Azure Blob storage account URL; backs intro/outro fetch, blob archive, and job manifests. |

Adjacent distribution toggles (same `from_env`): `VIDEO_YOUTUBE_ENABLED`,
`VIDEO_SPOTIFY_RSS_ENABLED`, `VIDEO_BLOB_ARCHIVE_ENABLED` (defaults `true`),
`VIDEO_DISTRIBUTE_DRY_RUN`. At least one distribution target must be enabled or
`distribute_video` aborts. `PODCASTER_VIDEO_QUEUE` selects the job queue
(default `video-jobs`).

Credentials are never hardcoded; they come from the environment (managed
identity / Key Vault in Azure). Do not commit `SP_DC`/`SP_KEY`.

---

## 9. Testing Strategy

A three-tier escalation, cheapest and fastest first:

1. **Local (unit tests)** — `pytest tests/ -q` (297+ tests). The video suites
   (`tests/test_video_compose.py`, `test_video_distribution.py`,
   `test_video_gen.py`, `test_video_intro_outro.py`, `test_video_job_runner.py`,
   `test_video_sync_plan.py`, `test_video_zoom.py`) assert on the **exact ffmpeg
   command strings** via a `FakeCommandRunner`, so the bt709 flags, the
   `h264_metadata` BSF arguments, the `tpad`/`apad` duration logic, and the
   `_fit_target_durations` maths are all verified without invoking ffmpeg. Run
   these on every change.
2. **Local (integration)** — `tests/integration/test_video_pipeline.py`
   (`pytest -m integration`) exercises `compose_video` end-to-end with a fake
   runner, asserting full pipeline ordering (normalize → compose → join → audio
   → BSF). For a real ffmpeg/render check, run the pipeline against small sample
   clips on a workstation that has system ffmpeg with libfreetype (for
   drawtext/lower-thirds).
3. **ACA (Azure Container Apps)** — deploy the synthesis/video job container and
   run a real episode so the actual ffmpeg binary, Playwright recording, blob
   intro/outro fetch, and memory behaviour (pairwise compose avoids the
   ~18-segment OOM) are validated in the production environment.
4. **GitHub Action** — `.github/workflows/integration-tests.yml` runs the
   integration suite in CI; deploy/publish workflows (`deploy-azure.yml`,
   `synthesis-image-publish.yml`) ship the validated container. A real Spotify
   upload is the final gate, validated manually against a draft episode.

> **CI must be correct, not just green:** never weaken the ffmpeg-command
> assertions or skip the colour/duration checks to make a build pass — those
> assertions are exactly what protect against the §6 Spotify rejections.

## 10. Canonical delivery state

The legacy per-platform `status: published` marker remains the at-most-once
compatibility guard, but it does not necessarily mean public availability.
Spotify video upload reports `outcome: draft_created` after the separate video
episode is uploaded and configured as a draft. Promotion reports `published`
only after state read-back; ambiguous read-back is `publication_unknown`, and a
known operator/capability stop is `manual_handoff_required`.
An overview HTTP 403 is permission/readback denial, not evidence that the
episode is unpublished or still a draft, so promotion aborts as
`publication_unknown` without sending a publish mutation.

The video publish run ID is threaded into promotion telemetry and durable
accepted-job evidence. An existing `draft_created`, `published`,
`manual_handoff_required`, or `publication_unknown` outcome blocks another
create/upload mutation. The audio anchor, protected W35 IDs, two-key live gate,
multipart behavior, quoted-ETag stripping, and per-language routing remain
unchanged.
