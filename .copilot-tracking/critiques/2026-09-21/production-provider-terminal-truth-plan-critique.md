<!-- markdownlint-disable-file -->
# RPI Plan Critique: Production Provider Terminal Truth

## Metadata

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Critique date: 2026-09-21
* Plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
* Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
* Critique execution status: Complete

## Inputs and Criterion Boundary

* Task context and caller requirements: truthful worker/ACA terminal status; YouTube draft-to-public lifecycle with pre-I/O configuration rejection; identity-bound bounded YouTube/Spotify reconciliation without blind retry; completion of `jmservera/SquadScope-Podcaster#681`; closure or supersession of all relevant `jmservera/SquadScope-Podcaster#682` threads; sanitized receipts/correlation; alerts; focused tests and full repository validation; independent implementation review with no accepted critical; pushed branch and PR with deployment/canary/rollback and cross-links; production canary plus four consecutive weeks of external provider readback; no weakened gates; no changes to `/home/azureuser/source/SquadScope`.
* Research and evidence considered: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`, including C1–C20 and W1–W30.
* Decisions, dependencies, and acceptance criteria considered: current `origin/main`/#680 baseline; atomic fenced outbox before truthful exit; reconcile-before-mutate; at-most-once mutation intent; W17–W29 closure matrix; locked validation contract; independent review; feature-flagged canary/rollback; four-week production proof.
* Assessment boundary: This critique assesses implementation readiness and internal credibility only from the supplied research, plan, phase details, and caller requirements. It does not independently inspect code, GitHub state, deployment permissions, provider accounts, or production.

## Coverage Assessment

| Requirement, research, phase, or task ID | Coverage | Evidence or concern |
|---|---|---|
| C1–C5, W1–W3, P03-T01 | Covered | The provider-objective lattice and non-zero process/ACA contract are explicit. |
| C6–C9, W4–W7, W12, W25, P02-T01 | Partial | Lifecycle and pre-I/O rejection are covered, but ambiguous/in-flight mutation ownership and later reconciliation scheduling are not safe enough to implement. |
| C10–C12, W8–W13, P02-T02 | Partial | Fail-closed Spotify behavior is credible, but manual handoff is incorrectly allowed to satisfy canary/weekly evidence without subsequent external readback. |
| C13–C14, C20, W14, P01 | Partial | Schema and fencing semantics are strong, but atomic artifact/outbox visibility is not tied to a feasible storage commit protocol. |
| W14 / `jmservera/SquadScope-Podcaster#681` | Partial | Most issue requirements are planned, but explicit issue acceptance/closure evidence is absent. |
| W16–W29, P03-T02, P05-T03 | Covered | All 13 supplied relevant PR #682 threads have task and closure mappings. |
| Caller receipt/correlation requirement, P01-T01, P02-T03 | Covered | Correlation and sanitization fields, prohibitions, and tests are well specified. |
| C15, C17–C18, P03-T03 | Partial | Alert categories exist, but deployable thresholds, evaluation windows, routing, and acceptance evidence are deferred. |
| Caller focused-test and validation requirement, P04 | Partial | Required scenarios and commands are strong; arbitrary test ceilings could prevent sufficient coverage. |
| Caller review/PR/deployment requirement, P05 | Partial | Review and PR content are covered, but merge, required checks, image provenance, and deployment order are missing. |
| Caller four-week requirement, P06 | Partial | Consecutive-week handling is explicit, but Spotify manual outcome is accepted in place of required external provider readback. |
| No weakened gates / no SquadScope changes | Covered | The plan explicitly prohibits both. |

## Verdict

* Verdict: Revise
* Rationale: The candidate has strong requirement breadth, traceability, and phase ordering, but it is not yet safe to implement. One critical provider-mutation race is hidden by an unenforceable stale-owner claim, and high-severity gaps remain in atomic storage feasibility, reconciliation scheduling, production evidence semantics, and reviewed-artifact deployment order. These are direct planner corrections; the caller has already resolved the relevant policy choices.

## Findings

<!-- rpi:critique id=PC-001 -->
### PC-001 [Critical]: A fencing token cannot prevent an already in-flight stale owner from mutating the provider

