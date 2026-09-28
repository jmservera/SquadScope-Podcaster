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
- 2026-06-07T20:52:01.950+00:00: PR #11 QA re-review rejected on deployment naming risk. Local pytest passed after installing repo requirements in `.venv` (19 tests), compileall and diff whitespace checks passed, workflow run blocks shellcheck clean except ignored style/CI env warnings, GitHub recognizes deploy workflow, and PR CI/test is green. Live Azure/Bicep validation remains blocked locally because `az` and `bicep` are unavailable. Defect: workflow accepts/generates Function App names up to 60 chars, but `infra/main.bicep` derives `${functionAppName}-plan` and `${functionAppName}-law`; long valid Function App names can exceed App Service Plan (40) and Log Analytics (63) limits before deployment completes. Original author Bender should be locked out for this revision; assign a different implementation agent.
- 2026-06-07T20:52:01.950+00:00: PR #11 Leela naming revision QA approved with conditions. Local checks passed after recreating `.venv`: 19 pytest tests, compileall, and `git diff --check`; GitHub PR CI/test is green; deploy workflow YAML is recognized; workflow run-block shellcheck reports only style/env false-positive warnings. Edge snippets confirm Function App names cap at 35 chars, keeping derived `-plan` at 40 and `-law` at 39; 36-char and unsafe overrides fail before deploy. Required prod environment variables are present by name, but live Azure/OIDC deployment and endpoint smoke test remain unverified in this local gate.

📌 Team update (2026-06-07T20:52:01Z): PR #11 naming rejection fixed by Leela (Function App capped at 35 chars). Re-review approved with conditions; deployment smoke tests pending Azure subscription. — decided by Leela
- 2026-06-07T21:43:10Z: Final Issue #7 workflow/package QA review approved locally. `pytest` passed 24 tests, compileall clean, diff whitespace clean, package build simulation produced a valid app.zip, and `az bicep build` compiled `infra/main.bicep`. Workflow remains manual-only and uses private Blob run-from-package with managed identity; no deploy was triggered.

📌 Team update (2026-06-07T21:43:10): Bender durable Function package deployment decision merged (private blob, managed identity, 24 tests passing) — Bender

📌 Team update (2026-09-21T22:12:12.721+00:00): Rejection lockout held until the PR #682 durable lifecycle and timing defects were repaired. Final validation passed 1230 targeted tests (1 skipped, 2 deselected), 292 focused tests, Ruff check/format, compileall, and `git diff --check`; PR remains unmerged.

- 2026-09-21T22:47:48.404+00:00: PR #682 review rejected recorder schema overflow taxonomy and routed the locked revision to Hermes. Re-review approved current head `2e070fc4286643f542ec37b2efc1020514cdb250`: all four requested threads resolved, no new code findings, one unrelated tracking-status thread still unresolved, PR not merged.

📌 Team update (2026-09-22T10:39:16.805+00:00): Bender fixed four YouTube no-repeat/ambiguity review threads on PR #682 in commit `a58a49b79f6bb83a04fb574781b5630eb971df33`, added focused tests, pushed the change, and resolved the threads while preserving provider safety and contracts.

📌 Team update (2026-09-22T07:47:54.369+00:00): Final independent gate approved PR #682 head `cfb0bb925838cf909bc6842743f18fa2147b3d1c`: 3329 passed, 2 skipped, 2 deselected; 15/15 checks passed; 0 unresolved threads; OPEN, MERGEABLE, CLEAN, operator-only and not merged. Reviewer lockout remains strict: rejected authors do not self-revise; a different agent implements the correction and independent QA reclassifies it.

📌 Team update (2026-09-22T14:52:36.169+00:00): Final PR #682 regression gate passed with 1423 tests passed, 2 deselected, plus clean Ruff and diff checks. Final pushed commit is `2ca66d4d33d8ff5efda11ea9e7e5c6ff1bd041b5`; hosted checks all passed. PR remains unmerged and PR #684 was untouched.

📌 Team update (2026-09-22T17:40:25.729+00:00): Fry enforced PR #684 rejection lockouts across Bender (three findings), Leela (two additional findings), and Amy (two final fence defects), then approved Hermes' independent commit `c8a4922a78af09ebc6c28cc69d795f035793cbcd`. PR remains OPEN/DRAFT/CLEAN with 13 green hosted checks.

📌 Team update (2026-09-22T21:35:50.453+00:00): Fry added exact takeover regressions and completed PR #684 final validation: 8 affected tests, 439 relevant-module tests, Ruff check/format, compile, and diff checks passed for final SHA `ac7bbdb32c762b32be7d4cd388ce42177c5a6f96`.

📌 Team update (2026-09-22T19:38:30.536+00:00): PR #684 delivery candidate reached f164977089d76905d957c32eb071777b8371da58 with Hermes acceptance, full validation green, and no operational mutation; scheduler notification repair uses a separate bounded cursor from due reconciliation. — decided by Bender

📌 Team update (2026-09-23T14:19:30.910+00:00): jmservera externally merged PR #686 at 2026-09-23T15:40:18Z as `5f31a7064568c323f3127042578f14c5e0693a31` after final head `a7cf44cec4f25ee43ca01a116843176144622368` passed all 13 exact-head checks. Fry's final verdict remained REJECT because duplicate episode identity across pages can hide a missing item and falsely prove absence; documentation also conflicts on cursor pagination and verification state. The remote branch was deleted and four review threads remain unresolved. — final review by Fry

📌 Team update (2026-09-23T16:02:34.285+00:00): PR #689 Spotify video evidence guard repair reached pushed commit `aebf3b37ee5156aa389d2ca3decbc23b6bb1b1e9`; Bender's publish/video-runner tests and Ruff gates passed, Fry independently approved after 28 focused tests, and the target thread was resolved. PR remains open and unmerged with four checks still in progress.

📌 Team update (2026-09-23T18:33:01.615+00:00): Fry independently approved Bender's full PR #693 repair patch with no concerns after 523 targeted tests and clean Ruff/diff gates; Bender subsequently pushed `d37dde86270b2b67af3774f7e2a458c8f3182745` and resolved all five target threads. PR remains open and unmerged while checks finish.

📌 Team update (2026-09-23T18:53:26.430+00:00): Fry's PR #693/#695 review gates prevented ambiguous recovery after failed evidence writes and required the recovery guard before token acquisition or provider reads. Final commit `62880f57` passed 5 focused tests, 378 publish tests, Ruff, 12/12 CI, and all threads; PR #695 merged as `c52782e3c683647da1c1c1321e2a1f8c63141872`. — final review by Fry

📌 Team update (2026-09-23T18:19:22.037+00:00): Fry's read-only PR #682 QA required explicit parent-budget and convergence regressions for repeated null-budget fan-out attempts. Final exact-head validation passed 3492 unit tests (1 skipped, 2 deselected) and 11 integration tests, with zero unresolved non-outdated threads.
