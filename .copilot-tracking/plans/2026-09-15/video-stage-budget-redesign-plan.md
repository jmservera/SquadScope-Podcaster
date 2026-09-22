<!-- markdownlint-disable-file -->
# RPI Plan: Video Stage Budget Redesign

## Task Metadata

* Task ID: video-stage-budget-redesign
* Task slug: video-stage-budget-redesign
* Planning status: Complete through P11; final hosted verification follows push
* Plan date: 2026-09-15
* Phase details: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md
* Plan critique: .copilot-tracking/reviews/plans/2026-09-15/video-stage-budget-redesign-plan-critique.md

## Executive Summary

This plan will replace independent video-pipeline timeouts with one editor-owned budget that reserves time for deterministic fallback, verified rendering, archive, provider admission/readback, evidence, and graceful shutdown. It will preserve the baseline provider-state safeguards and deliver a durable render/distribution boundary without attempting the riskier separate distribution worker in this PR.

### User Decisions and Requirements Highlights

* Application work must stop gracefully by T+85 minutes while ACA remains at 5400 seconds.
* Every stage, child recorder, browser, subprocess, storage operation, provider transition, and shutdown path must derive from the shared budget.
* W38 historical production state is evidence-only and must never be retried, resumed, or mutated.

### What You May Not Know

* Process-local monotonic clocks cannot be serialized to recorder workers; research recommends a conservative durable UTC projection for admission while each process enforces its own monotonic bound.
* Existing terminal manifest CAS does not bind the raw clip bytes, and the current fallback still launches Chromium.
* No transactional distribution outbox exists; moving provider work to a new worker now would introduce new mutation-recovery ambiguity.

### Unresolved Decisions or Blockers

* No design blocker. PR #682 has six unresolved review threads assigned to Bender for one focused correction pass, followed by local validation and hosted gate verification.

## User Decisions and Requirements

* Execute the complete Research -> Plan -> Implement -> Review -> Follow-up lifecycle and deliver code, tests, docs, commit, push, and an unmerged PR to `main`.
* Implement all binding stage budgets: 300, 1200, 1500, 3300, 3600, 4500, 4920, and 5100 seconds from one editor start; provider mutation requires at least 1800 seconds remaining and cannot start after T+75.
* Abandon a clip at the earliest of 12 minutes, two failed recorder executions, browser deadline, or T+20 fan-in cutoff.
* Preserve SSRF, leases, poison ceiling, CAS, clip-before-manifest, checkpoint, publication evidence, canonical provider identity, unknown/no-repeat, YouTube, Spotify, RSS, notification, protected-environment, and public-verification safeguards.
* Never use ACA hard kill as normal control flow; cancellation must stop process trees and partial/zero-byte outputs must not become complete checkpoints.
* Reuse only valid audio, clipset, clip manifests/media, normalized segments, and composed video after size, checksum, and bounded media validation.
* Persist verified `rendered_pending_distribution` before provider work. Implement the durable boundary and provider reserve now; defer a separate distribution worker to an exact follow-up issue if it cannot safely fit.
* Run the complete mandatory failure/replay/provider/fake-clock/infra test matrix and all requested repository validation without weakening gates.
* Do not operate on W38 production state, merge, or deploy.

## Goals

* Make every pipeline stage provably fit within a 5100-second application lifetime.
* Converge missing/slow/crashed recorder work by T+25 without launching post-cutoff Chromium.
* Resume only absent or invalid work and preserve terminal/provider decisions exactly once.
* Leave a durable, operator-readable boundary and append-only timing/timeout/cancellation evidence.

## Scope and Non-Goals

### In Scope

* Shared deadline/budget, cancellation, fan-in/recorder policy, deterministic fallback, media validation/resume, render boundary, provider admission/readback reserve, shutdown disposition, infra assertions, tests, docs, and delivery.

### Non-Goals

* W38 production actions, provider/archive reconciliation of the historical execution, merge/deploy, caller configuration redesign, or a speculative distribution worker without an atomic state machine.

## Functional Requirements

