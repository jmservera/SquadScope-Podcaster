# Editorial Standards for Podcast Production

## Overview

This document defines the local editorial artifact standards required for the SquadScope Podcaster production path. All artifacts—script, claim ledger, transcript, show notes, and publishing packet—must satisfy these standards before entering the TTS synthesis pipeline or the human review gate.

## Scope

These standards govern:
- **Script generation:** How source articles become TTS-ready copy
- **Claim ledger:** Traceability from generated claims back to source material  
- **Transcript standards:** Accessibility, accuracy, and searchability
- **Show notes:** Discoverability, citations, and editorial consistency
- **TTS provider expectations:** Input/output contracts and readiness criteria
- **Review gate constraints:** What must be verified before non-dry-run synthesis

These standards are **editorial in nature** and do not prescribe API, storage, or deployment mechanics.

---

## 1. Script Generation Standards

### 1.1 Source traceability

Every script MUST be traceable to the source article:
- Required: `article_url` (from integration contract request)
- Required: `article_sha256` (optional in request; computed if missing)  
- Required: Generation timestamp and tool chain version
- Stored in: Script manifest metadata (not the script body itself)

### 1.2 TTS-ready copy requirements

The script is the source material for TTS synthesis. It must meet these constraints:

#### Plain-text format
- No markup, HTML, or inline metadata
- No URLs or references embedded in prose (use show notes instead)
- No special characters that TTS engines misinterpret (®, ©, °, etc.)
- Expand all acronyms on first mention: "AI" → "artificial intelligence," "API" → "application programming interface"

#### Pronunciation and clarity
- Numbers spelled out unless clearly indicating a date or formal code: "2026" is fine; "23 articles" → "twenty-three articles"
- Punctuation aids pacing: use commas for natural breath points, periods for emphasis stops
- Contractions allowed and encouraged for natural spoken language
- Ambiguous terms must be disambiguated or avoided (e.g., "read" → "read (past tense)" in context)

#### Sentence and paragraph structure
- Sentences keep to 25–30 words average; avoid nesting or multiple dependent clauses
- Paragraphs are 2–4 sentences; serve as natural breathing points in audio
- Dialogue or quoted speech uses quotation marks (TTS respects these for intonation)
- No orphan abbreviations; define context on every mention

#### No unspoken markup
- No `[action]` or `[pause]` directives in the body
- No inline instructions for TTS (use script metadata for tone/speed guidance)
- No comment markers or editorial notes

### 1.3 Script metadata

Each script MUST include:
```
Title: (episode title from article or week)
Episode: (week identifier, e.g., 2026-W23)
Source URL: (article_url from request)
Source SHA256: (article_sha256 if provided; computed otherwise)
Generated: (ISO 8601 timestamp)
Generator: (tool chain identifier, e.g., "squad-podcaster v0.1")
---
[script body]
```

### 1.4 Editorial voice

Scripts MUST be:
- **Neutral and authoritative:** Suitable for a general technical audience
- **Conversational but not casual:** No slang, colloquialisms, or inside jokes
- **Accurate:** All claims verified against source article (checked by claim ledger)
- **Concise:** Omit elaboration not essential to the article's core message

---

## 2. Claim Ledger

### 2.1 Purpose

The claim ledger is an internal audit trail mapping every factual claim in the script back to its source in the article. It enables editors to spot unsupported inferences or accidental misrepresentation.

### 2.2 Format

One row per claim. Tab-separated or JSON lines for machine readability:

```
claim_id | script_excerpt | source_url | source_quote | verified | editor_notes
```

Or as JSON:
```json
{
  "claim_id": "script_001",
  "script_excerpt": "SquadScope published 23 articles last week.",
  "source_url": "https://squadscope.example/articles/2026-w23",
  "source_quote": "This week, SquadScope released 23 new articles.",
  "verified": true,
  "editor_notes": "Direct match; article count confirmed in metadata."
}
```

### 2.3 Completeness

- Every factual claim must have at least one ledger row
- Claims combining multiple sources must list all relevant source quotes
- Inferences or derived statements (e.g., "X is the third-highest instance of Y") must be marked `verified: false` until human review
- Editorial opinions (e.g., "This is an important development") are flagged separately with `claim_type: editorial`

### 2.4 Retention

Claim ledgers are kept in the publishing packet and in the manifest for audit purposes. They are not published.

---

## 3. Transcript Standards

