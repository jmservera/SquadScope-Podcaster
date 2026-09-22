<!-- markdownlint-disable-file -->
# RPI Phase Details: Video Stage Budget Redesign

## Metadata

* Task ID: video-stage-budget-redesign
* Task slug: video-stage-budget-redesign
* Related plan: .copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md
* Evidence sources: .copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md and its delegated lane artifacts

## Phase Index

| Phase ID | Name | Status | Detail sections |
|---|---|---|---|
| P01 | Establish shared budget and evidence contracts | Complete | P01, P01-T01, P01-T02 |
| P02 | Bound recorder fan-in and deterministic fallback | Complete | P02, P02-T01, P02-T02 |
| P03 | Bound render, archive, and resume | Complete | P03, P03-T01, P03-T02, P03-T03 |
| P04 | Persist render boundary and bound distribution/shutdown | Complete | P04, P04-T01, P04-T02, P04-T03 |
| P05 | Validate, review, follow up, and deliver | Complete | P05, P05-T01, P05-T02, P05-T03 |
| P06 | Fail closed audio-only publication | Complete | P06, P06-T01, P06-T02 |
| P07 | Remediate unresolved PR review findings | Complete | P07, P07-T01, P07-T02, P07-T03, P07-T04, P07-T05 |
| P08 | Complete focused PR gate remediation | Active: P08-T01-P08-T02 ready | P08, P08-T01, P08-T02, P08-T03 |

<!-- rpi:phase id=P01 -->
## P01: Establish shared budget and evidence contracts

### Context

Research C2-C20 proves strong atomic/CAS seams but no run-wide stage budget, process-tree cancellation, hash-bound terminal media, or complete validation schema.

### Intent

Create compatible shared abstractions and append-only state/evidence foundations before wiring behavior.

### Boundaries

* Included: Budget/stage constants, conservative local/durable remaining-time rules, cancellation/process helper, media validation metadata, evidence schema.
* Excluded: Behavior wiring owned by later phases and production operations.

### Likely Targets

* `podcaster/video/budget.py`: Shared clock, cutoffs, admission, timeout reason model.
* Existing subprocess/intermediate/state helpers: Reuse or add the smallest common cancellation/validation seam.
* Tests: Fake-clock and helper-level behavior.

### Dependencies

* Research C2-C20 and caller binding budgets.

### Validation Expectations

* Exact stage constants and earlier-of deadline semantics are deterministic under fake clocks.

### Completion Evidence

* Focused tests and changes-record entries.

### Unresolved Items

* None.

<!-- rpi:task id=P01-T01 -->
### P01-T01: Implement shared stage budget and conservative cross-process projection

#### Context

`wait_for_fanin` has an injectable monotonic clock, but the editor/recorder pipeline has no common lifetime. A monotonic epoch is process-local, so recorder children require durable UTC start/cutoff fields while each process uses its own monotonic clock. Clock disagreement must never extend the budget.

#### Intent

Add one immutable budget object with exact stage cutoffs and a conservative earlier-of remaining-time rule for local and durable projections.

#### Boundaries

* Included: Constants 300/1200/1500/3300/3600/4500/4920/5100, local monotonic origin, durable UTC origin/cutoffs, stage admission/timeout helpers, serialized recorder timing envelope, per-clip durable first-admission timestamp, timeout/cancellation reason types.
* Excluded: Stage-specific behavior beyond helper wiring.

#### Likely Targets

* New `podcaster/video/budget.py`.
* `podcaster/video/job_runner.py`, queue/clipset metadata as needed for durable child timing.
* New focused `tests/test_video_budget.py` (one of at most two new Python test files).

#### Dependencies

* None beyond existing clock/test injection conventions.

#### Validation Expectations

* Boundary tests at each exact cutoff; UTC skew/delayed-redelivery tests choose the earlier remaining value; clip elapsed time never resets on redelivery; production entry always creates/loads a budget while optional lower-level arguments preserve legacy test/call compatibility.

#### Completion Evidence

* Focused helper tests and documented constants.

#### Unresolved Items

* None; use no positive clock-skew grace.

<!-- rpi:task id=P01-T02 -->
### P01-T02: Implement bounded process, storage, validation, and evidence foundations

#### Context

Default normalization/EDL commands have no timeout, direct screenshot/probe calls bypass shared runners, storage streams lack a visible parent deadline, and reusable media lacks a common validation/evidence record.

#### Intent

Provide reusable bounded-operation and media-evidence primitives without breaking existing injected runners/backends.

#### Boundaries

* Included: Process-group/session launch, bounded terminate/kill/reap, optional deadline-aware command adapter, operation timeout derivation, SHA-256/size/probe evidence types, sanitized append-only stage/attempt/heartbeat/timeout records.
* Excluded: Replacing storage protocols globally. Documentation-only residual cancellation is not accepted for Chromium.

#### Likely Targets

* New `podcaster/video/process.py` only if existing helpers cannot safely own process trees.
* `podcaster/video/intermediates.py`, `podcaster/video/video_compose.py`, `podcaster/video/video_gen.py`, storage wrappers, publication/job evidence seams.
* Helper tests in existing compose/intermediate modules or `tests/test_video_budget.py`.

#### Dependencies

* P01-T01.

#### Validation Expectations

* Spawned command and browser-like child/grandchild descendants are terminated and reaped through an owned cancellable process boundary; timeout output is rejected; zero-byte and failed-probe evidence cannot become complete; evidence is bounded/sanitized.

#### Completion Evidence

* Focused process, validation, and evidence tests.

#### Unresolved Items

* Blocking browser work must run behind the owned process boundary. Storage SDK calls must use supported operation timeouts or an owned cancellable boundary and fail closed.

<!-- rpi:phase id=P02 -->
## P02: Bound recorder fan-in and deterministic fallback

