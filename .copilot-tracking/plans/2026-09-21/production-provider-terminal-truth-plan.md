<!-- markdownlint-disable-file -->
# RPI Plan: Production Provider Terminal Truth

## Task Metadata

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Task slug: `production-provider-terminal-truth`
* Planning status: Implementation-ready after one critique and planner-owned revision
* Plan date: 2026-09-21
* Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
* Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`

## Executive Summary

This plan makes production distribution truthful from durable enqueue through provider readback and process exit. It starts from current `origin/main` and merged PR #680, completes issue #681 as an atomic fenced distribution outbox, and selectively reworks—not wholesale merges—PR #682 changes. A worker may exit successfully only when every requested provider is externally verified in its required public state. Partial, draft, pending, unknown, manual-handoff, or failed outcomes remain durable and recoverable but are not reported as successful completion.

Provider mutation is intentionally *at-most-once per consumed intent*, not “exactly once.” Every attempt persists sanitized identity and consumes its one mutation authorization before I/O, reconciles before mutation, persists receipts/readback before queue acknowledgment, and stops unattended mutation when identity or state is ambiguous. A lease takeover may reconcile a possibly issued mutation but can never authorize a second mutation for that intent. YouTube follows draft upload → processing verification → public promotion → authoritative privacy readback. Spotify remains bounded reconcile/manual-handoff wherever the unsupported mutation contract or immutable identity cannot be proven, and manual publication must still be followed by external provider readback before canary or weekly acceptance.

The rollout is feature-flagged and reversible. It adds provider-state alerts, focused crash/concurrency/provider fault tests, repository-standard validation, independent implementation review with no accepted critical finding, a linked implementation PR, a real-provider production canary, and four consecutive weeks of external readback evidence.

### User Decisions and Requirements Highlights

* ACA/container success must represent requested-provider terminal truth, not queue drain or API acceptance.
* Initial YouTube `public` configuration is rejected before any provider mutation.
* Unknown mutations are never blindly retried; manual handoff is preserved when exact state cannot be proven.
* PR #682 is superseded or selectively reworked only after all 13 relevant unresolved threads have explicit closure evidence.
* Existing CI, tests, idempotency, provider safety, and security gates remain intact.

### What You May Not Know

* Current `main` already contains #680 canonical identity, append-only evidence, external-verification semantics, and ambiguity safeguards, but it has no fenced outbox and still exits zero for `partial`.
* PR #682 includes #680 in its ancestry but changes 62 files and has unresolved mutation, boundedness, lease, cleanup, and entrypoint findings; green checks do not make it safe to merge wholesale.
* A durable `unknown` or `manual_handoff_required` item can be acknowledged from the mutation queue to prevent unsafe replay while the worker still exits non-zero and a deduplicated due-time reconciliation/operator path remains actionable.

### Unresolved Decisions or Blockers

* None. The single critique returned `Revise` with 9 planner-owned findings; all are dispositioned below without a second critique.

## User Decisions and Requirements

* Make worker/container terminal status truthful: processed failures, partial distribution, unknown provider state, and manual handoff cannot exit as successful completion, while retries remain reconcile-first and idempotent.
* Enforce YouTube draft upload → processing verification → public promotion → authoritative privacy readback, rejecting initial `public` configuration before mutation.
* Use identity-bound bounded reconciliation for ambiguous YouTube/Spotify operations; never blindly retry unknown mutations and retain manual handoff when provider state cannot be proven.
* Complete or safely advance issue #681 from current `origin/main`/#680 and resolve every relevant unresolved safety thread on PR #682; if replaced, document and close/supersede PR #682 clearly.
* Persist sanitized terminal publication receipts and correlations across publication identity, enqueue, execution, provider item identity/state, and aggregate outcome.
* Add alerts for pending distribution age, provider unknown, manual handoff, non-public YouTube, Spotify draft, and public-verification lag.
* Add focused provider timeout/500, ambiguous create, mutation-to-receipt crash, lease/concurrency, partial outcome, bad privacy config, and ACA exit/status tests using repository-standard validation.
* Require independent implementation review with no accepted critical finding.
* Push the implementation branch and PR with validation, deployment/canary/rollback instructions, and links to `jmservera/SquadScope-Coordinator#17` and the upstream SquadScope PR when available.
* Verify production by external provider readback during canary and for four consecutive weeks, not by internal green status alone.
* Do not weaken CI/tests/provider safety/idempotency/security gates and do not modify `/home/azureuser/source/SquadScope`.

## Goals

