# Distribution UX & Operator Readiness

This document specifies how Podcaster supports human-centered distribution workflows, from artifact generation through operator publication.

## Design Principles

1. **Link-only for SquadScope:** Podcaster returns URLs, not embedded content. SquadScope surfaces these links but does not host, stream, or embed audio.

2. **Operator self-service:** The publishing packet contains everything needed for publication without external documentation or reverse-engineering.

3. **No secrets in artifacts:** Packets, manifests, and responses must not include API keys, auth tokens, or internal credentials.

4. **Stable response shape:** Response keys in the integration contract are immutable. Additive fields only.

5. **Future research, not MVP:** Spotify/podcast-host automation is research-stage. Initial distribution is manual.

## Endpoint Handoff Expectations

### What Podcaster Returns

From `POST /api/generate`:

1. **job_id** (string)
   - Stable identifier for this request
   - Format: `podcast-{week}-{random-suffix}` (e.g., `podcast-2026-W23-abc12345`)
   - Used for traceability and replay

2. **status** (enum: `accepted`, `completed`, `dry_run`, `failed`)
   - `accepted`: Request validated; generation queued or in progress
   - `completed`: All artifacts ready
   - `dry_run`: Dry run requested; artifacts are stubs/placeholders
   - `failed`: Validation or generation error

3. **Artifact URLs** (all string or null)
   - `manifest_url`: Episode metadata and inventory
   - `mp3_url`: Podcast audio file (MP3)
   - `wav_url`: Uncompressed source (optional, may be null)
   - `transcript_url`: Full transcript
   - `show_notes_url`: Markdown show notes
   - `publishing_packet_url`: Zip archive for human publishing

   All URLs are short-lived temporary access links (SAS URLs on Azure Blob Storage, filesystem-based on local development). Expiration is set in `expires_at` (7 days from job creation by default).

4. **expires_at** (ISO 8601 timestamp)
   - All artifact URLs expire at this time
   - Typical: 7–14 days from generation
   - Operators must download and store locally if they need longer retention

5. **warnings** (array of strings)
   - Non-fatal issues (e.g., "transcript confidence < 0.8", "source_artifacts missing")
   - Generation continues; artifacts are available
   - Operator should review but can proceed

6. **errors** (array of strings)
   - Fatal issues (e.g., "article_url is unreachable", "week format invalid")
   - If any errors, artifact URLs are null and `status` is `failed`
   - Operator must fix input and retry

### What Podcaster Does NOT Return

- API keys or auth credentials
- Internal endpoint URLs or deployment info
- Spotify URIs, Apple Podcasts IDs, or platform-specific handles (those are added manually by operator)
- Callback results or proof of publication (operator owns publication)

## Operator Workflow

### Step 1: Generate

Podcaster (via SquadScope or a distribution dashboard):
```bash
POST /api/generate
x-podcaster-api-key: <secret>
{
  "week": "2026-W23",
  "article_url": "https://squadscope.example/articles/2026-w23",
  "dry_run": false
}
```

### Step 2: Download

Operator downloads the `publishing_packet_url` zip to local machine.

### Step 3: Review

Operator extracts the packet and opens `README.txt`:
- Verifies metadata (title, article URL, week)
- **Notes:** During the MVP phase, `episode.mp3` is a deterministic placeholder (see warnings in API response). Real TTS audio generation is not yet implemented. The operator should expect to replace audio before publication or wait for live TTS support.
- Reviews script, transcript, and show notes for accuracy and completeness
- Confirms rights/source links and licensing

### Step 4: Publish

**Current workflow (MVP — placeholder audio):**
- Operator cannot publish directly with placeholder audio
- Operator must either: (a) wait for live TTS implementation, or (b) manually generate audio and replace `episode.mp3` in the packet before uploading

**Future workflow (with live TTS):**
- Upload audio to Spotify for Creators, a podcast host, or their own RSS feed
- Fill in episode metadata (title, description, publication date)
- Set cover art (or use centrally managed asset)

### Step 5: Archive

Operator stores the packet and manifest locally for audit and future reference.

## Integration Contract Stability

**Critical:** Response keys must not change. Additive fields are acceptable; removals or renames break SquadScope automation.

Current response fields (immutable):
- `job_id`
- `status`
- `manifest_url`
- `mp3_url`
- `wav_url`
- `transcript_url`
- `show_notes_url`
- `publishing_packet_url`
- `expires_at`
- `warnings`
- `errors`

If new fields are needed in the future (e.g., `video_url`, `transcript_language`), they should be added to the response object, not replace existing ones.

## SquadScope Integration Points

### Calling Podcaster

SquadScope can call `POST /api/generate` in these scenarios:

1. **Manual trigger:** Editor clicks a "Generate podcast" button in the article UI
2. **Workflow trigger:** GitHub Actions workflow calls Podcaster after article publication (via API or manually)
3. **Dashboard:** Distribution operators use a standalone dashboard to trigger generation by week/article

All calls must include:
- `PODCASTER_API_KEY` from GitHub secrets (never logged)
- `PODCASTER_ENDPOINT` as the production or local URL (stored as a repository variable)

### Displaying Results

SquadScope can surface the response in the article UI or a distribution dashboard:

```
📻 Episode Ready
Status: Completed
🔗 [Download packet]
📋 [Show notes] | [Transcript]
Expires: 2026-06-14
```

If `status` is `failed` or `warnings` exist, display them prominently so the editor can decide whether to retry or investigate.

## Error Scenarios

### Validation Errors (HTTP 400)

Example: `week` is missing

```json
{
  "job_id": null,
  "status": "failed",
  "errors": ["week is required"],
  "warnings": []
}
```

**SquadScope action:** Show error message to editor; suggest fixing input.

### Transient Errors (HTTP 500)

Example: Podcaster service is down or storage is unavailable

**SquadScope action:** Retry with exponential backoff (after a few seconds). If persists, alert operator.

### Article Unreachable

Example: Article URL returns 404 or is behind authentication

```json
{
  "job_id": "podcast-2026-W23-abc12345",
  "status": "failed",
  "errors": ["article_url returned HTTP 404"],
  "warnings": []
}
```

**SquadScope action:** Show error; verify article is published and URL is correct.

## Future: Spotify and Podcast-Host Automation

### Research Scope

Before automating Spotify or podcast-host publishing, research must answer:

1. **APIs and Support**
   - Does Spotify expose a public API for direct episode upload? (Current known: Spotify for Creators accepts RSS feeds, but not direct API uploads)
   - What are the official podcast host APIs (Apple Podcasts, Google Podcasts, Anchor, Podbean, etc.)?
   - Are there rate limits, review delays, or approval requirements?

2. **Authentication and Credentials**
   - How are operator credentials (Spotify username/token, podcast host API keys) stored securely?
   - Can Podcaster safely receive and use these credentials without logging them?
   - Should credentials be stored in Podcaster or passed through SquadScope?

3. **Platform Terms**
   - Do Spotify and podcast host terms allow automated publishing?
   - Are there restrictions on bulk uploads, AI-generated content, or third-party automation?

4. **Operator Experience**
   - Should operators pre-authorize Podcaster with their credentials (OAuth flow)?
   - Or should publication remain manual with operator control?

5. **Rollback and Audit**
   - If an episode is auto-published and later needs removal, how does the operator control that?
   - How is publication audited for compliance?

### Automation Boundaries (Not In Scope)

The following are explicitly research/future work:

- **Spotify direct upload API:** Does not exist for podcasts as of the PRD date. RSS feed submission is the known path.
- **Cross-platform sync:** Auto-publishing to Spotify, Apple, Google, and custom RSS in one call.
- **Automatic episode numbering:** Podcaster does not auto-increment episode numbers or manage series metadata.
- **Embedded SquadScope player:** SquadScope does not embed audio players; it links to external platforms.

### Recommended Initial Path (Post-MVP Research)

1. Operator pre-authorizes Podcaster with podcast host credentials via secure OAuth/token flow
2. Podcaster exposes an optional `"publish_to"` field in the request body: `["rss", "spotify"]`
3. For RSS: Podcaster generates an RSS `<item>` and posts it to the operator's RSS endpoint
4. For Spotify: Podcaster submits the episode via Spotify for Creators RSS submission or equivalent
5. Response includes a `"publication_urls"` field with final platform URLs (added after operator approves)

This path remains contingent on research validation and operator workflow feedback.

## Documentation Checklist for Operators

Before an operator can publish, they must have:

- [ ] This document (or a summary)
- [ ] The publishing-packet README.txt (included in the zip)
- [ ] Instructions for their specific podcast host (Spotify, Apple, Anchor, etc.)
- [ ] Confirmation that they own/manage the podcast account
- [ ] Storage for the zip archive (for audit and rollback)

## Non-Secret Endpoint Handoff for Deployment

When Podcaster is deployed, the following values must be shared with SquadScope (as GitHub variables or secrets):

**Non-secret (can be logged):**
- `PODCASTER_ENDPOINT`: The production URL (e.g., `https://podcaster-prod.azurewebsites.net`)

**Secret (must not be logged):**
- `PODCASTER_API_KEY`: The API key for authentication

**SquadScope Configuration:**
- Store both in SquadScope repository secrets and/or workflow variables
- Use the endpoint for all `POST /api/generate` calls
- Use the API key in the `x-podcaster-api-key` header

See `docs/integration-contract.md` and `README.md` for deployment details.