### Context

Fan-in currently consumes 5400 seconds, recorder failures terminalize only at poison count 5, and production gap filling launches Chromium.

### Intent

Finish recorder decisions by T+25 with immutable validated browser-free fallback.

### Boundaries

* Included: Queue admission/visibility/retry, T+20 fan-in, earliest abandonment, terminal CAS/hash, fallback cards, late recorder race.
* Excluded: Composition/provider work.

### Likely Targets

* `podcaster/video/editor.py`, `recorder.py`, `video_gen.py`, clip manifest/clipset helpers, ACA recorder Bicep, focused tests.

### Dependencies

* P01.

### Validation Expectations

* Missing/slow/crashed/poison/browser-hung clips converge without post-cutoff browser work; late writers cannot alter terminal media identity.

### Completion Evidence

* Focused recorder/editor/scale-out tests.

### Unresolved Items

* None.

<!-- rpi:task id=P02-T01 -->
### P02-T01: Enforce recorder admission, abandonment, retry, and fan-in cutoffs

#### Context

Fan-in currently defaults to 5400 seconds and recorder terminal fallback waits for dequeue count five. Queue visibility is selected once and retries are not parent-budget-aware.

#### Intent

Stop fan-out/fan-in by 1200 seconds and make each clip terminal at the earliest binding abandonment condition.

#### Boundaries

* Included: T+5 preflight admission; parent-bounded fan-in timeout/polling/heartbeat; recorder visibility and local hard deadline; 720 seconds measured from durable first recorder admission across redelivery; persisted attempt/failure history; two failed dequeued executions; browser deadline; retry eligibility; T+20 cutoff.
* Excluded: Lowering poison-message ceiling below five for malformed/general poison handling; terminal fallback creation is P02-T02.

#### Likely Targets

* `podcaster/video/job_runner.py`, `editor.py`, `recorder.py`, `video_gen.py`, clipset/manifest state, `infra/modules/aca-recorder.bicep`.
* `tests/test_editor.py`, `tests/test_recorder.py`, `tests/test_video_job_runner.py`, `tests/test_video_gen.py`, integration fan-out tests.

#### Dependencies

* P01.

#### Validation Expectations

* Effective clip deadline is the earliest conservative local/durable value among first-admission+720, configured browser hard deadline, recorder visibility/ACA reserve, and T+1200; two failed executions terminalize immediately. Tests cover equality, skew, delayed delivery, and pairwise/racing precedence. Fan-in never polls past T+20; no retry is admitted without enough remaining parent time; leases, SSRF, poison ceiling, and message disposition retain prior semantics.

#### Completion Evidence

* Fake-clock recorder/fan-in matrix and infrastructure assertions.

#### Unresolved Items

* A failed recorder execution is a dequeued attempt ending without a valid terminal manifest; internal retry attempts remain evidence but do not count as separate executions.

<!-- rpi:task id=P02-T02 -->
### P02-T02: Produce immutable browser-free fallback or recording-insufficient outcome

#### Context

Current post-fan-in gap filling invokes production recording/Chromium. Manifest CAS protects bytes of the sentinel but not the unconditionally uploaded raw clip.

#### Intent

Resolve all missing clips by T+25 without network/Chromium and ensure the winning terminal decision binds the exact consumed media.

#### Boundaries

* Included: Deterministic static card generation from local metadata/assets, content-addressed media, size/hash/probe validation, terminal manifest status/reason/timestamps/attempts, CAS, quality-policy denial and `recording_insufficient`, late-writer cleanup/ignore behavior.
* Excluded: Any fresh URL navigation or browser capture after T+20.

#### Likely Targets

* `podcaster/video/editor.py`, `recorder.py`, `edl_render.py`/section-card helpers, `clip_manifest.py`, `clipset.py`.
* `tests/test_editor.py`, `tests/test_recorder.py`, `tests/test_video_compose.py` or section-card tests.

#### Dependencies

* P02-T01 and P01 validation/process helpers.

#### Validation Expectations

* Fallback uses no Playwright/network; identical inputs produce stable content; output is decodable; concurrent success/fallback/late upload cannot change the hash-bound terminal artifact; quality denial stops before composition/providers.

#### Completion Evidence

* Determinism, decodability, and interleaved-race tests.

#### Unresolved Items

* Use a repository/container-owned fixed font/asset contract and fixed rendering metadata. Missing renderer/assets or renderer timeout produces `recording_insufficient`; never fall back to Chromium/network.

<!-- rpi:phase id=P03 -->
## P03: Bound render, archive, and resume

### Context

Normalization/EDL subprocesses are unbounded and checkpoint validation is partial or size-only.

### Intent

Complete composition by T+55, verified archive/readback by T+60, and reuse only validated work.

### Boundaries

* Included: Chromium/FFmpeg/ffprobe/storage deadlines, process-tree cancellation, checksum/probe validation, partial-output rejection, replay.
* Excluded: Provider mutation.

### Likely Targets

* `podcaster/video/video_gen.py`, `video_compose.py`, `edl_render.py`, `intermediates.py`, storage/archive seams, tests.

### Dependencies

* P01-P02.

### Validation Expectations

* Hang/delay tests cancel; corrupt/equal-size/zero-byte media recomputes or fails closed; valid checkpoints replay.

### Completion Evidence

* Focused compose/intermediate/job-runner tests.

### Unresolved Items

* None.

<!-- rpi:task id=P03-T01 -->
### P03-T01: Enforce stage-aware browser and media process cancellation

#### Context

FFmpeg/ffprobe invocation is split across injected and direct `subprocess.run` paths; browser cleanup is cooperative but not a proven hard cancellation mechanism.

#### Intent

Prevent any new normalize/compose/mux work at or after T+55 and reap all owned subprocess trees on timeout.

