# Cycle 1 / Wave 1 Wider — Internal evidence lane

## Input contract and preflight

- **Topic:** W38 video stage budget redesign in `jmservera/SquadScope-Podcaster`.
- **Lane:** bounded internal investigation of editor, recorder, fan-out/fan-in, clip manifest, clipset, queue lease/retry, and terminal-state architecture.
- **Questions:** current timing APIs/defaults; fan-in waits; recorder visibility/soft-hard/navigation/capture/finalization/process deadlines; clip abandonment/retry; manifest CAS/immutability and late-recorder races; SSRF/poison-message/clip-before-manifest safeguards; and test seams.
- **Evidence criteria:** workspace-relative `path:line` evidence, contradictions/gaps, and likely insertion points for one parent monotonic deadline.
- **Scope / non-goals:** balanced posture; static workspace evidence only. No live W38, queue, storage, or provider operation; no production mutation; no source edit.
- **Limit:** stop when this lane has sufficient static evidence to answer the delegated questions.
- **Wave-specific evidence goal:** Wider — inventory relevant current contracts, defaults, mechanisms, safeguards, and test seams without selecting a redesign.
- **Path preflight:** this evidence artifact is distinct from `.copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md` and is under the caller-approved `.copilot-tracking/research/subagents/` path.
- **Final status:** complete; static internal evidence saturated for the delegated questions.

## Research actions

1. Read the video editor, recorder, fan-out plan, queue, storage-CAS, video runner, capture/navigation, SSRF, and bounded unit-test seams.
2. Cross-checked implementation comments against unit tests for barrier timeout, leases, duplicate recording, poison handling, queue parsing, visibility timeout, and selected security/retry classifications.
3. No source, configuration, production documentation, live queue/storage/provider, or parent artifact was modified.

## Findings

### Timing APIs, defaults, and waits

| Delegated question | Fact evidence | Finding / evidence relationship | Confidence |
| --- | --- | --- | --- |
| Fan-in wait | `podcaster/video/editor.py:69-74`, `podcaster/video/editor.py:245-288` | **Supports:** `wait_for_fanin` calculates `monotonic() + 5400`, polls per expected manifest every 15 seconds, and returns partial completion on expiry. It is the direct fan-in blocking point. | High |
| Editor message visibility | `podcaster/video/job_runner.py:127-132`, `podcaster/video/job_runner.py:761-771`, `podcaster/video/job_runner.py:1754-1773` | **Supports:** video queue receive visibility defaults to 5400 seconds and accepts a positive `PODCASTER_VIDEO_VISIBILITY_TIMEOUT` override. The code documents it must cover fan-in + compose + publish, but does not calculate it from those phases. | High |
| Recorder message visibility | `podcaster/video/recorder.py:64-68`, `podcaster/video/recorder.py:479-504` | **Supports:** clip queue visibility defaults to 900 seconds and accepts a positive `PODCASTER_CLIP_VISIBILITY_TIMEOUT` override; it is not tied programmatically to capture/finalization time. | High |
| Clip duration limit | `podcaster/video/video_gen.py:170-215` | **Supports:** the default effective capture duration is 600 seconds, environment-overridable and disable-able with a non-positive value. The explanatory target is headroom beneath the 900-second recorder replica timeout. | High |
| Navigation and retry waits | `podcaster/video/video_gen.py:110-139`, `podcaster/video/video_gen.py:1890-1939`, `podcaster/video/video_gen.py:1979-2033` | **Supports:** general `networkidle` is 10 seconds, GitHub is 60 seconds, websites are 8 seconds, and repo recovery adds 1/3/5-second backoff; a GitHub timeout can still succeed if content is present. | High |
| Capture/finalization process timeout | `podcaster/video/video_gen.py:1150-1177`, `podcaster/video/video_gen.py:1231-1269` | **Supports:** screenshot-frame ffmpeg composition has a separate 600-second subprocess timeout; context close finalizes browser capture. These are independent phase limits rather than a shared budget. | High |
| Cookie / settle waits | `podcaster/video/video_gen.py:539-689`, `podcaster/video/video_gen.py:2090-2148`, `podcaster/video/video_gen.py:2335-2419` | **Supports:** consent dismissal is separately capped at 2500ms; recording also has page-settle and scroll/hold waits. A failure generally falls back or preserves usable content. | Medium |

### Fan-out, terminal state, abandonment, and retry

