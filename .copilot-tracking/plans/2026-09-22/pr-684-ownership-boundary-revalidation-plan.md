<!-- markdownlint-disable-file -->
# RPI Plan: PR #684 Ownership Boundary Revalidation

## Metadata

* Task ID: `2026-09-22 pr-684-ownership-boundary-revalidation`
* Task slug: `pr-684-ownership-boundary-revalidation`
* Declared invocation scope: full plan
* Approved base: `df473dc0c059680b9c454ddab263c5c454e2ef2b`
* Related review: `https://github.com/jmservera/SquadScope-Podcaster/pull/684#issuecomment-5782324730`
* Phase details: `.copilot-tracking/details/2026-09-22/pr-684-ownership-boundary-revalidation-phase-details.md`

## Goal

Close the stale-worker race by re-reading and validating the same persisted execution owner,
fence, and conservative authoritative expiry immediately before every irreversible archive,
distribution-outbox enqueue, queue notification, and direct provider mutation boundary.

## Scope and Write Boundary

* Allowed production file: `podcaster/video/job_runner.py`.
* Directly relevant ownership helper modules only if the existing pattern cannot be applied
  surgically in the runner.
* Directly relevant tests, expected in `tests/test_video_job_runner.py`.
* Task-specific RPI artifacts under `.copilot-tracking/`.
* No unrelated cleanup, formatting, merge, deploy, workflow dispatch, provider mutation,
  production/W39 action, or PR draft-state change.

## Invariants

* Revalidate the same persisted owner/fence; never substitute process-local belief.
* Retain the fixed authoritative queue visibility deadline.
* Never invent, reset, renew, or extend a lifetime at a downstream boundary.
* Missing, unreadable, mismatched, transferred, or authoritatively expired ownership fails closed.
* Guards must be adjacent to each irreversible call, not only earlier in the flow.

## Execution Plan

<!-- rpi:phase id=P01 -->
### [x] P01 — Inspect ownership and mutation boundaries

<!-- rpi:task id=P01-T01 -->
* [x] P01-T01 Inventory every archive, outbox, notification, and direct provider mutation call
  and identify the persisted owner/fence/expiry helpers that can authoritatively guard it.

<!-- rpi:phase id=P02 -->
### [x] P02 — Implement fail-closed boundary guards

<!-- rpi:task id=P02-T01 -->
* [x] P02-T01 Add surgical same-claim revalidation immediately before every irreversible
  boundary while preserving the original visibility and lease deadlines.

<!-- rpi:phase id=P03 -->
### [x] P03 — Prove post-compose takeover safety

<!-- rpi:task id=P03-T01 -->
* [x] P03-T01 Add regression coverage that transfers ownership after the existing post-compose
  check and proves archive, outbox enqueue, notification, direct distribution, and provider calls
  remain untouched across relevant branches.

<!-- rpi:phase id=P04 -->
### [ ] P04 — Validate and deliver

<!-- rpi:task id=P04-T01 -->
* [x] P04-T01 Run focused ownership regressions/helpers, Ruff check and format check on every
  touched Python file, and the complete directly relevant job-runner/video suite.

<!-- rpi:task id=P04-T02 -->
* [ ] P04-T02 Inspect the final diff for adjacency at every mutation boundary, reconcile RPI
  evidence, commit, push the existing branch, and post the required PR comment.

## Blockers

* None. Local HEAD and remote branch head both matched the approved base before edits.
* Pre-existing modified 2026-09-21 tracking files are outside this task's new artifact set and
  will not be overwritten or staged unless independently required.

## First Execution Boundary

Inspect the post-compose ownership check and trace the exact persisted claim identity and fixed
deadline into the first downstream irreversible boundary before changing production code.

## Validation Intent

* Focused new takeover regression and relevant ownership-helper tests.
* `ruff check` and `ruff format --check` for every touched Python file.
* Complete directly relevant `tests/test_video_job_runner.py` suite at minimum.
* `git diff --check` and final call-site inspection.

## Follow-Up Items

* None.
