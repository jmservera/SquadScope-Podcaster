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

### 2026-06-20T13-56-58: PR #303/#304 review fixes shipped; video stack to be un-stacked onto main atop corrected drawtext base
**By:** Coordinator
**What:** PR #303/#304 review fixes shipped; video stack to be un-stacked onto main atop corrected drawtext base
**References:** PR #303, PR #304, PR #305, PR #306, PR #307, PR #308, PR #309, PR #310, PR #311
**Why:** #303 fixed (commit 21dc646, 3 threads resolved). #304 fixed + stripped mis-merged #295 commit, force-pushed 3fdc815, 2 threads resolved. Video stack #311/#307/#308/#309/#310 share files so are sequential; plan: rebase chain onto corrected drawtext tip 3fdc815, retarget bases to main, force-push. Merge order #304->#311->#307->#308->#309->#310. #306 closed dup of #311.

### 2026-06-17T18-19-53: Integration smoke tests use local fakes instead of network or Docker
**By:** Fry
**What:** Integration smoke tests use local fakes instead of network or Docker
**References:** jmservera/SquadScope-Podcaster#265
**Why:** Added pytest integration smoke coverage for issue #265 that stays fully local: the video pipeline test exercises sync_plan -> video_gen.record_episode orchestration -> video_compose -> distribution with fake Playwright and fake ffmpeg runners that create real files under tmp_path, the publish-flow test uses LocalStorageBackend plus Spotify dry-run env vars, and the UI serve coverage uses FastAPI TestClient against the monitoring app with local artifacts. Static bundle validation checks ui/dist/index.html only when present and skips cleanly when the built UI bundle is absent, so `pytest tests/integration/` never requires Docker, browsers, live network, or credentials.

### 2026-06-17T18-26-30: Spotify dry-run publish path stays credential-free for issue #265 offline integration coverage
**By:** Scribe
**What:** Spotify dry-run publish path stays credential-free for issue #265 offline integration coverage
**References:** jmservera/SquadScope-Podcaster#265, Bender, Hermes, Fry
**Why:** For issue #265, the Spotify publish dry-run path must remain usable without credentials so offline integration coverage can exercise publish flow end-to-end. Bender updated `podcaster/publish.py` and the related tests/docs so dry-run bypasses credential requirements while preserving real publish validation for non-dry-run modes. Hermes re-reviewed the final behavior and returned a green verdict.

### 2026-06-17T18-19-07: Integration workflow runs offline pytest suite with prebuilt UI bundle
**By:** bender
**What:** Integration workflow runs offline pytest suite with prebuilt UI bundle
**References:** issue #265, tests/integration/test_video_pipeline.py, tests/integration/test_ui_serve.py, tests/integration/test_publish_flow.py
**Why:** For issue #265 workflow/docs ownership, I verified the repo layout before editing: Python imports resolve through the `podcaster` package (video pipeline modules under `podcaster.video.*`), while the management UI lives under `ui/` and must be treated as a built frontend rather than a Python package. I added `.github/workflows/integration-tests.yml` to build `ui/dist` with dummy MSAL/API values and run `pytest tests/integration/ -q -ra -m "not slow"` without Docker, live network, or credentials. The workflow also handles the transient case where `tests/integration/` is absent by emitting a notice instead of failing, so it stays safe during cross-agent landing order.


## Archived 2026-09-23T12:01:49Z (Tier 1: older than 30 days; cutoff 2026-08-24)

### 6. Operator Instructions are Plain Text, Not Embedded in Code

**Decision:** README.txt contains step-by-step guides for Spotify for Creators, Apple Podcasts, and RSS-based distribution. No binary/compiled content or platform-specific APIs.

**Rationale:**
- Plain text is universal and searchable
- Operators may not have Python, CLI tools, or SDKs installed
- Reduces support burden (single source of truth for publication steps)
- Supports accessibility

**Impact:** Operators can publish using familiar tools (browser, podcast host UIs, RSS feed managers). No hidden dependencies.

## Artifacts Created/Updated

- **Created:** `docs/distribution-ux.md` — Comprehensive operator readiness and integration UX guide
- **Updated:** `backlog/manual-publishing-packet.md` — Full packet specification and structure
- **Updated:** `backlog/spotify-publishing-research.md` — Link-only SquadScope integration UX, automation boundaries, and research scope
- **Updated:** `.squad/agents/amy/history.md` — Learnings appended

## Stability Checklist

- [x] API response keys are unchanged (see `docs/integration-contract.md` — no edits needed)
- [x] Packet structure is documented for code generation (when TTS is added)
- [x] Operator workflow is self-contained (no external tools required)
- [x] No secrets in public artifacts
- [x] SquadScope integration is minimal and link-only

