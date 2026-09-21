<!-- markdownlint-disable-file -->
# Review: Production Provider Terminal Truth

## Scope and Evidence

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Review date: 2026-09-21
* Review scope: Full task, with completed implementation scope P01-P04 and externally gated P05-P06 assessed separately
* Assessed boundary: Authoritative user outcomes, plan acceptance criteria, critique dispositions, implementation evidence, the complete tracked and untracked `origin/main` worktree delta, tests, infrastructure, operations documentation, and current GitHub evidence
* Plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
* Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
* Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`
* Changes: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
* Other evidence considered: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`; local source/tests/Bicep/docs; `jmservera/SquadScope-Podcaster#681`; `jmservera/SquadScope-Podcaster#682`; `jmservera/SquadScope-Coordinator#17`; focused fault reproduction and validation commands recorded below

## Opening Review State

* Interpreted review goal: Independently determine whether the partial implementation truthfully and safely satisfies P01-P04 and all currently reviewable authoritative outcomes, while leaving P05-P06 open unless real delivery and production evidence exists.
* Review scope: Full task boundary, with execution status and outcome separated and with no false completion requirement for externally gated P05-P06.
* Evidence readiness: One unambiguous artifact set exists and records P01-P04 complete, P05-P06 open, detailed validation claims, implementation-time reconciliations, and two distinct follow-up items. The full local implementation and current external metadata were available for comparison.
* Acceptance basis: Caller outcomes; plan functional, non-functional, and acceptance criteria; PC-001 through PC-009 dispositions; all current relevant safety concerns from `jmservera/SquadScope-Podcaster#682`; no weakening of quality or safety gates.
* First comparison boundary: Reconcile plan/detail markers and claimed completed work against the complete `origin/main` worktree delta before validating implementation semantics or external delivery claims.
* Active read-only boundaries: Source, configuration, documentation, tests, GitHub, and Azure evidence were read-only. This canonical review record was the only writable artifact.
* Initial blockers: P05 delivery/canary and P06 four-week production verification are externally gated; they do not block review of P01-P04 but prevent complete task execution.

## Execution Status

* Review execution status: Complete
* Assessed implementation execution status: Partial
* Review execution evidence: The complete artifact set, tracked and untracked implementation, focused fault behavior, full repository tests, static checks, Bicep, Checkov, container digest, and current GitHub state were assessed.
* Partial implementation basis: P01-P04 are marked complete; P05-T01 through P06-T02 remain open. This review does not require those external phases to be falsely complete.

## Plan-to-Change Reconciliation

| Current plan scope | Descriptive changes-record summary | Current-state reconciliation | Gap or rationale |
|---|---|---|---|
| P01-T01 | Versioned sanitized outbox and correlation schema | Reconciled | Durable publication, artifact, claim, intent, receipt, verification, and aggregate fields exist and reject URL/secret-shaped durable values. |
| P01-T02 | Atomic enqueue and fenced consumed-intent claims | Partial | Immutable artifact verification, conditional outbox create, claims, fences, lease margin, and consumed intent exist. Required orphan artifact repair/retention is absent; see RV-004. |
| P01-T03 | Deduplicated bounded reconciliation scheduling | Missing required semantics | A token and due time exist, but the scheduler repeatedly enqueues the same token and scans only the first 100 blob names without continuation; see RV-002. |
| P01-T04 | #680 compatibility and disabled-by-default routing | Reconciled for local scope | Routing defaults off and historical ambiguous evidence becomes reconciliation-only. Production migration evidence remains P05/P06 work. |
| P02-T01 | YouTube draft-processing-public lifecycle | Partial | Initial public configuration is rejected before provider I/O, draft upload and processing readback exist, and public success requires privacy readback. Ambiguous identity reconciliation is asserted rather than performed, and promotion takeover does not converge; see RV-001 and RV-005. |
| P02-T02 | Spotify bounded reconciliation/manual handoff | Reconciled for safe local direction | Unsupported unattended mutation remains fail-closed; manual handoff is non-success and expected-item readback can clear it. Live provider proof remains P05/P06 work. |
| P02-T03 | Durable intent, receipt, and readback | Reconciled with RV-001 exception | Intent is consumed before I/O and receipts/readback are durable. A consumed ambiguous promotion can remain without a durable bounded terminal/reconciliation result. |
| P03-T01 | Truthful aggregation and queue disposition | Reconciled | Empty, partial, pending, unknown, manual, failed, skipped, and non-public outcomes are nonzero; queue acknowledgment is separated from business success. |
| P03-T02 | Bounded execution and cleanup | Partial | One-item video/distribution workers and bounded scoped deletion exist. Orphan outbox artifacts have no repair/retention cleanup, and current #682 thread coverage has advanced beyond the locked matrix. |
| P03-T03 | Provider telemetry and deployable alerts | Missing required semantics | Low-cardinality signals exist, but the deployed queries ignore warning signals and do not implement missing-data, route, or per-contract window behavior; see RV-003. |
| P04 | Fault tests and repository validation | Partial | The claimed full suite and static/build evidence are reproducible. Required semantic gaps above are not covered by tests, and the existing tests pass while those defects remain. |
| P05-P06 | Delivery, supersession, canary, rollback, four weeks | Correctly open | No push, replacement PR, merge-derived release, deployment, canary, rollback drill, or four-week evidence is claimed. |

