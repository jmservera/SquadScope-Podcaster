<!-- markdownlint-disable-file -->
# Review: Production Provider Terminal Truth

## Scope and Evidence

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Review date: 2026-09-21
* Review scope: One corrected full-task review, reassessing the Hermes correction implementation and preserving the prior finding history
* Assessed boundary: Corrected W38/W39 incident framing; P00 Podcaster receipt/arrival observability; P01-P04 implementation; RV-001-RV-005 dispositions; safety invariants; validation; and residual P00-T01/P05/P06 work
* Plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
* Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
* Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`
* Changes: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
* Other evidence considered: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`; `.copilot-tracking/pr/pr.md`; complete `origin/main` worktree delta; source, tests, Bicep, and operations documentation; independently rerun validation recorded below

## Opening Review State

* Interpreted review goal: Independently determine whether the authoritative W38/W39 correction and RV-001-RV-005 implementation are conformant, while separating local execution from upstream delivery and production outcomes.
* Review scope: Corrected full-task boundary, without a second review pass.
* Evidence readiness: One unambiguous artifact set and one canonical review record exist. The correction source, tests, documentation, infrastructure, and validation evidence are available locally.
* Acceptance basis: Authoritative correction; plan functional/non-functional requirements and acceptance criteria; historical PC-001-PC-009 dispositions; prior RV findings; no unsafe narrowing or weakened quality/security gate.
* First comparison boundary: Reconcile corrected incident facts and plan/detail/change markers before reassessing each RV finding and validation claim.
* Active read-only boundaries: Source, tests, docs, plan, details, research, critique, changes, git, GitHub, and deployment state are read-only. This review record is the only writable artifact.
* Initial blockers: P00-T01 requires an owning `jmservera/SquadScope` prevention change. P05 requires repository/delivery/deployment authority. P06 requires four elapsed production weeks.

## Execution Status

* Review execution status: Complete
* Assessed implementation execution status: Partial
* Review execution evidence: Corrected artifacts and implementation were inspected; targeted/full tests, compile, Ruff, diff safety, Bicep, both Checkov modes, image digest, and container exit were independently checked.
* Partial implementation basis: Podcaster-side P00-T02/P00-T03 and P01-P04 ran. P00-T01 remains cross-repository work; P05-P06 were not executed and are not defects merely because they remain open.

## Plan-to-Change Reconciliation

| Current plan scope | Descriptive changes-record summary | Current-state reconciliation | Gap or rationale |
|---|---|---|---|
| W38/W39 correction | W38 is published comparative evidence; W39 is the missed publication blocked before Azure | Reconciled in research, plan, changes, source-facing docs, tests, and current PR draft | No W39 downstream execution is fabricated. Canary prose starts upstream and ends at provider readback. |
| P00-T01 | Exact upstream prevention change | Correctly blocked/residual | Owned by `jmservera/SquadScope`, not automatically a Podcaster defect. |
| P00-T02-P00-T03 | Dispatch intent, Azure arrival, missing-arrival signals, deterministic provider fixture | Reconciled for the Podcaster boundary | Registered intent is required before correlated generation can clear arrival absence. The fixture proves correlation shape, not live cross-repository deployment. |
| P01-T03 / RV-002 | Deduplicated fair reconciliation scheduler | Partial | Fresh notifications deduplicate and a cursor rotates within the returned set, but storage enumeration is capped at the first 5,000 paths without continuation. Records beyond that set can starve; RV-002 remains open in narrowed form. |
| P02-T01/P02-T03 / RV-001 | Read-only promotion convergence | Reconciled | A takeover performs authoritative readback, converges public, or records durable identity-bound unknown without issuing a second promotion mutation. |
| P03-T03 / RV-003 | Deployable warning/critical alerts, missing-data behavior, routes | Partial | Warning/critical query classification and windows are generated, but all logical routes still share one action group and missing-data behavior is description text rather than an executable absence/depth/heartbeat query. RV-003 remains open. |
| P01-T02/P03-T02 / RV-004 | Reference-safe retained orphan cleanup | Partial | Cleanup is age-bounded and reference-safe for a fully enumerated outbox set, but disables all cleanup at 5,000 returned outbox paths instead of continuing safely. RV-004 remains open in narrowed form. |
| P02-T01/P02-T03 / RV-005 | Identity-bound reconciliation or accurate fail-closed evidence | Reconciled | Missing provider identity is now accurately recorded as `youtube_identity_unprovable`; known promotion identity uses provider readback. |
| P04 | Locked validation | Reconciled with one environment variance | Targeted 741 passed. Current full rerun produced 3070 passed/3 skipped/2 deselected because the scale-out integration skipped when Docker Compose startup was unavailable; the changes record preserves the earlier 3071/2/2 run. |
| P05-P06 | Delivery, merge/image provenance, canary, rollback, four weeks | Correctly open | Residual external work, not proof of a Podcaster implementation defect. |