#### Boundaries

* Included: Budget propagation to compose, normalization, EDL, screenshot finalization, audio probe/mux, and browser actions; process-group termination; bounded stderr; partial output removal.
* Excluded: Unowned browser descendants. Blocking browser work must run behind an owned cancellable boundary and prove descendant cleanup.

#### Likely Targets

* `podcaster/video/video_gen.py`, `video_compose.py`, `edl_render.py`, `job_runner.py`, process helper.
* `tests/test_video_gen.py`, `tests/test_video_compose.py`, `tests/test_video_job_runner.py`.

#### Dependencies

* P01 and P02.

#### Validation Expectations

* Browser-like child/grandchild, FFmpeg, and ffprobe hang cases return typed timeout/cancellation, no new work starts at T+55, descendants are reaped, and partial output is not checkpointed.

#### Completion Evidence

* Process-tree fixture plus fake-clock stage tests.

#### Unresolved Items

* Playwright API timeouts are the first layer; the owned process boundary is the hard containment layer.

<!-- rpi:task id=P03-T02 -->
### P03-T02: Validate and reuse immutable media checkpoints

#### Context

Audio has SHA evidence, while raw clips, normalized segments, and composed checkpoints are sidecar/size/existence based and can trust corrupt equal-size bytes.

#### Intent

Reuse only media whose schema, logical identity, input identity, bytes, and decodability match.

#### Boundaries

* Included: Versioned validation record for audio, clipset, terminal clip media/manifests, normalized segments, and composed video; atomic download; size/SHA/probe/stream/duration checks; legacy/malformed/unknown metadata as cache miss.
* Excluded: Backfilling historical production objects.

#### Likely Targets

* `podcaster/video/intermediates.py`, `video_gen.py`, `video_compose.py`, `clip_manifest.py`, `clipset.py`, runner audio validation.
* `tests/test_video_intermediates.py`, `tests/test_video_gen.py`, `tests/test_video_compose.py`, `tests/test_editor.py`.

#### Dependencies

* P01-T02 and P02-T02.

#### Validation Expectations

* Valid replay bypasses work; missing sidecar, unknown schema, wrong identity, zero/truncated/equal-size altered bytes, hash mismatch, absent stream, or failed probe recomputes only when budget permits and otherwise stops safely.

#### Completion Evidence

* Full checkpoint migration/corruption/replay matrix.

#### Unresolved Items

* None; backward compatibility means readability and safe cache miss, not blind reuse.

<!-- rpi:task id=P03-T03 -->
### P03-T03: Complete verified archive and readback by T+60

#### Context

Archive work currently reads the final MP4 and lacks the required checksum/probe/readback stage reserve.

#### Intent

Reserve T+55–T+60 for final validation, archive upload, checksum, media probe, and readback only. Because the 1800-second provider reserve makes T+55 the effective latest mutation admission, any boundary persisted after T+55 is pending-only and starts no provider intent.

#### Boundaries

* Included: Deadline-aware archive wrapper, atomic staging/promotion where supported, streaming hash where practical, readback identity, validation evidence, timeout cleanup.
* Excluded: Provider identity, intent, or mutation after the admission check; archive work may finish through T+60 but cannot borrow provider reserve.

#### Likely Targets

* `podcaster/video/distribution.py` archive helper or a pre-distribution archive seam, storage/intermediate helpers, runner.
* `tests/test_video_distribution.py`, `tests/test_video_intermediates.py`, `tests/test_video_job_runner.py`.

#### Dependencies

* P03-T01 and P03-T02.

#### Validation Expectations

* Storage delay/hang stops by T+60; zero/truncated/corrupt/equal-size altered readback is rejected; tests cover archive completion at 3300, 3301, and 3600 and prove late completion persists pending-only with no provider intent.

#### Completion Evidence

* Fake-clock storage/readback tests and immutable archive identity.

#### Unresolved Items

* None.

<!-- rpi:phase id=P06 -->
## P06: Fail closed audio-only publication

### Context

Synthesis currently has direct `auto_publish_job` and `publish_episode` paths after audio validation. Those paths bypass the explicit review/manual orchestration gate and can make an audio-only job appear externally complete.

### Intent

Make synthesis responsible only for staged audio, readiness state, and independent video enqueue. Keep all Spotify mutation behind `process_review_decision(..., decision="approved")` or an explicit operator call to `publish_staged_job`.

### Boundaries

* Included: Synthesis publication state, direct publish removal, approved/manual orchestration preservation, ambiguous outcome semantics, integration coverage, conflicting docs, PR evidence.
* Excluded: Provider API changes, video publication behavior changes, deployment, production/W38 mutation.

### Dependencies

* Existing `publishing.blocked_by`, `eligible`, `packet_ready`, and `readiness_checks` contracts.
* Existing canonical identity and `publication_unknown`, `draft_created`, `manual_handoff_required`, and `published` orchestration states.

<!-- rpi:task id=P06-T01 -->
### P06-T01: Remove synthesis-time Spotify mutation bypass

#### Validation Expectations

* `run_synthesis` never calls `publish_episode` or `auto_publish_job` without an explicit approved/manual gate.
* Successful validated audio remains packet-ready but human-review-blocked and not eligible until approval.
* Approved `process_review_decision` still calls `publish_staged_job` and publishes.
* Ambiguous approved publication remains non-final and retry-blocked.
* Audio staging and video draft/live behavior remain independent.
* `podcaster/job_runner.py` and `docs/PRD.md` no longer promise automatic Spotify drafts.

<!-- rpi:task id=P06-T02 -->
### P06-T02: Validate, independently review, and update PR delivery

#### Validation Expectations

