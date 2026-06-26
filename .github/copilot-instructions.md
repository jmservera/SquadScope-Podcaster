# Copilot Instructions for SquadScope-Podcaster

This repository uses the **Squad agent** as the default for all AI-assisted work.

## Default Agent
Always use `--agent squad` when running Copilot CLI on this repository.

## Repository Context
- **Project:** SquadScope-Podcaster — Config-driven podcast generation engine
- **Architecture:** See `architecture.md` in repo root
- **Squad team:** See `.squad/team.md` for current roster

## Key Conventions
- All application code is in `podcaster/` (Python >=3.11)
- Infrastructure is Bicep in `infra/`
- Config is received via API payload from the caller (never hardcoded in this repo)
- Host personalities, show intro, episode style — all from config, not code
- audio.py uses eval=frame on ALL ffmpeg volume filters with time expressions
- Music never exceeds 10% volume when voice is playing
- Tests: pytest tests/ must pass before merge
- CI must be correct, not just green

## API Configuration
This repo receives configuration via the API payload. The config schema (PodcastConfig, ScriptDirections, MusicMixConfig) is defined in `config.py`.
- Changes to these dataclasses must stay compatible with the caller's config payload
- If new config fields are needed, coordinate with the calling platform's config

## Critical Technical Notes
- ffmpeg volume filter: ALWAYS use eval=frame with time-based expressions
- amix: use 2-input chain, not N-input (avoids amplitude dilution)
- Azure OpenAI auth: managed identity via az login --identity
- Upload type for Spotify: "default" (not "audio") for mp3 files
- ETag from GCS must be stripped of surrounding quotes

## Testing
- Run `pytest tests/ -q` (297+ tests)
- CI: `.github/workflows/ci.yml`
- Smoke coverage is the caller's responsibility

## DevSecOps Guardrails

Part of the DevSecOps Guardrails epic (jmservera/SquadScope-Coordinator#33). Rollout is
**phased**: Phase A = warning-only baselines (current), Phase B = fix findings,
Phase C = blocking gates + local hooks. See `docs/linting.md` for full details.
Hermes (Safety & Security) owns the DevSecOps surface; route hook/CI-security/
dependency-scan/secret-detection work and infra/Dockerfile/workflow security
review through Hermes.

**Before pushing, run locally:**
- `pytest tests/ -q` — tests must pass.
- `docker build -f Containerfile -t podcaster-synthesis:ci .` (and `Containerfile.api`,
  `ui/Dockerfile`) when you touch container definitions or their dependencies.
- `ruff check podcaster tests` and `ruff format --check podcaster tests` for Python
  lint/format. Auto-fix with `ruff check --fix` and `ruff format`.
- `checkov --directory . --framework dockerfile --skip-path .worktrees --skip-path ui/node_modules --baseline .checkov.baseline --soft-fail --compact`
  when you change `Containerfile*`/`ui/Dockerfile`; `checkov --directory infra --framework bicep` for infra.
- `zizmor .github/workflows/` (exclude generated `squad-*`/`sync-squad-labels` files) when you change anything under `.github/workflows/`.

**Installing the tools locally:**
```bash
pip install ruff==0.15.7 checkov==3.2.533 zizmor==1.25.2
```

**Local hooks (Phase C):** pre-commit/pre-push hooks will run ruff (and the security
scanners on relevant paths). Until then, run the commands above manually.

**Emergencies (skip checks):** prefer fixing the finding. If a hotfix genuinely must
bypass local hooks, use `git commit --no-verify` / `git push --no-verify` and call it
out in the PR so Hermes can follow up. CI scanners are non-blocking in Phase A, so a
push is never silently gated — but never weaken or delete a check to make CI green
(**CI must be correct, not just green**).

