# SquadScope to Podcaster Integration Contract

## Endpoint

- Production URL: `https://<aca-app-fqdn>/api/generate` (ACA App with HTTP ingress, see #131)
- Local URL: `http://localhost:8000/api/generate`
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
  "article_title": "This Week in Tech: AI and Open Source",
  "article_content": "Full article text here (optional — enables LLM script generation)",
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
  "script_directions": {
    "opening_cues": { "cold_open": "One stat that surprised you this week." },
    "episode_style": { "format": "Two-host conversational podcast, 8-10 minutes, 1200-1700 words." }
  },
  "music_mix": {
    "track": "Claracle Theme",
    "intro": { "full_volume_seconds": 10 },
    "outro": { "start_position": "0:00", "play_to_end": true }
  },
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
- `article_title` (optional string): Article title for script generation context.
- `article_content` (optional string): Full article text. When provided and the Azure OpenAI chat endpoint is configured, the system generates a dynamic LLM-based script and extracts real claims from the article instead of producing deterministic placeholders.
- `source_artifacts` (optional array): Supporting artifact references. For backward compatibility, each item may be either a string reference or an object reference emitted by SquadScope publish manifests.
- `dry_run` (optional boolean): Validate and generate draft/stub artifacts only.
- `force` (optional boolean): Regenerate even if prior artifacts exist.
- `callback` (optional object): Future callback target. The `secret_name` names a secret, not the secret value.
- `podcast_config` (optional object): Override podcast identity and style. Includes sub-fields `name`, `url`, `spoken_site`, `ai_voice_disclosure`, `host_a`, `host_b`, `style_guide`, and `dog_logo`.
- `podcast_config.style_guide` (optional string): Full text of the editorial style guide (segment structure, tone, phrasing principles). Passed from SquadScope's `docs/editorial-style-guide.md`. When present, it is included as context for script generation (#116).
- `podcast_config.dog_logo` (optional object): Configures a DOG (Digital On-Screen Graphic) logo watermark overlaid on the **main content** of the generated video (never on the intro/outro bumpers, which carry their own branding). When absent, no watermark is applied (graceful) — this remains fully supported. When **present**, the watermark is no longer best-effort: the Claracle logo is bundled in the synthesis image (`assets/images/claracle.jpeg`) and canonical Claracle URLs resolve to it with no network fetch. A configured watermark that cannot be resolved fails the job **permanently** (reason `watermark_asset_missing` or `watermark_fetch_failed`, one attempt, no retries) instead of shipping an unbranded episode. Sub-fields:
  - `url` (string, default `https://raw.githubusercontent.com/jmservera/SquadScope/main/assets/images/claracle.jpeg`): Logo image URL.
    - **Canonical Claracle URLs** (`claracle.com/images/claracle.jpeg`, `claracle.com/assets/images/claracle.jpeg`, and the SquadScope `raw.githubusercontent.com`/`github.com` variants) are served from the bundled asset with no network access, so a 404/DNS failure/rate limit cannot drop branding.
    - **Any other (third-party) URL** is downloaded through the SSRF-guarded fetcher (loopback/private/link-local/metadata hosts are refused, including via redirects), capped at 16 MiB, and validated from the response's actual image bytes rather than its `Content-Type` — a valid image served as `application/octet-stream` or with no `Content-Type` is accepted, while HTML error pages, forged `image/*` labels and decompression bombs are rejected. A third-party URL that fails is **never** substituted with the bundled Claracle logo (that would misbrand the episode); the job fails with `watermark_fetch_failed` instead. Fix the URL, point it at the canonical Claracle logo, or omit `dog_logo` for an intentionally unbranded episode.
  - `position` (string, default `top-right`): One of `top-left`, `top-right`, `bottom-left`, `bottom-right`.
  - `size` (integer, default `80`): Logo width in pixels (aspect ratio preserved).
  - `opacity` (float, default `0.5`): Logo opacity, clamped to `0.0`–`1.0`.
- `script_directions` (optional object): Guides LLM script generation with episode structure and cues. All sub-fields are optional:
  - `opening_cues.cold_open` (string): Prompt for a cold-open hook.
  - `opening_cues.ai_disclosure` (string): AI voice disclosure phrasing cue.
  - `closing_cues.corrections_path` (string): URL for listener corrections.
  - `closing_cues.source_article_link` (string): Source article link for outro.
  - `episode_style.format` (string): Episode format description (e.g., "Two-host conversational podcast, 8-10 minutes").
  - `episode_style.tone` (string): Tone guidance for the LLM.
  - `episode_style.segment_order` (array of strings): Ordered segment names.
- `music_mix` (optional object, also accepted nested under `script_directions`): Controls intro/outro music mixing. When absent, the default bundled music track plays with default timing:
  - `track` (string): Music track name. Omitting this field defaults to the bundled Claracle Theme. Specify `"Claracle Theme"` explicitly, or omit the field. The value `"Summer Sport"` is a legacy name that will NOT resolve to the retained historical asset — omit or use `"Claracle Theme"` instead.
  - `intro.full_volume_seconds` (number, default 10): Seconds of full-volume intro music before fading under speech.
  - `intro.fade_down_under` (string): Duration expression for the intro duck-under fade.
  - `outro.start_position` (string): Timestamp or duration expression for the outro start offset.
  - `outro.fade_up_during` (string): Duration expression for the outro fade-up.
  - `outro.play_to_end` (boolean, default true): Whether the outro plays to end of track.

### `source_artifacts` compatibility

Podcaster accepts both legacy string references and SquadScope object references in the same request. This is a backward-compatible `v1` contract behavior; callers do not need to send a new schema version.

String references are preserved as submitted. Object references must include at least one of:

- `path` (string): Repository-relative or artifact-relative path from the SquadScope publish manifest.
- `url` (string): HTTP(S) artifact URL.
- `href` (string): HTTP(S) artifact link emitted by a SquadScope manifest.
- `uri` (string): HTTP(S) artifact URI emitted by a SquadScope manifest.
- `name` (string): Stable operator-facing artifact name when no path or URL is available.

Recognized object metadata is preserved in the generated manifest and packet metadata when present: `role`, `path`, `url`, `href`, `uri`, `name`, `exists`, `size_bytes`, `sha256`, `artifact_checksum`, `week`, `crawled_at`, `generated_at`, `same_day_reuse`, `provenance`, `freshness`, `source_status`, `source_reuse_summary`, `source_artifact_provenance`, `source_config_checksum`, `schema_checksum`, `sources_requested`, `sources_succeeded`, and `sources_failed`. Unknown object fields are rejected so contract drift is visible during integration testing.

Regression fixtures live under `tests/fixtures/`:

- `podcaster_request_legacy_strings.json`
- `podcaster_request_squadscope_objects.json`

The deployed endpoint smoke check uses `tests/fixtures/podcaster_request_squadscope_objects.json` by default:

```bash
export PODCASTER_GENERATE_URL='https://<aca-app-fqdn>/api/generate'
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

When `PODCASTER_STORAGE_ACCOUNT_URL` is not configured, the service writes deterministic development artifacts under `.podcaster-artifacts/jobs/<job_id>/` and returns URLs using `PODCASTER_ARTIFACT_BASE_URL`. Artifacts are staged with a 7-day expiration set in `expires_at`. This keeps local tests and API contract validation independent of Azure credentials. Azure deployments configure `PODCASTER_STORAGE_ACCOUNT_URL` and `PODCASTER_STORAGE_CONTAINER`; blob writes use the ACA synthesis job's managed identity, and artifacts expire per the same `expires_at` schedule.

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

The storage lifecycle policy (`infra/main.bicep`) auto-deletes only **auto-generated** outputs — the `jobs/` and `bakeoff/` prefixes — after `artifactRetentionDays` (7 days). Operator **review** artifacts under the `review/` prefix (including `review/v3/`) are intentionally **excluded** and retained indefinitely until the editorial gate signs off (#93). Azure blob lifecycle filters cannot express exclusions, so review artifacts are protected by omitting their prefix from `autoExpireArtifactPrefixes`. Retiring review artifacts is an explicit operator action, not an automatic expiry.

## Manifest and packet metadata

The top-level response keys remain stable for SquadScope compatibility. Additional lifecycle details are stored in `manifest_url` and inside the publishing packet `MANIFEST.json`, including:

- `schema_version`
- `lifecycle.status`, `revision`, `force`, and deterministic transition timestamps
- `review.status`, blocked gate checks, and empty audit trail placeholders
- `generation.engine=local-deterministic-placeholder` (without `article_content`) or `llm-script-gen` (with `article_content` and configured chat endpoint), `deterministic=true/false` accordingly
- Initial response stages placeholder audio; the ACA synthesis job runs asynchronously and replaces it with real two-voice TTS (Azure OpenAI `gpt-4o-mini-tts`, voices fable + alloy). The manifest is updated in-place when synthesis completes.
- `publishing.mode=manual`, `eligible=false`, and blockers until human review and real audio exist
- `artifact_access.model=private_operator_path`, retention/cleanup timestamps, and audit correlation metadata
- artifact `content_type`, `size_bytes`, and `sha256`
- `observability.correlation_id` and safe log field names only
