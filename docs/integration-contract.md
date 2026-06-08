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

`PODCASTER_API_KEY` must be stored as a GitHub Actions secret in `jmservera/SquadScope` or synced by the Podcaster deployment workflow without printing it. Do not log, echo, or include it in workflow summaries. Future OIDC caller auth may be added, but this header remains the compatibility contract until both repositories migrate.

## Request body

```json
{
  "week": "2026-W23",
  "article_url": "https://squadscope.example/articles/2026-w23",
  "article_sha256": "optional-lowercase-hex-sha256",
  "source_artifacts": [
    "https://example.blob.core.windows.net/artifacts/source.json",
    {
      "role": "raw_github",
      "path": "data/candidates/2026-W23/github-crawl.json",
      "exists": true,
      "size_bytes": 45823,
      "sha256": "686085ace216e10d36837a91471e28a334b2fc3d93cc1085b8d5d0e7616891bf",
      "freshness": {
        "status": "fresh",
        "reasons": []
      },
      "provenance": {
        "generated_at": "2026-06-08T10:15:00Z",
        "sha256": "686085ace216e10d36837a91471e28a334b2fc3d93cc1085b8d5d0e7616891bf"
      }
    }
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
- `source_artifacts` (optional array): Supporting artifact references. For backward compatibility, each item may be either a string reference or an object reference emitted by SquadScope publish manifests.
- `dry_run` (optional boolean): Validate and generate draft/stub artifacts only.
- `force` (optional boolean): Regenerate even if prior artifacts exist.
- `callback` (optional object): Future callback target. The `secret_name` names a secret, not the secret value.

### `source_artifacts` compatibility

Podcaster accepts both legacy string references and SquadScope object references in the same request. This is a backward-compatible `v1` contract behavior; callers do not need to send a new schema version.

String references are preserved as submitted. Object references must include at least one of:

- `path` (string): Repository-relative or artifact-relative path from the SquadScope publish manifest.
- `url` (string): HTTP(S) artifact URL.

Recognized object metadata is preserved in the generated manifest and packet metadata when present: `role`, `exists`, `size_bytes`, `sha256`, `artifact_checksum`, `week`, `crawled_at`, `generated_at`, `same_day_reuse`, `provenance`, `freshness`, `source_status`, `source_reuse_summary`, `source_artifact_provenance`, `source_config_checksum`, `schema_checksum`, `sources_requested`, `sources_succeeded`, and `sources_failed`. Unknown object fields are rejected so contract drift is visible during integration testing.

Regression fixtures live under `tests/fixtures/`:

- `podcaster_request_legacy_strings.json`
- `podcaster_request_squadscope_objects.json`

The deployed endpoint smoke check uses `tests/fixtures/podcaster_request_squadscope_objects.json` by default:

```bash
export PODCASTER_GENERATE_URL='https://<function-app-name>.azurewebsites.net/api/generate'
export PODCASTER_API_KEY='<from secret manager>'
python scripts/smoke_generate.py
```

The script verifies HTTP 202 plus non-empty `job_id` and `manifest_url` fields with `errors=[]`. It does not print the API key or raw response body, and it redacts URL query strings before writing output so SAS tokens or other URL credentials do not appear in logs.

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
- `warnings`: Non-fatal issues, including reminders that placeholder artifacts require human review and returned artifact URLs are private operator paths.
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

## Artifact access semantics

Returned artifact URL fields (`manifest_url`, `mp3_url`, `transcript_url`, `show_notes_url`, and `publishing_packet_url`) are intentionally **private operator paths**, not public publishing links. Podcaster does not append SAS tokens, URL credentials, query strings, or fragments to response URLs. Access requires the operator's local filesystem access in development or explicitly granted Azure Storage permissions in deployed environments.

The manifest and packet metadata include `artifact_access` with:

- `model=private_operator_path`
- `response_urls.publicly_accessible=false`
- `response_urls.requires_operator_credentials=true`
- `response_urls.signed_urls=false`
- `retention.expires_at` and `retention.cleanup_after` equal to the top-level `expires_at`
- `audit.correlation_id` equal to `job_id`
- `publication.eligible=false` and blockers for `human_review` and `real_tts_not_implemented`

Cleanup is owned by the operator or a storage lifecycle policy using `expires_at`/`cleanup_after`. Audit review uses the job manifest, review audit trail placeholders, Application Insights `correlation_id`, and Azure Storage diagnostics. Placeholder artifacts remain blocked from publication until human/editorial review and real TTS gates exist.

## Manifest and packet metadata

The top-level response keys remain stable for SquadScope compatibility. Additional lifecycle details are stored in `manifest_url` and inside the publishing packet `MANIFEST.json`, including:

- `schema_version`
- `lifecycle.status`, `revision`, `force`, and deterministic transition timestamps
- `review.status`, blocked gate checks, and empty audit trail placeholders
- `generation.engine=local-deterministic-placeholder`, `deterministic=true`, and no paid/live TTS provider
- `publishing.mode=manual`, `eligible=false`, and blockers until human review and real audio exist
- `artifact_access.model=private_operator_path`, retention/cleanup timestamps, and audit correlation metadata
- artifact `content_type`, `size_bytes`, and `sha256`
- `observability.correlation_id` and safe log field names only
