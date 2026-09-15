# Cycle 1 / Wave 2 Deeper — Internal media/checkpoint evidence lane

## Delegated contract

- **Topic:** W38 video stage budget redesign in `jmservera/SquadScope-Podcaster`.
- **Lane:** Internal — exact additive APIs/call chains for shared monotonic time budget through Chromium, FFmpeg/ffprobe, storage/archive, process-tree termination, deterministic production static fallback, and a validated checkpoint/evidence schema.
- **Posture:** Balanced.
- **Evidence goal (Deeper):** Trace current APIs and focused tests sufficiently to identify compatible integration seams, enforcement locations at T+25/T+55/T+60/T+82/T+85, and material compatibility/performance risks. No design selection.
- **Scope / non-goals:** Workspace evidence only; no live W38/storage/provider calls, production mutation, or parent-artifact reading/editing. Parent artifact remains `.copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md`.
- **Limit / stop rule:** Stop at evidence saturation.

## Research log

| Action | Result | Next condition |
|---|---|---|
| Preflighted candidate artifact | Path is under `.copilot-tracking/research/subagents/` and distinct from parent artifact. | Inspect focused call chains and contracts. |
| Traced runner, editor/recorder, capture, compose/EDL, distribution, storage, checkpoint schemas, and focused tests | Identified compatible parameter seams and present validation limits. | Evidence saturated in delegated scope. |

## Findings (retrieved 2026-09-15)

### 1. Current call chains and additive budget seams

**Current call chain (facts)**

1. `process_message` invokes `run_video_generation`, maps permanent versus
   transient exceptions to queue disposition, and leaves transient work for
   bounded redelivery: `podcaster/video/job_runner.py:1677-1749`.
2. `run_video_generation` creates optional intermediate and fan-out backends,
   loads manifest/script/audio, plans, then invokes either `record_via_fanout`
   or `record_episode`, `compose_video`, and `distribute_video`:
   `podcaster/video/job_runner.py:800-1105,1263-1416`.
3. Fan-out is `record_via_fanout` → immutable/create-if-absent
   `plan_or_load_clipset` → additive `enqueue_missing_clips` → deadline-aware
   `wait_for_fanin` → `assemble_recording`:
   `podcaster/video/editor.py:151-288,337-379`.
4. Inline recording performs checkpoint resume before launching Playwright and
   calls `_record_segment` through bounded `retry_call`; its browser lifecycle
   is closed in a `finally`: `podcaster/video/video_gen.py:2487-2605`.
5. Composition accepts an injectable command `runner`, then uses it for every
   normalize/probe/compose/final-mux command: `podcaster/video/video_compose.py:1145-1165,
   3010-3108,3262-3557`. Distribution accepts a storage adapter and uses an
   injectable HTTP transport for YouTube but not a deadline abstraction:
   `podcaster/video/distribution.py:232-399,985-1065`.

**Additive API map (inference based on those seams; not a parent decision)**

| Boundary | Compatible additive argument / wrapper | Call-chain propagation | Existing behavior preserved when omitted |
|---|---|---|---|
| Parent run | `budget: MonotonicBudget | None = None` at `run_video_generation` | Construct once from `time.monotonic`; pass to recording, composition, and distribution adapters. | `None` means current unbounded orchestration. |
| Fan-in | `budget` (or a derived `timeout_seconds=min(existing, budget.remaining())`) at `record_via_fanout` / `wait_for_fanin` | Existing injectable `monotonic`, `sleep`, `timeout_seconds`, and `heartbeat` already permit a compatible remaining-time boundary. | Existing 5,400 s default / partial-gap behavior remains. |
| Chromium | `budget` at `record_episode`, `_record_segment`, and recorder `_production_record_segment` | Cap navigation/wait calls to remaining milliseconds; on exhausted budget unwind through current context/browser `finally` close paths. | Existing navigation caps and per-task retry behavior remain for no budget. |
| FFmpeg/ffprobe | `runner: CommandRunner` already exists in compose/EDL; provide a budget-aware runner implementation | Pass unchanged from `compose_runner` in runner through `compose_video`; use same runner for probes. Screenshot-segment helper needs a new optional runner because it calls `subprocess.run` directly. | Existing `_default_runner` retained for omitted runner. |
| Storage/archive | Optional deadline-aware adapter/protocol methods rather than changing all `StorageBackend` callers at once | `IntermediateStore` and `StorageUploader` operations can receive a wrapper enforcing remaining time; `distribute_video` can accept its current transport injection. | Existing protocol implementations and local/test fakes keep current signatures. |

