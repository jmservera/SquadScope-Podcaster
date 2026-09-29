# Cycle 1 / Wave 1 Wider — Internal evidence lane

## Delegated contract

- **Topic:** W38 video stage budget redesign in `jmservera/SquadScope-Podcaster`.
- **Lane:** Internal — browser capture, Chromium lifecycle, FFmpeg/ffprobe/process-tree cancellation, storage/archive operations, intermediates/checkpoint validation and resume, and deterministic static fallback possibilities.
- **Posture:** Balanced.
- **Evidence goal (Wider):** Map currently available implementation and test evidence that could support or constrain shared parent/stage time budgets; identify candidate insertion points and unanswered questions without choosing a design.
- **Questions:** Existing timeouts/cancellation; partial-output handling; size/checksum/probe validation; reuse rules for audio, clips, normalized segments, composed video; browser-free primitives; storage timeout controls; and tests for hangs, delay, or replay.
- **Criteria:** Cite workspace-relative `path:line` evidence; identify insertion points for a shared parent/stage budget; record gaps and contradictions; make no parent decisions or source edits.
- **Scope / non-goals:** Workspace evidence only. No live W38, storage, or provider calls; no production mutation; parent artifact `.copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md` is read-only and will not be read or edited.
- **Limit / stop rule:** Stop at evidence saturation.

## Research log

| Action | Result | Next condition |
|---|---|---|
| Preflighted candidate path | Candidate is distinct from the parent primary artifact and lies under `.copilot-tracking/research/subagents/`. | Continue with workspace-only investigation. |
| Examined runner, recorder/editor, capture, compose/EDL, intermediate-store, storage, and focused tests | Existing controls are a mixture of queue visibility/fan-in deadlines, small operation timeouts, retries, and a clip-duration cap; no shared parent/stage deadline or subprocess process-tree cancellation was found in this bounded lane. | Evidence saturated across the delegated components. |

## Findings (retrieved 2026-09-15)

### Q1 — Existing timeouts, cancellation, and likely budget insertion points

**Facts**

1. The editor queue message has an environment-configurable visibility window
   (default 5,400 seconds), and recorder messages have a separate configurable
   window (default 900 seconds): `podcaster/video/job_runner.py:127-133`,
   `podcaster/video/job_runner.py:761-772`, and
   `podcaster/video/recorder.py:61-67,337-348`. These prevent normal
   double-delivery but are not an in-process deadline/cancellation mechanism.
2. The fan-in barrier itself has injected monotonic-clock/sleep dependencies,
   a deadline, and returns incomplete indices rather than raising:
   `podcaster/video/editor.py:255-288`. Its caller is consequently a concrete
   insertion point for a stage budget/remaining-time check.
3. Capture has navigation deadlines (10 seconds general; 60 seconds GitHub;
   8 seconds website), bounded recording retries, and a cap of 600 seconds per
   clip by default: `podcaster/video/video_gen.py:105-184`. Screenshot-segment
   ffmpeg has a 600-second `subprocess.run` timeout:
   `podcaster/video/video_gen.py:1150-1175`.
4. The monolithic recording path always closes its browser in `finally`, and
   per-recorder Chromium is also closed in `finally`:
   `podcaster/video/video_gen.py:2578-2605` and
   `podcaster/video/recorder.py:274-291`. These are cleanup insertion points
   once a caller signals budget expiry, but they do not create a timer or
   terminate child process trees themselves.
5. Normalization has bounded task retries and task-level progress reporting,
   but its default runner calls `subprocess.run(..., check=True)` with no
   timeout: `podcaster/video/video_compose.py:1145-1165` and
   `podcaster/video/video_compose.py:3262-3398`. The EDL renderer's default
   ffmpeg runner also has no timeout: `podcaster/video/edl_render.py:406-414,
   480-548`.

**Inference for parent synthesis (not a design selection):** A parent-owned
deadline could be passed at `run_video_generation` before record/fan-in/compose
(`podcaster/video/job_runner.py:774-790,1050-1111`), then consumed at
`wait_for_fanin`, recording retry boundaries, normalization task launches, and
ffmpeg runner calls. Existing browser `finally` blocks can service cooperative
unwind; a new runner/process-group abstraction would be needed for positively
stopping a hung ffmpeg child tree.