* Establish one durable terminal contract across enqueue, claim, provider mutation/readback, aggregation, worker exit, ACA execution, telemetry, and operations.
* Make redelivery and crash recovery safe through atomic intent creation, fenced claims, identity-bound reconciliation, durable receipts, and fail-closed ambiguity handling.
* Preserve #680 evidence guarantees while completing #681 and replacing only the safe, independently validated intent of #682.
* Deliver a reversible rollout and sustained production proof based on external provider state.

## Scope and Non-Goals

### In Scope

* Immutable artifact upload and integrity verification followed by one conditional authoritative outbox create, claim-time artifact revalidation, idempotent queue notification, and orphan repair.
* Claim ownership, lease expiry, heartbeat, attempt identity, monotonically increasing fencing token, CAS persistence, consumed mutation authorization, read-only takeover after any possibly issued mutation, bounded reconciliation scheduling, bounded retry, and poison/manual states.
* Sanitized correlation among canonical publication identity, enqueue, outbox item, execution attempt, provider operation, provider item/state, receipt, verification, and aggregate result.
* YouTube and Spotify reconcile-before-mutate state machines.
* Truthful aggregation, process exit, ACA status behavior, telemetry/alerts, tests/fault injection, feature-flagged deployment, canary, rollback, four-week readback verification, review, PR, and #682 supersession.

### Non-Goals

* Wholesale merge or cherry-pick of PR #682.
* Claims of exactly-once provider mutation.
* Treating draft-only, private, unlisted, pending, unknown, manual handoff, skipped required work, or empty unexpected drains as production success.
* Unattended Spotify public mutation without an authoritative supported contract and immutable identity proof.
* Changes to `/home/azureuser/source/SquadScope`.
* Production implementation during this planning phase.

## Functional Requirements

* Recoverably atomic outbox creation binds a re-readable verified rendered artifact to canonical publication identity through the selected commit protocol.
  * Observable acceptance criteria: immutable artifact upload/readback verification precedes one conditional authoritative outbox create; queue notification is idempotent; claim revalidates integrity; orphan repair is deterministic; repeated enqueue returns the same logical work or a conflict, never a duplicate mutation intent.
* Claims are fenced and bounded without pretending repository fencing can revoke an in-flight provider call.
  * Observable acceptance criteria: owner, claim/attempt ID, lease expiry, heartbeat, attempt count, and fencing token are persisted; a provider call starts only when `remaining_lease > provider_timeout + receipt_margin`; consuming intent permanently removes mutation authority from takeover workers, which may only perform read-only reconciliation after a possibly issued call.
* Every provider operation persists and consumes intent before network I/O and persists receipt/readback before queue acknowledgment.
  * Observable acceptance criteria: crash/replay before intent may safely retry; crash/expiry after consumed intent converges through deduplicated read-only reconciliation without a second mutation.
* YouTube follows a safe public lifecycle.
  * Observable acceptance criteria: initial `public` is rejected at config/payload validation; upload begins private/unlisted; processing/upload success is authoritatively read; public promotion is single-attempt under a fence; final success requires authoritative `privacyStatus=public` readback bound to the expected video identity.
* Spotify remains fail-closed.
  * Observable acceptance criteria: production uses complete bounded reconciliation where supported; ambiguous create/publish, incomplete absence proof, unsupported mutation, or identity mismatch becomes `publication_unknown` or `manual_handoff_required`, never an automatic retry or success; after manual publication, authoritative readback of the expected provider item/state is still required for canary or weekly acceptance.
* Terminal aggregation is provider-objective aware and truthful.
  * Observable acceptance criteria: exit 0 requires every requested production provider to be externally verified public; all other requested-provider outcomes produce durable actionable evidence and non-zero exit. Pre-mutation transient failures may retry; durable unknown/manual states are queue-acknowledged to prevent mutation replay but remain execution failure/actionable.
* Operator signals expose provider truth and age through deployable operational contracts.
  * Observable acceptance criteria: every required alert has a signal, threshold/window, severity, owner/route, missing-data behavior, runbook, and deterministic fire/clear evidence.
* PR #682 is closed or superseded with complete thread evidence.
  * Observable acceptance criteria: each W17–W29 thread links to a task, code/test or explicit non-port disposition, reviewer reply, and resolved/closed state; the replacement PR explains why #682 must not be merged wholesale.

## Non-Functional Requirements

* Idempotency and fencing: provider mutation is at-most-once for each durable consumed intent; reconciliation is required before every mutation.
  * Objective threshold or evaluation condition: injected concurrency, lease expiry, response loss, and crash boundaries produce no known duplicate mutation and reject stale writes deterministically.
  * Observable acceptance criteria: semantic fault tests cover all #681 crash/replay boundaries and stale takeover.