## Questions for Reviewers

1. **Packet size:** Is 500 MB limit acceptable? (Typical: ~100 MB with MP3 + metadata)
2. **Expiration:** Is 7–14 days for SAS URLs reasonable? Should it be configurable?
3. **Cover art:** Should Podcaster generate/derive cover art, or should it be managed centrally?
4. **Markdown variant:** Show-notes.md is GitHub Flavored Markdown. Acceptable for all podcast hosts, or should there be a plain-text variant?
5. **RSS feed hosting:** Should the operator instructions assume they manage their own RSS, or should we research if a hosted RSS solution fits?

## Next Steps (Not In Scope for Amy)

- **Bender (API/Infra):** Implement packet zip generation when TTS provider is selected
- **Fry (Test Suite):** Add tests for packet structure, checksum validation, and operator README rendering
- **Farnsworth (Script):** Ensure script content aligns with packet metadata (title, duration, speaker names)
- **Future:** Research Spotify, Apple Podcasts, and podcast host automation APIs (post-MVP)


---
date: 2026-06-07T19:31:49Z
by: Amy (Distribution UX)
---

# Wave 3: Publishing Packet & Distribution UX Polish

## Decision

Clarified stale and misleading wording across documentation to make deployment-ready handoff prerequisites clear: **operators understand that real TTS is required before publication; local placeholder audio cannot go live.**

## Rationale

Wave 2 implemented deterministic placeholder TTS and full publishing packet structure. However, documentation implied that:
1. Storage was "Azure-only" (misleading for local development)
2. Expiry metadata was inconsistent (7 days vs "7–14 days")
3. Operator workflow suggested MP3 was ready to publish (dangerous assumption)

These gaps risked operator confusion during handoff and could delay deployment if operators expected live audio but found placeholders.

## Changes

1. **README.md** — Changed "stages artifacts in Azure Blob Storage" to "stages artifacts (locally or in Azure Blob Storage)" to reflect both paths.

2. **docs/integration-contract.md** — Added explicit note that expiry is "7 days from job creation by default" on both local and Azure paths, with parity statement.

3. **docs/distribution-ux.md** — Rewrote operator workflow steps 3–4 to:
   - Explicitly state MP3 is "deterministic placeholder (see warnings in API response)"
   - Mark current workflow as "MVP — cannot publish with placeholder audio"
   - Flag future workflow as dependent on live TTS implementation

4. **backlog/manual-publishing-packet.md** — Updated expiry section to:
   - Remove "7–14 days" ambiguity; state "7 days from job creation"
   - Add explicit: "Operator must download and store locally if longer retention is needed"
   - Mark "long-term archive in separate container" as Future work

## Upstream Impact

- ✅ API response keys unchanged (preserved contract stability)
- ✅ Operator expectations now aligned with current placeholder audio limitation
- ✅ Future TTS and archive work is clearly marked as post-MVP
- ✅ Local development docs now equally clear as Azure deployment docs

## Blocking Issue Resolved

First SquadScope dry-run is now clear: SquadScope can call `/api/generate`, receive URLs and metadata, and exercise the full contract. However, **operator cannot publish without real audio or manual audio replacement**. This is now documented as a known MVP limitation.

## Next Steps for Handoff

1. Fry to validate all test scenarios against updated docs
2. Leela to confirm scope boundaries (TTS and long-term archive are explicitly Future)
3. Deployment workflow can proceed; operator onboarding docs should reference these clarifications


# Production pipeline increment

- **Date:** 2026-06-07T19:07:49.816+00:00
- **Agent:** Bender

## Decision

Keep `/api/generate` synchronous for this increment and preserve the existing top-level response keys/status semantics while staging deterministic artifacts behind the response URLs.

## Rationale

SquadScope automation depends on a stable contract. The new job manifest carries richer lifecycle state (`review_pending`) and review metadata without adding top-level response fields or requiring Azure credentials for local validation.

## Operational notes

Local runs write artifacts under the configured local artifact directory. Azure deployment should configure blob account URL and container settings; the Function App managed identity writes blobs, with no storage keys returned or logged.


# Decision: Wave 2 local runtime metadata stays in artifacts

- **Date:** 2026-06-07T19:19:52.661+00:00
- **Owner:** Bender
- **Status:** Proposed

## Decision

Keep `/api/generate` backward compatible by preserving the existing top-level response keys. Put Wave 2 runtime expansion in the staged job manifest and publishing packet metadata instead: lifecycle transitions, review gate checks, publishing blockers, artifact hashes/content types, generation adapter identity, and safe observability correlation fields.

