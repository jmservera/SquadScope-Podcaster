# Implementation Plan: scaleout-fanout-flake

| Field | Value |
|-------|-------|
| Task ID | scaleout-fanout-flake |
| Date | 2026-09-29 |
| Status | Implemented / review-ready |
| Research | .copilot-tracking/research/2026-09-29/scaleout-fanout-flake-research.md |
| Phase details | .copilot-tracking/details/2026-09-29/scaleout-fanout-flake-phase-details.md |

## Executive Summary

Local `test_scaleout_fanout_end_to_end` failed 20/20 because the Docker Compose harness reused a fixed `podcaster-synthesis:test` image tag built from another video branch/worktree. The minimal fix is to isolate/build the fan-out test image for the current worktree before recorder scale-out runs, preserving product behavior and avoiding sleeps/skips/xfails. CI already discovers/runs the test path; no CI gate rewrite is required unless runtime evidence later shows Docker unavailable.

## User Decisions and Requirements

* Use hve-core RPI (`rpi`) lifecycle.
* Base on current `origin/main` in `/home/azureuser/source/SquadScope-Podcaster-wt-723` from `git worktree add ../SquadScope-Podcaster-wt-723 -b squad/723-scaleout-flake origin/main`; do not switch branches in the original Podcaster repo.
* Research the test and fan-out code path, reproduce in a 20-run loop, classify product race vs harness issue, and fix deterministically without skip/xfail.
* Check whether CI runs the integration test and coordinate with the `ci / test` job if a gate gap exists.
* Keep changes minimal because jmservera/SquadScope-Podcaster#682 changes nearby video code.
* Validate with `python3 -m pytest -q`, `ruff check`, `ruff format --check`, and 20 consecutive green targeted runs.
* Commit with the required Copilot co-author trailer, push, open a PR titled/described to close jmservera/SquadScope-Podcaster#723, do not merge.

## Goals

* Make the scale-out fan-out integration test deterministic on local machines/worktrees.
* Preserve existing product fan-out semantics: clip upload before manifest sentinel, manifest-based fan-in, idempotent recorder handling, additive fan-out.
* Preserve or improve CI coverage truthfulness without weakening tests.

## Scope and Non-Goals

* In scope: `tests/integration/test_scaleout_fanout.py`, `docker-compose.fanout.yml`, directly related test assertions/helpers, validation and PR metadata.
* In scope if necessary: a minimal CI path/gate fix, but research currently shows CI path selection already includes the test.
* Non-goals: broad video pipeline redesign, changing recorder/editor product ordering without evidence, skipping/xfailing integration tests, merging the PR.

## Functional Requirements

* The integration harness must build/use current worktree code before starting recorder containers.
* The image tag must avoid stale cross-worktree reuse where practical.
* The recorder fan-out assertion must continue to require each expected clip and manifest.
* CI must still run/collect the test; if skipped due Docker availability that must be reported, not hidden.

## Non-Functional Requirements

* Minimal, readable changes.
* No sleeps/retries that mask product races.
* No secrets or sensitive data in logs/artifacts.
* Compatible with concurrent worktrees/branches as much as Docker Compose permits.

## Acceptance Criteria

* Baseline failure rate recorded: 0/20 pass, 20/20 fail on current local state.
* Post-fix targeted test passes 20 consecutive runs.
* `python3 -m pytest -q` passes.
* `ruff check podcaster tests` passes.
* `ruff format --check podcaster tests` passes.
* PR opened against main with `Closes jmservera/SquadScope-Podcaster#723` and note about jmservera/SquadScope-Podcaster#682 interaction/no interaction.
* CI status and Copilot review thread state reported; valid review threads addressed up to two rounds.

## Initial Evidence and Readiness

* Research artifact records C1-C5: CI collection/gate evidence, 20-run baseline failure, stale-image recorder logs, and fixed image tag/no-build harness cause.
* Current product code path uses manifest-after-clip sentinels and fan-in waits on manifest existence; no observed current-product race caused the baseline symptom.

## Phase Checklist

### P01 — Harness isolation fix

* P01-T01 (done): Update fan-out compose/test harness to build the current worktree image and use a unique test image tag.
* P01-T02 (done): Keep recorder assertions strict and avoid sleeps/retries.

### P02 — Validation and PR delivery

* P02-T01 (done): Run focused targeted test once, then 20 consecutive targeted runs.
* P02-T02 (done): Run full pytest and ruff checks.
* P02-T03 (in progress): Commit, push, open PR, wait for CI/Copilot review, address valid threads up to two rounds.

## Critique Disposition

* Internal critique not delegated: plan is narrow, evidence-backed, and implementation consists of a minimal deterministic harness correction with explicit validation. No unresolved architecture or requirements choices require separate critique before implementation.

## Follow-Up Items

* PR delivery/review state remains active until PR is opened and CI/Copilot state is checked.

## Handoff

Proceed to implementation with changes record `.copilot-tracking/changes/2026-09-29/scaleout-fanout-flake-changes.md`.
