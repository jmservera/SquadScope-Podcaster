<!-- markdownlint-disable-file -->
# Review: Production Provider Terminal Truth

## Scope and Evidence

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Review date: 2026-09-21
* Final-revision review date: 2026-09-22
* Final revision reviewed: `02241a1707c8a5d2a17120185e988634de188d21`
* Final-revision reviewer: Fry (QA / Tester), independent of sole revision author Leela; Bender, Hermes, and Amy did not participate
* Review scope: One independent full-task review of Amy's current revision, including committed and uncommitted worktree state
* Assessed boundary: Immutable attempt truth; deterministic weekly aggregation; exact provider proof; controlled recovery; unknown-mutation safety; RV-002 scheduler fairness/deduplication; RV-003 alert deployment; RV-004 cleanup; RV-007 terminology/tracking consistency; W38/W39 fixtures; four-cycle evaluation; validation; and residual P00-T01/P05/P06 work
* Plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
* Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
* Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`
* Changes: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
* Other evidence considered: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`; `.copilot-tracking/pr/pr.md`; complete `origin/main` delta; current uncommitted author corrections; source, tests, Bicep, runbook, and independently reproduced validation below

## Opening Review State

* Interpreted review goal: Independently assess Amy's current immutable-attempt and weekly terminal-truth revision, transparently update prior RV dispositions, and distinguish in-repository conformance from upstream, delivery, and elapsed-cycle residual work.
* Review scope: Full current task boundary and `origin/main` delta, including the author's uncommitted correction state, without a second review pass.
* Evidence readiness: One unambiguous artifact set and one canonical review record exist. Plan, phase details, research, changes, historical critique, source, tests, runbook, infrastructure, and prior review state are available locally.
* Acceptance basis: The caller's authoritative QA gate; current plan requirements and acceptance criteria; historical PC-001-PC-009 dispositions; prior RV-001-RV-007 dispositions; exact provider proof, recovery, retry-safety, fairness, alert, cleanup, terminology, W38/W39, and four-cycle contracts.
* First comparison boundary: Inspect the actual `origin/main` implementation delta and current uncommitted author corrections for immutable attempts, weekly aggregation, exact proof, controlled recovery, and mutation safety before reassessing RV-002/RV-003/RV-004/RV-007 and validation.
* Active read-only boundaries: Source, tests, docs, plan, details, research, critique, changes, git, GitHub, and deployment state are read-only. This review record is the only writable artifact.
* Initial blockers: P00-T01 requires an owning `jmservera/SquadScope` change. P05 requires repository/delivery/deployment authority. P06 requires four elapsed production cycles.

## Execution Status

* Review execution status: Complete
* Assessed implementation execution status: Complete for Amy's declared in-repository P01-P04/RV correction scope
* Review execution evidence: Actual source and test behavior were inspected and probed; focused, locked, full, compile, Ruff, format, Bicep, diff, and Checkov validation were independently run.
* Scope boundary: P00-T01, P05, and P06 did not execute and remain distinct external or elapsed residual work, not incomplete Amy execution.

## Plan-to-Change Reconciliation

| Current plan scope | Descriptive changes-record summary | Current-state reconciliation | Gap or rationale |
|---|---|---|---|
| Immutable attempts and weekly aggregation | Append-only attempts plus separate weekly decision | Reconciled in structure and preservation behavior | Failed attempt identity, outcome, events, and evidence survive a later authorized attempt. |
| Exact green proof | Week, manifest, publication digest, canonical artifact, expected provider item, terminal readback, and no duplicate ambiguity | Missing | The implementation defaults omitted proof fields to success and accepts any non-empty provider item when no expected identity was persisted. See RV-008. |
| Controlled recovery | Failed/non-green predecessor plus distinct authorized success | Partial | A distinct attempt and predecessor are retained, but authorization is a caller-supplied `no_mutation_proven=True` assertion without durable proof, and W38-style fixtures can derive recovered green from labels alone. See RV-008. |
| Unknown mutation safety | No blind retry after `provider_unknown` | Reconciled | `authorize_recovery()` rejects a `provider_unknown` predecessor; read-only reconciliation remains the safe path. |
| RV-002 scheduler | Paginated fairness and notification deduplication | Partial | Continuation reaches records beyond 5,000, but selection and sent-marking are not atomic across concurrent scheduler runs. See RV-002. |
| RV-003 alerts | Distinct routes and executable missing-data/non-green rules | Partial | Route-specific action groups and scheduler absence query exist, but weekly critical rows use an event name that deployed weekly rules do not query. Active-depth semantics also alert on any active depth, not missing state. See RV-003. |
| RV-004 cleanup | Bounded, reference-safe, scalable cleanup | Partial | Pagination resolves the fixed 5,000 cap, but every run scans the complete outbox into an unbounded reference set and deletion is not protected from a concurrent new reference. See RV-004. |
| RV-007 consistency | Source/docs/tests/runbook/tracking terminology and dispositions | Partial | Terminology is substantially aligned, but plan/details/changes claim RV-002/RV-003/RV-004 complete and record a locked count of 754 while the current command collects 756. See RV-007. |
| W38/W39 | W38 evidence-conditional; W39 missed/not-dispatched | Partial | Narrative is correct, but executable W38-style fixtures permit recovered green without the required exact proof. W39 remains correctly `missed_not_dispatched`. |
| Four-cycle gate | Exactly four fully proven green cycles | Missing | `four_cycle_acceptance()` trusts weekly labels only and accepts documents with no provider proof or external readback. See RV-009. |
| P00-T01/P05/P06 | Upstream prevention, delivery/canary, four elapsed cycles | Correctly open | External residual work; no deployment or production acceptance is claimed. |

## Completed Work Assessment

| Related marker | Files | What changed and why | Completion evidence | Validation | Assessment |
|---|---|---|---|---|---|
| Attempt truth | `podcaster/distribution_outbox.py`, `tests/test_distribution_outbox.py` | Added attempt ledger, events, predecessor/authz links, and separate aggregation | Failed attempt equality and references survive recovery | Focused and full suites passed | Conformant for immutable preservation. |
| Unknown mutation | `podcaster/distribution_outbox.py` | Prevented recovery authorization from a `provider_unknown` predecessor | Independent inspection and existing test | Passed | Conformant. |
| RV-002 paging | `podcaster/storage.py`, `podcaster/distribution_outbox.py`, scheduler/tests | Added continuation-based scanning beyond 5,000 | Beyond-5,000 test passed | Passed | Prior fixed-window starvation defect resolved; concurrent dedup defect remains. |
| RV-003 routing | `infra/main.bicep`, `infra/modules/distribution-alerts.bicep`, telemetry/tests | Added route-specific action-group parameters and scheduled-query rules | Bicep and Checkov gate passed | Passed syntactically | Route wiring improved; weekly alert mismatch remains functional. |
| RV-004 pagination | outbox/storage/tests | Added complete reference pagination and metadata cursor | Pagination tests passed | Passed | Fixed-cap liveness improved; boundedness and concurrent reference safety remain incomplete. |
| RV-007 terminology | runbook, plan, details, changes, tests | Aligned attempt/weekly, W38/W39, and four-cycle vocabulary | Text comparison | No terminology contradiction found in source/runbook | Tracking completion and validation claims remain stale. |

## Implementation-Time Plan and Detail Update Assessment

