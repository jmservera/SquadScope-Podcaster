# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Platform/Backend during the Podcaster squad rebuild. Entry point is `function_app.py` (`/api/generate`), validation in `podcaster/validation.py`, infra in `infra/main.bicep`, deploy via `.github/workflows/deploy-azure.yml` using GitHub OIDC. Auth header is `x-podcaster-api-key`; never log it.
- 📌 Team update (2026-06-07): GitHub issue connect + triage. Assigned issues: #3, #7, #9 (3 total)
