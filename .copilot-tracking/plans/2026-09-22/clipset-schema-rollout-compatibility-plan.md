<!-- markdownlint-disable-file -->
# Plan: Clipset Schema Rollout Compatibility

## User Decisions and Requirements

* Treat the retrospective revision request for PR #682 as approved full-task scope.
* Recognize authentic pre-change v1 documents, whose serializer always emitted
  `video_budget` as an object or `null`.
* Preserve readable persisted plans and clip artifacts; never rebuild or delete
  them merely to add or validate budget metadata.
* Reject missing, string, list, numeric, and structurally invalid budgets
  explicitly, while making recorder-side rejection recoverable rather than
  terminal.
* Preserve durable job identity, completed-clip replay safety, and existing clipset semantics.
* Bender is reviewer-locked out; Leela is the independent revision author.
* Fry approved the revised implementation and its regression evidence for delivery.

## Executive Summary

Correct the rejected rollout patch so authentic v1 object/null budgets load
without plan replacement, optional metadata upgrades preserve the original
durable budget and use CAS, malformed budgets are rejected without cleanup,
and recorder messages remain available for repair/redelivery.

## Goals

* Prevent rollout-time terminal clip loss.
* Make authentic persisted budget compatibility explicit and testable.
* Keep readable existing clip plans, timestamps, and terminal clip artifacts intact.

## Scope and Non-Goals

* In scope: `podcaster/video/clipset.py`, editor migration/rebuild handling, recorder recoverable setup classification, focused tests, and RPI tracking.
* Non-goals: broader video-budget redesign, queue topology changes, PR/thread mutation, commit, or push.

## Functional Requirements

* Current v2 clipsets require an object-valued `video_budget`.
* Authentic v1 object budgets parse and preserve their durable projection;
  authentic v1 null budgets parse as no budget.
* Missing, scalar, list, and structurally invalid budgets are never deserialized
  as `None`.
* Readable v1 plans remain authoritative across redelivery even when the newly
  supplied segments differ.
* Recorder encounters with malformed budget metadata retain the message for
  retry and do not write a terminal manifest.

## Non-Functional Requirements

* Preserve `job_id`, clip ordering, clip indices, clip content, completed
  manifests/media, and durable timestamps.
* Keep changes surgical and backward-safe for active fan-out jobs.

## Acceptance Criteria

* Authentic serializer fixtures cover both object and null v1 budgets.
* Redelivery with changed segments retains the original three-clip plan and
  completed clip zero.
* Object-budget v1 retains its original durable timestamps through any metadata upgrade.
* Malformed budgets are rejected without plan replacement or artifact deletion.
* Concurrent migration/replay proves CAS safety where supported by storage abstractions.
* Targeted clipset/editor/recorder/job tests and Ruff pass.

<!-- rpi:phase id=P01 -->
## [x] P01 — Persisted Schema and Migration

<!-- rpi:task id=P01-T01 -->
### [x] P01-T01 — Version and validate clipsets

Require a budget object in the new persisted schema and expose explicit compatibility errors.

<!-- rpi:task id=P01-T02 -->
### [x] P01-T02 — Preserve before fan-out and retry safely

Load genuine v1 plans before enqueueing, upgrade metadata only through CAS when
appropriate, never clean up readable plans for budget metadata, and retain
recorder messages for rollout-repairable incompatibility.

<!-- rpi:phase id=P02 -->
## [x] P02 — Regression Validation

<!-- rpi:task id=P02-T01 -->
### [x] P02-T01 — Add rollout regressions

Cover legacy migration, malformed/non-object budget handling, replay safety, and non-terminal recorder behavior.

<!-- rpi:task id=P02-T02 -->
### [x] P02-T02 — Run targeted checks

Run targeted clipset/editor/recorder/job tests and Ruff on changed Python files.

## Dependencies

* P01-T02 depends on P01-T01.
* P02-T01 depends on P01.
* P02-T02 depends on P02-T01.

## Follow-Up Items

* None.

## Closeout

* Reviewer: Fry
* Review outcome: Approved.
* Validation: 354 targeted tests passed; Ruff check and format checks passed;
  `git diff --check` passed.
* Delivery state: Authorized for one commit, branch push, and resolution of the
  exact PR #682 review thread.
