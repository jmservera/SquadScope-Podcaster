# Review: Distribution outbox worker

## Scope and Evidence

* Task ID: jmservera/SquadScope-Podcaster#681
* Review date: 2026-09-29
* Review scope: first shippable PR slice for an atomic provider-distribution outbox worker
* Assessed boundary: issue #681 acceptance criteria scoped by the plan to worker foundation, claim/lease/message lifecycle, default-off provider mutation seam, default-off ACA wiring, RPI artifacts, and validation evidence. Full provider-specific dispatch and production rollout are assessed as residual follow-up work.
* Plan: .copilot-tracking/plans/2026-09-29/distribution-outbox-worker-plan.md
* Phase details: .copilot-tracking/details/2026-09-29/distribution-outbox-worker-phase-details.md
* Plan critique: .copilot-tracking/critiques/2026-09-29/distribution-outbox-worker-plan-critique.md
* Changes: .copilot-tracking/changes/2026-09-29/distribution-outbox-worker-changes.md
* Other evidence considered: .copilot-tracking/research/2026-09-29/distribution-outbox-worker-research.md; `podcaster/distribution_worker.py`; `tests/test_distribution_worker.py`; `infra/modules/aca-distribution.bicep`; `infra/main.bicep`; `tests/test_deploy_workflow.py`; local validation output.

## Opening Review State

* Interpreted review goal: assess whether the implemented first PR slice satisfies the RPI plan and preserves the caller's safety constraints for jmservera/SquadScope-Podcaster#681.
* Review scope: full planned first slice, not full issue closure.
* Evidence readiness: research, plan, phase details, plan critique, changes record, implementation diff, and validation evidence are available.
* Acceptance basis: caller requirements, issue #681 acceptance criteria as scoped to first PR, plan critique Pass verdict, and repository validation commands.
* First comparison boundary: compare P01-P04 and defaults/no-auto-approval invariants against changed code, infra, and tests.
* Active read-only boundaries: review creates only this review record; implementation artifacts remain unchanged during review.
* Initial blockers: none.

## Execution Status

* Execution status: Complete
* Review execution evidence: compared the full artifact set and changed implementation after local focused tests, full pytest, ruff, and diff checks passed. Post-PR Copilot review found valid worker lifecycle gaps; the implementation was updated to delete exhausted orphan hints, renew leases while injected dispatch runs, hash opaque pop receipts before claim writes, pass queue visibility longer than the ACA hard timeout, and preserve stronger provider truth during poison handling.

## Plan-to-Change Reconciliation

| Current plan scope | Descriptive changes-record summary | Current-state reconciliation | Gap or rationale |
|---|---|---|---|
| P01 Worker contract | Added `podcaster.distribution_worker` queue parser, safe execution IDs, claim, heartbeat, release, malformed, active-lease, orphan-hint, and poison handling | Reconciled | Worker uses outbox repository claim/lease as authority and queue messages as hints. |
| P02 Provider seam | Added `DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED` false default and injected dispatcher seam with lease renewal | Reconciled | Default path makes no provider API calls; real dispatch is residual work. |
| P03 Infrastructure | Added event ACA worker module and instantiated worker plus scheduler from root Bicep | Reconciled | Static deploy tests assert default-off mutation and scheduler wiring. |
| P04 Tests | Added worker lifecycle and infra wiring tests | Reconciled | Focused and full validation passed. |
| Deferred provider dispatch | YouTube/Spotify/RSS dispatch and rollout are listed as deferred | Reconciled | Outside first PR by plan; route to follow-up issues. |

## Completed Work Assessment

| Related marker | Files | What changed and why | Completion evidence | Validation | Assessment |
|---|---|---|---|---|---|
| P01 | `podcaster/distribution_worker.py`, `tests/test_distribution_worker.py` | Added durable outbox queue consumer with safe message disposition and claim lifecycle | Tests cover malformed, handled, active lease, missing outbox retry/delete, poison | Passed focused tests and full suite before review-comment fix; focused tests rerun after fix | Reconciled |
| P02 | `podcaster/distribution_worker.py`, `tests/test_distribution_worker.py` | Added mutation flag false default and dispatcher seam with renewal | Tests prove false default, fail-closed default, injected enabled path, and lease renewal | Passed focused tests and full suite before review-comment fix; focused tests rerun after fix | Reconciled |
| P03 | `infra/modules/aca-distribution.bicep`, `infra/main.bicep`, `tests/test_deploy_workflow.py` | Added worker job and scheduler wiring while preserving default-off flags | Static infra assertions pass | Passed deploy workflow tests | Reconciled |
| P04 | `tests/test_distribution_worker.py`, `tests/test_deploy_workflow.py` | Added targeted safety and wiring tests | 47 focused tests passed; full suite passed | Passed | Reconciled |