`MonotonicBudget` must use injected/standard `time.monotonic`, expose
`remaining_seconds()` and `expired()`, and derive *per-operation* timeout values
without using wall-clock timestamps. The current `wait_for_fanin` already uses
monotonic time (`podcaster/video/editor.py:255-288`); no parent-wide budget
object currently exists.

### 2. T+ enforcement mapping

The requested T+ markers are not present in workspace code; this is a proposed
mapping to the exact current seams, with the meaning of each T value left for
parent synthesis.

| Marker | Current enforcement seam | Observable result / compatibility constraint |
|---|---|---|
| **T+25** | At `run_video_generation` after input load and before plan/record (`podcaster/video/job_runner.py:800-1029`) | Record a budget checkpoint before expensive browser/fan-out work. Must retain current transient queue semantics rather than delete a resumable job. |
| **T+55** | `record_via_fanout` wait deadline and its per-poll heartbeat (`podcaster/video/editor.py:337-379`); inline retry boundary (`podcaster/video/video_gen.py:2510-2534`) | Clamp fan-in wait to remaining budget; produce current partial-fan-in gap fills rather than wait past budget. For inline recording, stop launching the next segment and rely on checkpointed earlier segments. |
| **T+60** | Browser page/context work followed by `context.close()` / `browser.close()` (`podcaster/video/video_gen.py:1179-1265,2205-2430,2578-2605`; `podcaster/video/recorder.py:274-291`) | Cooperative budget expiry can close Playwright resources. It cannot guarantee termination if a synchronous call does not return; this needs tested outer process isolation or a documented limitation. |
| **T+82** | `compose_video` start, per-normalize launch, and `_finalize_output` path (`podcaster/video/video_compose.py:3010-3108,3262-3557`) | Budget-aware command runner supplies no more than remaining time and prevents launching subsequent parallel work after expiry. Must preserve normalization checkpoint/retry behavior. |
| **T+85** | `distribute_video` / `archive_to_blob` after final MP4 validation (`podcaster/video/job_runner.py:1094-1111,1263-1416`; `podcaster/video/distribution.py:755-799,985-1065`) | Enforce a final reserve/check before irreversible provider/archive operations. Existing distribution currently archives by reading entire `video_path` into bytes, a performance/memory concern for any retry/cancellation boundary. |

### 3. Process termination and command timeout evidence

**Facts**

- Composition's default command runner is `subprocess.run(check=True)` without
  a timeout: `podcaster/video/video_compose.py:1155-1165`; EDL rendering has
  the same shape: `podcaster/video/edl_render.py:406-414`.
- Screenshot-to-video uses `subprocess.run(..., timeout=600)` but catches
  `TimeoutExpired` and raises a regular error: `podcaster/video/video_gen.py:1150-1175`.
- The source lane found no `Popen`, `start_new_session`, process-group ID,
  `terminate`, or `kill` management in these execution paths. Therefore
  `subprocess.run(timeout=...)` is evidence only of a direct child timeout, not
  proven whole-process-tree termination.
- Browser cleanup is explicit but cooperative: recording contexts are closed
  in `_finalize_segment` and browsers in `finally`
  (`podcaster/video/video_gen.py:1242-1265,2578-2605`).

