### 2026-06-07T18:26:33.954+00:00: Security handoff review (hermes)

**By:** Hermes

**What:** 
- Auth header is `x-podcaster-api-key`
- Podcaster deploy secret/app setting is `PODCASTER_API_KEY`
- Optional cross-repo sync token is `SQUADSCOPE_SYNC_TOKEN`
- SquadScope receives variable `PODCASTER_ENDPOINT` and secret `PODCASTER_API_KEY`
- Reviewed function_app.py, podcaster/validation.py, deploy workflow, CI workflow, bicep infra, integration docs, README, and sample local settings
- No secret values were recorded in outputs
- No release-blocking secret echo path found in API responses or workflow summaries

**Why:**
- Ensure secrets are properly handled in deploy workflow and application code
- Verify cross-repo sync token requirements and fallback behavior
- Confirm no sensitive data is leaked in logs or responses
- Residual operational gate: auto-sync is optional and silently skips when SQUADSCOPE_SYNC_TOKEN is missing
- Handoff must verify SquadScope variable/secret presence before relying on automation

# Distribution UX Design Decisions

**Date:** 2026-06-07T19:07:49Z  
**Agent:** Amy (Distribution UX)  
**Status:** Ready for merge to `.squad/decisions.md`

## Summary

Defined the manual publishing packet structure, SquadScope integration UX (link-only), operator workflow, and research boundaries for future Spotify/podcast-host automation. All decisions maintain API contract stability and keep complexity off operators.

## Decisions

### 2026-06-07T19:49:59.902+00:00: User directive
**By:** jmservera (via Copilot)
**What:** Work must be tracked with GitHub issues and delivered through pull requests. PRs must close the relevant issue(s) using closing keywords such as `Closes #123` / `Fixes #123`. Do not treat work as complete unless the issue/PR relationship is clear. Going forward, do not continue with untracked direct-main work. Use issue-first planning, feature branches, and PRs.
**Why:** User request — captured for team memory


# Decision: Editorial Artifact Standards (2026-06-07)

**By:** Farnsworth (Script & Audio Editor)  
**Date:** 2026-06-07  
**Status:** Ready for team review and merge to `.squad/decisions.md`

---

## Decision

I have documented comprehensive local editorial artifact standards for the podcast production path. These standards define **what must be true** of scripts, transcripts, show notes, claim ledgers, and review processes—without prescribing how they are generated, stored, or deployed.

### Commit 113b6c6 (2026-06-07 18:31:25)
**Title:** `docs(ai-team): Security handoff merged; team updates propagated`

- Merged Hermes security handoff decision (auth header, secret handling, sync token requirements)
- Updated agent history files (Bender, Fry, Hermes)
- Progresses issue #2 (privacy/security pre-TTS gate)

### Commit 78813be (2026-06-07 19:42:21) — Main Wave 1/2/3 Increment
**Title:** `feat(podcaster): Wave 1/2/3 local increment — production pipeline, infra, docs, tests`

**Scope:**
- **Production pipeline** (`podcaster/jobs.py`, `podcaster/generation.py`, `podcaster/storage.py`):
  - Deterministic artifact generation (script, show notes, transcript, MANIFEST.json, ZIP packet)
  - Local + Azure Blob storage backends with `expires_at` parity
  - No secrets in response bodies
- **Infrastructure** (`infra/main.bicep`, `infra/main.parameters.example.json`):
  - Blob storage account, managed identity, App Insights templates
  - Ready for Azure deployment after subscription setup
- **Documentation** (all updated):
  - `docs/AZURE-DEPLOYMENT.md` — step-by-step deployment guide
  - `docs/SECURITY.md` — auth, secret handling, privacy, RAI disclosures
  - `docs/architecture.md` — system design
  - `docs/distribution-ux.md` — publishing packet UX, manual workflow
  - `docs/editorial-standards.md` — script review, AI disclosure, claims ledger
  - `docs/integration-contract.md` — SquadScope API shape (request/response/fields)
- **Tests:** 19 passing (pytest)
- **Squad files** (`.squad/agents/*/history.md`, decisions in inbox, skills/local-artifact-storage)
- **Backlog** (fleshed out): blob-staging, human-review-gate, manual-publishing-packet, tts-bakeoff, spotify-publishing-research

