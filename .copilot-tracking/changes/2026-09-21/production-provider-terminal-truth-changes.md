<!-- markdownlint-disable-file -->
# RPI Changes: Production Provider Terminal Truth

## Metadata

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Related plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
* Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
* Implementation date: 2026-09-21

## Execution Status

* Status: Partial
* Declared invocation scope: full plan, P01-T01 through P06-T02
* Completed scope markers: P01–P04 and P01-T01 through P04-T03
* All remaining active-plan markers: P05-T01 through P06-T02
* Status basis: local source, infrastructure, fault tests, full repository validation, and container build are complete; delivery and production verification are external/dependency-gated.

## Execution Summary

Implementation is complete on local baseline `0752d1a` from current `origin/main` and merged PR #680. P01 established the durable boundary before provider mutation and truthful exit changes. Push, PR/issue mutation, merge, deployment, canary, and four-week production verification are intentionally not performed during this invocation.

## Completed Work

### Added the versioned sanitized outbox and correlation schema

* Related phase or task: P01-T01
* Files: `podcaster/distribution_outbox.py`, `tests/test_distribution_outbox.py`
* What changed and why: added deterministic logical identity, immutable artifact correlation, provider objectives, claim/execution/fence, intent, receipt, verification, reconciliation, aggregate, retention timestamps, and allowlisted durable values without URLs, bodies, credentials, tokens, or PII.
* Completion evidence: schema round-trip, malformed/unsafe value, duplicate logical item, conflicting artifact, retention-compatible #680 evidence, and non-public aggregate tests pass.
* Validation: Passed in the 571-test targeted command recorded below.

### Implemented recoverably atomic enqueue and fenced consumed-intent claims

* Related phase or task: P01-T02
* Files: `podcaster/distribution_outbox.py`, `podcaster/queue.py`, `podcaster/video/job_runner.py`, `tests/test_distribution_outbox.py`
* What changed and why: content-addressed artifact upload is read back and hash/size verified before one CAS-authored outbox record; queue notification is a repairable hint; claims use owner, claim ID, execution ID, lease, heartbeat, monotonic fence, and lease-budget inequality; intent is consumed before I/O and any takeover after consumption is read-only.
* Completion evidence: concurrent claim, lease expiry, stale writer, insufficient margin, mutation-to-receipt crash, artifact tamper, notification repair, and read-only takeover tests pass.
* Validation: Passed.

### Added deduplicated bounded reconciliation scheduling

* Related phase or task: P01-T03
* Files: `podcaster/distribution_outbox.py`, `tests/test_distribution_outbox.py`
* What changed and why: provider legs persist due time, separate verification attempt/budget/horizon, one deterministic active token, due scanning, stale-token rejection, terminal/manual exhaustion, and authoritative clearing.
* Completion evidence: fake-clock due/not-due, token deduplication/consumption, restart-readable state, and non-public exit tests pass.
* Validation: Passed.

### Preserved #680 evidence and added disabled-by-default routing

* Related phase or task: P01-T04
* Files: `podcaster/publication_state.py`, `podcaster/distribution_outbox.py`, `podcaster/video/job_runner.py`, `podcaster/queue.py`, `infra/main.bicep`, `infra/modules/aca.bicep`, `infra/modules/aca-video.bicep`, `tests/test_publication_state.py`, `tests/test_distribution_outbox.py`
* What changed and why: existing evidence remains readable and authoritative; new routing is gated by `DISTRIBUTION_OUTBOX_ENABLED=false`; historical unknown/manual evidence remains retry-blocking; repeated enqueue is idempotent; a dedicated queue contains only the outbox identity.
* Completion evidence: legacy evidence compatibility, flag-off, idempotent enqueue, and blocked ambiguous-history assertions pass.
* Validation: Passed.

### Enforced YouTube draft, processing, promotion, and public readback

