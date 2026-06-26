# Linting & DevSecOps guardrails

Part of the **DevSecOps Guardrails epic** (parent: jmservera/SquadScope-Coordinator#33).

The rollout is phased:

- **Phase A — baseline + non-blocking** (current): tools run in CI as
  warnings/annotations only. They never fail the build. Existing violations are
  recorded as a baseline.
- **Phase B** — fix the baseline violations.
- **Phase C** — flip the gates to blocking (and add local pre-commit/pre-push hooks).

## Ruff (Python lint + format) — #518

Ruff is the Python linter and formatter. Configuration lives in `pyproject.toml`
under `[tool.ruff]`. Phase A uses a conservative rule subset (`E`, `F`, `I`) with
`line-length = 100`.

### Local usage

```bash
# One-time install — Ruff ships in the `dev` extra (also installed in CI):
pip install -e ".[dev]"   # or: pip install ruff==0.15.7

# Lint (report only):
ruff check podcaster tests

# Lint with a count grouped by rule (the "baseline report"):
ruff check podcaster tests --statistics

# Auto-fix the safe subset (Phase B work, run deliberately):
ruff check podcaster tests --fix

# Formatting:
ruff format --check podcaster tests   # report files that would change
ruff format podcaster tests           # apply formatting
```

`ui/` (TypeScript) and `.worktrees/` are excluded from Ruff.

### CI

`.github/workflows/reusable-ci.yml` has a `lint` job that runs Ruff with
`continue-on-error: true` and emits GitHub annotations (`--output-format github`).
In Phase A this lane is **non-blocking** — it surfaces findings without failing CI.

### Baseline report (2026-06-26)

Captured with `ruff check podcaster tests --statistics`:

| Count | Rule  | Description |
| ----: | ----- | ----------- |
| 536   | E501  | line-too-long |
| 51    | I001  | unsorted-imports |
| 45    | F401  | unused-import |
| 29    | E402  | module-import-not-at-top-of-file |
| 7     | F541  | f-string-missing-placeholders |
| 6     | F841  | unused-variable |
| 4     | F811  | redefined-while-unused |
| 1     | F821  | undefined-name |
| **679** | **total** | |

`ruff format --check`: **137** files would be reformatted, 26 already formatted.

> Note: the single `F821` (undefined-name) finding may indicate a real defect —
> flagged for the Phase B fix work (#521), not addressed here.