* Boundedness: network, storage, subprocess, listing, processing verification, and finalization operations honor remaining deadlines.
  * Objective threshold or evaluation condition: no unbounded provider/listing/storage/subprocess wait remains in the affected lifecycle; one worker execution claims at most one distribution item.
  * Observable acceptance criteria: fake-clock/deadline tests and #682 W20/W21/W23/W26/W28/W29 closure evidence pass.
* Evidence safety: durable records contain no secrets, tokens, cookies, signed URLs, request/response bodies, or unnecessary PII.
  * Objective threshold or evaluation condition: allowlisted fields and existing sanitization rules reject unsafe keys/values.
  * Observable acceptance criteria: serialization and sanitization tests cover schema additions.
* Compatibility and rollout: new routing is disabled by default until canary; rollback stops new claims without discarding state or restoring blind inline mutation.
  * Observable acceptance criteria: old evidence remains readable, flag-off behavior is tested, and rollback preserves pending/unknown/manual records for reconciliation.
* Quality: no CI, test, provider safety, idempotency, or security gate is removed, skipped, weakened, or made non-blocking.
  * Observable acceptance criteria: repository-standard validation and independent implementation review pass with no accepted critical finding.

## Acceptance Criteria

* A durable schema, concrete immutable-artifact/conditional-outbox protocol, and state machine implement the invariants in P01 and preserve #680 canonical identity/evidence compatibility.
* YouTube reaches success only after processing success, promotion, and authoritative public readback; initial public config fails before I/O.
* Spotify unknown/unsupported states stop automation and produce durable manual/actionable evidence; manual publication alone never satisfies external-readback acceptance.
* Requested-provider partial, pending, private/unlisted/draft, unknown, manual, failed, skipped, or unexpected empty-drain states cannot produce exit 0.
* All required telemetry and alert rules are deployable, documented, and tested against emitted dimensions.
* The locked test contract is honored: exact owners, no removals, maximum additions, canonical/generated targets, semantic/regression split, and validation evidence.
* Every W17–W29 thread has exact closure evidence; #682 is explicitly resolved or superseded.
* Branch is pushed; required checks and final-SHA independent review pass; the PR is approved and merged without content drift; a release image is proven to derive from the merge SHA; the PR contains validation, canary, rollback, Coordinator #17, and the upstream PR link when metadata proves one exists.
* Independent implementation review has no unresolved or accepted critical finding.
* Production canary and four consecutive weekly checks record external YouTube/Spotify readback rather than internal green status.

## Implementation Context Record

| Context item | Current artifact or record |
|---|---|
| Plan | `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md` |
| Phase details | `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md` |
| Latest critique | `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md` (`Revise`: 1 Critical, 4 High, 4 Medium; all PC-001–PC-009 resolved by this revision) |
| Relevant research | `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md` |
| Changes-record role | `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md` is created and maintained by implementation as the evidence record |
| Planning execution and readiness | Exactly one critique complete; planner-owned revision applied; implementation-ready |
| Continuation context | Active `rpi-quick` parent may continue automatically to implementation |

## Implementation Status

* Execution status: Partial — local implementation and P01–P04 validation complete; externally gated P05–P06 remain open
* Declared scope: Full approved plan, P01-T01 through P06-T02
* Active implementation boundary: P05-T01, dependency-gated by the caller's no-push/no-PR/no-issue instruction
* Approved write boundary: this worktree's production source, tests, infrastructure, workflows, operator documentation, and RPI tracking artifacts; `/home/azureuser/source/SquadScope` is excluded
* Validation intent: task-focused semantic and fault tests followed by the full locked repository-standard validation contract
* Current blockers: none for local implementation; P05 delivery and P06 production verification remain dependency-gated and the caller has prohibited push/PR/issue mutation during this implementation invocation

## Sources

* `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`: completed C1–C20/W1–W30 evidence, safe direction, constraints, #682 inventory, and validation/deployment baseline.
* `jmservera/SquadScope-Podcaster#681`: required atomic/fenced outbox boundary.
* `jmservera/SquadScope-Podcaster#680`: merged canonical publication identity, evidence, and reconciliation baseline.
* `jmservera/SquadScope-Podcaster#682`: replacement/supersession target with W17–W29 unresolved safety threads.
* Caller requirements dated 2026-09-21: authoritative outcomes, exact artifact paths, one-critique constraint, implementation review, PR, and production verification.

## Phase Checklist

<!-- rpi:phase id=P01 -->
### [x] P01: Establish the durable outbox contract

* Intent: Add the atomic/fenced storage, identity, correlation, claim, receipt, and migration foundation without provider mutation.
* Dependencies: current `origin/main`/#680.