* Related IDs: Cross-Phase Invariants 3 and 6; P01-T02; P02; P04-T01; W14; W22; W25.
* Evidence: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md` says expired owners cannot mutate and each fenced intent permits one mutation call, while the supplied research establishes that providers do not expose a repository-integrated fencing or idempotency key.
* Concern: A worker can validate its fence, begin a provider request, lose its lease while the request is in flight, and still complete the mutation. A takeover worker can then reconcile an apparent absence and issue another mutation. Repository-side CAS rejects stale receipt writes but cannot revoke an external request already sent.
* Impact: The central no-duplicate/at-most-once safety claim is not implementable as written and can fail precisely at the mutation/response-loss boundary the plan is intended to secure.
* Smallest useful change: Add an explicit mutation-ownership protocol: require sufficient lease budget before I/O; make `intent_persisted` a consumed mutation authorization that takeover workers may only reconcile, never re-mutate; quarantine ambiguous or expired in-flight intents; define provider-call timeout versus lease margin; and test lease expiry before, during, and after the external call.
* Action owner: Planning parent.
* Exact resolving evidence: Revised invariants and P01/P02/P04 details state the consumed-intent/takeover rules and timing inequality, with named fault scenarios proving that a takeover performs read-only reconciliation after any possibly issued mutation and cannot authorize a second mutation.
* Decision route: Direct planner correction; no significant or divergent user decision is required.

<!-- rpi:critique id=PC-002 -->
### PC-002 [High]: Atomic artifact/outbox visibility lacks a feasible commit design

* Related IDs: Functional Requirement 1; Cross-Phase Invariant 1; P01-T02; C13–C14; W14.
* Evidence: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md` requires artifact and outbox visibility in the same CAS-protected transition, while `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md` excludes distributed transactions across unsupported stores and leaves the storage layout open.
* Concern: The plan does not say whether artifact and outbox share one conditionally written aggregate, use an immutable artifact commit marker, or use another recoverable protocol. Separate blob and queue writes cannot simply be described as atomic.
* Impact: Implementers may create claimable work with a missing/unverified artifact, or a durable artifact with lost work, undermining the foundational enqueue invariant and recovery behavior.
* Smallest useful change: Select one concrete atomic-visibility protocol and order its writes, conditional checks, crash recovery, and orphan handling. A credible minimum is immutable artifact upload and integrity verification first, followed by one conditional outbox create referencing its hash, with claim-time revalidation and idempotent orphan repair.
* Action owner: Planning parent.
* Exact resolving evidence: P01-T02 contains a stepwise storage protocol naming the authoritative record, conditional-write boundary, crash states, recovery action, and tests for every interruption between artifact write, verification, outbox creation, and queue notification.
* Decision route: Direct planner correction; no significant or divergent user decision is required.

<!-- rpi:critique id=PC-003 -->
### PC-003 [High]: Durable pending/unknown reconciliation has no explicit scheduling and execution contract

* Related IDs: Safe State Model; P01-T02; P02-T01–P02-T03; P03-T01; P04-T01–P04-T02; P05-T04.
* Evidence: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md` says mutation messages for durable non-success states are acknowledged and follow-up reconciliation is created from durable state, but no task defines the scheduler, due-time fields, queueing transition, deduplication, verification horizon, or relationship to ACA retry.
* Concern: The plan separates queue acknowledgment from process failure correctly but leaves no implementable mechanism that guarantees processing/public readback resumes after a non-zero execution or that ACA retries cannot create empty-drain failure loops.
* Impact: Items can remain permanently pending, be reconciled concurrently, or generate repeated failed ACA executions without progressing toward authoritative terminal state.
* Smallest useful change: Define the reconciliation work type and scheduler: durable `next_reconcile_at`, verification attempt/budget separate from mutation attempts, one active scheduled token, claim/fence rules, ACA trigger/retry behavior, terminal exhaustion, and alert transitions.
* Action owner: Planning parent.
* Exact resolving evidence: Revised P01/P03 details name the component and state transitions that re-enqueue due reconciliation, show how one-item ACA execution selects it, define retry/timeout/empty-drain semantics, and add fake-clock tests for scheduling, deduplication, restart, exhaustion, and successful clearing.
* Decision route: Direct planner correction; no significant or divergent user decision is required.

<!-- rpi:critique id=PC-004 -->
### PC-004 [High]: Spotify manual handoff is allowed to substitute for required production external readback

* Related IDs: Acceptance Criteria; P05-T04; P06-T01; caller production-canary and four-week requirement; W8–W13.
* Evidence: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md` accepts “Spotify verified state or expected manual handoff” for canary and “Spotify state/manual outcome” for weekly evidence.
* Concern: Manual handoff is actionable evidence, not external provider readback. The caller explicitly requires a production canary plus four consecutive weeks of external provider readback.
* Impact: The plan could declare rollout or sustained verification complete without proving Spotify’s externally visible state.
* Smallest useful change: Require manual operator publication, when needed, to be followed by bounded authoritative Spotify readback tied to the expected provider item before that provider/week is accepted. If authoritative readback itself is unavailable, the week is not accepted and the task remains open.
* Action owner: Planning parent.
* Exact resolving evidence: P05-T04 and P06-T01 acceptance tables require an externally read Spotify identifier/state/timestamp after handoff, define failure/restart behavior, and prohibit manual-handoff status alone from satisfying canary or weekly completion.
* Decision route: Direct planner correction because the caller already selected external readback; no user decision is required.

