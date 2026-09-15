<!-- markdownlint-disable-file -->
# RPI Changes: Video Stage Budget Redesign

## Metadata

* Task ID: video-stage-budget-redesign
* Related plan: .copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md
* Phase details: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md
* Implementation date: 2026-09-15

## Execution Status

* Status: Partial
* Declared invocation scope: Full plan
* Completed scope markers: P01-P05 and all tasks
* All remaining active-plan markers: P06-T02
* Status basis: Audio publication bypass removal passed focused validation; full validation and independent review are active.

## Execution Summary

The shared budget, recorder convergence, browser-free fallback, owned cancellation, validated resume, verified archive/readback, per-mutation provider admission, and bounded shutdown/queue disposition are complete and independently accepted. Production/W38/provider operations remain prohibited.

## Completed Work

### Fail-closed audio staging

* Related phase or task: P06-T01
* Files: `podcaster/job_runner.py`, `tests/test_job_runner.py`, `tests/test_orchestration.py`, `tests/integration/test_publish_flow.py`, `docs/PRD.md`.
* What changed and why: Removed synthesis-time `auto_publish_job` and direct `publish_episode` calls. Successful audio now remains `synthesized_review_ready`, packet-ready, ineligible, and blocked by `human_review` unless an explicit approved/manual orchestration gate owns publication. Video enqueue remains independent.
* Completion evidence: Spotify config and auto-publish environment no longer trigger provider calls; approved review still publishes; ambiguous approved outcome remains `publication_unknown` and non-final; integration covers blocked manual request followed by approved publication.
* Validation: Focused audio/orchestration/video suite passed (284 tests).

### Shared budget and conservative projection

* Related phase or task: P01-T01
* Files: `podcaster/video/budget.py`, `podcaster/video/job_runner.py`, `tests/test_video_budget.py`
* What changed and why: Added immutable stage constants, process-local monotonic plus durable UTC conservative remaining time, clip first-admission timing, provider reserve admission, and production budget persistence/reload.
* Completion evidence: Exact cutoffs, skew/redelivery, non-reset, serialization, provider 3300/1800 arithmetic, and production compatibility tests.
* Validation: Passed.

### Owned process trees and media evidence

* Related phase or task: P01-T02
* Files: `podcaster/video/process.py`, `tests/test_video_process.py`
* What changed and why: Added session-owned process execution with terminate/kill/reap and partial-output removal; added size/SHA-256/bounded-probe media evidence and bounded sanitized timing events.
* Completion evidence: Child/grandchild timeout fixture, checked-failure cleanup, zero/missing/equal-size hash/probe failures.
* Validation: Passed.

### Parent-bounded recorder and fan-in terminalization

* Related phase or task: P02-T01
* Files: `podcaster/video/clipset.py`, `editor.py`, `job_runner.py`, `recorder.py`, `sync_plan.py`, `infra/modules/aca-recorder.bicep`, focused tests.
* What changed and why: Wired T+300/T+1200/T+1500 budget projection through fan-out and recorder work; added durable first admission/attempt history, two-execution/720/browser/parent abandonment, and bounded visibility/retry.
* Completion evidence: Missing/slow/crash/poison/browser-timeout and clock/redelivery tests, while preserving leases, SSRF, poison ceiling, CAS, and legacy envelopes.
* Validation: Passed.

### Deterministic hash-bound terminal fallback

* Related phase or task: P02-T02
* Files: `podcaster/video/editor.py`, `recorder.py`, clipset/sync metadata, focused tests.
* What changed and why: Replaced post-cutoff browser capture with fixed local asset/ffmpeg rendering, content-addressed media, size/SHA/probe-bound terminal manifests, CAS race isolation, and `recording_insufficient` failure.
* Completion evidence: No-browser/network, stable/probe-valid output, unavailable renderer, and late-writer race tests.
* Validation: Passed.

### Stage-aware render cancellation and validated replay

* Related phase or task: P03-T01 and P03-T02
* Files: Video generation/composition/EDL/section-card/process/intermediate/clip schema paths and focused tests.
* What changed and why: Routed browser/media work through render-stage budgets and owned cancellation; added versioned identity/size/SHA/probe validation for reusable audio, clipset/clips, normalized segments, and composed video; invalid legacy/corrupt artifacts become safe cache misses.
* Completion evidence: Browser/FFmpeg/ffprobe timeout, partial-output, valid replay, and corruption matrices.
* Validation: Passed.

