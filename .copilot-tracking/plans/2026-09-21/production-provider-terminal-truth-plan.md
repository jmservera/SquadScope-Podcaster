<!-- markdownlint-disable-file -->
# RPI Plan: Production Provider Terminal Truth

## Task Metadata

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Task slug: `production-provider-terminal-truth`
* Planning status: Basher rejected exact head `df473dc0c059680b9c454ddab263c5c454e2ef2b` because ownership can transfer after the post-compose check and before final promotion, immutable archive, outbox/notification handoff, direct provider mutation, or terminal success. Frank completed the sole-author implementation and required validation. One fail-closed persisted video execution claim now binds owner, claim/execution identity, monotonic fence, authoritative visibility/lease expiry, CAS/readback, and durable boundary permits. Every downstream mutation or handoff consumes the current permit; takeover reconciles existing durable artifacts without granting a duplicate provider mutation. Exact probes `13`, focused `416`, locked `940`, and full `3302` passed with all required static, infrastructure, container, Compose, and security gates. Commit, push, GitHub evidence, and Rusty final-SHA review remain. Basher and jmservera's prior implementation context did not advise or pair; Bender, Hermes, and Amy remain excluded. P00-T01 is complete through upstream PR `jmservera/SquadScope#773`, reviewed head `d75e3f5523f4810edbcaeef9a217d34cd21825a2`, merged as `7a6d8811bf82507cbdd0b01ba1135bc42e5942f3` with all 18 checks successful. P05-T03 remains pending Rusty acceptance of the corrected final SHA. PR #682 remains open. P05-T04–P05-T06 and P06 remain future.
* Plan date: 2026-09-21
* Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
* Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`

## Executive Summary

This review-follow-up revision preserves the immutable attempt/weekly publication model and adds a distinct dependency-ordered P07 phase for the six active findings from the canonical review: RV-002, RV-003, RV-004, RV-007, RV-008, and RV-009. Earlier P01–P04 completion claims are historical implementation claims, not current acceptance evidence.

W39 remains the missed publication and is classified `missed_not_dispatched` unless stronger dispatch evidence emerges. W38 may be classified `published_verified_recovered` only if evidence binds the exact weekly publication identity, manifest/digest, canonical artifact, authorized succeeding attempt, provider item, and terminal provider readback while retaining all earlier failed/partial attempt records. Until that proof exists, `published_verified_recovered` is an allowed W38 classification candidate, not an assumed fact.

Provider mutation is intentionally *at-most-once per consumed intent*, not “exactly once.” Every attempt persists sanitized identity and consumes its one mutation authorization before I/O, reconciles before mutation, persists receipts/readback before queue acknowledgment, and stops unattended mutation when identity or state is ambiguous. A lease takeover may reconcile a possibly issued mutation but can never authorize a second mutation for that intent. YouTube follows draft upload → processing verification → public promotion → authoritative privacy readback. Spotify remains bounded reconcile/manual-handoff wherever the unsupported mutation contract or immutable identity cannot be proven, and manual publication must still be followed by external provider readback before canary or weekly acceptance.

P07 requires atomic scheduler notification ownership, one telemetry vocabulary shared by emitters/rules/tests/runbook, bounded fenced cleanup, exact identity-bound provider proof with durable recovery authorization, proof-backed four-cycle evaluation, and truthful delivery evidence. The rollout remains feature-flagged and reversible. P00-T01 is complete through merged upstream PR #773. P05-T03–P05-T06 and P06 elapsed production cycles remain residual gates, so PR #684 stays draft/blocked until those gates are satisfied. A generic W39-class canary is not production acceptance.

### User Decisions and Requirements Highlights

* W39 upstream dispatch prevention/detection and end-to-end terminal publication verification are the primary incident goals.
* W38 is a `published_verified_recovered` candidate only when exact identity and provider-readback proof exists; never erase its failed/partial attempts.
* ACA/container success must represent requested-provider terminal truth, not queue drain or API acceptance.
* Initial YouTube `public` configuration is rejected before any provider mutation.
* Unknown mutations are never blindly retried; manual handoff is preserved when exact state cannot be proven.
* Attempt history is immutable; weekly identity aggregation is deterministic and separately recorded.
* Four future post-fix cycles must each be `published_verified` or controlled `published_verified_recovered`; internal status is never sufficient.
* After all required Podcaster and upstream fixes are reviewed, merged, and deployed from exact merge-SHA artifacts and every review/deployment gate is clear, execute the real W39 generation and publishing pipeline. Reconcile all existing W39 intents, attempts, receipts, and provider records before mutation; fail closed to manual action rather than risk duplicate publication.
* PR #682 is superseded or selectively reworked only after the historical W17–W29 set, all six RV-006 threads, and later current unresolved safety threads have explicit closure evidence.
* Existing CI, tests, idempotency, provider safety, and security gates remain intact.
* Basher's rejection of `5fdd69f5210053daa742f3690d0fa34c5795f1bc` is authoritative for this cycle: exhausted chunk-upload ambiguity, unverifiable checkpoint sizes, and invalid final media must fail closed.
* Leela's revision at `02241a1` was rejected by Fry, Farnsworth's source candidate `601d36a` was rejected by Livingston, Frank's revision at `1efa749` was rejected by Rusty, Basher's revision at `eaaac57` was rejected by Ralph, and Ralph's revision at `e16963243973707ea2557f75f925d3c6935d49ee` was rejected by Livingston. A new author/reviewer pair must preserve the lockout and independence contract for the next correction.

### What You May Not Know

* W39 has no downstream execution evidence because dispatch was blocked before Azure; provider/outbox changes cannot be presented as its root-cause fix.
* The worktree contains implemented outbox/provider hardening, but the canonical review proved six active gaps: concurrent scheduler duplicate enqueue, alert vocabulary drift, unbounded/concurrency-unsafe cleanup, stale tracking claims, permissive green/recovery proof, and label-only four-cycle acceptance.
* PR #682 includes #680 in its ancestry but changes 62 files and has unresolved mutation, boundedness, lease, cleanup, and entrypoint findings; green checks do not make it safe to merge wholesale.
* A durable `provider_unknown` or `manual_action_required` attempt can be acknowledged from the mutation queue to prevent unsafe replay while the worker still exits non-zero and a deduplicated reconciliation/operator path remains actionable.
* A week can be green after controlled recovery only when the earlier non-green attempts remain visible and the succeeding authorized attempt independently satisfies every exact-identity and provider-readback requirement.

### Unresolved Decisions or Blockers

* No planning decision is open. Exact recursive typed canonical validation now covers the authorization set, envelope, evidence, history, attempt/event records, successor expectations, and versions before digest/equality checks. Scalar substitutions, exponent-overflow/non-finite numeric forms, negative zero, coercions, unsupported containers, and duplicate JSON fields fail closed. Basher independently accepted exact reviewed head `905a890`, completing P07-T07. P00-T01/upstream deployment proof, P05 merge/provenance/deployment and exact-W39 production acceptance, P06 elapsed cycles, and operator-only #682 remain acceptance blockers. No second critique was run; the original critique and its dispositions remain historical evidence.

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
* Treat the exact real-W39 recovery execution as a mandatory post-deployment production acceptance gate. Correlate the actual GitHub dispatch/run, upstream publication identity and dispatch result, Podcaster accepted job/correlation IDs, Azure synth/recorder/video executions, immutable attempts, provider item identity, and authoritative terminal external readback.
* Report safe run/job/execution IDs and URLs, provider states and safe provider URLs, reconciliation decisions, and manual-action blockers. Never accept tests, CI, GitHub success, Azure internal success, queue completion, ACA exit 0, or weekly labels as substitutes for authoritative provider truth.
* Verify four future consecutive post-fix scheduled cycles; each must end `published_verified` or controlled `published_verified_recovered` with external provider readback. Every non-green state blocks acceptance.
* The exact W39 recovery run does not automatically count toward P06. It counts only if it independently satisfies the plan's future-cycle timing and scheduled-cycle criteria; otherwise four additional qualifying cycles remain required.
* Preserve the single existing critique as historical evidence and do not run a second critique for this authoritative post-implementation correction.
* Do not weaken CI/tests/provider safety/idempotency/security gates and do not modify `/home/azureuser/source/SquadScope`.
* Plan only the active review findings RV-002, RV-003, RV-004, RV-007, RV-008, and RV-009; do not reopen or add findings.
* Add a distinct review-follow-up phase with dependency-ordered tasks and closure evidence for every active finding.
* Preserve every historical rejected author/reviewer pair. For the current correction from review head `c59669405018f7fa7f9b470568d22e8e474d6f6c`, Leela is the sole author and Basher is the fresh independent reviewer. Basher is excluded from authoring, advice, pairing, or implementation contribution before final-SHA review. Scribe may log only after the lifecycle completes.
* Keep PR #684 draft/blocked while P00-T01, any P05 review/merge/provenance/deployment/exact-W39 gate, or P06 elapsed-cycle gates remain.

## Goals

* Prevent or promptly detect recurrence of the W39 pre-Azure dispatch blockage and make cross-boundary arrival evidence durable.
* Establish one durable terminal contract across upstream intent/dispatch, Azure arrival, enqueue, claim, provider mutation/readback, aggregation, worker exit, ACA execution, telemetry, and operations.
* Make redelivery and crash recovery safe through atomic intent creation, fenced claims, identity-bound reconciliation, durable receipts, and fail-closed ambiguity handling.
* Preserve #680 evidence guarantees while completing #681 and replacing only the safe, independently validated intent of #682.
* Preserve W38's complete attempt history and classify its weekly identity only from the exact available proof.
* Preserve every attempt as immutable evidence and derive weekly identity state without destructive collapse.
* Deliver a reversible rollout and sustained production proof based on external provider state.
* Close all six active review findings with deterministic negative probes, locked validation, truthful tracking, and fresh independent review.

## Scope and Non-Goals

### In Scope

* W39-class upstream weekly-publication intent, dispatch attempt/result, Azure API acceptance, first Azure-side durable arrival, missing-arrival detection, and cross-boundary correlation.
* Immutable artifact upload and integrity verification followed by one conditional authoritative outbox create, claim-time artifact revalidation, idempotent queue notification, and orphan repair.
* Claim ownership, lease expiry, heartbeat, attempt identity, monotonically increasing fencing token, CAS persistence, consumed mutation authorization, read-only takeover after any possibly issued mutation, bounded reconciliation scheduling, bounded retry, and poison/manual states.
* Sanitized correlation among canonical publication identity, enqueue, outbox item, execution attempt, provider operation, provider item/state, receipt, verification, and aggregate result.
* Attempt-level lifecycle/outcome records and weekly identity-level aggregation decisions with deterministic precedence and proof references.
* YouTube and Spotify reconcile-before-mutate state machines.
* Truthful aggregation, process exit, ACA status behavior, telemetry/alerts, tests/fault injection, feature-flagged deployment, exact real-W39 end-to-end production acceptance, W38 comparative partial-attempt observation, rollback, four-week verification, review, PR, and #682 supersession.
* P07 review-follow-up implementation for atomic scheduler claims, canonical telemetry vocabulary, bounded fenced cleanup, exact proof/recovery authorization, proof-backed four-cycle evaluation, and evidence reconciliation for PR #684.

### Non-Goals

* Wholesale merge or cherry-pick of PR #682.
* Assuming W38 is `published_verified_recovered` without exact identity/readback evidence, or deleting/overwriting its earlier failed attempts.
* Attributing W39 to Podcaster synthesis, recorder, video, outbox, or provider code when no W39 Azure execution exists.
* Claims of exactly-once provider mutation.
* Treating draft-only, private, unlisted, pending, unknown, manual handoff, skipped required work, or empty unexpected drains as production success.
* Unattended Spotify public mutation without an authoritative supported contract and immutable identity proof.
* Changes to `/home/azureuser/source/SquadScope`.
* Production implementation during this planning phase.
* Editing the historical review, changes record, critique, source, tests, docs, git state, GitHub, or deployment during this planning revision.

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
* Scheduler notification selection/enqueue is single-winner under concurrent workers and lease races.
  * Observable acceptance criteria: an atomic durable claim/reservation or enqueue idempotency key prevents duplicate enqueue; deterministic barriers prove two concurrent schedulers and stale-lease recovery cannot select/enqueue the same token twice.
* One canonical telemetry vocabulary governs emission and deployment.
  * Observable acceptance criteria: emitter constants/schema, generated alert queries, tests, and runbook consume the same event/state names; generation or validation fails on drift; active-depth missing-state semantics are exercised.
* Cleanup is bounded, resumable, fenced, and reference-safe.
  * Observable acceptance criteria: explicit page/item/time/work budgets cap each run; durable continuation resumes work; a generation/claim/fence or conditional reference check prevents deletion after a concurrent reference appears.
* Green and recovered-green decisions require persisted authoritative proof.
  * Observable acceptance criteria: omitted, mismatched, ambiguous, or label-only week/publication/manifest/digest/artifact/provider/readback/authorization evidence is non-green; failed and unknown attempts remain immutable and unknown mutation never grants blind retry.
* Four-cycle acceptance consumes authoritative persisted proof envelopes.
  * Observable acceptance criteria: every cycle is re-evaluated from proof receipts/readbacks and exact identity, not a stored green label; any missing, mismatched, ambiguous, manual, unknown, partial, or duplicate-unresolved evidence fails.
* Exact real-W39 production acceptance is dependency-ordered and reconcile-first.
  * Observable acceptance criteria: required Podcaster and upstream changes are reviewed and merged; exact merge-SHA-derived artifacts are proven deployed; all review/deployment gates are clear; every existing W39 intent, dispatch, attempt, receipt, job, and provider candidate is reconciled before mutation; any existing or ambiguous publication state fails closed to manual action; one safe execution is correlated from GitHub dispatch/run IDs through upstream dispatch result, Podcaster acceptance, Azure synth/recorder/video execution, immutable attempts, provider item identity, and authoritative terminal external readback.

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
* Deterministic concurrency and proof testing: negative probes use controlled barriers/fake clocks/CAS outcomes rather than timing sleeps or labels.
  * Observable acceptance criteria: repeated focused runs prove single-winner scheduler behavior, cleanup reference protection, fail-closed proof, and four-cycle rejection without flakiness.

## Acceptance Criteria

* Attempt records use explicit lifecycle/outcome states and remain immutable; weekly aggregation is stored separately and references the complete attempt set.
* After all required Podcaster and upstream fixes are reviewed/merged, exact merge-SHA artifacts are deployed, and all gates clear, the actual W39 publication is accepted upstream, dispatched, correlated to Azure API acceptance and first durable Azure arrival, executes through synth/recorder/video/provider stages, and reaches `published_verified` or controlled `published_verified_recovered`.
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
* The mandatory production acceptance run uses exact W39 identity, first reconciles all historical W39 attempts/intents/receipts/provider records, proves no existing or ambiguous provider publication before mutation, and ends `published_verified` or controlled `published_verified_recovered` with exact identity/manifest/digest/canonical-artifact proof and authoritative external provider readback. Ambiguity requires fail-closed/manual action, never duplicate publication.
* Acceptance evidence safely records GitHub dispatch/run IDs and URLs, upstream publication identity and dispatch result, Podcaster job/correlation IDs, Azure synth/recorder/video job or execution IDs, immutable attempt IDs, provider item identity/state and safe provider URLs, reconciliation decisions, and manual-action blockers.
* Tests, CI, GitHub workflow success, Azure internal success, queue completion, ACA exit 0, and weekly labels are explicitly insufficient without the exact external proof chain.
* Four future consecutive post-fix scheduled cycles each end `published_verified` or controlled `published_verified_recovered`. Any non-green state blocks acceptance and restarts the gate after correction.
* The exact W39 production acceptance run does not automatically satisfy any P06 cycle; it counts only if it independently meets P06's future scheduled-cycle timing and evidence criteria. The four-cycle requirement is never weakened.
* W38 is recorded as `published_verified_recovered` only if the exact proof contract is met; otherwise it remains an explicitly unproven candidate. The historical W39 incident remains `missed_not_dispatched` until later recovery-execution evidence exists, and that recovery is recorded separately without rewriting the incident record.
* RV-002, RV-003, RV-004, RV-007, RV-008, and RV-009 each have P07 closure evidence linking implementation, deterministic negative probes, validation output, and the designated independent review disposition.
* Plan, details, changes delivery update, review status summary, and PR #684 report identical active/resolved counts without altering the historical review conclusions.
* P00-T01 is complete through merged upstream PR #773. P07 completion does not satisfy P05-T03–P05-T06 or P06; PR #684 remains draft/blocked while any residual gate is open.

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
| Planning execution and readiness | Exactly one critique remains historical; Basher's surgical RV-008 provider-item correction and locked validation are complete, and Ralph's final-SHA review rejected the receiptless authorization path |
| Continuation context | Require exact durable provider receipts for latest-unknown resolution, revalidate, obtain a new independent final-SHA review, and retain P00-T01/P05/P06 as explicit residual gates |

## Implementation Status

* Execution status: Leela's sole-author RV-008 typed-canonical correction is complete and fully validated; Basher independently accepted exact reviewed head `905a890c6a2176c799a5cd5f54fbb01d4c791aa9`
* Declared scope: P07-T01–P07-T07 only, followed by residual P00-T01, P05, and P06 gates
* Revision author: Leela only for the current correction
* Fresh independent reviewer: Basher completed a read-only review without contributing
* Excluded contributors: Bender, Hermes, Amy, Farnsworth, Rusty, Ralph, Livingston, and Frank did not author, advise, pair, or contribute. Basher was excluded from authoring/advice/implementation contribution and completed the independent final-SHA review. Leela is the correction author, not a reviewer, for this cycle.
* Delivery restrictions: commit and push only the existing branch and update existing draft PR #684; no deployment, issue mutation, replacement branch/PR, or changes to `/home/azureuser/source/SquadScope`
* Active implementation boundary: P07 review-follow-up defects only; P00-T01, P05, and P06 remain outside P07 and block final acceptance
* Approved implementation write boundary: this worktree's downstream source, tests, infrastructure, workflows, operator documentation, and RPI tracking artifacts only; do not modify `/home/azureuser/source/SquadScope`, git state, GitHub, PR text, issue threads, deployment, or production
* Validation intent: deterministic P07 negative probes, locked owner suites, full suite, Ruff check/format, compileall, Bicep, Checkov, diff check, container build, and applicable container/exit smoke without weakening
* Current blockers: P00-T01 upstream prevention and exact deployed-artifact evidence, P05 delivery/merge/deployment/provider authority plus P05-T06 exact-W39 execution, P06 four elapsed production cycles, and operator-only #682 remain blockers; P07-T07 is complete

## Sources

* `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`: completed C1–C20/W1–W30 evidence, safe direction, constraints, #682 inventory, and validation/deployment baseline.
* `jmservera/SquadScope-Podcaster#681`: required atomic/fenced outbox boundary.
* `jmservera/SquadScope-Podcaster#680`: merged canonical publication identity, evidence, and reconciliation baseline.
* `jmservera/SquadScope-Podcaster#682`: replacement/supersession target with W17–W29, six RV-006, and later current unresolved safety threads.
* `.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md`: Rusty's current disposition is RV-008 High open for exact latest-unknown provider-item readback binding; RV-002/RV-003/RV-004/RV-007/RV-009 remain resolved; RV-001/RV-005 remain resolved; and RV-006 remains resolved at planning level.
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

