<!-- markdownlint-disable-file -->
# RPI Plan: Production Provider Terminal Truth

## Task Metadata

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Task slug: `production-provider-terminal-truth`
* Planning status: Implementation-ready after authoritative QA state-model revision
* Plan date: 2026-09-21
* Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
* Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`

## Executive Summary

This revision separates **immutable attempt-level truth** from **weekly publication-identity truth**. Every failed, partial, unknown, manual, and successful attempt remains append-only evidence. Weekly success is derived only by deterministic aggregation over those attempts and exact external-provider proof; a later success never overwrites an earlier failed attempt.

W39 remains the missed publication and is classified `missed_not_dispatched` unless stronger dispatch evidence emerges. W38 may be classified `published_verified_recovered` only if evidence binds the exact weekly publication identity, manifest/digest, canonical artifact, authorized succeeding attempt, provider item, and terminal provider readback while retaining all earlier failed/partial attempt records. Until that proof exists, `published_verified_recovered` is an allowed W38 classification candidate, not an assumed fact.

Provider mutation is intentionally *at-most-once per consumed intent*, not “exactly once.” Every attempt persists sanitized identity and consumes its one mutation authorization before I/O, reconciles before mutation, persists receipts/readback before queue acknowledgment, and stops unattended mutation when identity or state is ambiguous. A lease takeover may reconcile a possibly issued mutation but can never authorize a second mutation for that intent. YouTube follows draft upload → processing verification → public promotion → authoritative privacy readback. Spotify remains bounded reconcile/manual-handoff wherever the unsupported mutation contract or immutable identity cannot be proven, and manual publication must still be followed by external provider readback before canary or weekly acceptance.

The rollout remains feature-flagged and reversible. The canary must originate at the W39-class upstream weekly-publication boundary and end in `published_verified` or controlled `published_verified_recovered` using external provider readback. Four future consecutive post-fix cycles must each end in one of those two green states. `partial`, `provider_unknown`, `manual_action_required`, unresolved duplicate ambiguity, identity mismatch, missing readback, or any other non-green state blocks acceptance.

### User Decisions and Requirements Highlights

* W39 upstream dispatch prevention/detection and end-to-end terminal publication verification are the primary incident goals.
* W38 is a `published_verified_recovered` candidate only when exact identity and provider-readback proof exists; never erase its failed/partial attempts.
* ACA/container success must represent requested-provider terminal truth, not queue drain or API acceptance.
* Initial YouTube `public` configuration is rejected before any provider mutation.
* Unknown mutations are never blindly retried; manual handoff is preserved when exact state cannot be proven.
* Attempt history is immutable; weekly identity aggregation is deterministic and separately recorded.
* Four future post-fix cycles must each be `published_verified` or controlled `published_verified_recovered`; internal status is never sufficient.
* PR #682 is superseded or selectively reworked only after the historical W17–W29 set, all six RV-006 threads, and later current unresolved safety threads have explicit closure evidence.
* Existing CI, tests, idempotency, provider safety, and security gates remain intact.

### What You May Not Know

* W39 has no downstream execution evidence because dispatch was blocked before Azure; provider/outbox changes cannot be presented as its root-cause fix.
* The worktree contains the implemented fenced outbox and provider hardening, but the current review keeps scheduler fairness, deployable alerts, scalable orphan cleanup, and artifact/handoff consistency open.
* PR #682 includes #680 in its ancestry but changes 62 files and has unresolved mutation, boundedness, lease, cleanup, and entrypoint findings; green checks do not make it safe to merge wholesale.
* A durable `provider_unknown` or `manual_action_required` attempt can be acknowledged from the mutation queue to prevent unsafe replay while the worker still exits non-zero and a deduplicated reconciliation/operator path remains actionable.
* A week can be green after controlled recovery only when the earlier non-green attempts remain visible and the succeeding authorized attempt independently satisfies every exact-identity and provider-readback requirement.

### Unresolved Decisions or Blockers

* The exact upstream component that blocked W39 dispatch remains an implementation evidence task, not a planning blocker. No second critique was run because this revision applies an authoritative post-implementation user correction; the original critique and its dispositions remain historical evidence.

## User Decisions and Requirements

* Treat W39 as the active missed-publication incident: it was blocked before Azure dispatch and has no synth, recorder, video, outbox, or provider execution.
* Prevent or detect W39-class upstream dispatch blockage, correlate accepted weekly intent through dispatch and first Azure-side durable arrival, and verify terminal publication by external provider readback.
* Treat W38 as an allowed `published_verified_recovered` classification candidate, not assumed proof. Require exact weekly identity, manifest/digest, canonical artifact, succeeding authorized attempt, provider item, and terminal provider readback evidence while preserving every earlier failed/partial/unknown/manual attempt.
* Make worker/container terminal status truthful: processed failures, partial distribution, unknown provider state, and manual handoff cannot exit as successful completion, while retries remain reconcile-first and idempotent.
* Enforce YouTube draft upload → processing verification → public promotion → authoritative privacy readback, rejecting initial `public` configuration before mutation.
* Use identity-bound bounded reconciliation for ambiguous YouTube/Spotify operations; never blindly retry unknown mutations and retain manual handoff when provider state cannot be proven.
* Complete or safely advance issue #681 from current `origin/main`/#680 and resolve every relevant unresolved safety thread on PR #682; if replaced, document and close/supersede PR #682 clearly.
* Persist sanitized terminal publication receipts and correlations across publication identity, enqueue, execution, provider item identity/state, and aggregate outcome.
* Keep immutable attempt history and a separate weekly aggregation decision; never overwrite a failed attempt with a later weekly success.
* Use explicit weekly states: `published_verified`, `published_verified_recovered`, `partial`, `provider_unknown`, `manual_action_required`, `missed_not_dispatched`, plus deterministic pending/failure/identity-conflict states where evidence requires them.
* Add alerts for pending distribution age, provider unknown, manual handoff, non-public YouTube, Spotify draft, and public-verification lag.
* Add focused provider timeout/500, ambiguous create, mutation-to-receipt crash, lease/concurrency, partial outcome, bad privacy config, and ACA exit/status tests using repository-standard validation.
* Require independent implementation review with no accepted critical finding.
* Push the implementation branch and PR with validation, deployment/canary/rollback instructions, and links to `jmservera/SquadScope-Coordinator#17` and the upstream SquadScope PR when available.
* Canary from the W39-class upstream boundary through Azure execution and external provider readback, ending in one of the two defined green weekly states.
* Verify four future consecutive post-fix scheduled cycles; each must end `published_verified` or controlled `published_verified_recovered` with external provider readback. Every non-green state blocks acceptance.
* Preserve the single existing critique as historical evidence and do not run a second critique for this authoritative post-implementation correction.
* Do not weaken CI/tests/provider safety/idempotency/security gates and do not modify `/home/azureuser/source/SquadScope`.

