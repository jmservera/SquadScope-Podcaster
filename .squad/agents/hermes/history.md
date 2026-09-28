# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Safety & Security during the Podcaster squad rebuild. Core rules: `PODCASTER_API_KEY` stored as GitHub/Azure secret, never logged; the API never echoes received keys; deploy uses GitHub OIDC; future storage access should prefer managed identity + short-lived SAS. See README "Secret handling" and `docs/architecture.md` "Security".
- 📌 Team update (2026-06-07): GitHub issue connect + triage. Assigned issues: #2 (1 total)

- 📌 Team update (2026-06-07T18:26:33.954+00:00): Security handoff review completed; API contract verified; secret handling confirmed; auto-sync requires SQUADSCOPE_SYNC_TOKEN — decided by Hermes

- 2026-06-07T19:07:49.816+00:00: **Local readiness & pre-release checklist complete.**
  - Created `docs/SECURITY.md`: comprehensive secret handling, logging policy, artifact staging/retention, human review gate requirements, TTS provider disclosure checklist, endpoint handoff procedure, Azure deployment prerequisites, and release checklist.
  - Created `docs/AZURE-DEPLOYMENT.md`: step-by-step runbook for OIDC setup, GitHub variables/secrets, first deployment, verification, SquadScope sync, troubleshooting, cost estimation, and security best practices.
  - Updated `backlog/blob-staging.md`: access control (managed identity ✓, SAS URLs 🔲), retention (7-day expiration), cleanup automation, artifact structure, sensitive data rules.
  - Updated `backlog/human-review-gate.md`: authentication via GitHub, audit trail (reviewer, timestamp, decision, notes), artifact regeneration & invalidation, secrets exclusion rules, implementation steps.
  - Updated `backlog/tts-bakeoff.md`: appended comprehensive security gate with credential handling, data privacy/compliance, SSML injection testing, integration security, failure modes, audit/logging, and sign-off requirement.
  - **Preserved:** `PODCASTER_API_KEY`, `PODCASTER_ENDPOINT`, `x-podcaster-api-key`, no-secret-logging expectations all documented and locked in.
  - **Key decision:** Before TTS integration, mandatory security review of data retention, SSML safety, error handling, and credential storage must be completed by Hermes.
  - No Python code modified; no Azure credentials used (runbook is setup-only, not execution).
  - All documents follow Hermes' uncompromising stance on secrets and are audit-ready.

- 2026-06-07T19:19:52.661+00:00: **Wave 2 security/secrets/observability review complete.**
  - Fixed duplicate code block in `podcaster/generation.py` (lines 464-475) that was preventing artifact generation.
  - Updated `generate_artifacts()` signature to accept optional `expires_at` parameter for consistent expiration across request/response/manifest.
  - Verified no API keys, headers, or secrets appear in logging, responses, or error messages.
  - Confirmed warnings properly identify stub/placeholder status: "audio is a deterministic placeholder pending TTS implementation".
  - Verified TTS claims (Azure Speech example) are correctly labeled as future/stub, not active features.
  - Validated least-privilege: Function App uses system-assigned managed identity with scoped `Storage Blob Data Contributor` role; no shared keys in code.
  - Reviewed observability metadata: request (week, URL, digest, sources), lifecycle (status transitions), correlation ID; no secrets in safe_log_fields.
  - Confirmed callback `secret_name` is logged as boolean flag only, never as actual secret.
  - Verified all documentation (SECURITY.md, AZURE-DEPLOYMENT.md, README.md, integration-contract.md) matches implementation.
  - All 19 tests pass; codebase is audit-ready for pre-release.

- 2026-06-07T19:31:49Z: **Wave 3 final polish complete.**
  - Fixed stale "future storage access" wording in `docs/architecture.md`; `AzureBlobStorageBackend` is already implemented via `DefaultAzureCredential`/managed identity when `PODCASTER_STORAGE_ACCOUNT_URL` is set.
  - Fixed misleading example commit message "Update TTS integration" in `docs/AZURE-DEPLOYMENT.md`; TTS is not implemented — changed to a generic "Update podcaster function" example.
  - No secret-leakage or deployment-blocking issues found in the Wave 2→3 diff.
  - All backward-compatible names preserved: `/api/generate`, `x-podcaster-api-key`, `PODCASTER_API_KEY`, `PODCASTER_ENDPOINT`, response keys.
  - Security/deployment wording is deployment-handoff ready.
📌 Team update (2026-06-07T19:49:59Z): Issue-first/PR workflow rule activated. PR #10 (wave-1-2-3-contract-pipeline-docs) closes #3, #8; progresses #1, #2, #6, #7. Inbox decisions merged (15 files). Post-merge tasks: #4 (TTS), #5 (Spotify) parallel, then #9 (CI) and #7 (deploy).

- 2026-06-07T20:24:55Z: Reviewed PR #11 (`fix/prod-deploy-environment`) deployment safety. The Azure deploy workflow is bound to GitHub environment `prod`, preserves `permissions: id-token: write`, uses `azure/login` with environment variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`, validates required prod variables/secrets by name only, and avoids printing `PODCASTER_API_KEY`/`SQUADSCOPE_SYNC_TOKEN` values. Documentation in README and `docs/AZURE-DEPLOYMENT.md` safely distinguishes non-secret variables from secrets.

📌 Team update (2026-06-07T20:24:55Z): prod-deploy-env decision merged to decisions.md — prod environment requires 7 vars and 1 secret (PODCASTER_API_KEY); deployment blocked until prod config complete