* Create one immutable video-job budget with local monotonic elapsed time, durable UTC projection for child admission, and hard cutoffs at 300, 1200, 1500, 3300, 3600, 4500, 4920, and 5100 seconds.
* Persist job start, stage transitions, deadlines, remaining budget, heartbeats, recorder attempts, artifact hashes, and timeout/cancellation reasons through bounded append-only evidence.
* Complete claim/manifest/checkpoint/provider preflight by T+5, recorder fan-in by T+20, and missing-clip classification/fallback by T+25.
* Abandon each clip at the earliest of 720 seconds elapsed, two failed recorder executions, its browser deadline, or the T+20 fan-in cutoff.
* Make terminal clip decisions immutable and bind each terminal manifest to the exact content-addressed, size/hash/probe-valid media consumed by composition.
* Generate deterministic browser-free fallback cards after cutoff when policy permits; otherwise persist `recording_insufficient` and stop before composition/provider work.
* Bound every new browser, FFmpeg, ffprobe, storage, archive, and provider operation to its stage and parent remaining budget; cancel and reap owned process trees on timeout.
* Complete normalization/composition/audio mux by T+55 and verified archive upload/checksum/probe/readback by T+60.
* Reuse audio, clipsets, clip manifests/media, normalized segments, composed media, and archive media only after identity, size, SHA-256, and media-decodability validation; treat legacy or invalid metadata as a cache miss.
* Persist a merge-safe verified `rendered_pending_distribution` record before provider intent or mutation.
* Admit a new provider mutation only strictly before T+75 and with at least 1800 seconds remaining using the earlier local/durable bound. With the 5100-second parent deadline, T+55 (3300 seconds) is the effective latest admission instant; later verified renders remain pending without provider intent.
* Permit only readback and durable evidence after mutation through T+82; complete cancellation, evidence, lease, heartbeat, and queue disposition by T+85.
* Preserve all baseline provider identities, evidence retention, unknown/no-repeat, YouTube, Spotify, RSS, notification, lease, checkpoint, protected-environment, and externally-verified-public semantics.
* Keep provider work in-process for this PR; create a follow-up issue defining atomic outbox/claim/reconcile-before-mutate distribution-worker separation.

## Non-Functional Requirements

* Compatibility: Existing API/config payloads, queue envelopes, result fields, legacy manifests, and test injection seams remain readable; new parameters are optional/additive.
* Timing: Application-owned work and durable disposition complete by 5100 seconds under deterministic fake-clock proof; ACA timeout remains 5400 seconds.
* Safety: Deadline, persistence, probe, or identity uncertainty fails closed and never becomes a success-shaped checkpoint or public-provider result.
* Cancellation: Owned subprocess groups are terminated, force-killed after a bounded grace when necessary, and reaped; zero-byte/partial outputs are rejected.
* Determinism: Post-cutoff fallback uses no network or Chromium and produces hash-stable, ffprobe-decodable media for identical inputs.
* Idempotency: Redelivery reuses valid immutable work, never repeats terminal clips or settled provider mutations, and retries only unresolved idempotent provider transitions.
* Observability: Evidence is bounded, append-only, sanitized, and retains provider evidence for at least 28 days without secrets or unbounded payloads.
* Quality: No CI, test, lint, security, or deployment gate is weakened or skipped.

## Acceptance Criteria

* Exact budget constants and admission inequalities are centralized and covered at boundary values.
* Integrated admission tests prove 3299/3300/3301 elapsed and archive completion at 3300/3301/3600: exactly 3300 may be admitted only when the conservative remaining value is at least 1800; later completion is pending-only; T+75 remains an independent deny guard.
* Missing, slow, crashed, poisoned, browser-hung, FFmpeg-hung, and storage-delayed scenarios stop at the correct stage without consuming later reserves.
* Fan-in, Chromium, FFmpeg/ffprobe, storage/archive, distribution, readback/evidence, and shutdown propagate cancellation/timeout reasons.
* A late recorder cannot alter a winning fallback manifest or the media bytes/hash referenced by it.
* Per-clip 720 seconds begins at the first durable recorder admission and spans redelivery; browser deadline is the earlier of the configured browser hard limit, the clip deadline, recorder visibility/ACA reserve, and T+20.
* Crash before provider mutation resumes from validated media checkpoints; crash after a potentially successful mutation becomes `publication_unknown` and never repeats create/insert.
* Platform-deadline simulation exits by T+85 with durable evidence and correct lease/heartbeat/queue disposition.
* Readback/evidence operations begun before T+82 are clamped, cancelled, and dispositioned by T+82; T+82-T+85 is reserved exclusively for shutdown.
* Replay reuses valid immutable clipset/audio/clips/normalized/composed/archive work and rejects corrupt, equal-size-altered, zero-byte, unprobeable, or identity-mismatched artifacts.
* Partial publication retries only unresolved idempotent transitions; YouTube ambiguous upload, Spotify ambiguous draft/pagination, and RSS replay retain baseline exactly-once/fail-closed behavior.
* Uploaded, draft, manual handoff, private/unlisted, provider readback, and unknown never aggregate as externally verified public.
* Infrastructure tests prove ACA editor timeout remains 5400 seconds, application deadline is 5100 seconds, and recorder timeout/visibility/capture cannot exhaust the parent fan-in window.
* Operational docs and PR describe incident timeline, stage table, safeguards, rollback, canary/rollout, evidence, and deferred worker.
* Focused tests, relevant video/provider/infra tests, full pytest, Ruff lint/format, Bicep build, Checkov, lockfile integrity, and diff checks pass.
* One independent implementation review explicitly checks every budget and safeguard; rejected revisions are corrected by a separate lockout implementer before re-review.