## Completed Work Assessment

| Related marker | Files | What changed and why | Completion evidence | Validation | Assessment |
|---|---|---|---|---|---|
| P00-T02/P00-T03 | `podcaster/dispatch_receipts.py`, `podcaster/api.py`, `podcaster/distribution_scheduler.py`, tests and alert Bicep | Added sanitized dispatch intent/arrival correlation and missing-arrival classification | Registered-intent requirement, fire/clear test, API correlation, terminal-provider fixture | Targeted suite passed | Conformant for Podcaster receipt/arrival observability; upstream prevention remains P00-T01. |
| RV-001 | `podcaster/distribution_worker.py`, `tests/test_distribution_worker.py` | Added read-only known-video promotion readback | Public takeover converges; non-public takeover records unknown; duplicate promotion is an assertion failure | Passed | Resolved. |
| RV-002 | `podcaster/distribution_outbox.py`, `podcaster/distribution_scheduler.py`, tests | Added notification markers, stale repair, and cursor rotation | 130-record and repeated-notification tests pass | Passed | Improved but not resolved beyond the fixed 5,000-path enumeration boundary. |
| RV-003 | `infra/modules/distribution-alerts.bicep`, docs, deployment tests | Added separate warning/critical rule definitions and descriptive route/missing-data metadata | Generated-query tests and Bicep build pass | Passed syntactically | Not fully deployable as accepted: route separation and missing-data detection are not executable. |
| RV-004 | `podcaster/distribution_outbox.py`, scheduler, tests | Added artifact metadata, retention, reference scan, and deletion | Referenced artifact retained; old orphan removed | Passed | Safe for fewer than 5,000 outbox paths, but cleanup stalls at/above the cap. |
| RV-005 | `podcaster/distribution_worker.py`, tests | Corrected ambiguous-upload evidence naming | `youtube_identity_unprovable` is durable and non-success | Passed | Resolved by the accepted fail-closed alternative. |
| Truthful terminal execution | worker/outbox/provider code and tests | Preserved non-zero exits, fencing, consumed intent, receipts, manual handoff, and external-readback-only success | State-lattice tests and container smoke | Exit 2 reproduced | Conformant. |

## Implementation-Time Plan and Detail Update Assessment

| Affected area or marker | What changed and why | Triggering evidence and user decision | Reconciliation performed | Planning and critique state | Assessment |
|---|---|---|---|---|---|
| W38/W39 incident boundary | Replaced W38 missed-week implications with W39 pre-Azure boundary | Authoritative caller correction | Research, plan, top-level details, changes, runbook, and PR draft updated | Historical critique intentionally unchanged | Reconciled. |
| P00 | Added upstream-to-Azure correlation lane | W39 had no Azure execution | Podcaster receipt/absence work completed; upstream prevention separately blocked | Compatible material revision | Reconciled. |
| RV-006 / P05-T03 | Expanded #682 closure inventory | Prior review found newer unresolved threads | Plan matrix includes six RV-006 rows and later rows without claiming GitHub resolution | Planning route completed | Prior RV-006 is resolved at planning level; execution remains P05 residual work. |
| Detail current-state prose | Some historical sentences still say RV-001-RV-005 or RV-002-RV-004 “remain open” despite completion claims elsewhere | Correction implementation | Not fully reconciled inside phase details | No second critique required | Medium artifact-state inconsistency; see RV-007. |
| PR narrative | Draft still says RV-001-RV-006 are unimplemented, reports the old image digest/test counts, and says “Do not deploy this draft” based on the superseded review state | Correction implementation and new review | Not updated after correction | P05 not executed | Must be rewritten before PR use; see PR narrative requirements. |

## Critique and Material Revision Assessment

* Latest critique dispositions: PC-001-PC-009 remain historical and unchanged as required. The corrected plan retains consumed-intent fencing, concrete artifact/outbox ordering, reconciliation scheduling, external Spotify readback, delivery provenance, issue/thread gates, alert contracts, validation coverage, and cross-repository linkage.
* Material revisions: The W39 upstream lane and W38 comparative-only treatment preserve confirmed user intent and do not justify removal of independent provider safety.
* Dependent-work pause assessment: P05/P06 remain open. No delivery or production acceptance is falsely claimed.
* Justification assessment: The incident correction is supported. Completion is overstated for RV-002/RV-003/RV-004 and phase-detail current-state prose is internally stale.

