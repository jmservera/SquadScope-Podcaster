# Backlog: Manual Publishing Packet

Define and generate a zip packet for human publishing to Spotify, podcast hosts, or archives.

## Overview

The publishing packet is a self-contained ZIP file containing all artifacts, metadata, and rights documentation needed for a human operator to manually upload an episode to distribution platforms.

**Full packet contents** are defined in **section 7 (Publishing Packet Contents)** of `docs/editorial-standards.md`.

---

## Packet Structure

```
packet-2026-W23/
├── MANIFEST.json                    (job metadata, review status, audit trail)
├── script.txt                       (approved script)
├── claim-ledger.json                (claim-to-source mapping)
├── transcript.txt                   (or .srt; timestamps + full audio text)
├── show-notes.md                    (markdown with links and context)
├── audio/
│   ├── episode-2026-W23.mp3         (primary audio)
│   └── episode-2026-W23.wav         (optional archival)
├── RIGHTS-AND-ATTRIBUTION.txt       (TTS voice rights, article license, show notes license)
└── CHECKSUMS.txt                    (SHA-256 of all above for integrity)
```

### Manifest (MANIFEST.json)

Contains:
- Job ID, week, article URL, article SHA-256
- Generation timestamp, review timestamp, reviewer identity
- Review status (`approved` only for production packets)
- Review notes and audit trail
- TTS provider, voice name, duration
- License type (e.g., CC-BY-4.0)
- Expiration time for artifact URLs

Example:
```json
{
  "job_id": "podcast-2026-W23-abc12345",
  "week": "2026-W23",
  "article_url": "https://squadscope.example/articles/2026-w23",
  "article_sha256": "...",
  "generated_at": "2026-06-07T19:00:00Z",
  "reviewed_at": "2026-06-07T19:30:00Z",
  "reviewer": "editor@squadscope.example",
  "review_status": "approved",
  "review_notes": "Script reads naturally; all claims verified. Ready for distribution.",
  "tts_provider": "azure-speech",
  "tts_voice": "en-US-AriaNeural",
  "duration_seconds": 942,
  "license": "CC-BY-4.0",
  "expires_at": "2026-06-14T19:00:00Z"
}
```

### Script (script.txt)

The approved, reviewed script meeting all standards in `docs/editorial-standards.md` section 1.

### Claim Ledger (claim-ledger.json)

Tab-separated or JSON format. Maps every factual claim in the script back to the source article. Example:

```json
[
  {
    "claim_id": "script_001",
    "script_excerpt": "SquadScope published 23 articles last week.",
    "source_url": "https://squadscope.example/articles/2026-w23",
    "source_quote": "This week, SquadScope released 23 new articles.",
    "verified": true,
    "editor_notes": "Direct match; article count confirmed in metadata."
  },
  {
    "claim_id": "script_002",
    "script_excerpt": "This represents a 15% increase from the prior week.",
    "source_url": "https://squadscope.example/articles/2026-w23",
    "source_quote": "Prior week metrics: 20 articles.",
    "verified": true,
    "editor_notes": "Calculated: (23-20)/20 = 15%. Editor approved math."
  }
]
```

### Transcript

Plain-text or SRT format with timestamps. Must include metadata header and cover 100% of audio. See `docs/editorial-standards.md` section 3.

### Show Notes

Markdown with links, timestamps, and context. Must include episode metadata and citations. See `docs/editorial-standards.md` section 4.

### Audio

- **MP3:** Primary format, 192 kbps, 44.1 kHz (standard podcast codec)
- **WAV:** Optional, lossless archival, 44.1 kHz or higher

### Rights and Attribution

Plain text documenting:
- TTS voice license and required attribution (e.g., "Generated with Microsoft Azure Speech Services")
- Original article copyright and license (e.g., "© SquadScope, CC-BY-4.0")
- Show notes license matching audio
- Distribution restrictions (if any)
- Any non-standard clauses from TTS provider

### Checksums

SHA-256 hashes for all files in the packet. Enables integrity verification after download.

```
script.txt: abc123...
transcript.txt: def456...
show-notes.md: ghi789...
audio/episode-2026-W23.mp3: jkl012...
audio/episode-2026-W23.wav: mno345...
claim-ledger.json: pqr678...
MANIFEST.json: stu901...
```

---

## Generation Workflow

1. **Generate artifacts** (from source article + TTS provider)
   - Script → TTS synthesis → audio + timestamps
   - Script + source → claim ledger
   - Audio + timestamps → transcript
   - Script summary + article references → show notes

2. **Validate artifacts** against `docs/editorial-standards.md` standards
   - Script readiness (section 1)
   - Claim verification (section 2)
   - Transcript completeness (section 3)
   - Show notes citations (section 4)

3. **Human editorial review** (mandatory)
   - All checks from `backlog/human-review-gate.md` section 6
   - Reviewer approves or requests changes
   - If rejected: artifacts invalidated; regeneration required

4. **Approved → packet generation**
   - Combine all approved artifacts into ZIP
   - Generate MANIFEST.json with review metadata and audit trail
   - Generate CHECKSUMS.txt
   - Create RIGHTS-AND-ATTRIBUTION.txt

5. **Return to caller**
   - Staging URL: `https://storage.example/packets/podcast-2026-W23-abc12345.zip`
   - Expiration time: typically 7–14 days from approval
   - Caller (SquadScope) downloads packet and stores for manual publishing

---

## Manual Publishing Steps (for human operator)

1. Download `publishing_packet_url` from API response
2. Extract ZIP
3. Read MANIFEST.json to understand episode metadata and review status
4. Review RIGHTS-AND-ATTRIBUTION.txt to confirm licensing and distribution rights
5. Verify CHECKSUMS.txt for file integrity
6. Use show-notes.md as content template for podcast host metadata
7. Confirm AI/synthetic voice disclosure and rights attribution are present in the audio and show notes
8. Upload audio file (MP3) to Spotify for Creators or the selected podcast host
9. Enter episode title, description (from show-notes.md), transcript, source article URL, and corrections/contact link
10. Set publish date and schedule
11. Publish episode
12. Record the public episode URL, platform/show URL, publish timestamp, and any corrections/update notes in the operator audit trail

---

## Storage & Expiration

- Packets are staged with time-bound URLs expiring 7 days after job creation (set in response `expires_at`)
- After expiration, URLs return HTTP 404 (SAS URL becomes invalid or filesystem artifact is not served)
- **Operator must download and store locally if longer retention is needed**
- **Future:** Podcaster may retain a long-term archive in a separate storage container (retention policy: 1+ years)
- Regeneration with `force: true` creates new packet with new job ID and new URLs

---

## Implementation Notes

- ZIP generation is implemented in `podcaster/packaging.py` (Bender)
- Manifest generation includes audit trail from review gate (section `backlog/human-review-gate.md`)
- Checksums are computed client-side or server-side before ZIP finalization (security: prevent tampering)
- Testing: verify packet extracts cleanly; all files present; MANIFEST.json is valid JSON; CHECKSUMS match all files