### Q2 — Partial outputs, validation, and archive/storage controls

**Facts**

1. The final runner only accepts an existing MP4 of at least 1,024 bytes:
   `podcaster/video/job_runner.py:135,1094-1111`. This is size-only and follows
   composition; it is not an ffprobe/container validity check.
2. Fresh recording validation runs ffprobe with a 30-second limit, but every
   failure is advisory; size-verified checkpoint upload is documented as the
   authoritative guarantee: `podcaster/video/video_gen.py:810-860`.
3. Intermediate uploads compare blob size with the local source and remove an
   unverified blob where possible: `podcaster/video/intermediates.py:126-196`.
   The local backend writes a sibling `.tmp` then atomically promotes it:
   `podcaster/storage.py:168-186`. Azure downloads similarly write `.part` and
   atomically promote only after a completed stream:
   `podcaster/storage.py:564-594`.
4. Azure control-plane-style Blob `GET`/`HEAD`/`DELETE` uses fixed 30-second
   `urlopen` timeouts: `podcaster/storage.py:297,358,391,472,501,606`.
   SDK streaming upload/download use bounded concurrency (2) but no explicit
   operation timeout passed by this code: `podcaster/storage.py:536-594`.
5. Cleanup is explicitly best-effort and is protected by a lifecycle-policy
   safety net: `podcaster/video/intermediates.py:240-260`; Azure prefix cleanup
   pages to exhaustion: `podcaster/storage.py:616-630`.

**Gap / qualification:** There is no checksum validation for media
intermediates in the inspected path. Size checks are best effort when a backend
cannot report size (`podcaster/video/intermediates.py:198-218`), and a resumed
recording trusts a parsable metadata sidecar plus successful download rather
than re-probing/checksumming the downloaded file
(`podcaster/video/video_gen.py:760-807`).

### Q3 — Reuse/checkpoint rules

**Facts**

1. Raw recordings are reused only when both sidecar metadata and the named blob
   exist and download succeeds; otherwise recording is redone:
   `podcaster/video/video_gen.py:760-807`. Successful raw recordings are
   ffprobe-advised, upload-size-verified, sidecar-marked, and only then locally
   deleted: `podcaster/video/video_gen.py:842-866`.
2. `record_episode` scans every segment before launching Playwright and does
   not launch a browser if all are resumed: `podcaster/video/video_gen.py:2487-2550`.
3. Normalized segments are named by index, reused when their checkpoint exists,
   fetched only when needed for pairwise composition, and locally released
   after verified upload: `podcaster/video/video_compose.py:3265-3378,
   3478-3500`.
4. A composed-video checkpoint can skip record → normalize → compose, but
   deliberately does not apply when animated intermissions are in use:
   `podcaster/video/video_compose.py:3170-3202`. The resumed output proceeds
   to final mux.
5. Fan-out clips use a terminal manifest as idempotency sentinel. A clip blob
   without its manifest is treated as mid-write and re-recorded; the recorder
   verifies blob size before conditionally writing the terminal manifest:
   `podcaster/video/recorder.py:1-18,142-229`. At assembly, a missing terminal
   pair is replaced with a gap filler rather than composed:
   `podcaster/video/editor.py:289-335`.

**Gap / qualification:** This lane found explicit checkpoint reuse for raw
recordings, normalized clips, fan-out clips, and composed video, but no
equivalent media-checkpoint rule for the audio input. Audio is an input to
final mux; its duration probe in the runner has no `subprocess.run` timeout:
`podcaster/video/job_runner.py:700-724`.

### Q4 — Browser-free/static fallback possibilities

**Facts**

1. The recorder has a CI-only `PODCASTER_RECORDER_FAKE_BROWSER` path that
   writes a tiny non-empty placeholder payload without Chromium:
   `podcaster/video/recorder.py:56-66,245-272`. Its own comments limit it to
   harness assertions, so it is not evidence of a production-valid static
   fallback.
2. Screenshot/hyperframe capture still begins with a Chromium context, then
   turns PNG frames or a still into MP4 through ffmpeg:
   `podcaster/video/video_gen.py:1179-1265`. Thus it reduces screencast
   dependence but is not browser-free for live page capture.