## Plan Follow-Up Assessment

| Follow-up item | Why outside immediate scope | Owner or next action | Assessment and route |
|---|---|---|---|
| Future authoritative Spotify mutation/idempotency support | Provider contract is unsupported today | Distinct provider-capability follow-up | Properly remains outside active implementation. |
| Optional W38 deployed-image/revision forensics | Historical reconstruction does not change the authoritative publication fact | Distinct incident-forensics follow-up | Optional; never use it to reclassify W38 as missed. |

## Findings

<!-- rpi:review id=RV-001 -->
### RV-001 [High, resolved]: Read-only YouTube promotion takeover convergence

* Related scope: P02-T01, P02-T03, P04
* Evidence: `podcaster/distribution_worker.py` now performs known-identity readback before any promotion branch. `tests/test_distribution_worker.py` proves public convergence and non-public fail-closed behavior while forbidding duplicate mutation.
* Impact: The former repeated-failure/stale-claim gap is closed.
* Destination: Resolved in current implementation.
* Disposition: Resolved; retain historical context and the regression tests.

<!-- rpi:review id=RV-002 -->
### RV-002 [High, remains open]: Scheduler fairness stops at the fixed 5,000-path storage window

* Related scope: P01-T03, P03-T02, P04
* Evidence: `due_reconciliations_page()` calls `list_blobs(..., limit=5000)` and rotates only that returned list. There is no storage continuation marker that can reach paths beyond the first 5,000. Notification deduplication and 130-record fairness tests pass but do not cover the cap.
* Impact: Long-retained outbox evidence can permanently starve valid due reconciliation work, despite the accepted starvation-safe contract.
* Destination: `rpi-implement`
* Smallest useful next action: Add backend continuation/pagination or a bounded shard/index contract that eventually visits every retained outbox record, with a test above the boundary.

<!-- rpi:review id=RV-003 -->
### RV-003 [High, remains open]: Alert routing and missing-data behavior are descriptive, not deployable

* Related scope: P03-T03, P04-T02, P05-T05
* Evidence: `infra/modules/distribution-alerts.bicep` now creates warning and critical queries, but every route uses the single `actionGroupResourceId`; route names exist only in descriptions. Missing-data contracts also exist only in descriptions, while queries require an emitted matching log row and cannot detect absence, active outbox depth without telemetry, or missing claim heartbeat.
* Impact: Operations/upstream/operator/production routing cannot be enforced, and the accepted missing-telemetry failures remain invisible. Canary fire/clear evidence would not prove the documented contract.
* Destination: `rpi-implement`
* Smallest useful next action: Bind logical routes to distinct action-group inputs and add executable absence/depth/heartbeat rules or authoritative scheduled emissions, then assert generated actions and queries.

<!-- rpi:review id=RV-004 -->
### RV-004 [Medium, remains open]: Orphan cleanup is safe but can stop permanently at scale

* Related scope: P01-T02, P03-T02, P04
* Evidence: `cleanup_orphan_artifacts()` returns zero whenever `list_blobs(..., limit=5000)` returns 5,000 paths. This avoids deleting referenced artifacts from an incomplete scan, but provides no continuation or alternate repair path.
* Impact: Reference safety is preserved, but old orphan artifacts can accumulate indefinitely once the durable outbox reaches the fixed cap.
* Destination: `rpi-implement`
* Smallest useful next action: Use complete paginated reference enumeration, a durable artifact-reference index, or another bounded fail-safe design that retains safety while making cleanup progress.

<!-- rpi:review id=RV-005 -->
### RV-005 [Medium, resolved]: Ambiguous upload evidence no longer claims identity-bound readback

* Related scope: P02-T01, P02-T03, P04
* Evidence: A consumed upload without provider identity records `youtube_identity_unprovable`; a consumed promotion with known identity records `youtube_promotion_identity_readback`.
* Impact: Durable evidence is accurate and fail-closed without inventing provider observations.
* Destination: Resolved in current implementation.
* Disposition: Resolved by the accepted accurately named unprovable-state alternative.

<!-- rpi:review id=RV-006 -->
### RV-006 [High, resolved at planning level]: Current PR #682 safety inventory