| Affected area or marker | What changed and why | Triggering evidence and user decision | Reconciliation performed | Planning and critique state | Assessment |
|---|---|---|---|---|---|
| Attempt/weekly state model | Added immutable attempts and weekly aggregation | Authoritative QA gate | Plan, details, changes, runbook, source, and tests updated | Historical critique preserved | Direction is reconciled; implementation proof gate is incomplete. |
| RV-002/RV-003/RV-004 | Marked complete after Amy's correction | Prior review findings | Source/tests/tracking updated | No second critique required | Completion claims overstate behavior; findings remain open in narrowed current form. |
| RV-007 | Marked reconciled except PR body | Authoritative consistency requirement | Source/docs/tests/runbook/tracking updated | PR remains P05 | Terminology is improved, but current dispositions/counts are inconsistent. |
| W38/W39 | W38 changed to evidence-conditional candidate; W39 retained as missed/not-dispatched | Authoritative caller correction | Narrative artifacts updated | Historical critique unchanged | Narrative is correct; W38 fixture behavior is not. |

## Critique and Material Revision Assessment

* Latest critique dispositions: PC-001-PC-009 remain historical and were not mutated or repeated.
* Material revisions: The dual-level attempt/weekly model, exact proof requirement, W38/W39 correction, pagination, alert routing, and cleanup revisions preserve confirmed intent.
* Dependent-work pause assessment: P05 and P06 remain open, and no deployment acceptance is claimed.
* Justification assessment: Revision direction is supported, but current completion claims are not supported for RV-002/RV-003/RV-004/RV-007 or the exact green/four-cycle gates.

## Plan Follow-Up Assessment

| Follow-up item | Why outside immediate scope | Owner or next action | Assessment and route |
|---|---|---|---|
| Future authoritative Spotify mutation/idempotency support | Provider contract remains unsupported | Distinct provider-capability follow-up | Properly outside current acceptance. |
| Optional W38 deployed-image/revision forensics | Historical causal reconstruction does not establish current exact proof | Distinct incident-forensics follow-up | Optional; must not manufacture recovered-green evidence. |
| P00-T01 upstream prevention | Owning code is in `jmservera/SquadScope` | Upstream owner | External residual work. |
| P05 delivery/canary | Requires git/GitHub/deployment/provider authority | Delivery owner | External residual work. |
| P06 four elapsed cycles | Requires future calendar cycles and production evidence | Production verification owner | Elapsed residual work. |

## Findings

<!-- rpi:review id=RV-001 -->
### RV-001 [High, resolved]: Read-only YouTube promotion takeover convergence

* Related scope: P02-T01, P02-T03
* Evidence: Known-identity promotion takeover performs authoritative readback and does not issue a second mutation.
* Impact: Prior duplicate-promotion/stale-claim risk remains closed.
* Destination: Resolved in current implementation.
* Disposition: Resolved; retain regression coverage.

<!-- rpi:review id=RV-002 -->
### RV-002 [High, remains open]: Scheduler notification deduplication is not atomic across concurrent runs

* Related scope: P01-T03, P03-T02, P04
* Evidence: `due_reconciliations_page()` only reads due state. `distribution_scheduler.run_once()` enqueues first and calls `mark_reconciliation_notified()` afterward. Two independent pre-mark scans returned the identical outbox/provider/token, so concurrent schedulers can both enqueue the same reconciliation. Continuation paging does reach records beyond 5,000.
* Impact: Duplicate work can race provider reconciliation and violates the required bounded-concurrency deduplication contract.
* Destination: `rpi-implement`
* Smallest useful next action: Add a fenced/CAS notification reservation or equivalent durable single-winner transition before enqueue, with a concurrent two-run test and stale-reservation recovery.

<!-- rpi:review id=RV-003 -->
### RV-003 [High, remains open]: Deployed weekly non-green alerts do not match emitted telemetry

* Related scope: P03-T03, P04-T02, P05-T05
* Evidence: `signal_rows()` emits `distribution_identity_conflict` and `distribution_weekly_non_green` with event `distribution_provider_state`; `distribution-alerts.bicep` queries event `distribution_weekly_state` for both rules. Independent output confirmed the mismatched event. The active-depth rule also matches every warning active-depth row rather than detecting active depth without state rows.
* Impact: Required identity-conflict and weekly non-green critical alerts cannot fire from actual emitted rows, and the active-depth rule does not implement its documented missing-state condition.
* Destination: `rpi-implement`
* Smallest useful next action: Align emitted/query event contracts, implement the active-depth-without-state join/absence condition, and add tests that evaluate representative emitted rows against generated rule queries.

<!-- rpi:review id=RV-004 -->
### RV-004 [Medium, remains open]: Cleanup pagination is complete but not bounded or concurrency-safe

* Related scope: P01-T02, P03-T02, P04
* Evidence: `cleanup_orphan_artifacts()` paginates every outbox record and retains every referenced artifact path in one in-memory set before deleting metadata-page candidates. No durable reference index, snapshot, or conditional deletion prevents an artifact from becoming referenced after the scan and before deletion.
* Impact: Work and memory grow with the complete retained outbox corpus, and a concurrent enqueue can create a new reference after the scan, violating the required bounded/reference-safe cleanup contract.
* Destination: `rpi-implement`
* Smallest useful next action: Use a durable reference index/refcount or bounded snapshot/mark-and-sweep generation with conditional deletion, plus scale and concurrent-reference tests.

<!-- rpi:review id=RV-005 -->
### RV-005 [Medium, resolved]: Ambiguous upload evidence remains accurately fail-closed

* Related scope: P02-T01, P02-T03
* Evidence: Missing provider identity remains `youtube_identity_unprovable`/non-green rather than being described as identity-bound readback.
* Impact: Durable evidence does not invent provider observations.
* Destination: Resolved in current implementation.
* Disposition: Resolved; retain fail-closed behavior.

<!-- rpi:review id=RV-006 -->
### RV-006 [High, resolved at planning level]: PR #682 closure inventory

* Related scope: P05-T03
* Evidence: The plan retains the historical and current review-thread closure matrix without claiming GitHub resolution.
* Impact: Planning coverage is present; actual replies/resolution remain delivery work.
* Destination: Residual P05-T03 execution.
* Disposition: Planning-level finding remains resolved.

<!-- rpi:review id=RV-007 -->
### RV-007 [Medium, remains open]: Canonical tracking overstates resolved findings and current validation counts

* Related scope: Source/docs/tests/runbook/tracking consistency and P05 handoff
* Evidence: Phase details and plan state that RV-002/RV-003/RV-004 are complete, but this review reproduces current defects. The changes record says the locked contract is 754 tests, while the exact current command passes 756. The PR body remains intentionally outside Amy's scope.
* Impact: A delivery owner could publish incorrect dispositions and validation evidence.
* Destination: `rpi-plan` for canonical tracking reconciliation; P05 for PR narrative.
* Smallest useful next action: After implementation corrections, update plan/details/changes with the exact dispositions and current command outputs; rewrite the PR narrative during P05 without claiming deployment acceptance.

<!-- rpi:review id=RV-008 -->
### RV-008 [High]: Exact provider proof and controlled-recovery authorization are not enforced

