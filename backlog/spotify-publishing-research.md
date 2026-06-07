# SquadScope Integration UX — Link-Only Distribution

## Design Principle

SquadScope displays podcast artifacts as links, not embedded content. This keeps SquadScope focused on article publishing and allows podcast workflows to evolve independently. Operators control where and how episodes are published.

## What SquadScope Surfaces (Link-Only)

After calling Podcaster `POST /api/generate`, SquadScope can store and display:

1. **Publishing packet link** (`publishing_packet_url` from response)
   - Labeled: "Download episode packet"
   - Tooltips: "Contains script, transcript, show notes, and audio — ready for manual publication"
   - Link opens a zip download; no audio embedded on SquadScope

2. **Manifest link** (`manifest_url` from response)
   - Labeled: "Episode metadata" (optional; primarily for operators/auditing)
   - Contains job tracking, checksums, and artifact inventory

3. **Transcript link** (`transcript_url` from response)
   - Labeled: "Episode transcript" (optional)
   - Can be displayed as inline text or as a downloadable link

4. **Show notes link** (`show_notes_url` from response)
   - Labeled: "Episode show notes" (optional; pre-rendered for reference)

## What SquadScope Does NOT Do

- **No audio player:** SquadScope does not embed `<audio>` tags or stream the MP3 directly
- **No direct Spotify/Apple Podcasts links:** SquadScope does not assume episodes are published; it only links to the packet or the operator's destination
- **No automation of Spotify/podcast-host uploads:** SquadScope does not call Spotify APIs or podcast host APIs on behalf of the operator
- **No storage:** SquadScope does not cache or copy artifacts; it links to transient URLs from Podcaster

## Integration Handoff

### Request

SquadScope calls `POST /api/generate` with:
- `week`: Article publication week (ISO format, e.g., "2026-W23")
- `article_url`: Published article URL
- `article_sha256`: (optional) SHA-256 of article content for traceability
- `dry_run`: (optional) If true, returns draft/stub artifacts only

### Response

```json
{
  "job_id": "podcast-2026-W23-abc12345",
  "status": "accepted|completed|dry_run",
  "manifest_url": "https://storage.blob.core.windows.net/podcasts/manifests/podcast-2026-W23-abc12345.json",
  "mp3_url": "https://storage.blob.core.windows.net/podcasts/audio/podcast-2026-W23-abc12345.mp3",
  "wav_url": null,
  "transcript_url": "https://storage.blob.core.windows.net/podcasts/transcripts/podcast-2026-W23-abc12345.txt",
  "show_notes_url": "https://storage.blob.core.windows.net/podcasts/show-notes/podcast-2026-W23-abc12345.md",
  "publishing_packet_url": "https://storage.blob.core.windows.net/podcasts/packets/podcast-2026-W23-abc12345.zip",
  "expires_at": "2026-06-14T17:41:40Z",
  "warnings": [],
  "errors": []
}
```

**Response Shape is Stable:** These keys are fixed; SquadScope can depend on them for automation or UI rendering. See `docs/integration-contract.md` for the contract.

### Displaying Results to End Users

SquadScope can surface results in article metadata or a separate "podcast" section:

```
📻 Episode Available
✓ Status: Completed
🔗 [Download episode packet] (expires 2026-06-14)
📋 [Show notes] | [Transcript] (optional)
Article ID: 2026-W23
```

## No Automatic Publication

- SquadScope does NOT publish to Spotify, Apple Podcasts, or any platform automatically
- SquadScope does NOT sync episode URLs back to Podcaster or store Spotify URIs
- An operator (human editor or distribution staff) must manually download the packet and publish using the README instructions
- Future automation (Spotify/podcast-host direct API calls) is research-stage and out of scope for SquadScope core

## Error Handling for SquadScope

If Podcaster returns `status: failed` or `errors` are populated:
- SquadScope should surface the error to the editor (e.g., "Podcast generation failed: [error message]")
- SquadScope does NOT retry automatically
- Editor can request a regeneration with `force: true` in a follow-up call

## Future: Podcast URL Linking (Not In Scope)

If/when episodes are published to a public platform (e.g., Spotify or Apple Podcasts), a *separate* link might be added to the SquadScope article:

```
Listen on: [Spotify] [Apple Podcasts]
```

This link would be added manually or through a future, separate integration. It is NOT part of the Podcaster MVP and must be explicitly designed when podcast URLs are public and stable.