* Focused job runner, orchestration, publication integration, and video-independence tests pass.
* `pytest tests/ -q`, Ruff lint/format, compileall, and `git diff --check` pass.
* Independent reviewer accepts the exact audio-gate change; rejected findings use separate lockout implementation.
* A conventional follow-up commit with required trailers is pushed to the existing PR #682 branch.
* PR #682 description/test evidence is updated; PR remains open and unmerged, and hosted checks start.

<!-- rpi:phase id=P07 -->
## P07: Remediate unresolved PR review findings

### Context

PR #682 received 13 unresolved review threads after P06 was independently accepted and pushed in `eecaffc`. The original Copilot author is locked out of this correction cycle. This design review assigns Bender as the sole correction implementer, Fry as independent functional/test reviewer, and Hermes as independent safeguard/security reviewer.

### Intent

Apply the narrowest backward-compatible corrections, preserve completed P01-P06 history, and prove that publication, lease, CAS, SSRF, poison, provider ambiguity/no-repeat, and public-verification gates remain at least as strict.

### Boundaries

* Included writes: `podcaster/video/editor.py`, `edl_render.py`, `intermediates.py`, `job_runner.py`, `process.py`, `video_gen.py`, `youtube.py`, `distribution.py`, `recorder.py`; `infra/modules/aca-recorder.bicep` and only directly threaded deployment parameters/docs; focused tests in existing files; RPI/PR evidence.
* Excluded writes: unrelated synthesis/audio APIs, provider credentials/config payloads, new public response fields, production state, deployment, W38 state, merge, thread resolution before acceptance, and the separate distribution worker tracked by `jmservera/SquadScope-Podcaster#681`.
* Bender must not broaden cleanup prefixes, weaken queue poison deletion, bypass editor/recorder leases, replace conditional manifest writes, relax SSRF checks, retry ambiguous provider mutations, or aggregate draft/private/unlisted/readback/unknown as externally public.

### Dependency and Assignment Board

| Task | Owner | Dependencies | Reviewer |
|---|---|---|---|
| P07-T01 | Bender | P06 and this design review | Fry, then Hermes where process/storage safety overlaps |
| P07-T02 | Bender | P06 and this design review | Fry + Hermes |
| P07-T03 | Bender | P06 and baseline provider-state reconciliation | Hermes + Fry |
| P07-T04 | Bender | P01 budget helpers and this design review | Fry + Hermes |
| P07-T05 | Fry and Hermes, read-only | P07-T01-P07-T04 implementation and focused validation | Leela final readiness |

### Thread-to-Correction and Evidence Matrix

| Thread / URL | Safest backward-compatible correction | Focused regression evidence | Comprehensive checks | Docs/infra |
|---|---|---|---|---|
| `PRRT_kwDOSzuis86iosWy` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018616332 | On malformed clipset, delete only `clipset_blob_path(job_id)` and `clips_prefix(job_id)` before CAS recreation; retain intermediates/checkpoints and unrelated job scratch. | Extend `test_budgeted_clipset_rejects_malformed_cache_and_recomputes` to prove stale clip data is removed while an intermediate and unrelated job artifact survive. | `pytest -q tests/test_editor.py tests/test_video_job_runner.py` | No. |
| `PRRT_kwDOSzuis86iosXi` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018616414 | Unlink `output_path` before raising on nonzero ffmpeg return, matching timeout/exception/empty-output behavior. | `tests/test_edl_render.py`: runner writes partial output and returns nonzero; output is absent and stderr remains reported. | `pytest -q tests/test_edl_render.py tests/test_video_compose.py tests/test_video_process.py` | No. |
| `PRRT_kwDOSzuis86iosYL` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018616473 | Put unique `.part` cleanup in `finally`; preserve a pre-existing valid destination because replacement never occurred. | `tests/test_video_intermediates.py`: failing backend creates partial temporary data and returns false; no `.part` remains and an existing destination is unchanged. | `pytest -q tests/test_video_intermediates.py tests/test_video_gen.py tests/test_video_compose.py` | No. |
| `PRRT_kwDOSzuis86ipakb` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903541 | Make recorder visibility cover the full ACA execution (default equal to the 840-second replica timeout) while P07-T04 bounds validation/uploads/readback/CAS inside the recorder deadline. | Update recorder Bicep assertions and browser-deadline tests; add a finalization-near-capture-limit case proving no visibility gap. | `pytest -q tests/test_recorder.py tests/test_deploy_workflow.py`; Bicep build and Checkov for changed infra. | Infra and `docs/scaleout-recorder-rfc.md` validation required. |
| `PRRT_kwDOSzuis86ipak9` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903592 | For `rendered_pending_distribution` while conservative provider admission is still usable, perform a shutdown-budgeted send-first replacement enqueue and delete the current message, avoiding the original 5400-second visibility delay. If admission is already impossible, retain durable pending state for `jmservera/SquadScope-Podcaster#681` and do not hot-loop or mutate. | Queue-order tests for enqueue-before-delete, send/delete ambiguity, still-admissible immediate handoff, and exhausted-admission durable retention. | `pytest -q tests/test_video_job_runner.py tests/test_video_distribution.py tests/test_video_budget.py tests/test_clip_queue.py`; preserve poison ceiling. | Update deployment/runbook wording only if queue behavior text changes; Bicep defaults need not be shortened. |
| `PRRT_kwDOSzuis86ipalc` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903639 | Acquire/renew the editor lease before `_resume_rendered_pending_distribution`; ensure every resumed exit releases only the owned lease. | Two redeliveries with the same pipeline lock: one owns distribution, the other returns lease-held without provider mutation; release-on-success/failure/pending cases. | `pytest -q tests/test_video_job_runner.py tests/test_video_distribution.py` | No. |
| `PRRT_kwDOSzuis86ipal6` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903679 | After SIGKILL, use a bounded final `communicate`; on expiry close owned pipes, retain bounded captured output, reap within grace, remove outputs, and raise timeout. | Escaped descendant retains stdout/stderr after parent kill; runner returns within the asserted bound and removes partial output. | `pytest -q tests/test_video_process.py tests/test_video_compose.py tests/test_edl_render.py` | No. |
| `PRRT_kwDOSzuis86ipamb` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903724 | Scan and validate checkpoints before the Playwright guard; require Playwright only when `needs_browser` is true. | Full valid replay with `_PLAYWRIGHT_AVAILABLE=False` succeeds without browser; partial replay still raises before recording. | `pytest -q tests/test_video_gen.py tests/test_video_intermediates.py tests/test_video_compose.py` | No. |
| `PRRT_kwDOSzuis86ipanQ` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903793 | Treat transport loss after resumable-session POST as mutation-ambiguous, bind it to the stable job/publish identity, persist `publication_unknown` with `retry_blocked=true`, and never start another session on replay. | Small and large upload init transport-loss tests; persisted unknown/no-repeat replay test; deterministic identity equality test. | `pytest -q tests/test_youtube_upload.py tests/test_video_distribution.py tests/test_video_job_runner.py tests/test_youtube_publish.py tests/test_publication_state.py` | No; PR evidence must call out ambiguity behavior. |
| `PRRT_kwDOSzuis86ipany` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903838 | Route each per-index `blob_exists` probe at both editor fan-in sites through the owned storage runner with remaining stage budget; stop the barrier/assembly path on timeout. | Blocking probe tests at both sites prove no subsequent index is probed and T+1200/fallback deadlines are honored. | `pytest -q tests/test_editor.py tests/test_video_intermediates.py tests/test_video_job_runner.py` | No. |
| `PRRT_kwDOSzuis86ipaoD` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903866 | Return resumed outcomes through `terminal_outcome`; terminal outcomes carry cleanup after queue deletion, pending outcomes retain the callback but do not run it. | Resumed terminal delete-then-cleanup, resumed pending no-cleanup, and cleanup-timeout no-republication tests. | `pytest -q tests/test_video_job_runner.py tests/test_video_distribution.py` | No. |
| `PRRT_kwDOSzuis86ipaoh` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903912 | At both success and fallback finalization sites, wrap every validation/upload/size/readback/legacy-upload/attempt-finalization/manifest-CAS storage call with the remaining recorder budget; timeout fails closed before later calls. | Parameterized blocking-operation tests for both sites, including partial content cleanup, no manifest after timeout, and winning CAS preservation. | `pytest -q tests/test_recorder.py tests/test_editor.py tests/test_video_process.py tests/integration/test_scaleout_fanout.py` | Recorder infra validation required with the visibility change. |
| `PRRT_kwDOSzuis86ipao6` / https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903953 | ACA `main()` calls `drain(..., max_messages=1)`; keep `drain`'s explicit test/local override compatible. | Entrypoint unit test asserts one-message cap; existing explicit multi-message drain test remains green. | `pytest -q tests/test_recorder.py tests/integration/test_scaleout_fanout.py` | No separate docs unless entrypoint wording claims queue drain. |