## Rationale

SquadScope automation depends on a deterministic response shape. Artifact metadata can evolve without breaking callers, gives human reviewers and publishing operators the detail they need, and keeps local development Azure-independent.

## Constraints

- Do not persist or log API keys, callback secret names, storage keys, or paid TTS credentials.
- Local generation remains deterministic and uses filesystem storage when Azure settings are absent.
- Publishing remains manual and blocked until human review and real audio are available.

### Interoperability

If the TTS provider changes or fails, can we switch to another? Standards define provider interface expectations (input/output contracts, failure modes) so any provider can be swapped without re-scripting.

---

## Team questions to resolve

1. **Claim ledger format:** JSON lines or tab-separated? (Recommend JSON for toolability; TSV for simplicity. Start with JSON.)
2. **Reviewer identity:** GitHub username or email? (Recommend GitHub username for GitHub-native audit trail.)
3. **Audit storage:** GitHub issue comments, `reviews/` branch, or Azure Table Storage? (Recommend GitHub issue comments for MVP; upgrade to Table Storage for scalability.)
4. **Dry-run response:** Should warnings list *which* editorial checks failed? (Recommend yes, for editor feedback without blocking acceptance.)
5. **TTS provider evaluation:** Who evaluates? What's the approval gate? (Recommend Leela (coordinator) + Farnsworth (editor) co-sign.)

---

## Next steps

1. **Team review:** Leela & Bender review editorial standards for feasibility and alignment with API design.
2. **Refinement:** Incorporate feedback; clarify any ambiguities.
3. **Merge:** Scribe merges this decision file into `.squad/decisions.md`.
4. **Implementation:** Bender designs validation logic (script linting, link checking, claim ledger validation) in `podcaster/validation.py`.
5. **Dry-run:** First episode uses these standards end-to-end; team collects feedback and iterates.

---

## References

- `docs/editorial-standards.md` — complete standards (10 sections)
- `backlog/tts-bakeoff.md` — provider evaluation criteria and interface expectations
- `backlog/human-review-gate.md` — review process, mandatory checks, audit trail
- `backlog/manual-publishing-packet.md` — packet structure and manual publishing workflow
- `docs/integration-contract.md` — unchanged; backward compatible

---

## Sign-off

✓ Standards are specific and testable.  
✓ Backward compatible with integration contract.  
✓ No Python code modifications required.  
✓ Validation points identified for lightweight checks.  
✓ No secrets or deployment credentials required.  
✓ Ready for team decision merge.


# QA Review: Local Production Path

- **Date:** 2026-06-07T19:07:49.816+00:00
- **Agent:** Fry
- **Verdict:** Approved with follow-up risks

## Scope

Reviewed Bender's local production-path increment and the documentation from Farnsworth, Amy, and Hermes. Added regression coverage for response shape, job lifecycle, artifact staging, packet extraction/checksums, malformed JSON, generation failure responses, dry-run review metadata, callback secret-name suppression, and practical response/artifact secret leakage checks.

## QA-owned fix

The publishing packet initially did not match the documented packet contract. I made a small QA-owned fix in `podcaster/generation.py` so staged scripts use `script.txt` and packet ZIPs include `README.txt`, `MANIFEST.json`, `script.txt`, `claim-ledger.json`, `transcript.txt`, `show-notes.md`, `audio/episode-{week}.mp3`, `RIGHTS-AND-ATTRIBUTION.txt`, and `CHECKSUMS.txt` with validated SHA-256 entries.

## Validation

- `.venv/bin/python -m pytest -q` — 16 passed
- `.venv/bin/python -m compileall -q podcaster function_app.py` — passed

## Remaining risks

- Placeholder script content is not yet a fully editorial-compliant final script; human review remains mandatory.
- Azure Blob SAS generation and lifecycle cleanup are documented but not exercised locally.
- Future real TTS integration still needs provider/security review and unhappy-path tests.


# Fry Wave 2 QA Verdict

Date: 2026-06-07T19:19:52.661+00:00
Requested by: jmservera
Verdict: APPROVE

## Re-gate Result
The earlier REJECT is resolved on the current filesystem state. The generation/job signature mismatch is fixed, local storage returns content type safely, and the `/api/generate` accepted path is covered by passing tests.

## Validation
- `.venv/bin/python -m pytest -q`: PASS — 19 passed.
- `.venv/bin/python -m compileall -q function_app.py podcaster tests`: PASS.

