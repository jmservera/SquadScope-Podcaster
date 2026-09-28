# Phase Details: CodeQL ReDoS and stack-trace exposure fixes

Task ID: codeql-redos-stacktrace
Date: 2026-09-28

## P01 — Implement and verify CodeQL fixes

Context:
- Research shows both section alerts can be removed by replacing regex matching over user script lines with deterministic string parsing.
- Research shows monitoring alerts are caused by returning `str(exc)` in four API response bodies.

### P01-T01 Replace section regex usage with deterministic parsing helpers

Expected work:
- Preserve prior matching contract for section headers.
- Preserve prior matching contract for generic speaker labels.
- Remove or stop using vulnerable regexes for the alerted line-level parsing.

Completion evidence:
- Existing section tests pass.
- New pathological-input test passes with a conservative elapsed-time bound.

### P01-T02 Sanitize four monitoring error response paths

Expected work:
- `/api/review` `ValueError` path returns a generic 404 body.
- Credential create/update `ValueError` paths return generic 400 bodies.
- Podcast-config save `ValueError` path returns a generic 400 body.
- Each path logs details server-side with traceback.

Completion evidence:
- Monitoring tests prove exception text is absent from response bodies and present in logs.

### P01-T03 Add and update regression tests

Expected work:
- Add pathological whitespace-heavy inputs for `match_section_header` and generic speaker fallback.
- Update existing missing-job/rejected-payload monitoring tests for generic response bodies.
- Add log assertions where needed.

Completion evidence:
- Targeted tests pass before full-suite validation.

### P01-T04 Run requested validation and fix failures

Expected work:
- Run `python3 -m pytest -q`.
- Run `python3 -m ruff check podcaster tests`.
- Fix only failures caused by this task.

### P01-T05 Record changes and review artifacts

Expected work:
- Write `.copilot-tracking/changes/2026-09-28/codeql-redos-stacktrace-changes.md`.
- Write `.copilot-tracking/reviews/logs/2026-09-28/codeql-redos-stacktrace-review.md`.

### P01-T06 Commit, push, create PR, and watch CI

Expected work:
- Commit with required co-author trailer.
- Push `squad/716-717-redos-stacktrace`.
- Create PR with issue-closing lines for jmservera/SquadScope-Podcaster#716 and jmservera/SquadScope-Podcaster#717.
- Run `gh pr checks --watch` and address failures if present.