### P07-T01: Correct artifact cleanup, process reap, and browser-free replay

#### Dependencies

* P06 complete and Bender assignment active.

#### Validation Expectations

* Implement the exact corrections for threads `PRRT_kwDOSzuis86iosWy`, `PRRT_kwDOSzuis86iosXi`, `PRRT_kwDOSzuis86iosYL`, `PRRT_kwDOSzuis86ipal6`, and `PRRT_kwDOSzuis86ipamb`.
* Preserve atomic replacement, valid cached artifacts, bounded output capture, and checkpoint size/hash/probe validation.

#### Completion Evidence

* Focused tests from the matrix pass and no cleanup expands beyond the affected artifact namespace.

### P07-T02: Correct lease, redelivery, ownership, cleanup, and ACA entrypoint behavior

#### Dependencies

* P06 complete; may proceed in parallel with P07-T01, P07-T03, and P07-T04 in Bender's single correction branch.

#### Validation Expectations

* Implement threads `PRRT_kwDOSzuis86ipakb`, `PRRT_kwDOSzuis86ipak9`, `PRRT_kwDOSzuis86ipalc`, `PRRT_kwDOSzuis86ipaoD`, and `PRRT_kwDOSzuis86ipao6`.
* Send-first handoff is budgeted and preserves poison semantics; ownership precedes every provider path; queue deletion still precedes optional cleanup.

#### Completion Evidence

* Queue/lease race tests, recorder infra assertions, Bicep build, and Checkov pass.

### P07-T03: Fail closed on ambiguous YouTube resumable-session initiation

#### Dependencies

* Baseline canonical provider identity and `publication_unknown` record schema from `bd59b69`.

#### Validation Expectations

* A request known not to have been sent may remain retryable; any transport loss after the init POST is issued is ambiguous and retry-blocked.
* Both single-request and chunked/resumable helper paths converge on the same deterministic identity and no-repeat evidence.

#### Completion Evidence

* Provider ambiguity/no-repeat and public-verification suites pass; no test expects a second session POST after unknown evidence.

### P07-T04: Budget editor fan-in probes and recorder finalization storage calls

#### Dependencies

* Existing `run_storage_operation`, stage budgets, durable recorder projection, and conditional manifest CAS.

#### Validation Expectations

* Every named call site derives a positive timeout immediately before the call.
* A timed-out owned call cannot perform a late upload/CAS side effect; later operations are not attempted.
* Timeout cannot convert partial media or missing evidence into a terminal success/fallback manifest.

#### Completion Evidence

* Both editor sites and both recorder finalization sites have blocking-call regression tests and pass integration fan-out coverage.

### P07-T05: Validate and independently accept the correction

#### Dependencies