## Completed Work Assessment

| Related marker | Files | What changed and why | Completion evidence | Validation | Assessment |
|---|---|---|---|---|---|
| P01 | `podcaster/distribution_outbox.py`, `podcaster/queue.py`, `podcaster/storage.py`, `podcaster/video/job_runner.py` | Added immutable artifacts, durable outbox state, CAS claims/fences, queue hints, and routing | Local state-machine tests and direct inspection | Focused tests pass | Partial because scheduler deduplication/starvation and orphan cleanup remain defective. |
| P02 | `podcaster/distribution_worker.py`, `podcaster/video/distribution.py`, `podcaster/video/youtube_publish.py`, `podcaster/publish.py` | Added draft-first YouTube flow, processing/public readback, and fail-closed Spotify handoff | Provider worker tests and direct fault reproduction | Focused tests pass; takeover reproduction fails closed with `StaleClaimError` | Partial because the takeover does not persist a bounded outcome and ambiguous upload does not perform identity-bound reconciliation. |
| P03 | `podcaster/video/job_runner.py`, `podcaster/distribution_telemetry.py`, `infra/modules/distribution-alerts.bicep`, `docs/ops/distribution-terminal-truth.md` | Made process exit truthful and added provider-state signals/alerts | Exit lattice is implemented; alert definitions build | Full pytest and Bicep pass | Partial because deployed alert behavior does not match the documented contract. |
| P04 | New and existing test suites plus repository validation surfaces | Added focused tests and ran repository validation | `3059 passed, 2 skipped, 2 deselected`; static/build checks pass | Independently reproduced as recorded below | Validation is real but does not prove missing scenarios. |

## Implementation-Time Plan and Detail Update Assessment

| Affected area or marker | What changed and why | Triggering evidence and user decision | Reconciliation performed | Planning and critique state | Assessment |
|---|---|---|---|---|---|
| Implementation Status and P01-P06 markers | Opened full plan boundary while preserving delivery restrictions | Caller supplied explicit full scope and read-only delivery boundary | Plan, details, changes, blockers, and remaining work agree | Compatible with approved plan | Reconciled. |
| P06-T01 Spotify wording | Required authoritative Spotify readback after handoff | Existing PC-004 and caller requirement, not a new decision | Plan/details/worker tests agree | Critique intent preserved | Reconciled. |
| P04-T03 baseline | Fast-forwarded to `0752d1a` and rebuilt/reran validation | `origin/main` advanced without source conflict | Plan/details/changes identify baseline and stale Compose image diagnosis | No material design divergence | Reconciled. |
| P03-T03 alert completion | Changes record states the exact alert contract is deployable and tested | Implementation-time assertion | Plan and docs were updated, but Bicep behavior was not reconciled to those claims | PC-007 is not actually satisfied | Gap; RV-003. |
| PR #682 closure matrix | Plan locks W17-W29 from research | Current GitHub review state now has six unresolved threads not represented by W17-W29 | No current plan/detail update covers the new safety inventory | Material external evidence changed after planning | Gap; RV-006. |

## Critique and Material Revision Assessment

* Latest critique dispositions: PC-001 through PC-009 are recorded as resolved by the planner. The selected direction remains compatible with confirmed user intent.
* Material revisions: The Spotify external-readback wording and current-main baseline updates are justified and reconciled. No unauthorized divergent implementation decision was found.
* Dependent-work pause assessment: P05-P06 remain open as required. However, P01-P04 completion markers were advanced despite unresolved implementation defects in the PC-003 and PC-007 areas.
* Justification assessment: The changes record accurately discloses external delivery blockers, but overstates reconciliation scheduling, orphan handling, identity-bound reconciliation, and deployable alert completion.

## Plan Follow-Up Assessment

