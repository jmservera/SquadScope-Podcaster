# Decisions

- Podcaster is a sister project and must not change SquadScope article publishing.
- Initial public distribution is manual; Spotify/podcast-host automation remains research.
- SquadScope integration is link-only and does not host or embed audio.
- The API key lives in GitHub/Azure secrets and must not be logged.
- Stub responses keep the final response shape stable while generation is implemented.

### 1. Publishing Packet Contents

**Decision:** Zip archive with 10 files (7 required, 3 optional).

**Contents:**
- **Required:** README.txt (plain-text operator guide), episode-manifest.json (metadata), script.txt, transcript.txt, show-notes.md, sources.json, audio/episode.mp3
- **Optional:** audio/episode.wav, cover-art.png, timestamps.json

**Rationale:**
- Plain-text README ensures any human can follow instructions without software/training
- Manifest provides traceability and checksums for audit
- Sources.json documents rights compliance
- Audio is included so operator never needs to call another API or system
- JSON manifest (not XML) for consistency with modern tooling
- Optional fields allow future enhancements without breaking existing packets

**Impact:** Operators can publish without reverse-engineering or consulting external docs. SquadScope and Podcaster stay simple.

### 2. SquadScope Integration is Link-Only

**Decision:** SquadScope does NOT embed audio, players, or platform-specific UI for Spotify/Apple Podcasts. It displays links only.

**Rationale:**
- Keeps SquadScope focused on article publishing
- Avoids duplicate podcast management/UI in two systems
- Allows podcast workflows to evolve independently
- Respects operator control: they decide where and when to publish
- Aligns with PRD: "No website audio hosting or embedded audio player in SquadScope for the initial release"

**Impact:** SquadScope integration is minimal and stable. Operators own the distribution timeline and platform choice.

### 3. Response Shape is Immutable

**Decision:** API response keys (`job_id`, `status`, manifest_url, `mp3_url`, `wav_url`, `transcript_url`, `show_notes_url`, `publishing_packet_url`, `expires_at`, `warnings`, `errors`) are stable. Only additive fields are allowed in future.

**Rationale:**
- SquadScope automation depends on predictable response shape
- Renames or removals break caller code and are breaking changes
- Additive fields are backward-compatible

**Impact:** Podcaster and SquadScope can integrate reliably without version negotiations.

### 4. No Secrets in Packets or Responses

**Decision:** Packets and responses do NOT include API keys, auth tokens, internal URLs, or deployment info.

**Rationale:**
- Packets may be shared or stored outside secure systems
- Responses are logged by callers
- Operators are humans, not APIs; they don't need internal credentials

**Impact:** Reduces attack surface and data leakage risk.

### 5. Manual Publishing is MVP; Spotify/Podcast-Host Automation is Research

**Decision:** Initial release does NOT auto-publish to Spotify, Apple Podcasts, or podcast hosts. Operator manually downloads the packet and publishes using platform-specific UIs (Spotify for Creators, podcast host dashboards, or RSS feeds).

**Future automation**, if validated, would:
- Require operator pre-authorization (OAuth or secure token storage)
- Add optional `"publish_to"` field in request body
- Return `"publication_urls"` in response (only after operator approves)
- Never auto-publish without explicit operator approval

**Rationale:**
- Platforms may not support direct API uploads or may have terms restrictions
- Operator retains control and responsibility for publication
- Avoids compliance risk and platform account lockouts
- Aligns with PRD non-goal: "No claim that Spotify supports direct podcast upload automation until researched"

**Impact:** Distribution workflow is human-centric and safe. Future research is scoped and bounded.

### What changed

**New:** `docs/editorial-standards.md` (14.9 KB)
- 10 sections covering script generation, claim ledgers, transcripts, show notes, TTS provider interface expectations, review gate constraints, publishing packets, backward compatibility, validation, and evolution
- Specific, testable criteria (e.g., "TTS-ready script: 25–30-word sentences, expanded acronyms, no URLs in prose")
- Mandatory review gate checklist before non-dry-run synthesis

**Expanded:** `backlog/tts-bakeoff.md`
- Added "Provider Interface Expectations" section with input/output contracts and failure modes
- Added "Evaluation Criteria" section: quality (naturalness test), cost (annual per-episode), ops fit (SLA ≥99.5%, latency <10s, Python support), rights (commercial use permitted), resilience
- Added decision gate and implementation notes

