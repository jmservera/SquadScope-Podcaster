# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Platform/Backend during the Podcaster squad rebuild. Entry point is `function_app.py` (`/api/generate`), validation in `podcaster/validation.py`, infra in `infra/main.bicep`, deploy via `.github/workflows/deploy-azure.yml` using GitHub OIDC. Auth header is `x-podcaster-api-key`; never log it.
- 📌 Team update (2026-06-07): GitHub issue connect + triage. Assigned issues: #3, #7, #9 (3 total)

- 📌 Team update (2026-06-07T18:26:33.954+00:00): Security handoff review completed; API contract verified; secret handling confirmed; auto-sync requires SQUADSCOPE_SYNC_TOKEN — decided by Hermes
- 2026-06-07T19:07:49.816+00:00: Implemented first production-path pipeline locally: deterministic job lifecycle now stages script, transcript, show notes, audio placeholder, publishing packet, and manifest artifacts. Local dev falls back to filesystem storage; Azure path uses managed identity blob writes through storage account URL/container settings.
- 2026-06-07T19:19:52.661+00:00: Wave 2 local runtime pass kept `/api/generate` response keys stable while moving lifecycle/review/publishing/observability expansion into manifest and packet metadata. Deterministic local generation now avoids wall-clock ZIP metadata and records artifact content types/hashes; callback secret names are reduced to boolean metadata and not persisted.
- 2026-06-07T19:31:49.311+00:00: Wave 3 polish confirmed `expires_at` parity is complete across API response, staged manifest blob, and publishing packet MANIFEST.json. Removed sprint-internal "Wave 2 stub" language from operator-facing packet content; updated `docs/architecture.md` failure-handling note to reflect that sync `failed_response` is already wired (not future async work).
- 2026-06-07T19:49:59.902+00:00: Prepared Wave 1/2/3 PR branch from local commits 113b6c6 and 78813be. No duplicate open PR existed; local checks passed in a project virtualenv after installing existing requirements. Azure deployment was intentionally not run and remains blocked on subscription/access.
