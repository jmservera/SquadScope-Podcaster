<!-- markdownlint-disable-file -->
# Review: Video Stage Budget Redesign

## Scope and Evidence

* Task ID: video-stage-budget-redesign
* Review date: 2026-09-15
* Review scope: Full implementation boundary through P05-T01; delivery work in P05-T03 remains subsequent
* Assessed boundary: User-supplied W38 timing contract, provider safeguards, implementation acceptance criteria, plan critique dispositions, source diff, focused/full validation, and explicit deferred-worker boundary
* Plan: .copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md
* Phase details: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md
* Plan critique: .copilot-tracking/reviews/plans/2026-09-15/video-stage-budget-redesign-plan-critique.md
* Changes: .copilot-tracking/changes/2026-09-15/video-stage-budget-redesign-changes.md
* Other evidence considered: .copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md; complete branch diff; validation commands recorded during implementation

## Opening Review State

* Interpreted review goal: Independently determine whether the completed video-budget implementation satisfies every binding deadline, replay rule, cancellation requirement, provider/publication safeguard, and validation requirement without expanding into the deferred distribution worker.
* Review scope: P01-P04 implementation plus P05-T01 candidate validation; P05-T03 issue/commit/push/PR delivery is not yet assessed as complete.
* Evidence readiness: Plan, details, critique, research, changes record, implementation diff, and fresh command results are available. The changes record still requires final P05 synchronization after review and delivery.
* Acceptance basis: Plan acceptance criteria, P05 mandatory matrix, resolved critique findings PC-001 through PC-006, explicit user disposition of PC-007, and preservation of baseline provider-state safeguards.
* First comparison boundary: Independent read-only inspection of every deadline and safeguard against production code, tests, and validation evidence.
* Active read-only boundaries: Review may update only this review record; reviewer has no source, plan, details, critique, research, or changes-record write authority.
* Initial blockers: None.

## Execution Status

* Execution status: Complete
* Review execution evidence: Independent reviewers inspected the complete implementation and every binding timing/safeguard row. Rejected findings were fixed only by separate lockout implementers, revalidated, and independently re-reviewed to acceptance.

## Plan-to-Change Reconciliation

| Current plan scope | Descriptive changes-record summary | Current-state reconciliation | Gap or rationale |
|---|---|---|---|
| P01-P04 | Budget, recorder, fallback, render/archive replay, provider reserve, evidence, and shutdown summaries | Reconciled | Final independent verdict accepts every phase |
| P05-T01 | Mandatory semantic and repository validation | Reconciled | Focused/full and infrastructure gates passed after all lockout fixes |
| P05-T02 | Independent review and lockout correction | Reconciled | All rejected findings were fixed by separate implementers and independently accepted |
| Follow-Up Items | Separate distribution worker | Reconciled as distinct residual work | Exact GitHub issue remains P05-T03 work |

## Completed Work Assessment

| Related marker | Files | What changed and why | Completion evidence | Validation | Assessment |
|---|---|---|---|---|---|
| P01-P04 | Production, infra, docs, and tests named in the changes record | Shared budget and bounded, resumable pipeline redesign | Exact boundary tests, replay/cancellation/provider matrices, and full suite | Passed | Reconciled and accepted |
| P05-T01 | Test, lint, compile, Bicep, Checkov, lock, diff, and integration surfaces | Proved the requested semantics without weakening gates | Final full suite and independent acceptance | Passed | Reconciled and accepted |

## Implementation-Time Plan and Detail Update Assessment

| Affected area or marker | What changed and why | Triggering evidence and user decision | Reconciliation performed | Planning and critique state | Assessment |
|---|---|---|---|---|---|
| Acceptance criteria and P01-P05 | Applied PC-001 through PC-006; preserved user-required trailer against PC-007 | Plan critique and explicit user requirement | Recorded in plan, details, changes, and review evidence | Accepted | Reconciled |

## Critique and Material Revision Assessment

