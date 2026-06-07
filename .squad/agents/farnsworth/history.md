# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Script & Audio Editor during the Podcaster squad rebuild. Owns script/transcript/show-note standards and TTS-readiness. TTS provider selection is research-stage (`backlog/tts-bakeoff.md`); no audio in source control. Human review gate is mandatory before public publishing.
- 📌 Team update (2026-06-07): GitHub issue connect + triage. Assigned issues: #4 (1 total)
- **2026-06-07 (editorial artifact standards):** Documented comprehensive editorial standards covering:
  - **Script standards:** TTS-ready copy format (plain-text, expanded acronyms, natural pacing, 25–30-word sentences)
  - **Claim ledger:** Factual audit trail mapping script claims back to source article for editor verification
  - **Transcript standards:** Accessibility requirements (timestamps, speaker IDs, searchability, metadata)
  - **Show notes:** Citation requirements (live URLs, source attribution, timestamps)
  - **TTS provider interface:** Input/output contracts, failure modes, validation before production use
  - **TTS bakeoff criteria:** Quality (naturalness test), cost (annual per-episode), ops fit (SLA ≥99.5%, latency <10s, Python support), rights (commercial use permitted), resilience (fallback strategy)
  - **Review gate constraints:** 6 mandatory checks before non-dry-run synthesis (script accuracy, claim verification, citation integrity, transcript readiness, TTS readiness, dry-run validation); reviewer identity & audit trail; regeneration triggers
  - **Publishing packet:** ZIP structure with manifest, script, claim ledger, transcript, show notes, audio (MP3/WAV), rights docs, checksums
  - All standards are backward-compatible with the integration contract; SquadScope caller sees no API changes
  - Dry-run handling is explicit: `status: dry_run`, real content (not placeholders), no audio synthesis, warnings field for editorial feedback
- **2026-06-07 (editorial generation compliance review):** Reviewed deterministic local generation outputs (podcaster/generation.py) against editorial standards and brought into compliance:
  - **Script header:** Added formal metadata structure (Title, Episode, Source URL, Source SHA256, Generated timestamp, Generator version) per section 1.3; separates metadata from body with "---"
  - **Transcript format:** Added full metadata header (Title, Episode, Published, Source, Duration, TTS Provider, License); timestamps each line [HH:MM:SS] per section 3.2-3.3 for searchability and accessibility
  - **Show-notes structure:** Implemented complete markdown structure per section 4.2 including episode metadata, intro summary, segment sections with source attribution, quick links, transcript link, and license. Each segment includes article title/source/timestamp.
  - **Manifest (packet):** Updated to flat structure per section 7.2 with all required fields: job_id, generated_at, article metadata, review_status, tts_provider/voice (null for stub), duration_seconds, license, expires_at (7-day retention)
  - **Claim ledger:** Clarified stub entry is deterministic placeholder with explicit editor_notes indicating real claims will be populated during editorial generation; maintains JSON format and verified=false status per section 2
  - **Test alignment:** Updated test assertions to expect flat manifest structure (review_status not nested review.status) per editorial standards
  - Deterministic stub approach is sound for integration testing; metadata headers enable both human review and machine parsing
  - All 14 tests pass; backward-compatible with integration contract (section 8 of editorial-standards.md)
