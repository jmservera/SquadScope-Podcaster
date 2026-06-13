# Cleanup Candidates

## Scope checked
- `scripts/` references from docs, workflows, tests, and repo code
- root `*.py` files
- root `*.md` / `*.json` scratch-looking files
- `docs/` references to removed Function App / Azure Functions architecture

## Quick audit notes
- No stray root-level `*.py` files were found.
- `scripts/get-podcaster-values.sh`, `scripts/smoke_generate.py`, and `scripts/record_review_approval.py` are referenced by docs/workflows/tests and do not look like cleanup candidates.

## Candidates

| Path | Why it appears unused or outdated | Recommendation |
| --- | --- | --- |
| `W21-article.md`, `W22-article.md`, `W23-article.md`, `W24-article.md` | Root-level weekly article drafts look like batch-generation scratch inputs, not durable repo assets. Repo-wide references only point to `scripts/generate_all_episodes.py` and the hardcoded `scripts/run_full_pipeline.py`; there are no workflow, test, or docs references. | Archive outside the repo or move under a dedicated `examples/` or `testdata/` location if they still matter for manual generation. Keeping them in repo root makes them look like accidental leftovers. |
| `podcast-config.json` | Root-level temp config copy conflicts with the repo convention that config comes from SquadScope, not this repo. It is only referenced by `scripts/generate_all_episodes.py` and `scripts/run_full_pipeline.py`. | Archive/delete if no longer needed, or move under `examples/` / `scripts/` as an explicit local-only sample. |
| `scripts/review_audio.py` | No repo references were found. The script is hardcoded to a specific job path (`podcast-2026-W24-e2e-test`), a specific endpoint, and tells the operator to run `run_full_pipeline.py` first. | Delete or move out of the repo if it was one-off operator scratch work. If it is still useful, turn it into a parameterized tool and document it. |
| `scripts/review_podcast.py` | No repo references were found. The script is hardcoded to `.podcaster-artifacts/jobs/podcast-2026-W24-e2e/audio/episode.mp3` and a fixed `gpt-audio-1.5` review flow. | Delete/archive as scratch work, or keep only after parameterizing and documenting it. |
| `scripts/run_agent.sh` | No repo references were found, and it launches `scripts/podcaster_agent.py`, which is not present in the repository. That makes the script look stale and currently broken. | Delete if obsolete. If it still has a future role, restore the missing target script and document the flow before keeping it. |
| `docs/adr/0001-production-audio-ffmpeg-hosting.md` | This ADR still spends most of its content on the old Function App / split Functions+ACA design, even though the tail note says PR #112 removed the Function App entirely. As current architecture guidance, it is easy to misread. | Keep as history, but add a clear **superseded/historical** banner at the top or archive it under a historical section. Consider adding a short ACA-only summary near the top. |
| `docs/ops/0001-bakeoff-resource-decommission.md` | This ops record still describes migrating Function App settings and Function App managed identity steps for the old bakeoff OpenAI flow. Those instructions are stale for the ACA-only stack. | Mark as historical/archive material, or update it so readers immediately understand it describes pre-ACA cleanup history. |
| `docs/ops/0002-rg-cleanup-pre-aca.md` | This file is explicitly “pre-ACA” and still contains extensive Function App inventory, redeploy, and validation language. Useful as a record, but outdated as active operations documentation. | Archive or banner it as historical so operators do not confuse it with the current ACA deployment shape. |
| `docs/security/0001-aca-synthesis-security-review.md` | The review scope still includes `function_app.py`, and the role table still calls out a “Function App system identity” even though the repo now uses an ACA API app. The file is close to current, but not fully updated. | Update in place to refer to the ACA API app identity and current ingress component names. |

## Evidence summary

### Script/reference scan
- `scripts/get-podcaster-values.sh` is referenced by `docs/AZURE-DEPLOYMENT.md` and covered by `tests/test_get_podcaster_values.py`.
- `scripts/smoke_generate.py` is referenced by `README.md` and `docs/integration-contract.md`.
- `scripts/record_review_approval.py` is invoked by `.github/workflows/podcast-review-gate.yml` and covered by `tests/test_review_gate.py`.
- No repo references were found for `scripts/review_audio.py`, `scripts/review_podcast.py`, or `scripts/run_agent.sh`.

### Root scratch-file scan
- `scripts/run_full_pipeline.py` hardcodes `W24-article.md` and `podcast-config.json`.
- `scripts/generate_all_episodes.py` hardcodes `W21`–`W24` article filenames and `podcast-config.json`.

### Removed-feature scan
- Function App / Azure Functions references remain in:
  - `docs/adr/0001-production-audio-ffmpeg-hosting.md`
  - `docs/ops/0001-bakeoff-resource-decommission.md`
  - `docs/ops/0002-rg-cleanup-pre-aca.md`
  - `docs/security/0001-aca-synthesis-security-review.md`