## Implementation-Time Plan and Detail Update Assessment

| Affected area or marker | What changed and why | Triggering evidence and user decision | Reconciliation performed | Planning and critique state | Assessment |
|---|---|---|---|---|---|
| First shippable scope | Provider-specific mutation was not implemented in this PR | Caller allowed a first shippable PR if #681 is large; research showed full provider migration is large | Plan, details, changes, and review all mark provider dispatch as deferred | Critique passed this bounded plan | Reconciled |
| Infra scheduler | Existing scheduler module was instantiated along with the new worker | Research found module existed but was unused | Plan and changes include scheduler wiring | Covered by critique and static tests | Reconciled |

## Critique and Material Revision Assessment

* Latest critique dispositions: plan critique passed with no blocking findings and explicitly accepted that full #681 closure requires follow-up work.
* Material revisions: implementation stayed within the critiqued first slice; no divergent design decision was introduced after critique.
* Dependent-work pause assessment: provider mutation work remains paused behind explicit follow-up issues and disabled flags.
* Justification assessment: supported by research and validation evidence.

## Plan Follow-Up Assessment

| Follow-up item | Why outside immediate scope | Owner or next action | Assessment and route |
|---|---|---|---|
| YouTube provider dispatcher with reconcile-before-create | Requires provider-specific mutation and crash matrix coverage | jmservera/SquadScope-Podcaster#725 | Open residual follow-up |
| YouTube playlist/public promotion dispatcher | Requires approval and terminal-public semantics | jmservera/SquadScope-Podcaster#726 | Open residual follow-up |
| Spotify video dispatcher | Requires draft/live/pagination/protected-ID safeguards | jmservera/SquadScope-Podcaster#727 | Open residual follow-up |
| Spotify RSS dispatcher/backfill | Requires feed CAS/migration semantics | jmservera/SquadScope-Podcaster#728 | Open residual follow-up |
| Production canary/rollback runbook evidence | Operational rollout excluded from first PR | jmservera/SquadScope-Podcaster#729 | Open residual follow-up |

Unresolved plan follow-up items remain distinct follow-up work. Do not treat them as defects or add them to active `Pxx` or `Pxx-Txx` implementation, completion, or acceptance scope.

## Findings

No defects found in the implemented first slice.

## Defects

* None.

## Routed Findings

| Finding | Destination | Owner or next action | Reason for route |
|---|---|---|---|
| None | none | none | No active implementation defects or decision gaps. |

Later implementation of a routed finding does not require another Review.

## Residual Work

* Provider-specific mutation dispatchers, migration/backfill, and canary/rollback operations remain distinct follow-up work.

## Blockers and Remaining Work

* Blockers: none.
* Remaining active work: none for the first PR slice.

## Validation Evidence

| Command | Scope | Status | Summary |
|---|---|---|---|
| `python3 -m pytest -q tests/test_distribution_worker.py tests/test_queue.py tests/test_deploy_workflow.py` | focused worker, queue, infra assertions | Passed | 52 passed |
| `python3 -m pytest -q` | full repository test suite | Passed | 4215 passed, 4 skipped, 2 deselected, 1 warning |
| `python3 -m ruff check podcaster tests` | Python lint | Passed | All checks passed |
| `python3 -m ruff format --check podcaster tests` | Python formatting | Passed | 209 files already formatted |
| `git --no-pager diff --check` | whitespace check | Passed | no whitespace errors |
| `checkov --directory infra --framework bicep --compact` | Bicep security scan | Failed | 36 passed, 7 failed on pre-existing storage/OpenAI/ACR resources; no finding targeted the new `aca-distribution.bicep` worker module |

## Outcome

* Outcome: Residual work
* Outcome rationale: the implemented first slice conforms to its bounded plan and validation passed, but full issue #681 remains open until provider-specific dispatch, migration, and rollout work are implemented in follow-up issues.

## Closeout Routing Record

| Finding class | Destination | Owner or next action |
|---|---|---|
| Implementation defect | none | none |
| Decision gap or invalid assumption | none | none |
| Material evidence gap | none | none |
| Non-blocking residual work | distinct follow-up issues | create issues for provider dispatch and rollout work |

* Execution status: Complete
* Outcome: Residual work
* Validation coverage: focused tests, full pytest, ruff lint/format, and diff check passed; Checkov Bicep scan ran and reported existing infra failures outside the new worker module.
* Blockers: none.