**Required new seam to make process-tree behavior testable (inference):**
replace only the default ffmpeg/ffprobe runner behind the existing injectable
`CommandRunner` protocol with a runner that launches a new session/process
group, waits at most the budget-derived timeout, then terminates and escalates
to kill for that group while collecting stderr. This must be added separately
to screenshot helper calls and the runner's `_probe_audio_duration`, which
currently call `subprocess.run` directly
(`podcaster/video/video_gen.py:1150-1175`; `podcaster/video/job_runner.py:699-724`).
It is not safe to claim current code has tree cancellation.

### 4. Deterministic production static fallback

**Facts**

- Fan-in's present production gap filler calls
  `recorder._production_record_segment`, which launches Playwright/Chromium;
  it is not browser-free: `podcaster/video/editor.py:412-430` and
  `podcaster/video/recorder.py:274-291`.
- Browser capture's generic/removed/url fallback cards still operate through a
  browser context before PNG/ffmpeg finalization:
  `podcaster/video/video_gen.py:1179-1265,2205-2430`.
- `render_edl` is the existing deterministic production-capable primitive:
  it can construct color/card/intermission filter sources, convert unavailable
  clip/screenshot segments through its fallback chain, and run an injectable
  ffmpeg command: `podcaster/video/edl_render.py:120-230,406-548`.

**Exact integration boundary (inference):** replace the *implementation
selected by* `fill_gap` in `assemble_recording`—not the fan-in sentinel,
clipset, or compose input shape—with a production function that creates a
deterministic one-segment card EDL and invokes `render_edl` through the
budget-aware runner. It returns the same `RecordedSegment` expected by
`compose_video`. This preserves `record_via_fanout`'s documented
byte-compatible `RecordingResult` contract (`podcaster/video/editor.py:1-36,
337-379`) while removing Chromium from poison/timeout fallback. It requires
an explicit, bundled font/card-asset contract; `edl_render` defaults to
`/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`
(`podcaster/video/edl_render.py:54-91`), so container-font availability is a
compatibility risk.

### 5. Validated checkpoint/evidence schema: existing fields and gaps

**Existing evidence**

| Artifact | Existing identity/validation fields | Provenance |
|---|---|---|
| Audio MP3/WAV | Audio synthesis records paths, size, SHA-256, and validation in main manifest; realized-audio metadata is a separate versioned JSON blob. | `podcaster/job_runner.py:105-130,365-435` |
| Immutable clipset | Versioned `schema_version`, job id, count, ordered entries; create-if-absent CAS and re-read makes an existing plan authoritative. No digest field. | `podcaster/video/clipset.py:20-218`; `podcaster/video/editor.py:151-190` |
| Fan-out clip + terminal manifest | Manifest sentinel follows size-verified `.webm`; conditional create protects terminal manifest. Manifest has version, ID, duration, trim/loop metadata and fallback flag; it has no clip size/hash/probe field. | `podcaster/video/recorder.py:128-229`; `podcaster/video/clip_manifest.py:166-246` |
| Raw/normalized intermediates | Blob size is verified after upload when backend supports it; sidecar only carries recovery metadata; stage manifest has status and arbitrary extras. | `podcaster/video/intermediates.py:126-218,275-297`; `podcaster/video/video_gen.py:741-866`; `podcaster/video/video_compose.py:3265-3378` |
| Composed video | Fixed `composed_video.mp4` checkpoint has size-verified upload and stage duration; resume downloads then best-effort probes it. Final MP4 is only existence + ≥1,024 bytes checked before distribution. | `podcaster/video/video_compose.py:2998,3170-3202,3540-3557`; `podcaster/video/job_runner.py:135,1094-1111` |

**Additive evidence-schema minimum (inference):**

Use a versioned JSON sidecar per binary checkpoint (or versioned `manifest.json`
stage entry) with: `schema_version`, `artifact_kind`, `job_id`, stable logical
name/index, source/clipset digest where applicable, `size_bytes`, `sha256`,
probe result (`duration_seconds`, selected stream facts, `probe_status`), and
`created_at`/producer version. Validate in this order before resume:

1. parse/version and identity; 2. blob existence and expected size; 3. download
   atomically; 4. local size and SHA-256; 5. bounded ffprobe for required
   streams/duration; 6. only then reuse. Missing/invalid metadata should follow
   the existing recompute-or-card fallback rather than be treated as a valid
   checkpoint.

This can be additive because `IntermediateStore.mark(stage, **extra)` already
accepts arbitrary fields (`podcaster/video/intermediates.py:275-297`), but the
present unconditional reuse checks must be strengthened. Clipset immutability
should include a persisted content hash so a recorder/editor can detect
corrupt-but-parseable plan bytes; audio's existing manifest SHA-256 can be
copied as the composed-video sidecar input identity.

### 6. Focused tests and risks

**Current relevant tests**

- Fan-in uses injected clock/sleep and verifies completion, delayed manifests,
  and timeout with partial set: `tests/test_editor.py:180-255`.
- Recording tests assert order under deliberate delays and a one-browser
  sequential compatibility path: `tests/test_video_gen.py:1980-2075`.
- Composition tests cover checkpoint persistence, resume bypass of
  normalize/compose, and normalized segment reuse:
  `tests/test_video_compose.py:3330-3475`.
- Intermediate tests cover size mismatch deletion and no-size best effort:
  `tests/test_video_intermediates.py:160-275`.

**Exact missing focused-test cases (inference)**

1. Inject monotonic time at each requested T+ marker and assert no next
   stage/provider call starts after expiry; assert partial fan-in takes current
   fallback path.
2. Use a controllable spawned parent/child fixture to verify new runner sends
   termination/kill to the entire FFmpeg process group and reaps it; cover
   timeout stderr/result classification.
3. Mock a blocking Playwright call and verify the documented cooperative
   boundary or an isolated-worker termination contract; current mocks only
   assert `browser.close`, not budget cancellation.
4. Test deterministic static `fill_gap` without importing Playwright and
   assert valid probe output, deterministic rendered inputs, and retained
   `RecordedSegment` shape.
5. For each audio/clipset/clip/normalized/composed schema: valid reuse,
   incorrect size, wrong SHA-256, invalid/missing sidecar, ffprobe failure, and
   old schema compatibility. Assert invalid checkpoints recompute/fallback and
   are not silently reused.
6. Test archive/distribution boundary with budget-expired transport/storage
   fakes; retain idempotent/persisted provider outcome behavior.

**Compatibility/performance risks**

- A new required hash/probe on every large intermediate adds full-file I/O and
  ffprobe subprocess cost; streaming hashing during existing upload/download
  or an opt-in schema-version transition avoids doubling read cost.
- Rejecting legacy metadata immediately would discard working checkpoints;
  versioned sidecars need a clear compatibility path (recompute, not trust).
- `ThreadPoolExecutor` normalization can begin multiple ffmpeg tasks before a
  shared budget notices expiry (`podcaster/video/video_compose.py:3387-3398`);
  worker count and launch gating affect CPU/disk pressure.
- Clamping default queue visibility/fan-in caps without preserving lease
  heartbeat can permit duplicate editors; current renewal happens per poll
  (`podcaster/video/job_runner.py:1029-1070`).
- Archive currently reads the complete final MP4 into memory before
  `StorageUploader.upload` (`podcaster/video/distribution.py:755-799`), so
  budget retries or checksum implementations must avoid increased peak memory.
- Static ffmpeg cards depend on an installed deterministic font and ffmpeg;
  it is a safer browser-free fallback only if those image/font dependencies
  are packaged and probed.

## Stop decision

**Complete — evidence saturation.** The deeper lane traced all delegated
execution and data contracts to concrete APIs and tests. The remaining items
are design/parent decisions or require implementation/runtime evidence outside
the no-mutation scope.