<!-- rpi:task id=P01-T01 -->
#### [x] P01-T01: Define outbox and correlation schemas

* Requirement and evidence: C13–C14, C20; #681; caller requirements 4–5.
* Expected result: versioned sanitized records bind canonical publication identity to enqueue, artifact, claim/execution, provider operations, receipts/readback, aggregation, and retention.
* Detail section: P01-T01 in `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`.

<!-- rpi:task id=P01-T02 -->
#### [x] P01-T02: Implement atomic enqueue and fenced claim lifecycle

* Requirement and evidence: C14, W14; research constraints 3–5.
* Expected result: immutable artifact upload/verification precedes one conditional authoritative outbox create; queue notification is idempotent; claim-time integrity revalidation and orphan repair are defined; lease/heartbeat/takeover uses monotonic fencing and consumed mutation authorization so any possibly issued mutation can only be reconciled.
* Detail section: P01-T02 in phase details.

<!-- rpi:task id=P01-T03 -->
#### [x] P01-T03: Schedule deduplicated provider reconciliation

* Requirement and evidence: provider pending/unknown states; safe queue acknowledgment; critique PC-003.
* Expected result: durable `next_reconcile_at`, separate verification attempt/budget, one active scheduled token, fenced one-item execution, exhaustion/manual transitions, safe empty-drain behavior, and alert clearing are explicit and tested.
* Detail section: P01-T03 in phase details.

<!-- rpi:task id=P01-T04 -->
#### [x] P01-T04: Preserve #680 evidence and migrate routing safely

* Requirement and evidence: C13–C16, W15.
* Expected result: backward-readable #680 evidence, flag-off compatibility, reconciliation-only migration/backfill, and no automatic replay of historical ambiguous mutations.
* Detail section: P01-T04 in phase details.

<!-- rpi:phase id=P02 -->
### [x] P02: Implement reconcile-first provider state machines

* Intent: Move provider work behind the fenced outbox and make authoritative provider state the only success proof.
* Dependencies: P01.

<!-- rpi:task id=P02-T01 -->
#### [x] P02-T01: Enforce the YouTube draft-processing-public lifecycle

* Requirement and evidence: C6–C9, W4–W7, W12, W25.
* Expected result: early privacy validation, persisted upload intent, bounded identity reconciliation, processing/upload verification, gated promotion, public readback, and no blind retry after ambiguous resumable-session initiation.
* Detail section: P02-T01 in phase details.

<!-- rpi:task id=P02-T02 -->
#### [x] P02-T02: Enforce Spotify bounded reconciliation and manual handoff

* Requirement and evidence: C10–C12, W8–W13.
* Expected result: complete bounded identity reads, one consumed-intent mutation where provably safe, durable unknown/manual outcomes, and no unsupported unattended public promotion.
* Detail section: P02-T02 in phase details.

<!-- rpi:task id=P02-T03 -->
#### [x] P02-T03: Persist provider intent, receipts, and terminal readback

* Requirement and evidence: C13–C15; caller requirement 5.
* Expected result: append-only sanitized evidence records every provider transition and authoritative final state before acknowledgment.
* Detail section: P02-T03 in phase details.

<!-- rpi:phase id=P03 -->
### [x] P03: Make execution, cleanup, and provider aggregation truthful

* Intent: Align queue disposition, worker exit, ACA status, bounded lifecycle behavior, and operator signals with durable provider truth.
* Dependencies: P01–P02.

<!-- rpi:task id=P03-T01 -->
#### [x] P03-T01: Implement truthful aggregation and queue disposition

* Requirement and evidence: C1–C5; W1–W3.
* Expected result: exit 0 only for all-requested externally verified public providers; durable unknown/manual states are mutation-queue acknowledged without becoming success; transient pre-mutation work remains safely retryable.
* Detail section: P03-T01 in phase details.

<!-- rpi:task id=P03-T02 -->
#### [x] P03-T02: Bound execution, cleanup, and one-item worker behavior

* Requirement and evidence: W17–W24, W26–W29.
* Expected result: bounded cleanup/storage/subprocess/finalization, safe artifact handling, visibility/application budget alignment, terminal cleanup on resume, and one claimed item per worker execution.
* Detail section: P03-T02 in phase details.

<!-- rpi:task id=P03-T03 -->
#### [x] P03-T03: Add provider-state telemetry and alerts

* Requirement and evidence: C15, C17–C18; caller requirement 6.
* Expected result: sanitized metrics/logs/alerts for outbox age, claim latency/lease loss, provider unknown, manual handoff, non-public YouTube, Spotify draft, poison, and verification lag.
* Detail section: P03-T03 in phase details.

