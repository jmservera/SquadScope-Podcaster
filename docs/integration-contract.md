# SquadScope to Podcaster Integration Contract

## Endpoint

- Production URL: `https://<function-app-name>.azurewebsites.net/api/generate`
- Local URL: `http://localhost:7071/api/generate`
- Method: `POST`
- Content type: `application/json`

## Authentication

Send the API key in this header:

```text
x-podcaster-api-key: <PODCASTER_API_KEY>
```

`PODCASTER_API_KEY` must be stored as a GitHub Actions secret in `jmservera/SquadScope`. Do not log, echo, or include it in workflow summaries.

## Request body

```json
{
  "week": "2026-W23",
  "article_url": "https://squadscope.example/articles/2026-w23",
  "article_sha256": "optional-lowercase-hex-sha256",
  "source_artifacts": [
    "https://example.blob.core.windows.net/artifacts/source.json"
  ],
  "dry_run": false,
  "force": false,
  "callback": {
    "url": "https://example.com/podcaster-callback",
    "secret_name": "PODCASTER_CALLBACK_SECRET"
  }
}
```

### Fields

- `week` (required string): Issue or ISO week identifier.
- `article_url` (required string): Published article URL from SquadScope.
- `article_sha256` (optional string): SHA-256 digest of article artifact/content.
- `source_artifacts` (optional array of strings): Supporting artifact URLs.
- `dry_run` (optional boolean): Validate and generate draft/stub artifacts only.
- `force` (optional boolean): Regenerate even if prior artifacts exist.
- `callback` (optional object): Future callback target. The `secret_name` names a secret, not the secret value.

## Response body

```json
{
  "job_id": "podcast-2026-W23-abc12345",
  "status": "accepted",
  "manifest_url": "https://storage.example/jobs/podcast-2026-W23-abc12345/manifest.json",
  "mp3_url": "https://storage.example/jobs/podcast-2026-W23-abc12345/audio/podcast-2026-W23-abc12345.mp3",
  "wav_url": null,
  "transcript_url": "https://storage.example/jobs/podcast-2026-W23-abc12345/transcript.txt",
  "show_notes_url": "https://storage.example/jobs/podcast-2026-W23-abc12345/show-notes.md",
  "publishing_packet_url": "https://storage.example/jobs/podcast-2026-W23-abc12345/packets/podcast-2026-W23-abc12345.zip",
  "expires_at": "2026-06-14T17:41:40Z",
  "warnings": ["audio is a deterministic placeholder pending TTS implementation", "human review is required before publishing"],
  "errors": []
}
```

### Response fields

- `job_id`: Stable job identifier for this request.
- `status`: `accepted`, `completed`, `failed`, or `dry_run`.
- `manifest_url`: URL for job manifest metadata.
- `mp3_url`: URL for staged MP3 output when available.
- `wav_url`: Optional WAV URL. May be `null`.
- `transcript_url`: URL for transcript.
- `show_notes_url`: URL for show notes.
- `publishing_packet_url`: URL for the manual publishing packet.
- `expires_at`: Expiration timestamp for temporary URLs.
- `warnings`: Non-fatal issues.
- `errors`: Fatal issues. Empty for accepted jobs.

## Error responses

Validation failure:

```json
{
  "job_id": null,
  "status": "failed",
  "manifest_url": null,
  "mp3_url": null,
  "wav_url": null,
  "transcript_url": null,
  "show_notes_url": null,
  "publishing_packet_url": null,
  "expires_at": null,
  "warnings": [],
  "errors": ["week is required"]
}
```

## Local artifact staging

When `PODCASTER_STORAGE_ACCOUNT_URL` is not configured, the service writes deterministic development artifacts under `.podcaster-artifacts/jobs/<job_id>/` and returns URLs using `PODCASTER_ARTIFACT_BASE_URL`. Artifacts are staged with a 7-day expiration set in `expires_at`. This keeps local tests and API contract validation independent of Azure credentials. Azure deployments configure `PODCASTER_STORAGE_ACCOUNT_URL` and `PODCASTER_STORAGE_CONTAINER`; blob writes use the Function App managed identity, and artifacts expire per the same `expires_at` schedule.

## Manifest and packet metadata

The top-level response keys remain stable for SquadScope compatibility. Additional lifecycle details are stored in `manifest_url` and inside the publishing packet `MANIFEST.json`, including:

- `schema_version`
- `lifecycle.status`, `revision`, `force`, and deterministic transition timestamps
- `review.status`, blocked gate checks, and empty audit trail placeholders
- `generation.engine=local-deterministic-placeholder`, `deterministic=true`, and no paid/live TTS provider
- `publishing.mode=manual`, `eligible=false`, and blockers until human review and real audio exist
- artifact `content_type`, `size_bytes`, and `sha256`
- `observability.correlation_id` and safe log field names only