* Related scope: P01-P04 exact green gate; W38 fixture; controlled recovery
* Evidence: `_verification_proof()` treats omitted week, manifest, publication digest, and artifact digest as matches; defaults canonical selection from the local record; and accepts any observed provider item when no expected provider ID exists. An independent probe with no intent/receipt/expected provider ID and no supplied proof returned all proof fields green and produced `published_verified`. `authorize_recovery()` persists only a caller assertion `no_mutation_proven=True`, not durable safety evidence. `weekly_state_from_attempts()` derives `published_verified_recovered` from outcome labels without authorization or exact proof.
* Impact: W38 or any week can be classified green/recovered without the exact identity, expected provider item, duplicate-resolution, and proven-safe authorization required by the authoritative gate.
* Destination: `rpi-implement`
* Smallest useful next action: Require explicit identity-bound proof values, a persisted expected provider identity, authoritative readback source/state, explicit duplicate reconciliation, and durable authorization proof references; make label-only W38/recovery fixtures non-green.

<!-- rpi:review id=RV-009 -->
### RV-009 [High]: Four-cycle acceptance trusts weekly labels without external proof

* Related scope: P06 evaluator and acceptance gate
* Evidence: `four_cycle_acceptance()` checks only that four `weekly_aggregation.state` values are green. An independent probe of four label-only dictionaries returned `True`; no attempts, publication identity, manifest/digest, canonical artifact, provider item, duplicate resolution, aggregate provider state, or external readback was present.
* Impact: The production acceptance evaluator can accept four cycles containing missing external readback or other absent proof merely because their labels are green.
* Destination: `rpi-implement`
* Smallest useful next action: Evaluate each cycle's complete proof envelope and reject missing/partial/unknown/manual/identity-conflict/duplicate-ambiguous/no-readback evidence, with a parameterized negative matrix.

## Defects

* High: RV-002, RV-003, RV-008, RV-009.
* Medium: RV-004, RV-007.
* Resolved: RV-001 and RV-005.
* Resolved at planning level with external execution remaining: RV-006.
* No critical defect was identified.

## Routed Findings

| Finding | Destination | Owner or next action | Reason for route |
|---|---|---|---|
| RV-002 | `rpi-implement` | Add atomic scheduler reservation/deduplication | In-repository implementation defect |
| RV-003 | `rpi-implement` | Align telemetry/query contracts and absence semantics | In-repository implementation defect |
| RV-004 | `rpi-implement` | Make cleanup bounded and concurrent-reference-safe | In-repository implementation defect |
| RV-007 | `rpi-plan` plus P05 handoff | Reconcile tracking and PR narrative | Canonical artifact/handoff gap |
| RV-008 | `rpi-implement` | Enforce exact proof and durable recovery authorization | In-repository acceptance defect |
| RV-009 | `rpi-implement` | Validate complete proof per four-cycle row | In-repository acceptance defect |

Later implementation of routed findings does not require another Review.

## Residual Work

* P00-T01 remains cross-repository residual work owned by `jmservera/SquadScope`.
* P05 remains external delivery work: commit/push/PR, final-SHA independent review, checks, approval/merge, release provenance, deployment, canary, alert fire/clear, and rollback evidence.
* P06 remains elapsed production work: four consecutive future post-fix cycles with full upstream, Azure, attempt, aggregation, and provider-readback evidence.
* These residual items do not reduce the severity of current in-repository defects and do not constitute deployment acceptance.

## Blockers and Remaining Work

* Blockers: P00-T01 upstream ownership; P05 repository/deployment/provider authority; P06 elapsed calendar cycles.
* Remaining implementation defects: RV-002, RV-003, RV-004, RV-008, RV-009.
* Remaining planning/handoff reconciliation: RV-007.
* Remaining external work: P00-T01, P05, P06.

## Validation Evidence

| Command | Scope | Status | Summary |
|---|---|---|---|
| Focused correction command | Outbox/worker/telemetry/deploy | Passed | `81 passed in 2.03s`. |
| Locked contract command | Dispatch/API/outbox/worker/provider/publication/monitoring/deployment | Passed with current count | `756 passed, 1 warning in 55.69s`; current worktree count differs from the recorded 754. |
| Initial `pytest tests/ -q` | Full repository | Failed from stale Compose image | `1 failed, 3085 passed, 2 skipped, 2 deselected`; missing fanout clip. |
| `docker compose -f docker-compose.fanout.yml build --quiet` plus focused integration | Existing scale-out image | Passed | Rebuilt existing test image; integration `1 passed in 29.65s`. |
| Final `pytest tests/ -q` | Full repository | Passed | `3086 passed, 2 skipped, 2 deselected, 1 warning in 85.64s`. |
| `python3 -m compileall -q podcaster` | Production Python | Passed | No compile failures. |
| `ruff check podcaster tests --quiet` | Production and tests | Passed | No lint findings. |
| `ruff format --check podcaster tests --quiet` | Production and tests | Passed | Formatting check passed. |
| `az bicep build --file infra/main.bicep --stdout >/dev/null` | Infrastructure | Passed with existing warning | Existing BCP318 warning only. |
| `git diff --check` and deleted-file query | Worktree safety | Passed | No whitespace errors; no deleted files in the implementation delta. |
| Exact Checkov | Infrastructure baseline | Baseline retained | `36 passed, 7 failed`; failures are the documented ACR/storage/OpenAI baseline. |
| CI-equivalent Checkov skip-list gate | Blocking infrastructure policy | Passed | `34 passed, 0 failed`. |
| Exact-proof independent probe | Green provider gate | Failed | Missing explicit proof and expected IDs still produced all-green proof and `published_verified`. |
| Concurrent scheduler pre-mark probe | RV-002 deduplication | Failed | Two scans selected the identical outbox/provider/token. |
| Weekly telemetry/query probe | RV-003 alert contract | Failed | Emitted event was `distribution_provider_state`; deployed weekly rule expects `distribution_weekly_state`. |
| Label-only four-cycle probe | P06 evaluator | Failed | Four green labels with no external evidence returned `True`. |

## Unsafe-Narrowing Assessment

* Truthful non-green exits remain preserved in worker aggregation.
* Immutable attempt preservation is implemented.
* Unknown mutations remain blocked from blind recovery authorization.
* Provider green proof is unsafely broadened by inferred/omitted fields; RV-008 is material.
* Four-cycle acceptance is unsafely narrowed to labels; RV-009 is material.
* CI/security gates were not weakened.
* No secret/PII regression was identified in the reviewed boundary.

## PR Narrative Changes Required

Before `.copilot-tracking/pr/pr.md` is used:

1. State review execution `Complete`, implementation execution `Complete for Amy's declared in-repository scope`, and outcome `Not accepted`.
2. List exact dispositions: RV-001 resolved; RV-002 high open; RV-003 high open; RV-004 medium open; RV-005 resolved; RV-006 planning-level resolved with P05 execution open; RV-007 medium open; RV-008 high open; RV-009 high open.
3. Use independent validation: focused 81; locked 756/1 warning; final full 3086/2 skipped/2 deselected/1 warning after stale-image refresh; compile/Ruff/format/Bicep/diff passed; exact Checkov 36/7 baseline; gate 34/0.
4. Keep W38 evidence-conditional and explicitly state current fixtures do not yet enforce the full recovered-green proof contract. Keep W39 `missed_not_dispatched`.
5. State P00-T01 is owned by `jmservera/SquadScope`; P05 and P06 remain external/elapsed residual work.
6. Do not claim merge readiness, deployment readiness, canary acceptance, four-cycle completion, or production acceptance.

## Outcome

