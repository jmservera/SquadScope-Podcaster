# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Producer Lead during the Podcaster squad rebuild (Futurama cast, continued from the SquadScope sister project). Prime directive: never change or block SquadScope article publishing. Milestones live in `docs/PRD.md`.
- 2026-06-07: Triaged all 9 P1 open issues. Routed: #1 (review gate)→Leela, #2 (privacy/RAI)→Hermes, #3 (blob storage)→Bender, #4 (TTS bakeoff)→Farnsworth, #5 (Spotify research)→Amy, #6 (publishing packet)→Amy, #7 (deploy)→Bender, #8 (API contract)→Leela, #9 (Actions chore)→Bender.
- 2026-06-07: Wave 1/2 local readiness review found the diff cohesive in direction but not releasable: pytest currently fails on publishing packet MANIFEST.json missing nested review metadata expected by tests; Azure subscription work remains gated until Bender fixes packet manifest parity and Fry re-runs the suite.
- 2026-06-07: Final Wave 2 readiness gate passed locally: `.venv/bin/python -m pytest -q` reported 19 passed, compileall succeeded, and diff hygiene passed; approved to proceed to Azure subscription setup without deploying or committing from this review.
- 2026-06-07: Wave 3 release gate APPROVED and committed. Full Wave 1/2/3 local increment committed: production pipeline (jobs/generation/storage), infra bicep, docs, tests (19/19), squad files. No scope creep detected; RESPONSE_KEYS and all backward-compat identifiers unchanged; no secrets in responses. Remaining gate before Azure deploy: subscription setup + GitHub secrets; remaining gate before live TTS: Farnsworth bakeoff.
- 2026-06-07: Audit and planning: 9 open issues reviewed; 6 progressed locally (issues #1, #2, #3, #6, #8, #7 code-ready). 2 blockers remain (issues #4 TTS bakeoff, #5 Spotify research — both require specialist investigation). Local work is cohesive and ready for single PR `wave-1-2-3-contract-pipeline-docs` closing/progressing issues #8, #3, #2, #1, #6, #7. Azure subscription remains the only blocker for deployment; all code, tests, docs, and design work is complete and validated. Decision document written to `.squad/decisions/inbox/leela-issue-first-plan.md`.
📌 Team update (2026-06-07T19:49:59Z): Issue-first/PR workflow rule activated. PR #10 (wave-1-2-3-contract-pipeline-docs) closes #3, #8; progresses #1, #2, #6, #7. Inbox decisions merged (15 files). Post-merge tasks: #4 (TTS), #5 (Spotify) parallel, then #9 (CI) and #7 (deploy).
- 2026-06-07T20:52:01.950+00:00: Reviewer revision for PR #11/#7 caps deployment Function App names at 35 characters in workflow/Bicep so derived App Service Plan (`-plan`) and Log Analytics (`-law`) names remain Azure-compliant; optional app/storage overrides remain supported but unsafe values fail before deploy.

📌 Team update (2026-06-07T20:52:01Z): Non-Bender PR #11 revision approved; naming constraints prevent Azure silent failures. CI green; ready for live prod/OIDC deploy and endpoint smoke test. — consolidated by Scribe from Bender, Hermes, Fry
