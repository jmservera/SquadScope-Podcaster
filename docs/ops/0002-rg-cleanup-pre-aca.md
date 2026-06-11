# Ops Record 0002 — RG cleanup + redeploy plan before the ACA stack

- **Issue:** [#91](https://github.com/jmservera/SquadScope-Podcaster/issues/91)
- **Owner:** Bender (deploy/Azure) · Support: Hermes (identity/permissions/artifact-access)
- **Resource group:** `squadscope-podcaster` (sub `99d2c976-…`, `swedencentral`)
- **Inventory verified:** 2026-06-11 (live `az resource list`)
- **Status:** PLAN ONLY. No deletions executed by the agent. All teardown steps
  require explicit operator go-ahead (see §4).

## TL;DR — the cleanup is smaller (and safer) than the issue assumed

A live inventory + cross-check against `infra/main.bicep` shows that **none of the
current RG resources are safe to delete autonomously right now**:

- Most resources are **bicep-managed** and reused idempotently by the next deploy
  (deterministic names) — they do **not** need teardown to "deploy fresh".
- The storage account is **stateful** and holds the operator's review artifacts.
- The two resources the issue flagged as "orphaned/former" are **in active use**:
  - the EventGrid system topic carries a **Defender for Storage** malware-scan
    subscription (security feature), not a deployment leftover;
  - `squadscope-mi` is the **RG `Owner` working identity** (deleting it would
    remove the credential operating this RG).

The only genuinely *former* resource is the **manual bakeoff Azure OpenAI**
account, and it is still referenced by the live Function App, so it is
operator-gated behind the bicep-provisioned OpenAI replacement (§3.B).

## 1. Live RG inventory and classification

| Resource | Type | Classification | Action |
|---|---|---|---|
| `podcaster-squadscope-p-3f9a07d60de7` | `Microsoft.Web/sites` (Function App) | **bicep-managed / keep** | Reused by deploy (same name). No teardown. |
| `podcaster-squadscope-p-3f9a07d60de7-plan` | `Microsoft.Web/serverFarms` | **bicep-managed / keep** | Reused by deploy. |
| `podcaster-squadscope-p-3f9a07d60de7-appi` | `Microsoft.Insights/components` | **bicep-managed / keep** | Reused by deploy. |
| `podcaster-squadscope-p-3f9a07d60de7-law` | `Microsoft.OperationalInsights/workspaces` | **bicep-managed / keep** | Reused by deploy. |
| `squadscopepo3f9a07d60de7` | `Microsoft.Storage/storageAccounts` | **bicep-managed / keep — STATEFUL** | Reused by deploy. Holds review artifacts — preserve first (§2). |
| `Application Insights Smart Detection` | `microsoft.insights/actiongroups` | **auto-created / keep** | Auto-provisioned by App Insights; harmless, no action. |
| `Failure Anomalies - …-appi` | `microsoft.alertsmanagement/smartDetectorAlertRules` | **auto-created / keep** | Auto-provisioned by App Insights; harmless, no action. |
| `squadscopepo3f9a07d60de7-be6266f3-…` | `Microsoft.EventGrid/systemTopics` | **security feature / keep** | NOT orphaned. `source` = the storage account; carries `StorageAntimalwareSubscription` (Defender for Storage). Deleting it disables malware scanning. |
| `squadscope-mi` | `Microsoft.ManagedIdentity/userAssignedIdentities` | **working identity / keep — operator-gated** | Holds **`Owner`** on the RG and `Storage Blob Data Contributor` on the storage account. Not referenced by `main.bicep` (the Function App uses a `SystemAssigned` identity), but it is the operating credential for this RG. Do **not** delete without operator confirmation. |
| `podcaster-openai-bakeoff-20260609` | `Microsoft.CognitiveServices/accounts` (OpenAI) | **FORMER — operator-gated delete** | Manual bakeoff account. Still referenced by the live Function App `AZURE_OPENAI_ENDPOINT` and by the deploy workflow default. Delete only after the bicep-provisioned OpenAI replaces it (§3.B). |

### What `infra/main.bicep` recreates / owns

- Storage account (deterministic name `squadscopepo3f9a07d60de7`), the
  `podcaster-artifacts` and `function-packages` containers, and the blob
  lifecycle policy (#89).
- Log Analytics workspace, Application Insights, Consumption plan, Linux
  Function App (`SystemAssigned` identity) + Storage data-plane role assignments.
- Conditional **Azure OpenAI** account/deployments when `deployOpenAi=true`
  (`modules/openai.bicep`) — this is the intended replacement for the manual
  bakeoff account.
- Conditional **ACA** environment + queue-triggered synthesis Job + identity
  when `deployAudioJob=true` (`modules/aca.bicep`).

Because every kept resource uses a **deterministic name**, re-running Deploy
Azure is **idempotent**: it reconciles in place. "Deploy fresh" does **not**
require deleting the storage/site/plan/law/appi.

## 2. PRESERVE ARTIFACTS FIRST (do this before any storage-touching step)

Review episodes currently in `squadscopepo3f9a07d60de7` /
`podcaster-artifacts/review/` (verified 2026-06-11):

```
review/claracle-2026-W24-script.txt               5,351 B
review/claracle-2026-W24-v2-script.txt            6,009 B
review/claracle-2026-W24-v2.mp3                3,472,030 B  (v1 audio)
review/claracle-2026-W24.mp3                  4,102,731 B
review/v3/claracle-2026-W24-review-manifest.json  7,778 B
review/v3/claracle-2026-W24-script.txt            5,920 B
review/v3/claracle-2026-W24.mp3               4,135,645 B
```

> NOTE: these `review/` blobs sit alongside the lifecycle policy's
> `podcaster-artifacts/` prefix match. The #89 policy deletes blobs
> `daysAfterModificationGreaterThan: 7`, so **the review episodes are already on
> a 7-day expiry clock**. Back them up to a retained location regardless of the
> cleanup decision. (Follow-up: consider excluding `review/` from auto-expiry —
> see §5.)

Recommended backup (operator-run; identity-only, no keys):

```bash
# Pull a local retained copy before any destructive step
az storage blob download-batch \
  --account-name squadscopepo3f9a07d60de7 \
  --source podcaster-artifacts --pattern 'review/*' \
  --destination ./rg-cleanup-backup/review --auth-mode login
```

The storage account is **not** scheduled for teardown in this plan (it is
bicep-managed and reused), so no SAS-link breakage is expected. The backup is a
safety net only.

## 3. Sequenced cleanup + redeploy

### A. Non-stateful cleanup the agent is confident about

**None.** After live verification, every "former-looking" resource is actually
in use (Defender system topic) or is the operating identity (`squadscope-mi`).
There is no safe, non-stateful deletion to perform autonomously. The previous
assumption that the EventGrid system topic was an orphaned deployment artifact
is **incorrect** — see §1.

### B. Former bakeoff OpenAI — operator-gated, paired with redeploy

1. Operator runs **Deploy Azure with `deploy_openai=true`** in a region/SKU that
   supports `gpt-4o-mini-tts` + `gpt-4o-mini` (`infra/modules/openai.bicep`).
   This provisions `…-openai` and points the Function App at it via managed
   identity. *Note:* `swedencentral` may not offer these models — pick the region
   per #30/#60 before enabling.
2. Confirm the live Function App `AZURE_OPENAI_ENDPOINT` now resolves to the
   bicep-provisioned account and `/api/generate` smoke is still green.
3. **Only then** delete the manual bakeoff account:
   ```bash
   az cognitiveservices account delete \
     -g squadscope-podcaster -n podcaster-openai-bakeoff-20260609
   az cognitiveservices account purge \
     -g squadscope-podcaster -l swedencentral -n podcaster-openai-bakeoff-20260609
   ```
   This breaks production OpenAI TTS until step 1's account is live — acceptable
   pre-production, but must follow, not precede, the replacement.

### C. ACA stack — follow-up, not part of this cleanup

After §3.B and operator go-ahead, the separate ACA deploy
(`deploy_openai=true` **and** `deployAudioJob=true`) provisions the fresh
synthesis stack (#76–#80). Tracked separately; do not bundle here.

## 4. FLAG — destructive / stateful steps requiring operator confirmation

Do **not** execute autonomously. Each is irreversible or breaks live behaviour:

- [x] Delete/purge the manual bakeoff Azure OpenAI (`podcaster-openai-bakeoff-20260609`) — ✅ deleted 2026-06-11; purge denied (insufficient perms, will auto-purge after retention). Replaced by `podcaster-squadscope-p-3f9a07d60de7-openai` in eastus2.
- [ ] Any change to `squadscope-mi` — it holds **`Owner` on the RG**; removing it can sever RG operation. Operator must confirm whether it is still the working credential before touch.
- [ ] Any teardown of the storage account, Function App, plan, App Insights, or Log Analytics — these are bicep-managed and **should be reconciled by redeploy, not deleted**. Deletion would drop review artifacts and break the green deploy.
- [ ] Do not delete the EventGrid system topic / `StorageAntimalwareSubscription` — that disables Defender for Storage malware scanning.

## 5. Follow-ups identified during this review

- **Review artifacts are on the 7-day lifecycle expiry clock** (#89 policy
  matches `podcaster-artifacts/`). File a follow-up to either move review
  episodes under a retained prefix/container or add a lifecycle exclusion so
  operator review copies are not auto-deleted.
- Confirm with the operator whether `squadscope-mi` (RG `Owner`) is still needed;
  if it is the human/automation working identity, document it as intentional in
  the architecture notes rather than treating it as a cleanup candidate.

## 6. Execution log — approved #92 cleanup attempt (2026-06-11)

Executed by the Podcaster squad under operator approval ("ensure the Podcaster
deployment is working and get rid of the unused resources"). Preserve-first,
verify-before-delete. **Outcome: BLOCKED at provisioning; nothing deleted.**

1. **Deployment health — CONFIRMED.** Latest `Deploy Azure` on `main`
   ([run 27344966830](https://github.com/jmservera/SquadScope-Podcaster/actions/runs/27344966830))
   succeeded; `Deploy infrastructure`, `Wait for Function App to index functions`,
   and `Smoke deployed generate endpoint` all green (HTTP 202 with non-empty
   `job_id`/`manifest_url`, `errors=[]`). The prior 3+ runs also succeeded. The
   deployment works.

2. **Review artifacts — RETAINED (verified live).** All seven
   `podcaster-artifacts/review/` blobs are present (script/v2/v3 + mp3s). The
   storage lifecycle policy's `expire-artifacts` rule matches only
   `podcaster-artifacts/jobs/` and `podcaster-artifacts/bakeoff/` (7 days); the
   `review/` prefix is **not** matched, so the #93/#94 retention is in effect and
   the review episodes are safe.

3. **Provision bicep-managed OpenAI — BLOCKED (region/model gap). STOPPED; did
   NOT run `deploy_openai=true`.** Pre-flight `az cognitiveservices model list`
   shows the configured TTS model is **not offered in `swedencentral`**:
   - `gpt-4o-mini-tts` (bicep default `ttsModelName`, version `2025-03-20`):
     **0 results in `swedencentral`**. Of the regions checked, only `eastus2`
     offers it. swedencentral exposes only legacy `tts` / `tts-hd` (version `001`).
   - `gpt-4o-mini` (chat, `2024-07-18`): available in swedencentral. ✓
   - The manual bakeoff does **not** prove `gpt-4o-mini-tts` availability — its
     deployment `tts-bakeoff` uses model `tts` version `001` (the legacy TTS
     model), which is a different model from the bicep-configured
     `gpt-4o-mini-tts`.

   Running `deploy_openai=true` as-is would fail on the `ttsDeployment`
   (`modules/openai.bicep`) and leave a partially-provisioned OpenAI account in
   the RG (a new orphan). Per the approved guard ("if the deploy fails on
   model/region/quota, STOP and report; do not delete anything"), provisioning was
   not attempted. This realises the risk flagged in §3.B / #30 / #60.

4. **Repoint + verify — NOT REACHED.** No bicep OpenAI exists, so the Function App
   `AZURE_OPENAI_ENDPOINT` remains the bakeoff
   (`https://podcaster-openai-bakeoff-20260609.openai.azure.com/`,
   `AZURE_OPENAI_TTS_DEPLOYMENT=tts-bakeoff`). Note: even after provisioning, the
   repo variables `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_TTS_DEPLOYMENT` (consumed
   by the deploy workflow's app-settings step) must be repointed to the bicep
   account/`tts` deployment, otherwise that step re-clobbers the endpoint back to
   the bakeoff. Tracked for the unblock work.

5. **Delete the bakeoff — NOT DONE (correct).** `podcaster-openai-bakeoff-20260609`
   is still the live OpenAI endpoint and has no working replacement, so it remains
   in use and was **not** deleted.

6. **Sweep for other unused resources — none found.** RG inventory unchanged from
   §1; every resource is KEEP. `function-packages/` blobs are auto-expired by the
   `expire-deploy-packages` lifecycle rule (7 days) — none currently exceed the
   window, so no manual deletion. ARM deployment history is small and healthy. The
   ACA job (`deploy_audio_job`) was **not** enabled (separate operator decision).

### Resolution (2026-06-11, later same day)

The blocker above was resolved by PRs #96/#100/#103/#105/#106: the OpenAI account
was moved to eastus2 (supports `gpt-4o-mini-tts`), the bakeoff was deleted, and
the deploy workflow now points to the Bicep-provisioned resource.

## 7. Region migration — 2026-06-11

- **OpenAI account moved to eastus2** (`podcaster-squadscope-p-3f9a07d60de7-openai`)
  to support `gpt-4o-mini-tts` (GlobalStandard SKU). The subscription lacks
  compute quota in eastus2, so Function App, Storage, App Insights, and Log
  Analytics remain in swedencentral.
- **Bakeoff retired**: `podcaster-openai-bakeoff-20260609` deleted (soft-delete;
  purge pending auto-retention). Workflow defaults updated to point at the new
  bicep-provisioned `tts` / `chat` deployments.
- **Infra parameter**: new `openAiLocation` param (default `eastus2`) added to
  `infra/main.bicep` so the OpenAI account can deploy to a different region
  from the rest of the stack. PR #96.

## Constraints honoured

- No resource deleted by the agent. No secrets printed (identity-only `az`,
  no account keys). Every recommended deletion is recorded above with its
  precondition. Review artifacts preserved-first.
