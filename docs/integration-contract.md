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

`PODCASTER_API_KEY` must be stored as a GitHub Actions repository secret in `jmservera/SquadScope` and, for handoff workflows, as the same secret on environment `podcaster-real-generation`; the Podcaster deployment workflow syncs both without printing it. Do not log, echo, or include it in workflow summaries. Future OIDC caller auth may be added, but this header remains the compatibility contract until both repositories migrate.

## Read-only terminal status

`GET /api/publication-status` uses the same API-key header and returns
`schema_version: "podcast_publication_status_v1"`. Set the SquadScope repository **and**
`podcaster-real-generation` environment variable `PODCASTER_STATUS_ENDPOINT` to
`https://<aca-app-fqdn>/api/publication-status` after deploying the reviewed API image.
Do not append a job path or put credentials in the URL.

Required query parameters are `week`, `publish_run_id`, `article_sha256`,
`manifest_sha256`, `job_id`, and `correlation_id`. They must exactly match the accepted
canonical job; the response correlation ID is the accepted job ID. `language` is optional
(defaults to `en`), accepts `en`/`es`/`fr`, and must match that job's stored language.
Duplicate, unknown, unsafe, or blank parameters are rejected.

The response includes the exact four-field `identity`, `job_id`, `correlation_id`,
`synthesis.state` (`pending`, `started`, `succeeded`, `failed`, `unknown`),
`video.state` (`pending`, `succeeded`, `failed`, `unknown`), and
`provider.state` (`pending`, `published`, `failed`, `unknown`) plus boolean
`provider.external_verified`. Supplemental per-provider codes, check timestamps,
language, and `observed_at` describe missing, incomplete, or stale evidence without
returning logs, artifact paths, signed URLs, bodies, or secrets.

This endpoint projects the existing manifest/progress and durable publication documents.
It never generates, enqueues, mutates receipts, uploads, changes visibility, or promotes.
The response's `provider.semantics` explicitly distinguishes:

- `canonical_public_outbox`: exact-script jobs and jobs with an existing distribution
  outbox reference use that outbox only. Both YouTube and Spotify VIDEO require bound
  receipts and terminal public verification, validated by the existing weekly aggregation
  proof rules. `provider.weekly.state` and `provider.attempts` retain weekly identity truth
  separately from each attempt's outcome. Durable completed-public evidence does not require
  a new live OAuth read on every poll. Missing references, ambiguous receipts, invalid proofs,
  or identity/locale conflicts never fall back to legacy evidence.
- `legacy_delivery`: only pre-exact-script jobs without an outbox reference use legacy
  publication evidence. Their YouTube video must be identity-tagged and freshly read back
  as **processed**, processing **succeeded**, and **unlisted or public**; Spotify must have
  an identity-bound VIDEO publication receipt backed by authoritative provider readback.
  This delivery objective does not certify canonical public weekly success.

Audio-only, draft, private, ambiguous, or unverified delivery is not green. Legacy receipts
older than 24 hours or over five minutes in the future are not reusable; polling does not
refresh a persisted receipt's age.

The API identity needs read access to job/progress/publication-evidence blobs and only the
YouTube client ID, client-secret reference, and refresh-token reference used by the existing
OAuth reader. No Spotify credentials are needed by this route. If Key Vault is used, grant
only secret-read access to the YouTube reference, not vault write access. OAuth exchange and
exact-video `videos.list` reads use three-second HTTP timeouts; no provider mutation is allowed.

API deployment wiring is opt-in: set `publicationStatusYoutubeReadbackEnabled=true` in
`infra/main.bicep` deployment parameters only when legacy live readback is needed. It passes
the existing secure `videoYoutubeClientId`, `videoYoutubeClientSecret`, and
`videoYoutubeRefreshToken` parameters to ACA secret references for the API, not plain-text
environment values or outputs. The default is false; incomplete credentials emit no
YouTube secrets/env and readback fails closed. This adds no roles, OAuth scopes, network
access, upload flags, or provider-write paths. Do not enable the caller endpoint until
the reviewed API image and required credential configuration have been validated.