**Issues addressed (fully or partially):**
- ✅ #8 (Define SquadScope-to-Podcaster export contract) — integration-contract.md finalized
- ✅ #3 (Design Azure Blob temporary staging strategy) — design doc + infra ready
- ✅ #2 (Privacy/RAI gate) — SECURITY.md + editorial-standards.md; Hermes approved Wave 3
- ✅ #1 (Review gate) — architecture documented; UI backlog item remains
- ✅ #6 (Manual publishing packet) — implementation + ZIP generation
- 🔄 #7 (Deploy Azure Function) — code ready; deployment requires Azure subscription + secrets

---

## Part 3: Recommended PR Boundaries

### 2026-06-07T20:24:55.821+00:00: prod deployment environment (bender)

**By:** Bender

**What:**
- The Azure deployment workflow must use the GitHub environment named exactly `prod`
- Authenticate with `azure/login` using environment variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
- Required `prod` variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION`, `AZURE_RESOURCE_GROUP`, `AZURE_FUNCTION_APP_NAME`, `AZURE_STORAGE_ACCOUNT_NAME`
- Required `prod` secret: `PODCASTER_API_KEY`
- Optional `prod` secret: `SQUADSCOPE_SYNC_TOKEN`, required only when `sync_squadscope=true`

**Why:**
- Environment-scoped OIDC aligns with the existing Azure federated credential
- Keeps deploy configuration scoped away from repository-wide settings
- Validation reports missing variable/secret names only; it never prints secret values


### 2026-06-07T20:52:01.950+00:00: Deployment auth bootstrap - optional PODCASTER_API_KEY with automatic generation (consolidated)

**By:** Bender, Hermes

**What:**
- Keep the current shared `x-podcaster-api-key` contract
- Make the deploy path bootstrap-safe: `PODCASTER_API_KEY` is optional in the Podcaster `prod` environment
- If `PODCASTER_API_KEY` is absent, generate a 256-bit key, mask it immediately, set as Function App app setting, never print it
- If `PODCASTER_API_KEY` exists, deploy that stable secret as the Function App app setting
- Never print generated keys to logs, summaries, artifacts, or `.squad/` records
- Automated SquadScope sync is explicitly gated by `sync_squadscope=true` and `SQUADSCOPE_SYNC_TOKEN`
- Optional overrides: `AZURE_FUNCTION_APP_NAME` and `AZURE_STORAGE_ACCOUNT_NAME` with deterministic defaults validated against Azure naming rules

**Why:**
- Allows Azure deployment to succeed without pre-existing secret material while preserving SquadScope compatibility
- Prevents log-based manual copy/paste of generated keys (intentionally unrecoverable from logs)
- Avoids long-lived Azure credentials and keeps GitHub OIDC least-privileged for deployment
- Manual caller handoff requires stable pre-created secret (generated keys are unrecoverable)
- Azure OIDC cannot write GitHub secrets in another repository; sync requires GitHub-scoped credentials

**Future considerations:**
- A second Azure federated identity is not appropriate now for GitHub secret/variable sync
- Second federated identity is appropriate later if SquadScope caller auth migrates to OIDC
  - Requires dedicated Azure app registration or user-assigned managed identity for `jmservera/SquadScope`
  - Federated credential subject: `repo:jmservera/SquadScope:environment:prod`
  - Audience: `api://AzureADTokenExchange`
  - Permissions: only app role or Function/App Service auth audience needed to invoke `/api/generate`
  - Retain `x-podcaster-api-key` until SquadScope verifies OIDC token acquisition

**Gate:**
APPROVE WITH CONDITIONS: deployment may proceed after workflow uses optional deterministic names, never logs generated keys, and syncs resolved key rather than empty/missing GitHub secret.

# Fry PR #11 QA rejection: derived Azure names can exceed limits

- Date: 2026-06-07T20:52:01.950+00:00
- Reviewer: Fry
- PR: #11 (`fix/prod-deploy-environment`, commit `58ad887`)
- Verdict: REJECT — implementation revision required by an agent other than Bender.

## Finding

The deploy workflow validates and can generate `AZURE_FUNCTION_APP_NAME` values up to 60 characters, which is valid for `Microsoft.Web/sites`. However, `infra/main.bicep` derives sibling resource names from the Function App name:

- `hostingPlanName = '${functionAppName}-plan'`
- `logAnalyticsName = '${functionAppName}-law'`
- `appInsightsName = '${functionAppName}-appi'`