## Implementation Context Record

| Context item | Current artifact or record |
|---|---|
| Plan | .copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md |
| Phase details | .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md |
| Latest critique | .copilot-tracking/reviews/plans/2026-09-15/video-stage-budget-redesign-plan-critique.md (pending) |
| Relevant research | .copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md |
| Changes-record role | .copilot-tracking/changes/2026-09-15/video-stage-budget-redesign-changes.md will be created by implementation |
| Planning execution and readiness | Planning complete and implementation-ready after one Revise critique and direct corrections |
| Continuation context | Confirmed automatic RPI Agent continues to implementation |

## Sources

* .copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md: C1-C20 research synthesis and convergence recommendation.
* User-supplied W38 incident, binding budgets, implementation requirements, mandatory tests, and delivery constraints.
* Baseline `bd59b69`: provider-state safeguards that must remain invariant.

## Phase Checklist

<!-- rpi:phase id=P01 -->
### [x] P01: Establish shared budget and evidence contracts

* Intent: Define the common timing, cancellation, terminal evidence, and validation foundations.
* Dependencies: Completed research.

<!-- rpi:task id=P01-T01 -->
#### [x] P01-T01: Implement shared stage budget and conservative cross-process projection

* Requirement and evidence: Binding deadlines; research C2, C11, C17.
* Expected result: Central immutable stage model, local monotonic checks, durable UTC projection, earlier-of remaining time, typed timeout/admission reasons, and fake-clock boundary tests.
* Detail section: P01-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P01-T02 -->
#### [x] P01-T02: Implement bounded process, storage, validation, and evidence foundations

* Requirement and evidence: Research C4-C5, C12-C13, C19.
* Expected result: Budget-aware process-group runner, deadline wrapper seams, artifact validation metadata, and append-only stage/attempt/hash/timeout evidence.
* Detail section: P01-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P02 -->
### [x] P02: Bound recorder fan-in and deterministic fallback

* Intent: Enforce recorder cutoffs and terminal browser-free fallback without races.
* Dependencies: P01.

<!-- rpi:task id=P02-T01 -->
#### [x] P02-T01: Enforce recorder admission, abandonment, retry, and fan-in cutoffs

* Requirement and evidence: T+20 and earliest-of abandonment; research C2-C3, C9-C11, C16-C17.
* Expected result: Parent-bounded visibility, capture/retry eligibility, two-execution terminalization, and fan-in stop at 1200 seconds without weakening queue/lease/SSRF/poison behavior.
* Detail section: P02-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P02-T02 -->
#### [x] P02-T02: Produce immutable browser-free fallback or recording-insufficient outcome

* Requirement and evidence: T+25 deterministic fallback; research C3, C6, C18.
* Expected result: Content-addressed, hash/probe-bound terminal fallback cards with no network/Chromium after cutoff, or durable `recording_insufficient` before composition.
* Detail section: P02-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P03 -->
### [x] P03: Bound render, archive, and resume

* Intent: Enforce T+55/T+60 and validate reusable media.
* Dependencies: P01-P02.

<!-- rpi:task id=P03-T01 -->
#### [x] P03-T01: Enforce stage-aware browser and media process cancellation

* Requirement and evidence: T+55 hard render cutoff; research C4, C12.
* Expected result: No new render operation starts after T+55; owned subprocess trees are reaped; partial outputs are rejected.
* Detail section: P03-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P03-T02 -->
#### [x] P03-T02: Validate and reuse immutable media checkpoints