Errors are sanitized: `401` unauthenticated/unconfigured key, `400` invalid query,
`404` missing job, `409` missing/malformed/mismatched canonical identity or evidence,
and `503` storage unavailable. Valid accepted jobs can report pending stages or explicit
unknown provider evidence without inventing success.

## Request body

```json
{
  "week": "2026-W23",
  "publish_run_id": "12345",
  "article_url": "https://squadscope.example/articles/2026-w23",
  "article_sha256": "optional-lowercase-hex-sha256",
  "manifest_sha256": "optional-lowercase-hex-sha256",
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
- `publish_run_id` (optional decimal string): Exact SquadScope crawl-and-publish run identifier. When supplied with `manifest_sha256`, it is persisted unchanged as part of the cross-repository publication identity.
- `article_url` (required string): Published article URL from SquadScope.
- `article_sha256` (optional string): SHA-256 digest of article artifact/content.
- `manifest_sha256` (optional string): SHA-256 digest of the exact SquadScope publish manifest bytes. It is additive for legacy callers and required for canonical reconciliation evidence.
- `article_title` (optional string): Article title for script generation context.
- `article_content` (optional string): Full article text. When provided and the Azure OpenAI chat endpoint is configured, the system generates a dynamic LLM-based script and extracts real claims from the article instead of producing deterministic placeholders.
- `source_artifacts` (optional array): Supporting artifact references. For backward compatibility, each item may be either a string reference or an object reference emitted by SquadScope publish manifests.
- `dry_run` (optional boolean): Validate and generate draft/stub artifacts only.
- `force` (optional boolean): Regenerate even if prior artifacts exist.
- `callback` (optional object): Future callback target. The `secret_name` names a secret, not the secret value.
- `podcast_config` (optional object): Override podcast identity and style. Includes sub-fields `name`, `url`, `spoken_site`, `ai_voice_disclosure`, `host_a`, `host_b`, `style_guide`, and `dog_logo`.
- `podcast_config.style_guide` (optional string): Full text of the editorial style guide (segment structure, tone, phrasing principles). Passed from SquadScope's `docs/editorial-style-guide.md`. When present, it is included as context for script generation (#116).
- `podcast_config.dog_logo` (optional object): Configures a DOG (Digital On-Screen Graphic) logo watermark overlaid on the **main content** of the generated video (never on the intro/outro bumpers, which carry their own branding). When absent, no watermark is applied (graceful) — this remains fully supported. When **present**, the watermark is no longer best-effort: the Claracle logo is bundled in the synthesis image (`assets/images/claracle.jpeg`) and canonical Claracle URLs resolve to it with no network fetch. A configured watermark that cannot be resolved fails the job instead of shipping an unbranded episode, with the failure classified by *whether a retry could help*: a **permanent** failure (reason `watermark_asset_missing` or `watermark_fetch_failed` — a malformed/blocked/unsupported URL, a definitive `4xx` such as `404`, an oversized body, or bytes that are not a valid image) ends the job on a single attempt with no retries, while a **transient** failure (reason `watermark_fetch_transient` — timeout, DNS/connection error, or HTTP `408`/`425`/`429`/`5xx`) leaves the job for the normal bounded retry. Sub-fields:
  - `url` (string, default `https://raw.githubusercontent.com/jmservera/SquadScope/main/assets/images/claracle.jpeg`): Logo image URL.
    - **Canonical Claracle URLs** (`claracle.com/images/claracle.jpeg`, `claracle.com/assets/images/claracle.jpeg`, and the SquadScope `raw.githubusercontent.com`/`github.com` variants) are served from the bundled asset with no network access, so a 404/DNS failure/rate limit cannot drop branding.
    - **Any other (third-party) URL** is downloaded through the SSRF-guarded fetcher (loopback/private/link-local/metadata hosts are refused, including via redirects), capped at 16 MiB, and validated from the response's actual image bytes rather than its `Content-Type` — a valid image served as `application/octet-stream` or with no `Content-Type` is accepted, while HTML error pages, forged `image/*` labels and decompression bombs are rejected. Downloads are cached by a hash of the URL; a cache hit is re-validated against the same image-bytes check (and discarded and re-downloaded if it no longer parses) and every fresh download is renamed into place atomically, so a truncated or poisoned cache entry is never handed to ffmpeg. A third-party URL that fails is **never** substituted with the bundled Claracle logo (that would misbrand the episode) in either classification: a definitive failure ends the job with `watermark_fetch_failed`, while an unreachable endpoint (timeout, DNS/connection error, `408`/`425`/`429`/`5xx`) is reported as `watermark_fetch_transient` and retried. Fix the URL, point it at the canonical Claracle logo, or omit `dog_logo` for an intentionally unbranded episode.
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