| Follow-up item | Why outside immediate scope | Owner or next action | Assessment and route |
|---|---|---|---|
| Evaluate future authoritative Spotify creator mutation/idempotency support | Provider has not published a supported contract with immutable identity and idempotency | Distinct future provider-capability follow-up | Properly outside current safe automation scope; remains open and is not a defect. |
| Optional deployed-image/revision W38 forensics | Historical causal reconstruction does not change the accepted implementation boundary | Distinct incident-forensics follow-up if still desired | Properly outside active P01-P06 acceptance; remains optional. |

## Findings

<!-- rpi:review id=RV-001 -->
### RV-001 [High]: Read-only takeover of an ambiguous YouTube promotion cannot converge

* Related scope: P01-T02, P01-T03, P02-T01, P02-T03, P04-T01
* Evidence: `podcaster/distribution_worker.py` attempts to persist and consume a new `public_promotion` intent whenever a known video is processed but non-public. A takeover after the original promotion intent was consumed is correctly marked read-only by `podcaster/distribution_outbox.py`, so this path raises `StaleClaimError` instead of recording authoritative readback, `publication_unknown`, manual handoff, or a bounded next reconciliation. A direct fault reproduction produced `REPRODUCED: takeover claim is reconciliation-only`.
* Impact: The safety fence prevents a duplicate promotion, but an ambiguous promotion can cycle through failed ACA executions without the durable actionable convergence required by the plan.
* Destination: `rpi-implement`
* Smallest useful next action: Make the read-only YouTube path branch before intent persistence, perform bounded readback only, and persist public, pending-with-deduplicated-schedule, or unknown/manual terminal evidence with tests for lost promotion response and expired takeover.

<!-- rpi:review id=RV-002 -->
### RV-002 [High]: Reconciliation scheduling is neither deduplicated nor starvation-safe

* Related scope: P01-T03, P03-T02, P04-T01
* Evidence: `podcaster/distribution_scheduler.py` enqueues every due outbox ID on every five-minute run while its active token remains present; no durable scheduled/sent marker prevents repeat queue messages. `DistributionOutboxRepository.due_reconciliations()` calls `list_blobs(..., limit=100)` with no continuation or cursor, so due records outside the stable first 100 blob names can be ignored indefinitely as retained records accumulate. The only scheduler test covers one invocation with three returned rows.
* Impact: A delayed worker can receive unbounded duplicate hints, while valid due reconciliation work can permanently starve. This violates the bounded, deduplicated, restart-safe progression contract.
* Destination: `rpi-implement`
* Smallest useful next action: Add durable conditional token-notification state and paginated/cursored bounded scanning with fairness, then test repeated scheduler ticks, more than 100 retained records, restart recovery, and stale notification repair.

<!-- rpi:review id=RV-003 -->
### RV-003 [High]: Deployed alert rules do not implement the required operational contract

* Related scope: P03-T03, P04-T02, P05-T05
* Evidence: `infra/modules/distribution-alerts.bicep` filters every rule to log rows containing `"severity": "critical"`. Warning conditions documented in `docs/ops/distribution-terminal-truth.md` therefore never alert, including pending age over 15 minutes, manual handoff under 24 hours, YouTube non-public over 15 minutes, Spotify draft over 15 minutes, verification lag over 15 minutes, and claim latency. Every rule also uses the same 10-minute window, no missing-data query exists, and route labels only affect description text while all rules share one optional action group.
* Impact: Required warning and missing-telemetry conditions are operationally invisible, and configured routes/windows do not match the accepted alert table. Production canary cannot credibly prove alert fire/clear behavior from this infrastructure.
* Destination: `rpi-implement`
* Smallest useful next action: Encode warning and critical thresholds/windows per signal, authoritative outbox-depth/heartbeat missing-data rules, explicit action-group routing, and Bicep/deployment tests that inspect each generated query and fire/clear contract.

<!-- rpi:review id=RV-004 -->
### RV-004 [Medium]: Immutable artifact orphan repair and retention are claimed but not implemented

* Related scope: P01-T01, P01-T02, P03-T02, P04-T01
* Evidence: `commit_immutable_artifact()` uploads and verifies before outbox creation, but `podcaster/distribution_outbox.py` has no orphan inventory, retention timestamp policy, garbage-collection transition, or deletion path. The test suite covers lost queue notification, not interruption after artifact upload before authoritative outbox creation. The changes record nevertheless claims orphan repair and retention-compatible completion.
* Impact: Crashes in the required upload-before-outbox window leak artifacts indefinitely and the plan's deterministic interruption/cleanup acceptance is unproven.
* Destination: `rpi-implement`
* Smallest useful next action: Add bounded orphan discovery keyed by content-addressed artifact reference and retention age, deterministic repair-or-delete behavior, and interruption tests before/after verification and conditional outbox creation.