* Outcome: Not accepted
* Outcome rationale: Amy completed the declared in-repository execution and materially improved attempt preservation, paging, routing, and terminology. However, RV-002 and RV-003 remain high operational defects, RV-008 and RV-009 allow false green/recovered and four-cycle acceptance without exact external proof, RV-004 remains a boundedness/reference-safety defect, and RV-007 leaves canonical claims inaccurate. High-severity defects prevent acceptance.

## Closeout Routing Record

| Finding class | Destination | Owner or next action |
|---|---|---|
| Implementation defect | `rpi-implement` | Resolve RV-002, RV-003, RV-004, RV-008, and RV-009. |
| Decision/artifact gap | `rpi-plan` | Reconcile RV-007 after implementation, with PR narrative handled in P05. |
| Material evidence gap | None | Evidence is sufficient for this verdict. |
| Non-blocking residual work | P00-T01, P05, P06 | Upstream owner, delivery owner, and production verification owner respectively. |

* Review execution status: Complete
* Assessed implementation execution status: Complete for Amy's declared in-repository scope
* Outcome: Not accepted
* Severity summary: 0 Critical; 4 High open; 2 Medium open; 2 resolved prior findings; 1 planning-level prior finding resolved
* Validation coverage: Focused/locked/full pytest, compile, Ruff, format, diff safety, Bicep, exact and blocking Checkov, and independent negative probes
* Blockers: P00-T01 upstream ownership; P05 delivery/deployment authority; P06 elapsed cycles

## Relevant Artifacts

| Artifact | Description |
|---|---|
| [.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md](.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md) | Current requirements, acceptance gates, markers, and residual phases |
| [.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md](.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md) | Detailed state model and task contracts |
| [.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md](.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md) | W38/W39 and provider evidence research |
| [.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md](.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md) | Preserved historical critique |
| [.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md](.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md) | Amy's implementation and validation claims |
| [docs/ops/distribution-terminal-truth.md](docs/ops/distribution-terminal-truth.md) | Operational truth, alert, canary, and rollback contract |
| [.copilot-tracking/pr/pr.md](.copilot-tracking/pr/pr.md) | Draft PR narrative requiring the listed corrections |
| [.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md](.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md) | Canonical independent review result |

## Next Steps

Route RV-002, RV-003, RV-004, RV-008, and RV-009 to `/rpi-implement`; route RV-007 tracking reconciliation to `/rpi-plan` after those corrections. Separately clear P00-T01 in `jmservera/SquadScope` and retain P05/P06 as external residual work. Do not run another review.

---

## Fresh Independent Final-Revision Review — 2026-09-22

### Identity, Boundary, and Repository State

* Reviewer: **Fry, QA / Tester**.
* Independence: Fry did not author revision `02241a1`; Leela was the sole revision author. Bender, Hermes, Amy, and Leela did not advise, pair, or contribute to this review.
* Review boundary: exact committed delta `5cd84c4c29f7f597f0a5b03a2c23e5b79b5ed7f7..02241a1707c8a5d2a17120185e988634de188d21`.
* Ancestry: both `5cd84c4` and implementation commit `dd7b265` are ancestors of `02241a1`.
* Opening state: clean worktree; local branch and `origin/squad/incident-provider-terminal-truth` both at `02241a1`.
* PR state inspected: `jmservera/SquadScope-Podcaster#684` open, draft, mergeable, no reviews, no review threads, and checks still running at review start.
* Scope truth retained: W39 is `missed_not_dispatched`; W38 was published, but recovered-green requires exact identity-bound authoritative provider readback and durable recovery authorization.

### Final Verdict

**Not accepted (`request_changes`).** RV-003 is resolved and RV-007 is reconciled by this review-only update. RV-002, RV-004, RV-008, and RV-009 remain implementation findings. RV-002, RV-008, and RV-009 remain High; RV-004 is escalated from Medium to High because the new reference index can delete an artifact still referenced by a pre-index outbox record. PR #684 must remain draft and blocked.

### Final Finding Dispositions

<!-- rpi:review-final id=RV-002 -->
#### RV-002 [High, remains open]: an expired `enqueue_started` reservation is permanently hidden from scheduler recovery

* Evidence: `due_reconciliations_page()` treats every `notification_reservation.stage == "enqueue_started"` as fresh without considering `lease_expires_at`. The independent fake-clock probe advanced beyond the lease and still returned no due reconciliation.
* Impact: a crash or ambiguous exception after `begin_reconciliation_enqueue()` but before completion can strand the reconciliation token indefinitely. Concurrent initial reservation is single-winner, but the required bounded abandoned-reservation recovery is not satisfied.

<!-- rpi:review-final id=RV-003 -->
#### RV-003 [High, resolved]: emitted weekly events and deployed alert queries now agree

* Evidence: representative identity-conflict and weekly-non-green rows emitted `distribution_provider_state`; both Bicep rules query that event; the stale `distribution_weekly_state` literal is absent. The active-depth rule now requires active depth and zero provider-state rows in the same window.
* Disposition: Resolved in `02241a1`; retain executable row/query and fire/clear regression coverage.

<!-- rpi:review-final id=RV-004 -->
#### RV-004 [High, remains open; escalated]: cleanup can delete artifacts referenced by pre-index outbox records

* Evidence: cleanup now trusts only `distribution-artifact-references/<digest>.json`. There is no migration or fallback that registers artifacts already referenced by outbox records created before this index existed. The independent probe retained an outbox record, removed only its reference-index document to model the deployed pre-index state, aged the metadata, and cleanup reported `removed=1` with `artifact_exists=False`.
* Impact: rollout against existing durable records can delete a live referenced artifact. Per-run budgets and the new-reference CAS fence work for records created under the new schema, but backward-compatible reference safety is not satisfied.

<!-- rpi:review-final id=RV-007 -->
#### RV-007 [Medium, resolved by review reconciliation]: canonical review state and counts are current

* Evidence: this artifact, plan, phase details, changes record, and PR #684 now report the same final-SHA verdict, dispositions, `83` focused tests, final `3088 passed, 2 skipped, 2 deselected, 1 warning`, and retained P00-T01/P05/P06 blockers.
* Disposition: Resolved as a tracking-only review update; no source or test was altered.

<!-- rpi:review-final id=RV-008 -->
#### RV-008 [High, remains open]: recovery classification still accepts label-only/opaque authorization evidence

* Evidence: exact `record_verification()` proof now fails closed, and unknown/ambiguous predecessor evidence is rejected. However, public `weekly_state_from_attempts()` still returns `published_verified_recovered` from only `terminal_outcome="published_verified"`, `proof_complete=True`, and an arbitrary non-empty `recovery_evidence_reference`. `authorize_recovery()` likewise validates an opaque token and a source enum rather than a durable evidence object bound to the predecessor/provider identity.
* Impact: W38-style data can still become recovered green without the exact provider identity/readback and authorization proof required by the authoritative gate.

<!-- rpi:review-final id=RV-009 -->
#### RV-009 [High, remains open]: four-cycle evaluation trusts persisted proof booleans after identity mutation

* Evidence: four label-only rows are now correctly rejected. However, `_authoritative_green_document()` trusts stored proof booleans and does not rebind them to raw proof values. Four complete synthetic envelopes passed; changing one cycle's `accepted_job_id` after proof creation still returned `True`.
* Impact: contradictory or corrupted persisted identity/proof envelopes can satisfy the four-cycle gate. The evaluator does not independently establish exact identity-bound external proof for each cycle.