<!-- rpi:phase id=P04 -->
### [x] P04: Prove safety with focused tests and repository validation

* Intent: Exercise semantic failure boundaries and preserve regression coverage and quality gates.
* Dependencies: P01–P03.

<!-- rpi:task id=P04-T01 -->
#### [x] P04-T01: Add outbox, fencing, crash, and concurrency fault tests

* Requirement and evidence: C16–C17; #681 crash matrix.
* Expected result: deterministic fake-clock and injected-fault coverage for artifact/outbox interruption, enqueue, scheduling/deduplication, claim, lease expiry before/during/after provider I/O, consumed intent, read-only takeover, mutation/receipt crash windows, poison, and stale-owner rejection.
* Detail section: P04-T01 in phase details.

<!-- rpi:task id=P04-T02 -->
#### [x] P04-T02: Add provider and terminal-exit semantic tests

* Requirement and evidence: C1–C12, C16–C17.
* Expected result: provider timeout/500, ambiguous create/publish, bad privacy config, processing/readback, partial/manual/unknown/draft/pending/skipped/empty outcomes, and ACA entrypoint exit behavior are proven.
* Detail section: P04-T02 in phase details.

<!-- rpi:task id=P04-T03 -->
#### [x] P04-T03: Run locked repository-standard validation

* Requirement and evidence: C18; caller requirement 7.
* Expected result: targeted tests plus full pytest, compileall, Ruff check/format, Bicep build, Checkov, relevant workflow tests, and synthesis container build complete without weakened gates.
* Detail section: P04-T03 in phase details.

<!-- rpi:phase id=P05 -->
### [ ] P05: Deliver reviewed, reversible implementation

* Intent: Push the replacement PR, pass required checks, independently review the final pushed SHA, merge without drift, establish merge-SHA image provenance, resolve prior work, and deploy reversibly.
* Dependencies: P04.

<!-- rpi:task id=P05-T01 -->
#### [ ] P05-T01: Push branch and open the implementation PR

* Requirement and evidence: caller requirement 9.
* Expected result: branch is pushed; PR includes validation evidence, schema/state-machine summary, deployment/canary/rollback, Coordinator #17, metadata-only discovery of the upstream SquadScope PR when available, and explicit `Supersedes #682`.
* Detail section: P05-T01 in phase details.

<!-- rpi:task id=P05-T02 -->
#### [ ] P05-T02: Pass required checks and independent final-SHA review

* Requirement and evidence: caller requirement 8 and repository required checks.
* Expected result: required PR checks pass; independent review of the final pushed SHA covers correctness, concurrency/idempotency, provider safety, evidence security, operations, and tests; no critical finding is accepted or unresolved, and any post-review content change triggers revalidation/re-review.
* Detail section: P05-T02 in phase details.

<!-- rpi:task id=P05-T03 -->
#### [ ] P05-T03: Resolve or supersede every PR #682 safety thread

* Requirement and evidence: W16–W29.
* Expected result: W17–W29 each has code/test or non-port evidence, a reviewer-facing reply, and resolved/closed status; #682 is closed once replacement linkage is durable.
* Detail section: P05-T03 in phase details.

<!-- rpi:task id=P05-T04 -->
#### [ ] P05-T04: Approve, merge, and prove release image provenance

* Requirement and evidence: C18; critique PC-005.
* Expected result: PR approval and required checks gate merge; merge introduces no content drift; release workflow builds/identifies an image derived from the merge SHA; reviewed SHA, merged SHA, workflow run, digest, and equality/provenance checks are durable.
* Detail section: P05-T04 in phase details.

<!-- rpi:task id=P05-T05 -->
#### [ ] P05-T05: Deploy feature-flagged canary and verify rollback

* Requirement and evidence: C18; research constraints 14–15.
* Expected result: exact merge-derived image is deployed with routing disabled, controlled real-provider canary proves external YouTube and Spotify state (including post-handoff Spotify readback where necessary), staged enablement occurs, alert contracts fire/clear, and rollback stops new claims while preserving outbox evidence.
* Detail section: P05-T05 in phase details.

<!-- rpi:phase id=P06 -->
### [ ] P06: Verify four consecutive production weeks

* Intent: Prove sustained production terminal truth through external provider readback.
* Dependencies: P05 canary accepted and production routing enabled.

<!-- rpi:task id=P06-T01 -->
#### [ ] P06-T01: Record four weekly external-readback verification windows

* Requirement and evidence: caller requirement 10.
* Expected result: four consecutive publication weeks record sanitized correlation, YouTube authoritative public/processing readback, Spotify authoritative expected-item public readback after any manual handoff, ACA exit, alert health, and reconciliation outcome; handoff alone never satisfies a week.
* Detail section: P06-T01 in phase details.