<!-- rpi:review id=RV-005 -->
### RV-005 [Medium]: Ambiguous YouTube upload is labeled identity-bound without an identity-bound provider read

* Related scope: P02-T01, P02-T03, P04-T02
* Evidence: When a consumed draft upload has no provider item ID, the read-only path in `podcaster/distribution_worker.py` writes `publication_unknown` with source `identity_bound_reconciliation` but performs no YouTube listing/search/readback and stores no provider-supported immutable identity or precondition fingerprint. The worker test verifies only one upload call and the durable unknown result.
* Impact: The result is safely non-success and does not blindly retry, but the evidence source overstates what was observed and the required bounded reconciliation attempt is absent.
* Destination: `rpi-implement`
* Smallest useful next action: Either perform a genuinely bounded provider-supported identity lookup tied to durable canonical metadata, or record that identity is unprovable and transition to accurately named unknown/manual evidence without claiming reconciliation.

<!-- rpi:review id=RV-006 -->
### RV-006 [High]: The current PR #682 safety inventory has advanced beyond the authoritative plan matrix

* Related scope: P03-T02, P05-T03, plan PR #682 Thread Closure Matrix
* Evidence: Current GitHub GraphQL evidence for `jmservera/SquadScope-Podcaster#682` reports 22 review threads, 16 resolved and 6 unresolved. The six unresolved threads concern mutation before lease, swallowed admission exceptions, recorder timeout/visibility mismatch, unbounded fallback storage work, pre-budget storage work, and synchronous full-file hashing. They are not the W17-W29 rows locked from the earlier research inventory.
* Impact: The plan cannot yet demonstrate that every relevant current safety concern is ported, rejected with evidence, or superseded. P05-T03 has an incomplete authoritative closure set even before delivery starts.
* Destination: `rpi-plan`
* Smallest useful next action: Reconcile the six current unresolved thread URLs into the closure matrix, map each to replacement code/test or explicit non-port rationale, and update P05-T03 acceptance without marking the threads resolved prematurely.

## Defects

* RV-001, RV-002, RV-003, RV-004, and RV-005 are implementation defects routed to `rpi-implement`.
* No critical-severity defect was found. At-most-once mutation fencing remained fail-closed in the reproduced takeover scenario.

## Routed Findings

| Finding | Destination | Owner or next action | Reason for route |
|---|---|---|---|
| RV-001 | `rpi-implement` | Implement read-only promotion convergence and fault tests | Accepted design, implementation defect |
| RV-002 | `rpi-implement` | Implement durable deduplicated fair scheduling | Accepted design, implementation defect |
| RV-003 | `rpi-implement` | Implement the exact deployable alert contract | Accepted design, implementation defect |
| RV-004 | `rpi-implement` | Implement bounded orphan retention/cleanup | Accepted design, implementation defect |
| RV-005 | `rpi-implement` | Implement real bounded identity reconciliation or accurate unprovable evidence | Accepted design, implementation defect |
| RV-006 | `rpi-plan` | Expand the current #682 closure matrix and P05-T03 gate | Material current-state planning gap |

Later implementation of a routed finding does not require another Review.

## Residual Work

* P05-T01 through P06-T02 remain active plan work, not review defects: push/replacement PR, final-SHA checks, #682 supersession, merge/image provenance, canary/rollback, and four consecutive production weeks.
* `jmservera/SquadScope-Podcaster#681` remains open and has no replacement PR/canary/four-week closure evidence; this is correctly deferred to P06-T02.
* `jmservera/SquadScope-Coordinator#17` exists and remains open. The exact upstream SquadScope PR link is not established in the reviewed evidence and remains P05-T01 metadata discovery.
* The two plan follow-up items remain distinct future work as assessed above.

## Blockers and Remaining Work

* Blockers: P05 delivery requires repository mutation/approval authority; P05-T05 requires deployment authority and real provider credentials; P06 requires four consecutive elapsed production weeks.
* Remaining active work: P05-T01 through P06-T02.
* Review blocker status: None. Evidence was sufficient for a complete review verdict.

## Validation Evidence