## Goals

* Prevent or promptly detect recurrence of the W39 pre-Azure dispatch blockage and make cross-boundary arrival evidence durable.
* Establish one durable terminal contract across upstream intent/dispatch, Azure arrival, enqueue, claim, provider mutation/readback, aggregation, worker exit, ACA execution, telemetry, and operations.
* Make redelivery and crash recovery safe through atomic intent creation, fenced claims, identity-bound reconciliation, durable receipts, and fail-closed ambiguity handling.
* Preserve #680 evidence guarantees while completing #681 and replacing only the safe, independently validated intent of #682.
* Preserve W38's complete attempt history and classify its weekly identity only from the exact available proof.
* Preserve every attempt as immutable evidence and derive weekly identity state without destructive collapse.
* Deliver a reversible rollout and sustained production proof based on external provider state.

## Scope and Non-Goals

### In Scope

* W39-class upstream weekly-publication intent, dispatch attempt/result, Azure API acceptance, first Azure-side durable arrival, missing-arrival detection, and cross-boundary correlation.
* Immutable artifact upload and integrity verification followed by one conditional authoritative outbox create, claim-time artifact revalidation, idempotent queue notification, and orphan repair.
* Claim ownership, lease expiry, heartbeat, attempt identity, monotonically increasing fencing token, CAS persistence, consumed mutation authorization, read-only takeover after any possibly issued mutation, bounded reconciliation scheduling, bounded retry, and poison/manual states.
* Sanitized correlation among canonical publication identity, enqueue, outbox item, execution attempt, provider operation, provider item/state, receipt, verification, and aggregate result.
* Attempt-level lifecycle/outcome records and weekly identity-level aggregation decisions with deterministic precedence and proof references.
* YouTube and Spotify reconcile-before-mutate state machines.
* Truthful aggregation, process exit, ACA status behavior, telemetry/alerts, tests/fault injection, feature-flagged deployment, W39-class end-to-end canary, W38 comparative partial-attempt observation, rollback, four-week verification, review, PR, and #682 supersession.

### Non-Goals

* Wholesale merge or cherry-pick of PR #682.
* Assuming W38 is `published_verified_recovered` without exact identity/readback evidence, or deleting/overwriting its earlier failed attempts.
* Attributing W39 to Podcaster synthesis, recorder, video, outbox, or provider code when no W39 Azure execution exists.
* Claims of exactly-once provider mutation.
* Treating draft-only, private, unlisted, pending, unknown, manual handoff, skipped required work, or empty unexpected drains as production success.
* Unattended Spotify public mutation without an authoritative supported contract and immutable identity proof.
* Changes to `/home/azureuser/source/SquadScope`.
* Production implementation during this planning phase.

## Functional Requirements

* W39-class weekly publication intent is durably correlated across the upstream dispatch boundary.
  * Observable acceptance criteria: one sanitized correlation key binds accepted upstream intent, dispatch attempt/result, Azure API acceptance, and the first Azure-side durable enqueue/execution record; a blocked dispatch is distinguishable from a downstream execution failure.
* Missing Azure arrival is detected within a reviewed service-level window.
  * Observable acceptance criteria: an accepted intent with no correlated Azure arrival fires an actionable alert; authoritative arrival clears it; a direct/manual downstream invocation cannot satisfy the missing dispatch evidence.
* Recoverably atomic outbox creation binds a re-readable verified rendered artifact to canonical publication identity through the selected commit protocol.
  * Observable acceptance criteria: immutable artifact upload/readback verification precedes one conditional authoritative outbox create; queue notification is idempotent; claim revalidates integrity; orphan repair is deterministic; repeated enqueue returns the same logical work or a conflict, never a duplicate mutation intent.
* Claims are fenced and bounded without pretending repository fencing can revoke an in-flight provider call.
  * Observable acceptance criteria: owner, claim/attempt ID, lease expiry, heartbeat, attempt count, and fencing token are persisted; a provider call starts only when `remaining_lease > provider_timeout + receipt_margin`; consuming intent permanently removes mutation authority from takeover workers, which may only perform read-only reconciliation after a possibly issued call.
* Every provider operation persists and consumes intent before network I/O and persists receipt/readback before queue acknowledgment.
  * Observable acceptance criteria: crash/replay before intent may safely retry; crash/expiry after consumed intent converges through deduplicated read-only reconciliation without a second mutation.
* YouTube follows a safe public lifecycle.
  * Observable acceptance criteria: initial `public` is rejected at config/payload validation; upload begins private/unlisted; processing/upload success is authoritatively read; public promotion is single-attempt under a fence; final success requires authoritative `privacyStatus=public` readback bound to the expected video identity.
* Spotify remains fail-closed.
  * Observable acceptance criteria: production uses complete bounded reconciliation where supported; ambiguous create/publish, incomplete absence proof, unsupported mutation, or identity mismatch becomes `provider_unknown` or `manual_action_required`, never an automatic retry or success; after manual publication, authoritative readback of the expected provider item/state is still required for canary or cycle acceptance.
* Terminal aggregation is provider-objective aware and truthful.
  * Observable acceptance criteria: exit 0 requires the weekly identity to aggregate to `published_verified` or controlled `published_verified_recovered`; every requested provider is externally verified against the exact manifest/digest and canonical artifact, all duplicate ambiguity is resolved, and all attempt records remain immutable. Every other outcome produces durable actionable evidence and non-zero exit.
* Controlled recovery is authorized, evidence-bound, and never blind.
  * Observable acceptance criteria: retry/reconciliation occurs only after proving the prior mutation state safe; unknown mutation never grants mutation authority. `published_verified_recovered` references the retained failed attempt(s), the explicit authorization, and the succeeding verified attempt.