<!-- rpi:task id=P06-T02 -->
#### [ ] P06-T02: Close production verification and residual actions

* Requirement and evidence: four-week evidence and any canary/weekly findings.
* Expected result: changes record summarizes sustained proof, incidents/reconciliations, rollback readiness, issue/PR closures, and any genuinely out-of-scope follow-up without claiming internal green status as provider success.
* Detail section: P06-T02 in phase details.

## Dependencies

* Current `origin/main` and merged PR #680 are the authoritative baseline; implementation first updates the worktree from that baseline without importing #682 wholesale.
* P01 precedes provider mutation because truthful non-zero exit is unsafe until retries are fenced.
* P02 precedes terminal aggregation because provider state definitions and receipts must exist before exit can be authoritative.
* P04 must pass before independent review and deployment.
* P05 required checks and independent final-pushed-SHA review must have no accepted critical finding before approval/merge.
* Production deploy uses only a release image proven to derive from the merge SHA.
* P06 requires an accepted production canary from P05-T05 and remains open until four consecutive weekly external-readback windows complete.

## Locked Test and Validation Contract

### Ownership and allowed test targets

* P01-T01/P01-T04 own `tests/test_publication_state.py` and compatibility assertions in `tests/test_monitoring.py`.
* P01-T02/P04-T01 own a new `tests/test_distribution_outbox.py` and, only if process-level storage integration requires it, one new `tests/integration/test_distribution_outbox.py`.
* P01-T03/P04-T01 own at most one new `tests/test_distribution_reconciliation.py`.
* P02-T01/P04-T02 own `tests/test_video_distribution.py`, `tests/test_youtube_publish.py`, `tests/test_youtube_upload.py`, and `tests/test_youtube_distribute_integration.py`.
* P02-T02/P04-T02 own `tests/test_publish.py`.
* P03-T01/P04-T02 own `tests/test_video_job_runner.py`.
* P03-T02 owns existing affected lifecycle suites: `tests/test_clipset.py`, `tests/test_edl_render.py`, `tests/test_video_intermediates.py`, `tests/test_recorder.py`, `tests/test_editor.py`, and `tests/test_video_gen.py`.
* P03-T03 owns `tests/test_monitoring.py`, deployment assertions in `tests/test_deploy_workflow.py`, and at most one new `tests/test_distribution_telemetry.py`.
* P05-T04/P05-T05 own `tests/test_deploy_workflow.py` and at most one new `tests/integration/test_distribution_canary.py`.

### Removals and maximum additions

* Test removals: **none**. Existing provider safety, ambiguity, sanitization, lease, and external-verification assertions must remain semantically equivalent or stronger.
* Maximum new test files: **six** total, allocated to outbox unit, optional outbox integration, reconciliation scheduler, telemetry, canary, and one independently diagnosable provider/ACA integration boundary if existing owners cannot express it.
* Maximum new shared test helper/fixture modules: **two**, under `tests/fixtures/`, only when each is reused across at least three owner suites.
* New named test cases have **no numerical ceiling**: every required scenario matrix row must have independently diagnosable assertions. The file/helper maxima organize ownership and may not be used to omit, merge beyond diagnosis, skip, or weaken safety coverage; exceeding them requires an explicit plan update before implementation continues.

### Canonical and generated targets

* Canonical sources are Python modules under `podcaster/`, Bicep under `infra/`, workflow YAML under `.github/workflows/`, repository docs, and the tracking changes record.
* Generated Bicep JSON/stdout, coverage output, local provider fixtures containing live identifiers, container layers, and transient canary output are **not committed**.
* Sanitized canary/four-week summaries belong in the changes record or an existing operator evidence location selected during implementation; secrets, signed URLs, cookies, tokens, bodies, and unnecessary PII are never persisted.

### Semantic versus regression coverage

* Semantic coverage proves state transitions, fencing, identity binding, ambiguity handling, provider readback, queue disposition, exit codes, alert conditions, feature flags, and rollback.
* Regression coverage preserves #680 evidence/readback behavior and all existing provider, lifecycle, infrastructure, workflow, sanitization, and security tests.
* Mock-only green status is insufficient for canary or four-week acceptance; those phases require sanitized external provider readback.

### Required validation evidence