* Related scope: P05-T03
* Evidence: The plan closure matrix now includes all six prior RV-006 threads and later current rows, with evidence/reply/actual-state requirements.
* Impact: The planning gap is closed without falsely claiming GitHub thread resolution.
* Destination: Residual P05-T03 execution.
* Disposition: Planning finding resolved; thread replies/resolution remain external delivery work.

<!-- rpi:review id=RV-007 -->
### RV-007 [Medium]: Phase details and PR handoff retain superseded review state

* Related scope: Artifact reconciliation and P05 handoff
* Evidence: Phase details still contain statements that RV-001-RV-005 or RV-002-RV-004 remain open, conflicting with its completion table. `.copilot-tracking/pr/pr.md` still presents the former six-finding rejection, old `3059`/image-digest evidence, and says the correction findings are unimplemented.
* Impact: A delivery owner could publish a materially false review and validation narrative even though source behavior changed.
* Destination: `rpi-plan` for canonical phase-detail reconciliation; P05 PR narrative update before delivery.
* Smallest useful next action: Reconcile phase-detail status prose and replace the PR review/validation sections with this review’s exact dispositions and current evidence without claiming P05/P06 completion.

## Defects

* RV-002 and RV-003 are high-severity implementation defects routed to `rpi-implement`.
* RV-004 is a medium-severity implementation defect routed to `rpi-implement`.
* RV-007 is a medium artifact-state/handoff defect routed to `rpi-plan` and P05 narrative work.
* RV-001 and RV-005 are resolved. RV-006 is resolved at planning level.
* No critical-severity defect was found. Fencing, consumed mutation authority, truthful exits, provider readback, manual handoff, and secret/PII protections remain fail-closed.

## Routed Findings

| Finding | Destination | Owner or next action | Reason for route |
|---|---|---|---|
| RV-002 | `rpi-implement` | Add complete bounded scheduler enumeration | Accepted design, remaining implementation defect |
| RV-003 | `rpi-implement` | Implement executable route and missing-data contracts | Accepted design, remaining implementation defect |
| RV-004 | `rpi-implement` | Make cleanup progress beyond the safe scan cap | Accepted design, remaining implementation defect |
| RV-007 | `rpi-plan` plus P05 handoff | Reconcile details and PR narrative | Canonical artifact-state inconsistency |

Later implementation of a routed finding does not require another Review.

## Residual Work

* P00-T01 is cross-repository residual work owned by `jmservera/SquadScope`: prevent/classify the blocked dispatch at its exact source and prove it with focused upstream tests.
* P05 remains external delivery work: commit/push/PR checks, final-SHA independent review, #682 evidence/replies, approval/merge, release provenance, deployment, canary, and rollback.
* P06 remains elapsed production work: four consecutive weeks with upstream dispatch evidence, Azure correlation, and authoritative provider readback.
* These residual items are not automatically Podcaster defects.

## Blockers and Remaining Work

* Blockers: P00-T01 upstream repository ownership; P05 repository/deployment/provider authority; P06 elapsed calendar weeks.
* Remaining implementation defects: RV-002, RV-003, RV-004.
* Remaining artifact/handoff correction: RV-007.
* Remaining active plan work: P00-T01 and P05-T01 through P06-T02.

## Validation Evidence

| Command | Scope | Status | Summary |
|---|---|---|---|
| Targeted correction command from changes record | P00/outbox/worker/provider/deploy owners | Passed | Independently reproduced `741 passed, 1 warning in 55.54s`. |
| `pytest tests/ -q` | Full repository | Passed with environment variance | `3070 passed, 3 skipped, 2 deselected, 1 warning`; scale-out skipped because Docker Compose startup was unavailable. Author record reports `3071 passed, 2 skipped, 2 deselected` when that integration ran. |
| `python3 -m compileall -q podcaster` | Production Python | Passed | No compile failures. |
| `ruff check podcaster tests --quiet` | Production and tests | Passed | No lint findings. |
| `ruff format --check podcaster tests --quiet` | Production and tests | Passed | Formatting check passed. |
| `git diff --check` and deleted-test query | Worktree safety | Passed | No whitespace errors and no deleted tests. |
| `az bicep build --file infra/main.bicep --stdout >/dev/null` | Infrastructure | Passed with existing warning | Existing BCP318 warning only. |
| Exact unskipped Checkov | Infrastructure security baseline | Baseline retained | `36 passed, 7 failed`; the seven are the documented pre-existing ACR/storage/OpenAI findings. |
| Repository-standard Checkov skip-list gate | Blocking infrastructure gate | Passed | `34 passed, 0 failed`. |
| `docker image inspect podcaster-synthesis:ci --format '{{.Id}}'` | Corrected local image | Passed | `sha256:ac60e9065a3ccbdd77f26253b88bb61ae610926424878f2d92eb0a7372fe6b1d`. |
| Distribution-worker container smoke | Truthful process exit | Passed | Missing required queue configuration exited `2`. |

