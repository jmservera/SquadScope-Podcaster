# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Platform/Backend during the Podcaster squad rebuild. Entry point is `function_app.py` (`/api/generate`), validation in `podcaster/validation.py`, infra in `infra/main.bicep`, deploy via `.github/workflows/deploy-azure.yml` using GitHub OIDC. Auth header is `x-podcaster-api-key`; never log it.
- 📌 Team update (2026-06-07): GitHub issue connect + triage. Assigned issues: #3, #7, #9 (3 total)

- 📌 Team update (2026-06-07T18:26:33.954+00:00): Security handoff review completed; API contract verified; secret handling confirmed; auto-sync requires SQUADSCOPE_SYNC_TOKEN — decided by Hermes
- 2026-06-07T19:07:49.816+00:00: Implemented first production-path pipeline locally: deterministic job lifecycle now stages script, transcript, show notes, audio placeholder, publishing packet, and manifest artifacts. Local dev falls back to filesystem storage; Azure path uses managed identity blob writes through storage account URL/container settings.
- 2026-06-07T19:19:52.661+00:00: Wave 2 local runtime pass kept `/api/generate` response keys stable while moving lifecycle/review/publishing/observability expansion into manifest and packet metadata. Deterministic local generation now avoids wall-clock ZIP metadata and records artifact content types/hashes; callback secret names are reduced to boolean metadata and not persisted.
- 2026-06-07T19:31:49.311+00:00: Wave 3 polish confirmed `expires_at` parity is complete across API response, staged manifest blob, and publishing packet MANIFEST.json. Removed sprint-internal "Wave 2 stub" language from operator-facing packet content; updated `docs/architecture.md` failure-handling note to reflect that sync `failed_response` is already wired (not future async work).
- 2026-06-07T19:49:59.902+00:00: Prepared Wave 1/2/3 PR branch from local commits 113b6c6 and 78813be. No duplicate open PR existed; local checks passed in a project virtualenv after installing existing requirements. Azure deployment was intentionally not run and remains blocked on subscription/access.
📌 Team update (2026-06-07T19:49:59Z): Issue-first/PR workflow rule activated. PR #10 (wave-1-2-3-contract-pipeline-docs) closes #3, #8; progresses #1, #2, #6, #7. Inbox decisions merged (15 files). Post-merge tasks: #4 (TTS), #5 (Spotify) parallel, then #9 (CI) and #7 (deploy).
- 2026-06-07T20:24:55.821+00:00: Updated deploy path to bind `.github/workflows/deploy-azure.yml` to GitHub environment `prod`, with OIDC vars (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`) read from environment variables and required config validated by name only. Deployment was not triggered because the visible `prod` environment config exposes Azure IDs as secret names and no required `prod` variables or `PODCASTER_API_KEY` secret names via `gh`.

📌 Team update (2026-06-07T20:24:55Z): prod-deploy-env decision merged to decisions.md — prod environment requires 7 vars and 1 secret (PODCASTER_API_KEY); deployment blocked until prod config complete
- 2026-06-07T20:52:01.950+00:00: Updated PR #11 deployment bootstrap for issue #7. Deploy workflow now derives deterministic Function App and Storage Account names when prod override variables are absent, validates Azure naming constraints, and no longer requires a pre-existing PODCASTER_API_KEY secret. If the key is absent, deployment generates a masked high-entropy key and can sync the resolved endpoint/key to SquadScope only when explicitly requested with SQUADSCOPE_SYNC_TOKEN. Local checks passed in .venv; actual Azure deployment remains unattempted pending live prod/OIDC execution.

📌 Team update (2026-06-07T20:52:01Z): PR #11 auth bootstrap approved with conditions; Leela resolved Fry's naming rejection (35-char cap on Function App names). Deployment ready pending Azure subscription. — decided by Hermes, Fry, Leela
- 2026-06-07T21:43:10.916+00:00: Issue #7 durable deploy path updated for PR: GitHub Actions now builds Python 3.11 dependencies into `.python_packages`, packages with Python stdlib ZIP logic, uploads `app.zip` to private `function-packages` storage using OIDC/Entra `--auth-mode login`, and configures `WEBSITE_RUN_FROM_PACKAGE` with managed-identity blob reads instead of unsupported config-zip/webapp deploy or package SAS URLs.

📌 Team update (2026-09-21T22:12:12.721+00:00): The six original PR #682 review repairs reached approved commit `3b25ce995b55b140c9fd452b843fe901a0d14310`; Leela fixed durable lifecycle CAS before manifest parsing, and Amy completed exact T+1500 error restoration, push, and thread resolution. PR #682 remains unmerged with four new/unrelated unresolved threads.

- 2026-09-21T22:47:48.404+00:00: Implemented four requested PR #682 findings in the isolated worktree, pushed commits `ee008e68abb0ca819631241fbffee2aa393d8297` and `2ae42d3ae1cdf6f4a86f7672bb9929352947f3bf`, and resolved all four target threads. Initial full validation passed 3215 tests (2 skipped, 2 deselected); recorder taxonomy required a later independent Hermes revision.

- 2026-09-22T09:47:59.819+00:00: Fixed PR #682 escaped-descendant shutdown/reaping in `podcaster/video/process.py`, added regression coverage, pushed `f548e43649a1158424ece22bab1e79ec8994173e`, and resolved thread `PRRT_kwDOSzuis86krITD`. Validation passed: 3 targeted tests, all 16 video-process tests, Ruff check, and Ruff format check. PR #684 was untouched; no blockers.


## 2026-09-22T09-57-58.635: PR #682 Terminal-State Persistence Defect — RESOLVED

**Outcome:** Remediated terminal-state persistence retry exhaustion defect in PR #682.
- Fixed: `podcaster/video/job_runner.py` — retry logic corrected
- Fixed: `tests/test_video_job_runner.py` — 154 tests pass, focused regression validated
- CI: Ruff checks passed, format checks passed
- Commit: `cbc3bb94f9f32089bdcff879a64444b2853aba46` pushed to `origin/squad/video-stage-budget-redesign`
- Status: Ready for operator review and merge

**Impact:** PR #682 now has no open review threads. Defect fixed. No safety gates weakened. No test skips.

📌 Team update (2026-09-22T10:39:16.805+00:00): Fry independently approved PR #682 commit `a58a49b79f6bb83a04fb574781b5630eb971df33` after the 183-test suite and Ruff checks passed with no blocking findings.

📌 Team update (2026-09-22T07:47:54.369+00:00): PR #682 remediation completed at remote head `cfb0bb925838cf909bc6842743f18fa2147b3d1c`; 15/15 checks passed, 0 review threads remained, and the full suite reported 3329 passed, 2 skipped, 2 deselected. The PR is OPEN, MERGEABLE, CLEAN, operator-only, and was not merged. Durable lessons: provider mutations with ambiguous outcomes must not be blindly retried; budget accounting needs one explicit owner; schema rollouts need migration-compatible reads plus malformed-input guards; rejected work stays under strict different-agent revision and independent re-review lockout.

📌 Team update (2026-09-22T14:52:36.169+00:00): Bender's YouTube ambiguity, exact-size, final-output, bounded-cleanup, and terminal-evidence repairs reached PR #682 final commit `2ca66d4d33d8ff5efda11ea9e7e5c6ff1bd041b5`. Farnsworth independently restored metadata invalidation; Hermes approved; all hosted checks passed. No merge/deploy/provider mutation occurred.

📌 Team update (2026-09-22T17:40:25.729+00:00): PR #684 initial provider fixes were pushed as `a8f4730`, then Fry rejected three findings and enforced Bender's reviewer lockout. Independent revisions ultimately reached approved commit `c8a4922a78af09ebc6c28cc69d795f035793cbcd`; PR remains draft and unmerged.

📌 Team update (2026-09-22T21:35:50.453+00:00): Bender implemented PR #684's initial durable notification-send and post-provider persistence fencing, then correctly yielded revision ownership after Hermes rejected head `071a85a` for three stale-owner/test gaps. Independent correction reached approved final SHA `ac7bbdb32c762b32be7d4cd388ce42177c5a6f96`.

📌 Team update (2026-09-22T19:38:30.536+00:00): PR #684 delivery candidate reached f164977089d76905d957c32eb071777b8371da58 with Hermes acceptance, full validation green, and no operational mutation; independent bounded scheduler repair and reconciliation cursors were merged into team decisions. — decided by Bender

📌 Team update (2026-09-23T14:19:30.910+00:00): jmservera externally merged PR #686 at 2026-09-23T15:40:18Z as `5f31a7064568c323f3127042578f14c5e0693a31` after final head `a7cf44cec4f25ee43ca01a116843176144622368` passed all 13 exact-head checks. Fry's final verdict remained REJECT because duplicate episode identity across pages can hide a missing item and falsely prove absence; documentation also conflicts on cursor pagination and verification state. The remote branch was deleted and four review threads remain unresolved. — final review by Fry

📌 Team update (2026-09-23T16:02:34.285+00:00): PR #689 Spotify video evidence guard repair reached pushed commit `aebf3b37ee5156aa389d2ca3decbc23b6bb1b1e9`; Bender's publish/video-runner tests and Ruff gates passed, Fry independently approved, and the target thread was resolved. PR remains open and unmerged with four checks still in progress.

📌 Team update (2026-09-23T18:33:01.615+00:00): PR #693's five Copilot review fixes were independently approved by Fry, then committed and pushed as `d37dde86270b2b67af3774f7e2a458c8f3182745`; exactly five target threads were resolved and 0 remain. PR stays open and unmerged while checks finish.

- 2026-09-23T19:00Z: Refreshed operator-only PR #682 from remote head `be6a2d27342d9211bafbc5f124d27abaeede7b54` by a non-rewriting merge of main `4118c6661e4a8ed7528fd7d8dc793b41dea781cc`. Semantically resolved conflicts in `podcaster/publish.py`, `podcaster/video/distribution.py`, `podcaster/video/job_runner.py`, and `tests/test_video_job_runner.py`, preserving both shared video budget/provider ambiguity controls and PR #689 Spotify orphan-draft evidence/strict reconciliation. Required an explicit parent `VideoStageBudget` for queued `record_via_fanout`; regression proves repeated null-budget attempts enqueue nothing and create neither clipset nor terminal manifest. Final pushed SHA `d427acee5064081343e4f179b698ea2565c47e70`; exact-head hosted checks all passed (3474 passed, 1 skipped, 2 deselected; integration 11 passed; lockfile/lint/infra/UI/images/security/CodeQL green), PR MERGEABLE/CLEAN, zero unresolved non-outdated threads. No merge/deploy.
📌 Team update (2026-09-23T18:53:26.430+00:00): PR #693's initial `ed8415f7` repair was superseded after Fry found a double-storage-write ambiguity gap. The fail-closed correction ultimately shipped through follow-up PR #695 and was squash-merged as `c52782e3c683647da1c1c1321e2a1f8c63141872`; future irreversible recovery must remain blocked unless durable evidence is established before credential/provider access. — reviewed by Fry; revised by Hermes and Leela

📌 Team update (2026-09-23T18:19:22.037+00:00): Bender refreshed operator-only PR #682 against advancing `main` twice, fixed null-budget fan-out by requiring the parent budget, preserved PR #689 Spotify/provider safety, and pushed final SHA `52d3bd2c93c635a0a7595535ddd0e994c8cfa32f`. All 16 exact-head checks passed; the PR is MERGEABLE/CLEAN and remains unmerged and undeployed.
