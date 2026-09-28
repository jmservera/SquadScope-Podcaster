# Implementation Plan: CodeQL ReDoS and stack-trace exposure fixes

Task ID: codeql-redos-stacktrace
Date: 2026-09-28
Owner: Frank

## Executive Summary

Fix two CodeQL issue groups in one PR by replacing vulnerable script-section regex matching with deterministic linear parsing and by changing four monitoring API exception paths to generic client-visible errors with server-side logging. Validation must include full pytest, ruff, push, PR creation closing both issues, and CI watch.

## Sources

- `.copilot-tracking/research/2026-09-28/codeql-redos-stacktrace-research.md`
- `podcaster/sections.py`
- `podcaster/monitoring.py`
- `tests/test_sections.py`
- `tests/test_monitoring.py`

## User Decisions and Requirements

- Use a worktree; do not switch branches in `/home/azureuser/source/SquadScope-Podcaster`.
- Address jmservera/SquadScope-Podcaster#716 alerts #31 and #32 at `podcaster/sections.py`.
- Address jmservera/SquadScope-Podcaster#717 alerts #27-#30 at `podcaster/monitoring.py`.
- Add a pathological-input test with a time bound for regex changes.
- Return generic error bodies and log details server-side for stack-trace exposure paths.
- Do not suppress/dismiss CodeQL findings or weaken tests.
- Stay out of `podcaster/publish.py`, `podcaster/storage.py`, and `podcaster/orchestration.py`.
- Validate with `python3 -m pytest -q` and `python3 -m ruff check podcaster tests`.
- Commit with required trailer, push, create a PR closing both issues, and watch CI.

## Goals

- Remove polynomial-regex behavior from the alerted section parsing paths.
- Prevent exception details from being returned to API clients in the alerted monitoring paths.
- Preserve existing accepted parsing and API behavior except for intentionally generic error responses.
- Provide regression tests that prove pathological inputs are bounded and exception details are logged but not exposed.

## Scope and Non-Goals

In scope:
- `podcaster/sections.py`
- `podcaster/monitoring.py`
- Directly related tests in `tests/test_sections.py` and `tests/test_monitoring.py`
- RPI tracking artifacts under `.copilot-tracking/`

Out of scope:
- Concurrent path-injection work in publish/storage/orchestration files.
- Broad API error contract redesign outside the four alerted flows.
- CodeQL alert suppression or dismissal.

## Functional Requirements

- `match_section_header` must still accept one to six `#`, case-insensitive `section`, `:` or `-`, and flexible surrounding whitespace.
- Generic speaker parsing without config must still accept labels matching the prior allowed characters and length, reject empty text, and ignore non-dialogue lines.
- `/api/review` missing job `ValueError` responses must no longer include exception text.
- Credential create/update and podcast-config save `ValueError` responses must no longer include exception text.
- Server logs must include the exception detail for operator diagnosis.

## Non-Functional Requirements

- Section parsing must be linear in input length for user-provided lines.
- Tests must use conservative timing assertions that catch obvious pathological regressions without being flaky on CI.
- Ruff and full pytest must pass.

## Acceptance Criteria

- Alert #31 and #32 root causes are removed by deterministic parsing or equivalent linear behavior.
- Alert #27-#30 client bodies are generic and details are logged server-side.
- A pathological-input test exercises long whitespace-heavy inputs with a time bound.
- Updated monitoring tests assert sanitized client responses and logged detail.
- Full requested validation passes locally.
- PR body contains `Closes jmservera/SquadScope-Podcaster#716` and `Closes jmservera/SquadScope-Podcaster#717`.
- CI checks are watched and reported.

## Phase Checklist

### P01 — Implement and verify CodeQL fixes

- [x] P01-T01 Replace section regex usage with deterministic parsing helpers.
- [x] P01-T02 Sanitize four monitoring error response paths and add server-side logs.
- [x] P01-T03 Add and update regression tests.
- [x] P01-T04 Run requested validation and fix failures.
- [x] P01-T05 Record changes and review artifacts.
- [ ] P01-T06 Commit, push, create PR, and watch CI.

## Critique Disposition

Self-critique result: pass. The plan directly covers every requested alert, validation command, repository constraint, PR requirement, and concurrent-edit exclusion. No blocking plan changes required.

## Follow-Up Items

- None.

## Handoff

Implementation may proceed with changes-record path `.copilot-tracking/changes/2026-09-28/codeql-redos-stacktrace-changes.md`.