* Operator signals expose provider truth and age through deployable operational contracts.
  * Observable acceptance criteria: every required alert has a signal, threshold/window, severity, owner/route, missing-data behavior, runbook, and deterministic fire/clear evidence.
* PR #682 is closed or superseded with complete thread evidence.
  * Observable acceptance criteria: every row in the authoritative closure matrix links to a task, code/test or explicit non-port disposition, reviewer reply, and actual resolved/open state; the replacement PR explains why #682 must not be merged wholesale.

## Non-Functional Requirements

* Cross-boundary observability: dispatch evidence is sanitized, durable, queryable, and low-cardinality without carrying article bodies, credentials, tokens, signed URLs, or unnecessary PII.
  * Objective threshold or evaluation condition: every accepted scheduled intent has one correlation chain or one explicit blocked/failed dispatch terminal record.
  * Observable acceptance criteria: correlation, duplicate intent, delayed arrival, missing arrival, and alert fire/clear tests pass.
* Idempotency and fencing: provider mutation is at-most-once for each durable consumed intent; reconciliation is required before every mutation.
  * Objective threshold or evaluation condition: injected concurrency, lease expiry, response loss, and crash boundaries produce no known duplicate mutation and reject stale writes deterministically.
  * Observable acceptance criteria: semantic fault tests cover all #681 crash/replay boundaries and stale takeover.
* Boundedness: network, storage, subprocess, listing, processing verification, and finalization operations honor remaining deadlines.
  * Objective threshold or evaluation condition: no unbounded provider/listing/storage/subprocess wait remains in the affected lifecycle; one worker execution claims at most one distribution item.
  * Observable acceptance criteria: fake-clock/deadline tests and #682 W20/W21/W23/W26/W28/W29 closure evidence pass.
* Evidence safety: durable records contain no secrets, tokens, cookies, signed URLs, request/response bodies, or unnecessary PII.
  * Objective threshold or evaluation condition: allowlisted fields and existing sanitization rules reject unsafe keys/values.
  * Observable acceptance criteria: serialization and sanitization tests cover schema additions.
* Evidence immutability and deterministic aggregation: attempt records are append-only and aggregation decisions are reproducible from referenced receipts.
  * Objective threshold or evaluation condition: no attempt outcome is mutated into another attempt's result; re-aggregation over the same evidence produces the same weekly state and proof set.
  * Observable acceptance criteria: multi-attempt, out-of-order receipt, duplicate candidate, identity mismatch, and recovery tests prove stable precedence.
* Compatibility and rollout: new routing is disabled by default until canary; rollback stops new claims without discarding state or restoring blind inline mutation.
  * Observable acceptance criteria: old evidence remains readable, flag-off behavior is tested, and rollback preserves pending/unknown/manual records for reconciliation.
* Quality: no CI, test, provider safety, idempotency, or security gate is removed, skipped, weakened, or made non-blocking.
  * Observable acceptance criteria: repository-standard validation and independent implementation review pass with no accepted critical finding.

## Acceptance Criteria

* Attempt records use explicit lifecycle/outcome states and remain immutable; weekly aggregation is stored separately and references the complete attempt set.
* A W39-class scheduled publication is accepted upstream, dispatched, correlated to Azure API acceptance and first durable Azure arrival, executes through the Podcaster pipeline, and reaches `published_verified` or controlled `published_verified_recovered`.
* An accepted W39-class intent that does not arrive in Azure within the reviewed window is durably classified and alerted; no W39 downstream execution is fabricated.
* A durable schema, concrete immutable-artifact/conditional-outbox protocol, and state machine implement the invariants in P01 and preserve #680 canonical identity/evidence compatibility.
* YouTube reaches success only after processing success, promotion, and authoritative public readback; initial public config fails before I/O.
* Spotify unknown/unsupported states stop automation and produce durable manual/actionable evidence; manual publication alone never satisfies external-readback acceptance.
* Requested-provider partial, pending, private/unlisted/draft, `provider_unknown`, `manual_action_required`, failed, skipped, identity mismatch, unresolved duplicate ambiguity, missing readback, or unexpected empty-drain states cannot produce exit 0 or a green weekly state.
* All required telemetry and alert rules are deployable, documented, and tested against emitted dimensions.
* The locked test contract is honored: exact owners, no removals, maximum additions, canonical/generated targets, semantic/regression split, and validation evidence.
* Every W17–W29 thread, all six RV-006 threads, and any later unresolved #682 safety thread present before delivery have exact closure evidence; no thread is marked resolved without a reviewer reply plus code/test or explicit non-port evidence.
* Branch is pushed; required checks and final-SHA independent review pass; the PR is approved and merged without content drift; a release image is proven to derive from the merge SHA; the PR contains validation, canary, rollback, Coordinator #17, and the upstream PR link when metadata proves one exists.
* Independent implementation review has no unresolved or accepted critical finding.
* The production canary starts at the W39-class upstream boundary and ends `published_verified` or controlled `published_verified_recovered`, with exact identity/manifest/digest/canonical-artifact proof and external YouTube/Spotify readback.
* Four future consecutive post-fix scheduled cycles each end `published_verified` or controlled `published_verified_recovered`. Any non-green state blocks acceptance and restarts the gate after correction.
* W38 is recorded as `published_verified_recovered` only if the exact proof contract is met; otherwise it remains an explicitly unproven candidate. W39 remains `missed_not_dispatched`.

## Attempt and Weekly Identity State Model

### Attempt-level truth

Each attempt has a stable `attempt_id` and append-only lifecycle events. Required lifecycle states are `created`, `claimed`, `reconciling`, `mutation_authorized`, `mutation_issued`, `receipt_recorded`, and `verifying`. Each attempt terminates independently as exactly one of:

* `published_verified` — exact publication identity, manifest/digest, canonical artifact, provider item, and terminal external-provider state are proven.
* `partial` — some required provider objective or proof completed, but the attempt did not verify the complete requested publication.
* `provider_unknown` — mutation or provider state cannot be proven; no blind retry is authorized.
* `manual_action_required` — safe automation cannot continue and an operator action plus later external readback is required.
* `failed_retryable_pre_mutation` — evidence proves no mutation was issued and a new authorized attempt may be created.
* `failed_terminal` — deterministic failure that cannot continue automatically.
* `cancelled_safe` — cancellation occurred before mutation and the no-mutation proof is retained.

