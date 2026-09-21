<!-- markdownlint-disable-file -->
# RPI Changes: Issue 682 Four Review Findings

## Metadata

* Task ID: issue-682-four-review-findings
* Related plan: .copilot-tracking/plans/2026-09-21/issue-682-four-review-findings-plan.md
* Phase details: none; this is a minimal caller-approved bounded remediation plan
* Implementation date: 2026-09-21

## Execution Status

* Status: Partial full plan; implementation, independent revision, final review, and pre-commit validation are complete.
* Declared invocation scope: full plan
* Completed scope markers: P01, P01-T01, P01-T02, P01-T02-R01, P01-T03, P01-T04
* All remaining active-plan markers: none
* Remaining delivery steps: Commit/push and exact GitHub review-thread replies/resolution are pending.
* Status basis: All four production remediations and the independent recorder revision are approved and validated; no commit, push, or GitHub thread closure has occurred in this finalization step.

## Execution Summary

All four exact unresolved PR findings are implemented in production code. Hermes initially rejected the recorder artifact because malformed attempt-history entries could escape permanent-failure classification. Under strict reviewer lockout, Farnsworth independently revised that validation and added regression coverage. Hermes subsequently returned final APPROVE for all four fixes. Fry's final pre-commit validation passed; commit/push and the four GitHub thread lifecycle actions remain pending.

## Implementation-Time Plan and Detail Updates

### Initialized bounded implementation state

* Affected plan area or markers: full plan, P01, P01-T01 through P01-T04
* What changed: Created a minimal ordered plan and this changes record before source edits.
* Why: No matching plan existed and the caller required current tracking artifacts.
* Triggering evidence: PR #682 unresolved threads identify exactly four findings in the named modules.
* User answer or decision: The caller approved the tightly bounded remediation.
* Reconciliation performed: Objective, scope, non-goals, ordered tasks, validation, blockers, and follow-up state are current.
* Planning and critique state: Approved and ready; no critique required.

### Opened production-code implementation

* Affected plan area or markers: full plan, starting with P01-T01
* Approved write boundary: `podcaster/video/editor.py`, `podcaster/video/recorder.py`, `podcaster/video/video_compose.py`, `podcaster/video/intermediates.py`, and the two RPI tracking artifacts.
* Validation intent: Run the smallest available production-module checks covering syntax and Ruff; focused regression and broader video-suite ownership remains with Fry because tests are outside this invocation's write boundary.
* First execution boundary: Inspect existing exception taxonomy, ownership helpers, checkpoint identity, and intermediate-download replacement flow before editing P01-T01.
* Blockers: None.

### Hardened terminal clip ownership

* Related markers: P01-T01
* Files: `podcaster/video/editor.py`
* What changed and why: Terminal manifests now require the expected `clip_id`, valid media evidence, and the exact content-addressed path derived from the current job, clip index, and media SHA-256 before any media download. Empty terminal manifests fail closed; a genuinely absent manifest can still follow the existing explicit gap-fill path.
* Completion evidence: Cross-job, cross-clip, and mismatched digest paths cannot select downloadable media.
* Validation: Editor regression module passed, including the Fry-owned identity mismatch cases.

### Separated permanent and transient recorder setup failures

* Related markers: P01-T02
* Files: `podcaster/video/recorder.py`
* What changed and why: Added a permanent setup-state exception used only for locally parsed malformed clipset, admission, attempt-history, and timing data. Storage operations are bounded through the setup operation runner; storage exceptions and timeouts now return `retry` without a terminal manifest or queue deletion. A missing clipset blob is treated as transient, while malformed bytes are permanent.
* Completion evidence: All three setup storage boundaries retain messages on injected timeouts; malformed clipset, admission, and timing state terminalize as `recording_insufficient`.
* Validation: Recorder regression module passed.

### Independently revised rejected recorder attempt-history validation

* Related markers: P01-T02, P01-T02-R01
* Reviewer finding: Hermes rejected the recorder artifact because an `executions` list containing a non-object reached `.get()`, raised `AttributeError`, and incorrectly preserved the queue message for retry.
* Revision owner: Farnsworth, independently under strict reviewer lockout; Bender did not contribute.
* Files: `podcaster/video/recorder.py`, `tests/test_recorder.py`
* What changed and why: Attempt-history parsing now rejects every execution entry that is not a mapping before any `.get()` access. The existing permanent setup exception path therefore writes a terminal `recording_insufficient` manifest and deletes the queue message.
* Regression evidence: A durable attempt document containing `{"executions":["not-an-object"]}` terminalizes and records queue deletion instead of returning retry.
* Review history: Hermes initially rejected this recorder artifact and imposed strict reviewer lockout.
* Independent revision: Farnsworth owned the correction without Hermes contributing to the implementation.
* Final approval state: Hermes re-reviewed the revised artifact and returned APPROVE; all four fixes are approved.