<!-- rpi:phase id=P07 -->
### [x] P07: Review-follow-up closure for terminal truth

* Intent: Resolve only RV-002, RV-003, RV-004, RV-007, RV-008, and RV-009 with fail-closed implementation, deterministic negative probes, truthful delivery evidence, and fresh independent review.
* Dependencies: canonical review complete; historical P01–P04 implementation available as the correction baseline.
* Ownership: Leela alone authored the current correction from review head `c596694`; Basher independently reviewed exact head `905a890` without contributing. Every prior author/reviewer cycle remains immutable historical evidence.

<!-- rpi:task id=P07-T01 -->
#### [x] P07-T01: Enforce exact proof and durable recovery authorization

* Finding: RV-008.
* Expected result: green requires exact week, publication, manifest, publication digest, canonical artifact digest/selection, expected provider identity, authoritative terminal readback source/state, and explicit duplicate resolution. Recovery authorization is durable, evidence-referenced, bound to the complete ordered attempt history/latest relevant state, and cannot arise from an unresolved unknown mutation or caller boolean alone.
* Closure evidence required: label-only/omitted/mismatched/ambiguous proof probes are non-green; stale predecessor and omitted/reordered-history probes fail closed; a later unknown leaves reconciliation read-only. Authorization must retain and recompute a versioned canonical, typed, exact ordered representation of every relevant durable attempt and event through a precise predecessor boundary, not only prior attempt IDs. The binding includes attempt identity/order/status/classification, mutation possibility, event type/state/sequence/time/owner/execution/fence/lease, provider intent/receipt/evidence/readback identity and references, week/publication/manifest/digests/artifact, recovery linkage/authorization, and record version. Duplicate IDs/events, missing or ambiguous sequence/time, any semantic mutation, insertion/omission/reorder, cross-week replay, unexpected post-authorization append, legacy/unknown schema, and successor mismatch fail closed. Exactly the authorized successor transition may extend the bound history.
* Fry disposition: predecessor attempts/events and the expected successor satisfy this contract, but the containing `recovery_authz` record does not. Mutated `source`, `reason`, or `authorized_at`, an extra field, a duplicate matching authorization, or an unrelated additional authorization record leaves the successor mutation-capable. Completion additionally requires exactly one fully recomputed authorization envelope whose metadata and evidence digest agree with the successor.
* Fry correction evidence: v4 exact-envelope and v1 set-manifest recomputation denies all six bypasses, every field removal/type/null mutation, unknown/legacy versions, extension or unknown-field mutation, duplicate/reordered/extra/conflicting collections, and post-claim tampering. Exact unchanged evidence is accepted once; concurrent authorization and claim each have one winner; reuse is read-only.