### Verified archive and readback cutoff

* Related phase or task: P03-T03
* Files: `podcaster/video/distribution.py`, `job_runner.py`, intermediate/storage seams, focused tests.
* What changed and why: Added bounded streaming archive/readback with checksum/probe evidence and exact T+3300 pending-only/T+3600 cutoff behavior.
* Completion evidence: Storage delay, mismatch, corrupt readback, and deadline boundary tests.
* Validation: Passed.

### Durable rendered boundary and provider reserve

* Related phase or task: P04-T01 and P04-T02
* Files: `podcaster/video/budget.py`, `distribution.py`, `job_runner.py`, focused tests.
* What changed and why: Persisted merge-safe immutable `rendered_pending_distribution`, revalidated it on redelivery, gated provider intent/mutation on conservative >=1800 seconds and absolute T+75, and retained all canonical provider/public safeguards.
* Completion evidence: Pre/post-boundary crash, 3299/3300/3301, 1801/1800/1799, 4499/4500, late archive, ambiguity/no-repeat, and public-semantics tests.
* Validation: Passed.

### T+82 evidence deadline and T+85 shutdown

* Related phase or task: P04-T03
* Files: `podcaster/video/distribution.py`, `job_runner.py`, budget/evidence helpers, focused tests.
* What changed and why: Clamped provider readback/evidence to finish by 4920 seconds and added bounded timing/heartbeat/hash/cancellation/shutdown evidence plus safe lease/queue disposition through 5100 seconds.
* Completion evidence: Hanging operation, shutdown, queue retention/deletion, unknown/no-repeat, and fake-clock tests.
* Validation: Passed.

### Independent lockout fixes

* Related phase or task: P05-T02
* Files: `podcaster/video/intermediates.py`, `process.py`, `recorder.py`, `editor.py`, `job_runner.py`, `distribution.py`, `youtube.py`, `youtube_playlist.py`, `podcaster/publish.py`, and focused tests.
* What changed and why: Separate implementers corrected budgeted checkpoint success reporting, replaced abandoned timeout threads with kill/join/reap owned callable processes, counted only explicit recorder failures, ordered terminal evidence and lease release before bounded optional cleanup, bounded queue deletion before queue-driven cleanup, rechecked admission at every provider mutation/retry, and made large YouTube uploads initialize exactly one resumable session while preserving prior-mutation ambiguity.
* Completion evidence: Each rejected finding was fixed by a lockout implementer and rechecked by an independent reviewer; the final verdict accepted P01-P04 and P05-T01 with no open actionable defects.
* Validation: Passed.

## Implementation-Time Plan and Detail Updates

### Added fail-closed audio publication follow-up

* Affected plan area or markers: P06, P06-T01, P06-T02.
* What changed: Added the explicit requirement that synthesis stages audio and readiness metadata but never mutates Spotify without an approved/manual orchestration gate.
* Why: The direct synthesis-time `auto_publish_job` and `publish_episode` paths conflict with the user-required audio-only fail-closed policy.
* Triggering evidence: Explicit PR #682 continuation requirement on 2026-09-15.
* User answer or decision: Audio-only automatic publication is prohibited; approved/manual publication and video behavior must remain independent.
* Reconciliation performed: Plan, phase details, active markers, validation boundary, and delivery lifecycle updated.
* Planning and critique state: Immediate in-scope clarification preserving the accepted provider safeguard direction; no new critique required before implementation.

### Applied final-candidate critique corrections

* Affected plan area or markers: Acceptance criteria; P01-P05.
* What changed: Defined T+3300 as the effective mutation admission boundary under the 1800-second reserve; durable clip clock authority; mandatory browser process ownership; T+82 operation completion; executable test ownership; deterministic packaged fallback assets.
* Why: Resolve PC-001 through PC-006 before source edits.
* Triggering evidence: .copilot-tracking/reviews/plans/2026-09-15/video-stage-budget-redesign-plan-critique.md
* User answer or decision: The user explicitly required `Copilot-Session: d2b30d61-1562-4590-b298-16e187772282`, so PC-007 was rejected.
* Reconciliation performed: Plan summary, functional/non-functional requirements, acceptance criteria, tasks, details, critique dispositions, and handoff synchronized.
* Planning and critique state: Ready; one critique completed and all findings disposed.

## Validation Record