| Command | Scope | Status | Summary |
|---|---|---|---|
| `pytest -q tests/test_distribution_outbox.py tests/test_distribution_worker.py tests/test_distribution_telemetry.py tests/test_video_job_runner.py::test_outcomes_exit_code_requires_nonempty_all_completed` | Focused outbox/provider/telemetry/exit behavior | Passed | `29 passed in 0.59s`. |
| Direct Python promotion-takeover fault reproduction | Consumed YouTube promotion, expired lease, read-only takeover | Failed behavior reproduced | Raised `StaleClaimError: takeover claim is reconciliation-only` before a durable convergence result, supporting RV-001. |
| `pytest tests/ -q` | Full repository | Passed | `3059 passed, 2 skipped, 2 deselected, 1 warning in 103.24s`. |
| `python3 -m compileall -q podcaster` | Production Python | Passed | No compile failures. |
| `ruff check podcaster tests --quiet` | Production and tests | Passed | No lint findings. |
| `ruff format --check podcaster tests --quiet` | Production and tests | Passed | Formatting check passed. |
| `git diff --check` and deleted-test query | Worktree safety | Passed | No whitespace errors and no deleted test files. |
| `az bicep build --file infra/main.bicep --stdout >/dev/null` | Infrastructure | Passed with existing warning | Build passed; existing BCP318 warning remains. |
| `checkov --directory infra --framework bicep --quiet` | Exact unskipped infrastructure security scan | Failed baseline | 36 passed, 7 failed; failures match the documented pre-existing ACR/storage/OpenAI baseline. No gate was weakened to hide them. |
| `docker image inspect podcaster-synthesis:ci --format '{{.Id}}'` | Claimed local image | Passed | Exact digest `sha256:02bb1d7b7852cdb748125bbafe3d9572e8732c45fb75e26f39d22b273c62b2a9`. |
| `git rev-parse origin/main`, `git rev-parse HEAD` | Baseline | Passed | Both resolve to `0752d1a8118027a90172a0af88cf7b51e4d9cfeb`; implementation is an uncommitted worktree delta. |
| GitHub read-only queries | #681, #682, Coordinator #17 | Available | #681 open; #682 open/blocked with 22 threads, 16 resolved and 6 unresolved; Coordinator #17 open. |
| Changes-record targeted `571 passed`, expanded `949 passed, 1 skipped, 2 deselected`, and scale-out `1 passed` | Author-recorded validation | Available, not independently rerun in this review | Consistent with the independently reproduced full suite and static/build evidence; retained as implementation evidence rather than re-labeled as independently executed. |

## Outcome

* Outcome: Not accepted
* Outcome rationale: Implementation execution is correctly Partial, and P05-P06 are legitimately open. However, five implementation defects include three high-severity gaps in provider takeover convergence, reconciliation scheduling, and alert operations, while a high-severity current-state planning gap leaves six current #682 safety threads outside the closure matrix. The full green test suite and valid image digest do not satisfy the authoritative outcomes while these gaps remain.

## Closeout Routing Record

| Finding class | Destination | Owner or next action |
|---|---|---|
| Implementation defect | `rpi-implement` | Resolve RV-001 through RV-005 in the accepted design direction. |
| Decision gap or invalid assumption | `rpi-plan` | Resolve RV-006 by updating the current #682 closure inventory and P05-T03 gate. |
| Material evidence gap | None | No separate `rpi-research` route is required; current code and GitHub evidence are sufficient. |
| Non-blocking residual work | Existing P05-P06 and plan follow-ups | Active `rpi-quick` parent continues only after routed defects/plan gap are incorporated; external gates remain distinct. |

* Review execution status: Complete
* Assessed implementation execution status: Partial
* Outcome: Not accepted
* Severity summary: 0 Critical, 4 High, 2 Medium, 0 Low
* Validation coverage: Focused tests, direct fault reproduction, full pytest, compile, Ruff, diff safety, Bicep, exact Checkov baseline, image digest, baseline SHA, and current GitHub state
* Blockers: External P05-P06 dependencies remain; no blocker prevented this review verdict.

## Relevant Artifacts

| Artifact | Description |
|---|---|
| [.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md](.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md) | Authoritative plan, markers, acceptance criteria, matrices, and follow-up items |
| [.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md](.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md) | Phase/task semantics and completion expectations |
| [.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md](.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md) | Research inventory and original #682 evidence set |
| [.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md](.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md) | Single plan critique and required corrections |
| [.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md](.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md) | Implementation claims, validation record, blockers, and remaining work |
| [.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md](.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md) | Canonical review result and routed finding set |

## Next Steps

Return this record to the active `rpi-quick` parent. Route RV-006 to `rpi-plan`, then RV-001 through RV-005 to `rpi-implement`; preserve P05-P06 as externally gated active work and do not require a second Review by protocol.
