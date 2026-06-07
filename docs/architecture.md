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
- User-assigned or system-assigned managed identity for future storage access.
- Log Analytics workspace and Application Insights.

## API design

The initial API returns a deterministic stub response with the full response shape. This lets SquadScope integration tests and workflow wiring proceed before TTS and storage implementation are complete.

## Security

- `x-podcaster-api-key` is required for all calls.
- The key is configured as an Azure app setting and synchronized to SquadScope only through GitHub secrets.
- API keys are never printed by workflows and never returned by the service.
- Future storage URLs should be short-lived SAS URLs or private URLs brokered by managed identity.

## Failure handling

Validation errors return HTTP 400 with structured error messages. Authentication failures return HTTP 401. Generation failures should return a job response with `status` set to `failed` and populated `errors` once asynchronous processing is implemented.