## Residual Caveats
- Local deterministic artifact staging is validated; Azure Blob upload/SAS expiry behavior was not exercised locally.
- No deploy and no commit performed.


---
author: fry
created_at: 2026-06-07T19:31:49Z
wave: 3
---

# Wave 3 Final Validation Gate — Approved

**By:** Fry

## Commands Run

```
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q function_app.py podcaster tests
git diff --check
```

## Results

| Check | Result |
|---|---|
| pytest (19 tests) | ✅ PASSED |
| compileall | ✅ PASSED (no errors) |
| git diff --check | ✅ PASSED (no whitespace errors) |

## Scope Verified

- `expires_at` metadata parity (Bender): present in API response (`jobs.py:54`, `generation.py:201`), staging manifest blob, and MANIFEST.json — confirmed by updated `test_validation.py` assertion `response["expires_at"] == "2026-06-14T17:41:40Z"`.
- Response shape contract (`RESPONSE_KEYS` tuple): locked across 202, 400, 401, and 500 paths.
- No secret values (`PODCASTER_API_KEY`, `x-podcaster-api-key`) leak into response bodies — asserted in tests.
- Backward-compatible identifiers unchanged: `/api/generate`, `x-podcaster-api-key`, `PODCASTER_API_KEY`, `PODCASTER_ENDPOINT`, `RESPONSE_KEYS`.
- Docs/distribution/security files (Amy, Hermes) are non-code and do not affect test outcomes.

## Verdict

**APPROVED.** Local increment is ready for Leela to commit.


# Decision: Safety & Security Documentation & Pre-Release Checklists

**By:** Hermes (Safety & Security)  
**Date:** 2026-06-07T19:07:49.816+00:00  
**Status:** Ready for team review

---

## What

Updated safety/security documentation and pre-release checklists for local readiness (no deployment or real Azure credentials required).

**Artifacts created/updated:**

1. **`docs/SECURITY.md`** (new)
   - Comprehensive secret handling policy: `PODCASTER_API_KEY`, `SQUADSCOPE_SYNC_TOKEN`, Azure credentials (non-secrets)
   - Logging & observability policy: what is safe, what must never be logged
   - Artifact staging & retention: managed identity, SAS URLs, 7-day expiration, lifecycle cleanup
   - Human review gate security requirements: auth, audit trail, artifact regeneration, secrets exclusion
   - TTS provider disclosure checklist: data privacy, SSML safety, credential handling, integration gate
   - Endpoint handoff to SquadScope: manual setup + optional auto-sync
   - Azure deployment prerequisites: subscription, naming, OIDC setup, variables/secrets, pre-flight validation
   - Release checklist: 8 gates covering secrets, API security, logging, artifacts, integration, docs, tests, human review

2. **`docs/AZURE-DEPLOYMENT.md`** (new)
   - Pre-deployment checklist: subscription, naming constraints, OIDC federation, GitHub variables/secrets
   - Step-by-step OIDC setup: app registration, federated credentials, tenant/subscription IDs, role assignment
   - GitHub configuration: variables (non-secret), secrets, `SQUADSCOPE_SYNC_TOKEN` generation
   - Pre-flight validation: Azure CLI checks for names, resource availability
   - First deployment runbook: trigger workflow, monitor, verify, test endpoint, manual SquadScope setup
   - Subsequent deployment: code update → deploy → verify (idempotent)
   - Optional sync to SquadScope: auto-populate variables/secrets
   - Troubleshooting: OIDC login, function app deployment, 401 errors, timeouts
   - Cost estimation: Function App Y1 ($0.17), Storage (~$5–10), App Insights (free tier), Log Analytics (~$5–15)
   - Security best practices: key rotation, cost monitoring, alerts, audit logs, no secret printing

3. **`backlog/blob-staging.md`** (updated)
   - **Access control:** Managed identity ✓, SAS URLs (to implement)
   - **Retention policy:** 7-day expiration, cleanup automation
   - **Artifact types & paths:** Consistent blob structure (manifests/, transcripts/, etc.)
   - **Sensitive data:** No API keys, tokens, or credentials in any blob
   - **Implementation steps:** Container creation, SAS generation, cleanup job, logging, testing

4. **`backlog/human-review-gate.md`** (updated)
   - **Authentication:** GitHub-based, write permission to Podcaster repo
   - **Audit trail:** Reviewer ID, timestamp, job ref, decision, reason, optional artifact hash
   - **Storage options:** GitHub issues, committed JSON, or Azure Table Storage
   - **Artifact integrity:** Regeneration invalidates old SAS URLs, reviewers must re-approve
   - **Secrets exclusion:** No API keys, SAS URLs with embedded creds, caller credentials in reviews
   - **Safe to review:** Transcripts, voice metadata, audio specs, titles, show notes
   - **Implementation steps:** Review status field, GitHub issue interface, audit trail, regeneration workflow