* P07-T01 through P07-T04 implemented by Bender with focused tests passing.

#### Validation Expectations

* Fry runs the matrix's focused commands, `pytest tests/ -q`, `ruff check podcaster tests`, `ruff format --check podcaster tests`, compileall, and `git diff --check`.
* For infra/docs changes, Fry also runs Bicep builds for `aca-recorder.bicep` and any changed parent module plus `checkov --directory infra --framework bicep`.
* Hermes independently verifies SSRF behavior, lease ownership/visibility, poison ceiling, manifest/provider CAS, deterministic identity, publication ambiguity/no-repeat, secrets/PII exclusion, and externally-verified-public aggregation.
* Reviewers remain read-only. A rejection locks Bender out of the next revision and requires a new implementer.

#### Completion Evidence

* Fry and Hermes each record an independent verdict with exact commands/results. Leela confirms all 13 thread IDs are covered before any thread is resolved.

#### Unresolved Items

* None. The separate distribution worker remains `jmservera/SquadScope-Podcaster#681` and is not a P07 blocker.

<!-- rpi:phase id=P08 -->
## P08: Complete focused PR gate remediation

### Context

Hosted CI at head `6acad97` fails the foreign-clipset fallback test, and six Copilot review threads remain unresolved.

### Intent

Perform one narrow correction pass that preserves provider no-repeat, storage verification, stage-budget, ffmpeg selection, and artifact-cleanup semantics.

### Boundaries

* Included: `youtube.py`, `youtube_playlist.py`, `intermediates.py`, `section_cards.py`, `video_gen.py`, the directly related recorder correction already present in the worktree, focused tests, tracking, and PR operations.
* Excluded: Issues `#681`, `#678`, `#679`, merge/auto-merge, deployment, and unrelated refactoring.

### Validation Expectations

* Focused regressions cover every applicable review behavior and the hosted recorder failure.
* Repository-standard full pytest and Ruff checks pass before push.
* Threads are resolved only after the corresponding focused proof passes.

<!-- rpi:task id=P08-T01 -->
### P08-T01: Fail closed on ambiguous provider and storage outcomes

#### Dependencies

* P07 complete.

#### Completion Evidence

* YouTube post-mutation retry exhaustion returns retry-blocked unknown.
* Playlist lookup errors and ambiguous insert outcomes remain fail closed under regression tests.
* Storage size-verification timeout raises and emits no validation sidecar.

<!-- rpi:task id=P08-T02 -->
### P08-T02: Preserve stage budgets and failed-render cleanup

#### Dependencies

* P07 complete.

#### Completion Evidence

* Direct section-card generation selects a drawtext-capable ffmpeg binary under a budget.
* Both recording metadata sidecar reads receive FANIN budget/stage.
* Non-zero screenshot ffmpeg results remove partial output.
* Foreign clipset terminalization retains fallback-rendering budget and passes the hosted failing test.

<!-- rpi:task id=P08-T03 -->
### P08-T03: Validate, resolve proven threads, and deliver

#### Dependencies

* P08-T01 and P08-T02 complete.

#### Completion Evidence

* Focused/full validation, commit SHA, push result, resolved thread IDs, and remote check states are recorded.
* PR remains open, operator-only, and unmerged.

<!-- rpi:phase id=P04 -->
## P04: Persist render boundary and bound distribution/shutdown

### Context

Provider work is in-process with strong baseline evidence/no-repeat semantics but no time admission or durable rendered boundary.

### Intent

Persist verified `rendered_pending_distribution`, admit mutation only with reserve, continue readback/evidence to T+82, and settle graceful disposition by T+85.

### Boundaries

* Included: Durable state, archive/readback evidence, provider admission, partial idempotent retry, publication unknown/no-repeat, shutdown/lease/queue outcome.
* Excluded: Separate distribution worker.

### Likely Targets

* `podcaster/video/job_runner.py`, `distribution.py`, publication state/monitoring seams, tests, runbooks.

### Dependencies

* P01-P03.

### Validation Expectations

* Provider crash/ambiguity/replay matrix preserves all baseline outcomes and public semantics under fake time.

### Completion Evidence

* Focused provider/job/monitoring tests and manifest/evidence assertions.

### Unresolved Items

* None.

<!-- rpi:task id=P04-T01 -->
### P04-T01: Persist verified rendered-pending-distribution state

#### Context

Current runner proceeds from MP4 validation into provider identity/intent in one process and whole-map runner writes can erase additive state.

#### Intent

Create a merge-safe recovery boundary before any provider work.

#### Boundaries

* Included: CAS-persisted `rendered_pending_distribution` with artifact location, size/SHA/probe, source/clipset/audio identity, archive/readback facts, run/timing identity; redelivery validation.
* Excluded: Queue/outbox or separate distribution worker.

#### Likely Targets

* `podcaster/video/job_runner.py`, manifest/state helpers, publication evidence if appropriate.
* `tests/test_video_job_runner.py`.

#### Dependencies

* P03.

#### Validation Expectations

* Crash before/after boundary and replay with valid/invalid archive; no provider intent occurs before successful boundary persistence; later writes preserve the record or terminal successor.

#### Completion Evidence

* State-transition and redelivery tests.

#### Unresolved Items

* None.

<!-- rpi:task id=P04-T02 -->
### P04-T02: Enforce provider mutation reserve and preserve safeguards

#### Context

Current provider admission checks identity/evidence/configuration but not elapsed budget.

#### Intent

Admit mutation only under both independent guards: strictly before 4500 seconds and at least 1800 seconds remaining. Under the 5100-second application lifetime, 3300 seconds is the effective latest admission boundary.

#### Boundaries

* Included: One fail-closed admission decision before mutation intent; pending disposition when denied; timeout propagation into provider transport; canonical identity/evidence/no-repeat; YouTube/Spotify/RSS/notification/public semantics.
* Excluded: New provider APIs or retry broadening.