A valid 60-character Function App name therefore produces derived names of 64–65 characters. That can exceed Azure resource limits, especially App Service Plan (`Microsoft.Web/serverfarms`, 40 chars) and Log Analytics workspace (63 chars), so deployment can fail despite workflow validation passing. Fry reproduced the workflow naming edge case locally with a long resource group producing a 60-character default Function App name.

## Checks run

- `.venv/bin/python -m pytest -q` — 19 passed
- `.venv/bin/python -m compileall -q function_app.py podcaster tests` — passed
- `git diff --check` — passed
- Workflow run blocks through `shellcheck` — passed with CI env/style warnings ignored (`SC2154`, `SC2129`)
- `gh workflow view deploy-azure.yml --ref fix/prod-deploy-environment --yaml` — GitHub recognizes workflow YAML
- `gh pr checks 11` — CI/test successful
- Local Azure/Bicep validation — blocked: `az` and `bicep` unavailable

## Required revision

Have a non-Bender implementation agent update naming so every derived Azure resource has its own deterministic, Azure-valid length and character handling, or add explicit validated override parameters for constrained derived names. Add regression coverage or scripted validation for long/weird resource group names.



### 2026-06-07T20:52:01.950+00:00: Cap deploy Function App names for derived Azure resources
**By:** Leela
**What:** PR #11 deployment now treats `AZURE_FUNCTION_APP_NAME` as optional but validates any resolved value to 2–35 characters. The workflow default truncates the resource-group-derived prefix accordingly, and Bicep adds matching min/max decorators. Storage account override behavior remains optional and validated at 3–24 lowercase alphanumeric characters.
**Why:** Azure Function Apps can be longer, but this template derives the App Service Plan and Log Analytics workspace by appending suffixes. Capping the source name keeps `${functionAppName}-plan` and `${functionAppName}-law` within Azure resource-name limits before live deployment, preserving the stable SquadScope response contract and avoiding half-baked deploy failures.
# Durable Function package deployment

- Date: 2026-06-07
- Owner: Bender
- Context: Azure rejected `az functionapp deployment source config-zip` and `az webapp deploy --type zip` for this Function App environment, while manual private blob run-from-package deployment worked.
- Decision: `deploy-azure.yml` now builds Python 3.11 dependencies on the runner, packages the Function App locally, uploads the ZIP to a private `function-packages` blob container with OIDC/Entra auth, sets `WEBSITE_RUN_FROM_PACKAGE` to that private blob URL, enables managed-identity package reads, and restarts the app.
- Security: No Storage Account keys or package SAS URLs are used. The deploy service principal is assigned Storage Blob Data Contributor on the deployed Storage Account for package upload. The Function App's managed identity reads the private package blob.

# Artifact access uses private operator paths

## Decision

Podcaster generated artifact URLs use the `private_operator_path` access model for the initial release. Response URLs are storage/local locator paths only; they must not include SAS tokens, URL credentials, query strings, or fragments.

## Rationale

Placeholder podcast artifacts are not publishable output, and public/SAS access is unnecessary before human review, real TTS, and publication gates exist. Keeping returned URLs private avoids accidental public exposure while still giving operators stable artifact locators for validation.

## Impact

- Operators need local filesystem access in development or explicitly granted Azure Storage permissions in deployed environments.
- Job manifests and publishing packets include `artifact_access` metadata for URL policy, expiry, cleanup ownership, audit correlation, and publication blockers.
- Cleanup is driven by `expires_at`/`cleanup_after` and owned by an operator or storage lifecycle policy.
- Future SAS or brokered access requires an explicit follow-up decision and tests.

# Decision: source_artifacts accepts string and object references

## Context

SquadScope emits `source_artifacts` as object references, while the original Podcaster validation accepted only `array[string]`.

## Decision

Keep the `/api/generate` v1 request backward compatible: `source_artifacts` accepts both legacy string references and SquadScope object references in the same array. Object references must include at least one stable reference field: `path`, `url`, `href`, `uri`, or `name`.

## Consequences

- Existing callers using `array[string]` continue to work.
- SquadScope object references pass validation without requiring a schema-version fork.
- Unknown object fields remain rejected so future contract drift is visible in tests.
- The top-level `/api/generate` response shape remains unchanged.

### 2026-06-13: Branch rulesets directive (moved to decisions.md)
Archived here as cross-reference only. Active directive lives in decisions.md.

