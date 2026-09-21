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
