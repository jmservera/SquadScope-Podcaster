# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Re-chartered as Scribe during the Podcaster squad rebuild. Append-only state files (`decisions.md`, `agents/*/history.md`, `log/**`, `orchestration-log/**`) use `merge=union` per `.gitattributes`. Decisions ledger is at `.squad/decisions.md`; the drop-box is `.squad/decisions/inbox/`.
