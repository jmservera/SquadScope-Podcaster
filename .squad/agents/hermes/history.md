# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Safety & Security during the Podcaster squad rebuild. Core rules: `PODCASTER_API_KEY` stored as GitHub/Azure secret, never logged; the API never echoes received keys; deploy uses GitHub OIDC; future storage access should prefer managed identity + short-lived SAS. See README "Secret handling" and `docs/architecture.md` "Security".
