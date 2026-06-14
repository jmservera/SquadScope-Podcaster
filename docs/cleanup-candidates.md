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

| Path | Status | Action taken |
| --- | --- | --- |
| `W21-article.md` … `W24-article.md` | ✅ Already removed | Previously deleted from repo root. |
| `podcast-config.json` | ✅ Already removed | Previously deleted from repo root. |
| `scripts/review_audio.py` | ✅ Already removed | Previously deleted. |
| `scripts/review_podcast.py` | ✅ Already removed | Previously deleted. |
| `scripts/run_agent.sh` | ✅ Deleted | Launched missing `podcaster_agent.py`; stale/broken. Removed in this pass. |
| `docs/adr/0001-production-audio-ffmpeg-hosting.md` | ✅ Bannered | Status changed to **Superseded** (ACA-only since PR #112). |
| `docs/ops/0001-bakeoff-resource-decommission.md` | ✅ Bannered | Added historical-record banner. |
| `docs/ops/0002-rg-cleanup-pre-aca.md` | ✅ Bannered | Added historical-record banner. |
| `docs/security/0001-aca-synthesis-security-review.md` | ✅ Updated | Replaced `function_app.py` reference with `podcaster/api.py` + `infra/modules/api.bicep`. |

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
