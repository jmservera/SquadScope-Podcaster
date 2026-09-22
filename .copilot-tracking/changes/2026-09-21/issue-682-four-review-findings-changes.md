<!-- markdownlint-disable-file -->
# RPI Changes: Issue 682 Four Review Findings

## Metadata

* Task ID: issue-682-four-review-findings
* Related plan: .copilot-tracking/plans/2026-09-21/issue-682-four-review-findings-plan.md
* Phase details: none; this is a minimal caller-approved bounded remediation plan
* Implementation date: 2026-09-21

## Execution Status

* Status: Partial full plan; the Fry-rejected recorder numeric schema correction and validation are complete.
* Declared invocation scope: full plan
* Completed scope markers: P01, P01-T01, P01-T02, P01-T02-R01, P01-T02-R02, P01-T03, P01-T04
* All remaining active-plan markers: none
* Remaining delivery steps: Create and push the single correction commit, reply to the recorder review thread, and re-query PR head/unresolved threads.
* Status basis: Numeric conversion failures now enter the existing permanent malformed-history taxonomy; focused and expanded validations pass.

## Execution Summary

All four exact unresolved PR findings are implemented in production code. Hermes initially rejected the recorder artifact because malformed attempt-history entries could escape permanent-failure classification. Under strict reviewer lockout, Farnsworth independently revised that validation and added regression coverage. Hermes subsequently returned final APPROVE for all four fixes. Fry's final pre-commit validation passed; commit/push and the four GitHub thread lifecycle actions remain pending.

Fry later rejected requirement 2 because non-finite numeric schema input still escapes the permanent malformed-state taxonomy. Hermes owns this independent revision; Bender is locked out. The correction is limited to numeric schema parsing in `podcaster/video/recorder.py`, one focused recorder regression, and this plan/changes evidence.

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

### Opened Fry-rejected numeric schema correction

* Related markers: P01-T02, P01-T02-R02
* Approved write boundary: `podcaster/video/recorder.py`, `tests/test_recorder.py`, and the existing 2026-09-21 plan/changes artifacts.
* What must change: Attempt-history `schema_version` parsing must reject overflow, non-finite, and malformed values through the existing permanent malformed-history path rather than allowing numeric conversion exceptions to become retries.
* Triggering evidence: JSON input containing `{"schema_version":1e400,"executions":[]}` decodes the schema value as positive infinity; `int()` raises `OverflowError`, which `_begin_execution` does not classify as permanent.
* Validation intent: Run the focused regression, full recorder module, the four affected video modules when reasonable, Ruff check/format-check on touched Python, and `git diff --check`.
* Revision ownership: Hermes independently owns this correction; Bender must not contribute.
* Blockers: None.

### Corrected numeric schema error taxonomy

* Related markers: P01-T02, P01-T02-R02
* Files: `podcaster/video/recorder.py`, `tests/test_recorder.py`
* What changed and why: `_attempt_document` now normalizes schema conversion failures, including `OverflowError` from non-finite JSON numbers, to `ValueError`. `_begin_execution` already maps that parser error to `PermanentRecorderSetupError`, so malformed durable input follows the established terminal fallback and queue-deletion path.
* Regression evidence: The exact durable payload `{"schema_version":1e400,"executions":[]}` returns `recording_insufficient`, writes the terminal manifest, and deletes the queue message.
* Scope review: No operation-runner, fake-clock, retry, or unrelated video behavior changed.
* Full-suite decision: Not repeated because the correction is isolated to attempt-history schema conversion and the focused, full recorder, and complete four-module affected suites passed; the prior 3215-test full-suite evidence remains applicable.

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
| Numeric schema overflow regression | P01-T02-R02 | Passed | `python3 -m pytest -q --tb=short tests/test_recorder.py::test_process_message_overflow_attempt_schema_terminalizes` — 1 passed in 0.29s. |
| Recorder module after numeric correction | P01-T02-R02 | Passed | `python3 -m pytest -q --tb=short tests/test_recorder.py` — 60 passed in 9.68s. |
| Compose module | P01-T03 | Passed | `pytest -q --tb=short tests/test_video_compose.py` — 304 passed in 5.62s. |
| Intermediates module | P01-T04 | Passed | `pytest -q --tb=short tests/test_video_intermediates.py` — 41 passed in 1.47s. |
| Ruff | All eight touched Python files | Passed | Fry final run: `ruff check` passed; `ruff format --check` reported 8 files formatted. |
| Expanded video suite | P01 | Passed | Fry final affected-module grouping: 440 tests passed in 17.64s pytest time (18.24s wall). |
| Expanded video suite after numeric correction | P01-T02-R02 | Passed | `python3 -m pytest -q --tb=short tests/test_editor.py tests/test_recorder.py tests/test_video_compose.py tests/test_video_intermediates.py` — 441 passed in 17.01s. |
| Full repository suite | Repository | Passed | `pytest tests/ -q --basetemp=.test-all` — 3215 passed, 2 skipped, 2 deselected in 249.19s (4:10.65 wall). |
| Full repository suite repeat | Repository | Skipped | Tightly local parser-taxonomy correction is covered by the focused regression, 60-test recorder suite, and 441-test affected video suite; prior full-suite evidence remains applicable. |
| Diff review | Full shared change | Passed | Final scope review found no scope creep; `git diff --check` passed. |
| Ruff after numeric correction | P01-T02-R02 | Passed | `ruff check podcaster/video/recorder.py tests/test_recorder.py` and `ruff format --check podcaster/video/recorder.py tests/test_recorder.py` — all checks passed; 2 files already formatted. |
| Independent security review | P01-T02-R01 and all four fixes | Passed | Hermes' initial rejection was corrected under strict reviewer lockout by Farnsworth; Hermes' final disposition is APPROVE. |