```bash
pytest tests/test_publication_state.py tests/test_video_distribution.py tests/test_youtube_publish.py tests/test_youtube_upload.py tests/test_publish.py tests/test_video_job_runner.py tests/test_monitoring.py tests/test_deploy_workflow.py -q
pytest tests/ -q
python -m compileall podcaster
ruff check podcaster tests
ruff format --check podcaster tests
az bicep build --file infra/main.bicep --stdout >/dev/null
checkov --directory infra --framework bicep
docker build -f Containerfile -t podcaster-synthesis:ci .
```

* Add new outbox/telemetry/canary test paths to the targeted command when created.
* Record command, exit status, relevant summary, commit SHA, and exact image digest in the changes record.
* Do not bypass, skip, mark expected-failure, reduce assertions, or change required checks to non-blocking to obtain green status.

## Alert Contract

Thresholds are initial production defaults and remain configurable through reviewed infrastructure; changing them requires equivalent tests and runbook updates.

| Alert | Signal and sanitized dimensions | Default threshold/window | Severity and route | Missing-data behavior | Required fire/clear evidence |
|---|---|---|---|---|---|
| Pending distribution age | oldest non-terminal outbox age by environment/provider/media kind | warning `>15m for 10m`; critical `>60m for 5m` | warning to operations; critical page to production owner | no-data is healthy only when queue depth is also zero; inconsistent no-data warns | fake-clock aged item fires; authoritative terminal transition clears |
| Claim latency / lease loss | enqueue-to-claim latency and lease-loss counter | latency warning `p95 >5m for 15m`; any lease loss `>=1 in 5m` critical | operations/page | heartbeat metric missing while active claims exist is critical | delayed claim and forced expiry fire; clean claim/heartbeat clears |
| Provider unknown | count of `publication_unknown` | any item `>=1 for 5m` critical | production owner page + incident ticket | missing state metric with active outbox is warning | ambiguous response fixture fires; authoritative reconciliation clears |
| Manual handoff | count/age of `manual_handoff_required` | any item warning in `5m`; critical if age `>24h` | operator queue; aged item pages | no-data with known manual records is warning | manual fixture fires; post-handoff external readback clears |
| Non-public YouTube | promoted/expected-public video not authoritatively public | warning after `15m`; critical after `60m` | production owner | readback failures count as verification lag, not healthy | private/unlisted readback fires; public readback clears |
| Spotify draft | requested-public Spotify item remains draft | warning after `15m`; critical after `24h` | manual publication owner; aged item pages | unavailable readback remains non-success and warns | draft readback fires; externally read published state clears |
| Poison exhaustion | outbox item enters `poisoned` | any item immediate critical | production owner page + incident ticket | missing poison metric with poison record is critical | exhaustion fixture fires; operator resolution/requeue clears |
| Public-verification lag | mutation/receipt-to-external-verification duration | warning `>15m`; critical `>60m` | operations/page | missing verification heartbeat while pending is warning | delayed verifier fires; external verification clears |

Every deployed rule records rule ID, owner, route, runbook URL, dimensions, and canary-safe synthetic/controlled fire-clear evidence in the changes record.

## Issue #681 Acceptance Matrix

| #681 requirement | Planned tasks | Required completion/closure evidence |
|---|---|---|
| Atomic outbox creation with verified artifact | P01-T01–P01-T02, P04-T01 | Immutable upload/verify → conditional outbox protocol; interruption/orphan tests |
| Fenced claims and stale-owner safety | P01-T02, P02, P04-T01 | Lease margin, consumed intent, read-only takeover, stale-write and in-flight-expiry tests |
| Dedicated queue/lease/budget and one-item worker | P01-T02–P01-T03, P03-T02 | Queue/scheduler schema, budget invariant, one-item entrypoint tests |
| Reconcile before mutate | P02-T01–P02-T03 | Provider scenario tests and durable intent/readback evidence |
| Crash/replay matrix | P04-T01 | All artifact/enqueue/claim/intent/mutation/receipt/verification/ack boundaries mapped and passing |
| Metrics and alerts | P03-T03, P05-T05 | Alert contract, deployed rule IDs, fire/clear evidence |
| Feature flag and rollback | P01-T04, P05-T05 | Disabled-by-default routing, staged enablement, rollback drill preserving state |
| Production acceptance and issue closure | P06-T01–P06-T02 | Canary plus four-week external readback, closure comment linking replacement PR/evidence, issue closed |

## PR #682 Thread Closure Matrix

