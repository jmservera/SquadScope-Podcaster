# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Re-chartered as Work Monitor during the Podcaster squad rebuild. Backlog of research/work items lives in `backlog/` (blob-staging, human-review-gate, manual-publishing-packet, monetization, spotify-publishing-research, tts-bakeoff). GitHub label automation depends on the `## Members` header in `team.md`.
- 2026-09-23 reskill: Production is ACA-only in eastus2; the old Function App and split-region directives are historical.
- Current gate: operator-only architecture PR #682 is merge-clean, all checks pass, and no active review threads remain.
- Draft PR #684 is also green and thread-clean, but must be restacked and revalidated after #682 merges.
- Next dependency layer is #678/#679; #671 remains blocked on an authoritative Spotify video publication contract.
- 2026-09-24 reskill: #679 merged via #704/#706. #703 (YouTube reconcile) has fix PR #705 stacked on Bender's branch; it needs Bender to merge #705, then coordinator sign-off (changes live upload metadata before the W40 check).
- #682 is operator-only and now CONFLICTING with main; #684 stays draft one layer behind it; #681 is gated on both.
- #692 (Spotify creds) and #671 (Spotify video contract) need a human. Production boundary: no deploys/env/loop changes until W40 (2026-09-30) is verified.