The storage lifecycle policy (`infra/main.bicep`) auto-deletes only **auto-generated** outputs — the deployment-owned `jobs/` and `bakeoff/` prefixes — after `artifactRetentionDays` (7 days). Operator **review** artifacts under `review/` (including `review/v3/`) and canonical publication evidence under `publication-evidence/` are outside every automatic-expiry match. Azure lifecycle filters cannot express exclusions, so the expiry prefix list is non-configurable and limited to generated outputs. Retiring review artifacts is an explicit operator action; canonical publication evidence is retained for at least 28 days.

## Manifest and packet metadata

The top-level response keys remain stable for SquadScope compatibility. Additional lifecycle details are stored in `manifest_url` and inside the publishing packet `MANIFEST.json`, including:

- `schema_version`
- `lifecycle.status`, `revision`, `force`, and deterministic transition timestamps
- `review.status`, blocked gate checks, and empty audit trail placeholders
- Production requires `article_content` and configured LLM generation; missing configuration
  or failed generation fails closed rather than accepting a placeholder.
  `generation.engine=llm-script-gen`; dry runs may still use the deterministic placeholder.
- Initial response stages the final script and placeholder audio, **without enqueueing
  TTS**. Manifest `status=review_pending`, `review.required_for_tts=true`,
  `generation.synthesis_queue.status=awaiting_review` and `tts_synthesis.allowed=false`
  describe this pause. The eleven response keys and HTTP 202 `accepted` remain unchanged.
  A staging/COST-ledger persistence failure never opens the approval gate.
- Transcript and packet script/claim content are derived from the same staged generation;
  durations remain explicitly pending rather than invented timestamps. The packet
  `MANIFEST.json` records the script digest. The mutable job manifest is authoritative
  for approval and later lifecycle state.
- `publishing.mode=review_gate`, `eligible=false`, and blockers until human review and real audio exist
- `artifact_access.model=private_operator_path`, retention/cleanup timestamps, and audit correlation metadata
- artifact `content_type`, `size_bytes`, and `sha256`
- `observability.correlation_id` and safe log field names only

### Exact-content human review before TTS

Use the existing authenticated `POST /api/review` with `job_id`, actual human
`reviewer`, `decision`, and timezone-qualified `reviewed_at` (server time if omitted).
For an `approved` decision **before audio exists**, also submit
`script_sha256` and `manifest_sha256`, copied from the staged job's `review.snapshot`.
They are optional for other review decisions and existing post-audio publication review.
Review the actual script, transcript, packet, unverified claim ledger, provider
constraints/licensing, rights, final configured AI disclosure, acronym pronunciation,
and Markdown readiness. An agent must not impersonate a human reviewer.

Pre-TTS approvals additionally require `review_checks_confirmed: true` and a
`provider_authorization` object with **only** these fields:

```json
{
  "provider_settings_sha256": "<generation.provider_settings_sha256 reviewed by the human>",
  "selected_provider": "<generation.provider_settings.provider>",
  "fallback_provider": "<fallback recorded in the approved provider decision>",
  "provider_selection_reference": "<recorded primary/fallback bakeoff decision>",
  "provider_privacy_review_reference": "<recorded terms, retention, deletion and training review>",
  "rai_security_signoff_reference": "<actual security/RAI reviewer signoff>",
  "disclosure_reference": "<merged disclosure linked from docs/SECURITY.md>",
  "region": "<approved processing region>"
}
```