| Delegated question | Fact evidence | Finding / evidence relationship | Confidence |
| --- | --- | --- | --- |
| Fan-out enablement / insertion boundary | `podcaster/video/job_runner.py:734-750`, `podcaster/video/job_runner.py:774-793`, `podcaster/video/job_runner.py:1029-1076` | **Supports:** fan-out is on only with scratch and a producer (unless explicit override), then `run_video_generation` calls `record_via_fanout`; otherwise it uses in-process `record_episode`. This call boundary is a likely parent-deadline propagation point. | High |
| Plan immutability | `podcaster/video/editor.py:179-209`, `podcaster/video/clipset.py:156-218` | **Supports:** first editor create-if-absent CAS-writes `clipset.json`; redelivery re-reads the settled plan, preventing expected-index drift. Clipset validates job ID, index type/range, and declared count. | High |
| Fan-in terminal sentinel | `podcaster/video/clipset.py:51-63`, `podcaster/video/editor.py:212-288`, `podcaster/video/editor.py:291-335` | **Supports:** a per-index manifest, written after the size-verified clip, is the completion sentinel. The barrier checks each expected sentinel rather than listing blobs. On timeout, unverified/missing clips are replaced locally by fallback cards. | High |
| Recorder duplicate / late-writer race | `podcaster/video/recorder.py:160-182`, `podcaster/video/recorder.py:185-275`, `podcaster/storage.py:307-333` | **Supports:** the recorder checks the manifest before and after recording; uploads then verifies the clip size; terminal manifest creation uses `update_bytes` CAS (`If-Match` / `If-None-Match` in Azure) so a late recorder cannot overwrite the winning manifest. Duplicate raw clip upload may be last-write-wins, described as harmless only because the terminal manifest wins. | High |
| Poison / retry / abandon | `podcaster/video/recorder.py:339-395`, `podcaster/video/job_runner.py:1685-1749` | **Supports:** malformed clip/video messages are deleted; transient recorder/video errors remain for retry; at dequeue count >= 5 clip poison writes a terminal fallback then deletes, while video poison reports `retry_exhausted` and deletes. Permanent video errors delete immediately. | High |
| Editor lease race control | `podcaster/video/editor.py:81-176`, `podcaster/video/job_runner.py:851-865`, `podcaster/video/job_runner.py:1032-1055`, `podcaster/video/job_runner.py:1738-1749` | **Supports:** separate 1800-second UTC-wall-clock lease uses CAS; heartbeats renew during fan-in; foreign unexpired owner causes no-op and queue redelivery. It protects duplicate editor work, not elapsed stage budgeting. | High |
| Post-success cleanup | `podcaster/video/job_runner.py:1404-1411`, `podcaster/video/editor.py:338-345` | **Supports:** scratch clips are best-effort deleted only after successful compose; clipset survives the clips-prefix deletion. | High |

### Recorder behavior: visibility, soft/hard handling, navigation, capture, finalization

- **Visibility:** clip-message receive uses 900 seconds by default; the code has no renewal/extension API during an active recording. `podcaster/video/recorder.py:64-68`, `podcaster/video/recorder.py:490-504`.
- **Soft recovery:** failed repo navigation gets direct/retry/article/fallback progression; if a page was successfully loaded but later polish fails, the recorder keeps that page/partial capture rather than layering a fallback. `podcaster/video/video_gen.py:1979-2033`, `podcaster/video/video_gen.py:2328-2419`.
- **Hard / terminal outcomes:** recorder exceptions are not made terminal immediately; they return retry until poison count, after which a fallback manifest lets the barrier converge. `podcaster/video/recorder.py:339-395`. Invalid/malformed queue envelopes are hard poison and are deleted. `podcaster/queue.py:145-190`.
- **Capture and finalization:** actual per-segment duration is cap-clamped, recording uses a new context, and finalization closes the context then either ffmpeg-composes screenshot frames or renames the finalized Playwright WebM. `podcaster/video/video_gen.py:170-215`, `podcaster/video/video_gen.py:1181-1269`, `podcaster/video/recorder.py:443-477`.
- **Contradiction / terminology caution:** comments describe the 600-second clip cap as providing headroom under a 900-second ACA `replicaTimeout` (`podcaster/video/video_gen.py:181-191`), while the implementation evidenced here only controls the queue visibility default (`podcaster/video/recorder.py:64-68`); the ACA deployment timeout was not inspected in this lane. Treat the actual configured platform deadline as unverified.

### Safeguards: poison messages, plan-before-clip, and SSRF