<!-- rpi:task id=P07-T02 -->
#### [x] P07-T02: Make scheduler notification enqueue single-winner

* Finding: RV-002.
* Dependencies: P07-T01 schema compatibility.
* Expected result: use an atomic durable reservation/claim or broker idempotency key before enqueue, fenced against stale leases, with bounded recovery of abandoned reservations.
* Closure evidence: deterministic two-scheduler barriers prove one enqueue winner; stale worker completion is rejected; lease expiry/recovery cannot duplicate a notification.

<!-- rpi:task id=P07-T03 -->
#### [x] P07-T03: Bound and fence resumable cleanup

* Finding: RV-004.
* Dependencies: P07-T01 identity/reference invariants.
* Expected result: every run has explicit page, item, elapsed-time, and work budgets; durable continuation resumes; deletion uses a generation/claim/fence or atomic conditional reference check so a concurrent enqueue/reference wins over deletion.
* Closure evidence: scale probes prove bounded memory/work and forward progress; crash/restart resumes; a deterministic concurrent-reference probe preserves the newly referenced artifact.

<!-- rpi:task id=P07-T04 -->
#### [x] P07-T04: Unify emitted telemetry and deployable alert vocabulary

* Finding: RV-003.
* Expected result: one canonical vocabulary/schema is consumed by emitters, generated Bicep query inputs, rule tests, and runbook examples; drift validation fails generation/tests. Active-depth detection implements documented missing-state semantics.
* Closure evidence: representative emitted rows satisfy generated queries; renamed/mismatched event probes fail validation; fire/clear and missing-data cases pass for every affected rule.