### Independent Negative Probes

| Finding | Probe | Result |
|---|---|---|
| RV-002 | Two-thread reservation barrier from the owner suite | Passed: one reservation winner and one stale loser |
| RV-002 | Begin enqueue, advance fake clock beyond lease, query due work | **Failed closed-loop recovery:** returned `[]`; token remained stranded |
| RV-003 | Emit identity-conflict rows and compare event/metric to Bicep; check stale event absence and active-depth absence query | Passed |
| RV-004 | Bounded/page/restart/concurrent-new-reference owner tests | Passed for new-schema records |
| RV-004 | Model a retained pre-index outbox by removing only its reference-index row, age metadata, run cleanup | **Failed:** `removed=1`, referenced artifact deleted |
| RV-007 | Compare plan/details/changes/review/PR statuses and current command counts | Initially stale; reconciled in this review-only update |
| RV-008 | Parameterized missing/contradictory exact-verification owner tests | Passed: `identity_conflict` |
| RV-008 | Two attempts with only labels, `proof_complete=True`, and opaque evidence token | **Failed:** returned `published_verified_recovered` |
| RV-009 | Four label-only weekly rows | Passed: rejected |
| RV-009 | Four complete envelopes, then mutate one `accepted_job_id` without recomputing proof | **Failed:** still accepted |

### Independent Validation

| Command | Result |
|---|---|
| `TMPDIR="$PWD/.test-tmp" pytest tests/test_distribution_outbox.py tests/test_distribution_worker.py tests/test_distribution_telemetry.py tests/test_deploy_workflow.py -q` | `83 passed in 2.15s` |
| Initial `TMPDIR="$PWD/.test-tmp" pytest tests/ -q` | `1 failed, 3087 passed, 2 skipped, 2 deselected, 1 warning`; stale Compose recorder image reproduced |
| `docker compose -f docker-compose.fanout.yml build --quiet` then focused fanout test | `1 passed in 14.74s` |
| Final `TMPDIR="$PWD/.test-tmp" pytest tests/ -q` | `3088 passed, 2 skipped, 2 deselected, 1 warning in 82.22s` |
| `ruff check podcaster tests` | Passed |
| `ruff format --check podcaster tests` | Passed; `192 files already formatted` |
| `python3 -m compileall -q podcaster` | Passed |
| `git diff --check 5cd84c4..02241a1` | Passed |
| `az bicep build --file infra/main.bicep --stdout >/dev/null` | Passed with pre-existing BCP318 warning |
| `checkov --directory infra --framework bicep --quiet` | Baseline reproduced: `36 passed, 7 failed` |
| CI-equivalent Bicep Checkov skip-list command | `34 passed, 0 failed` |
| `docker build -f Containerfile -t podcaster-synthesis:fry-review . --quiet` | Passed; image ID `sha256:d35b69747463d706227b30dedeaf1ecc22bac8248c6fb45a30f45d7de2ee6291` |
| Container smoke | Passed: ffmpeg/ffprobe present, UID `999`, pipeline imports pass, unconfigured worker exits `2` |
| Changed-diff credential/secret pattern scan | No suspected secret, credential value, private key, signed URL, or raw PII found; matches were documentation/identifier terms and image hashes |

### GitHub and External Relationships

* `jmservera/SquadScope-Podcaster#684`: open and draft; no review threads; keep draft/blocked because High implementation findings and external gates remain.
* `jmservera/SquadScope-Podcaster#682`: open and non-draft; not superseded, closed, merged, or approved by this review. Its separate review history remains intact.
* `jmservera/SquadScope-Podcaster#671`: open; Spotify/manual-handoff provider dependency remains.
* `jmservera/SquadScope-Podcaster#678`: open; YouTube ambiguous-create identity reconciliation relationship remains.
* `jmservera/SquadScope-Podcaster#679`: open; Spotify exact reconciliation relationship remains.
* `jmservera/SquadScope-Podcaster#681`: open; atomic outbox work item remains the parent implementation relationship.

### Blockers and Clearing Evidence

| Blocker | Owner | Evidence required to clear |
|---|---|---|
| RV-002 | Podcaster revision owner | Expired/abandoned `enqueue_started` recovery that preserves single-winner semantics, with crash-before-send, ambiguous-send, stale-fence, and restart probes |
| RV-004 | Podcaster revision owner | Safe migration/backfill or fallback proof for every pre-index retained outbox artifact, plus rollout regression proving no referenced deletion |
| RV-008 | Podcaster revision owner | Recovery authorization bound to durable, validated predecessor/provider safety evidence; label-only helper path removed or made exact-proof aware |
| RV-009 | Podcaster revision owner | Four-cycle revalidation against immutable raw proof values/references so post-proof identity contradiction fails |
| P00-T01 | `jmservera/SquadScope` owner | Exact W39 upstream blocked-stage fix and durable dispatch-to-Azure evidence |
| P05 | Delivery/deployment owners | Accepted final-SHA review, all checks, approval/merge provenance, authorized deployment, provider canary, alert fire/clear, and rollback evidence |
| P06 | Production verification owner | Four consecutive future post-fix cycles with complete authoritative external proof |

No GitHub thread, issue, or related PR was resolved or closed by this review.

---

## Fresh Independent Farnsworth-Revision Review — Livingston — 2026-09-22

### Reviewer Identity, Independence, and Exact Boundary

* Reviewer: **Livingston, QA / Verification**.
* Independence: Livingston did not author the revision. Farnsworth was the sole revision author. Bender, Hermes, Amy, Leela, Fry, and Farnsworth did not advise, pair, or contribute during review.
* Exact comparison: `2e87d9bf2596df491494a3160b127e79e8f0f301..601d36afc62d745c6a67d917b63bcd89e8c18737`.
* Source focus: Farnsworth commit `0f489b12ae93b8e5f479fb9278f369f99e89190f`.
* Opening repository state: clean worktree; local and `origin/squad/incident-provider-terminal-truth` both at `601d36a`; comparison base is an ancestor; remote divergence `0/0`.
* PR state at opening: #684 open, draft, merge state `CLEAN`, 13 successful checks, no reviews, and no review threads.
* Incident truth retained: W39 is `missed_not_dispatched`. W38 was published, but `published_verified_recovered` remains evidence-conditional on exact weekly identity-bound authoritative provider proof.

### Livingston Final Verdict

**Not accepted (`request_changes`).** The Farnsworth revision resolves RV-002, RV-004, and RV-009, while RV-003 and RV-007 remain resolved. RV-008 remains **High** because recovery authorization can branch around a later `provider_unknown` attempt and restore mutation authority. Severity: 0 Critical, 1 High, 0 Medium, 0 Low.

### Finding Dispositions

<!-- rpi:review-livingston id=RV-002 -->
#### RV-002 [High, resolved]: expired notification reservations recover with one fenced owner

* Evidence: the two-thread barrier produced one winner and one stale loser; both expired `reserved` and `enqueue_started` stages became due; replacement incremented the fence; stale owners could neither abort nor complete the replacement.
* Disposition: Resolved. Retain exact fake-clock, concurrent-winner, and stale-owner coverage.

<!-- rpi:review-livingston id=RV-003 -->
#### RV-003 [High, remains resolved]: emitted and deployed alert contracts stay aligned

