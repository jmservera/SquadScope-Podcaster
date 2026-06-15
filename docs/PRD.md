# Product Requirements: SquadScope Podcaster

## Summary

SquadScope Podcaster is a separate Azure-hosted service that turns a published SquadScope article into podcast production artifacts. It is intentionally separated from the main SquadScope publishing pipeline so podcast work can evolve independently and never block article publishing.

## Goals

- Accept a post-publish article URL or artifact reference from SquadScope.
- Create a podcast generation job and return stable artifact URLs.
- Produce a reviewed script, transcript, show notes, publishing packet, and audio files in later iterations.
- Stage artifacts in Azure Blob Storage with expiring access URLs or controlled access.
- Keep the initial Spotify or podcast-host publishing workflow manual.
- Provide a simple endpoint/key contract for SquadScope integration.

## Non-goals

- No change to the existing SquadScope article publishing process.
- No website audio hosting or embedded audio player in SquadScope for the initial release.
- No claim that Spotify supports direct podcast upload automation until researched.
- No generated audio files in source control.

## Users

- SquadScope maintainers triggering podcast generation after publication.
- Human editors reviewing episodes as Spotify drafts before promoting to public.
- Future distribution operators publishing to Spotify or a podcast host.

## Functional requirements

1. Provide `POST /api/generate` with API-key authentication.
2. Validate required fields: `week` and `article_url`.
3. Accept optional `article_sha256`, `source_artifacts`, `dry_run`, `force`, and `callback` fields.
4. Return `job_id`, `status`, artifact URLs, expiration time, warnings, and errors.
5. Stage artifacts in Azure Blob Storage when generation is implemented.
6. Preserve traceability from article URL and hash to podcast artifacts.
7. Auto-publish episodes as Spotify drafts after successful synthesis and audio validation; humans review directly on the Spotify platform.
8. Record a cost ledger for every episode and block non-dry-run synthesis or packet readiness when monthly guardrails are unknown or exceeded.

## Quality requirements

- Secrets are stored in GitHub Actions secrets or Azure app settings, never in code.
- Logs must not include API keys.
- API responses must have deterministic shape for caller automation.
- CI must run validation tests.
- Podcast audio must pass technical validation before any publishing packet is marked ready: MP3, mono, 44.1 kHz, 64-96 kbps, near -16 LUFS, under 10 minutes unless a manual override is recorded, and under 10 MB unless explicitly documented.
- MVP podcast operations are capped at 5 episodes/month and $5/month total podcast spend unless an explicit operator override is recorded. The ledger must include script generation, validation, TTS, staging storage, egress/download, and platform/provider cost categories, even when current estimated cost is zero.

## Milestones

1. Contract scaffold: API validates input and returns stub accepted/completed responses.
2. Blob staging: write manifest and packet placeholders to Azure Blob Storage.
3. TTS bakeoff: compare providers, cost, quality, rights, and operational fit.
4. Draft publishing: after successful synthesis and audio validation, episodes are automatically published as Spotify drafts. Humans review directly on the Spotify platform before promoting to public.
5. Publishing packet: package all content needed for distribution, blocked only until audio validation passes.
6. Distribution research: evaluate Spotify and podcast-host automation options.
