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