3. The EDL renderer can construct deterministic card/intermission sources and
   degrade unavailable clip/screenshot material to screenshot → card →
   intermission before a single ffmpeg invocation:
   `podcaster/video/edl_render.py:120-230,406-479,480-548`. Card/intermission
   rendering is a browser-free primitive once the EDL and any required local
   assets are available.

### Q5 — Tests for hangs, delay, and replay/resume

**Facts**

1. Fan-in completion, delayed arrival, and partial timeout are tested using
   injected clock/sleep: `tests/test_editor.py:180-255`.
2. A hung drawtext capability probe is tested as a 10-second timeout and
   candidate skip: `tests/test_video_compose.py:698-708`.
3. Intermediate tests cover roundtrip, disabled behavior, cleanup, rejected
   truncated upload, size-match acceptance, and the no-size best-effort
   exception: `tests/test_video_intermediates.py:24-272`.

**Gap:** No inspected test exercises cancellation/termination of an active
Chromium or ffmpeg process tree, a parent-wide elapsed-time budget across
record/fan-in/compose/distribute, a storage streaming operation that hangs, or
validation of a downloaded checkpoint with ffprobe/checksum before reuse.

## Compact evidence relationships

| Question | Claim | Relationship | Provenance |
|---|---|---|---|
| Timeouts/cancellation | Queue windows, fan-in deadline, navigation limits, and selected probe/segment limits exist; shared stage deadline and process-tree cancellation were not found. | Supports bounded-control baseline; leaves cancellation gap unresolved. | `podcaster/video/job_runner.py:127-133,761-772`; `podcaster/video/editor.py:255-288`; `podcaster/video/video_gen.py:105-184,1150-1175`; `podcaster/video/video_compose.py:1145-1165` |
| Partial output / validation | Size verification plus atomic local/download promotion prevents several torn-artifact paths; final MP4 and resumed recordings lack strong content validation. | Supports safe-transfer baseline; weakens a claim of complete media validation. | `podcaster/video/intermediates.py:126-218`; `podcaster/storage.py:168-186,564-594`; `podcaster/video/job_runner.py:1094-1111` |
| Reuse | Recording, normalized, fan-out, and composed checkpoints have distinct reuse sentinels/rules. | Supports resumability; leaves audio reuse and resumed-media integrity unresolved. | `podcaster/video/video_gen.py:760-866,2487-2550`; `podcaster/video/video_compose.py:3170-3202,3265-3500`; `podcaster/video/recorder.py:142-229` |
| Static fallback | Deterministic EDL card/intermission paths are ffmpeg-only; the existing fake-browser path is CI-only. | Supports a browser-free fallback primitive; leaves production policy/asset contract unresolved. | `podcaster/video/edl_render.py:120-230,480-548`; `podcaster/video/recorder.py:245-272` |
| Tests | Fan-in delay/partial completion and one probe hang have tests; end-to-end cancellation/hang coverage is absent in inspected tests. | Supports targeted timing-test baseline; leaves major budget test gap unresolved. | `tests/test_editor.py:180-255`; `tests/test_video_compose.py:698-708`; `tests/test_video_intermediates.py:160-275` |

## Conflicts and gaps for parent

- The 600-second recording cap is described as leaving headroom beneath a
  900-second recorder timeout, while the code permits `VIDEO_MAX_CLIP_RECORD_SECONDS`
  to be non-positive and thereby disables the cap:
  `podcaster/video/video_gen.py:162-184`. This is a configurable safety
  measure, not an invariant.
- Storage has fixed 30-second urllib timeouts but SDK stream transfers have no
  visible deadline in this layer; whether SDK defaults satisfy a stage budget
  cannot be determined from workspace evidence alone.
- Stage duration attribution is available via composition task progress and
  `PipelineTimings`, but no inspected mechanism applies a shared cancellation
  signal to those units: `podcaster/video/video_compose.py:3080-3093,
  3262-3398`; `podcaster/video/job_runner.py:1094-1111`.

## Stop decision

**Complete — evidence saturation.** The bounded workspace lane has been
examined across its execution, storage/checkpoint, deterministic fallback, and
focused-test surfaces. Further sources within the stated scope are likely to
repeat the same call paths rather than answer the identified cancellation and
integrity gaps.