### 3.1 Accessibility

Transcripts are public-facing and must be accessible:
- **Accuracy:** Verbatim to the audio, including umms, pauses, and speaker tone cues
- **Searchability:** Plain text or SRT format; metadata embedded as RFC 822 headers
- **Completeness:** Cover 100% of the audio output; no redactions

### 3.2 Format

Two formats are acceptable:

**Plain text with timestamps:**
```
[00:00:00] Welcome to the SquadScope Podcast. I'm your host.
[00:00:05] This week, we cover three major developments in cloud infrastructure.
```

**SRT (SubRip) format:**
```
1
00:00:00,000 --> 00:00:05,000
Welcome to the SquadScope Podcast. I'm your host.

2
00:00:05,000 --> 00:00:12,000
This week, we cover three major developments in cloud infrastructure.
```

### 3.3 Metadata

Transcripts must include a header section (before line 1 or before subtitle 1):
```
Title: SquadScope Podcast – Week 2026-W23
Episode: 2026-W23
Published: 2026-06-07
Source: https://squadscope.example/articles/2026-w23
Duration: 15:42
TTS Provider: [provider name, e.g., Azure Speech]
License: [e.g., CC-BY-4.0]
```

### 3.4 Speaker identification

If multiple speakers (host + guest):
```
[00:05:00] [HOST] Let me introduce our guest, Alex Chen from DevOps Weekly.
[00:05:10] [GUEST (Alex Chen)] Thanks for having me!
```

---

## 4. Show Notes Standards

### 4.1 Purpose

Show notes provide links, citations, and context for listeners to dive deeper. They are published alongside the audio and must be independently auditable.

### 4.2 Structure

```markdown
# SquadScope Podcast — Week 2026-W23

**Episode:** Week 2026-W23  
**Published:** 2026-06-07  
**Duration:** 15:42  
**Read by:** [TTS voice name if relevant]  

## Show notes

[Intro summary — 1–2 sentences on episode theme]

### Segment 1: [Topic Title]

- **Article:** [Title](https://example.com/article1) — Synopsis from source or episode  
- **Source:** SquadScope, 2026-06-07  
- **Timestamp:** 2:30–5:15  

### Segment 2: [Topic Title]

[Repeat structure]

## Quick links

- [SquadScope main site](https://squadscope.example)
- [Original article](https://squadscope.example/articles/2026-w23)

## Transcript

[Link to transcript or embedded below]

## License

These show notes and the podcast audio are available under CC-BY-4.0.
```

### 4.3 Citation requirements

Every external link MUST:
- Include the source domain (e.g., "DevOps Weekly" not just "article")
- Link to the actual resource referenced in the script (not to a landing page)
- Include a brief description (one sentence) of what listeners will find there
- Be verified as live before the review gate approval

### 4.4 Timestamps

- Optional but recommended: mark key sections with audio timestamps so listeners can jump to segments
- Format: `[MM:SS–MM:SS]` or `start at [MM:SS]`

---

## 5. TTS Provider Interface Expectations

### 5.1 Input contract

The TTS provider receives:
- **Script:** Plain-text UTF-8, meeting all section 1 requirements (TTS-ready copy)
- **Metadata:** Episode title, speaker/voice identity, requested tone (e.g., "neutral," "conversational")
- **Format request:** MP3 (primary); WAV optional for archival
- **Quality spec:** Audio codec, bitrate, sample rate (defined in TTS bakeoff evaluation)

### 5.2 Output contract

The TTS provider returns:
- **Audio file(s):** MP3 and/or WAV meeting codec/bitrate spec
- **Timestamp mapping:** Byte-accurate link between script text and audio time
- **Metadata:** Encoding details, duration, voice/model used, synthesis parameters
- **License certificate:** Documentation of voice rights, usage restrictions, attribution requirements

### 5.3 Failure modes

Expected failures and recovery:
- **Unsupported characters in script:** Provider rejects; error includes character and position; script must be re-prepared
- **Timeout on long scripts:** Provider returns partial output or queue status; caller retries with shorter segments
- **Voice/model unavailable:** Provider returns list of alternative voices; caller selects and resynthesizes
- **Rate limit:** Provider returns HTTP 429 with `Retry-After` header; caller backs off and retries

### 5.4 Validation before production use