### Expanded composed checkpoint identity

* Related markers: P01-T03
* Files: `podcaster/video/video_compose.py`
* What changed and why: Added `source_url` and `removed_reason` to each composed checkpoint segment-plan identity entry so metadata that affects source/fallback selection invalidates stale composed output.
* Completion evidence: Parameterized metadata-change regressions force recomposition.
* Validation: Video compose regression module passed.

### Preserved existing intermediate destinations

* Related markers: P01-T04
* Files: `podcaster/video/intermediates.py`
* What changed and why: Removed failure-path deletion of the destination. Downloads remain staged in a unique temporary file, the destination is atomically replaced only after validation, and `finally` cleans only the temporary file.
* Completion evidence: Both download failure and validation failure preserve prior destination bytes.
* Validation: Intermediate-store regression module passed.

## Validation Record

| Check | Scope | Status | Evidence or reason |
|---|---|---|---|
| PR thread identification | PR #682 | Passed | Exact four unresolved thread IDs and paths recorded during implementation bootstrap. |
| Focused regressions | P01 | Passed | Fry final run: 15 focused tests passed in 0.71s pytest time (1.38s wall). |
| Editor module | P01-T01 | Passed | `pytest -q --tb=short tests/test_editor.py` — 36 passed in 1.05s. |
| Rejected recorder edge case | P01-T02-R01 | Passed | `python3 -m pytest -q --tb=short tests/test_recorder.py::test_process_message_malformed_attempt_entry_terminalizes` — 1 passed in 0.30s. |
| Recorder module | P01-T02 | Passed | `python3 -m pytest -q --tb=short tests/test_recorder.py` — 59 passed in 9.17s, including preserved transient storage/time-out redelivery coverage. |
| Compose module | P01-T03 | Passed | `pytest -q --tb=short tests/test_video_compose.py` — 304 passed in 5.62s. |
| Intermediates module | P01-T04 | Passed | `pytest -q --tb=short tests/test_video_intermediates.py` — 41 passed in 1.47s. |
| Ruff | All eight touched Python files | Passed | Fry final run: `ruff check` passed; `ruff format --check` reported 8 files formatted. |
| Expanded video suite | P01 | Passed | Fry final affected-module grouping: 440 tests passed in 17.64s pytest time (18.24s wall). |
| Full repository suite | Repository | Passed | `pytest tests/ -q --basetemp=.test-all` — 3215 passed, 2 skipped, 2 deselected in 249.19s (4:10.65 wall). |
| Diff review | Full shared change | Passed | Final scope review found no scope creep; `git diff --check` passed. |
| Independent security review | P01-T02-R01 and all four fixes | Passed | Hermes' initial rejection was corrected under strict reviewer lockout by Farnsworth; Hermes' final disposition is APPROVE. |

## Pre-Review Reconciliation

* Plan markers and phase details: P01, P01-T01, P01-T02, P01-T02-R01, P01-T03, and P01-T04 are complete; no separate phase details required.
* Completed-work evidence and handoff prose: Current.
* Validation, blockers, remaining work, and follow-up items: Current.
* Review readiness: Hermes APPROVE and Fry validation are complete; the artifact is ready for commit/push and subsequent GitHub thread closure.

## Blockers

* None.

## Remaining Work

* Create the conventional commit and push it to `origin/squad/video-stage-budget-redesign`.
* Reply to and resolve only the exact four identified GitHub review threads, then re-query unresolved threads.

## Follow-Up Items

* Canonical plan list: .copilot-tracking/plans/2026-09-21/issue-682-four-review-findings-plan.md, `## Follow-Up Items`
* None.

## Return-to-Caller State

* Implementation execution status: Partial full plan; the complete production-code scope is implemented, independently revised, approved, and validated.
* Declared scope and markers: Full plan; P01 and P01-T01 through P01-T04 complete; no active implementation markers remain.
* Validation coverage: Fry final results are 15 focused tests passed in 0.71s pytest/1.38s wall, 440 affected-module tests passed in 17.64s pytest/18.24s wall, Ruff check passed, Ruff format check reported 8 files formatted, and `git diff --check` passed.
* Blockers: None.
* Current plan and detail updates: P01 and its implementation tasks are complete; focused tests, affected video grouping, Ruff, diff review, and Hermes final approval are complete; commit/push and thread lifecycle remain unchecked.
* Planning and critique state: Hermes initially rejected the recorder artifact, strict reviewer lockout was observed, Farnsworth independently revised it, and Hermes returned final APPROVE.
* Follow-up items: None.
* Review readiness or no-handoff reason: Pre-commit RPI evidence is finalized and ready for delivery continuation; GitHub thread closure must wait for the pending commit/push.
* Continuation owner: Caller.