Terminal attempt outcomes are immutable. Recovery creates a new authorized attempt; it never edits, replaces, or hides an earlier attempt.

### Weekly publication-identity truth

The weekly aggregation record has its own stable decision ID and one of:

* `published_verified` — exactly one clean verified publication path, with no earlier non-green attempt and no unresolved ambiguity.
* `published_verified_recovered` — a later authorized attempt satisfies the full green proof contract after one or more retained non-green attempts.
* `partial`
* `provider_unknown`
* `manual_action_required`
* `missed_not_dispatched`
* `failed_terminal`
* `pending`
* `identity_conflict`

Deterministic precedence is:

1. `identity_conflict` for manifest/digest mismatch, wrong canonical artifact, conflicting provider item, or unresolved duplicate ambiguity.
2. `provider_unknown` when any possibly mutated attempt remains unproven.
3. `manual_action_required` when operator action or post-action readback remains open.
4. `partial` when some required objective is incomplete and no higher-precedence condition applies.
5. `missed_not_dispatched` when the weekly cutoff passes without an authorized downstream attempt or proven Azure arrival.
6. `failed_terminal` when all authorized attempts failed deterministically with no safe continuation.
7. `pending` while an authorized attempt remains inside its reviewed processing/reconciliation window.
8. `published_verified_recovered` when a succeeding authorized attempt meets every green proof requirement, all higher-precedence conditions are resolved, and earlier non-green attempts remain referenced.
9. `published_verified` when the clean verified proof exists without prior non-green attempts.

The aggregation decision persists the evaluated attempt IDs, rule/version, decision timestamp, winning proof references, and reasons every higher-precedence state did or did not apply.

### Green proof and controlled recovery gate

A weekly green state requires all of:

* exact week/publication identity and requested provider objectives;
* exact manifest and digest match;
* one canonical artifact selection with hash/size proof;
* provider item identity bound to the publication;
* terminal provider visibility/state readback from the external provider;
* zero unresolved duplicate ambiguity or identity mismatch;
* immutable receipts for mutation intent, mutation result/ambiguity, and terminal readback.

Controlled recovery is allowed only after bounded reconciliation proves a new mutation safe or an authorized operator explicitly approves a new attempt from that evidence. Unknown mutation state cannot be blindly retried. `published_verified_recovered` must reference the retained failed/partial attempt evidence, the recovery authorization, and the succeeding verified attempt.

## Implementation Context Record

| Context item | Current artifact or record |
|---|---|
| Plan | `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md` |
| Phase details | `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md` |
| Latest critique | `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md` (`Revise`: 1 Critical, 4 High, 4 Medium; PC-001–PC-009 were resolved by the original planner revision and remain historical) |
| Relevant research | `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md` |
| Changes-record role | `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md` is created and maintained by implementation as the evidence record |
| Planning execution and readiness | Exactly one critique remains historical; authoritative QA revision applied without a second critique; implementation-ready with reopened dependency-ready markers |
| Continuation context | Active `rpi-quick` parent may continue automatically to implementation |

## Implementation Status

* Execution status: Complete for the declared reopened P01–P04 scope — Amy implemented and validated the attempt/weekly aggregation contract plus RV-002, RV-003, RV-004, and source/docs/tests/runbook/tracking consistency for RV-007; P00-T01, P05, and P06 remain outside this invocation
* Declared scope: reopened P01-T01–P01-T03, P02-T03, P03-T01–P03-T03, and P04-T01–P04-T03, including RV-002, RV-003, RV-004, and the source/docs/tests/runbook/tracking portion of RV-007
* Implementation owner: Amy, independent from locked-out prior authors Bender and Hermes
* Delivery restrictions: no commit, push, deployment, PR/GitHub mutation, or changes to `/home/azureuser/source/SquadScope`
* Active implementation boundary: P00-T01 begins with Podcaster-side receipt/absence observability and an explicit upstream cross-repository blocker because `/home/azureuser/source/SquadScope` cannot be modified; then the reopened markers listed in `Implementation Marker Reconciliation`
* Approved implementation write boundary: this worktree's downstream source, tests, infrastructure, workflows, operator documentation, and RPI tracking artifacts only; do not modify `/home/azureuser/source/SquadScope`, git state, GitHub, PR text, issue threads, deployment, or production
* Validation intent: task-focused attempt-history, aggregation-precedence, recovery, pagination/cleanup, alert-contract, and external-readback tests followed by the full locked repository-standard validation contract
* Current blockers: the exact upstream W39 dispatch prevention/fix belongs to the owning `jmservera/SquadScope` component and is outside this worktree; P00 completes here only to the supported Podcaster receipt/absence boundary with that cross-repository blocker recorded. P05–P06 remain outside scope.

## Sources

* `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`: completed C1–C20/W1–W30 evidence, safe direction, constraints, #682 inventory, and validation/deployment baseline.
* `jmservera/SquadScope-Podcaster#681`: required atomic/fenced outbox boundary.
* `jmservera/SquadScope-Podcaster#680`: merged canonical publication identity, evidence, and reconciliation baseline.
* `jmservera/SquadScope-Podcaster#682`: replacement/supersession target with W17–W29, six RV-006, and later current unresolved safety threads.
* `.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md`: current RV-002, RV-003, RV-004, and RV-007 findings; RV-001/RV-005 resolved; RV-006 planning-level disposition.
* Authoritative QA correction dated 2026-09-21: immutable attempt truth; deterministic weekly aggregation; evidence-conditional W38 recovery classification; W39 missed/not-dispatched; exact external-readback and four-cycle gates.

## Phase Checklist

<!-- rpi:phase id=P00 -->
### [ ] P00: Prevent and detect W39-class upstream dispatch blockage

* Intent: Identify the W39 dispatch boundary, make intent-to-Azure arrival observable, and prove the incident path before relying on downstream hardening.
* Dependencies: corrected research and access to upstream/Azure metadata.

<!-- rpi:task id=P00-T01 -->
#### [ ] P00-T01: Trace and control the W39 dispatch boundary

* Requirement and evidence: authoritative correction; corrected research Q10/Q12.
* Expected result: the owning upstream stage that blocked W39 is identified from durable evidence, recurrence controls are implemented in the owning repository, and no Podcaster worker/provider component is named as W39 root cause without execution evidence.
* Detail section: P00-T01 in `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`.