| Safeguard | Evidence relationship |
| --- | --- |
| Poison envelopes | **Supports:** encoder requires nonblank job ID and nonnegative integer index; parser rejects malformed/mismatched schema messages, although it intentionally accepts raw JSON and absent schema for compatibility. `podcaster/queue.py:103-190`; test coverage `tests/test_clip_queue.py:25-125`. |
| Clip-before-manifest / clip-before-plan | **Supports:** no manifest is emitted before upload and optional size verification; a `.webm` without terminal manifest is deliberately re-recorded or gap-filled. `podcaster/video/recorder.py:205-275`, `podcaster/video/editor.py:318-332`; tests `tests/test_recorder.py:95-111`, `tests/test_editor.py:273-309`. **Leaves unresolved:** `load_clipset` failing because the plan is missing is only retried until poison; the eventual fallback uses `repo_url=None`, so it converges but does not prove plan integrity. `podcaster/video/recorder.py:121-124`, `podcaster/video/recorder.py:279-332`. |
| SSRF for guarded HTTP | **Supports:** `safe_urlopen` permits only HTTP(S), fails closed on private/metadata/unresolvable hosts, rechecks redirects and connect-time resolved addresses. `podcaster/ssrf.py:1-24`, `podcaster/ssrf.py:109-190`, `podcaster/ssrf.py:190-374`. Watermark queue-lifecycle tests cover DNS transient vs private-address permanent behavior. `tests/test_video_job_runner.py:2800-3098`. |
| SSRF in recorder browser navigation | **Leaves unresolved / weakens an end-to-end safeguard claim:** generic `segment.source_url`, repo URL recovery, and extracted project website URLs are sent to Playwright `page.goto` in the recorded flow, whereas the cited `safe_urlopen` guard applies to Python HTTP. `podcaster/video/video_gen.py:1890-1939`, `podcaster/video/video_gen.py:2090-2148`, `podcaster/video/video_gen.py:2315-2373`. This lane found URL-format checks for GitHub repo URLs (`podcaster/video/video_gen.py:1813-1841`) but no demonstrated DNS/private-IP guard immediately before these browser navigations. |

### Test seams

- The barrier accepts injected `sleep`, `monotonic`, `on_poll`; fan-out accepts `fill_gap` and `heartbeat`, allowing deterministic timeout/lease tests. `podcaster/video/editor.py:245-288`, `podcaster/video/editor.py:347-390`; `tests/test_editor.py:186-465`.
- Recorder accepts a fake queue, scratch backend, injected segment recorder, and environment; fake browser mode avoids Chromium. `podcaster/video/recorder.py:185-195`, `podcaster/video/recorder.py:418-477`; `tests/test_recorder.py:24-358`.
- Video runner accepts configuration, `now`, compose runner, fan-out override, scratch backend, and clip producer. `podcaster/video/job_runner.py:774-793`; queue disposition and visibility have dedicated tests. `tests/test_video_job_runner.py:1830-1905`, `tests/test_video_job_runner.py:2770-2812`.
- Pool has injectable browser launch/record functions and Playwright factory, but no parent deadline parameter. `podcaster/video/recording_pool.py:127-230`.

## Likely insertion points for one parent monotonic deadline (evidence, not a recommendation)

1. **Create / own budget at video-run entry:** `run_video_generation` is the common path immediately before planning and recording (`podcaster/video/job_runner.py:774-865`); it currently receives `now` only for state/lease use, not a monotonic deadline.
2. **Pass remaining budget to fan-in:** `record_via_fanout` and `wait_for_fanin` already expose `timeout_seconds`, injectable `monotonic`, and polling (`podcaster/video/editor.py:245-288`, `podcaster/video/editor.py:347-390`). Current default remains an independent fixed 5400 seconds.
3. **Apply remaining budget at recorder work:** `record_clip` delegates to `_production_record_segment` without an elapsed-budget contract (`podcaster/video/recorder.py:185-275`, `podcaster/video/recorder.py:443-477`); clip visibility is separately selected at receive time.
4. **Thread through navigation/capture/finalization:** navigation `page.goto`, wait/settle, scroll, cookie handling, and the 600-second ffmpeg subprocess each own fixed limits (`podcaster/video/video_gen.py:110-139`, `podcaster/video/video_gen.py:1150-1269`, `podcaster/video/video_gen.py:2090-2435`).
5. **Coordinate with queue/lease semantics:** editor visibility and a UTC lease are distinct mechanisms (`podcaster/video/job_runner.py:761-771`, `podcaster/video/editor.py:126-176`). Evidence does not show either receives a single monotonic deadline or guarantees it outlives all remaining work.