<!-- rpi:task id=P07-T05 -->
#### [x] P07-T05: Require authoritative proof for four-cycle acceptance

* Finding: RV-009.
* Dependencies: P07-T01.
* Expected result: the evaluator loads and validates persisted cycle proof receipts/readbacks, complete attempt sets, exact identities, provider objectives/items/states, duplicate resolution, and aggregation proof instead of trusting weekly labels.
* Closure evidence: a parameterized negative matrix rejects label-only, missing, mismatched, ambiguous, partial, unknown, manual, identity-conflict, duplicate-unresolved, and no-readback cycles; exactly four consecutive fully proven cycles are required.

<!-- rpi:task id=P07-T06 -->
#### [x] P07-T06: Run the locked review-follow-up validation contract

* Findings: RV-002, RV-003, RV-004, RV-008, RV-009.
* Dependencies: P07-T01–P07-T05.
* Expected result: focused negative probes, locked owner suites, full suite, compileall, Ruff check/format, Bicep, Checkov, diff check, container build, and applicable entrypoint/container exit smoke all pass without removals, skips, weakened assertions, or non-blocking gates.
* Closure evidence: exact commands, counts, exit statuses, SHA/image digest where applicable, and any generated-artifact cleanup are recorded in the implementation delivery update.

<!-- rpi:task id=P07-T07 -->
#### [x] P07-T07: Reconcile delivery evidence and obtain a fresh independent review

* Finding: RV-007 and cross-finding closure.
* Dependencies: P07-T06.
* Expected result: plan/details remain current; delivery records Leela's correction SHA, exact counts, validation commands, and residual gates without rewriting any historical rejection cycle.
* Closure evidence: Leela's historical rejection remains recorded against exact head `273f94e0d1fa773e108661f908aca6f34be132c4`; Basher independently accepted corrected exact head `905a890c6a2176c799a5cd5f54fbb01d4c791aa9` with no blocking findings. The existing branch remains open/draft/blocked by P00-T01, P05, P06, and operator-only #682. All prior rejection cycles remain immutable historical evidence.

<!-- rpi:phase id=P05 -->
### [ ] P05: Deliver reviewed, reversible implementation and exact W39 production acceptance

* Intent: Push coordinated upstream/Podcaster PRs as required, preserve Fry's independent P07 review or re-review any changed final SHA, merge without drift, establish merge-SHA artifact provenance, deploy reversibly, then execute the exact real-W39 recovery only after every prerequisite gate is clear.
* Dependencies: P07 complete and P00-T01 resolved; P04 is historical baseline evidence only.

<!-- rpi:task id=P05-T01 -->
#### [ ] P05-T01: Push branch and open the implementation PR

* Requirement and evidence: caller requirement 9.
* Expected result: branch is pushed; PR explains immutable attempt versus weekly identity truth, gives W38's evidence-conditional classification rule and W39's `missed_not_dispatched` status, includes current RV-002/RV-003/RV-004/RV-007/RV-008/RV-009 dispositions, validation evidence, deployment/canary/rollback, Coordinator #17, the coordinated upstream PR, and explicit `Supersedes #682`.
* Detail section: P05-T01 in phase details.

<!-- rpi:task id=P05-T02 -->
#### [ ] P05-T02: Pass required checks and independent final-SHA review