## Unsafe-Narrowing Assessment

* Truthful exits: Preserved; non-public, partial, pending, unknown, manual, failed, and unexpected empty work remain non-success.
* Provider safety and fencing: Preserved; consumed intent prevents takeover mutation and stale writes remain rejected.
* Bounded reconciliation: Preserved in per-operation behavior, but global enumeration defects remain RV-002/RV-004.
* Terminal receipts and manual handoff: Preserved.
* External readback: Preserved as the only provider-success basis.
* CI/security gates: Not weakened; exact baseline and blocking Checkov modes are both disclosed.
* PII/secrets: Dispatch and outbox durable fields remain allowlisted/sanitized; no body, URL, token, cookie, signed URL, account identity, or provider identity is added to dispatch correlation.

## PR Narrative Changes Required

Before `.copilot-tracking/pr/pr.md` is used as a real PR description:

1. Replace the former “0 Critical / 4 High / 2 Medium” rejection section with this review’s current dispositions: RV-001 and RV-005 resolved, RV-006 resolved at planning level, RV-002/RV-003/RV-004 open, and RV-007 added.
2. Replace old validation counts and image digest with targeted `741`, author full `3071/2/2` plus the independent environment-variant `3070/3/2`, exact Checkov `36/7`, gate `34/0`, digest `sha256:ac60e9065a3ccbdd77f26253b88bb61ae610926424878f2d92eb0a7372fe6b1d`, and smoke exit `2`.
3. Keep W38 explicitly published and comparative-only; keep W39 as the pre-Azure incident with no downstream execution.
4. State that P00-T01 is owned by `jmservera/SquadScope` and that P05/P06 remain residual external work.
5. Do not claim merge readiness, deployment readiness, #682 supersession, canary acceptance, or four-week completion while open high findings and external gates remain.

## Outcome

* Outcome: Not accepted
* Outcome rationale: Execution is Partial and external P00/P05/P06 work is correctly residual. However, RV-002 and RV-003 are actual high-severity defects in accepted scheduler and alert contracts, RV-004 remains a medium cleanup-liveness defect, and RV-007 leaves canonical detail/PR state inconsistent. The correction is materially safer and resolves RV-001/RV-005, but the high findings require a Not accepted outcome.

## Closeout Routing Record

| Finding class | Destination | Owner or next action |
|---|---|---|
| Implementation defect | `rpi-implement` | Resolve RV-002, RV-003, and RV-004. |
| Decision/artifact gap | `rpi-plan` | Reconcile RV-007 phase-detail status and P05 handoff state. |
| Material evidence gap | None | Evidence is sufficient for this verdict. |
| Non-blocking residual work | P00-T01, P05, P06 | Upstream owner, delivery owner, and production verification owner respectively. |

* Review execution status: Complete
* Assessed implementation execution status: Partial
* Outcome: Not accepted
* Severity summary: 0 Critical; 2 High open; 2 Medium open; 2 resolved prior findings; 1 planning-level prior finding resolved
* Validation coverage: Targeted/full pytest, compile, Ruff, diff safety, Bicep, exact and blocking Checkov, image digest, and truthful container exit
* Blockers: P00-T01 upstream ownership; P05 delivery/deployment authority; P06 elapsed weeks

## Relevant Artifacts

| Artifact | Description |
|---|---|
| [.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md](.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md) | Corrected plan, acceptance criteria, closure matrix, and residual phases |
| [.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md](.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md) | Phase/task semantics and the RV-007 stale-state inconsistency |
| [.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md](.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md) | Authoritative W38/W39 correction evidence |
| [.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md](.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md) | Single preserved historical critique |
| [.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md](.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md) | Hermes implementation and validation evidence |
| [.copilot-tracking/pr/pr.md](.copilot-tracking/pr/pr.md) | Draft PR narrative requiring the changes listed above |
| [.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md](.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md) | Canonical corrected review result |

## Next Steps

Return this canonical record to the requesting parent. Route RV-002/RV-003/RV-004 to `/rpi-implement` and RV-007 to `/rpi-plan`; separately clear P00-T01 in `jmservera/SquadScope` and retain P05/P06 as external residual work. Do not run another review.
