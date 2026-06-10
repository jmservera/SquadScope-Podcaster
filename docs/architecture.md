# Architecture

## System boundaries

SquadScope Podcaster is a sister repository and service. SquadScope remains responsible for writing and publishing articles. Podcaster starts only after an article has already been published and receives the published URL or artifact metadata.

## Flow

1. SquadScope completes article publication.
2. SquadScope calls Podcaster `POST /api/generate` with an API key stored as a GitHub secret.
3. Podcaster validates the request and creates a job identifier.
4. Podcaster stages generated artifacts in Azure Blob Storage.
5. Podcaster returns URLs for manifest, transcript, show notes, publishing packet, and audio artifacts.
6. A human reviews the packet and manually publishes to Spotify or a podcast host.
7. SquadScope can link to the final public podcast URL later; it does not host or embed audio initially.

## Azure resources

- Resource group supplied by deployment workflow.
- Storage Account for Function host state and podcast artifact staging.
- Linux Function App running Python.
- System-assigned managed identity for blob storage writes (active when `PODCASTER_STORAGE_ACCOUNT_URL` is configured).
- Log Analytics workspace and Application Insights.

## API design

The API returns the stable response shape while running a synchronous production-path increment: deterministic job ID creation, artifact generation interfaces, manifest staging, review-pending metadata, publishing packet creation, and an audio placeholder. Local development uses filesystem-backed storage; Azure deployments use Blob Storage through managed identity.

Lifecycle, review-gate, publishing readiness, and observability metadata live in the staged manifest and publishing packet rather than new top-level response fields. This keeps SquadScope callers compatible while letting editors and operators inspect `schema_version`, lifecycle transitions, blocked review checks, packet eligibility, artifact hashes/content types, and safe `correlation_id` metadata.

## Security

- `x-podcaster-api-key` is required for all calls.
- The key is configured as an Azure app setting and synchronized to SquadScope only through GitHub secrets.
- API keys are never printed by workflows and never returned by the service.
- Future storage URLs should be short-lived SAS URLs or private URLs brokered by managed identity.

## Failure handling

Validation errors return HTTP 400 with structured error messages. Authentication failures return HTTP 401. Generation failures return HTTP 500 with a job response with `status` set to `failed` and populated `errors`.

## Production audio (ffmpeg) hosting

The deployed Linux Consumption Function App cannot run `ffmpeg`/`ffprobe`, so the audio stitch + validation gate cannot execute in-process; `/api/generate` currently returns a non-publishable placeholder while real synthesis runs on hosts that have `ffmpeg`. The decision for the production audio path (split: thin Functions front door + a queue-triggered Azure Container Apps Job that owns `ffmpeg`) is recorded in [ADR 0001](adr/0001-production-audio-ffmpeg-hosting.md). Provisioning is gated on operator approval of Azure spend (#67).

## Decision records

- [ADR 0001 — Production hosting for audio synthesis + ffmpeg](adr/0001-production-audio-ffmpeg-hosting.md)