* Evidence: no source change in the Farnsworth revision reopened the previously verified event/query and active-depth absence semantics; focused telemetry/deployment tests passed.
* Disposition: Remains resolved.

<!-- rpi:review-livingston id=RV-004 -->
#### RV-004 [High, resolved]: legacy reference migration is bounded, resumable, fail-closed, and fenced

* Evidence: cleanup backfilled a removed legacy reference before deletion; incomplete one-record pages returned without deleting; persisted cursor resumed; current-schema concurrent references defeated the CAS cleanup claim.
* Disposition: Resolved. The migration completion gate prevents pre-index referenced deletion.

<!-- rpi:review-livingston id=RV-007 -->
#### RV-007 [Medium, remains resolved]: canonical tracking preserves history and current ownership

* Evidence: Fry/Leela rejection history remains intact; Farnsworth/Livingston ownership is explicit; this cycle reconciles exact final-head findings and validation without rewriting prior cycles.
* Disposition: Remains resolved by review-only tracking reconciliation.

<!-- rpi:review-livingston id=RV-008 -->
#### RV-008 [High, open]: an older predecessor can authorize mutation after a later `provider_unknown`

* Evidence: a first attempt terminated `failed_terminal` and received valid structured recovery evidence. Its succeeding attempt then terminated `provider_unknown`. Reusing the older predecessor and its evidence was accepted, appended a third attempt, and `claim()` returned `read_only=False`.
* Root cause: `_validated_recovery_authorization_evidence()` compares `prior_attempt_ids` only through the selected predecessor. `authorize_recovery()` rejects unknown state only on that selected predecessor and does not require it to be the latest terminal attempt or reject later unresolved/unknown attempts.
* Impact: the branch can regain provider mutation authority after an unknown mutation, violating the authoritative no-blind-retry invariant and the exact complete-attempt-history binding required for controlled recovery.
* Required clearing evidence: authorization rejects any non-latest predecessor and any history containing a later `provider_unknown`, ambiguous receipt, identity conflict, or unresolved mutation; a succeeding claim remains reconciliation-only or is not created; focused concurrent/history-tamper probes and full validation pass.

<!-- rpi:review-livingston id=RV-009 -->
#### RV-009 [High, resolved]: proof envelopes rebind identity and require four exact cycles

* Evidence: label-only, tampered week/job/manifest/artifact/provider/readback/duplicate, and unauthorized-recovery cycles were rejected; four consecutive exact complete envelopes passed.
* Disposition: Resolved. Retain raw-evidence rebinding and exact four-cycle positive coverage.

### Independent Negative Probes

| Target | Command or probe | Result |
|---|---|---|
| RV-002/RV-004/RV-008/RV-009 owner matrix | `TMPDIR="$PWD/.test-tmp" pytest tests/test_distribution_outbox.py -q -k 'reconciliation_notification_reservation_is_single_winner_and_fenced or cleanup_backfills_legacy_reference_before_deletion or cleanup_fails_closed_while_legacy_reference_scan_is_incomplete or recovery_authorization_rejects_mismatched_structured_evidence or recovery_intent_rejects_provider_identity_outside_authorization or recovery_authorization_rejects_opaque_evidence or four_cycle_acceptance_requires_complete_authoritative_envelopes or four_cycle_acceptance_rejects_identity_tampering or weekly_w38_recovery_candidate_and_w39_missed_fixture'` | `22 passed, 35 deselected` |
| RV-008 history-order bypass | Create failed predecessor, authorize recovery, terminate successor `provider_unknown`, then reuse older predecessor evidence | **Failed safety:** `RV008_BYPASS ... read_only=False attempts=3` |
| Changed executable diff secret/PII scan | Private-key, token, connection-string, JWT, and email patterns | `NO_SUSPECTED_SECRETS_OR_PII` |

### Independent Full Validation

| Command | Result |
|---|---|
| Focused outbox suite | `57 passed in 1.88s` |
| Focused correction suite | `102 passed in 5.62s` |
| Locked targeted contract | `777 passed, 1 warning in 60.48s` |
| Initial full `pytest tests/ -q` | `1 failed, 3106 passed, 2 skipped, 2 deselected, 1 warning`; only stale Compose recorder image |
| Compose rebuild plus focused fanout integration | `1 passed in 16.08s` |
| Final full `pytest tests/ -q` | `3107 passed, 2 skipped, 2 deselected, 1 warning in 85.77s` |
| Ruff, format, compile, exact diff safety, deleted-file check | Passed |
| Bicep build | Passed with pre-existing BCP318 warning |
| Exact Checkov | Documented baseline retained: `36 passed, 7 failed` |
| CI-equivalent Bicep Checkov | `34 passed, 0 failed` |
| Dockerfile Checkov baseline | Passed |
| Container build | `sha256:826e759f3685a781d185a334f86014b71238ab84fda4896776b5f396392622f6` |
| Container smoke | UID `999`; ffmpeg/ffprobe and pipeline imports passed; unconfigured distribution worker exited `2` |

No test, assertion, validation gate, or security gate was weakened.

### GitHub and External Relationships

* #684: open, draft, merge state `CLEAN`; 13 checks successful at review opening; no reviews or review threads. It remains draft/blocked and is not approved by this review.
* #682: open, non-draft, not superseded or mutated; its separate stage-budget work and review history remain unchanged.
* #671: open; Spotify video publication contract/manual-handoff dependency remains unchanged.
* #678: open; YouTube ambiguous-create identity reconciliation dependency remains unchanged.
* #679: open; Spotify audio exact-reconciliation dependency remains unchanged.
* #681: open; atomic outbox worker remains the parent implementation relationship.

### Blockers and Clearing Evidence

| Blocker | Owner | Evidence required to clear |
|---|---|---|
| RV-008 | Podcaster revision owner | Bind authorization to the complete current attempt history and latest eligible predecessor; reject later unknown/ambiguous/conflicting attempts; prove no mutation-capable claim can follow `provider_unknown` |
| P00-T01 | `jmservera/SquadScope` owner | Exact W39 upstream blocked-stage fix and durable dispatch-to-Azure evidence |
| P05 | Delivery/deployment owners | Accepted final-SHA review, all checks, approval/merge provenance, authorized deployment, provider canary, alert fire/clear, and rollback evidence |
| P06 | Production verification owner | Four consecutive future post-fix cycles with complete authoritative external proof |

No issue, PR, review thread, deployment, canary, or production state was resolved, closed, or mutated by this review.

## Ralph Fresh Independent Final-SHA Review — 2026-09-22

### Independence and Exact Boundary

* Reviewer: Ralph.
* Independence: Ralph did not author Basher's revision and received no contribution or advice from Bender, Hermes, Amy, Leela, Fry, Farnsworth, Livingston, Frank, Rusty, or Basher.
* Comparison: `97c9520b7c365b078a50df113154bb1e66b1ecbb..fcfa40015ed68d9e38d8432425b7cbd15171e835`.
* Source commit: `eaaac5706985d0df4058f46d25e4aa4d9217f41e`.
* Final reviewed head: `fcfa40015ed68d9e38d8432425b7cbd15171e835`.
* Ancestry and divergence: comparison base is the merge base; local, origin, and PR head matched with divergence `0/0`; the worktree was clean before review tracking updates.
* Post-source changes: `cef1aab` and `fcfa400` changed tracking/PR narrative only; no executable source or tests changed after `eaaac570`.

### Ralph Verdict

