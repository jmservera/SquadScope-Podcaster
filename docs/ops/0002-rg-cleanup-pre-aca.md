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

- [ ] Delete/purge the manual bakeoff Azure OpenAI (`podcaster-openai-bakeoff-20260609`) — only after §3.B steps 1–2.
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

## Constraints honoured

- No resource deleted by the agent. No secrets printed (identity-only `az`,
  no account keys). Every recommended deletion is recorded above with its
  precondition. Review artifacts preserved-first.
