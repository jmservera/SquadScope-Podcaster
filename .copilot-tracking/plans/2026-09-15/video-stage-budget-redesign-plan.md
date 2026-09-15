<!-- markdownlint-disable-file -->
# RPI Plan: Video Stage Budget Redesign

## Task Metadata

* Task ID: video-stage-budget-redesign
* Task slug: video-stage-budget-redesign
* Planning status: Ready; critique revisions applied
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

* None.

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
### [~] P05: Validate, review, follow up, and deliver

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
#### [~] P05-T03: Record follow-up and deliver commit, push, and PR

* Requirement and evidence: Caller delivery and deferred-worker requirements.
* Expected result: Exact worker issue, synchronized RPI artifacts/docs, conventional commit with required trailers, pushed branch, and unmerged PR to `main`.
* Detail section: P05-T03 in .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md

## Dependencies

* Baseline provider evidence and distribution state machine: must remain additive and fail closed.
* Existing ffmpeg/ffprobe/Playwright/storage test doubles: phase details will assign exact coverage.

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

## Handoff

* Implementation artifact: .copilot-tracking/changes/2026-09-15/video-stage-budget-redesign-changes.md
* Ready phase or task: P01-T01.
* Remaining provisional question or blocker: None.