**Not accepted (`request_changes`).** Basher closes Rusty's different-item bypass, but RV-008 remains **High** because exact-item failed-terminal readback can authorize a new mutation-capable attempt when the latest `provider_unknown` attempt has no durable provider receipt. Severity: 0 Critical, 1 High, 0 Medium, 0 Low.

### Finding Dispositions

| Finding | Ralph disposition |
|---|---|
| RV-002 | Remains resolved; atomic reservation, stale-owner fencing, and race probes pass. |
| RV-003 | Remains resolved; telemetry/deployment regressions, Bicep, and CI Checkov pass. |
| RV-004 | Remains resolved; bounded cleanup and reference-fencing regressions pass. |
| RV-007 | Remains resolved at tracking level; all rejection cycles and exact author/reviewer identities are preserved. |
| RV-008 | **High, open.** Different-item, missing identity, duplicate, conflict, wrong-kind, stale receipt, stale authorization, wrong binding, and history tampering fail closed, but missing receipt does not. |
| RV-009 | Remains resolved; four-cycle proof-envelope regressions pass. |

### High Finding: Missing Provider Receipt Authorizes Recovery

`_recovery_predecessor_provider_evidence()` requires `receipts` to be a list but does not require it to contain an exact provider receipt. The repository's accepted exact-match fixture consumes each mutation intent, records no receipt, terminates `provider_unknown`, and later accepts an exact-item failed-terminal readback. Authorization then appends a third attempt and `claim()` returns mutation authority.

Exact reproduction:

```text
RV008_MISSING_RECEIPT_BYPASS receipts={'youtube': 0, 'spotify': 0} attempts=3 read_only=False
```

This violates the authoritative requirement that resolution be bound to durable consumed intent, receipt, and provider evidence. An expected item in intent is not evidence that the provider accepted, rejected, or identified the mutation. Required correction: each provider must have exactly one usable, non-ambiguous receipt bound to the consumed intent, provider kind, and expected provider item before exact failed-terminal readback can authorize the specifically bound successor. Missing or duplicate receipts must fail closed.

### Independent Probes

| Probe | Result |
|---|---|
| Rusty different-item reproduction | Denied; no successor authorization |
| Exact matching item | Accepted only under existing history/item bindings, but exposes the missing-receipt High finding |
| Missing identity | Denied |
| Duplicate readback identity | Denied |
| Conflicting provider items | Denied |
| Provider-kind mismatch | Denied |
| Stale receipt / wrong intent | Denied |
| Wrong week/publication/digest/artifact/binding fields | Denied by structured authorization regressions |
| Stale authorization / non-latest predecessor | Denied |
| Reordered or omitted terminal history | Denied |
| Scheduler race and fencing | Passed; one fenced winner and stale-owner rejection |
| Missing provider receipt | **Failed safety:** successor created with `read_only=False` |

### Independent Validation

| Command or gate | Result |
|---|---|
| Focused RV regression selection | `29 passed, 39 deselected` |
| Focused correction suite | `113 passed` |
| Locked contract suite | `788 passed, 1 warning` |
| Initial full suite | `1 failed, 3117 passed, 2 skipped, 2 deselected, 1 warning`; stale Compose recorder image only |
| Rebuilt Compose recorder and fanout probe | `1 passed` |
| Final full suite | `3118 passed, 2 skipped, 2 deselected, 1 warning` |
| Ruff, format, compile, diff safety, deleted-test check | Passed; `192 files already formatted` |
| Bicep build | Passed with existing BCP318 warning |
| Exact Checkov | Existing baseline reproduced: `36 passed, 7 failed` |
| CI-equivalent Checkov | `34 passed, 0 failed` |
| Dockerfile Checkov baseline | Passed |
| Review container | `sha256:5baa9f5828ab055362ba8e5b25d3fae388ab5cb85c5029c335cce4d79f2ccf10` |
| Container smoke | UID `999`; ffmpeg/ffprobe and pipeline imports passed; unconfigured distribution worker exited `2` |
| Suspected secret/PII scan | No private key, access key, JWT, signed credential URL, email-address pattern, or raw PII found |

No test, assertion, quality gate, or security gate was weakened.

### GitHub and Residual Gates

* #684: open, draft, PR head matched reviewed head, mergeable/CLEAN, 13 successful checks, no reviews, and zero review threads before this tracking-only update. It remains blocked and is not approved.
* jmservera/SquadScope-Podcaster#682, jmservera/SquadScope-Podcaster#671, jmservera/SquadScope-Podcaster#678, jmservera/SquadScope-Podcaster#679, jmservera/SquadScope-Podcaster#681, and jmservera/SquadScope-Coordinator#17 remain open.
* jmservera/SquadScope#770 remains merged but does not clear P00-T01.
* RV-008/P07-T01 remains owned by the Podcaster revision owner and requires exact durable receipt binding plus fresh independent final-SHA review.
* P00-T01 remains owned by `jmservera/SquadScope`; P05 remains owned by delivery/deployment; P06 remains owned by production verification after four elapsed cycles.

No issue, review thread, deployment, canary, or production state was mutated.

---

## Fresh Independent Frank-Revision Review — Rusty — 2026-09-22

### Reviewer Identity, Independence, and Exact Boundary

* Reviewer: **Rusty, Lead / Orchestration**.
* Independence: Rusty did not author the revision. Frank was the sole current revision author. Bender, Hermes, Amy, Leela, Fry, Farnsworth, Livingston, and Frank did not advise, pair, or contribute during this review.
* Exact comparison: `1b057c0ea9073fb195c56cc884625216e18e49a7..1efa74956e23b51512b7eff1ded4e809b79566e1`.
* Source focus: Frank commit `502807d562996ecf6c8cd4213afd4cdf454aa5c3`.
* Opening repository state: clean worktree; local branch, remote branch, and PR head all at `1efa74956e23b51512b7eff1ded4e809b79566e1`; both comparison base and source commit are ancestors; remote divergence `0/0`.
* Post-source scope: `502807d..1efa749` changes only the changes record and PR narrative; no source or test changed after Frank's source commit.
* PR state at opening: #684 open, draft, merge state `CLEAN`, mergeable, 13 successful checks, no reviews, and no review threads.
* Incident truth retained: W39 is `missed_not_dispatched`. W38 was published, but `published_verified_recovered` remains conditional on exact identity-bound authoritative provider readback and safe recovery authorization.

### Rusty Final Verdict

**Not accepted (`request_changes`).** Frank's revision resolves Livingston's branch-around-later-attempt defect, and RV-002, RV-003, RV-004, RV-007, and RV-009 remain resolved. RV-008 remains **High** because the latest-unknown readback is not bound to the provider item identity implicated by that unknown attempt. A failed readback for a different provider item authorizes a new mutation-capable attempt. Severity: 0 Critical, 1 High, 0 Medium, 0 Low.

### Finding Dispositions

<!-- rpi:review-rusty id=RV-001 -->
#### RV-001 [High, remains resolved]: read-only YouTube promotion takeover convergence

* Evidence: no source change reopens the previously verified authoritative-readback convergence path; focused and full regression suites pass.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-002 -->
#### RV-002 [High, remains resolved]: expired reconciliation reservations recover with one fenced owner

* Evidence: owner regression coverage remains present and the locked suite passes.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-003 -->
#### RV-003 [High, remains resolved]: emitted and deployed alert contracts remain aligned