* Related phase or task: P02-T01
* Files: `podcaster/video/distribution.py`, `podcaster/video/youtube_publish.py`, `podcaster/distribution_worker.py`, `tests/test_video_distribution.py`, `tests/test_youtube_publish.py`, `tests/test_distribution_worker.py`, `infra/modules/aca-video.bicep`
* What changed and why: initial `public` configuration is rejected during config construction; resumable initiation/upload timeout or 5xx is a single consumed ambiguous mutation with no blind retry; the outbox worker uploads a draft, reads upload and processing status, persists a distinct promotion intent, promotes once, and accepts success only from authoritative `privacyStatus=public` readback.
* Completion evidence: zero-provider-call invalid-config test, ambiguous initiation/create tests, processing failure/pending tests, promotion receipt/history test, and public readback test pass.
* Validation: Passed.

### Kept Spotify fail-closed with identity-bound manual handoff

* Related phase or task: P02-T02
* Files: `podcaster/publish.py`, `podcaster/distribution_worker.py`, `tests/test_publish.py`, `tests/test_distribution_worker.py`
* What changed and why: incomplete/paginated absence proof now fails closed by default; the outbox worker does not attempt unsupported unattended public mutation and preserves `manual_handoff_required` unless authoritative expected-item readback is already durable.
* Completion evidence: incomplete listing, ambiguous create/publish, unsupported automation, and manual-handoff non-success tests pass.
* Validation: Passed.

### Persisted provider intents, receipts, and terminal readback

* Related phase or task: P02-T03
* Files: `podcaster/distribution_outbox.py`, `podcaster/distribution_worker.py`, `tests/test_distribution_outbox.py`, `tests/test_distribution_worker.py`
* What changed and why: every operation has a durable intent, consumed fence/time, sanitized receipt, provider identity/state, verification source/time, and aggregate. Distinct upload and promotion intents retain history. Ambiguous receipts prevent mutation-capable takeover.
* Completion evidence: crash mutation-to-receipt, ambiguous receipt takeover, receipt reuse, intent history, sanitization, and aggregate tests pass.
* Validation: Passed.

### Made worker and ACA status truthful

* Related phase or task: P03-T01
* Files: `podcaster/video/job_runner.py`, `podcaster/distribution_worker.py`, `tests/test_video_job_runner.py`, `tests/test_distribution_worker.py`, `infra/main.bicep`, `infra/modules/aca-video.bicep`
* What changed and why: exit 0 requires nonempty processed work with every relevant distribution aggregate externally verified public. Partial, skipped, pending, draft/private/unlisted, unknown, manual, failed, and empty expected drains exit nonzero. Durable actionable states are acknowledged only after evidence persistence; transient pre-mutation video work remains redeliverable.
* Completion evidence: state-lattice unit tests and container smoke exit `2` for missing required queue configuration.
* Validation: Passed.

### Bounded cleanup and one-item execution without importing PR #682 wholesale

* Related phase or task: P03-T02
* Files: `podcaster/storage.py`, `podcaster/video/editor.py`, `podcaster/video/intermediates.py`, `podcaster/video/job_runner.py`, `podcaster/distribution_worker.py`, `tests/test_editor.py`, existing lifecycle suites
* What changed and why: scoped cleanup now deletes at most 1000 enumerated blobs per invocation; video and distribution ACA executions consume one queue item; current-main atomic partial-file/intermediate handling, recorder one-message behavior, deadline/finalization caps, and checkpoint cleanup were retained instead of porting the unsafe #682 stage architecture.
* Completion evidence: focused lifecycle suite passed; W17 uses bounded scoped deletion, W21/W22 use outbox/fence tests, W25 uses ambiguity tests, W29 is covered by one-item worker behavior, and W18–W20/W23–W24/W26–W28 retain current-main regression/non-port evidence pending reviewer-thread replies in P05-T03.
* Validation: Passed.

### Added low-cardinality provider telemetry, Azure alerts, and runbook

