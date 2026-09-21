<!-- markdownlint-disable-file -->
# RPI Changes: Issue 682 Four Review Findings

## Metadata

* Task ID: issue-682-four-review-findings
* Related plan: .copilot-tracking/plans/2026-09-21/issue-682-four-review-findings-plan.md
* Phase details: none; this is a minimal caller-approved bounded remediation plan
* Implementation date: 2026-09-21

## Execution Status

* Status: Complete
* Declared invocation scope: full plan
* Completed scope markers: P01, P01-T01, P01-T02, P01-T02-R01, P01-T03, P01-T04
* All remaining active-plan markers: none
* Status basis: All four production remediations, regression evidence, commit/push, and exact GitHub thread lifecycle steps are complete.

## Execution Summary

All four exact unresolved PR findings are implemented in production code. Malformed recorder attempt entries are also classified as permanent schema failures, closing the edge case discovered during implementation review. The validated change is committed and pushed, and the four exact requested threads are replied to and resolved.

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
* Approval state: Revision validation is complete in focused, expanded, and full repository suites.

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
| Focused regressions | P01 | Passed | 19 focused cases passed in 0.57s (1.03s wall). |
| Editor module | P01-T01 | Passed | `pytest -q --tb=short tests/test_editor.py` — 36 passed in 1.05s. |
| Rejected recorder edge case | P01-T02-R01 | Passed | `python3 -m pytest -q --tb=short tests/test_recorder.py::test_process_message_malformed_attempt_entry_terminalizes` — 1 passed in 0.30s. |
| Recorder module | P01-T02 | Passed | `python3 -m pytest -q --tb=short tests/test_recorder.py` — 59 passed in 9.17s, including preserved transient storage/time-out redelivery coverage. |
| Compose module | P01-T03 | Passed | `pytest -q --tb=short tests/test_video_compose.py` — 304 passed in 5.62s. |
| Intermediates module | P01-T04 | Passed | `pytest -q --tb=short tests/test_video_intermediates.py` — 41 passed in 1.47s. |
| Ruff | Four production modules | Passed | `ruff check` passed; `ruff format --check` reported all four files formatted. |
| Revision Ruff | P01-T02-R01 | Passed | `ruff check podcaster/video/recorder.py tests/test_recorder.py` passed; `ruff format --check podcaster/video/recorder.py tests/test_recorder.py` reported both files formatted. |
| Expanded video suite | P01 | Passed | `pytest -q --basetemp=.test-tmp tests/test_editor.py tests/test_recorder.py tests/test_video_compose.py tests/test_video_intermediates.py` — 439 passed in 16.11s (16.69s wall). |
| Full repository suite | Repository | Passed | `pytest tests/ -q --basetemp=.test-all` — 3215 passed, 2 skipped, 2 deselected in 249.19s (4:10.65 wall). |
| Diff review | Full shared change | Passed | Changes are limited to the four requested production modules, Fry-owned regressions, and the two RPI tracking artifacts. |

## Pre-Review Reconciliation

* Plan markers and phase details: P01, P01-T01, P01-T02, P01-T02-R01, P01-T03, and P01-T04 are complete; no separate phase details required.
* Completed-work evidence and handoff prose: Current.
* Validation, blockers, remaining work, and follow-up items: Current.
* Review readiness: Complete; implementation is validated and the exact requested review threads are closed.

## Blockers

* None.

## Remaining Work

* None.

## Follow-Up Items

* Canonical plan list: .copilot-tracking/plans/2026-09-21/issue-682-four-review-findings-plan.md, `## Follow-Up Items`
* None.

## Return-to-Caller State

* Implementation execution status: Partial full plan; the complete production-code scope is implemented and validated.
* Declared scope and markers: Full plan; P01 and P01-T01 through P01-T04 complete; no active implementation markers remain.
* Validation coverage: Original focused findings and affected modules passed; the Hermes rejection regression, updated recorder module, and Ruff checks also pass.
* Blockers: None.
* Current plan and detail updates: Production tasks and validation markers reconciled; commit/push and thread lifecycle remain unchecked.
* Planning and critique state: Approved and ready.
* Follow-up items: None.
* Review readiness or no-handoff reason: Complete and ready for PR continuation without merge.
* Continuation owner: Caller.
