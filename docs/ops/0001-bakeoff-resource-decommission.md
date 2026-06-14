# Ops Record 0001 — Bakeoff Azure resource decommission

> **⚠️ Historical record.** The bakeoff resources described here have been decommissioned. The production stack now uses bicep-managed Azure OpenAI in eastus2 (see `infra/main.bicep`). Retained for audit trail.

- **Issue:** [#68](https://github.com/jmservera/SquadScope-Podcaster/issues/68)
- **Owner:** Bender (deploy/Azure) · Support: Hermes (secrets/permissions)
- **Resource group:** `squadscope-podcaster` (sub `99d2c976-…`, `swedencentral`)
- **Verified:** 2026-06-11

This record documents the safe decommission of the two TTS bakeoff resources
created on 2026-06-09. Provider selection landed on **OpenAI TTS (`fable` +
`alloy`)**, so the Azure Speech bakeoff is no longer needed; the OpenAI bakeoff
is still actively serving as the production OpenAI endpoint and must be kept.

## 1. Azure Speech bakeoff — `podcaster-tts-bakeoff-20260609` — ✅ DECOMMISSIONED

Selected provider is OpenAI TTS, so this Azure Speech (Cognitive Services)
account is unused.

Verification performed before treating it as safe to remove:

- **No source/workflow/secret references.** Repo-wide search for the exact name
  returns nothing:
  ```
  grep -rn "podcaster-tts-bakeoff-20260609" .   # → no matches (excl. .venv)
  ```
- **Absent from the subscription.** It is not present in the resource group nor
  in any Cognitive Services listing:
  ```
  az resource list -g squadscope-podcaster --query "[?contains(name,'bakeoff')]"
  az cognitiveservices account list --query "[].name"
  # → only podcaster-openai-bakeoff-20260609 remains
  ```

**Status:** the Speech account is already gone (no live resource, no
references). No further action required. If a stray Speech account reappears in
this RG, it can be deleted with `az cognitiveservices account delete` after
re-confirming there are no references.

## 2. Azure OpenAI bakeoff — `podcaster-openai-bakeoff-20260609` — ⚠️ KEEP (IN USE)

This account (kind `OpenAI`, deployment `tts-bakeoff`, model `tts`) is the
**current production OpenAI endpoint**. The Deploy Azure workflow defaults the
Function App settings to it:

```
# .github/workflows/deploy-azure.yml
AZURE_OPENAI_ENDPOINT=${OPENAI_ENDPOINT:-https://podcaster-openai-bakeoff-20260609.openai.azure.com/}
AZURE_OPENAI_TTS_DEPLOYMENT=${OPENAI_TTS_DEPLOYMENT:-tts-bakeoff}
```

It also produced the first reviewed episode. **Do NOT delete.**

### Migration → delete plan (flag operator before each step)

1. Provision a dedicated **production Azure OpenAI** resource with a `tts`
   deployment (tracked by #30 / #67; needs operator approval for spend).
2. Wire its endpoint/deployment via the workflow inputs
   (`OPENAI_ENDPOINT` / `OPENAI_TTS_DEPLOYMENT`) and grant the Function App
   managed identity `Cognitive Services User` on the new account (no keys).
3. Re-run Deploy Azure; confirm `/api/generate` smoke stays green
   (HTTP 202, `job_id`, `manifest_url`, `errors=[]`, no secret leakage).
4. Soak for one full generation cycle on the production resource.
5. **Flag the operator**, then delete the bakeoff account:
   `az cognitiveservices account delete -n podcaster-openai-bakeoff-20260609 -g squadscope-podcaster`
   followed by `... account purge` if soft-delete is enabled.

## 3. Sweep for other orphaned bakeoff artifacts

- **Cognitive Services / RG resources:** only the in-use OpenAI bakeoff remains
  (see §1 query). No orphaned bakeoff resources.
- **Storage `podcaster-artifacts` → `bakeoff/tts/2026-06-09/`:** the six sample
  MP3s and two manifests are the bakeoff listening evidence for #4 and are
  **retained pending human listening notes / sign-off** on that issue. Do not
  delete until #4 is closed.
- **Storage `function-packages`:** run-from-package deploy blobs (~30). Normal
  deploy churn; the live package is referenced by `WEBSITE_RUN_FROM_PACKAGE`.
  Out of scope here — a future lifecycle/retention policy can prune old
  packages safely (do not delete the currently referenced blob).

## Guardrail

Never delete a resource, deployment, or blob that is actively referenced by
code, a workflow, an app setting, or an open issue's acceptance evidence.
Cost-saving cleanup, but correctness first.
