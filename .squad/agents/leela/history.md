# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Producer Lead during the Podcaster squad rebuild (Futurama cast, continued from the SquadScope sister project). Prime directive: never change or block SquadScope article publishing. Milestones live in `docs/PRD.md`.
- 2026-06-07: Triaged all 9 P1 open issues. Routed: #1 (review gate)→Leela, #2 (privacy/RAI)→Hermes, #3 (blob storage)→Bender, #4 (TTS bakeoff)→Farnsworth, #5 (Spotify research)→Amy, #6 (publishing packet)→Amy, #7 (deploy)→Bender, #8 (API contract)→Leela, #9 (Actions chore)→Bender.