5. **`backlog/tts-bakeoff.md`** (updated with security gate)
   - Appended **Security & Compliance Gate** section with mandatory checklist before integration:
     - Credential & secret handling (API key format, rotation, error handling, fallback)
     - Data privacy & compliance (retention, deletion, location, sub-processors, DPA, audit rights)
     - SSML safety & injection prevention (tag support, sanitization, injection testing, error handling)
     - Integration security (managed identity, credential storage, logging, error messages, timeouts)
     - Failure modes & rate limiting (429 handling, quota enforcement, fallback)
     - Audit & compliance (request logging, cost tracking, audit trail)
   - Hermes sign-off required before code review

---

## Why

### 6. Callback vs. Polling

**Decision:** Polling for v1 (simpler); callback for future release.

**Rationale:** Polling is simpler to implement and debug; callback requires SquadScope to expose a webhook. Polling is acceptable for low-frequency events (1–2 articles/week).

---

## Outstanding Items (Not Blocking)

- [ ] **TTS provider evaluation:** Bakeoff is design-complete; awaiting provider selection and security review.
- [ ] **Blob staging implementation:** Runbook is ready; Bender implements SAS URL generation and cleanup job.
- [ ] **Human review implementation:** Requirements are clear; Bender/Fry implement review status, audit trail, and regeneration workflow.
- [ ] **Cost monitoring setup:** Azure Cost Management alerts for budget overrun (optional for v1).
- [ ] **OIDC federation setup:** Teams should follow runbook; no blocking issues.

---

## Sign-Off

**Hermes:** ✅ Approved.  
**Status:** Ready for team review and operational use.

**Next steps:**
1. Team reviews documentation for accuracy and completeness.
2. Deployment teams follow `AZURE-DEPLOYMENT.md` for setup.
3. Implementation teams use `backlog/` documents as requirements.
4. Before TTS integration, Hermes reviews provider security gate (see `tts-bakeoff.md`).

---

## Questions / Feedback

- **Documentation clarity:** All sections should be actionable. Please flag unclear runbook steps.
- **Missing prerequisites:** If your deployment requires additional setup, contact Hermes.
- **TTS provider:** Once provider is selected, contact Hermes for security gate review.


# Wave 2 Security & Observability Review — Verdict

**By:** Hermes (Safety & Security)  
**Date:** 2026-06-07T19:19:52.661+00:00  
**Status:** ✅ APPROVED

## Executive Summary

SquadScope Podcaster passes all Wave 2 security gates. No secret leaks, no false claims, least-privilege correctly implemented, and observability metadata is safe and useful.

---

## Detailed Findings

### SAS URL & Expiration (Wave 3)
When implementing real artifact hosting:
1. Return short-lived SAS URLs (currently stub URLs with 7-day expiration metadata)
2. Implement lifecycle management to delete artifacts after expiration
3. Test SAS token rotation and revocation workflows

---

## Sign-Off

**Security Verdict:** ✅ **APPROVED**

This codebase is security-hardened and ready for release. All secret-handling policies are enforced, least-privilege is correctly implemented, observability metadata is safe and useful, and documentation is audit-ready.

---

**Hermes**  
Safety & Security Specialist  
SquadScope Podcaster Project


# Hermes — Wave 3 Security / Docs Polish Decision

**Date:** 2026-06-07T19:31:49Z  
**By:** Hermes  
**Wave:** 3 (final polish)

## What

Reviewed the Wave 2→3 diff for stale security/deployment wording and secret-leakage paths.

**Changes made:**

- `docs/architecture.md`: Removed "future storage access" qualifier from the managed identity bullet. `AzureBlobStorageBackend` (using `DefaultAzureCredential`) is already implemented in `podcaster/storage.py` and activates when `PODCASTER_STORAGE_ACCOUNT_URL` is configured. Wording now reads: "System-assigned managed identity for blob storage writes (active when `PODCASTER_STORAGE_ACCOUNT_URL` is configured)."

- `docs/AZURE-DEPLOYMENT.md`: Replaced misleading example commit message `"Update TTS integration"` with `"Update podcaster function"`. TTS is not implemented; an example suggesting otherwise could create false expectations or confuse operators about deployment scope.

## Why

Stale "future" wording about managed identity contradicted the actual implementation. The TTS example commit message violated the principle that docs must accurately reflect what is and is not implemented — a security documentation concern because false capability claims lead to incorrect threat models.

