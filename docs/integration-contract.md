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
  "manifest_url": "https://storage.example/manifests/podcast-2026-W23-abc12345.json",
  "mp3_url": "https://storage.example/audio/podcast-2026-W23-abc12345.mp3",
  "wav_url": null,
  "transcript_url": "https://storage.example/transcripts/podcast-2026-W23-abc12345.txt",
  "show_notes_url": "https://storage.example/show-notes/podcast-2026-W23-abc12345.md",
  "publishing_packet_url": "https://storage.example/packets/podcast-2026-W23-abc12345.zip",
  "expires_at": "2026-06-14T17:41:40Z",
  "warnings": [],
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