| Check | Scope | Status | Evidence or reason |
|---|---|---|---|
| `pytest -q tests/test_video_budget.py tests/test_video_process.py tests/test_video_job_runner.py -q` | P01 and runner regression | Passed | 143 passed |
| Ruff check/format on P01 files | P01 Python | Passed | All checks passed; 5 files formatted |
| `git diff --check` | Current diff | Passed | No whitespace errors |
| Focused P01-P02 pytest | Budget/process/editor/recorder/job/sync | Passed | 553 passed, 2 deselected |
| Ruff check/format on video and P02 tests | P01-P02 Python | Passed | All checks passed; 31 files formatted |
| Focused P01-P03 pytest | Budget/process/video generation/compose/intermediates/distribution/job/editor/recorder | Passed | 1039 passed, 1 skipped, 2 deselected |
| Ruff check/format on P03 scope | P01-P03 Python | Passed | All checks passed; 93 files formatted |
| Focused P01-P04 and provider pytest | Video/provider/publication/monitoring | Passed | 1532 passed, 2 deselected; one existing httpx deprecation warning |
| Ruff check/format on P04 scope | P01-P04 Python | Passed | All checks passed; 88 files formatted |
| Mandatory focused matrix including integration | Video/provider/publication/infra/integration | Passed | 1323 passed, 2 deselected |
| Lockout fix suites | Checkpoint/cancellation/recorder/shutdown/provider/queue/YouTube | Passed | 284, 223, 570, and 264 focused tests passed in successive fix cycles |
| `pytest tests/ -q` final candidate | Full repository | Passed | 3141 passed, 2 skipped, 2 deselected; one existing httpx deprecation warning |
| `ruff check podcaster tests` | Python lint | Passed | All checks passed |
| `ruff format --check podcaster tests` | Python format | Passed | 187 files already formatted |
| `python3 -m compileall -q podcaster tests` | Python compile | Passed | No errors |
| Bicep build | Changed ACA recorder/editor modules | Passed | Both modules compiled |
| CI-equivalent Checkov Bicep scan | `infra/` | Passed | 34 passed, 0 failed |
| Dependency lock assertion | Dependency manifests and lock | Passed | No changes |
| `git diff --check` | Complete diff | Passed | No whitespace errors |
| Independent lockout review | Complete implementation boundary | Passed | Final outcome Conformant; no open actionable defects |
| P06 focused audio gate suite | Job runner, orchestration, integration, video distribution/runner | Passed | 284 passed |
| P06 focused Ruff/format/compile/diff | Changed Python and complete diff | Passed | All checks passed; 4 files formatted |
| `pytest tests/ -q` after P06 | Full repository | Passed | 3142 passed, 2 skipped, 2 deselected; one existing httpx deprecation warning |
| Full Ruff/format/compile/diff after P06 | Python and complete diff | Passed | All checks passed; 187 files formatted |

## Pre-Review Reconciliation

* Plan markers and phase details: P01-P05 and P06-T01 complete; P06-T02 active.
* Completed-work evidence and handoff prose: Current.
* Validation, blockers, remaining work, and follow-up items: Current.
* Review readiness: P06 candidate is ready for independent read-only review.

## Blockers

* Independent P06 review rejected malformed/cost-state normalization, legacy duplicate convergence, and remaining architecture/README automatic-publication claims. Fixes are assigned to a separate lockout implementer.

## Remaining Work

* P06-T02 lockout fixes, revalidation, independent acceptance, commit, push, and PR update.

## Follow-Up Items

* Canonical plan list: .copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md, `## Follow-Up Items`
* Separate distribution worker with atomic outbox/claim, reconcile-before-mutate, separate budget/queue/lease/recovery, and provider crash matrix: #681.
* Implementation commit: `4033df6`.
* Pushed branch: `origin/squad/video-stage-budget-redesign`.
* Unmerged pull request: #682.
* Merge/deploy/production mutation: Not performed.

## Return-to-Caller State

* Implementation execution status: Partial
* Declared scope and markers: Full plan; P01-P05 complete, P06 active.
* Validation coverage: Mandatory focused/full pytest, real fanout integration, Ruff, compile, Bicep, Checkov, lock, and diff checks passed.
* Blockers: None.
* Current plan and detail updates: Critique corrections applied.
* Planning and critique state: Implementation-ready.
* Follow-up items: Distribution worker tracked in #681.
* Review readiness or no-handoff reason: Ready for independent P06 review; focused and full validation passed.
* Continuation owner: Confirmed automatic RPI Agent.