#### Likely Targets

* `podcaster/video/job_runner.py`, `distribution.py`, provider modules only as necessary for timeout propagation, publication state.
* `tests/test_video_job_runner.py`, `tests/test_video_distribution.py`, `tests/test_publish.py`, publication-state tests.

#### Dependencies

* P04-T01.

#### Validation Expectations

* Integrated boundary matrix covers elapsed 3299/3300/3301, remaining 1801/1800/1799, archive completion at 3300/3301/3600, and independent 4499/4500 guard. Late archive persists `rendered_pending_distribution` and creates no intent. Crash before/after intent/provider mutation remains fail closed; ambiguous outcomes never repeat; public status requires external verification.

#### Completion Evidence

* Provider/admission/crash/replay regression matrix.

#### Unresolved Items

* Exactly 1800 seconds may be admitted only if all earlier prerequisites are already durable and the absolute T+75 guard also passes; exactly T+75 is denied.

<!-- rpi:task id=P04-T03 -->
### P04-T03: Bound readback/evidence and graceful queue/lease disposition

#### Context

Queue outcomes are error-class-based, but there is no signal-aware deadline shutdown path.

#### Intent

Stop new readback/evidence at T+82 and finish safe process, evidence, heartbeat, lease, and queue disposition by T+85.

#### Boundaries

* Included: Remaining-time transport/readback limits clamped to finish by T+82, cancellation at T+82, unknown on post-mutation ambiguity, stop-admission flag, process cancellation, bounded safety writes, owned-lease release, heartbeat stop, queue delete/retain rules, operator evidence. T+82-T+85 is shutdown-only.
* Excluded: ACA kill as normal behavior.

#### Likely Targets

* `podcaster/video/job_runner.py`, `editor.py`, `distribution.py`, publication/evidence/monitoring paths.
* `tests/test_video_job_runner.py`, `tests/test_video_distribution.py`, monitoring/publication tests.

#### Dependencies

* P04-T02.

#### Validation Expectations

* A hanging operation begun before 4920 is cancelled/dispositioned by 4920; no provider/readback/evidence work continues afterward. Shutdown-only work completes by 5100; pre-mutation pending work remains retryable, post-mutation ambiguity remains unknown/retry-blocked, and terminal/permanent paths retain existing delete behavior.

#### Completion Evidence

* Fake-clock shutdown, queue, lease, and provider-state tests.

#### Unresolved Items

* None.

<!-- rpi:phase id=P05 -->
## P05: Validate, review, follow up, and deliver

### Context

Caller requires complete validation, independent lockout review, exact deferred-worker issue, commit, push, and unmerged PR.

### Intent

Prove acceptance, resolve findings through separate implementers, and deliver an auditable PR without merge/deploy.

### Boundaries

* Included: Mandatory matrix, full requested checks, independent review, follow-up issue, commit/push/PR.
* Excluded: Merge, deployment, or live W38/provider operations.

### Likely Targets

* Tests, infra validation, docs/runbooks, tracking artifacts, Git/GitHub delivery.

### Dependencies

* P01-P04.

### Validation Expectations

* Focused and full pytest, Ruff, Bicep build, Checkov, lockfile/diff checks, independent budget/safeguard review.

### Completion Evidence

* Commands/results, review verdict, issue URL, commit SHA, push, and PR URL.

### Unresolved Items

* None.

<!-- rpi:task id=P05-T01 -->
### P05-T01: Complete mandatory focused and full validation

#### Context

The caller supplied a mandatory semantic matrix plus complete repository checks.

#### Intent

Prove the exact behavior rather than merely keeping CI green.

#### Boundaries

* Included: All named tests and commands; exact removals: none; maximum additions: two Python test files (`test_video_budget.py` and one process/media-validation helper test if existing files cannot own it); canonical targets remain existing production/test/infra/docs files; generated artifacts are not hand-edited.
* Excluded: Skipping, xfail, loosening assertions, increasing platform timeout, or suppressing scanner findings.

#### Likely Targets

* Existing video/editor/recorder/distribution/compose/intermediate/publish/infra/integration tests plus at most two new focused helper test files.

#### Dependencies

* P01-P04.

#### Validation Expectations

* Semantic coverage: exact budgets, admission, race, cancellation, resume, provider ambiguity/public semantics, shutdown. Regression coverage: existing provider/queue/SSRF/lease/checkpoint/API behavior.
* Commands: focused pytest while developing; all relevant video/provider/infra tests; `pytest tests/ -q`; `ruff check podcaster tests`; `ruff format --check podcaster tests`; Bicep build for affected entry/module; Checkov for infra; repository lockfile check command or lockfile unchanged assertion; `git diff --check`.