- 2026-06-07T20:52:01.950+00:00: Reviewed PR #11 deployment bootstrap/auth design for issue #7. Recommended current-release shared-key compatibility with safe generated-key bootstrap when `PODCASTER_API_KEY` is absent, deterministic optional Function App/Storage names, and explicit gated SquadScope sync. Determined a second Azure federated identity is not useful for GitHub secret sync, but is appropriate later for keyless SquadScope caller auth with subject `repo:jmservera/SquadScope:environment:prod`, audience `api://AzureADTokenExchange`, and no Azure management/storage roles. Gate: APPROVE WITH CONDITIONS; Bender's committed workflow now meets the key conditions, leaving live prod/OIDC deployment verification as the remaining release gate.

📌 Team update (2026-06-07T20:52:01Z): Security conditions on PR #11 auth bootstrap satisfied; Leela's naming fix ensures no half-baked Azure deployments. OIDC migration path documented for Phase 2. — decided by Leela

📌 Team update (2026-06-07T21:43:10): Bender durable Function package deployment decision merged (private blob, managed identity, no SAS/keys) — Bender

📌 Team update (2026-09-21T22:12:12.721+00:00): Expanded fallback-admission and owned-runner coverage across pre-finalization work was incorporated into final approved commit `3b25ce995b55b140c9fd452b843fe901a0d14310`. Validation passed; PR #682 remains unmerged with four new/unrelated unresolved threads.

- 2026-09-21T22:47:48.404+00:00: As independent revision owner for PR #682, corrected malformed, non-finite, and overflow recorder schema classification in commit `2e070fc4286643f542ec37b2efc1020514cdb250`, pushed it, and supplied recorder-thread evidence. Validation passed 60 recorder tests and 441 tests across the four-module suite; Fry approved the revised head.

📌 Team update (2026-09-22T07:47:54.369+00:00): PR #682 reached clean final head `cfb0bb925838cf909bc6842743f18fa2147b3d1c` with all checks and threads cleared. Safety lessons locked in: mutating provider calls with ambiguous remote outcomes fail closed against retry; budget/editor/job/recorder paths require a single explicit budget owner; clipset schema changes require rollout migration compatibility and explicit malformed-schema rejection; escaped descendants remain within shutdown/reaping containment.

📌 Team update (2026-09-22T14:52:36.169+00:00): Hermes' initial rejection for two metadata invalidation failures was resolved by Farnsworth's independent revision of `video_compose.py`; final approval followed with final validation preserved. PR #682 final commit `2ca66d4d33d8ff5efda11ea9e7e5c6ff1bd041b5` passed all hosted checks without merge, deploy, or provider mutation.

📌 Team update (2026-09-22T17:40:25.729+00:00): Hermes independently closed PR #684's final defects by preventing stale-claim poison exhaustion and fencing direct playlist ownership, pushing `c8a4922a78af09ebc6c28cc69d795f035793cbcd`. Fry approved; all 13 hosted checks are green, with no merge/deploy/provider mutation.

📌 Team update (2026-09-22T21:35:50.453+00:00): Hermes rejected PR #684 head `071a85a` for three precise stale-owner/test gaps, then approved corrected source `881a9fe` and tracking-only final tip `ac7bbdb32c762b32be7d4cd388ce42177c5a6f96`. Operator and provider-safety gates remain intact.

📌 Team update (2026-09-22T19:38:30.536+00:00): PR #684 delivery candidate reached f164977089d76905d957c32eb071777b8371da58 with exact-head acceptance, full validation green, and no merge, deploy, workflow, provider, or production mutation; scheduler repair and reconciliation retain independent bounded cursors. — decided by Bender

- 2026-09-23T15:12:00Z: Independently revised PR #686 at commit `f0532994029288e5f65813c6260bedb6189f26e3`. Restricted episode extraction to `data.webGetIndexedEpisodeList`, rejected arbitrary/duplicate episode-like containers, required the Spotify show ID without station-ID fallback, preserved bounded listing/overview retries and 403 unknown-state aborts, and gated the live-disconfirmed listing contract behind explicit `PODCASTER_SPOTIFY_RECONCILE=true` (explicit false remains the operator blind-create escape hatch). Added no-create/no-upload regressions and updated operator documentation. Validation: focused 11 passed; full `tests/test_publish.py` 321 passed; Ruff and `git diff --check` clean. Pushed to PR #686; exact-head CI triggered, with Squad CI successful and CI/CodeQL in progress at inspection time.

📌 Team update (2026-09-23T14:19:30.910+00:00): jmservera externally merged PR #686 at 2026-09-23T15:40:18Z as `5f31a7064568c323f3127042578f14c5e0693a31` after final head `a7cf44cec4f25ee43ca01a116843176144622368` passed all 13 exact-head checks. Fry's final verdict remained REJECT because duplicate episode identity across pages can hide a missing item and falsely prove absence; documentation also conflicts on cursor pagination and verification state. The remote branch was deleted and four review threads remain unresolved. — final review by Fry

📌 Team update (2026-09-23T18:53:26.430+00:00): Hermes produced stronger fail-closed correction `ed29d741` after PR #693 had already been externally squash-merged at its older head. The correction continued in PR #695, was tightened to guard before credential/provider access, and merged as `c52782e3c683647da1c1c1321e2a1f8c63141872`. — revised by Hermes and Leela; approved by Fry

📌 Team update (2026-09-23T18:19:22.037+00:00): Hermes confirmed refreshed PR #682 preserves fail-closed Spotify/provider listing and reconciliation safeguards from PR #689 while fixing null-budget fan-out ownership. No credentials, deployment, merge, or live provider mutation occurred.