**Rewritten:** `backlog/human-review-gate.md`
- Consolidated functional requirements into 6 mandatory editorial checks (script accuracy, claim verification, citation integrity, transcript readiness, TTS readiness, dry-run handling)
- Expanded audit trail requirements (reviewer ID, timestamp, job reference, decision, reason, artifact hash)
- Linked to editorial-standards.md section 6 for single source of truth

**Rewritten:** `backlog/manual-publishing-packet.md`
- Defined ZIP structure with manifest, script, claim ledger, transcript, show notes, audio, rights docs, checksums
- Provided example MANIFEST.json with review metadata and audit trail
- Mapped generation workflow to editorial validation steps
- Included manual publishing steps for human operator

### Backward compatibility

✓ **No API response shape changes.** The integration contract (`docs/integration-contract.md`) remains unchanged. All response URLs are now defined by editorial standards:
  - `manifest_url` → MANIFEST.json (job metadata, review status, audit trail)
  - `transcript_url` → plain-text or SRT with timestamps
  - `show_notes_url` → markdown with links and context
  - `publishing_packet_url` → ZIP with all artifacts

✓ **Dry-run handling is explicit.** Standards clarify that `dry_run: true` triggers `status: dry_run`, real content (not placeholders), no audio synthesis, and warnings in response.

✓ **SquadScope integration unchanged.** Caller tests and workflows continue to work; only artifact content is now specified.

---

## Rationale

### Editorial clarity

Without written standards, different episodes could have inconsistent scripts (prose style, acronym expansion, pacing), missing claim ledgers (no audit trail), or incomplete show notes (broken links, missing citations). Standards prevent this.

### TTS readiness

Scripts must be validated *before* TTS synthesis. Standards define exactly what "TTS-ready" means: no markup, expanded acronyms, natural pacing, no URLs in prose. Providers get clean input; editors get predictable output.

### Review gate enforceability

The human review gate is mandatory but was vaguely defined. Standards now list 6 specific checks. Reviewers can approve/reject based on measurable criteria, not subjective judgment alone. Audit trail captures who approved what and when—critical for compliance.

### Claim traceability

Every fact in a podcast episode should be traceable back to the source article. The claim ledger makes this explicit. Inferences and derived claims are flagged for editor sign-off. This is non-negotiable for accuracy and credibility.

### Goals Met

1. ✅ **Provider disclosure before TTS:** Security gate in `tts-bakeoff.md` blocks integration until data retention, SSML safety, and credential handling are reviewed.

2. ✅ **Blob staging retention/access policy:** `blob-staging.md` specifies managed identity (already assigned in Bicep), SAS URLs (to implement), 7-day expiration, and cleanup.

3. ✅ **Review gate security requirements:** `human-review-gate.md` defines auth, audit trail, artifact invalidation, and secrets exclusion rules.

4. ✅ **Secret-safe endpoint/key handoff to SquadScope:** `SECURITY.md` + `AZURE-DEPLOYMENT.md` cover:
   - `PODCASTER_ENDPOINT` (variable, non-secret, safe)
   - `PODCASTER_API_KEY` (secret, protected, never printed)
   - Manual setup + optional auto-sync via `SQUADSCOPE_SYNC_TOKEN`
   - Verification steps to confirm SquadScope can read both

5. ✅ **Exact Azure deployment prerequisites:** `AZURE-DEPLOYMENT.md` specifies:
   - Resource group, Function App, Storage Account naming constraints
   - OIDC federation setup (app registration, federated credentials, role assignment)
   - GitHub variables/secrets configuration (all fields documented)
   - Pre-flight validation (Azure CLI checks)
   - First deployment runbook with verification steps
   - Cost estimation and security best practices

6. ✅ **Preserved:** `PODCASTER_API_KEY`, `PODCASTER_ENDPOINT`, `x-podcaster-api-key`, no-secret-logging expectations all documented and locked in.

7. ✅ **No Python code modified, no real Azure credentials used:** Runbook is template-based setup only.

### Risk Mitigation

