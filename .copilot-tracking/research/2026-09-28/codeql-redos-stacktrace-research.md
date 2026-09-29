# Research: CodeQL ReDoS and stack-trace exposure alerts

Date: 2026-09-28
Owner: Frank
Output mode: convergence for implementation planning

## Brief

Address jmservera/SquadScope-Podcaster#716 and jmservera/SquadScope-Podcaster#717 without suppressing CodeQL, weakening tests, or touching concurrent-edit files (`podcaster/publish.py`, `podcaster/storage.py`, `podcaster/orchestration.py`). Work must use the isolated worktree `SquadScope-Podcaster-wt-716`, validate with `python3 -m pytest -q` and `python3 -m ruff check podcaster tests`, commit, push, create a PR closing both issues, and watch CI.

## Scope and instructions

Selected repo instructions:
- `AGENTS.md`: Squad-managed repo, use standard test discipline.
- `.github/copilot-instructions.md`: Python code in `podcaster/`; run pytest and ruff before pushing; CI must remain correct.

Tracking convention: `.copilot-tracking/` artifacts are committed in this repo, so this RPI cycle records artifacts under `.copilot-tracking/`.

## Cycle 1

### Wider wave

- C1 `podcaster/sections.py`: CodeQL alerts #31 and #32 identify polynomial ReDoS at section parsing helpers. The likely sources are the tolerant section-header regex and generic speaker regex over user-provided script lines.
- C2 `podcaster/monitoring.py`: CodeQL alerts #27-#30 identify stack-trace exposure where exception text flows into API response bodies at `/api/review`, credential create/update, and podcast-config save.
- C3 `tests/test_sections.py` and `tests/test_monitoring.py`: Existing coverage exercises normal parsing and API contracts but lacks pathological regex timing and generic-body assertions for the alerted paths.

### Deeper wave

- C4 `podcaster/sections.py`: The section-header match can be implemented with deterministic string parsing: trim bounded line, validate one to six leading `#`, parse `section`, allow `:` or `-`, and return a stripped title. This removes ambiguous whitespace/`.+?` regex behavior while preserving accepted examples.
- C5 `podcaster/sections.py`: The generic `Speaker: text` fallback can be implemented with `partition(":")`, a label-length cap matching the prior regex, explicit first-character and allowed-character validation, and a non-empty stripped text check.
- C6 `podcaster/monitoring.py`: The alerted `ValueError` paths currently return `str(exc)` for some input/domain errors. Generic client-visible bodies plus `logger.warning(..., exc_info=True)` preserve server-side detail without exposing exception text.

### Contrarian wave

- C7 Some `ValueError` messages are currently user-friendly validation details. However, CodeQL marks these flows as stack-trace exposure sources and the user explicitly requested generic error bodies, so tests must update to the new contract rather than preserving detailed responses.
- C8 Bounding only the input length would address the ReDoS risk but would leave ambiguous regexes in place. Deterministic parsers are lower-risk and preserve functionality; no suppression is needed.

## Decision state

Selected approach:
1. Replace the two alerted regex uses in `sections.py` with linear deterministic parsing helpers.
2. Add pathological-input tests using long whitespace-heavy inputs with a conservative elapsed-time bound.
3. Return generic API error bodies for the four alerted monitoring paths and log details server-side with traceback.
4. Update monitoring tests to assert generic bodies and logged server-side detail.

## Planning readiness

Ready for planning and implementation. No blockers.