* Related phase or task: P03-T03
* Files: `podcaster/distribution_telemetry.py`, `podcaster/distribution_worker.py`, `infra/modules/distribution-alerts.bicep`, `docs/ops/distribution-terminal-truth.md`, `tests/test_distribution_telemetry.py`
* What changed and why: emitted sanitized signals and deployable scheduled-query alerts for pending age, claim latency, lease loss, provider unknown, manual handoff, non-public YouTube, Spotify draft, poison, and public-verification lag; no provider item/job identity is a metric dimension.
* Completion evidence: deterministic fire/clear and no-identity tests, Bicep build, and Checkov pass.
* Validation: Passed.

### Added focused crash, concurrency, provider, exit, and deployment tests

* Related phase or task: P04-T01, P04-T02
* Files: `tests/test_distribution_outbox.py`, `tests/test_distribution_worker.py`, `tests/test_distribution_telemetry.py`, existing provider/worker/lifecycle suites
* What changed and why: added fake-clock and injected fault coverage for artifact/outbox interruption, concurrent claims, lease expiry, consumed intent, stale takeover, ambiguous mutation, receipt crash, reconciliation dedupe, invalid privacy, provider 500/timeout, manual handoff, partial outcomes, and process exit.
* Completion evidence: focused semantic/lifecycle command passed with `949 passed, 1 skipped, 2 deselected`.
* Validation: Passed.

## Implementation-Time Plan and Detail Updates

### Opened the approved full-plan implementation boundary

* Affected plan area or markers: Implementation Status, phase index, P01-T01 through P06-T02
* What changed: recorded the declared full-plan scope, P01-T01 as the active dependency-ready task, allowed write boundary, validation intent, and delivery restrictions.
* Why: the implementation protocol requires canonical state before substantive source edits.
* Triggering evidence: approved plan and phase details; caller-declared scope and no-push/no-PR/no-issue constraints.
* User answer or decision: caller explicitly declared full scope and delivery restrictions.
* Reconciliation performed: plan implementation status, detail phase index, execution boundary, changes execution status, blockers, and remaining markers.
* Planning and critique state: implementation-ready; PC-001 through PC-009 remain resolved by the approved planner revision.

### Reconciled the weekly Spotify acceptance wording

* Affected plan area or markers: P06-T01
* What changed: replaced stale checklist wording that allowed an explicit manual handoff with the approved requirement for authoritative expected-item public readback after any handoff.
* Why: PC-004 and the phase details already require external readback; the checklist summary was internally inconsistent.
* Triggering evidence: implementation of `read_spotify_video_publication_state` and the post-handoff clearing test exposed the stale wording.
* User answer or decision: no new decision; this preserves the caller's confirmed external-readback requirement.
* Reconciliation performed: P06-T01 expected result, provider worker behavior, tests, and changes evidence now agree.
* Planning and critique state: immediately relevant current-state correction; critique remains historical and resolved.

### Reconciled the implementation with the advanced main baseline

* Affected plan area or markers: P04-T03, implementation status, validation record
* What changed: fast-forwarded the incident branch to write-disjoint `origin/main` commit `0752d1a` (`build(deps): update grouped npm dependencies (#683)`), rebuilt the production image, and reran repository validation.
* Why: independent review must evaluate implementation based on current main rather than a stale parent.
* Triggering evidence: the branch became one commit behind during implementation.
* Reconciliation performed: no source conflict or semantic change was required; static checks and container build remained green.
* Validation note: the first post-fast-forward full suite reported one scale-out recorder failure because `docker-compose.fanout.yml` reused the stale pre-fast-forward `podcaster-synthesis:test` image. An explicit Compose rebuild restored the focused test, and the subsequent complete suite passed. No test or gate was weakened.

## Validation Record