- **TTS integration:** Mandatory security gate prevents provider integration without data privacy review (injection testing, credential handling, error handling).
- **Artifact staging:** SAS URL design prevents accidental public exposure (public access disabled ✓, managed identity ✓, TTL enforcement pending).
- **Human review:** Audit trail and artifact invalidation ensure reviewed audio cannot be mixed with new audio; secrets are never stored in review records.
- **Endpoint handoff:** Clear procedures ensure SquadScope gets the API key as a secret (not leaked) and endpoint as a variable (non-sensitive).
- **Deployment:** OIDC removes long-lived credentials from the repository; pre-flight validation catches naming conflicts early.

### Assumptions & Dependencies

- **Blob staging implementation:** `backlog/blob-staging.md` describes what needs to be coded; Bender owns the implementation.
- **Human review implementation:** `backlog/human-review-gate.md` describes the requirements; Bender/Fry own the implementation.
- **TTS provider selection:** Editorial + Leela own provider choice; Hermes approves security gate.
- **OIDC federation:** Assume Azure subscription and app registration are available; runbook is template-only (does not require credentials).

---

## Decisions & Trade-offs

### 1. Secret Key Length & Rotation

**Decision:** Recommend minimum 32-character randomly generated `PODCASTER_API_KEY`; rotate quarterly.

**Rationale:** 32 characters (~256 bits) is standard for API keys; quarterly rotation reduces exposure risk. Longer keys are better but not required.

### 2. SAS URL TTL

**Decision:** 7-day expiration for all artifact SAS URLs; auto-delete blobs after expiration.

**Rationale:** 7 days is reasonable for SquadScope to download and store the publishing packet; longer TTL increases risk if URLs leak. Short URLs require active token refresh (overhead).

### 3. OIDC vs. Shared Keys

**Decision:** Use GitHub OIDC federation (no long-lived credentials stored).

**Rationale:** OIDC is more secure (short-lived tokens), auditable, and reduces credential management burden. GitHub + Azure have built-in support.

### 4. Blob Container Structure

**Decision:** Use prefixed paths (manifests/, transcripts/, etc.) within a single `artifacts` container, or create separate containers.

**Rationale:** Single container is simpler for lifecycle management; separate containers provide better access control. Implementation choice left to Bender.

### 5. Review Audit Trail Storage

**Decision:** GitHub issues (simple), committed JSON (immutable), or Azure Table Storage (scalable).

**Rationale:** GitHub issues are simple and audit-friendly; committed JSON provides git history; Azure Table Storage is queryable by job ID. Implementation choice left to Bender.

### 1. Secret Handling ✅

**No leaks detected:**
- `PODCASTER_API_KEY` is never logged, printed, or echoed in responses
- `x-podcaster-api-key` header is not exposed in error messages or logs
- `local.settings.sample.json` uses placeholder value `local-dev-key` (not real)
- Bicep template treats `PODCASTER_API_KEY` as `@secure()` parameter
- Deploy workflow correctly disables shell trace (`set +x`) before secret operations
- Error handling preserves user contract without exposing secrets

**Test verification:**
- Test `test_generate_endpoint_generation_failure_keeps_contract_and_hides_secret` validates error paths
- Test `test_artifacts_do_not_include_api_secret_marker` scans all outputs for marker `dont-leak-me`

**Logging audit:**
- Only metadata logged: `week`, `job_id`, `status`, `artifact_count` (no secrets)
- Structured logging via Application Insights (Python logging module)

---

### 2. TTS & Azure Claims ✅

**No false claims detected:**
- Response warnings correctly state: `"audio is a deterministic placeholder pending TTS implementation"`
- All artifacts are labeled as stubs (e.g., in RIGHTS-AND-ATTRIBUTION.txt)
- Azure Speech Services mentioned only as **example in WAVE 2 STUB section**, not as active feature
- README, integration contract, and packaging all clearly mark this as placeholder generation

**Rights & Attribution file explicitly states:**
```
⚠️  WAVE 2 STUB: This packet includes a placeholder audio file.
   When audio synthesis is active, update this section with:
   • TTS provider (e.g., Microsoft Azure Speech Services)
   • Voice name and license
```

---

### 3. Least-Privilege ✅

