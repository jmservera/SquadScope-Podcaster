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

### Phase B progress (#521) — 2026-06-26

First incremental pass: applied `ruff check --fix` (safe fixes only) and
hand-fixed the real-defect categories. Cleared `I001`, `F401`, `F541`, `F811`,
`F841`, and `F821` entirely:

- **F821** — `podcaster/video/video_gen.py` referenced an unimported
  `Playwright` type in a `_launch` annotation; added it to the
  `playwright.sync_api` import.
- **F841** — removed dead assignments (e.g. unused `hf_s` in `zoom.py` and
  leftover constructions/locals in tests), keeping any side-effecting calls.

Remaining baseline *after this first pass* (point-in-time snapshot; deferred to
follow-up Phase B passes, by directory — see "Remaining baseline (current)"
below for the live count):

| Count | Rule  | Description |
| ----: | ----- | ----------- |
| 533   | E501  | line-too-long |
| 28    | E402  | module-import-not-at-top-of-file |
| **561** | **total** | |

`E501` (line length) and `E402` (import placement) are larger, higher-touch
changes and are intentionally left for subsequent incremental PRs so each stays
small and reviewable. The full test suite (2049 passed) is green after this pass.

### Phase B progress (#521) — E501 follow-up (jobs subsystem)

Incremental `E501` pass over the job/script subsystem. Cleared
`podcaster/script_gen.py` (29), `podcaster/jobs.py` (22), and
`podcaster/job_runner.py` (10) — **61** `line-too-long` violations total.

As with the other E501 slices, every fix is pure re-wrapping (string-concat
splits with boundary whitespace preserved, Black-style call/comprehension
wrapping). The parsed AST of all three modules is byte-for-byte identical to
before, so there is no string-content or logic change. No `# noqa: E501` was
needed. `ruff check` reports no `E501` on these files; the full suite
(2049 passed, 1 skipped) stays green.

### Phase B progress (#521) — E402 follow-up

Cleared `E402` (module-import-not-at-top-of-file) entirely. These were imports
placed after early `logging.getLogger(__name__)` module-level statements or a
mid-file import block:

- **`podcaster/episode.py`** / **`podcaster/monitoring.py`** — the
  `log`/`logger = logging.getLogger(__name__)` assignment sat between the stdlib
  imports and the first-party `podcaster.*` imports, pushing every following
  import past a non-import statement. Moved the logger assignment below the
  import block.
- **`tests/test_video_sync_plan.py`** — a second `from podcaster.video.sync_plan
  import (...)` block lived mid-file (after the test classes started); merged its
  names into the single top-of-file import block.

### Phase B progress (#521) — E501 follow-up (core slice 1)

Started clearing `E501` (line-too-long) by module, beginning with the
`podcaster/` config/validation core. Code lines were wrapped to the 100-column
limit; intentional string literals that must not be reflowed (injection-detection
regexes in `sanitization.py`, the visual-intent prompt text in `script_plan.py`)
carry a justified `# noqa: E501` instead.

Files cleared in this slice: `podcaster/config.py`, `podcaster/costs.py`,
`podcaster/validation.py`, `podcaster/sanitization.py`, `podcaster/script_plan.py`.

### Phase B progress (#521) — E501 follow-up (generation core)

Incremental `E501` pass over the episode-generation core. Cleared
`podcaster/generation.py` (47) and `podcaster/episode.py` (18), **65**
`line-too-long` violations in total.

All fixes are pure re-wrapping: over-length spoken-script f-strings were split
into Python implicit string concatenation with the boundary whitespace preserved
exactly, and long call/comprehension lines were wrapped Black-style. The parsed
AST of both modules is byte-for-byte identical to before the pass, so there is
no string-content or logic change. No `# noqa: E501` was needed.

### Remaining baseline (current)

After the passes above (imports/dead-code, jobs `E501`, `E402`, the config-core
`E501` slice, and the generation-core `E501` slice), the live
`ruff check podcaster tests --statistics` count is:

| Count | Rule  | Description |
| ----: | ----- | ----------- |
| 368   | E501  | line-too-long |
| **368** | **total** | |

`E501` (line length) is the last remaining category, being cleared in further
incremental by-module slices. The full test suite stays green after this pass.

## Checkov (IaC / container security) — #519

Checkov scans infrastructure-as-code and container definitions. It **already**
gated `infra/` (Bicep) in `.github/workflows/reusable-ci.yml`; Phase A extends it
to the container images (`Containerfile`, `Containerfile.api`, `ui/Dockerfile`)
in **non-blocking** mode.

### Baseline file

`.checkov.baseline` (repo root) records the existing Dockerfile findings so CI
only surfaces *new* issues. Regenerate it after intentionally accepting a finding:

```bash
checkov --directory . --framework dockerfile \
  --skip-path .worktrees --skip-path ui/node_modules \
  --create-baseline --soft-fail --compact
```

### Local usage