* Requirement and evidence: caller requirement 8 and repository required checks.
* Expected result: required PR checks pass; the designated fresh independent reviewer assesses each final pushed SHA for dispatch correctness, concurrency/idempotency, provider safety, evidence security, operations, and tests; no critical finding is accepted or unresolved, and any post-review content change triggers revalidation/re-review. Bender, Hermes, and Amy remain excluded from implementation.
* Detail section: P05-T02 in phase details.

<!-- rpi:task id=P05-T03 -->
#### [ ] P05-T03: Resolve or supersede every PR #682 safety thread

* Requirement and evidence: W16–W29.
* Expected result: W17–W29, the six RV-006 rows, and later current unresolved rows each have code/test or non-port evidence, a reviewer-facing reply, and actual resolved/closed state; final-media evidence requires structural metadata plus successful complete bounded decode before atomic promotion. #682 is closed once replacement linkage and fresh Basher acceptance are durable. No row is pre-marked resolved.
* Detail section: P05-T03 in phase details.

<!-- rpi:task id=P05-T04 -->
#### [ ] P05-T04: Approve, merge, and prove release image provenance

* Requirement and evidence: C18; critique PC-005.
* Expected result: PR approval and required checks gate merge; merge introduces no content drift; release workflow builds/identifies an image derived from the merge SHA; reviewed SHA, merged SHA, workflow run, digest, and equality/provenance checks are durable.
* Detail section: P05-T04 in phase details.

<!-- rpi:task id=P05-T05 -->
#### [ ] P05-T05: Deploy exact merge-SHA artifacts and clear rollout gates

* Requirement and evidence: C18; research constraints 14–15.
* Expected result: exact merge-SHA-derived upstream and Podcaster artifacts are deployed with provenance; readiness, migration, alert fire/clear, rollback, credentials/authority, and deployment gates are complete. Provider-disabled or non-mutating readiness probes may run, but no generic canary, internal green signal, or weekly label satisfies production acceptance.
* Detail section: P05-T05 in phase details.

<!-- rpi:task id=P05-T06 -->
#### [ ] P05-T06: Execute and verify the exact real-W39 production recovery

* Requirement and evidence: authoritative acceptance directive dated 2026-09-22.
* Expected result: only after P05-T01–P05-T05 and upstream delivery gates are complete, reconcile every existing W39 intent/attempt/receipt/job/provider candidate; prove no existing or ambiguous provider publication; fail closed/manual-action rather than duplicate; dispatch exact W39 through GitHub, upstream identity/dispatch, Podcaster acceptance, Azure synth/recorder/video, immutable attempts, provider identity, and terminal external readback. Record safe IDs/URLs, states, reconciliation decisions, and blockers.
* Detail section: P05-T06 in phase details.

<!-- rpi:phase id=P06 -->
### [ ] P06: Verify four consecutive post-fix production cycles

* Intent: Prove sustained scheduled dispatch, Azure execution correlation, and external provider terminal truth.
* Dependencies: P05-T06 exact W39 production acceptance complete and production routing approved.

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
* P07-T01–P07-T05 precede P07-T06; P07-T07 follows locked validation.
* P07, required Podcaster/#682 dispositions, and upstream fixes must be reviewed and merged before P05 deployment. P07 completion alone does not unblock final delivery.
* P05 required checks and Fry's final-pushed-SHA review must have no accepted critical finding before approval/merge.
* Production deploy uses only upstream and Podcaster artifacts proven to derive from their exact merge SHAs; all review/deployment gates must clear before P05-T06.
* P05-T06 follows P05-T05 and is the mandatory exact real-W39 production acceptance run. It is reconcile-first and may not mutate while provider state is existing, conflicting, or ambiguous.
* P06 requires accepted exact-W39 production evidence from P05-T06 and remains open until four future consecutive post-fix cycles each have complete green proof. W39 does not count automatically.

## Implementation Marker Reconciliation

| Marker(s) | Revised disposition | Required next evidence |
|---|---|---|
| P00-T01 | Complete through `jmservera/SquadScope#773` | Reviewed head `d75e3f5523f4810edbcaeef9a217d34cd21825a2` merged as `7a6d8811bf82507cbdd0b01ba1135bc42e5942f3`; all 18 PR checks and merge-SHA CI/security/release/deploy workflows succeeded; canonical identity, append-only trusted receipts, missing/terminal monitoring, and fail-closed weekly evidence preserve W39 as `missed_not_dispatched` |
| P07-T01 | Complete for Leela correction | Exact recursive typed canonical contracts bind `distribution-recovery-authz-v4`, `distribution-recovery-authz-set-v1`, evidence/history, attempt/events, and successor expectations; all scalar/type/numeric/duplicate-key probes fail closed |
| P07-T02 | Complete; RV-002 resolved | Expired `reserved` and `enqueue_started` leases become due; CAS fencing preserves a single current owner and rejects stale completion/release |
| P07-T03 | Complete; RV-004 resolved | Cleanup completes a bounded resumable pre-index outbox migration before deletion; incomplete scans fail closed and current references retain CAS priority |
| P07-T04 | Complete; RV-003 resolved | Canonical emitted/query vocabulary and active-depth absence semantics independently passed |
| P07-T05 | Complete; RV-009 resolved | Stored proof booleans are rebound to raw exact evidence and the current weekly record; label-only, tampered, mismatched, and unauthorized recovered cycles fail closed |
| P07-T06 | Complete for Leela correction | Focused `241`, locked `916`, full `3246`, static/infra/Checkov/container/exit/security gates passed without weakening |
| P07-T07; P05-T01 | Complete | Basher independently accepted exact executable head `905a890`; exact final drift through `da84b6b` is tracking-only; PR #684 now links upstream #773 and preserves W38/W39 and exact-W39 requirements |
| P05-T02 | Complete | No executable drift after `9204e139`; final-SHA review found no High/Critical issue; all 13 hosted checks on the final pushed tracking head succeeded |
| P05-T03 | Frank implementation and validation complete; Rusty acceptance pending | All 67 #682 threads remain resolved with durable dispositions. Complete decode remains lifecycle-bounded, and one persisted fenced execution claim now guards promotion, archive, outbox, notification, direct-provider intent/mutation, and terminal success. Exact `13`, focused `416`, locked `940`, full `3302`, and all delivery gates passed; commit/push evidence and Rusty's independent final-SHA acceptance remain required. |
| P05-T05 | Expanded prerequisite gate | Deploy exact reviewed/merged upstream and Podcaster artifacts with merge-SHA provenance; clear review, deployment, readiness, alert, authority, and rollback gates before any real W39 mutation |
| P05-T06 | New authoritative acceptance gate | Reconcile all W39 history/provider candidates, then execute exact W39 with complete GitHub→upstream→Podcaster→Azure→provider correlation and authoritative external readback; ambiguity fails closed/manual-action |
| P06-T01–P06-T02 | Preserved without credit reduction | Four future qualifying cycles remain required; P05-T06 W39 counts only if it independently meets future scheduled-cycle timing and proof criteria |
| P01-T04, P02-T01–P02-T02 | Implemented surfaces; dependency verification | Preserve safe behavior unless the new receipt/attempt schema requires minimal compatible updates |
| Existing W38 references in plan/PR handoff | Evidence-conditional correction | Use `published_verified_recovered` only with full exact proof; otherwise label it an allowed candidate and retain all attempt history |
| W39 classification | Settled | `missed_not_dispatched`; no downstream attempt may be fabricated |
| Original PC-001–PC-009 dispositions | Historical, no change | Preserve existing critique artifact and disposition record; no second critique |
| P05/P06 | Residual gates | PR #684 stays draft/blocked pending exact merge-SHA deployment, reconcile-first exact-W39 production acceptance, and four fully proven elapsed cycles |