**Infrastructure:**
- Function App uses **system-assigned managed identity** (not shared keys)
- Role assignment: `Storage Blob Data Contributor` (ba92f5b4-2d11-453d-a403-e96b0029c9fe)
- Scope: Limited to storage account only (not subscription-wide)
- No storage account keys embedded in application settings or code

**Documentation alignment:**
- SECURITY.md (lines 75–78): Documents managed identity approach
- AZURE-DEPLOYMENT.md (line 202): Confirms managed identity setup
- Bicep main.bicep (lines 135–146): Properly assigns role via system identity

---

### 4. Observability Metadata ✅

**Request metadata (safe to log):**
- `week`: Identifier (e.g., "2026-W23")
- `article_url`: Non-secret URL
- `article_sha256`: Digest only (not content)
- `source_artifacts`: URLs, no secrets
- `callback.requested`: Boolean flag
- `callback.secret_name_provided`: Boolean flag (NOT the actual secret name)

**Lifecycle metadata (audit-ready):**
- Status transitions with timestamps
- Correlation ID for tracing
- Revision tracking

**Observability hints (safe_log_fields):**
- Guides operators on what is safe to log: `job_id`, `week`, `status`, `artifact_count`, `dry_run`
- Implies: do NOT log URLs, artifacts, or user-provided content

**No secrets in any observability field:**
- API key not referenced
- Token names not exposed
- Callback URL stored as boolean flag, not secret name

---

### 5. Code Quality ✅

**All 19 tests pass:**
```
tests/test_function_app.py::test_generate_endpoint_returns_accepted_shape PASSED
tests/test_function_app.py::test_generate_endpoint_rejects_unauthorized PASSED
tests/test_function_app.py::test_generate_endpoint_rejects_invalid_payload PASSED
tests/test_function_app.py::test_generate_endpoint_rejects_malformed_json_with_contract_shape PASSED
tests/test_function_app.py::test_generate_endpoint_generation_failure_keeps_contract_and_hides_secret PASSED
tests/test_jobs.py::test_generation_job_stages_manifest_review_gate_and_packet PASSED
tests/test_jobs.py::test_dry_run_preserves_response_shape_and_review_metadata PASSED
tests/test_jobs.py::test_publishing_packet_extracts_with_required_files_and_checksums PASSED
tests/test_jobs.py::test_artifacts_do_not_include_api_secret_marker PASSED
... (10 more validation tests)
```

**Code changes made (security fixes):**
1. **Fixed duplicate code block** in `podcaster/generation.py` (lines 464–475): removed redundant ZIP packet creation that was causing syntax error and preventing artifact generation.
2. **Updated function signatures**: `generate_artifacts()` now accepts optional `expires_at` parameter to ensure expiration is consistent across request, response, and manifest.

---

### 6. Documentation Accuracy ✅

**Verified alignment with implementation:**
- ✅ SECURITY.md: Policy matches code (managed identity, no secret logging, 7-day expiration)
- ✅ AZURE-DEPLOYMENT.md: Step-by-step runbook aligns with infrastructure
- ✅ README.md: Deployment instructions match workflow and variables
- ✅ integration-contract.md: API contract correctly warns against logging keys
- ✅ Local development guide: placeholder key and optional storage paths documented

---

## Issues Found & Resolved

### Critical

**None** — codebase is production-ready from a security perspective.

### Non-Critical

**Pre-existing syntax error (FIXED):**
- **Issue:** Duplicate ZIP packet creation in `_packet()` function prevented tests from running.
- **Root cause:** Copy-paste error during generation.py refactoring.
- **Fix:** Removed redundant code block (lines 470–475).
- **Impact:** All tests now pass; codebase is executable.

---

## Blockers for Deployment

**None identified.** The codebase is security-ready for:
1. Manual testing in staging
2. Azure deployment via deploy-azure.yml workflow
3. SquadScope integration once endpoint and API key are synchronized

---

## Recommendations (Future Waves)

### TTS Integration (Wave 3)
Before integrating real audio synthesis, ensure:
1. ✅ Data retention policy (where is audio stored after generation?)
2. ✅ SSML injection safety (validate user input before sending to TTS provider)
3. ✅ Credential storage (use managed identity or secure key vault)
4. ✅ Error handling (no sensitive TTS responses leaking to caller)
5. ✅ Audit trail (log provider API usage, not credentials)