* Latest critique dispositions: PC-001 through PC-006 are resolved in the implementation; PC-007 correctly preserves explicit user direction.
* Material revisions: Lockout corrections strengthened the accepted design without changing scope: actual callable cancellation, explicit recorder-failure counting, bounded cleanup/queue disposition, per-mutation provider admission, and one-session large YouTube uploads.
* Dependent-work pause assessment: Every rejection paused acceptance until a separate implementer fixed it and a new independent reviewer accepted the result.
* Justification assessment: Supported by production-path inspection, focused regression tests, and the final full suite.

## Plan Follow-Up Assessment

| Follow-up item | Why outside immediate scope | Owner or next action | Assessment and route |
|---|---|---|---|
| Separate distribution worker | Requires independent queue, lease, outbox/claim, reconcile-before-mutate, migration, and crash matrix beyond this bounded redesign | #681 | Valid distinct follow-up; not an implementation defect |

## Findings

<!-- rpi:review id=RV-001 -->
### RV-001 [High]: Budgeted validated uploads reported false success state

* Related scope: P03-T02
* Evidence: `podcaster/video/intermediates.py` and `tests/test_video_intermediates.py`
* Impact: Validation sidecars were not written for budgeted checkpoint uploads, preventing replay.
* Destination: rpi-implement
* Smallest useful next action: Return verified upload success regardless of budget presence and test budgeted replay.
* Disposition: Resolved by a separate lockout implementer and independently accepted.

<!-- rpi:review id=RV-002 -->
### RV-002 [High]: Timed storage/provider callables could outlive their deadlines

* Related scope: P03-T03 and P04-T03
* Evidence: `podcaster/video/intermediates.py`, `podcaster/video/process.py`, and provider/storage call sites
* Impact: Abandoned daemon threads could mutate after T+3600/T+4920 and overlap shutdown.
* Destination: rpi-implement
* Smallest useful next action: Execute callables in an owned process group and kill, join, and reap on timeout.
* Disposition: Resolved by a separate lockout implementer and independently accepted.

<!-- rpi:review id=RV-003 -->
### RV-003 [Medium]: Concurrent recorder deliveries were inferred as failures

* Related scope: P02-T01
* Evidence: `podcaster/video/recorder.py` and `tests/test_recorder.py`
* Impact: A healthy overlapping capture could be displaced by fallback before two real failures.
* Destination: rpi-implement
* Smallest useful next action: Count only explicit failures and test overlapping active deliveries.
* Disposition: Resolved by a separate lockout implementer and independently accepted.

<!-- rpi:review id=RV-004 -->
### RV-004 [High]: Optional cleanup could delay mandatory T+5100 shutdown disposition

* Related scope: P04-T03
* Evidence: `podcaster/video/job_runner.py`, `podcaster/video/intermediates.py`, and `podcaster/video/editor.py`
* Impact: Slow scratch deletion could delay terminal evidence and lease release.
* Destination: rpi-implement
* Smallest useful next action: Persist terminal evidence and release the lease first; bound optional cleanup to remaining shutdown time.
* Disposition: Resolved by a separate lockout implementer and independently accepted.

<!-- rpi:review id=RV-005 -->
### RV-005 [High]: Later provider mutations and queue deletion lacked final boundary admission

* Related scope: P04-T02 and P04-T03
* Evidence: Provider mutation paths in `podcaster/publish.py` and `podcaster/video/distribution.py`; queue orchestration in `podcaster/video/job_runner.py`
* Impact: A later mutation could start after T+4500, and queue deletion could start after cleanup outside T+5100.
* Destination: rpi-implement
* Smallest useful next action: Recheck admission before every provider mutation/retry and perform bounded mandatory queue disposition before optional cleanup.
* Disposition: Resolved by a separate lockout implementer and independently accepted.

<!-- rpi:review id=RV-006 -->
### RV-006 [High]: Large YouTube uploads lost prior-mutation state

