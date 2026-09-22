<!-- markdownlint-disable-file -->
# Phase Details: Clipset Schema Rollout Compatibility

## Active Scope

Full plan (`P01` and `P02`).

<!-- rpi:phase id=P01 -->
## P01 — Persisted Schema and Migration

### Purpose

Remove ambiguity between pre-budget v1 data and budget-bearing clipsets, while keeping rollout recovery deterministic.

<!-- rpi:task id=P01-T01 -->
### P01-T01 — Version and validate clipsets

* Bump the serialized clipset schema for the required budget-bearing shape.
* Add explicit schema and budget validation errors.
* Retain a narrow parser for authentic v1 documents with object or null budget.

Completion evidence: v2 rejects missing, null, and non-object budgets; v1
accepts only object or null and preserves job, clip plan, and durable budget
identity.

<!-- rpi:task id=P01-T02 -->
### P01-T02 — Repair before fan-out and retry safely

* In `plan_or_load_clipset`, treat every readable authentic v1 plan as
  authoritative and never replace it or delete its clip prefix for metadata.
* If object-budget v1 metadata is upgraded, preserve its existing projection and
  use storage CAS conventions; concurrent winners remain authoritative.
* Reject malformed budget metadata without plan replacement or artifact cleanup.
* Classify invalid budget metadata as recoverable recorder setup so the queue
  message remains available for repair/redelivery.

Completion evidence: changed-segment redelivery retains the original plan and
completed artifacts; concurrent replay is CAS-safe; recorder does not delete or
terminalize malformed-budget messages.

<!-- rpi:phase id=P02 -->
## P02 — Regression Validation

<!-- rpi:task id=P02-T01 -->
### P02-T01 — Add rollout regressions

* Construct authentic pre-change v1 JSON documents with object and null budgets.
* Prove object-budget migration preserves timestamps, clips, and completion sentinels.
* Prove null-budget replay remains readable and immutable.
* Parameterize missing, malformed, and non-object budget values.
* Prove recorder rejection returns retry, retains the queue message, and writes no terminal manifest.
* Prove concurrent migration/replay honors the CAS winner.

<!-- rpi:task id=P02-T02 -->
### P02-T02 — Run targeted checks

Planned commands:

* `pytest tests/test_clipset.py tests/test_editor.py tests/test_recorder.py tests/test_job_runner.py tests/test_video_job_runner.py -q`
* `ruff check` on changed Python files.
* `ruff format --check` on changed Python files.

## Approved Write Boundary

* `.copilot-tracking/{plans,details,changes}/2026-09-22/clipset-schema-rollout-compatibility-*`
* `podcaster/video/clipset.py`
* `podcaster/video/editor.py`
* `podcaster/video/recorder.py`
* Directly related tests under `tests/`

## Blockers

* None.

## Reviewer Approval

* Fry approved the authentic v1 destructive-rebuild regression revision.
* Delivery validation: 354 targeted tests passed; Ruff check and format checks
  passed on all changed Python files; `git diff --check` passed.

## Revision Completion Evidence

* P01-T01: v1 accepts only an object or explicit null budget; v2 accepts only
  an object; missing, scalar, list, and invalid object values raise
  `ClipsetBudgetError`.
* P01-T02: v1 metadata upgrades preserve the persisted clip tuple and existing
  object budget, use `update_bytes` CAS, accept a concurrent winner, and never
  invoke cleanup for schema/budget errors. Null-budget v1 remains readable and
  is upgraded only when an authoritative current budget is supplied.
* P02-T01: authentic object/null fixtures, changed-segment replay, completed
  manifest/media preservation, durable timestamp retention, malformed-budget
  immutability, recorder retry, and concurrent migration are covered.
* P02-T02: requested targeted tests report 354 passed; Ruff check and format
  checks pass on all changed Python files; `git diff --check` passes.