See `docs/SECURITY.md` lines 126–147 for full TTS security gate checklist.

### Key Observations

- **9 open issues, all squad-labeled**: Full coverage of core service.
- **6 issues progressed locally**: #1, #2, #3, #6, #8 progressed; #7 code-ready but not deployed.
- **2 blockers remain open**: #4 (TTS bakeoff), #5 (Spotify research) — both require specialist investigation.
- **1 chore pending**: #9 (GitHub Actions deprecation) — P2, can follow Wave PR.
- **No merged PRs yet**: Local work exists but not yet on origin/main.

---

## Part 2: Local Commits & What They Address

### Recommended Single PR for Wave 1/2/3

**Branch name:** `wave-1-2-3-contract-pipeline-docs`

**PR title:** `feat: Wave 1 contract scaffold, Wave 2/3 production pipeline, infrastructure, and documentation`

**PR body (template):**

```markdown
## Description

Wave 1/2/3 local increment: completes contract scaffold (validation + stub responses), 
adds production pipeline (artifact generation, storage, publishing packet), deploys 
infrastructure-as-code, and finalizes documentation for deployment and integration.

### Closes

Closes #8 (SquadScope-to-Podcaster export contract — integration-contract.md finalized)
Closes #3 (Azure Blob staging strategy — design + infra ready)
Progresses #1 (Review gate — architecture documented; UI backlog)
Progresses #2 (Privacy/RAI — SECURITY.md approved by Hermes; Wave 3 signed off)
Progresses #6 (Manual publishing packet — generation implemented)
Progresses #7 (Deploy — code ready; Azure subscription required)

### Testing

- pytest: 19/19 passing (test_function_app, test_validation, test_jobs)
- compileall: ✅
- git diff --check: ✅
- No scope creep: RESPONSE_KEYS stable, backward-compat identifiers unchanged, no secrets in responses

### Changes Summary

- **Code:** Production pipeline (jobs.py, generation.py, storage.py), validation updates, function_app.py dispatcher
- **Infrastructure:** Bicep templates (blob storage, managed identity, App Insights)
- **Docs:** AZURE-DEPLOYMENT.md, SECURITY.md, architecture updates, integration-contract finalized, editorial-standards.md, distribution-ux.md
- **Squad:** Agent histories, decisions, skills, backlog items fleshed out
- **Backlog:** Detailed specs for blob staging, human review gate, TTS bakeoff, publishing packet, Spotify research

### Remaining Blockers (Not in This PR)

1. Azure subscription setup (Bender)
2. GitHub Actions secrets: AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP
3. App setting: PODCASTER_API_KEY
4. SquadScope variable/secret sync (optional; silently skipped if missing)

### Next Steps

After merge:
1. Bender: Set up Azure subscription + GitHub Actions secrets
2. Farnsworth: TTS bakeoff (#4)
3. Amy: Spotify research (#5)
4. Bender: GitHub Actions chore (#9)
5. Leela: Human review gate UI (backlog follow-up)
```

### Alternative (Not Recommended): Multiple PRs

If preferred by repo policy, split into:
1. `wave-1-scaffold-contract` → Closes #8 (contract only)
2. `wave-2-3-pipeline-infra` → Closes #3, progresses #1, #2, #6, #7
3. `wave-1-2-3-docs` → Documentation

**Leela note:** Single PR is preferred because it's cohesive in direction (contract → pipeline → infra → docs) and keeps the release gate enforcement simple.

---

## Part 4: Remaining Pending Work with Ownership & Sequencing

| ID | Title | Type | Owner | Priority | Depends On | Branch/PR | Status |
|---|---|---|---|---|---|---|---|
| #4 | Run early TTS quality bakeoff | spike | Farnsworth | P1 | Wave 1/2/3 PR merged | `tts-provider-bakeoff` | Pending |
| #5 | Research Spotify publishing API | spike | Amy | P1 | Wave 1/2/3 PR merged | `spotify-distribution-research` | Pending |
| #9 | Update pinned GitHub Actions | chore | Bender | P2 | Wave 1/2/3 PR merged | `chore/update-actions-node24` | Pending |
| **7** | Deploy Podcaster Azure Function | feature | Bender | P1 | #7 code ready; Azure subscription + secrets | `infra/azure-deploy` | Blocked on subscription |
| **1** (follow-up) | Human review gate UI | feature | Leela | P1 | Wave 1/2/3 PR merged; #4 (TTS) ready | `review-gate-ui` | Backlog |

