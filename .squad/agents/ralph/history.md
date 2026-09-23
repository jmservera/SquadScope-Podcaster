# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Re-chartered as Work Monitor during the Podcaster squad rebuild. Backlog of research/work items lives in `backlog/` (blob-staging, human-review-gate, manual-publishing-packet, monetization, spotify-publishing-research, tts-bakeoff). GitHub label automation depends on the `## Members` header in `team.md`.

📌 Team update (2026-09-23T12:01:49Z): Fry accepted PR #684 at final SHA `6096d37`; Basher's stale `905a890` acceptance is superseded. Residual gates remain P05 deployment/W39 and P06 elapsed-cycle — decided by Fry.