| Evidence | Risk | Planned task | Required closure evidence |
|---|---|---|---|
| W17 / r4018616332 | Unbounded clipset prefix deletion | P03-T02, P04-T03, P05-T03 | Scoped cleanup implementation/test or explicit non-port proof; reviewer reply and resolved thread |
| W18 / r4018616414 | Partial render artifact after ffmpeg failure | P03-T02, P04-T03, P05-T03 | Atomic/cleanup regression test; reviewer reply and resolved thread |
| W19 / r4018616473 | Leaked intermediate `.part` file | P03-T02, P04-T03, P05-T03 | Failure cleanup test; reviewer reply and resolved thread |
| W20 / r4018903541 | Recorder visibility shorter than lifetime | P03-T02, P04-T03, P05-T03 | Bicep budget invariant and workflow/infra test; reviewer reply and resolved thread |
| W21 / r4018903592 | Video visibility can strand pending distribution | P01-T02, P03-T02, P05-T03 | Outbox decoupling plus queue/lease budget test; reviewer reply and resolved thread |
| W22 / r4018903639 | Resume mutates before lease | P01-T02, P03-T01, P05-T03 | Fenced claim before provider action and concurrency test; reviewer reply and resolved thread |
| W23 / r4018903679 | Unbounded communicate after SIGKILL | P03-T02, P04-T03, P05-T03 | Bounded subprocess cleanup test; reviewer reply and resolved thread |
| W24 / r4018903724 | Playwright guard blocks checkpoint replay | P03-T02, P05-T03 | Confirm corrected/currently outdated with regression evidence or independently rework; reviewer reply and resolved thread |
| W25 / r4018903793 | Ambiguous YouTube resumable session recreation | P02-T01, P04-T02, P05-T03 | Intent-before-I/O, no-blind-retry ambiguity test; reviewer reply and resolved thread |
| W26 / r4018903838 | Fan-in deadline does not bound storage probes | P03-T02, P04-T03, P05-T03 | Remaining-budget storage test; reviewer reply and resolved thread |
| W27 / r4018903866 | Resumed terminal outcome bypasses cleanup | P03-T02, P04-T03, P05-T03 | Unified terminal wrapper and resume cleanup test; reviewer reply and resolved thread |
| W28 / r4018903912 | Recorder finalization ignores deadline | P03-T02, P04-T03, P05-T03 | Deadline-aware finalization test; reviewer reply and resolved thread |
| W29 / r4018903953 | Recorder drains multiple messages | P03-T02, P04-T03, P05-T03 | One-message entrypoint test; reviewer reply and resolved thread |

## Critique Disposition

| Critique run and finding | Disposition | Plan response or residual risk |
|---|---|---|
| Single critique / PC-001 | Resolved by planner | Added lease-budget inequality, consumed mutation authorization, read-only takeover, quarantine/reconciliation, and before/during/after I/O expiry tests. External calls remain inherently unrevocable, so safety comes from prohibiting a second mutation. |
| Single critique / PC-002 | Resolved by planner | Selected immutable artifact upload + integrity verification → conditional authoritative outbox create → idempotent queue notify, with claim-time revalidation and orphan repair/interruption tests. |
| Single critique / PC-003 | Resolved by planner | Added P01-T03 durable reconciliation scheduler with due time, separate verification budget, deduplicated token, fenced one-item execution, exhaustion, empty-drain, alert, and fake-clock tests. |
| Single critique / PC-004 | Resolved by planner | Manual Spotify publication must be followed by authoritative external readback of the expected item; handoff alone cannot pass canary/week acceptance. |
| Single critique / PC-005 | Resolved by planner | Reordered P05 to push/open PR, required checks, final-SHA independent review, approval/merge without drift, merge-SHA release image provenance, then canary deployment. |
| Single critique / PC-006 | Resolved by planner | Added #681 acceptance matrix and P06-T02 issue closure evidence/state gate. |
| Single critique / PC-007 | Resolved by planner | Added deployable alert contract with thresholds/windows, severity/routes, missing-data behavior, and fire/clear evidence. |
| Single critique / PC-008 | Resolved by planner | Replaced named-test ceiling with mandatory independently diagnosable scenario coverage; retained exact maximum of six new test files and two helper modules as the caller-required additions lock, with plan-update escape rather than coverage compression. |
| Single critique / PC-009 | Resolved consistent with user requirement | P05-T01 performs metadata-only upstream PR discovery and requires the link when available; verified absence is recorded in PR/changes because the caller explicitly qualified the link “when available.” No SquadScope checkout is modified. |

## Follow-Up Items

* If Spotify publishes an authoritative creator mutation/idempotency contract and immutable identity capability, evaluate a separate plan to replace permanent manual handoff; this is outside current safe automation scope.
* If causal W38 forensics are still desired, trace the deployed image/revision separately; it does not change this implementation boundary.

## Handoff

* Implementation artifact: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
* Approved implementation marker range: P01-T01 through P06-T02, including newly added P01-T04 and P05-T05.
* Remaining provisional question or blocker: None.
