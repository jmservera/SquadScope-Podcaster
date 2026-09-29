<!-- markdownlint-disable-file -->
# Plan: Issue 682 Four Review Findings

## Objective

Implement only the four approved PR #682 review remediations, add focused regressions, validate the affected video pipeline, and close the exact corresponding review threads.

## Scope

* Harden terminal clip-manifest ownership validation in `editor.py`.
* Preserve recorder queue redelivery for transient setup storage/time-out failures.
* Expand composed-checkpoint identity for behavior-affecting segment metadata.
* Preserve an existing intermediate destination when remote validation fails.
* Add focused tests, run the relevant video suite and Ruff, review scope, commit, push, reply to and resolve only the four identified threads.

## Non-Goals

* Unrelated review threads, refactors, CI weakening, or behavior outside the four findings.

## Implementation

<!-- rpi:phase id=P01 -->
### [x] P01 — Four bounded remediations

- [x] <!-- rpi:task id=P01-T01 --> Validate terminal `clip_id` and exact content-addressed `media_blob_path` before editor download; add regression coverage.
- [x] <!-- rpi:task id=P01-T02 --> Restrict recorder setup terminalization to permanent timing/schema failures while retaining transient storage/time-out messages; add operation-runner/fake-clock taxonomy regressions.
- [x] <!-- rpi:task id=P01-T02-R01 --> Independently revise rejected recorder attempt-history validation so every execution entry must be an object before field access; add malformed-entry terminalization coverage.
- [x] <!-- rpi:task id=P01-T02-R02 --> Independently revise rejected recorder numeric schema parsing so overflow, non-finite, and malformed schema values terminalize as permanent malformed attempt history; add focused queue-deletion coverage.
- [x] <!-- rpi:task id=P01-T03 --> Add `source_url` and `removed_reason` to composed checkpoint identity and prove metadata changes invalidate reuse.
- [x] <!-- rpi:task id=P01-T04 --> Preserve `dest` across every intermediate download/validation failure and test replacement only after successful validation.

## Validation

- [x] Focused regression tests for all four findings pass.
- [x] Repository-defined relevant video test grouping passes, with exact command and duration recorded.
- [x] `ruff check` and `ruff format --check` pass for all touched Python files.
- [x] Final diff contains no scope creep.
- [x] Hermes' initial rejection was handled under strict reviewer lockout by an independent Farnsworth revision; Hermes' final re-review disposition is APPROVE.
- [x] Fry's numeric schema-overflow rejection is corrected independently by Hermes with focused and relevant recorder/video validation.
- [ ] One new conventional commit is pushed to `origin/squad/video-stage-budget-redesign`.
- [ ] Exact four review threads are replied to with commit/evidence and resolved; unresolved threads are re-queried.

## Follow-Up Items

* None.

<!-- rpi:phase id=P02 -->
### [x] P02 — Independent post-review revision

- [x] <!-- rpi:task id=P02-T01 --> Bind normalized DOG configuration and resolved logo content SHA-256 into composed-checkpoint identity before resume admission.
- [x] <!-- rpi:task id=P02-T02 --> Classify identifier-less YouTube resumable success responses as retry-blocked unknown outcomes.
- [x] <!-- rpi:task id=P02-T03 --> Classify transient playlist insert responses as retry-blocked unknown outcomes without a second insert.
- [x] <!-- rpi:task id=P02-T04 --> Validate the three fixes with focused regression coverage.

<!-- rpi:phase id=P03 -->
### [x] P03 — Final lifecycle safety findings

- [x] <!-- rpi:task id=P03-T01 --> Keep subprocesses launched inside an owned callable in the callable session so the outer deadline kills the complete nested process tree.
- [x] <!-- rpi:task id=P03-T02 --> Propagate fallback storage/finalization failures for retry instead of writing a permanent `recording_insufficient` manifest.
- [x] <!-- rpi:task id=P03-T03 --> Stop recording retries immediately on owned timeout and reject backoff that would cross the FANIN deadline.
- [x] <!-- rpi:task id=P03-T04 --> Run focused, affected-suite, Ruff, formatting, and diff validation; deliver one conventional commit and resolve only proven threads.

<!-- rpi:phase id=P04 -->
### [x] P04 — Terminal-state persistence recovery

- [x] <!-- rpi:task id=P04-T01 --> Handle `TerminalStatePersistenceError` before generic transient retry exhaustion so terminal persistence failures never delete or acknowledge the queue message, including at or above `MAX_DEQUEUE_COUNT`; add focused regression coverage while preserving all other poison and retry semantics.
- [x] <!-- rpi:task id=P04-T02 --> Run focused and immediately affected tests plus Ruff checks for every changed Python file, review the final diff for scope creep, and confirm PR #684 remains untouched.