<!-- rpi:task id=P00-T02 -->
#### [x] P00-T02: Persist dispatch-to-Azure correlation and missing-arrival alerts

* Requirement and evidence: corrected research Implementation Constraints A1–A3.
* Expected result: accepted weekly intent, dispatch attempt/result, Azure API acceptance, and first Azure durable arrival share one sanitized correlation; missing arrival fires and authoritative arrival clears an actionable alert.
* Detail section: P00-T02 in phase details.

<!-- rpi:task id=P00-T03 -->
#### [x] P00-T03: Prove the upstream-to-provider integration boundary

* Requirement and evidence: corrected research C18/Q7 and authoritative canary correction.
* Expected result: automated integration coverage starts at the W39-class upstream boundary and proves dispatch, Azure arrival, downstream execution, and terminal provider-readback correlation without requiring live provider credentials in ordinary CI.
* Detail section: P00-T03 in phase details.

<!-- rpi:phase id=P01 -->
### [x] P01: Establish the durable outbox and immutable attempt contract

* Intent: Add the atomic/fenced storage, identity, correlation, claim, receipt, and migration foundation without provider mutation.
* Dependencies: current `origin/main`/#680.

<!-- rpi:task id=P01-T01 -->
#### [x] P01-T01: Define outbox, attempt, receipt, and aggregation schemas

* Requirement and evidence: C13–C14, C20; #681; caller requirements 4–5.
* Expected result: versioned sanitized records bind week/publication identity, immutable attempt identity/history, manifest/digest, canonical artifact, provider item, mutation intent/receipt, terminal readback, and weekly aggregation decision.
* Detail section: P01-T01 in `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`.

<!-- rpi:task id=P01-T02 -->
#### [x] P01-T02: Implement atomic enqueue and fenced claim lifecycle

* Requirement and evidence: C14, W14; research constraints 3–5.
* Expected result: retain the implemented immutable artifact, conditional outbox, claim, and fencing surfaces; add RV-004 bounded orphan discovery, retention, and repair-or-delete behavior with interruption tests.
* Detail section: P01-T02 in phase details.

<!-- rpi:task id=P01-T03 -->
#### [x] P01-T03: Schedule deduplicated provider reconciliation

* Requirement and evidence: provider pending/unknown states; safe queue acknowledgment; critique PC-003.
* Expected result: retain due-time/budget/token state; fix RV-002 with durable notification state, paginated/fair scanning beyond 100 records, restart recovery, and stale-notification repair tests.
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
* Expected result: preserve early privacy validation and no-blind-retry behavior; fix RV-001 read-only promotion convergence and RV-005 by performing genuinely identity-bound lookup where provider-supported or recording accurately named unprovable unknown/manual evidence.
* Detail section: P02-T01 in phase details.

<!-- rpi:task id=P02-T02 -->
#### [x] P02-T02: Enforce Spotify bounded reconciliation and manual handoff

* Requirement and evidence: C10–C12, W8–W13.
* Expected result: complete bounded identity reads, one consumed-intent mutation where provably safe, durable unknown/manual outcomes, and no unsupported unattended public promotion.
* Detail section: P02-T02 in phase details.

<!-- rpi:task id=P02-T03 -->
#### [x] P02-T03: Persist provider intent, receipts, terminal readback, and attempt outcomes

* Requirement and evidence: C13–C15; caller requirement 5.
* Expected result: append-only sanitized evidence records every provider transition and authoritative final state before acknowledgment, including bounded read-only takeover outcomes required by RV-001 and accurate evidence-source naming required by RV-005.
* Detail section: P02-T03 in phase details.

<!-- rpi:phase id=P03 -->
### [x] P03: Make execution, cleanup, and weekly aggregation truthful

* Intent: Align queue disposition, worker exit, ACA status, bounded lifecycle behavior, and operator signals with durable provider truth.
* Dependencies: P01–P02.

<!-- rpi:task id=P03-T01 -->
#### [x] P03-T01: Implement truthful attempt and weekly aggregation

* Requirement and evidence: C1–C5; W1–W3.
* Expected result: attempt outcomes remain immutable; weekly aggregation follows the defined precedence; exit 0 occurs only for `published_verified` or controlled `published_verified_recovered`; safe retries create new authorized attempts.
* Detail section: P03-T01 in phase details.

<!-- rpi:task id=P03-T02 -->
#### [x] P03-T02: Bound execution, cleanup, and one-item worker behavior

* Requirement and evidence: W17–W24, W26–W29.
* Expected result: preserve the implemented bounded/one-item behavior; add RV-002 starvation-safe reconciliation scanning and RV-004 orphan artifact retention/cleanup. The six RV-006 #682 threads remain closure evidence work under P05-T03.
* Detail section: P03-T02 in phase details.

<!-- rpi:task id=P03-T03 -->
#### [x] P03-T03: Add attempt/weekly-state telemetry and deployable alerts

* Requirement and evidence: C15, C17–C18; caller requirement 6.
* Expected result: retain sanitized signals; fix RV-003 by implementing warning and critical thresholds/windows, missing-data behavior, explicit routing, and generated-query fire/clear tests for every alert contract row.
* Detail section: P03-T03 in phase details.

<!-- rpi:phase id=P04 -->
### [x] P04: Prove safety with focused tests and repository validation

* Intent: Exercise semantic failure boundaries and preserve regression coverage and quality gates.
* Dependencies: P01–P03.

<!-- rpi:task id=P04-T01 -->
#### [x] P04-T01: Add attempt-history, outbox, fencing, crash, and concurrency fault tests

* Requirement and evidence: C16–C17; #681 crash matrix.
* Expected result: prove immutable failed-attempt retention, authorized recovery, no retry after unknown mutation, scheduler traversal beyond storage-page boundaries, and scalable reference-safe orphan cleanup.
* Detail section: P04-T01 in phase details.

<!-- rpi:task id=P04-T02 -->
#### [x] P04-T02: Add provider, aggregation, alert, and terminal-exit semantic tests

* Requirement and evidence: C1–C12, C16–C17.
* Expected result: table-drive every aggregation state and precedence edge, exact identity/readback proof, W38 candidate classification, W39 missed classification, RV-003 executable routes/missing-data alerts, and non-green exit behavior.
* Detail section: P04-T02 in phase details.