| Check | Scope | Status | Evidence or reason |
|---|---|---|---|
| Targeted implementation contract | outbox, telemetry, publication, YouTube, Spotify, video worker, deployment assertions | Passed | `pytest tests/test_distribution_outbox.py tests/test_distribution_telemetry.py tests/test_publication_state.py tests/test_video_distribution.py tests/test_youtube_publish.py tests/test_publish.py tests/test_video_job_runner.py tests/test_deploy_workflow.py -q` → `571 passed in 53.16s`. |
| Expanded semantic/lifecycle suite | outbox worker, provider, worker exit, monitoring, deployment, cleanup, recorder, render, generation | Passed | `949 passed, 1 skipped, 2 deselected in 58.34s`. |
| Full pytest | repository | Passed | Final post-fast-forward `pytest tests/ -q` → `3059 passed, 2 skipped, 2 deselected, 1 warning in 80.91s`. |
| Scale-out fanout regression | Azurite + Docker Compose | Passed | After rebuilding the stale Compose image, `pytest tests/integration/test_scaleout_fanout.py::test_scaleout_fanout_end_to_end -q` → `1 passed in 14.29s`; no source/test change was needed. |
| Compile | production Python | Passed | `python3 -m compileall -q podcaster`. The plan's `python` executable is unavailable on this host; repository execution uses Python 3. |
| Ruff check | production and tests | Passed | `ruff check podcaster tests` → all checks passed. |
| Ruff format | production and tests | Passed | `ruff format --check podcaster tests` → 190 files already formatted. |
| Bicep build | `infra/main.bicep` | Passed | `az bicep build --file infra/main.bicep --stdout >/dev/null`; existing BCP318 warning remains unrelated. |
| Checkov exact plan command | Bicep | Failed (pre-existing baseline) | Unskipped command reports the repository's 7 existing accepted findings. No new finding was introduced. |
| Checkov repository-standard gate | Bicep | Passed | CI-equivalent skip list → 34 passed, 0 failed. |
| Container build | synthesis/distribution image | Passed | Final post-fast-forward `docker build -f Containerfile -t podcaster-synthesis:ci .`; image ID `sha256:02bb1d7b7852cdb748125bbafe3d9572e8732c45fb75e26f39d22b273c62b2a9`. Compose integration image was separately rebuilt as `sha256:6087ecd924d9f0a8972f062430892753089807e3acc625e1f68344d4f83b77dc`. |
| Container exit smoke | distribution worker | Passed | Image execution without required queue configuration returned exit `2`, not success. |
| Diff safety | worktree | Passed | `git diff --check`; no deleted test files; no tracking path added to production code/docs/comments. |

## Pre-Review Reconciliation

* Plan markers and phase details: P01–P04 reconciled complete; P05–P06 remain open.
* Completed-work evidence and handoff prose: current through local implementation and validation.
* Validation, blockers, remaining work, and follow-up items: current.
* Review readiness: ready for independent review of local implementation, but not delivery/production completion.

## Blockers

* P05-T01–P05-T04: caller prohibited push and PR/issue mutation; clearing action is independent review followed by caller/parent authorization for delivery.
* P05-T05: requires merge-derived image deployment authority, real provider credentials, controlled canary, and rollback evidence.
* P06-T01–P06-T02: require four consecutive elapsed production weeks and issue/PR closure evidence.

## Remaining Work

* P05-T01 through P06-T02.

## Follow-Up Items

* Canonical plan list: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`, `## Follow-Up Items`
* Evaluate authoritative Spotify creator mutation/idempotency support if the provider publishes it.
* Perform separate deployed-image/revision forensics if W38 causal reconstruction is still required.

## Return-to-Caller State

* Implementation execution status: Partial
* Declared scope and markers: full plan; P01–P04 complete; P05-T01 through P06-T02 remain.
* Validation coverage: targeted, expanded, full pytest, compile, Ruff, Bicep, repository-standard Checkov, container build, exit smoke, and diff safety completed; exact unskipped Checkov retains the documented pre-existing baseline failures.
* Blockers: delivery and production-evidence phases are dependency-gated and excluded from external mutation in this invocation.
* Current plan and detail updates: implementation opening state recorded.
* Planning and critique state: approved and implementation-ready.
* Follow-up items: Spotify contract reevaluation and optional W38 deployed-image forensics.
* Review readiness or no-handoff reason: local implementation is ready for independent RPI review; delivery and production acceptance remain dependency-gated.
* Continuation owner: active `rpi-quick` parent.