| Scenario | Ownership | Canonical test target | Focused evidence |
|---|---|---|---|
| Exact stage constants, 5100 lifetime, skew/redelivery, integrated T+3300/T+4500 admission | Semantic | `tests/test_video_budget.py` (new) | Budget boundary and fake-clock suite |
| Missing/slow clip and T+20 fan-in | Semantic | `tests/test_editor.py` | Fan-in fake-clock selectors |
| Recorder crash, two executions, 720 seconds, visibility/retry | Semantic | `tests/test_recorder.py`, `tests/test_video_job_runner.py` | Recorder/queue selectors |
| Poison URL, SSRF, poison ceiling | Regression + semantic deadline | `tests/test_video_gen.py`, `tests/test_video_job_runner.py`, existing queue/SSRF tests | Security and disposition selectors |
| Browser hang and descendant cancellation | Semantic | `tests/test_video_process.py` (new, only if needed) or `tests/test_video_gen.py` | Owned child/grandchild fixture |
| FFmpeg/ffprobe hang and partial output | Semantic | `tests/test_video_process.py` or `tests/test_video_compose.py` | Process-tree and partial-output selectors |
| Storage delay, archive cutoff/readback/hash mismatch | Semantic | `tests/test_video_intermediates.py`, `tests/test_video_distribution.py` | Delay/corruption selectors |
| Late recorder vs fallback manifest/blob identity | Semantic | `tests/test_recorder.py`, `tests/test_editor.py` | Interleaved race selectors |
| Deterministic browser-free fallback and missing assets | Semantic | `tests/test_editor.py`, `tests/test_video_compose.py` | No-browser/stable-hash/probe selectors |
| Crash before provider mutation resumes boundary/checkpoints | Semantic | `tests/test_video_job_runner.py` | Boundary crash/replay selectors |
| Crash after mutation becomes unknown/no-repeat | Semantic + regression | `tests/test_video_job_runner.py`, `tests/test_video_distribution.py`, publication-state tests | Ambiguity/no-repeat selectors |
| Platform timeout exits by T+85; T+82 cancellation; lease/queue disposition | Semantic | `tests/test_video_budget.py`, `tests/test_video_job_runner.py` | End-to-end fake-clock/shutdown selectors |
| Replay reuses audio, clipset, clips, normalized, composed, archive | Semantic | `tests/test_video_intermediates.py`, `tests/test_video_gen.py`, `tests/test_video_compose.py`, `tests/test_video_job_runner.py` | Replay matrix |
| Legacy/corrupt/equal-size/zero-byte/unprobeable checkpoints | Semantic | `tests/test_video_intermediates.py`, `tests/test_video_compose.py` | Validation migration matrix |
| Partial publication retries only unresolved idempotent transitions | Regression | `tests/test_video_distribution.py`, `tests/test_publish.py` | Provider transition selectors |
| YouTube ambiguous upload/privacy/approval/quota/resumable | Regression + semantic timeout | Existing YouTube/distribution tests | YouTube selectors |
| Spotify ambiguous draft/pagination/live gates/protected IDs | Regression + semantic timeout | `tests/test_publish.py`, `tests/test_video_distribution.py` | Spotify selectors |
| RSS replay exactly once/CAS | Regression | `tests/test_video_distribution.py` | RSS selectors |
| Draft/upload/manual/private/unlisted/readback/unknown/public semantics | Regression | Publication-state, distribution, monitoring tests | Outcome aggregation selectors |
| ACA 5400, app 5100, recorder cannot exhaust parent | Semantic infra | Existing deployment/Bicep tests | Infra selectors and Bicep build |
| API/config compatibility, queue leases, clip-before-manifest | Regression | Existing job/editor/recorder/queue tests | Existing focused suites |
| Full repository confidence | Regression | All tests/lint/infra | Full commands below |

* Exact removals: none. New Python test files: at most `tests/test_video_budget.py` and `tests/test_video_process.py`; use existing files when ownership fits.
* Reviewer P05-T02 must mark every matrix row pass/fail with the exact command/result and explicitly inspect every deadline and safeguard.

#### Completion Evidence

* Command/result table in changes record with no unresolved failure caused by the change.

#### Unresolved Items

* Determine the repository's exact Bicep/lockfile commands from existing CI/scripts during implementation.

<!-- rpi:task id=P05-T02 -->
### P05-T02: Run independent review and resolve findings under lockout

#### Context

Review must explicitly inspect every budget and safeguard, and a rejected revision locks out its author.

#### Intent

Obtain an independent acceptance verdict and fix every actionable finding without self-review.

#### Boundaries

* Included: Read-only reviewer against plan/research/diff/tests; structured budget/safeguard checklist; separate implementation agent for rejected findings; targeted/full revalidation; final independent verdict.
* Excluded: Original author modifying a rejected revision cycle or reviewer editing source.

#### Likely Targets

* `.copilot-tracking/reviews/logs/2026-09-15/video-stage-budget-redesign-review.md`, diff, plans, changes record.

#### Dependencies

* P05-T01 candidate validation.

#### Validation Expectations

* Reviewer checks all 300/1200/1500/3300/3600/4500/4920/5100 deadlines, 1800 reserve, recorder abandonment, cancellation, immutable fallback, checkpoint validation, durable boundary, provider safeguards, public semantics, infra, docs, and delivery readiness.

#### Completion Evidence

* ACCEPT/Conformant verdict or finding disposition plus separate lockout fix evidence and re-review.

#### Unresolved Items

* None.

<!-- rpi:task id=P05-T03 -->
### P05-T03: Record follow-up and deliver commit, push, and PR

#### Context

The distribution worker is intentionally deferred and delivery requires an unmerged PR.

#### Intent

Leave exact follow-up, operational documentation, and GitHub delivery evidence.

#### Boundaries

* Included: Issue for atomic outbox/claim/reconcile worker; docs/runbooks; synchronized RPI artifacts; conventional commit; required trailers/session ID; push current branch; PR to `main`.
* Excluded: Merge/deploy or production validation.

#### Likely Targets

* Operational docs/runbooks, tracking artifacts, Git/GitHub.

#### Dependencies

* Accepted P05-T02.

#### Validation Expectations

* Follow-up issue acceptance covers durable outbox state machine, atomic claim, separate budget/queue/lease, provider reconciliation/no-repeat, crash matrix, monitoring, migration, rollout, and rollback.
* PR includes W38 timeline, stage table, safeguard preservation, tests/checks, rollback, canary/rollout, and explicit worker deferral.
* Commit includes:
  * `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`
  * `Copilot-Session: d2b30d61-1562-4590-b298-16e187772282`

#### Completion Evidence

* Issue URL, commit SHA, pushed branch, PR URL, and no merge/deploy.

#### Unresolved Items

* None.