## Pre-Review Reconciliation

* Plan markers and phase details: P01, P01-T01, P01-T02, P01-T02-R01, P01-T02-R02, P01-T03, and P01-T04 are complete; no separate phase details required.
* Completed-work evidence and handoff prose: Current.
* Validation, blockers, remaining work, and follow-up items: Current.
* Review readiness: The independently owned Hermes correction and required validation are complete; the artifact is ready for commit/push and recorder-thread evidence reply.

## Blockers

* None.

## Remaining Work

* Create the conventional correction commit and push it to `origin/squad/video-stage-budget-redesign`.
* Reply to recorder thread `PRRT_kwDOSzuis86kim9A`, then re-query PR head and all unresolved threads without changing unrelated resolution state.

## Follow-Up Items

* Canonical plan list: .copilot-tracking/plans/2026-09-21/issue-682-four-review-findings-plan.md, `## Follow-Up Items`
* None.

## Return-to-Caller State

* Implementation execution status: Partial full plan; the numeric schema correction is implemented and validated, with delivery actions pending.
* Declared scope and markers: Full plan; P01 and P01-T01, P01-T02, P01-T02-R01, P01-T02-R02, P01-T03, and P01-T04 complete; no active implementation markers remain.
* Validation coverage: Exact overflow regression 1 passed, recorder module 60 passed, affected four-module video suite 441 passed, Ruff check/format-check passed on the two touched Python files, and `git diff --check` passed.
* Blockers: None.
* Current plan and detail updates: P01 and all implementation tasks, including P01-T02-R02, are complete; commit/push and GitHub recorder-thread evidence remain unchecked.
* Planning and critique state: Fry's numeric overflow rejection was corrected independently by Hermes under the Bender lockout.
* Follow-up items: None.
* Review readiness or no-handoff reason: Pre-commit correction evidence is finalized and ready for delivery continuation; the recorder-thread reply must cite the pending commit.
* Continuation owner: Hermes.

## Independent Post-Review Revision

* Revision owner: Amy; original author Bender remains locked out.
* Approved scope: composed-checkpoint DOG identity, YouTube resumable completion ambiguity, and playlist insert ambiguity.
* Implementation state: P02-T01 through P02-T03 are implemented with focused regressions.

## Final Lifecycle Safety Revision

* Nested owned-process safety: subprocesses launched from an owned callable now inherit its session, while their own timeout path terminates the bounded descendant tree. The outer callable timeout therefore cannot leave a nested ffmpeg/ffprobe process running.
* Recorder finalization taxonomy: only renderer or asset failures become terminal fallback insufficiency. Upload, readback, legacy upload, and storage-integrity failures clean partial content and propagate for queue retry.
* FANIN retry boundary: an owned recording timeout is never retried, and retry backoff is rejected before sleeping when it would cross the remaining FANIN deadline.
* Focused validation: `pytest -q tests/test_video_process.py tests/test_recorder.py tests/test_video_gen.py tests/test_video_compose.py tests/test_youtube_upload.py tests/test_youtube_playlist.py` — 641 passed, 2 deselected.
* Full lint validation: `ruff check podcaster tests` and `ruff format --check podcaster tests` passed; all 187 Python files are formatted.
* Full test validation: the first repository run passed 3271 tests, with three environment/state failures (a stale Docker Compose stack and two dirty local artifact races). Each failed test passed independently after the stale stack was removed; no production or test gate was weakened.