* Requirement and evidence: Size+checksum+probe replay; research C5, C13, C18-C19.
* Expected result: Versioned validation for audio, clipset/clips, normalized segments, composed video, and archive with fail-closed legacy/corrupt handling.
* Detail section: P03-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P03-T03 -->
#### [x] P03-T03: Complete verified archive and readback by T+60

* Requirement and evidence: Archive budget and storage delays; research C5, C14, C19.
* Expected result: Deadline-aware upload, checksum, ffprobe, and readback with no complete claim on timeout or mismatch.
* Detail section: P03-T03 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P04 -->
### [x] P04: Persist render boundary and bound distribution/shutdown

* Intent: Add durable provider admission/readback/evidence and graceful disposition.
* Dependencies: P01-P03.

<!-- rpi:task id=P04-T01 -->
#### [x] P04-T01: Persist verified rendered-pending-distribution state

* Requirement and evidence: Durable boundary; research C7, C14-C15, C20.
* Expected result: Merge-safe verified artifact identity is durable before provider intent and reusable after pre-mutation crash.
* Detail section: P04-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P04-T02 -->
#### [x] P04-T02: Enforce provider mutation reserve and preserve safeguards

* Requirement and evidence: >=1800 seconds and before T+75; research C8, C14, C17, C20.
* Expected result: Provider mutation admission is fail-closed at boundary values and retains canonical identity, no-repeat, provider-specific, and public-verification semantics.
* Detail section: P04-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P04-T03 -->
#### [x] P04-T03: Bound readback/evidence and graceful queue/lease disposition

* Requirement and evidence: T+82/T+85; research C8, C10, C14, C20.
* Expected result: Readback/evidence stops by 4920 seconds and safe cancellation, evidence, heartbeat, lease, and queue outcome completes by 5100 seconds.
* Detail section: P04-T03 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P05 -->
### [x] P05: Validate, review, follow up, and deliver

* Intent: Prove all requirements, resolve review findings, create follow-up issue, commit, push, and open PR.
* Dependencies: P01-P04.

<!-- rpi:task id=P05-T01 -->
#### [x] P05-T01: Complete mandatory focused and full validation

* Requirement and evidence: Caller mandatory matrix and repository checks.
* Expected result: Locked semantic/regression matrix and all required pytest, Ruff, Bicep, Checkov, lockfile, and diff checks pass.
* Detail section: P05-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P05-T02 -->
#### [x] P05-T02: Run independent review and resolve findings under lockout

* Requirement and evidence: Caller reviewer-lockout requirement.
* Expected result: Reviewer explicitly checks every budget/safeguard; any rejection is fixed by a separate implementer and independently re-reviewed.
* Detail section: P05-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P05-T03 -->
#### [x] P05-T03: Record follow-up and deliver commit, push, and PR

* Requirement and evidence: Caller delivery and deferred-worker requirements.
* Expected result: Exact worker issue, synchronized RPI artifacts/docs, conventional commit with required trailers, pushed branch, and unmerged PR to `main`.
* Detail section: P05-T03 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P06 -->
### [x] P06: Fail closed audio-only publication

* Intent: Keep synthesized audio staged until an explicit approved/manual publication gate invokes `publish_staged_job`.
* Dependencies: P05 and the existing orchestration review gate.

<!-- rpi:task id=P06-T01 -->
#### [x] P06-T01: Remove synthesis-time Spotify mutation bypass

* Requirement and evidence: PR #682 hard requirement; synthesis must not call Spotify merely because audio validated or Spotify config/auto-publish environment is present.
* Expected result: `run_synthesis` updates packet/readiness/blocker state and independently enqueues video, but only explicit approved/manual orchestration can publish audio.
* Detail section: P06-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P06-T02 -->
#### [x] P06-T02: Validate, independently review, and update PR delivery

* Requirement and evidence: Required focused/full gates, strict lockout review, follow-up commit, push, and PR #682 evidence update.
* Expected result: Audio no-auto and approved-gate behavior are accepted, video behavior remains independent, and PR #682 stays open with checks triggered.
* Detail section: P06-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P07 -->
### [x] P07: Remediate unresolved PR review findings

* Intent: Correct all 13 unresolved PR #682 review threads without weakening publication, lease, CAS, SSRF, poison, provider ambiguity/no-repeat, or public-verification safeguards.
* Dependencies: P06 complete; this before-work design review accepted; original Copilot author remains locked out.

