# RFC: Scale-out video recording (recorder/editor split) — epic #552

- **Status:** Design (DESIGN-ONLY pass — no video-pipeline code in this change)
- **Date:** 2026-06-28
- **Epic:** [jmservera/SquadScope-Podcaster#552](https://github.com/jmservera/SquadScope-Podcaster/issues/552)
- **Owner (design):** Podcaster subsquad (Coordinator-driven)
- **Gating update (2026-06-28):** The original design-only / "gated on #558/#559/#560
  first" stance is **lifted** by operator directive — implement the scale-out fan-out
  in parallel with the A/V-sync fixes to speed up every test cycle. The foundational,
  conflict-free pieces land first (clip-queue schema/codec, recorder entrypoint, KEDA
  bicep); the **editor refactor still lands LAST** and rebases on `main` after the
  A/V-sync quality fixes merge (it is the only shared touch point). The original
  sequencing below is retained for context.
- **Gated on (historical):** A/V-sync fixes #558, #559, #560 (see [Sequencing](#sequencing--gating)).

> **Concurrency note:** Another subsquad is fixing the A/V-sync pipeline (#558/#559/#560)
> on the main tree. This RFC deliberately touches **no** video-pipeline code. It reuses
> the recording/compose code those fixes harden, behind a new fan-out/fan-in seam.

> **Design vetting:** This RFC was pressure-tested with the rubber-duck agent. Findings folded in:
> manifest-as-completion-sentinel (not clip-blob); per-index `blob_exists` fan-in barrier (not
> `list_blobs`, which caps at 10); recorder-written **terminal fallback manifests** so the barrier
> always converges; a dedicated **editor lease** (since `pipeline_lock` permits same-pipeline
> re-confirm); immutable `clipset.json`; durable **per-platform** publish state with stated residual
> at-least-once risk; and queue **visibility-timeout vs replica-timeout** alignment.

## 1. Problem

Today a single ACA video Job replica records **all** repo clips serially (bounded by a
3-browser in-process pool, `recording_pool.py`) and then composes. Recording dominates
wall time (~84% of the run). In-process parallelism is capped by one replica's vCPU/RAM
(~3 Chromium contexts on a 4 vCPU / 8 GB box) and **cannot scale across containers**.
The only way to materially cut wall time is **horizontal scale-out** of recording.

## 2. Goals / non-goals

**Goals**
- Fan recording **out** across many queue-driven containers (KEDA min 0 / **max 10**, scale to zero when idle).
- Split the **recorder** (web clip capture → blob) from the **editor** (fan-in compose/converge/publish).
- Reuse existing patterns: `queue.py` message schema, `video-scratch` blob container + `intermediates` prefix convention, `pipeline_lock` manifest-CAS, and the `aca-video.bicep` KEDA `azure-queue` trigger.
- Deterministic, **single-publish** per job (kill the duplicate-Spotify-draft seen in e2e).
- Local-first validation with **Azurite** (Blob + Queue) + docker-compose before any merge.

**Non-goals (YAGNI)**
- No change to clip recording fidelity, A/V-sync, EDL, or compose math (owned by #558/#559/#560 + existing modules).
- No new event grid / durable-orchestration framework. No per-clip autoscaling beyond queue length.
- No cross-region, no multi-tenant scheduling, no clip-level speculative re-execution.

## 3. Architecture — two roles, one image

Keep the existing image (ffmpeg + Playwright baked in). Add **one** new role and **one** new
queue. Three responsibilities, but only **two container roles** (KISS):

```
 synthesis runner ──(video-jobs msg)──▶  EDITOR / ORCHESTRATOR  (KEDA min0/max1)
                                          │  1. claim video pipeline lock
                                          │  2. plan episode → expected clip set
                                          │  3. write clipset.json (fan-out plan)
                                          │  4. enqueue N per-clip msgs ─────────────┐
                                          │  5. WAIT on fan-in barrier               │
                                          │  6. download clips → compose → distribute│
                                          │  7. cleanup scratch                      │
                                          ▼                                          ▼
                                    final MP4 + single publish        video-clip-jobs queue
                                                                                     │
                                                          ┌──────────────┬───────────┤  (KEDA min0/max10)
                                                          ▼              ▼           ▼
                                                      RECORDER       RECORDER     RECORDER   … up to 10
                                                   record 1 clip   record 1 clip  …
                                                   write clip+manifest to scratch
```

- **Recorder** (`podcaster.video.recorder`, new entrypoint): consumes **one** `video-clip-jobs`
  message = one `(job_id, clip_index)`. Records exactly one segment (reusing the existing
  `_record_segment` logic, factored out of the in-process pool), writes the clip **and** its
  per-clip `ClipManifest` to the `video-scratch` container, then deletes the queue message.
  Stateless, horizontally scalable, **idempotent** (skip when the terminal manifest already exists;
  see §5).
- **Editor / Orchestrator** (the existing `podcaster.video.job_runner`, refactored): triggered by
  the existing `video-jobs` message. It no longer records inline; instead it **plans**, **fans
  out** clip messages, **waits** for the fan-in barrier, then runs the **unchanged** download →
  compose → distribute path and cleans up scratch.

### Why two roles, not three (trade-off)

A dedicated *dispatcher* role (plan+enqueue only) was considered and rejected (YAGNI): planning
takes seconds and already lives in the editor; a third ACA Job + queue adds ops surface for no
throughput gain. Folding plan+fan-out into the editor keeps the change to **one** new role.

### Why the editor blocks on a wait, not an event trigger (trade-off)

The epic says "editor converges once the clip manifest is complete." Two ways to trigger it:

| Option | Mechanism | Verdict |
|---|---|---|
| **A — blocking wait (chosen)** | Editor polls the fan-in barrier (list scratch manifests vs `clipset.json` count) with backoff until complete or timeout. | **KISS.** One replica (max 1, weekly cadence) parked during recording is cheap. No extra coordination, trivially idempotent on redelivery. |
| B — last-recorder-enqueues | The recorder that writes the final clip CAS-detects "I am last" and enqueues an `edit-jobs` message; editor scales from zero only when work is ready. | Truer scale-to-zero, but adds a 3rd queue + "am I last" CAS race + a separate editor role. **Deferred (YAGNI)** — revisit only if editor idle-wait cost ever matters. |

Option A wastes at most one small-replica-hour per weekly episode. Recording fan-out (the actual
84%) is fully parallel in **both** options, so A captures ~all the win at a fraction of the complexity.

## 4. Queue schema

Reuse `queue.py`'s base64-JSON envelope (matches Azure SDK default encoding) and add a clip variant.

- **`video-jobs`** (existing, unchanged): `{"schema_version":"…video-queue-v1","job_id":"…"}` — triggers the editor.
- **`video-clip-jobs`** (new): one message per clip.
  ```json
  {"schema_version":"squadscope-podcaster-clip-queue-v1","job_id":"<job>","clip_index":7}
  ```
  Add `encode_clip_message(job_id, clip_index)` / `parse_clip_job(body) -> (job_id, clip_index)`
  next to the existing `encode_video_message` / `parse_job_id`. Body carries **no** secrets/PII.

**Poison handling — recorder writes a terminal manifest (key invariant).** Mirror the editor's
existing `MAX_DEQUEUE_COUNT = 5`. When a recorder dequeues a clip whose `dequeue_count >=
MAX_DEQUEUE_COUNT`, it does **not** silently drop it: it writes a **terminal fallback manifest**
(`ClipManifest.is_fallback = true`, plus a `status: "fallback"` / failure reason) for that
`clip_index` and then deletes the message. This establishes the invariant that **every expected
`clip_index` eventually has exactly one terminal manifest — either `success` or `fallback`** —
so the editor's fan-in is a pure presence check and never has to introspect the queue or guess
whether a slow recorder is still working. (Vetting note: this resolves the "editor can't see the
poison decision" ambiguity — the poison decision is materialized as a manifest the barrier reads.)
A clip that fails to *plan* (not record) is a hard job failure via existing `report_failure`.

## 5. Scratch blob layout (temporary clips)

Reuse the **existing** `video-scratch` container (already account-scoped RBAC; no new role
assignment) and the existing `video-jobs/{job_id}/…` prefix convention from `intermediates.py`:

```
video-scratch/
  video-jobs/{job_id}/
    clipset.json                       # editor-written fan-out plan: expected clip_index list, count, repo→index map, schema_version
    clips/{clip_index:03d}.webm        # recorder output (raw clip)
    clips/{clip_index:03d}.manifest.json   # per-clip ClipManifest (existing schema, to_dict())
```

- **Content-addressed by `{job_id}/{clip_index}`** → a redelivered clip overwrites the same path:
  idempotent by construction.
- **Manifest is the completion sentinel, written strictly after the clip.** The per-clip
  `manifest.json` is written **only after** the `.webm` blob is fully uploaded and size-verified
  (reuse the `intermediates` size-verify pattern). The recorder's idempotency check is therefore
  **"does the manifest exist?"**, *not* "does the clip exist?":
  - manifest present (success or fallback) → skip (already done);
  - clip present but manifest **missing** (recorder died mid-write) → re-record and overwrite, then
    write the manifest. This avoids the strand where a torn `.webm`-without-manifest is mistaken for
    "done" and the barrier waits forever.
- **Fan-in barrier = per-index `blob_exists`, not a `list_blobs` count.** The editor reads the
  expected `clip_index` set from `clipset.json` and checks
  `blob_exists(video-jobs/{job_id}/clips/{idx:03d}.manifest.json)` for **each** index. This sidesteps
  `list_blobs`'s default `limit=10` / lack of pagination (an episode can have >10 clips) and the
  fact that the `clips/` prefix mixes `.webm` and `.manifest.json`. A job is "fan-in complete" iff
  every expected index has a terminal manifest. Manifest presence (written after the clip) is the
  torn-read-safe "this clip is done" signal.
- **Cleanup:** editor deletes `…/clips/**` after a successful compose (reuse the
  `intermediates` cleanup pattern). Backstop: a storage **lifecycle rule** TTL-deletes
  `video-jobs/*/clips/` after N days for crashed/abandoned jobs (infra note, not code).

## 6. Idempotency & single-publish (determinism)

The e2e exposed **duplicate Spotify drafts** from concurrent/redelivered executions. The split
must drive external publishing toward at-most-once. **Manifest CAS alone is not sufficient** — it
serializes *local* manifest writes but does not make Spotify/YouTube side effects atomic — so the
design layers four mechanisms:

1. **Recorder idempotency:** manifest-existence sentinel (§5) → skip re-record when the terminal
   manifest is present; output path is content-addressed so even a re-record is a safe overwrite.
2. **Editor execution lease (not just `pipeline_lock`).** `pipeline_lock.claim_pipeline` only guards
   *audio-vs-video*; it lets the **same** pipeline "re-confirm" ownership, so two video editors for
   one `job_id` could both proceed if KEDA transiently over-provisions or the `video-jobs` message
   becomes visible mid-run. Add a dedicated **video editor lease** in the manifest via `update_bytes`
   CAS: `{run_id, claimed_at, expires_at}` with a heartbeat-renewed expiry. A second editor that
   sees an unexpired lease owned by another `run_id` **exits without working**. Pair this with a
   `video-jobs` **receive visibility timeout ≥ worst-case editor runtime** (fan-in wait + compose +
   publish), or periodic visibility renewal, so the message isn't redelivered while an editor still holds it (§8).
3. **`clipset.json` is immutable after first plan.** Create it **create-if-absent** (CAS) on the
   first editor run. On redelivery the editor **loads** the existing `clipset.json` as the source of
   truth instead of re-planning, so the expected clip set can't drift if script/metadata changed
   between attempts. Re-plan only when it is absent. Re-enqueue is **additive** — only indices with
   no terminal manifest yet — and the recorder's "never overwrite a completed manifest" rule plus
   content-addressed paths make a duplicate in-flight recorder harmless (last write wins, same bytes).
4. **Durable per-platform publish state (the real single-publish guard).** Before any external call
   the editor CAS-claims a `publish` phase in the manifest; after **each** platform succeeds it
   immediately persists that platform's result (`spotify: {status, episode_id}`, `youtube: {…}`).
   On retry it **skips platforms already recorded as published**, so a Spotify-ok / YouTube-fail
   partial publish retries only YouTube. **Residual risk (stated, not hidden):** a crash *after* a
   provider create but *before* persisting its result is still at-least-once. To close it, the
   Spotify/YouTube publish steps should **look up by deterministic key (job_id/episode title) and
   reconcile before creating** rather than blind-create. Until that reconcile exists, this is the
   only window and is documented as a known operational follow-up, not a strict guarantee.

## 7. KEDA scale rules (mirror `aca-video.bicep`)

| Param | **Recorder** (`video-clip-jobs`) | **Editor** (`video-jobs`) |
|---|---|---|
| trigger | `azure-queue`, identity-auth | `azure-queue`, identity-auth |
| `queueLength` | 1 (one replica per pending clip) | 1 (one replica per episode) |
| `minExecutions` | **0** | 0 |
| `maxExecutions` | **10** | 1 (cap concurrency to enforce single-publish) |
| `parallelism` | 1 | 1 |
| `replicaCompletionCount` | 1 | 1 |
| `pollingInterval` | 30s | 30s |
| `replicaRetryLimit` | 1 (→ poison/fallback) | 1 |
| CPU / mem | **2.0 / 4Gi** (one Chromium ≈1.5 GB) | 4.0 / 8Gi (ffmpeg compose, unchanged) |
| `replicaTimeout` | per-clip budget (e.g. 900s) | covers fan-in wait + compose (e.g. 5400s, unchanged) |

The recorder is a **smaller** box than today's 4/8 monolith because each replica records a single
clip. Wall-clock recording time drops from ~`N/3 × per_clip` (single box, 3 threads) to
~`ceil(N/10) × per_clip` (up to 10 boxes), scaling to zero between weekly runs.

## 8. Failure / retry & well-architected trade-offs

- **Reliability:** clip **receive visibility timeout ≥ max per-clip record time** so a slow clip
  isn't double-delivered mid-flight; `dequeue_count >= MAX_DEQUEUE_COUNT` → recorder writes a
  **terminal fallback manifest** (§4) so the barrier always converges. The **`video-jobs` (editor)
  receive visibility timeout must be ≥ the editor's worst-case runtime** (`fan-in wait + compose +
  publish`) — or the editor must renew visibility while working — otherwise the job is redelivered
  to a second editor while the first still holds the lease. ACA `replicaTimeout` is set **above** the
  corresponding queue visibility/processing budget for each role. The editor fan-in wait is bounded
  by `expected_clips × per_clip_budget × MAX_DEQUEUE_COUNT / max_recorders + slack`; on timeout it
  composes with whatever terminal manifests exist (fallbacks fill the gaps) or fails via
  `report_failure`. No bespoke reaper (YAGNI) — KEDA scales recorders to zero when the queue drains;
  the lifecycle rule reaps orphaned scratch.
- **Cost:** recorders scale to zero; smaller per-clip boxes; the only added steady cost is the
  parked editor replica during recording (≤1 small replica-hour/week).
- **Performance:** up to 10× recording fan-out; compose unchanged.
- **Security:** identity-only Blob+Queue (no keys/SAS), reusing the existing UAMI + account-scoped
  roles. Queue bodies carry `job_id`/`clip_index` only. Hermes owns the DevSecOps review of the new
  bicep/entrypoints.
- **Operability:** every artifact lives under `video-jobs/{job_id}/` for easy triage; the editor logs
  the fan-in barrier state (present/expected) each poll.

## 9. Local test plan — Azurite + docker-compose (fan-out/fan-in)

Validate **local → direct-ACA → GitHub Action**. No merge/deploy without the local fan-out passing;
clean up before/after. This extends the existing `docker-compose.test.yml` Azurite stack.

**Scenario (integration test `tests/integration/test_scaleout_fanout.py`, added with the recorder/editor work):**

1. **Up:** `docker compose -f docker-compose.fanout.yml up -d azurite` (Blob + Queue emulation).
2. **Seed:** create `video-clip-jobs` + `video-jobs` queues and the `video-scratch` container in
   Azurite (via the well-known dev connection string already wired in `docker-compose.test.yml`);
   stage a tiny 3-clip job manifest/script fixture.
3. **Fan-out:** start **3 recorder** replicas
   (`docker compose up --scale recorder=3 recorder`) against a fixture plan whose `record_one` is a
   fake that writes a 1-frame webm + manifest (no real Chromium needed in CI). Assert each clip blob
   + manifest appears exactly once under `…/clips/`.
4. **Fan-in:** start the **editor**; assert it (a) writes `clipset.json`, (b) blocks until all 3
   manifests are present, (c) composes, (d) records a single published artifact.
5. **Idempotency:** re-deliver the `video-jobs` message → assert the second editor sees an unexpired
   **lease** owned by another `run_id` and exits with **no** duplicate publish; assert **no** re-record
   (clips skipped via the manifest sentinel) and that `clipset.json` is reused, not re-planned.
   Re-deliver one clip → assert overwrite, not a second manifest.
6. **Poison/fallback:** force one clip to exceed `MAX_DEQUEUE_COUNT` → assert the recorder writes a
   **terminal fallback manifest** for that index, the barrier converges (every index has a manifest),
   the fallback card is substituted, and the episode still composes.
7. **Down:** `docker compose -f docker-compose.fanout.yml down -v` and assert scratch cleaned.

**`docker-compose.fanout.yml` sketch** (added in the implementation issues, not in this design pass —
reuses the existing Azurite service + dev connection string verbatim):

```yaml
# docker-compose.fanout.yml — local fan-out/fan-in harness (Azurite Blob+Queue)
services:
  azurite:        # identical to docker-compose.test.yml azurite service (Blob 10000 / Queue 10001)
    image: mcr.microsoft.com/azure-storage/azurite
    command: >-
      azurite --blobHost 0.0.0.0 --blobPort 10000 --queueHost 0.0.0.0 --queuePort 10001
      --location /data --skipApiVersionCheck
    ports: ["10000:10000", "10001:10001"]
    volumes: [azurite-data:/data]

  recorder:       # scale with `--scale recorder=N`; one clip per replica
    image: podcaster-synthesis:test
    build: { context: ., dockerfile: Containerfile }
    depends_on: { azurite: { condition: service_healthy } }
    environment:
      AZURE_STORAGE_CONNECTION_STRING: *azurite_dev_conn   # well-known Azurite dev key
      PODCASTER_VIDEO_CLIP_QUEUE: "video-clip-jobs"
      PODCASTER_VIDEO_SCRATCH_CONTAINER: "video-scratch"
      PODCASTER_RECORDER_FAKE_BROWSER: "1"                 # CI: synthesize a 1-frame clip, no Chromium
    entrypoint: ["python", "-m", "podcaster.video.recorder"]

  editor:
    image: podcaster-synthesis:test
    depends_on: { azurite: { condition: service_healthy } }
    environment:
      AZURE_STORAGE_CONNECTION_STRING: *azurite_dev_conn
      PODCASTER_VIDEO_QUEUE: "video-jobs"
      PODCASTER_VIDEO_CLIP_QUEUE: "video-clip-jobs"
      PODCASTER_VIDEO_SCRATCH_CONTAINER: "video-scratch"
    entrypoint: ["python", "-m", "podcaster.video.job_runner"]

volumes: { azurite-data: {} }
```

> Azurite speaks the storage **key** (connection-string) data plane, not Azure AD; the recorder/editor
> grow a thin connection-string path for local tests only, exactly as `docker-compose.test.yml`
> already documents. Production stays identity-only.

## 10. Sequencing & gating

**Implementation is gated on #558/#559/#560 merging first** (they harden the recording/compose path
this RFC fans out). Recommended order once the gate clears:

1. **Clip queue + message schema** (foundational).
2. **Recorder entrypoint** (depends on 1) ∥ **KEDA bicep** for recorder+editor (depends on 1).
3. **Editor fan-out + fan-in barrier + scratch cleanup** (depends on 1, 2).
4. **Idempotent single-publish guard** (depends on 3) ∥ **Azurite fan-out integration test + compose** (depends on 2, 3).

See the squad-labeled sub-issues filed under #552.
