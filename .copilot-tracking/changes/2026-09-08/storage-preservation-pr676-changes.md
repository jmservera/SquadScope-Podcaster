# RPI Changes: PR676 storage preservation

## Metadata

* Task ID: pr676-storage-preservation
* Related plan: .copilot-tracking/plans/2026-09-08/storage-preservation-pr676-plan.md
* Phase details: .copilot-tracking/details/2026-09-08/storage-preservation-pr676-phase-details.md
* Implementation date: 2026-09-08

## Execution Status

* Status: Partial
* Declared invocation scope: full_plan
* Completed scope markers: P01-T01, P01-T02
* All remaining active-plan markers: P01-T03, P02-T01, P02-T02, P02-T03
* Status basis: The isolated worktree fix and focused validation are complete; GitHub merge and post-merge deployment evidence are still pending.

## Execution Summary

Investigated the live PR #676 and issue #675 state, identified a second real caller (`release.yml`) that also needed the new storage network parameter, delegated the bounded follow-up to Bender in the isolated worktree, and obtained focused validation plus an independent QA approval from Fry before GitHub push/merge handling.

## Completed Work

### PR state and caller audit

* Related phase or task: P01-T01
* Files: .github/workflows/deploy-azure.yml, .github/workflows/reusable-deploy-azure.yml, .github/workflows/release.yml, tests/test_deploy_workflow.py, docs/AZURE-DEPLOYMENT.md, README.md
* What changed and why: Confirmed the live unresolved review thread targeted `DEPLOY_VNET` normalization/validation and found `release.yml` as an additional reusable deploy caller that otherwise would not preserve an explicit storage network override across image-promotion redeploys.
* Completion evidence: GitHub PR GraphQL thread data showed one unresolved thread on `.github/workflows/reusable-deploy-azure.yml`; local caller audit showed both `infra` and `promote` jobs in `release.yml` invoke `.github/workflows/reusable-deploy-azure.yml`.
* Validation: run / passed — GitHub PR/ruleset inspection and caller audit completed.

### Narrow implementation and focused validation

* Related phase or task: P01-T02
* Files: .github/workflows/reusable-deploy-azure.yml, .github/workflows/deploy-azure.yml, .github/workflows/release.yml, tests/test_deploy_workflow.py, docs/AZURE-DEPLOYMENT.md, README.md
* What changed and why: Normalized and validated `DEPLOY_VNET` before the storage contradiction guard, preserved exact `Enabled|Disabled` storage validation, exposed the input on both wrapper workflows, and documented the explicit public-storage opt-in so fresh non-private installs remain supported without reopening current production Storage.
* Completion evidence: Commit `de8cea9e8bb81b3c5e8556b0ba4227d9e19cf199` on `fix/storage-network-access-675`; Fry independently approved the changed diff.
* Validation: run / passed — `pytest tests/test_deploy_workflow.py -q` => `32 passed in 0.15s`.

## Implementation-Time Plan and Detail Updates

### Caller coverage clarification

* Affected plan area or markers: Scope, P01-T01, P01-T02
* What changed: Added `release.yml` to the active write boundary and acceptance checks because it is a real reusable deploy caller that can re-apply infra defaults during promote.
* Why: The initial three-file PR surface was not the whole effective deployment entry surface for the changed parameter.
* Triggering evidence: Caller search showed `release.yml` invokes `.github/workflows/reusable-deploy-azure.yml` twice (`infra` and `promote`).
* User answer or decision: none
* Reconciliation performed: Scope, acceptance criteria, and phase details aligned to include wrapper/caller wiring.
* Planning and critique state: current readiness; no new critique needed.

## Validation Record

| Check | Scope | Status | Evidence or reason |
|-----------|-----------|------------------------------------------|------------------------|
| PR/ruleset/thread inspection | P01-T01 | Passed | Live GitHub PR GraphQL + ruleset inspection completed |
| pytest tests/test_deploy_workflow.py -q | P01-T02 | Passed | 32 passed in 0.15s |
| Independent QA review | P01-T02 | Passed | Fry review verdict: approve, no significant issues found |

## Pre-Review Reconciliation

* Plan markers and phase details: current through P01-T02; pending GitHub merge/deploy markers remain unchecked.
* Completed-work evidence and handoff prose: current.
* Validation, blockers, remaining work, and follow-up items: current.
* Review readiness: ready for GitHub merge gate handling; post-merge deploy proof still pending.

## Blockers

* None currently; GitHub merge/deploy outcome still needs to be attempted and recorded.

## Remaining Work

* P01-T03 — Push the fix, resolve the addressed thread if GitHub leaves it open, and attempt normal merge to discover any real remaining external gate.
* P02-T01 — Run sanitized post-merge what-if with production-safe inputs and capture the no-network-flip evidence.
* P02-T02 — Run the preservation deployment and read back Storage/ACR/network state.
* P02-T03 — Close issue #675 after verified evidence is complete.

## Follow-Up Items

* Canonical plan list: .copilot-tracking/plans/2026-09-08/storage-preservation-pr676-plan.md, `## Follow-Up Items`
* None.

## Return-to-Caller State

* Implementation execution status: Partial
* Declared scope and markers: full_plan with P01-T01 and P01-T02 complete; P01-T03, P02-T01, P02-T02, and P02-T03 remaining.
* Validation coverage: Focused deploy workflow tests plus independent QA review completed for the code changes.
* Blockers: none current.
* Current plan and detail updates: release.yml caller coverage added to active scope.
* Planning and critique state: current and implementation-ready.
* Follow-up items: none.
* Review readiness or no-handoff reason: ready for GitHub merge gate handling; not yet ready for final closeout until post-merge deploy evidence exists.
* Continuation owner: standalone
