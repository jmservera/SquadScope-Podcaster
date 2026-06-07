# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Distribution UX during the Podcaster squad rebuild. SquadScope integration is link-only (no hosted/embedded audio initially). Distribution automation (Spotify/podcast-host) is research-stage. Owns publishing-packet usability. See `backlog/manual-publishing-packet.md` and `backlog/spotify-publishing-research.md`.
