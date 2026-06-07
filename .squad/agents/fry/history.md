# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as QA/Tester during the Podcaster squad rebuild. Tests live in `tests/`; run with `pytest`. Key paths to keep covered: 401 unauthorized, 400 validation/malformed JSON, 202 accepted stub. The deterministic response shape is a contract under test — guard it against drift.