<!-- rpi:task id=P07-T01 -->
#### [x] P07-T01: Correct artifact cleanup, process reap, and browser-free replay

* Requirement and evidence: Threads `PRRT_kwDOSzuis86iosWy`, `PRRT_kwDOSzuis86iosXi`, `PRRT_kwDOSzuis86iosYL`, `PRRT_kwDOSzuis86ipal6`, and `PRRT_kwDOSzuis86ipamb`.
* Expected result: Cleanup is scoped and atomic, killed processes have a bounded final reap, and fully checkpointed replay never requires Playwright.
* Detail section: P07-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P07-T02 -->
#### [x] P07-T02: Correct lease, redelivery, ownership, cleanup, and ACA entrypoint behavior

* Requirement and evidence: Threads `PRRT_kwDOSzuis86ipakb`, `PRRT_kwDOSzuis86ipak9`, `PRRT_kwDOSzuis86ipalc`, `PRRT_kwDOSzuis86ipaoD`, and `PRRT_kwDOSzuis86ipao6`.
* Expected result: Recorder visibility covers finalization, usable provider-window redelivery is not visibility-delayed, resumed distribution is single-owner, terminal resumed outcomes clean up, and each ACA recorder execution consumes one message.
* Detail section: P07-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P07-T03 -->
#### [x] P07-T03: Fail closed on ambiguous YouTube resumable-session initiation

* Requirement and evidence: Thread `PRRT_kwDOSzuis86ipanQ`.
* Expected result: A transport-ambiguous session-init POST persists deterministic `publication_unknown` evidence and blocks blind session/video creation retries.
* Detail section: P07-T03 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P07-T04 -->
#### [x] P07-T04: Budget editor fan-in probes and recorder finalization storage calls

* Requirement and evidence: Threads `PRRT_kwDOSzuis86ipany` and `PRRT_kwDOSzuis86ipaoh`, each covering two call sites.
* Expected result: Every affected storage call uses the remaining stage/recorder budget and stops without late side effects after timeout.
* Detail section: P07-T04 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P07-T05 -->
#### [x] P07-T05: Validate and independently accept the correction

* Requirement and evidence: Reviewer-protocol lockout and all 13 unresolved PR #682 threads.
* Expected result: Bender implements only the correction boundary; Fry independently reviews functional/test completeness; Hermes independently reviews lease/CAS/SSRF/poison/provider ambiguity/no-repeat/public-verification preservation; all focused and comprehensive checks pass before threads are eligible for resolution.
* Detail section: P07-T05 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P08 -->
### [x] P08: Complete focused PR gate remediation

* Intent: Correct the six current review findings and the hosted recorder regression without broadening the operator-only PR.
* Dependencies: P07 and current PR #682 review/check evidence.

<!-- rpi:task id=P08-T01 -->
#### [x] P08-T01: Fail closed on ambiguous provider and storage outcomes

* Requirement and evidence: Current YouTube upload, playlist reconciliation, and intermediate verification review threads.
* Expected result: Post-mutation transport exhaustion is retry-blocked unknown, playlist lookup/insert ambiguity remains fail closed, and timed-out blob-size verification cannot publish a sidecar.
* Detail section: P08-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P08-T02 -->
#### [x] P08-T02: Preserve stage budgets and failed-render cleanup

* Requirement and evidence: Current section-card and video-generation review threads plus the hosted recorder failure.
* Expected result: Drawtext-capable ffmpeg selection is preserved, FANIN metadata reads are budgeted, failed screenshot artifacts are removed, and foreign clipsets retain fallback rendering time.
* Detail section: P08-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P08-T03 -->
#### [x] P08-T03: Validate, resolve proven threads, and deliver

* Requirement and evidence: PR #682 remains blocked by one failing check and six unresolved threads.
* Expected result: Focused and repository-standard validation pass, applicable threads are resolved, the existing branch is committed/pushed, and remote checks are reported without merging.
* Detail section: P08-T03 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P09 -->
### [x] P09: Close post-push budget and runner findings

* Intent: Address the two review threads created after the P08 green snapshot.
* Dependencies: P08 and current PR #682 review evidence.

<!-- rpi:task id=P09-T01 -->
#### [x] P09-T01: Fail closed on backward-clock budget reload

