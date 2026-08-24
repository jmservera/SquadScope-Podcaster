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

## Azure resources (ACA-only architecture, #109/#112)

All resources deploy to **eastus2** (required for `gpt-4o-mini-tts` model availability).

| Resource | Purpose |
|----------|---------|
| Storage Account | Artifact staging (`podcaster-artifacts` container) + synthesis queue |
| Azure OpenAI (Cognitive Services) | TTS (`gpt-4o-mini-tts`, deployment `tts`) + chat (`gpt-4o-mini`, deployment `chat`) |
| Azure Container Registry (Basic) | Hosts the synthesis + API container images (#129) |
| Container Apps Environment | Hosts the API app and synthesis job |
| Container Apps App (HTTP ingress) | Thin `/api/generate` front door: validates, stages, enqueues (#131) |
| Container Apps Job (queue-triggered) | Runs the full episode pipeline: script → TTS → ffmpeg stitch → validate → stage |
| User-assigned Managed Identity | Identity-only auth to Storage (Blob + Queue), Azure OpenAI, and ACR (AcrPull) |
| Log Analytics + Application Insights | Observability |

The architecture has two ACA workloads sharing one Container Apps Environment:
- **API App** — lightweight HTTP ingress (scale-to-zero, no ffmpeg) that accepts requests and enqueues synthesis messages.
- **Synthesis Job** — queue-triggered, ffmpeg-backed job that produces the full episode audio.

Both pull images from the private ACR using the managed identity (AcrPull role).

### Region rationale

`eastus2` was chosen because it is the only region where `gpt-4o-mini-tts` (GlobalStandard) is available AND Container Apps are supported. All resources are co-located to minimize latency.

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

## Production audio pipeline

The ACA synthesis job runs the full episode pipeline in a container with `ffmpeg` baked in:

1. A queue message (containing only `job_id`) triggers the job.
2. The job reads the staged manifest/script from Blob Storage.
3. Azure OpenAI TTS synthesizes each script segment (two-voice: fable/alloy).
4. `ffmpeg` stitches segments + the bundled Claracle Theme intro/outro music bed into one MP3.
5. `ffprobe` validates loudness, duration, and format.
6. The validated MP3 + updated manifest are staged back to Blob Storage.
7. Publication remains **human-gated** — the job never marks an episode eligible for public release.

The decision for this architecture is recorded in [ADR 0001](adr/0001-production-audio-ffmpeg-hosting.md).

## Decision records

- [ADR 0001 — Production hosting for audio synthesis + ffmpeg](adr/0001-production-audio-ffmpeg-hosting.md)