Leela alone authored the current P07-T01 correction. Basher completed the fresh independent review after implementation/validation without contributing. Bender, Hermes, Amy, Farnsworth, Rusty, Ralph, Livingston, Frank, and Fry were excluded from authoring, advice, pairing, or contribution.

## Review-Follow-Up Finding Map

| Finding | P07 task | Required closure evidence |
|---|---|---|
| RV-002 High | P07-T02, P07-T06, P07-T07 | Resolved by atomic durable scheduler reservation, deterministic concurrent-worker/lease-race probes, and Livingston disposition |
| RV-003 High | P07-T04, P07-T06, P07-T07 | Remains resolved by canonical emitted/query/test/runbook vocabulary and representative query fire/clear proof |
| RV-004 High | P07-T03, P07-T06, P07-T07 | Explicit run/page/item/time budgets, durable legacy-reference migration, fenced/conditional deletion, concurrent-reference negative probe; validation and Livingston disposition |
| RV-007 Medium | P07-T07, P05-T01 | Exact current counts/statuses/commands in delivery update and PR #684; historical review unchanged; residual gates explicit |
| RV-008 High | P07-T01, P07-T06, P07-T07 | Preserve complete ordered attempt/event and successor binding; bind the complete selected authorization record and enforce exactly one matching authorization so metadata mutation, extra fields, duplicates, and unrelated records fail closed |
| RV-009 High | P07-T05, P07-T06, P07-T07 | Resolved by authoritative proof-envelope reload/rebinding, label-only and incomplete proof rejection, and Livingston disposition |

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
* P07-T01/P07-T05 own the existing outbox/publication-state/dispatch-receipt proof owners; no parallel green-state model or label-only evaluator test owner may be introduced.
* P07-T02 owns the existing scheduler/outbox suites and deterministic concurrency fixtures.
* P07-T03 owns the existing outbox/storage cleanup suites and bounded-work/concurrent-reference fixtures.
* P07-T04 owns the existing telemetry/deploy-workflow owners plus canonical generated vocabulary/query assertions.
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
* Fault coverage includes failed attempt followed by evidence-authorized success, unknown mutation followed by prohibited blind retry, omitted/mismatched week/publication/manifest/digest/artifact/provider/readback proof, duplicate provider candidates, missing terminal readback, out-of-order receipts, two concurrent scheduler workers, lease-race reservation recovery, bounded cleanup restart, concurrent reference creation, telemetry vocabulary drift, generated-query mismatch, and label-only four-cycle rows.
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
git diff --check
```

* Add new outbox/telemetry/canary test paths to the targeted command when created.
* Run the repository's applicable container/entrypoint exit smoke and prove non-green/missing-required-configuration exits non-zero while a fully proven fixture exits according to contract.
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

## PR #682 Thread Closure Matrix — Requirements

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

### P05-T03 authoritative disposition ledger

All 67 threads were inspected through GitHub GraphQL on 2026-09-22. GitHub reported
`67 total / 67 resolved / 0 unresolved` before replies. Replies were added only to the 17
threads whose final comment was still a reviewer finding; no thread resolution state was
changed. Replacement evidence is anchored to PR
[#684](https://github.com/jmservera/SquadScope-Podcaster/pull/684) and accepted executable
boundary `9204e139be485cb916ccd6e70b6fce355b656136`.

| Requirement thread | Thread ID / durable author reply | #684 disposition and evidence | Actual GitHub state |
|---|---|---|---|
| W17 / [r4018616332](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018616332) | `PRRT_kwDOSzuis86iosWy`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066782891) | Fixed by bounded, reference-safe cleanup in `podcaster/storage.py` / `podcaster/distribution_outbox.py`; `test_cleanup_pages_complete_references_before_deleting`, `test_cleanup_reference_created_before_delete_prevents_removal`, and `test_cleanup_clips_removes_only_clip_prefix`. | Resolved |
| W18 / [r4018616414](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018616414) | `PRRT_kwDOSzuis86iosXi`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066783076) | #682's stage architecture was not ported; retained `edl_render` removes failed partial output, covered by `test_render_failure_removes_partial_output`. | Resolved |
| W19 / [r4018616473](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018616473) | `PRRT_kwDOSzuis86iosYL`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066783181) | #682's validated-download wrapper was not ported; retained checkpoint failure is non-terminal recomputation and unverified uploads are deleted, covered by `TestVerifiedUpload::test_upload_rejects_size_mismatch_and_drops_blob`. | Resolved |
| W20 / [r4018903541](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903541) | `PRRT_kwDOSzuis86ipakb`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066783308) | #682's 840-second recorder lifecycle was not ported. #684 decouples provider delivery into its own queue/lease budget; deployment invariants remain covered by `tests/test_deploy_workflow.py`. | Resolved |
| W21 / [r4018903592](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903592) | `PRRT_kwDOSzuis86ipak9`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066783410) | Fixed by immutable artifact commit plus durable outbox enqueue before video completion; covered by `test_schema_round_trip_is_versioned_correlated_and_sanitized` and worker queue tests. | Resolved |
| W22 / [r4018903639](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903639) | `PRRT_kwDOSzuis86ipalc`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066783561) | Fixed by claim/fence before provider intent or mutation; covered by `test_concurrent_claim_has_one_owner_and_takeover_increments_fence` and `test_consumed_intent_makes_takeover_read_only_and_rejects_second_mutation`. | Resolved |
| W23 / [r4018903679](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903679) | `PRRT_kwDOSzuis86ipal6`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066783722) | #682's subprocess stage wrapper was not ported; #684 adds no equivalent unbounded post-SIGKILL `communicate` path. Existing process tests remain the regression boundary. | Resolved |
| W24 / [r4018903724](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903724) | `PRRT_kwDOSzuis86ipamb`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066783846) | Outdated for the retained implementation: checkpoint resume remains optional and browser-free when complete; covered by `test_partial_resume_records_only_missing` and `test_resumes_from_composed_checkpoint`. | Resolved, outdated |
| W25 / [r4018903793](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903793) | `PRRT_kwDOSzuis86ipanQ`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066785011) | Fixed by intent-before-I/O, ambiguous receipt persistence, reconciliation-only takeover, and no blind retry; covered by `test_ambiguous_youtube_create_is_durable_unknown_without_retry` and `test_crash_after_mutation_before_receipt_preserves_consumed_intent`. | Resolved |
| W26 / [r4018903838](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903838) | `PRRT_kwDOSzuis86ipany`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066785113) | #682's remaining-budget fan-in wrapper was not ported. The retained barrier is a per-index sentinel check with a monotonic overall timeout; covered by `test_wait_for_fanin_times_out_with_partial`. | Resolved |
| W27 / [r4018903866](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903866) | `PRRT_kwDOSzuis86ipaoD`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066785212) | #682's resume terminal wrapper was not ported. #684 cleanup is reference-safe and provider work is independently terminalized through the outbox; cleanup tests prevent deletion of live references. | Resolved, outdated |
| W28 / [r4018903912](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903912) | `PRRT_kwDOSzuis86ipaoh`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066785331) | #682's recorder deadline/finalization architecture was not ported. Retained terminal manifests are conditional and never overwrite a winner; covered by `test_write_fallback_manifest_is_terminal_and_never_overwrites`. | Resolved, outdated |
| W29 / [r4018903953](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4018903953) | `PRRT_kwDOSzuis86ipao6`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066785440) | Fixed by one-message recorder and one-message distribution workers; covered by `test_process_message_records_and_deletes` and distribution worker message tests. | Resolved |
| RV-006 / [r4066838845](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066838845) | `PRRT_kwDOSzuis86kiVIr`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067041949) | Fixed by the shared outbox claim/fence path before every provider mutation; concurrent claim and consumed-intent takeover tests pass. | Resolved |
| RV-006 / [r4066838898](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066838898) | `PRRT_kwDOSzuis86kiVJS`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067042097) | #682's playlist admission wrapper was not ported. The replacement worker does not perform playlist mutation and records provider failures durably. | Resolved |
| RV-006 / [r4066838929](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066838929) | `PRRT_kwDOSzuis86kiVJk`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067042207) | #682's 840-second contract was not ported. #684 uses a dedicated distribution queue/lease budget and the current deployment/runbook assertions. | Resolved, outdated |
| RV-006 / [r4066838959](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066838959) | `PRRT_kwDOSzuis86kiVJ3`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067042346) | #682's fallback stage runner was not ported. Retained recorder transient failures remain queued and poison fallback is a conditional terminal manifest; recorder tests cover both outcomes. | Resolved, outdated |
| RV-006 / [r4066838991](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066838991) | `PRRT_kwDOSzuis86kiVKQ`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067042495) | Superseded by immutable artifact commit before outbox enqueue and a durable claim/intent before provider I/O; artifact-tamper and consumed-intent tests pass. | Resolved, outdated |
| RV-006 / [r4066839027](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066839027) | `PRRT_kwDOSzuis86kiVKp`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067042670) | #682's stage-budget hashing wrapper was not ported. #684 binds the immutable artifact size/digest before enqueue and revalidates it at claim; `test_artifact_claim_revalidation_detects_tamper` passes. | Resolved, outdated |
| Current / [r4066949442](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066949442) | `PRRT_kwDOSzuis86kim8j`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067136178) | Retained manifest ownership validation and content-addressed paths are covered by editor/recorder tests; #682's alternate stage architecture was not ported. | Resolved, outdated |
| Current / [r4066949487](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066949487) | `PRRT_kwDOSzuis86kim9A`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067153226) | Retained recorder transient exceptions leave the message for redelivery; only poison writes fallback and deletes. Covered by `test_process_message_transient_error_leaves_message`. | Resolved, outdated |
| Current / [r4066949518](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066949518) | `PRRT_kwDOSzuis86kim9Q`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067136177) | #682's composed-checkpoint identity extension was not ported. Retained plan annotation preserves `source_url` and `removed_reason`; complete checkpoint resume uses one shared finalizer. | Resolved |
| Current / [r4066949564](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4066949564) | `PRRT_kwDOSzuis86kim9u`; [author disposition](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4067136179) | #682's validated-download destination replacement was not ported. Retained checkpoint failures trigger recomputation and unverified uploads are removed rather than accepted. | Resolved |

### Previously undispositioned final reviewer findings

| Thread ID / finding | New #684 evidence reply | Disposition | Actual GitHub state |
|---|---|---|---|
| `PRRT_kwDOSzuis86kjgMz` / r4067314166 | [corrected evidence r4073299493](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4073299493) | Fixed at `d6e85ef`: exhausted chunk transport/transient-status uncertainty is mutation-ambiguous, durable `provider_unknown`, and redelivery is reconcile-only; deterministic pre-mutation rejection remains failed. | Resolved; acceptance pending Rusty |
| `PRRT_kwDOSzuis86kjsxQ` / r4067399102 | [r4072863344](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072863344) | Playlist mutation intentionally not ported to the replacement worker. | Resolved, outdated |
| `PRRT_kwDOSzuis86kjsxn` / r4067399134 | [final addendum r4073532947](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4073532947) | Fixed through `b7e3615`: missing, throwing, null, incorrect, or non-integer size probes reject the checkpoint and retain the local source for recomputation; exact integer size succeeds. | Resolved; acceptance pending Rusty |
| `PRRT_kwDOSzuis86kjsx7` / r4067399162 | [r4072863767](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072863767) | Budget overload not ported; production builder requires drawtext-capable ffmpeg. | Resolved, outdated |
| `PRRT_kwDOSzuis86kjsyP` / r4067399193 | [r4072863985](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072863985) | FANIN budget model not ported; sidecar failure causes recomputation. | Resolved |
| `PRRT_kwDOSzuis86kjsym` / r4067399216 | [r4072864217](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072864217) | Superseded by UUID output paths and checkpoint-after-success. | Resolved |
| `PRRT_kwDOSzuis86kpTkQ` / r4069614166 | [r4072864494](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072864494) | Persisted stage budget not ported; outbox takeover is reconciliation-only. | Resolved |
| `PRRT_kwDOSzuis86kpTkt` / r4069614211 | [r4072864723](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072864723) | Budgeted injected-runner branch not ported; production runner raises on non-zero. | Resolved, outdated |
| `PRRT_kwDOSzuis86kpfBJ` / r4069683474 | [r4072864920](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072864920) | Fixed: transient PUT exhaustion is durable `publication_unknown`, not blind retry. | Resolved |
| `PRRT_kwDOSzuis86kpfBi` / r4069683508 | [r4072865106](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072865106) | Duration-sidecar validator not ported; artifact identity/readback is authoritative. | Resolved, outdated |
| `PRRT_kwDOSzuis86kpfBw` / r4069683528 | [r4072865276](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072865276) | Fallback redesigned without a zero-timeout branch. | Resolved, outdated |
| `PRRT_kwDOSzuis86kpfCH` / r4069683559 | [final addendum r4073533159](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4073533159) | Fixed through `b7e3615`: shared finalization stages output, requires finite positive-duration video and requested audio via ffprobe, atomically promotes only valid media, preserves an existing destination, and cleans failed staging. | Resolved; acceptance pending Rusty |
| `PRRT_kwDOSzuis86kpfCY` / r4069683589 | [r4072865681](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072865681) | Fixed: transient init status is mutation-ambiguous and non-retryable. | Resolved |
| `PRRT_kwDOSzuis86kprDp` / r4069757485 | [r4072865926](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072865926) | Budget early-append path not ported; retained pass preserves annotations/order. | Resolved |
| `PRRT_kwDOSzuis86kscwn` / r4070842241 | [r4072866127](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072866127) | Allowed-version/destructive recovery path not ported; no fix overclaimed. | Resolved |
| `PRRT_kwDOSzuis86ksoS6` / r4070913130 | [r4072866389](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072866389) | Deadline suffix-slice path not ported; retained pass processes each source once. | Resolved, outdated |
| `PRRT_kwDOSzuis86ktgD2` / r4071255754 | [r4072866652](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4072866652) | Playlist mutation intentionally not ported to replacement worker; legacy parser not claimed fixed. | Resolved, outdated |

P05-T03 recommendation: **complete**. No #682 thread remains without a durable author or
replacement disposition, and no thread is currently unresolved. PR #682 should **not yet be
closed**: operator closure as superseded requires #684 to remain unchanged at the accepted
executable boundary, receive final acceptance, become merge-ready, and retain green required
checks. P05-T04–P05-T06 and P06 remain separate future gates; closing #682 does not authorize
merge, deployment, provider mutation, W39 execution, or four-cycle acceptance.

## Critique Disposition

The existing critique is preserved unchanged as historical evidence. No second critique was run or requested. PC-001–PC-009 retain their recorded dispositions. The later canonical review independently opened RV-002, RV-003, RV-004, RV-007, RV-008, and RV-009; P07 plans their closure without rewriting the critique or historical review conclusions.

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
* Review-follow-up implementation marker range: P07-T01 through P07-T07.
* Current author/reviewer: Leela is the sole correction author from review head `c59669405018f7fa7f9b470568d22e8e474d6f6c`; Basher independently accepted exact reviewed head `905a890c6a2176c799a5cd5f54fbb01d4c791aa9` without contributing. All prior author/reviewer cycles remain historical evidence.
* Current finding state: RV-001/RV-002/RV-003/RV-004/RV-005/RV-007/RV-008/RV-009 are resolved; RV-006 remains planning-resolved.
* Remaining blockers after revision: proof that all required upstream and Podcaster fixes are reviewed/merged; exact merge-SHA artifact deployment and cleared review/deployment gates; P05-T06 reconcile-first exact W39 production execution with authoritative external provider readback; P06 four elapsed future post-fix cycles; and operator-only #682. Basher independently accepted exact reviewed implementation head `905a890`, so P07-T07 is complete. PR #684 remains draft/blocked while any remaining gate is open.

## 2026-09-22 Independent Amy Terminal-Truth Correction

This user-directed reconciliation supersedes the stale author exclusions above only for the exact
revision based on remote head `19706f4b7bffe1375d6ccf3ef25d7a39454bd314`. Bender and Leela are
locked out from authoring, advice, pairing, or co-authorship. The branch remains draft and stacked;
production rollout, provider mutation, merge, and ready-for-review promotion remain prohibited.
The reconciliation must preserve the complete downstream ownership permit, fixed lifecycle expiry,
takeover, archive, outbox, notification, direct-provider, and terminal-write fences already present
at the approved base.

<!-- rpi:phase id=P08 -->
### P08: Close provider approval, RSS, reconcile, and playlist findings

* [x] <!-- rpi:task id=P08-T01 --> Persist publication-bound human approval and gate YouTube
  playlist insertion/public promotion without accepting automated identities.
* [x] <!-- rpi:task id=P08-T02 --> Add a fenced Spotify RSS provider leg using an immutable public
  locator and require external media hash/size plus feed-content readback.
* [x] <!-- rpi:task id=P08-T03 --> Make Spotify video reconcile-before-create mandatory regardless
  of `PODCASTER_SPOTIFY_RECONCILE`, retain `uploadType=default`, protected IDs, media separation,
  approval/live gates, and fail-closed ambiguity.
* [x] <!-- rpi:task id=P08-T04 --> Reconcile YouTube by deterministic outbox marker before first
  create intent and retain consumed-intent no-repeat behavior.
* [x] <!-- rpi:task id=P08-T05 --> Require fenced, idempotent, externally read-back playlist
  membership before terminal YouTube public success.
* [x] <!-- rpi:task id=P08-T06 --> Preserve bounded poison exhaustion, sanitized evidence, safe
  malformed discard, and current queue/editor/job-runner lifecycle ownership behavior.
* [ ] <!-- rpi:task id=P08-T07 --> Complete full validation, final remote-SHA gate, commit, normal
  push, and hosted draft/stack/check verification.

P08-T01 through P08-T06 have source and focused/affected-suite evidence. P08-T07 remains the only
active implementation marker; its first boundary is intent-level reconciliation of Amy's source
commit onto the ownership-fenced base before rerunning all provider and ownership validation.

## 2026-09-22 Hermes Final Blocking Correction

This user-directed correction starts from exact remote head
`ddaada1e8ca9d9203cd258f314be52dfee721898`. Bender, Leela, and Amy are locked
out from contribution. Existing provider terminal truth, approval, lifecycle,
ownership, and idempotent reconciliation behavior remains authoritative.

<!-- rpi:phase id=P09 -->
### P09: Close stale exhaustion and direct playlist fencing

* [x] <!-- rpi:task id=P09-T01 --> On final queue delivery, recover from an
  expired worker claim by acquiring fresh fenced ownership before recording
  sanitized poison/manual-handoff evidence, then discard the exhausted message.
* [x] <!-- rpi:task id=P09-T02 --> Fence direct-distribution playlist insertion
  after idempotent membership readback and immediately before the external
  insert, with exactly one callback for an actual mutation.
* [ ] <!-- rpi:task id=P09-T03 --> Run focused, affected, full, static, diff,
  compile, and changed-line secret validation; require the exact remote-head
  gate before a normal push and verify the draft stacked PR checks.

P09 is the active implementation scope. P08 history remains intact; P09 corrects
the two final blocking cases without reopening unrelated provider behavior or
claiming deployment, merge, production mutation, or P05/P06 completion.
