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

Remaining baseline (deferred to follow-up Phase B passes, by directory):

| Count | Rule  | Description |
| ----: | ----- | ----------- |
| 533   | E501  | line-too-long |
| 28    | E402  | module-import-not-at-top-of-file |
| **561** | **total** | |

`E501` (line length) and `E402` (import placement) are larger, higher-touch
changes and are intentionally left for subsequent incremental PRs so each stays
small and reviewable. The full test suite (2049 passed) is green after this pass.

### Phase B progress (#521) — E501 follow-up (generation core)

Incremental `E501` pass over the episode-generation core. Cleared
`podcaster/generation.py` (47) and `podcaster/episode.py` (18 — the E501 subset
only; its pre-existing `E402` import-placement findings are handled separately),
**65** `line-too-long` violations in total.

All fixes are pure re-wrapping: over-length spoken-script f-strings were split
into Python implicit string concatenation with the boundary whitespace preserved
exactly, and long call/comprehension lines were wrapped Black-style. The parsed
AST of both modules is byte-for-byte identical to before the pass, so there is
no string-content or logic change. No `# noqa: E501` was needed.

`ruff check` on both files reports no `E501`; the full suite (2049 passed,
1 skipped) stays green. Remaining `E501` lives in other modules and tests,
deferred to subsequent slices.

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