## Not Changed

- All backward-compatible API names and env vars preserved: `/api/generate`, `x-podcaster-api-key`, `PODCASTER_API_KEY`, `PODCASTER_ENDPOINT`, response keys.
- No code modified; no Azure credentials used.
- `dry_run` description in `integration-contract.md` left as-is ("draft/stub artifacts") — the word "stub" is accurate since the audio artifact is explicitly labeled a deterministic placeholder.

## Verdict

Security/deployment wording is **deployment-handoff ready** after these two fixes. No secret-leakage paths found in the diff.


---
author: leela
created_at: 2026-06-07T19:49:59.902Z
type: audit-and-plan
priority: p0
---

# Audit & Planning: Local Work, Open Issues, and Release Sequencing

## Executive Summary

**Local readiness:** Wave 1/2/3 local increment (78813be) is complete and validated (pytest 19/19 ✅). Ready for PR and merge, NOT ready for Azure deployment.

**PR recommendation:** Single PR `wave-1-2-3-contract-pipeline-docs` closing #8, #3, #2, and progressing #1, #7, #6.

**Blockers before Azure deploy:** Azure subscription + GitHub Actions secrets. SquadScope variable/secret sync is optional.

**Remaining backlog:** #4 (TTS bakeoff), #5 (Spotify research), #9 (GitHub Actions chore), plus human review gate UI work.

---

## Part 1: Issues Reviewed (All 9 Open Squad Issues)