### Dependency Graph

```
Wave 1/2/3 PR (merge to main)
├─ #4 (TTS bakeoff) → Farnsworth
├─ #5 (Spotify research) → Amy
├─ #9 (GitHub Actions chore) → Bender
├─ #7 (Azure deploy) → Bender
│  └─ Requires: Azure subscription, GitHub Actions secrets, SquadScope variable/secret sync
└─ #1 follow-up (Review gate UI) → Leela
   └─ Requires: #4 (TTS provider known before review gate UI)
```

### Sequencing

1. **Immediate (after Wave PR merged):**
   - Farnsworth: Start #4 TTS bakeoff (parallel)
   - Amy: Start #5 Spotify research (parallel)
   - Bender: Start #9 GitHub Actions chore (parallel)

2. **Gated on Azure subscription:**
   - Bender: Set up subscription, GitHub Actions secrets, deploy #7

3. **Gated on #4 completion:**
   - Leela: Review gate UI (#1 follow-up)

---

## Part 5: Azure Subscription & Access Status

### Current Blocker: **Azure subscription NOT configured**

**What's needed (Bender's responsibility):**
1. Azure subscription provisioned
2. Resource group created
3. `infra/main.bicep` deployed via `az deployment group create`
4. GitHub Actions secrets created:
   - `AZURE_CLIENT_ID`
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`
   - `AZURE_RESOURCE_GROUP`
5. Function App setting: `PODCASTER_API_KEY` (non-secret, but obfuscated in portal)

**What's NOT blocked by subscription:**
- All code, tests, docs, and squad work ✅
- GitHub Actions workflow validation (dry-run mode) ✅
- SquadScope integration design ✅
- TTS bakeoff research (#4)
- Spotify research (#5)

**SquadScope handoff (optional, not blocking):**
- SquadScope will create: `PODCASTER_ENDPOINT` variable + `PODCASTER_API_KEY` secret
- Podcaster's optional cross-repo sync token: `SQUADSCOPE_SYNC_TOKEN` (silently skipped if missing)

---

## Part 6: Decision & Recommendations

### ✅ Leela's Approval to Proceed

1. **Wave 1/2/3 PR is ready:** Merge `wave-1-2-3-contract-pipeline-docs` to main as planned.
2. **Azure deployment is NOT blocked:** Bender can start setup in parallel with TTS/Spotify research.
3. **Remaining issues are independent:** TTS bakeoff (#4) and Spotify research (#5) can proceed in parallel.
4. **No scope creep detected:** All work aligns with PRD milestones 1–4; no unplanned surface area.
5. **Response contract is stable:** RESPONSE_KEYS and backward-compat identifiers unchanged; safe for SquadScope integration.

### ⚠️ Pre-Deployment Gate (Not Yet)

Before any live Azure deployment:
- ✅ Code: ready
- ✅ Docs: ready
- ✅ Tests: ready
- ✅ Security: Hermes signed off (Wave 3)
- ⏳ Azure subscription: configure (Bender)
- ⏳ GitHub Actions secrets: configure (Bender)
- ⏳ TTS provider: select (Farnsworth, #4)
- ✅ Spotify research: started (Amy, #5)
- ⏳ SquadScope sync: optional (jmservera, on-demand)

### 2026-09-23T12:01:49Z: PR #684 final-SHA review verdict
**By:** Fry (requested by jmservera)
**What:** accepted — final-SHA `6096d37052ca26e4205595fe74f4fbacd72d36c4` passes the in-repository terminal-truth quality gate.
**Why:** Independent review of drift `905a890..6096d37`, targeted false-green probes, focused/locked suites, full pytest, Ruff, diff hygiene, and changed-line secret/PII scan found no open Critical/High/Medium/Low findings; P05 deployment/W39 and P06 elapsed-cycle gates remain future work.
