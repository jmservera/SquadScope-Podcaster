# Changes Log: CodeQL ReDoS and stack-trace exposure fixes

Task ID: codeql-redos-stacktrace
Date: 2026-09-28

## Scope

Full plan `.copilot-tracking/plans/2026-09-28/codeql-redos-stacktrace-plan.md`, phase P01.

## P01-T01 Section parser hardening

Implemented deterministic parsing in `podcaster/sections.py`:
- Replaced section-header regex matching with bounded string parsing for heading marks, keyword, separator, and title extraction.
- Replaced fallback generic speaker regex matching with `partition(":")`, explicit label length, first-character, allowed-character, and non-empty text checks.
- Updated docstrings to remove stale references to the removed section-header regex.

## P01-T02 Monitoring response sanitization

Implemented generic client-visible responses in `podcaster/monitoring.py`:
- `/api/review` `ValueError` now logs server-side details and returns `{"error": "review request not found"}`.
- Credential create/update `ValueError` now logs server-side details and returns `{"error": "invalid credential payload"}`.
- Podcast-config save `ValueError` now logs server-side details and returns `{"error": "invalid podcast config payload"}`.

## P01-T03 Regression tests

Updated tests:
- `tests/test_sections.py` adds a pathological whitespace-heavy timing test for section-header and generic speaker parsing.
- `tests/test_monitoring.py` asserts alerted error paths do not expose exception text in response bodies and do preserve details in server-side logs.

## Validation

- Focused: `python3 -m pytest -q tests/test_sections.py tests/test_monitoring.py` — passed, 136 tests.
- Full: `python3 -m pytest -q` — passed, 3642 passed, 4 skipped, 2 deselected, 1 warning.
- Lint: `python3 -m ruff check podcaster tests` — passed.

## Remaining

- Commit, push, PR, and CI watch.
