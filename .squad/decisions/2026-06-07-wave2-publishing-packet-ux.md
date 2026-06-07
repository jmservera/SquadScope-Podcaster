# Decision: Wave 2 Publishing Packet Operator UX

**Date:** 2026-06-07T19:19:52.661Z  
**Author:** Amy (Distribution UX)  
**Status:** Approved for Wave 2  
**Scope:** Publishing packet usability for human operators

## Context

Wave 2 requires the publishing packet to guide human operators from download to publication without implying automation. Operators need metadata, platform guidance, and clear attribution templates without facing a blank manifest.json.

## Decision

Enhance the publishing packet to include:

### 1. **README.txt** (Operator Quick-Start Guide)
- **What it is:** Primary operator entry point  
- **Contains:**   - Packet contents inventory with file purposes   - Episode week, source article URL   - Review status and Wave 2 limitations   - Next steps checklist leading to PUBLISHING-GUIDE.txt- **Why:** Operators rarely read manifests; they start with README. Clear structure saves support tickets.

### 2. **PUBLISHING-GUIDE.txt** (Platform-Specific Steps)
- **What it is:** Copy-paste-friendly procedure guide  
- **Contains:**   - Spotify: Anchor vs. RSS submission paths   - Apple Podcasts: RSS requirements   - Google Podcasts: Indexing process (no direct upload)
   - Custom RSS: XML template with field mappings   - Verification checklist and archival procedure- **Why:** Operators use different platforms; generic "upload to podcast host" fails. Platform-specific examples reduce friction.

### 3. **Improved RIGHTS-AND-ATTRIBUTION.txt**
- **What it is:** Legal/licensing guidance  
- **Contains:**   - Wave 2 placeholder note (no real TTS yet)   - Source article attribution template (copy-paste ready)   - Show notes licensing instructions   - Distribution restrictions (authorized operators only)- **Why:** Operators must understand rights before publishing. Templates prevent license violations.

### 4. **Metadata Headers in Script/Show-Notes/Transcript**
- **What it is:** Machine-readable metadata + human context  
- **Contains:**   - Generated timestamp, job ID, source URL, SHA256   - Duration estimate, TTS provider placeholder   - Episode title, publication date   - Clear "Wave 2 stub" markers- **Why:** Operators need to spot check metadata quickly without parsing JSON; headers stay visible in text editors.

## Preserved Constraints

- **Link-only SquadScope:** Packet does not assume distribution URLs are public; SquadScope never embeds audio players.  
- **Manual workflow:** Operators download → review → publish. No automation, no callback URLs returned to SquadScope.  
- **Deterministic outputs:** Generated artifacts are identical for the same inputs; enables predictable testing and diff-based verification.  
- **No secrets:** API keys, credentials, or internal deployment info never appear in packets or manifests.  
- **Stable response shape:** Integration contract fields remain immutable; only additive changes allowed.

## Validation

- All 14 tests pass (7 jobs + 7 validation).  
- Packet structure is self-contained and extracts cleanly.  
- Operator README guides users through MANIFEST → script → show-notes → publishing platform without confusion.  
- CHECKSUMS.txt enables integrity verification post-download.

## Future Enhancements

- When TTS synthesis is live, replace "Wave 2 stub" markers with real voice name, provider, and license text.
- When Spotify/podcast-host automation research completes, add optional `"publish_to"` field to request body; update PUBLISHING-GUIDE to reference integration paths.
- Internationalization: Provide guide translations for non-English operators.

## Open Questions

- Should packet include a CHANGELOG.txt tracking prior versions? (Deferred to post-MVP.)  
- Should operators be able to request ZIP structure (nested folders vs. flat)? (Test in feedback; not required for Wave 2.)

## Related Files

- `podcaster/generation.py` — Implements packet, README, PUBLISHING-GUIDE, metadata headers.  
- `docs/distribution-ux.md` — Operator workflow narrative and integration contract summary.  
- `backlog/manual-publishing-packet.md` — Detailed packet structure reference.