Before marking a TTS provider ready for production:
- Synthesize a 5–10 minute test script in the target voice
- Verify output quality: no skipped words, no AI artifacts, natural pacing
- Confirm timestamps are within ±100ms accuracy
- Check metadata completeness and correctness
- Audit license/rights documentation for compliance with SquadScope distribution model

---

## 6. Review Gate Constraints

### 6.1 Mandatory checks before non-dry-run synthesis

**Before any non-dry-run TTS synthesis, the following MUST be verified:**

1. **Script accuracy**
   - [ ] All claims in the claim ledger marked `verified: true`
   - [ ] No unspoken markup or unresolved editorial notes in script body
   - [ ] Script reads naturally when spoken aloud (editor performs audio test)
   - [ ] All acronyms expanded; no orphan abbreviations

2. **Claim verification**
   - [ ] Spot-check at least 3 major claims against source article
   - [ ] Inferences or derived claims have editor sign-off with justification
   - [ ] No claims are contradicted by the source material

3. **Citation and link integrity**
   - [ ] All URLs in show notes resolve (HTTP 200 or 3xx with final target live)
   - [ ] Transcripts/show-notes metadata is complete (title, episode, source URL, date, duration)

4. **Transcript readiness** (if pre-recorded or applicable)
   - [ ] Transcript format matches one of the approved standards (plain-text + timestamps or SRT)
   - [ ] Speaker IDs and tone cues are correct
   - [ ] No profanity, slurs, or other sensitive content without explicit context flag

5. **TTS readiness**
   - [ ] Script has been validated by the selected TTS provider (dry-run synthesis or lint check)
   - [ ] No provider-specific unsupported characters remain in script
   - [ ] Provider license terms permit intended distribution (Spotify, web, archives, etc.)

6. **Dry-run validation** (if `dry_run: true` in request)
   - [ ] Manifest created with `status: dry_run`
   - [ ] Stub transcript and show notes generated (real content, not placeholders)
   - [ ] No audio synthesis performed
   - [ ] Response includes `warnings` if any non-fatal issues detected

### 6.2 Reviewer identity and audit trail

- Each review MUST record: reviewer name/ID, timestamp, status (approved/rejected), and change summary
- Rejections MUST include a reason tied to one or more mandatory checks above
- Approved reviews MUST be signed or cryptographically bound to prevent tampering
- Audit trail is retained in the manifest and publishing packet for legal/compliance purposes

### 6.3 Regeneration invalidation

If ANY of the following change after review approval, audio and transcript invalidation must be triggered:
- Script text (any word or punctuation change)
- TTS provider or voice selection
- Metadata (episode date, source URL, etc.)

Regeneration requires a new review cycle with `force: true` in the request.

---

## 7. Publishing Packet Contents

The publishing packet (ZIP file) contains all artifacts needed for manual publishing to Spotify, podcast hosts, or archives.

### 7.1 Required files

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

### 7.2 Manifest contents (MANIFEST.json)

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

### 7.3 Rights and attribution

The `RIGHTS-AND-ATTRIBUTION.txt` file MUST clearly state:
- TTS voice license and required attribution (e.g., "Generated with Microsoft Azure Speech Services")
- Original article copyright and license (e.g., "© SquadScope, CC-BY-4.0")
- Show notes license matching audio
- Distribution restrictions (if any)

---

## 8. Backward Compatibility

These standards define what must be *true* of artifacts, not how they are generated or stored. They do not alter the integration contract or API response shape. All artifact URLs returned by the API remain valid; only the standards for what those URLs point to are clarified here.

---

## 9. Validation and Compliance

### 9.1 Automated validation

Where possible, implement checks:
- **Script linting:** Detect unexpanded acronyms, URLs, unspoken markup using regex patterns
- **Claim ledger validation:** Ensure every claim_id is unique; verify source_url format
- **Link checker:** Dry-run HTTP HEAD requests on all show-notes URLs
- **TTS readiness scan:** Check for unsupported characters or rare Unicode that may fail TTS

### 9.2 Manual review

The human review gate (section 6) is mandatory for all production synthesis requests. Automated validation flags issues but does not bypass human judgment.

---

## 10. Evolution

These standards will be refined as:
- TTS providers are evaluated and chosen (see `backlog/tts-bakeoff.md`)
- Editorial feedback from the first episodes informs style tweaks
- Accessibility requirements or distribution partner requirements emerge

Changes to these standards MUST be approved by the editorial team and documented in `.squad/decisions.md`.