```bash
# One-time install (already pinned in CI):
pip install checkov==3.2.533

# Scan the Dockerfiles against the recorded baseline (non-blocking):
checkov --directory . --framework dockerfile \
  --skip-path .worktrees --skip-path ui/node_modules \
  --baseline .checkov.baseline --soft-fail --compact

# Scan the Bicep infra (matches the existing gating step):
checkov --directory infra --framework bicep --compact

# Full report WITHOUT the baseline (to see everything):
checkov --directory . --framework dockerfile \
  --skip-path .worktrees --skip-path ui/node_modules --compact
```

### CI

The `infrastructure` job runs a Checkov Dockerfile step with `--soft-fail` and
`--baseline .checkov.baseline`. In Phase A this lane is **non-blocking**.

### Baseline report (2026-06-26)

Dockerfile framework (`Containerfile`, `Containerfile.api`, `ui/Dockerfile`):

| Severity | Count | Findings |
| -------- | ----: | -------- |
| CRITICAL | 0 | — |
| HIGH | 0 | — |
| Other | 1 | `CKV_DOCKER_2` — missing `HEALTHCHECK` in `ui/Dockerfile` |

Passed: 94, Failed: 1, Skipped: 0. No CRITICAL/HIGH findings.

> `docker-compose.test.yml` is a local-only integration-test stack. Checkov 3.2.x
> has no dedicated `docker_compose` framework, so it is not separately gated;
> container hardening is covered via the Dockerfile framework above and Trivy
> config scans already in CI.

## Zizmor (GitHub Actions security) — #520

[zizmor](https://docs.zizmor.sh) audits GitHub Actions workflows for
supply-chain risks: template injection, dangerous triggers, unpinned actions,
and overly-permissive `permissions:`. Phase A runs it in **non-blocking** mode.

### CI

`.github/workflows/zizmor.yml` runs `zizmorcore/zizmor-action` (pinned to commit
`5f14fd08f7cf1cb1609c1e344975f152c7ee938d`, v0.5.6) on changes under
`.github/workflows/`. The job is `continue-on-error: true` and uploads SARIF to
GitHub Code Scanning. Generated Squad workflows (`squad-*`, `sync-squad-labels`)
are excluded since they are produced upstream. Mirrors the SquadScope setup.

### Local usage

```bash
# One-time install:
pip install zizmor==1.25.2   # or: brew install zizmor / cargo install zizmor

# Scan the repository-owned workflows (excluding generated Squad files):
files=$(find .github/workflows -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) \
  ! -name 'squad-*.yml' ! -name 'squad-*.yaml' ! -name 'sync-squad-labels.yml' | sort)
zizmor $files

# Machine-readable output:
zizmor --format json $files
```

### Baseline report (2026-06-26)

`zizmor` over 11 repository-owned workflows — **17** findings (32 suppressed):

| Count | Audit | Notes |
| ----: | ----- | ----- |
| 8 | `secrets-inherit` | reusable workflows called with `secrets: inherit` |
| 4 | `template-injection` | `${{ inputs.* }}` expanded into `run:` blocks |
| 3 | `unpinned-uses` | actions not pinned to a commit SHA |
| 1 | `artipacked` | checkout credential persistence |
| 1 | `excessive-permissions` | broader `permissions:` than required |

By severity: **7 High, 9 Medium, 1 Informational**.

> Fixes are deferred to Phase B (#521); blocking enforcement to Phase C.

### Phase B progress (#521) — zizmor HIGH cleared

All **7 High** findings have been fixed (plus the Medium `artipacked` finding as a
low-risk bonus); the new severity breakdown is **0 High, 8 Medium, 1 Informational**.
What was fixed:

- **template-injection — 3 of 4 (the High-severity subset) → 0** in 2 workflows —
  moved interpolated
  `${{ inputs.* }}` expressions into `env:` variables and referenced them as
  quoted shell vars in `run:` blocks:
  - `release.yml` — `inputs.tag` → `INPUT_TAG`.
  - `reusable-publish-image.yml` — `inputs.dockerfile` → `DOCKERFILE` and
    `inputs.build_context` → `BUILD_CONTEXT`.
- **unpinned-uses (3 → 0)** — pinned 3 official actions in
  `integration-tests.yml` to full commit SHAs (with `# vX.Y.Z` comments):
  `actions/checkout@v4` → `34e1148…` (v4.3.1),
  `actions/setup-python@v5` → `a26af69…` (v5.6.0),
  `actions/setup-node@v4` → `49933ea…` (v4.4.0).
- **excessive-permissions (1 → 0)** in `release.yml` — dropped the workflow-level
  `id-token: write`; least-privilege `contents: read` stays at the top level and
  `id-token: write` remains scoped to the individual jobs that need OIDC.
- **artipacked (1 → 0)** in `integration-tests.yml` — set
  `persist-credentials: false` on the checkout step (the job only runs tests and
  never pushes, so the persisted token was unnecessary).

> Out of scope for this HIGH slice: the **Medium** `secrets-inherit` (8) findings
> remain deferred in the non-blocking baseline, along with the **1 Informational**
> `template-injection` finding (the lower-severity remainder of the 4 total).

