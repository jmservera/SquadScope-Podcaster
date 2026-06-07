# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as QA/Tester during the Podcaster squad rebuild. Tests live in `tests/`; run with `pytest`. Key paths to keep covered: 401 unauthorized, 400 validation/malformed JSON, 202 accepted stub. The deterministic response shape is a contract under test — guard it against drift.

- 📌 Team update (2026-06-07T18:26:33.954+00:00): Security handoff review completed; API contract verified; secret handling confirmed; auto-sync requires SQUADSCOPE_SYNC_TOKEN — decided by Hermes
- 2026-06-07: QA review expanded production-path coverage to lock `/api/generate` response keys across 202/401/400/500, malformed JSON handling, dry-run review metadata, packet ZIP contents/checksums, artifact staging, and practical no-secret response/artifact checks. Packet structure is now tested against the documented manual publishing contract.
- 2026-06-07: Wave 2 QA added regression coverage for deterministic generation outputs, local artifact staging path safety, job lifecycle/request metadata, manifest serialization, and staging observability logs. Current gate is blocked: `podcaster.jobs.run_generation_job` calls `generate_artifacts` with 4 positional args while `podcaster.generation.generate_artifacts` accepts 3, causing `/api/generate` to return 500 instead of the accepted response shape.
- 2026-06-07: Final Wave 2 QA re-gate after Bender/Farnsworth/Amy/Hermes fixes passed: 19 pytest tests and compileall succeeded. Earlier reject condition is resolved; local deterministic generation and `/api/generate` accepted-path compatibility are restored. Residual caveat remains non-Azure: Azure Blob/SAS behavior is not proven by local tests.
- 2026-06-07: Wave 3 final validation gate: all 19 pytest tests pass, compileall clean, git diff --check clean. `expires_at` parity verified in API response, manifest, and MANIFEST.json. RESPONSE_KEYS contract locked across all status-code paths; secret-leak assertions confirmed. APPROVED for Leela to commit.
📌 Team update (2026-06-07T19:49:59Z): Issue-first/PR workflow rule activated. PR #10 (wave-1-2-3-contract-pipeline-docs) closes #3, #8; progresses #1, #2, #6, #7. Inbox decisions merged (15 files). Post-merge tasks: #4 (TTS), #5 (Spotify) parallel, then #9 (CI) and #7 (deploy).
- 2026-06-07: PR #11 deploy-environment QA: local pytest passed (19), compileall and diff whitespace checks passed, and PR CI/test is green/clean. Deployment must not be attempted yet because GitHub environment `prod` has no required environment variables and lacks `PODCASTER_API_KEY`; Azure IDs currently appear as environment secrets, but the workflow reads them from `vars.*`.

📌 Team update (2026-06-07T20:24:55Z): prod-deploy-env decision merged to decisions.md — prod environment requires 7 vars and 1 secret (PODCASTER_API_KEY); deployment blocked until prod config complete