* Expected result: A durable projection observed before its persisted start cannot regain the job lifetime.
* Detail section: P09-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P09-T02 -->
#### [x] P09-T02: Reject custom section-card runner failures

* Expected result: Non-zero injected-runner returns raise and remove partial card output.
* Detail section: P09-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P09-T03 -->
#### [x] P09-T03: Validate, resolve, and restore operator readiness

* Expected result: Focused/full checks pass, both new threads resolve, hosted gates pass, and readiness is re-established without merge.
* Detail section: P09-T03 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P10 -->
### [x] P10: Close final post-push safety findings

* Intent: Resolve the five findings created after the P09 push without expanding beyond review remediation.

<!-- rpi:task id=P10-T01 -->
#### [x] P10-T01: Preserve provider and media ambiguity safety

* Expected result: Budgeted small YouTube uploads use the ambiguity-aware uploader, transient init statuses are unknown, and non-finite probe durations are rejected.
* Detail section: P10-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P10-T02 -->
#### [x] P10-T02: Preserve terminalization and final-output validation

* Expected result: All permanent recorder setup errors terminalize with bounded writes, and resumed composition validates the final mux.
* Detail section: P10-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P10-T03 -->
#### [x] P10-T03: Validate and deliver the final correction

* Expected result: Focused/full validation passes; commit, push, thread resolution, and hosted checks are recorded.
* Detail section: P10-T03 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:phase id=P11 -->
### [x] P11: Preserve pending replay plan identity

* Intent: Reuse persisted clipset facts after the preflight cutoff so removed-repo annotations cannot drift on pending distribution redelivery.

<!-- rpi:task id=P11-T01 -->
#### [x] P11-T01: Reuse persisted clipset identity after preflight cutoff

* Expected result: Pending replay after T+300 compares against the persisted rendered plan rather than an unannotated regenerated tail.
* Detail section: P11-T01 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

<!-- rpi:task id=P11-T02 -->
#### [x] P11-T02: Validate and deliver the replay correction

* Expected result: Focused/full tests pass; final commit/push, thread resolution, and hosted checks are recorded.
* Detail section: P11-T02 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

## Dependencies

* Baseline provider evidence and distribution state machine: must remain additive and fail closed.
* Existing ffmpeg/ffprobe/Playwright/storage test doubles: phase details will assign exact coverage.
* P07 correction ownership: Bender. Original Copilot author is locked out for this revision cycle.
* P07 acceptance ownership: Fry for functional/regression evidence and Hermes for security/provider/lease invariants. Neither reviewer writes the correction they review.

## Critique Disposition

| Critique run and finding | Disposition | Plan response or residual risk |
|---|---|---|
| PC-001 effective mutation window | Resolved | P03/P04 now define T+3300 as effective latest admission, pending-only late archive, and integrated boundaries. |
| PC-002 recorder/browser clock authority | Resolved | P01/P02 now define durable first admission, redelivery-stable 720 seconds, browser formula, and earliest conservative precedence. |
| PC-003 Chromium cancellation | Resolved | P01/P03 require an owned cancellable process boundary and descendant reap proof; documentation-only limitation is not accepted. |
| PC-004 T+82 operation deadline | Resolved | P04 requires operations to finish/cancel by T+82 and reserves the final 180 seconds for shutdown only. |
| PC-005 executable test ownership | Resolved | P05 details contain the row-per-scenario matrix, focused targets, reviewer ownership, no removals, and two-file maximum. |
| PC-006 deterministic assets | Resolved | P02 requires repository/container-owned font/assets and fails closed to `recording_insufficient`. |
| PC-007 session trailer | Rejected | The requester explicitly requires `Copilot-Session: d2b30d61-1562-4590-b298-16e187772282`; confirmed user direction outranks critique inference. |

## Follow-Up Items

* Separate distribution worker with atomic outbox/claim, reconcile-before-mutate, independent queue/lease/recovery, and provider crash matrix: #681.
* P07 must not implement that separate worker. It may add only the bounded same-queue handoff needed to avoid delaying a still-admissible durable distribution retry; once provider admission is no longer possible, the record remains durable pending for `jmservera/SquadScope-Podcaster#681` rather than blind mutation.

## Handoff

* Implementation artifact: .copilot-tracking/changes/2026-09-15/video-stage-budget-redesign-changes.md
* Ready phase or task: None; P01-P11 implementation and local validation are complete.
* Remaining provisional question or blocker: Hosted checks and thread resolution follow the final push.