<!-- rpi:critique id=PC-005 -->
### PC-005 [High]: Review, merge, required-check, image-build, and production-deploy order is incomplete

* Related IDs: P04-T03; P05-T01–P05-T04; Dependencies; caller branch/PR and deployment requirement; C18.
* Evidence: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md` orders independent review before branch push/PR, then allows canary after PR/thread work, but does not require PR checks/approval, merge to current main, post-merge validation, or a release-built digest before production deployment.
* Concern: The “exact reviewed image” can differ from the merge result or bypass the repository’s normal main/release path. There is also no explicit merge task even though the standard release workflow appears to operate from repository state rather than an unmerged local branch.
* Impact: Production could run an unmerged or differently built artifact, breaking provenance, rollback, and the claim that repository gates were preserved.
* Smallest useful change: Define the delivery chain explicitly: push/open PR, run required checks, independent review of the final pushed SHA, resolve findings, obtain approval, merge without content drift, build/sign or identify the release image from the merge SHA, verify digest, then deploy/canary that digest. Re-review or revalidate any post-review change.
* Action owner: Planning parent.
* Exact resolving evidence: P05 includes explicit PR-check/approval/merge/image-provenance tasks and gates; the changes record must capture reviewed SHA, merged SHA, workflow run, image digest, deployment revision, and equality/provenance checks.
* Decision route: Direct planner correction; no significant or divergent user decision is required.

<!-- rpi:critique id=PC-006 -->
### PC-006 [Medium]: Issue #681 completion is not an explicit acceptance and closure gate

* Related IDs: Caller requirement to complete `jmservera/SquadScope-Podcaster#681`; P01; P04-T01; P06-T02; W14.
* Evidence: The plan implements most #681 concepts and mentions issue updates at final closeout, but only PR #682 receives a dedicated closure matrix and explicit closed-state criterion.
* Concern: “Safely advance” language in the plan can permit completion while #681 remains open or while an issue acceptance item is untraced.
* Impact: The implementation may satisfy the internal plan while failing the caller’s explicit requirement to complete #681.
* Smallest useful change: Add a #681 acceptance matrix mapping every issue requirement to task, code/test/operational evidence, and a dedicated closure task requiring the issue to be closed with the replacement PR and production-evidence links.
* Action owner: Planning parent.
* Exact resolving evidence: A plan matrix covers all #681 acceptance bullets, and P05/P06 completion requires a durable `jmservera/SquadScope-Podcaster#681` closure comment/link and closed issue state.
* Decision route: Direct planner correction; no significant or divergent user decision is required.

<!-- rpi:critique id=PC-007 -->
### PC-007 [Medium]: Alert requirements are categories rather than deployable operational contracts

* Related IDs: P03-T03; P05-T04; P06-T01; C15; C17–C18; caller required-alert requirement.
* Evidence: The plan names all required alert types and asks implementation to record severity, owner, and runbook, but it does not define threshold semantics, evaluation windows, routing, deduplication, missing-data behavior, or canary acceptance for each alert.
* Concern: Implementers can satisfy the task with alerts that exist syntactically but are too noisy, too slow, non-paging when required, or unable to clear after authoritative resolution.
* Impact: Provider truth failures may remain operationally invisible despite nominal alert coverage.
* Smallest useful change: Add an alert contract table with signal, dimensions, threshold/window, severity, owner/route, missing-data behavior, runbook, fire fixture, and clear fixture for every required alert.
* Action owner: Planning parent.
* Exact resolving evidence: P03-T03 contains the complete alert contract table and P05-T04/P06 require deployed rule IDs plus observed fire/clear evidence for canary-safe synthetic or controlled states.
* Decision route: Direct planner correction; no significant or divergent user decision is required.

<!-- rpi:critique id=PC-008 -->
### PC-008 [Medium]: Arbitrary test-file and named-test ceilings can weaken the safety proof