* Related scope: P04-T02
* Evidence: `podcaster/video/distribution.py`, `podcaster/video/youtube.py`, and focused YouTube tests
* Impact: The greater-than-128-MiB path initialized two sessions and could misclassify post-mutation denial as safe pre-mutation retry.
* Destination: rpi-implement
* Smallest useful next action: Choose the large path before initialization so one session owns all mutation state.
* Disposition: Resolved by a separate lockout implementer and independently accepted.

## Defects

* None open. RV-001 through RV-006 are resolved and independently accepted.

## Routed Findings

| Finding | Destination | Owner or next action | Reason for route |
|---|---|---|---|
| RV-001 through RV-006 | rpi-implement | Completed by separate lockout implementers | Accepted implementation defects required correction before review acceptance |

## Residual Work

* The separate distribution worker is tracked in #681 as a distinct follow-up, not an open defect.

## Blockers and Remaining Work

* Blockers: None.
* Remaining active work: None.

## Validation Evidence

| Command | Scope | Status | Summary |
|---|---|---|---|
| `pytest -q` focused mandatory matrix | Video/provider/publication/infra/integration | Passed | 1323 passed, 2 deselected |
| `pytest tests/ -q` | Full repository final candidate | Passed | 3141 passed, 2 skipped, 2 deselected, one pre-existing httpx warning |
| `ruff check podcaster tests` | Python lint | Passed | All checks passed |
| `ruff format --check podcaster tests` | Python formatting | Passed | 187 files already formatted |
| `python3 -m compileall -q podcaster tests` | Python compile | Passed | No errors |
| Bicep build for `aca-recorder.bicep` and `aca-video.bicep` | Changed infrastructure modules | Passed | Both compiled; only tool update notices |
| CI-equivalent Checkov Bicep command | `infra/` | Passed | 34 passed, 0 failed |
| Dependency manifest/lock assertion | Dependency surface | Passed | No dependency manifest or lock changes |
| `git diff --check` | Complete diff | Passed | No whitespace errors |
| `pytest tests/integration/test_scaleout_fanout.py -q` | Real Azurite/Compose fanout | Passed | 1 passed |
| Independent reviewer acceptance slices | Deadline, replay, provider, queue, and shutdown behavior | Passed | Final reviewer reported 203 passed and no actionable defects |

## Outcome

* Outcome: Conformant
* Outcome rationale: Every binding deadline and safeguard passed independent inspection after all rejected findings were corrected under lockout. Remaining P05-T03 delivery and the separate worker issue are distinct follow-up/delivery work.

## Closeout Routing Record

| Finding class | Destination | Owner or next action |
|---|---|---|
| Implementation defect | None | All RV findings resolved |
| Decision gap or invalid assumption | None | No route required |
| Material evidence gap | None | No route required |
| Non-blocking residual work | Distinct follow-up | #681 |

* Execution status: Complete
* Outcome: Conformant
* Validation coverage: Mandatory focused/full Python, integration, lint, format, compile, Bicep, Checkov, dependency lock, diff checks, and independent acceptance passed.
* Blockers: None.

| Artifact | Description |
|---|---|
| [.copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md](.copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md) | Current implementation and delivery plan |
| [.copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md](.copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md) | Detailed phase contracts and validation matrix |
| [.copilot-tracking/reviews/plans/2026-09-15/video-stage-budget-redesign-plan-critique.md](.copilot-tracking/reviews/plans/2026-09-15/video-stage-budget-redesign-plan-critique.md) | Planning critique and dispositions |
| [.copilot-tracking/changes/2026-09-15/video-stage-budget-redesign-changes.md](.copilot-tracking/changes/2026-09-15/video-stage-budget-redesign-changes.md) | Implementation and validation evidence |
| [.copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md](.copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md) | Research evidence and design basis |

## Next Steps

No user action is required for this review. Delivery is recorded in implementation commit `4033df6`, pushed branch `squad/video-stage-budget-redesign`, PR #682, and follow-up issue #681.