<!-- rpi:task id=P04-T03 -->
#### [x] P04-T03: Run locked repository-standard validation

* Requirement and evidence: C18; caller requirement 7.
* Expected result: rerun targeted and full validation after P00 and RV-001–RV-005 changes without weakening gates; preserve the previous baseline as historical implementation evidence, not final acceptance.
* Detail section: P04-T03 in phase details.

<!-- rpi:phase id=P05 -->
### [ ] P05: Deliver reviewed, reversible implementation

* Intent: Push coordinated upstream/Podcaster PRs as required, pass checks, independently review final pushed SHAs with an agent other than locked-out original implementer Bender, merge without drift, establish merge-SHA image provenance, resolve prior work, and deploy reversibly.
* Dependencies: P04.

<!-- rpi:task id=P05-T01 -->
#### [ ] P05-T01: Push branch and open the implementation PR

* Requirement and evidence: caller requirement 9.
* Expected result: branch is pushed; PR explains immutable attempt versus weekly identity truth, gives W38's evidence-conditional classification rule and W39's `missed_not_dispatched` status, includes current RV-002/RV-003/RV-004/RV-007 dispositions, validation evidence, deployment/canary/rollback, Coordinator #17, the coordinated upstream PR, and explicit `Supersedes #682`.
* Detail section: P05-T01 in phase details.

<!-- rpi:task id=P05-T02 -->
#### [ ] P05-T02: Pass required checks and independent final-SHA review

* Requirement and evidence: caller requirement 8 and repository required checks.
* Expected result: required PR checks pass; an independent agent other than Bender reviews each final pushed SHA for dispatch correctness, concurrency/idempotency, provider safety, evidence security, operations, and tests; no critical finding is accepted or unresolved, and any post-review content change triggers revalidation/re-review.
* Detail section: P05-T02 in phase details.

<!-- rpi:task id=P05-T03 -->
#### [ ] P05-T03: Resolve or supersede every PR #682 safety thread

* Requirement and evidence: W16–W29.
* Expected result: W17–W29, the six RV-006 rows, and later current unresolved rows each have code/test or non-port evidence, a reviewer-facing reply, and actual resolved/closed state; #682 is closed once replacement linkage is durable. No row is pre-marked resolved.
* Detail section: P05-T03 in phase details.

<!-- rpi:task id=P05-T04 -->
#### [ ] P05-T04: Approve, merge, and prove release image provenance

* Requirement and evidence: C18; critique PC-005.
* Expected result: PR approval and required checks gate merge; merge introduces no content drift; release workflow builds/identifies an image derived from the merge SHA; reviewed SHA, merged SHA, workflow run, digest, and equality/provenance checks are durable.
* Detail section: P05-T04 in phase details.

<!-- rpi:task id=P05-T05 -->
#### [ ] P05-T05: Deploy feature-flagged canary and verify rollback

* Requirement and evidence: C18; research constraints 14–15.
* Expected result: exact merge-derived artifacts are deployed with routing disabled; the canary originates at the W39-class upstream boundary and ends `published_verified` or controlled `published_verified_recovered`. Evidence proves exact identity, manifest/digest, canonical artifact, authorized attempt chain, provider item, terminal external readback, and zero unresolved duplicate ambiguity. Every non-green state rejects the canary. Alert contracts fire/clear and rollback stops new dispatch/claims while preserving every attempt.
* Detail section: P05-T05 in phase details.

<!-- rpi:phase id=P06 -->
### [ ] P06: Verify four consecutive post-fix production cycles

* Intent: Prove sustained scheduled dispatch, Azure execution correlation, and external provider terminal truth.
* Dependencies: P05 canary accepted and production routing enabled.

<!-- rpi:task id=P06-T01 -->
#### [ ] P06-T01: Record four weekly external-readback verification windows

* Requirement and evidence: caller requirement 10.
* Expected result: four future consecutive post-fix cycles each independently end `published_verified` or controlled `published_verified_recovered`, with upstream dispatch/Azure correlation, immutable attempt history, exact identity/manifest/digest/canonical-artifact proof, provider item identity, and terminal external readback. Any non-green state blocks acceptance and restarts the gate after correction.
* Detail section: P06-T01 in phase details.

<!-- rpi:task id=P06-T02 -->
#### [ ] P06-T02: Close production verification and residual actions

* Requirement and evidence: four-week evidence and any canary/weekly findings.
* Expected result: changes record summarizes sustained proof, incidents/reconciliations, rollback readiness, issue/PR closures, and any genuinely out-of-scope follow-up without claiming internal green status as provider success.
* Detail section: P06-T02 in phase details.

## Dependencies

* P00 begins at the owning upstream weekly-publication boundary. It may require a coordinated upstream repository worktree/PR, but must not directly modify `/home/azureuser/source/SquadScope`.
* Current `origin/main` and merged PR #680 are the authoritative baseline; implementation first updates the worktree from that baseline without importing #682 wholesale.
* P01 precedes provider mutation because truthful non-zero exit is unsafe until retries are fenced.
* P02 precedes terminal aggregation because provider state definitions and receipts must exist before exit can be authoritative.
* P04 must pass before independent review and deployment.
* P05 required checks and independent final-pushed-SHA review must have no accepted critical finding before approval/merge.
* Production deploy uses only a release image proven to derive from the merge SHA.
* P06 requires an accepted W39-class end-to-end canary from P05-T05 and remains open until four future consecutive post-fix cycles each have complete green proof.

## Implementation Marker Reconciliation