All values must be nonempty strings. References must identify actual recorded
evidence, not promises or generated signoffs, and must not contain credentials or
signed URLs. The approving human attests to these checks and the effective settings
fingerprint, including voices/model and pool policy. This records authorization;
`production_ready` alone is configuration, **not** privacy/security clearance.
The primary/fallback decision, retention/training/deletion terms, region, logging,
cost/rate/retry limits, rights and merged operator disclosure must already satisfy
`docs/SECURITY.md`. No fallback is invoked automatically by this endpoint.
Missing or mismatched authorization leaves the provider-selection/privacy/RAI
blockers in force regardless of snapshot presence.

The protected `podcast-review-gate.yml` workflow accepts both digests, the JSON
authorization and the explicit checklist confirmation as human-supplied inputs.
It never fetches/hashes unknown content to manufacture human consent.
These additional inputs remain optional for existing post-audio publication review.

Here `script_sha256` hashes the exact staged UTF-8 script bytes.
`manifest_sha256` hashes the canonical immutable content projection: job identity,
creation/expiry, request/source/replay/config metadata, artifact metadata and hashes,
COST ledger, generation engine, resolved podcast config, and provider-settings digest.
It is **not** the hash of the mutable `manifest.json` file and is **not** the upstream
publication-identity `request.manifest_sha256`; those remain separate contracts.
Lifecycle/progress/audit/dispatch state is excluded so concurrent state can be preserved.
The provider fingerprint covers effective TTS settings and pool policy without storing
credentials or the provider endpoint itself.

Approval never regenerates the script. It atomically binds both digests to the human
identity/time/decision/audit and consumes one dispatch intent before sending the
existing job-ID-only synthesis message. `publish_on_approval` still controls only
post-audio publication, not this pre-TTS dispatch. Stale/missing digests, changed
content/config/provider settings, missing approval binding, dry runs, or repeated
dispatch requests fail closed (`409` for binding/dispatch conflicts).
Both the deployed FastAPI monitoring app and the standalone API use this same
review route contract and conflict status.
`required_for_tts:false` cannot waive the policy.
Script approval and its evidence are stored only under
`review.synthesis_approval` (`binding.scope=pre_tts`), with a dedicated audit trail.
Shared publication fields `review.status`, `approved_by`, `approved_at` and
`audit_trail` remain pending/empty. For exact-script jobs, subsequent generic
post-audio decisions are recorded separately under `review.audio_approval`;
they do not authorize shared audio/video/public promotion or replace the
original synthesis binding. Existing nonsnapshot post-audio review behavior
is unchanged.

Dispatch states are `staging`, `awaiting_review`, `dispatching`, `enqueued`,
`not_configured`, or `unknown`. A lost acknowledgement/crash may leave `dispatching`
or `unknown`; neither those states nor `not_configured` permit blind review resubmission.
Inspect/reconcile the existing queue/job evidence instead. No automatic recovery or
artifact adoption endpoint is introduced. Dry-run artifacts cannot become production.
Negative decisions (`rejected` or `changes_requested`) remain available after dispatch:
they atomically revoke script approval without replacing or rearming the dispatch intent.
An already accepted provider request cannot be rolled back; any live synthesis outcome
requires reconciliation, not automatic retry.

Before any TTS provider call the worker re-verifies the staged bytes, immutable
projection, effective provider/config settings and recorded binding. A separate atomic
`generation.synthesis_claim` (`started`/`completed`/`unknown`) ensures concurrent or
duplicate queue delivery cannot duplicate TTS. A started or ambiguous provider attempt
is consumed permanently pending operator reconciliation, even if audio/result
persistence failed. Existing cost gates and explicit provider/rights blockers remain.
Exact-review synthesis admits one segment at a time, checking the current approval
before every attempt (including auxiliary clips). Only explicit HTTP 429 rejections
are retried with the configured backoff; ambiguous transport/timeouts and server
failures stop the run, mark the claim unknown and never send later segments.
Legacy non-exact-review callers retain their existing pool and retry behavior.

Infrastructure passes the same endpoint, managed-identity auth mode, deployment,
voices and `ttsRuntimePolicy` (API version, styles, format and pool/backoff settings)
to the API and synthesis deployments, so both compute the same provider fingerprint.