## Evidence relationships for parent synthesis

- Fan-out enabled → `record_via_fanout` → monotonic 5400-second manifest barrier → partial assemble with fallback cards: `podcaster/video/job_runner.py:1029-1076`; `podcaster/video/editor.py:245-390`. **Supports** a bounded fan-in insertion seam, but **leaves unresolved** a shared end-to-end budget.
- Clip queue delivery → 900-second visibility → record / size verify → CAS terminal manifest → delete or retry/poison fallback: `podcaster/video/recorder.py:64-68`; `podcaster/video/recorder.py:160-395`. **Supports** convergence and duplicate safety; **leaves unresolved** active-message visibility renewal and a shared deadline.
- Editor retry/redelivery → CAS clipset and editor lease → additive enqueue only absent sentinels: `podcaster/video/editor.py:126-255`; `podcaster/video/job_runner.py:851-865`. **Supports** stable plan and anti-concurrency properties; **does not establish** monotonic timing coherence because lease is UTC-expiry based.
- Capture navigation → fixed per-action waits + fallback/preserve behavior → context close / ffmpeg finalization: `podcaster/video/video_gen.py:110-139`; `podcaster/video/video_gen.py:1979-2033`; `podcaster/video/video_gen.py:2090-2435`. **Supports** several soft/hard recovery paths; **weakens** a claim that the recorder has one hard process deadline.
- SSRF helper → guarded Python fetches: `podcaster/ssrf.py:109-374`. **Supports** watermark-style HTTP safeguards; browser `page.goto` paths above **leave recorder-navigation SSRF coverage unresolved**.

## Gaps and contradictions

1. No single monotonic parent deadline object/parameter or explicit remaining-time calculation was located across editor, recorder, browser navigation, capture, compose, or distribution.
2. The 5400-second fan-in bound equals the default editor visibility timeout, despite comments saying visibility must include additional compose/publish time; this is a timing-risk relationship, not proof of a runtime failure. `podcaster/video/editor.py:69-74`; `podcaster/video/job_runner.py:127-132`.
3. The 900-second clip visibility and 600-second capture cap leave nominal setup/upload headroom but do not evidence a hard cancellation/kill deadline in recorder Python code; the platform `replicaTimeout` configuration is out of this lane's inspected evidence.
4. Browser navigation has recovery and URL-format validation but no workspace evidence of the shared SSRF guard being applied immediately before Playwright navigation; external/derived URLs need parent consideration.
5. Manifest sentinel presence establishes completion, but editor reads manifest extras permissively and does not validate an explicit recorder `status` before using a clip. `podcaster/video/editor.py:394-426`. This may be intentional compatibility behavior; scope cannot establish whether stricter validation is required.
6. Queue parser's acceptance of absent schema is an intentional compatibility exception, so schema stamping is not an absolute poison-message barrier. `podcaster/queue.py:145-190`; `tests/test_clip_queue.py:121-123`.

## Provenance

- Retrieved from workspace on 2026-09-15: `podcaster/video/editor.py:60-390`, `podcaster/video/editor.py:394-466`, `podcaster/video/recorder.py:50-510`, `podcaster/video/clipset.py:1-224`, `podcaster/video/job_runner.py:85-165`, `podcaster/video/job_runner.py:730-773`, `podcaster/video/job_runner.py:774-1080`, `podcaster/video/job_runner.py:1370-1775`.
- Retrieved from workspace on 2026-09-15: `podcaster/video/video_gen.py:100-215`, `podcaster/video/video_gen.py:1150-1269`, `podcaster/video/video_gen.py:1813-2033`, `podcaster/video/video_gen.py:2090-2435`, `podcaster/video/recording_pool.py:45-230`, `podcaster/queue.py:60-350`, `podcaster/storage.py:280-340`, `podcaster/ssrf.py:1-374`.
- Retrieved from workspace on 2026-09-15: `tests/test_editor.py:186-477`, `tests/test_recorder.py:1-358`, `tests/test_clip_queue.py:1-158`, `tests/test_clipset.py:1-113`, `tests/test_clip_manifest.py:1-199`, `tests/test_video_job_runner.py:1830-1905`, `tests/test_video_job_runner.py:2770-3098`.

## Stop decision

- **Reason:** lane criteria met and source results saturated. The bounded static architecture, timing defaults, fan-in location, retries/terminal behavior, CAS race handling, safeguards, and test seams have direct evidence. Remaining items require parent scope decisions or non-inspected deployment/execution evidence.