| Marker(s) | Revised disposition | Required next evidence |
|---|---|---|
| P00-T01 | Preserved external gate | Exact W39 blocked-stage evidence and upstream prevention/detection remain owned by `jmservera/SquadScope`; W39 stays `missed_not_dispatched` |
| P01-T01, P02-T03, P03-T01, P04-T01–P04-T02 | Reopened for authoritative QA state model | Immutable attempt ledger; receipt/correlation schema; deterministic weekly aggregation; controlled recovery; W38/W39 classification tests |
| P01-T02, P03-T02, P04-T01 | Reopened for RV-004 | Complete bounded/paginated reference enumeration or equivalent safe index; cleanup must make progress beyond 5,000 records |
| P01-T03, P03-T02, P04-T01 | Reopened for RV-002 | Durable pagination/continuation or bounded sharding that eventually visits every retained due record; starvation test beyond 5,000 |
| P03-T03, P04-T02 | Reopened for RV-003 | Distinct deployable action routes plus executable absence/depth/heartbeat detection and generated action/query assertions |
| P04-T03 | Reopened validation gate | Targeted and full validation after the QA state-model and RV-002/RV-003/RV-004 corrections |
| Phase details; P05-T01 | Reopened for RV-007 | Canonical artifact statuses reconciled in planning; PR narrative must use current review, state model, validation, and residual-work truth |
| P05-T03 | Expanded by RV-006 and refreshed current metadata | Closure evidence for W17–W29, the six RV-006 threads, and four later unresolved threads found during revision; do not claim resolution without GitHub evidence |
| P01-T04, P02-T01–P02-T02 | Implemented surfaces; dependency verification | Preserve safe behavior unless the new receipt/attempt schema requires minimal compatible updates |
| Existing W38 references in plan/PR handoff | Evidence-conditional correction | Use `published_verified_recovered` only with full exact proof; otherwise label it an allowed candidate and retain all attempt history |
| W39 classification | Settled | `missed_not_dispatched`; no downstream attempt may be fabricated |
| Original PC-001–PC-009 dispositions | Historical, no change | Preserve existing critique artifact and disposition record; no second critique |

Implementation and independent review for reopened markers must be assigned to an agent other than Bender.

### Implemented Surface Disposition

| Disposition | Exact existing surfaces | Revision consequence |
|---|---|---|
| State-model and narrative correction | `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`, `.copilot-tracking/pr/pr.md`, implementation PR body/handoff, production evidence summaries | Preserve immutable attempt truth; use W38 `published_verified_recovered` only with exact proof; keep W39 `missed_not_dispatched`; separate W39 remediation from downstream hardening. |
| W39 observability additions | Owning upstream dispatch workflow/client/status store identified by P00-T01; Podcaster/Azure ingress or queue arrival metadata; `podcaster/monitoring.py` or the selected low-cardinality telemetry owner; alert infrastructure; cross-boundary integration owner | Add accepted-intent → dispatch → Azure API acceptance → first durable arrival correlation, missing-arrival fire/clear, and integration/canary evidence. Exact upstream paths are evidence-selected in P00-T01 rather than guessed. |
| Review-driven code/test additions | `podcaster/publication_state.py`, `podcaster/distribution_worker.py`, `podcaster/distribution_outbox.py`, `podcaster/distribution_scheduler.py`, `infra/modules/distribution-alerts.bicep`, and focused owner tests | Implement the QA attempt/aggregation contract plus RV-002, RV-003, and RV-004; preserve resolved RV-001/RV-005 behavior. |
| Dependency verification | disabled-by-default routing; `podcaster/publish.py` Spotify fail-closed/manual action; YouTube read-only promotion convergence | Preserve these independent safety requirements and extend only as needed for the compatible receipt/attempt schema. |
| Narrowed/removed | Assumed W38 recovery, W38 incident-cause claims, Podcaster-only W39 canary, blind retry, or provider hardening presented as W39 root-cause remediation | Remove from active implementation and acceptance. |

## Locked Test and Validation Contract

### Ownership and allowed test targets

* P00-T01–P00-T03 own focused tests in the upstream dispatch owner plus Podcaster ingress/deployment integration assertions; exact upstream paths are selected only after P00-T01 identifies the owning stage.
* P01-T01/P01-T04 own `tests/test_publication_state.py` and compatibility assertions in `tests/test_monitoring.py`.
* P01-T02/P04-T01 own a new `tests/test_distribution_outbox.py` and, only if process-level storage integration requires it, one new `tests/integration/test_distribution_outbox.py`.
* P01-T03/P04-T01 own at most one new `tests/test_distribution_reconciliation.py`.
* P02-T01/P04-T02 own `tests/test_video_distribution.py`, `tests/test_youtube_publish.py`, `tests/test_youtube_upload.py`, and `tests/test_youtube_distribute_integration.py`.
* P02-T02/P04-T02 own `tests/test_publish.py`.
* P03-T01/P04-T02 own `tests/test_video_job_runner.py`.
* P03-T02 owns existing affected lifecycle suites: `tests/test_clipset.py`, `tests/test_edl_render.py`, `tests/test_video_intermediates.py`, `tests/test_recorder.py`, `tests/test_editor.py`, and `tests/test_video_gen.py`.
* P03-T03 owns `tests/test_monitoring.py`, deployment assertions in `tests/test_deploy_workflow.py`, and at most one new `tests/test_distribution_telemetry.py`.
* P05-T04/P05-T05 own `tests/test_deploy_workflow.py` and at most one new `tests/integration/test_distribution_canary.py`.
* The existing outbox/publication-state/worker owners must cover immutable multi-attempt history, aggregation precedence, exact proof references, controlled recovery authorization, and out-of-order receipt replay; do not add a parallel state-model test owner.

### Removals and maximum additions

* Test removals: **none**. Existing provider safety, ambiguity, sanitization, lease, and external-verification assertions must remain semantically equivalent or stronger.
* Existing downstream maximum new test-file allocation remains **six**. P00 may add the minimum focused tests in the owning upstream repository and one cross-boundary integration owner; those additions are separately justified because the original downstream-only lock could not cover the corrected W39 boundary.
* Maximum new shared test helper/fixture modules: **two**, under `tests/fixtures/`, only when each is reused across at least three owner suites.
* New named test cases have **no numerical ceiling**: every required scenario matrix row must have independently diagnosable assertions. The file/helper maxima organize ownership and may not be used to omit, merge beyond diagnosis, skip, or weaken safety coverage; exceeding them requires an explicit plan update before implementation continues.

### Canonical and generated targets

* Canonical sources are Python modules under `podcaster/`, Bicep under `infra/`, workflow YAML under `.github/workflows/`, repository docs, and the tracking changes record.
* Generated Bicep JSON/stdout, coverage output, local provider fixtures containing live identifiers, container layers, and transient canary output are **not committed**.
* Sanitized canary/four-week summaries belong in the changes record or an existing operator evidence location selected during implementation; secrets, signed URLs, cookies, tokens, bodies, and unnecessary PII are never persisted.

### Semantic versus regression coverage