* Evidence: no source change affects telemetry or Bicep alert semantics; telemetry/deployment regressions, Bicep build, and CI-equivalent Checkov pass.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-004 -->
#### RV-004 [High, remains resolved]: legacy reference migration and cleanup remain fail-closed and fenced

* Evidence: no source change affects cleanup; locked and full regressions pass.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-005 -->
#### RV-005 [Medium, remains resolved]: missing provider identity remains fail-closed

* Evidence: unknown provider identity remains non-green and reconciliation-only.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-006 -->
#### RV-006 [High, remains resolved at planning level]: closure inventory retained

* Evidence: historical issue/thread inventory remains present; no issue or thread is represented as resolved by this review.
* Disposition: Planning-level resolution remains; P05 execution is open.

<!-- rpi:review-rusty id=RV-007 -->
#### RV-007 [Medium, remains resolved]: rejection history and current ownership remain explicit

* Evidence: Leela/Fry and Farnsworth/Livingston rejection history is preserved; this section appends Frank/Rusty evidence without rewriting prior cycles.
* Disposition: Remains resolved by tracking-only reconciliation.

<!-- rpi:review-rusty id=RV-008 -->
#### RV-008 [High, open]: latest-unknown readback is not bound to the unknown attempt's provider item

* Evidence: the latest attempt consumed mutation intents for `youtube-unknown` and `spotify-unknown`, then terminated `provider_unknown`. Read-only reconciliation recorded authoritative-looking `failed_terminal` readbacks for different item identities, `youtube-DIFFERENT-ITEM` and `spotify-DIFFERENT-ITEM`. `exact_recovery_authorization_evidence()` accepted those readbacks, `authorize_recovery()` appended a third attempt, and `claim()` returned `read_only=False`.
* Root cause: `_recovery_predecessor_provider_evidence()` requires a non-empty provider item, native state, failed result, and source containing `readback`, but does not require the post-terminal readback item to equal the unknown attempt's persisted expected provider item, mutation receipt identity, or prior observed identity. `_validated_recovery_authorization_evidence()` then self-consistently validates the mismatched readback rather than rebinding it to the unknown mutation.
* Impact: authoritative readback about an unrelated failed provider item can be used to declare the actual unknown mutation safe and restore mutation authority. This violates the exact readback-binding and no-new-mutation contract.
* Required clearing evidence: bind each post-terminal failed readback to the latest unknown attempt's exact persisted provider identity/intent/receipt; reject missing, conflicting, duplicate, or different item identities; prove the mismatched-item reproduction cannot append or claim a succeeding attempt while exact matching readback still permits only the specifically authorized continuation.

<!-- rpi:review-rusty id=RV-009 -->
#### RV-009 [High, remains resolved]: four-cycle proof envelopes remain rebound and exact

* Evidence: focused and full regressions pass; no source change affects the four-cycle evaluator.
* Disposition: Remains resolved.

### Independent Probes

| Target | Probe | Result |
|---|---|---|
| RV-008 older authorization | Failed terminal, authorized successor becomes `provider_unknown`, reuse older predecessor | Passed: stale older predecessor is rejected and takeover remains read-only |
| RV-008 latest exact readback owner test | Matching failed-terminal readback of latest unknown, fresh authorization, succeeding claim | Passed |
| RV-008 stale/non-latest authorization | Select a non-latest predecessor | Passed: rejected |
| RV-008 omitted/reordered history | Remove or reorder terminal attempt IDs | Passed: rejected |
| RV-008 exact safe recovery | Failed predecessor plus exact authorized succeeding provider identities | Passed: immutable failed attempt retained and recovered state accepted |
| RV-008 readback identity binding | Unknown intent for `*-unknown`, then failed readback for `*-DIFFERENT-ITEM` | **Failed safety:** `RV008_READBACK_BINDING_BYPASS read_only=False attempts=3` |
| RV-002/RV-003/RV-004/RV-007/RV-009 regressions | Focused, locked, and full suites | Passed |

The ordered list and latest-predecessor checks reject stale, omitted, and reordered histories. Atomic storage updates fence concurrent authorization writers. Attempt timestamps are descriptive rather than the ordering authority. Generated attempt IDs are unique in normal repository operations, but the safe readback decision still fails because it is not bound to the exact provider item of the latest unknown attempt.

### Independent Full Validation

| Command | Result |
|---|---|
| Required RV-008 focused selection | `15 passed, 47 deselected` |
| Focused correction suite | `107 passed in 3.18s` |
| Locked targeted contract | `782 passed, 1 warning in 56.02s` |
| Initial full `pytest tests/ -q` | `1 failed, 3111 passed, 2 skipped, 2 deselected, 1 warning`; stale Compose recorder image only |
| Compose rebuild plus focused fanout integration | `1 passed in 13.39s` |
| Final full `pytest tests/ -q` | `3112 passed, 2 skipped, 2 deselected, 1 warning in 82.67s` |
| Ruff, format, compile, and exact diff safety | Passed; `192 files already formatted` |
| Bicep build | Passed with pre-existing BCP318 warning |
| Exact Checkov | Documented baseline retained: `36 passed, 7 failed` |
| CI-equivalent Bicep Checkov | `34 passed, 0 failed` |
| Dockerfile Checkov baseline | Passed |
| Container build | `sha256:72257821fdc2c45de68d98857a35d8d2f72fced688c829d75dccfe651d38d37b` |
| Container smoke | UID `999`; ffmpeg/ffprobe and pipeline imports passed; unconfigured distribution worker exited `2` |
| Changed diff secret/PII scan | No suspected secret, credential value, private key, signed credential URL, JWT, email address, or raw PII identified |

No test, assertion, validation gate, security gate, or baseline was weakened.

### GitHub and External Relationships

* #684: open, draft, merge state `CLEAN`, mergeable; 13 successful checks; no reviews or review threads. It remains draft/blocked and is not approved by this review.
* jmservera/SquadScope-Podcaster#682: open, non-draft; not superseded, closed, merged, approved, or otherwise mutated by this review.
* jmservera/SquadScope-Podcaster#671: open; Spotify/manual-handoff dependency remains.
* jmservera/SquadScope-Podcaster#678: open; YouTube ambiguous-create identity reconciliation remains.
* jmservera/SquadScope-Podcaster#679: open; Spotify exact reconciliation remains.
* jmservera/SquadScope-Podcaster#681: open; atomic outbox worker remains the parent implementation relationship.
* jmservera/SquadScope#770: merged; it does not clear the still-open P00-T01 evidence requirement recorded by this plan.
* jmservera/SquadScope-Coordinator#17: open.

### Blockers and Clearing Evidence

| Blocker | Owner | Evidence required to clear |
|---|---|---|
| RV-008 / P07-T01 | Podcaster revision owner | Exact latest-unknown provider-item binding for post-terminal readback; mismatched-item rejection; exact-match safe continuation; focused and full validation |
| P00-T01 | `jmservera/SquadScope` owner | Durable evidence that the W39 blocked dispatch stage and recurrence controls are implemented and verified |
| P05 | Delivery/deployment owners | Accepted final-SHA review, approval/merge provenance, authorized deployment, provider canary, alert fire/clear, and rollback evidence |
| P06 | Production verification owner | Four consecutive future post-fix cycles with complete authoritative external proof |

No issue, PR, review thread, deployment, canary, or production state was resolved, closed, or mutated by this review.