Pre-TTS script approval is **not** audio/video/public promotion approval. Synthesis
does not publish audio automatically. The staged script includes the caller's
configured synthetic-voice disclosure as its final spoken text, even when it
already appears in the opening; transcript content follows that same script.
Exact-script jobs preserve the shared
`human_review` publication blocker even after a generic post-audio approval and
never automatically enqueue the legacy video pipeline, including on synthesis
redelivery. Neither script nor audio review can authorize an unknown future video.
The legacy Spotify video path can live-publish independently of outbox approval;
it is not a supported publication route for these jobs.

Eventual video delivery requires protected review-only/outbox preparation followed
by an actual human review bound to the completed video bytes and publication
identity: YouTube must retain human media-bound outbox review, and Spotify remains
operator-only for reviewed video. This exact-script-to-TTS change does **not**
establish a supported safe preparation/publication route for these jobs or
implement that later review. Video/provider mutation therefore remains blocked;
generic `/api/review` approval, `publish_on_approval`, and a configured video target
do not unblock it. Existing unrelated legacy publication behavior is unchanged.
The manual video enqueue endpoint returns `409` for these jobs, and the direct video
worker skips them before resolving default configuration, stores or mutable budgets.

## Provider delivery outcomes

Provider delivery state is additive and does not change the stable top-level
response keys or legacy status values. Current manifests and authenticated job
detail responses may expose `outcome`/`publication_outcome` using exactly:
`uploaded`, `draft_created`, `manual_handoff_required`, `published`, or
`publication_unknown`.

`published` requires independent provider read-back. A successful upload that
remains non-public is `draft_created`; an ambiguous mutation is
`publication_unknown` and blocks automatic repetition. Accepted jobs may also
have `publication-evidence/{job_id}.json`, an atomic immutable append-only
transition log under a dedicated durable prefix that is not matched by the
seven-day jobs lifecycle. It has no count-based eviction and a declared 28-day
minimum retention window. Existing
`jobs/{job_id}/publication-evidence.json` documents remain readable and migrate
forward on the next append; legacy manifests without either document remain
readable.

Rollback does not delete provider artifacts. Disable consumption of the
additive fields or revert the implementation while continuing to treat legacy
fields as authoritative.
## Publication identity and provider state

New SquadScope handoffs use the canonical publication identity
`(week, publish_run_id, article_sha256, manifest_sha256)`. If either
`publish_run_id` or `manifest_sha256` is supplied, or
`publication_identity_mode=canonical` is declared, all four identity fields are
required. `week` must be `YYYY-WNN`, the run ID remains a decimal string, and
both digests remain lowercase 64-hex values. The accepted manifest persists
these values unchanged; mismatch, omission, or conflict blocks provider
mutation. Requests that omit the canonical fields entirely remain readable
through the bounded legacy path and are persisted with
`publication_identity_mode=legacy`; explicitly legacy requests cannot include
canonical-only fields and do not gain canonical reconciliation evidence.

Provider evidence keeps transport, provider state, and public verification
separate. Each record includes normalized `status`, `provider_id`,
`native_state`, `verification`, `checked_at`, `evidence_source`,
`last_error_code`, and `retry_blocked` semantics while retaining the legacy
`outcome` and provider artifact field for readers already deployed.
`verification=provider_readback` proves only API/provider state.
`status=public` requires `verification=external_verified`, representing an
anonymous listener-facing check. Uploaded, draft, private, unlisted, gated, and
unknown records never count as publicly completed delivery.

Existing video publication snapshots retain their legacy `status=published`
at-most-once marker. Their additive `provider_status` and `verification` fields,
and the normalized records exposed beside them, are authoritative for public
visibility; the legacy marker alone never proves listener availability.

Publication evidence is keyed by the accepted job ID under the dedicated
`publication-evidence/` prefix as an append-only document. The deployed
lifecycle expires only `jobs/` and `bakeoff/`, so canonical evidence is not
deleted by the seven-day generated-artifact rule. Records are not evicted by
count, preserving at least the four-week acceptance and reconciliation window.
The document declares
`minimum_retention_days=28` and
`retention_policy=append_only_no_count_eviction`; rollback preserves it.