* Semantic coverage proves state transitions, fencing, identity binding, ambiguity handling, provider readback, queue disposition, exit codes, alert conditions, feature flags, and rollback.
* Fault coverage includes failed attempt followed by authorized success, unknown mutation followed by prohibited blind retry, manifest/digest mismatch, wrong canonical artifact, duplicate provider candidates, missing terminal readback, out-of-order receipts, scheduler records beyond 5,000, cleanup beyond 5,000, and executable alert missing-data behavior.
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
| Missing Azure arrival | accepted upstream intent without correlated first Azure durable arrival | reviewed warning/critical windows derived in P00-T02; critical before the scheduled publication window is irrecoverable | upstream dispatch owner and production owner | missing upstream telemetry is itself warning; direct downstream execution does not clear | blocked-dispatch fixture fires; correlated first arrival clears |
| Claim latency / lease loss | enqueue-to-claim latency and lease-loss counter | latency warning `p95 >5m for 15m`; any lease loss `>=1 in 5m` critical | operations/page | heartbeat metric missing while active claims exist is critical | delayed claim and forced expiry fire; clean claim/heartbeat clears |
| Provider unknown | count of `provider_unknown` | any item `>=1 for 5m` critical | production owner page + incident ticket | missing state metric with active outbox is warning | ambiguous response fixture fires; authoritative reconciliation clears |
| Manual action | count/age of `manual_action_required` | any item warning in `5m`; critical if age `>24h` | operator queue; aged item pages | no-data with known manual records is warning | manual fixture fires; post-action external readback clears |
| Non-public YouTube | promoted/expected-public video not authoritatively public | warning after `15m`; critical after `60m` | production owner | readback failures count as verification lag, not healthy | private/unlisted readback fires; public readback clears |
| Spotify draft | requested-public Spotify item remains draft | warning after `15m`; critical after `24h` | manual publication owner; aged item pages | unavailable readback remains non-success and warns | draft readback fires; externally read published state clears |
| Poison exhaustion | outbox item enters `poisoned` | any item immediate critical | production owner page + incident ticket | missing poison metric with poison record is critical | exhaustion fixture fires; operator resolution/requeue clears |
| Public-verification lag | mutation/receipt-to-external-verification duration | warning `>15m`; critical `>60m` | operations/page | missing verification heartbeat while pending is warning | delayed verifier fires; external verification clears |
| Identity/duplicate ambiguity | weekly aggregate has manifest/digest mismatch, wrong canonical artifact, conflicting provider items, or unresolved duplicate candidates | any item immediate critical | production owner page + incident ticket | missing aggregation telemetry with active attempts is warning | ambiguity fixture fires; exact identity reconciliation and re-aggregation clears |
| Weekly non-green terminal state | weekly identity reaches `partial`, `provider_unknown`, `manual_action_required`, `missed_not_dispatched`, `failed_terminal`, or `identity_conflict` | any scheduled identity immediate critical | production owner plus owning operational route | missing weekly decision after cutoff is critical | each state fixture fires; only externally proven green decision clears |

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
| Production acceptance and issue closure | P06-T01–P06-T02 | Accepted canary plus four future green post-fix cycles, closure comment linking replacement PR/evidence, issue closed |

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
| RV-006 / r4066838845 | Non-fan-out resume mutates before equivalent durable claim | P01-T02, P03-T01, P04-T01, P05-T03 | Shared lease/fence-protected resume path or explicit non-port proof; concurrency test; reviewer reply; actual thread state |
| RV-006 / r4066838898 | Provider admission exception swallowed by generic playlist handler | P02-T01, P03-T01, P04-T02, P05-T03 | Preserve/re-raise admission failure or explicit non-port proof; provider-cutoff test; reviewer reply; actual thread state |
| RV-006 / r4066838929 | Recorder runbook visibility contradicts deployed 840-second contract | P03-T02, P04-T03, P05-T03 | Align canonical deployment/RFC/runbook value with test evidence; reviewer reply; actual thread state |
| RV-006 / r4066838959 | Fallback storage work escapes remaining deadline | P03-T02, P04-T03, P05-T03 | Pass admission/bounded storage runner or explicit non-port proof; timeout test; reviewer reply; actual thread state |
| RV-006 / r4066838991 | Durable video budget starts after retryable preflight I/O | P03-T02, P04-T03, P05-T03 | Persist lifecycle start before retryable preflight or explicit non-port proof; redelivery test; reviewer reply; actual thread state |
| RV-006 / r4066839027 | Synchronous full-file hashing ignores stage budget | P03-T02, P04-T03, P05-T03 | Budget-aware/owned hashing or explicit non-port proof; large/stalled-read test; reviewer reply; actual thread state |
| Current / r4066949442 | Terminal manifest can reference another job/clip blob | P03-T02, P04-T02, P05-T03 | Validate clip identity and exact content-addressed path or explicit non-port proof; ownership test; reviewer reply; actual thread state |
| Current / r4066949487 | Broad recorder setup handler deletes transient storage failures | P03-T02, P04-T02, P05-T03 | Preserve retry for transient storage/timeouts or explicit non-port proof; redelivery test; reviewer reply; actual thread state |
| Current / r4066949518 | Composed checkpoint identity omits source/removal fields | P03-T02, P04-T02, P05-T03 | Include all composition-affecting fields or explicit non-port proof; stale-checkpoint test; reviewer reply; actual thread state |
| Current / r4066949564 | Failed checkpoint validation deletes existing destination | P03-T02, P04-T02, P05-T03 | Preserve existing destination and clean only staged temporary file or explicit non-port proof; failure test; reviewer reply; actual thread state |

## Critique Disposition

The existing critique is preserved unchanged as historical evidence. No second critique was run or requested. PC-001–PC-009 retain their recorded dispositions; this authoritative QA revision and the current review's RV-002/RV-003/RV-004/RV-007 routes are planner-owned current-state updates.

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
* If deeper W38 attempt reconstruction is desired, trace the deployed image/revision as comparative observability work only; W38's successful publication is authoritative and not reopened.

## Handoff

* Implementation artifact: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
* Approved implementation marker range: P00-T01 through P06-T02, limited by the dispositions in `Implementation Marker Reconciliation`.
* Remaining blockers: P00-T01 upstream ownership; P05 git/GitHub/deployment/provider authority; P06 four elapsed future post-fix cycles.