* Related IDs: Locked Test and Validation Contract; P04-T01–P04-T03; caller no-weakened-gates requirement.
* Evidence: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md` limits new test files to four, named test cases to 90, and shared fixture modules to one without evidence that these ceilings fit the crash, concurrency, provider, ACA, telemetry, workflow, canary, and rollback matrices.
* Concern: The ceilings optimize artifact shape rather than required behavioral coverage and may encourage overly broad parameterization or force unrelated suites together.
* Impact: Important semantic boundaries could be omitted while the implementation still claims compliance with the “locked” contract.
* Smallest useful change: Replace hard maxima with a no-redundancy rule and a required scenario matrix; if limits are retained, make them advisory and state that required safety coverage takes precedence.
* Action owner: Planning parent.
* Exact resolving evidence: The validation contract states that every required scenario must have independently diagnosable assertions and that no numerical ceiling may block coverage; the changes record maps all matrix rows to tests.
* Decision route: Direct planner correction; no significant or divergent user decision is required.

<!-- rpi:critique id=PC-009 -->
### PC-009 [Medium]: The required upstream SquadScope PR cross-link is left conditional without a closure rule

* Related IDs: User Decisions and Requirements; Acceptance Criteria; P05-T02; caller required cross-links.
* Evidence: The candidate says the upstream SquadScope PR is linked “when available” and records availability as unresolved, while the caller requires the branch/PR to include the upstream PR cross-link and forbids local changes to `/home/azureuser/source/SquadScope`.
* Concern: The plan provides no task to identify the authoritative upstream PR or document why none exists before PR acceptance.
* Impact: The implementation PR can be declared complete with a missing required relationship, weakening incident traceability across repositories.
* Smallest useful change: Add a metadata-only cross-link resolution step that discovers the upstream PR without modifying the SquadScope checkout; require the exact link before acceptance, or record authoritative evidence that no upstream PR exists and route that mismatch back to the parent rather than silently omitting it.
* Action owner: Planning parent.
* Exact resolving evidence: P05-T02 names the upstream PR URL and reciprocal-link evidence, or records a verified no-PR condition as an explicit blocker requiring caller disposition.
* Decision route: Direct planner correction for discovery and gating; a user decision is required only if authoritative evidence proves no upstream PR exists and the caller must waive or replace the required link.

## Strengths and Residual Risk

* The plan correctly starts from current main/#680, rejects wholesale #682 reuse, and gives every supplied W17–W29 thread an explicit implementation/test/disposition path.
* The terminal-state lattice is materially stronger than the current runtime: only externally verified public state succeeds, while unknown/manual/poison states remain durable and actionable.
* YouTube pre-I/O validation, processing verification, promotion, and final privacy readback are all represented, and Spotify mutation remains fail-closed under unsupported contracts.
* Correlation and sanitization requirements are unusually concrete and prohibit unsafe response bodies, credentials, signed URLs, and high-cardinality/PII dimensions.
* Rollback preserves evidence and does not restore inline blind mutation.
* Residual provider uncertainty remains unavoidable: external providers cannot honor repository fencing, and Spotify’s unofficial mutation surface may change. The revised plan must handle those as explicit reconciliation/manual states rather than stronger delivery guarantees.

## Questions or Blocking Evidence Gaps

* No additional research is required to revise the plan.
* Deployment authority and provider credentials remain execution prerequisites, but they are not decision-critical planning gaps if recorded as pre-deployment gates.
* The only potentially user-routed issue is PC-009 if metadata proves that the required upstream PR does not exist.

## Limitations

* This critique did not independently verify the current worktree, GitHub thread states, Azure configuration, provider API behavior, or production access.
* It evaluates the supplied final candidate, not an implementation diff.
* It cannot confirm whether a reciprocal upstream PR link is currently available; it only identifies that the plan lacks a deterministic resolution path.

## Recommended Next Action

* Highest-impact finding: PC-001.
* Action owner: Planning parent.
* Smallest next action: Revise the mutation-ownership invariant first so an expired or ambiguous in-flight intent can only reconcile and can never authorize a second provider mutation, then propagate that rule into P01, P02, and the crash/lease matrix.
* User response required: No for the planner-owned revision set; only PC-009 may require a later caller decision if the required upstream PR is proven not to exist.

## Existing Artifacts

| Artifact | Description |
|---|---|
| [.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md](.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md) | Supplied research and evidence boundary. |
| [.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md](.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md) | Final-candidate plan assessed by this critique. |
| [.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md](.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md) | Detailed phases, tasks, state model, and completion evidence assessed by this critique. |
| [.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md](.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md) | Complete independent final-candidate critique. |

## Next Steps

Active `rpi-quick` planning parent should apply PC-001 through PC-009 directly, record dispositions, and continue only after the revised plan establishes implementable provider-mutation ownership, reconciliation scheduling, production evidence, and deployment provenance. No user action is currently required.