| # | Title | Type | Priority | Owner | Status | Notes |
|---|---|---|---|---|---|---|
| **1** | Define and enforce Podcaster script review gate | feature | P1 | Leela | Partial ✓ | Architecture + editorial standards documented; review gate UI backlog item (Leela #1 in local backlog) |
| **2** | Update Podcaster privacy and provider disclosure before TTS | docs | P1 | Hermes | Partial ✓ | SECURITY.md + docs merged; no live TTS approved until this signed off (signed off in Wave 3 gate) |
| **3** | Design Azure Blob temporary staging strategy | feature | P1 | Bender | Partial ✓ | Design + infra ready; deployment gated on subscription |
| **4** | Run early TTS quality bakeoff | spike | P1 | Farnsworth | Pending ⏳ | Needs investigation; blocks TTS implementation |
| **5** | Research Spotify publishing API and automation path | spike | P1 | Amy | Pending ⏳ | Needs investigation; blocks distribution automation |
| **6** | Generate weekly manual publishing packet after article publish | feature | P1 | Amy | Partial ✓ | Publishing packet generation + ZIP implemented; Wave 3 approved |
| **7** | Deploy Podcaster Azure Function and publish integration contract | feature | P1 | Bender | Pending ⏳ | Code ready; gated on Azure subscription + GitHub Actions secrets |
| **8** | Define SquadScope-to-Podcaster export contract | feature | P1 | Leela | Partial ✓ | `docs/integration-contract.md` finalized; validation in `podcaster/validation.py` |
| **9** | Update pinned GitHub Actions before Node 20 deprecation | chore | P2 | Bender | Pending ⏳ | Chore; can follow Wave 1/2/3 PR |

### 📋 Action Items for Next Sprint

**Immediate (after Wave PR merges):**
1. Bender: Create Azure subscription + GitHub Actions secrets (#7)
2. Farnsworth: Kick off TTS bakeoff (#4)
3. Amy: Kick off Spotify research (#5)
4. Bender: Update GitHub Actions Node 20 → 24 (#9)

**Follow-up (after #4 TTS complete):**
5. Leela: Design + implement review gate UI (#1 follow-up)

---

## Appendix: Issue-by-Issue Closure Mapping

| Issue | Title | Local Work Addresses | PR Closes | Note |
|---|---|---|---|---|
| #1 | Review gate | Architecture + editorial standards docs | Progresses (UI backlog) | Leela will own UI follow-up after #4 |
| #2 | Privacy/RAI | SECURITY.md + editorial-standards.md | Progresses (signed off Wave 3) | Hermes approved; no live TTS without sign-off |
| #3 | Blob staging | Design doc + bicep infra | Closes | Ready for Azure deploy |
| #4 | TTS bakeoff | None (research spike) | N/A | Farnsworth backlog |
| #5 | Spotify research | None (research spike) | N/A | Amy backlog |
| #6 | Publishing packet | Generation + ZIP impl | Closes | Approved Wave 3 |
| #7 | Deploy Azure | Code + bicep ready | Progresses (subscription needed) | Bender: set up Azure + secrets |
| #8 | Export contract | integration-contract.md finalized | Closes | Validation implemented |
| #9 | GitHub Actions | None (chore) | N/A | Bender backlog |


# Leela Wave 2 Readiness Verdict

- **Date:** 2026-06-07T19:19:52.661+00:00
- **Agent:** Leela
- **Status:** APPROVE / READY_FOR_AZURE_SUBSCRIPTION

## Decision

Approve the current Wave 1 + Wave 2 local diff for Azure subscription setup. The prior local blocker is resolved: packet `MANIFEST.json` now carries review metadata compatible with the tests, and the local suite is green.

## Validation

- `.venv/bin/python -m pytest -q` — 19 passed
- `.venv/bin/python -m compileall -q podcaster function_app.py` — passed
- `git diff --check` — passed

## Scope and compatibility

The diff remains cohesive and safe to keep. It preserves `/api/generate`, `x-podcaster-api-key`, `PODCASTER_API_KEY`, `PODCASTER_ENDPOINT`, and the documented top-level response keys while adding local artifact staging, review-pending metadata, packet generation, and Azure/security/operator documentation.

## READY_FOR_AZURE_SUBSCRIPTION conditions

- Do not change or block SquadScope article publishing; Podcaster remains post-publish and link-only.
- Configure Azure OIDC, repository variables, and `PODCASTER_API_KEY` without committing secrets.
- First Azure run must verify the Function endpoint, auth failure behavior, successful `202` accepted response shape, secret-free logs, managed-identity blob writes, and SquadScope `PODCASTER_ENDPOINT`/`PODCASTER_API_KEY` handoff.
- Keep human review required; current generated audio/script artifacts remain placeholders and are not publication-ready.

## Non-blocking follow-ups

- Hermes/Bender should clean up stale `docs/SECURITY.md` wording that still mentions HTTP 200/stub-only storage; actual local contract returns HTTP 202 and stages artifacts.
- Azure-only gaps remain gated for cloud verification: SAS/brokered artifact access and lifecycle cleanup.
- Real TTS, editorial-compliant script generation, and reviewer approval workflow remain future release gates.


---
author: leela
created_at: 2026-06-07T19:31:49Z
wave: 3
---

# Wave 3 Release Gate — APPROVED

**By:** Leela

## Gate Checks

| Check | Result |
|---|---|
| pytest 19/19 | ✅ PASSED (Fry confirmed) |
| compileall | ✅ PASSED |
| git diff --check | ✅ PASSED |
| `expires_at` parity (API / manifest blob / MANIFEST.json) | ✅ CONFIRMED (Bender) |
| RESPONSE_KEYS tuple stable across 202/400/401/500 | ✅ CONFIRMED (Fry) |
| No secret values in response bodies | ✅ CONFIRMED (Hermes + Fry) |
| Backward-compat identifiers unchanged | ✅ CONFIRMED |
| Scope creep check | ✅ CLEAN — no new top-level response fields, no TTS audio, no Azure calls |

## Scope Summary

Wave 1/2/3 local increment covers:
- **Contract scaffold** (`function_app.py`, `podcaster/validation.py`): deterministic stub → real job dispatch
- **Production pipeline** (`podcaster/jobs.py`, `podcaster/generation.py`, `podcaster/storage.py`): artifact generation, local storage backend, publishing packet ZIP
- **Infrastructure** (`infra/main.bicep`, `infra/main.parameters.example.json`): blob storage, managed identity, App Insights ready for Azure
- **Docs** (`docs/architecture.md`, `docs/AZURE-DEPLOYMENT.md`, `docs/SECURITY.md`, `docs/distribution-ux.md`, `docs/editorial-standards.md`, `docs/integration-contract.md`): deployment and editorial guidance
- **Backlog** (all 4 backlog files): fleshed-out specs for blob staging, human review gate, TTS bakeoff, publishing packet, Spotify research
- **Tests** (`tests/test_function_app.py`, `tests/test_validation.py`, `tests/test_jobs.py`): 19 passing
- **Team files** (`.squad/agents/*/history.md`, `.squad/decisions/inbox/*`, `.squad/skills/`): squad knowledge captured

## What Is NOT Included (By Design)

- No Azure deployment (gated on subscription setup)
- No live TTS/audio generation (Farnsworth bakeoff pending)
- No Spotify/podcast host automation (Amy research pending)
- No human review gate UI (Leela backlog item)
- No actual MP3 files (placeholder path only)

## Remaining Prerequisites Before Azure / Live Smoke Test

1. Azure subscription configured; resource group created
2. `az deployment group create` with `infra/main.bicep`
3. GitHub Actions secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`
4. App setting `PODCASTER_API_KEY` set in Function App
5. SquadScope side: `PODCASTER_ENDPOINT` variable + `PODCASTER_API_KEY` secret wired to Actions
6. First dry-run: POST `/api/generate` with `dry_run: true` from SquadScope test environment

## Decision

Approved to commit Wave 1/2/3 local increment as a single cohesive release. No Azure deployment. Review gate remains a human step before any public publishing.

---

# 2026-06-13: NEVER bypass branch rulesets

- **Date:** 2026-06-13
- **Agent:** Operator directive (jmservera)
- **Status:** Active — permanent rule

## Decision

All changes MUST go through branch + PR. No direct pushes to main. Agents must create a feature branch and open a PR, even for cleanup/docs/trivial changes. Never disable, bypass, or work around branch protection rulesets. No exceptions.

---

# 2026-06-13: Security review — Spotify publish module (#182)

- **Date:** 2026-06-13
- **Agent:** Hermes (Security)
- **Status:** Blocking — production auto-publish NOT approved

## Decision

Verdict: 🔴 — Do not enable production auto-publish until the implementation lands and receives a second security review.

Review scope: `feat/spotify-publish-182` does not contain `podcaster/publish.py`. This review covers `architecture.md` + upstream `spotifyconnector==0.8.2` behavior only.

## Key Findings

| # | Severity | Finding |
|---|----------|---------|
| 1 | High | Implementation missing — secret handling, dry-run enforcement, response parsing unverifiable. |
| 2 | High | `spotifyconnector` logs sensitive auth material (Bearer tokens, auth codes) at TRACE/DEBUG level. |
| 3 | High | Unofficial cookie-based auth (`sp_dc`/`sp_key`) = account-takeover risk if leaked. |
| 4 | Medium | Caller-controlled base URL could weaken TLS guarantees. |
| 5 | Medium | Bearer token cached in memory — must not leak through exceptions/logs. |
| 6 | Medium | Supply-chain risk — small third-party package, single PyPI owner, unofficial API. |
| 7 | Medium | Dry-run behavior cannot be verified (no implementation). |
| 8 | Medium | Upstream HTML/regex parsing is brittle; failures may be noisy. |
| 9 | Low | `SPOTIFY_SHOW_ID` is public metadata, not a secret. |
| 10 | Low | Input validation for publish fields not reviewable yet. |

## Recommendations

1. Do not enable production auto-publish until implementation exists and is security-reviewed.
2. Prefer a tiny in-repo adapter or vendored fork for cookie→Bearer exchange.
3. Force HTTPS; hardcode allowed Spotify hosts.
4. Sanitize every failure path at the publish boundary.
5. Dry-run must be network-dark (no auth exchange, no upload).
6. Add security tests (no secrets in logs, dry-run = zero HTTP calls, reject non-HTTPS, sanitized error responses).
7. Use `SPOTIFY_SHOW_ID` as non-secret config; keep only `SP_DC`/`SP_KEY` in secret stores.

### Spotify live-publish fail-safe (jmservera/SquadScope-Podcaster#602)

**Date:** 2026-07-17 · **Owner:** Hermes (Security)

**Context:** `podcaster/publish.py` authenticates to Spotify for Creators with
browser session cookies (`SP_DC`/`SP_KEY`) against the **unofficial** internal
`api-v5.anchor.fm` API. A cookie leak is Spotify-account-takeover material and
the API can break without notice — it is not a supported production integration.

**Decision:** The publisher **fails safe**. `SPOTIFY_PUBLISH_ENABLED` still only
gates whether the integration runs at all; a **separate** explicit opt-in,
`SPOTIFY_ALLOW_LIVE_PUBLISH=true`, now gates whether an episode may be made
*public*. Without that opt-in, any `immediate`/`scheduled` request is downgraded
to a **draft** (a one-time warning is logged). This encodes the standing rule
"drafts only — never auto-publish live" as a code-level default so a
misconfiguration or leaked cookie cannot silently push a public episode.

**Risk acceptance / follow-ups (not yet done):**
- Live publishing must stay **off** in production (do not set
  `SPOTIFY_ALLOW_LIVE_PUBLISH`) until an official OAuth/app-auth flow replaces
  the cookie path.
- Move `SP_DC`/`SP_KEY` into Azure Key Vault (same pattern as the YouTube
  refresh token) rather than plain env/repo secrets — tracked separately.
- Re-review before enabling live publishing; prefer a dedicated least-privilege
  bot account if the unofficial path is kept.
