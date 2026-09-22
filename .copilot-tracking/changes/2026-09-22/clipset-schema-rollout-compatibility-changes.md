<!-- markdownlint-disable-file -->
# RPI Changes: Clipset Schema Rollout Compatibility

## Metadata

* Task ID: clipset-schema-rollout-compatibility
* Related plan: .copilot-tracking/plans/2026-09-22/clipset-schema-rollout-compatibility-plan.md
* Phase details: .copilot-tracking/details/2026-09-22/clipset-schema-rollout-compatibility-phase-details.md
* Implementation date: 2026-09-22
* Revision author: Leela
* Reviewer lockout: Bender must not contribute after Fry's blocking rejection.

## Execution Status

* Status: Complete for the revised full-plan implementation scope.
* Completed scope markers: P01, P01-T01, P01-T02, P02, P02-T01, P02-T02.
* Remaining active-plan markers: None.
* External state: Fry approved the revision for commit, push, and exact-thread
  closeout.

## Blocking Rejection and Revised Boundary

* Related phase or task: P01-T01, P01-T02, P02-T01
* What changed and why: Fry established that the pre-change v1 serializer
  always wrote `video_budget` as an object or null. The rejected patch instead
  recognized an invented keyless shape and rebuilt malformed-budget plans,
  risking replacement of an immutable three-clip plan and deletion of completed
  clip zero.
* Reconciliation: The plan, details, implementation, and tests now use the
  authentic serializer shapes. Budget metadata errors are rejected without
  replacement or cleanup.
* Ownership: Leela performed the independent revision; Bender remained locked out.

## Authentic V1 Compatibility

* Related phase or task: P01-T01
* Files: `podcaster/video/clipset.py`, `tests/test_clipset.py`
* What changed and why: V1 parsing now requires the historical
  `video_budget` key and accepts exactly an object or null. Object budgets are
  parsed into their original durable projection; null is represented as no
  budget. Missing, string, list, numeric, and structurally invalid objects raise
  `ClipsetBudgetError`. V2 continues to require an object budget.
* Completion evidence: Authentic old-serializer object/null fixtures round-trip;
  invalid shapes are explicitly rejected.

## Immutable Plan and CAS-Safe Metadata Upgrade

* Related phase or task: P01-T02
* Files: `podcaster/video/editor.py`, `tests/test_editor.py`
* What changed and why: A readable v1 clipset remains authoritative even when
  redelivery supplies changed segments. Metadata upgrade copies only schema and
  budget metadata while preserving job ID and the exact clip tuple. Existing
  object-budget timestamps win over the new invocation budget; null is upgraded
  only when the caller supplies an authoritative budget. `update_bytes` performs
  the conditional update and a concurrent winner is re-read as authoritative.
  The upgrade edits only `schema_version` and `video_budget`, retaining all
  other persisted document fields.
* Safety evidence: Schema/budget errors are re-raised before the pre-existing
  corrupt-document rebuild branch, so no clipset replacement, clip-prefix
  deletion, manifest deletion, or media deletion occurs for malformed budgets.
* Completion evidence: Three-clip replay retains clip order/content and completed
  clip-zero manifest/content; concurrent migration retains the CAS winner.

## Recoverable Recorder Behavior

* Related phase or task: P01-T02
* Files: `podcaster/video/recorder.py`, `tests/test_recorder.py`
* What changed and why: Malformed budget/schema metadata and authentic null v1
  state are recoverable setup conditions. Recorder messages remain queued and
  no terminal manifest is written. Job mismatch remains a permanent fatal setup
  condition.
* Completion evidence: Parameterized missing/string/list/numeric/invalid-object
  recorder cases and null-v1 replay all return retry without queue deletion or
  terminalization.

## Coupled Test Construction

* Related phase or task: P02-T01
* Files: `tests/test_video_job_runner.py`
* What changed and why: Two tests that intentionally construct current-schema
  clipsets now supply the required durable budget projection.
* Completion evidence: The requested job-runner target passes with the explicit
  current schema.

## Validation Record

| Check | Scope | Status | Evidence |
|---|---|---|---|
| Focused pytest | clipset/editor/recorder | Passed | 145 passed in 16.19s |
| Requested targeted pytest | clipset/editor/recorder/job/video-job | Passed | 354 passed in 27.73s |
| Ruff check | Seven changed Python files | Passed | All checks passed |
| Ruff format check | Seven changed Python files | Passed | 7 files already formatted |
| Git diff check | Working-tree patch | Passed | No whitespace errors |

## Delivery Reconciliation

* Plan markers and phase details: Current; all revised full-plan markers complete.
* Blockers: None.
* Reviewer outcome: Fry approved the authentic v1 destructive-rebuild scenario
  and preserved regression coverage.
* Delivery evidence: 354 targeted tests passed; Ruff check and format checks
  passed; `git diff --check` passed.
* Remaining work: Commit, push, and reply to and resolve
  `PRRT_kwDOSzuis86krd7s`.
* Follow-up items: None.
* Scope guard: No branch switch or PR #684 mutation was performed.
