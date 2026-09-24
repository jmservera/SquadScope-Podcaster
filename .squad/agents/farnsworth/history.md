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
📌 Team update (2026-06-07T19:49:59Z): Issue-first/PR workflow rule activated. PR #10 (wave-1-2-3-contract-pipeline-docs) closes #3, #8; progresses #1, #2, #6, #7. Inbox decisions merged (15 files). Post-merge tasks: #4 (TTS), #5 (Spotify) parallel, then #9 (CI) and #7 (deploy).

📌 Team update (2026-09-21T22:12:12.721+00:00): Bounded terminal-manifest reads were incorporated into final approved commit `3b25ce995b55b140c9fd452b843fe901a0d14310`. The six requested PR #682 threads were resolved; four new/unrelated threads remain and the PR was not merged.

📌 Team update (2026-09-22T07:47:54.369+00:00): Production section-card drawtext now follows the selected ffmpeg binary consistently and shipped in final PR #682 head `cfb0bb925838cf909bc6842743f18fa2147b3d1c`. Final gate: 15/15 checks, 0 unresolved threads, full suite 3329 passed, 2 skipped, 2 deselected; PR remains operator-only and unmerged.

📌 Team update (2026-09-22T14:52:36.169+00:00): Independently revised the rejected `video_compose.py` artifact to restore metadata invalidation while preserving final-output validation. Hermes approved the revision; PR #682 final commit `2ca66d4d33d8ff5efda11ea9e7e5c6ff1bd041b5` passed all hosted checks and remains unmerged.

📌 Team update (2026-09-22T19:38:30.536+00:00): PR #684 delivery candidate reached f164977089d76905d957c32eb071777b8371da58 with real-provider-sink and ownership-boundary revisions included, Hermes acceptance, and all validation green; it remains draft and blocked by #682. — decided by Bender

- 📌 Team update (2026-09-23T15:42:00Z): Independently revised PR #686 on owner-verified contract base `612b5b8`, fixing issue #679 fail-closed reconciliation. Exact-title matching now rejects multiple reusable candidate IDs before any create/upload/process/metadata/publish mutation; GraphQL pagination now reconciles `totalItems`, `totalPages`, fixed page size, exact per-page contents, and stable cross-page counts. Updated `docs/spotify-video-upload.md` to distinguish the verified strict envelope from intentionally unsupported aliases/nesting/REST/state guesses. Pushed `a7cf44cec4f25ee43ca01a116843176144622368`; local `tests/test_publish.py` 334 passed, focused surface 119 passed, new regressions 7 passed, Ruff clean (183 files), diff check clean, exact-head hosted checks 12/12 successful. No review threads were manually resolved; Fry approval remains required.

📌 Team update (2026-09-23T14:19:30.910+00:00): jmservera externally merged PR #686 at 2026-09-23T15:40:18Z as `5f31a7064568c323f3127042578f14c5e0693a31` after final head `a7cf44cec4f25ee43ca01a116843176144622368` passed all 13 exact-head checks. Fry's final verdict remained REJECT because duplicate episode identity across pages can hide a missing item and falsely prove absence; documentation also conflicts on cursor pagination and verification state. The remote branch was deleted and four review threads remain unresolved. — final review by Fry
