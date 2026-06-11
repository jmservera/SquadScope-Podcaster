# Azure Deployment Prerequisites & Runbook

This document specifies the exact Azure setup required before deploying Podcaster, and the runbook to execute deployment.

> **Architecture:** Podcaster uses an **ACA-only** (Azure Container Apps) architecture
> as of PR #112. The Function App was removed. All resources deploy to **eastus2**
> (required for `gpt-4o-mini-tts` model availability). See `docs/architecture.md` for
> the full system design.

## Pre-Deployment Checklist

### Azure Subscription & Access

- [ ] **Azure subscription exists** and you have Contributor or equivalent role (plus `User Access Administrator` for role assignments during bootstrap).
- [ ] **Test access:** Run `az account list` to verify credentials are configured.
- [ ] **Target region:** `eastus2` (required for `gpt-4o-mini-tts` + ACA). Do not change unless you verify both ACA and the TTS model are available in the alternative region.

### Naming & Resource Constraints

- [ ] **Resource group name decided** (e.g., `squadscope-podcaster`).
  - Constraint: 1–90 characters; alphanumeric, underscore, hyphen allowed; globally unique within subscription.
  - If it doesn't exist, the deploy workflow creates it.

- [ ] **Storage Account name is globally unique, lowercase, alphanumeric only**.
  - The workflow derives a deterministic default from `AZURE_RESOURCE_GROUP` and `AZURE_SUBSCRIPTION_ID`.
  - Constraint: 3–24 characters; lowercase letters and digits only.
  - If the derived name conflicts globally, set `AZURE_STORAGE_ACCOUNT_NAME` as an override.

- [ ] **Avoid naming conflicts:** If any name is already in use in your subscription, choose a different name.

### Provisioned Resources

The Bicep template (`infra/main.bicep`) deploys:

| Resource | Purpose |
|----------|---------|
| Storage Account | Artifact staging (`podcaster-artifacts` container) + synthesis queue |
| Azure OpenAI (Cognitive Services) | TTS (`gpt-4o-mini-tts`, deployment `tts`) + chat (`gpt-4o-mini`, deployment `chat`) |
| Container Apps Environment | Hosts the synthesis job |
| Container Apps Job (queue-triggered) | Full episode pipeline: script → TTS → ffmpeg stitch → validate → stage |
| User-assigned Managed Identity | Identity-only auth to Storage (Blob + Queue) and Azure OpenAI (no keys) |
| Log Analytics + Application Insights | Observability |

All resources are co-located in eastus2 to minimize latency.

### OIDC Federation Setup (GitHub ↔ Azure)

**Goal:** GitHub Actions authenticate to Azure via OIDC, not long-lived credentials.

#### Step 1: Create or Identify an App Registration

1. Go to **Azure Portal > Microsoft Entra ID > App registrations**.
2. **Option A:** Create a new app registration:
   - Click **+ New registration**.
   - **Name:** `Podcaster-GitHub-Actions` (or similar).
   - **Supported account types:** Accounts in this organizational directory only.
   - Click **Register**.
3. **Option B:** Use an existing app registration for GitHub Actions (if available).
4. **Note the Application ID** (also called "Client ID"):
   - Go to the app registration's **Overview** tab.
   - Copy the **Application (client) ID** → this is `AZURE_CLIENT_ID`.

#### Step 2: Add Federated Credentials

1. In the app registration, go to **Certificates & secrets > Federated credentials**.
2. Click **+ Add credential**.
3. **Scenario:** GitHub Actions deploying Azure resources.
4. Fill in:
   - **Organization:** `jmservera`
   - **Repository:** `SquadScope-Podcaster`
   - **Entity type:** `Environment`
   - **GitHub environment name:** `prod`
5. Click **Add**.
6. **Verify:** The credential appears in the list with a preview of the subject identifier `repo:jmservera/SquadScope-Podcaster:environment:prod`.

#### Step 3: Get Azure Subscription & Tenant IDs

1. Go to **Azure Portal > Subscriptions**.
2. Select the subscription where Podcaster will be deployed.
3. **Copy the Subscription ID** → this is `AZURE_SUBSCRIPTION_ID`.
4. Go to **Microsoft Entra ID > Overview**.
5. **Copy the Tenant ID** → this is `AZURE_TENANT_ID`.

#### Step 4: Grant the App Registration Azure Access

1. Go to **Azure Portal > Subscriptions > Your Subscription > Access control (IAM)**.
2. Click **+ Add > Add role assignment**.
3. **Role:** `Contributor` (or `Owner` if you need to create resource groups and manage all resources).
4. **Members > Select members:** Search for the app registration name (e.g., `Podcaster-GitHub-Actions`).
5. Click **Assign**.

The deployment also creates Azure role assignments for the synthesis job's user-assigned managed identity. The deploy identity therefore needs permission to write role assignments at the target scope during bootstrap (for example `Owner`, or `Contributor` plus `User Access Administrator`). After the first successful deployment, keep the scope as narrow as possible.

The deploy identity also receives `Storage Blob Data Contributor` on the Storage Account so it can upload artifacts during CI/CD if needed.

### GitHub Environment Configuration

#### Step 1: Add `prod` Environment Variables

The deploy workflow uses the GitHub environment named exactly `prod`. Go to **Settings > Environments > prod > Environment variables** and create:

```
AZURE_CLIENT_ID=<Application ID from step 1>
AZURE_TENANT_ID=<Tenant ID from step 3>
AZURE_SUBSCRIPTION_ID=<Subscription ID from step 3>
AZURE_LOCATION=eastus2
AZURE_RESOURCE_GROUP=squadscope-podcaster
```

**Important:** These are **environment variables**, not secrets. They are non-sensitive, but the workflow only checks that they are present and does not print their values.

Optional override variable:

```text
AZURE_STORAGE_ACCOUNT_NAME=podcasterstgprod
```

If omitted, the workflow computes a safe deterministic default. Storage Account names are globally unique across Azure; a rare collision requires setting the override variable.

#### Step 2: Add `prod` Environment Secrets

Go to **Settings > Environments > prod > Environment secrets** and optionally create:

```
PODCASTER_API_KEY=<randomly-generated-key-at-least-32-characters>
```

If `PODCASTER_API_KEY` is absent, the deploy workflow generates a 256-bit key with OpenSSL, masks it immediately, and sets it as the Function App app setting without printing it. That generated value is not recoverable from logs; use `sync_squadscope=true` with `SQUADSCOPE_SYNC_TOKEN` during that deployment to push it to SquadScope, or set your own `PODCASTER_API_KEY` secret before deploying when manual handoff/rotation is required.

**Manual generation:** Use a secure random generator:
```bash
# Option 1: OpenSSL
openssl rand -hex 32

# Option 2: Python
python3 -c "import secrets; print(secrets.token_hex(32))"

# Option 3: /dev/urandom
head -c 32 /dev/urandom | base64
```

**Optional secret** (only if syncing to SquadScope):

```
SQUADSCOPE_SYNC_TOKEN=<fine-grained-PAT-with-secrets:write-and-variables:write>
```

**How to create `SQUADSCOPE_SYNC_TOKEN`:**
1. Go to **GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens**.
2. Click **Generate new token**.
3. **Token name:** `Podcaster-Sync-to-SquadScope`.
4. **Resource owner:** Select `jmservera`.
5. **Repository access:** Select `jmservera/SquadScope` (the caller repo).
6. **Permissions:**
   - **Secrets:** Read and write
   - **Variables:** Read and write
7. Click **Generate token**.
8. **Copy the token** and paste into the Podcaster `prod` environment's `SQUADSCOPE_SYNC_TOKEN` secret.
9. **Store the token securely** (GitHub shows it only once).

### Pre-Flight Validation

Before running the deploy workflow, verify all prerequisites are in place:

```bash
# 1. Check Azure CLI is authenticated
az account show
# Should print your subscription details

# 2. Verify resource group name is available (or will be created)
az group exists --name squadscope-podcaster
# Returns: false (good, it will be created) or true (already exists, safe to reuse)

# 3. If using an override, verify Storage Account name is globally unique
az storage account check-name --name podcasterstgprod
# Should return nameAvailable: true

# 4. Verify GitHub prod environment variables are set (names only)
gh variable list --repo jmservera/SquadScope-Podcaster --env prod
# Should show AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, AZURE_LOCATION, AZURE_RESOURCE_GROUP

# 5. Optionally verify GitHub prod environment secret names are set (values are never shown)
gh secret list --repo jmservera/SquadScope-Podcaster --env prod | grep PODCASTER_API_KEY
# If absent, deployment generates and masks a transient key.
```

---

## Deployment Runbook

### First Deployment (Initial Setup)

#### Step 1: Trigger the Deploy Workflow

```bash
gh workflow run deploy-azure.yml \
  -R jmservera/SquadScope-Podcaster \
  -f sync_squadscope=false \
  -f deploy_openai=true
```

**Or via GitHub UI:**
1. Go to **SquadScope-Podcaster > Actions > Deploy Azure**.
2. Click **Run workflow**.
3. **Inputs:**
   - `sync_squadscope`: Use `true` only when `SQUADSCOPE_SYNC_TOKEN` is configured and you want the API key pushed to SquadScope.
   - `deploy_openai`: `true` (default) — provisions the Azure OpenAI TTS + chat infrastructure.
4. Click **Run workflow**.

#### Step 2: Monitor the Workflow Run

```bash
# Watch the workflow in real-time
gh run watch -R jmservera/SquadScope-Podcaster

# Or check the run log
gh run view -R jmservera/SquadScope-Podcaster --log
```

**Expected output:**
- ✓ Checkout code
- ✓ Build Bicep templates (validates syntax)
- ✓ Validate configuration (required variables present)
- ✓ Resolve deployment names and API key (API key value is masked)
- ✓ Azure login (OIDC federated identity)
- ✓ Ensure resource group exists
- ✓ Deploy infrastructure (Bicep)
  - Creates Storage Account, Azure OpenAI, Container Apps Environment + Job, Managed Identity, observability stack
  - Assigns managed identity roles (Storage Blob/Queue, Cognitive Services OpenAI User)
- ✓ Optionally sync values to SquadScope

#### Step 3: Verify the Deployment

```bash
# 1. Check that the Container Apps Job exists
az containerapp job show \
  --resource-group squadscope-podcaster \
  --name <synth-job-name> \
  --query "{name:name, provisioningState:properties.provisioningState}" \
  -o table

# 2. Check the Azure OpenAI endpoint is healthy
az cognitiveservices account show \
  --name <openai-account-name> \
  --resource-group squadscope-podcaster \
  --query "{endpoint:properties.endpoint, state:properties.provisioningState}" \
  -o table

# 3. Verify model deployments
az cognitiveservices account deployment list \
  --name <openai-account-name> \
  --resource-group squadscope-podcaster \
  --query "[].{name:name, model:properties.model.name}" \
  -o table
# Should show: tts (gpt-4o-mini-tts) and chat (gpt-4o-mini)
```

#### Step 4: Verify SquadScope Integration (Manual Setup)

If `SQUADSCOPE_SYNC_TOKEN` is not configured, manually set up SquadScope. Manual setup requires a stable API key that you generated and stored as the Podcaster `PODCASTER_API_KEY` secret before deploy; a workflow-generated key is intentionally not printed or recoverable.

1. In **SquadScope repository settings** (`jmservera/SquadScope`):
   - Go to **Settings > Secrets and variables > Variables**.
   - Create `PODCASTER_ENDPOINT` with the endpoint from step 3 output.
   - Create the secret `PODCASTER_API_KEY` (same value as Podcaster's secret).

2. Verify SquadScope can read the values:
   ```bash
   gh variable get PODCASTER_ENDPOINT --repo jmservera/SquadScope
   gh secret list --repo jmservera/SquadScope | grep PODCASTER_API_KEY
   ```

3. Test from SquadScope CI:
   - Add a test workflow step or run manually:
   ```bash
   curl -X POST "${{ vars.PODCASTER_ENDPOINT }}" \
     -H 'content-type: application/json' \
     -H 'x-podcaster-api-key: ${{ secrets.PODCASTER_API_KEY }}' \
     -d '{"week":"2026-W23","article_url":"https://example.com"}'
   ```

#### Step 5: Discover values automatically (recommended)

Instead of looking up names by hand, use the helper to discover the deployed
resources at the resource-group level and emit ready-to-run `gh` commands with
the real resolved values:

```bash
# Defaults: resource group 'squadscope-podcaster',
# SquadScope repo 'jmservera/SquadScope', Podcaster repo 'jmservera/SquadScope-Podcaster'
scripts/get-podcaster-values.sh

# Or target a specific resource group / repos
scripts/get-podcaster-values.sh \
  --resource-group squadscope-podcaster \
  --squadscope-repo jmservera/SquadScope \
  --podcaster-repo jmservera/SquadScope-Podcaster

# Write the commands to a local (gitignored) file instead of stdout
scripts/get-podcaster-values.sh --out ./podcaster-secrets.local.sh
```

It discovers the Container Apps Job, the storage account, and the Azure OpenAI
account (endpoint + key for TTS generation).
It then prints, for review before you run them:

```bash
gh variable set PODCASTER_ENDPOINT --repo jmservera/SquadScope --body '<generate-url>'
gh secret set   PODCASTER_API_KEY  --repo jmservera/SquadScope --body '<api-key>'
gh variable set AZURE_OPENAI_ENDPOINT --repo jmservera/SquadScope-Podcaster --body '<endpoint>'
gh secret set   AZURE_OPENAI_API_KEY  --repo jmservera/SquadScope-Podcaster --body '<key>'
```

**Safety:** this is a local operator tool. It prints secret values to your
terminal only — it never writes them to a committed file, a CI log, or
`$GITHUB_OUTPUT`, and it refuses to run inside GitHub Actions (unless
`--force-ci` is passed). Requires `az` authenticated (`az login`) and `gh`
authenticated to run the emitted commands. If you use `--out`, the file is
created with owner-only permissions and contains real secrets — keep it out of
git and delete it when done.

### Subsequent Deployments (Infrastructure Updates)

#### Step 1: Update Bicep and Commit

```bash
cd /home/azureuser/source/SquadScope-Podcaster
# Make infrastructure changes to infra/main.bicep or infra/modules/
git add infra/
git commit -m "infra: update deployment" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
git push origin <feature-branch>
```

#### Step 2: Trigger Deploy (Manual)

The deploy workflow is triggered manually (workflow_dispatch):
- `gh workflow run deploy-azure.yml -R jmservera/SquadScope-Podcaster --ref main`

#### Step 3: Verify Deployment

Same as Step 3 from "First Deployment"; verify the ACA Job and OpenAI resources are healthy.

### Container Apps Job Deployment Design

Podcaster uses a **queue-triggered Azure Container Apps Job** as the sole compute resource. The synthesis container image (with ffmpeg baked in) is referenced by the Bicep template's `synthesisImage` parameter. The job:

1. Scales to zero when idle (no cost when no synthesis work is queued).
2. Is triggered by messages on the `synthesis-jobs` Storage Queue.
3. Authenticates to Azure OpenAI and Storage via its user-assigned managed identity (no keys).
4. Runs the full episode pipeline: script generation → TTS synthesis → ffmpeg audio assembly → validation → artifact staging.

The container image is published via the `synthesis-image-publish.yml` workflow.

### Sync to SquadScope (Optional, After First Deploy)

If `SQUADSCOPE_SYNC_TOKEN` is configured, you can automate the sync:

```bash
gh workflow run deploy-azure.yml \
  -R jmservera/SquadScope-Podcaster \
  -f sync_squadscope=true
```

The workflow will:
1. Deploy Podcaster (if needed).
2. Sync `PODCASTER_ENDPOINT` variable and `PODCASTER_API_KEY` secret to SquadScope automatically. This works with either a preconfigured Podcaster secret or a key generated during the same deployment.

---

## Troubleshooting

### OIDC Login Fails

**Error:** `ERROR: AADSTS700016: Application ... is not authorized to use the requested scope.`

**Solution:**
1. Verify the federated credential is configured (Azure > App registration > Federated credentials).
2. Verify the app registration has `Contributor` role on the subscription.
3. Re-run the deploy workflow.

### Bicep Deployment Fails

**Error:** Model not available in region.

**Solution:**
1. `gpt-4o-mini-tts` (GlobalStandard) requires `eastus2`. Verify `AZURE_LOCATION=eastus2`.
2. Check Azure OpenAI model availability: `az cognitiveservices account list-models --name <account> -g <rg>`.

**Error:** Role assignment conflict or insufficient permissions.

**Solution:**
1. Verify the deploy identity has role-assignment write permission for bootstrap.
2. Wait a few minutes for Azure RBAC propagation, then re-run the workflow.

### Container Apps Job Not Running

**Error:** Job executions show failures or never start.

**Solution:**
1. Check the job execution logs:
   ```bash
   az containerapp job execution list \
     --name <synth-job-name> \
     --resource-group squadscope-podcaster \
     --query "[0:3].{name:name, status:properties.status}" -o table
   ```
2. Check the container logs in Log Analytics or the Container Apps Environment logs.
3. Verify the managed identity has the required roles (Storage Blob/Queue + Cognitive Services OpenAI User).

### Endpoint Returns 401 Unauthorized

**Error:** `{"error": "Invalid API key"}`

**Solution:**
1. Verify the `x-podcaster-api-key` header matches the `PODCASTER_API_KEY` secret.
2. Verify the secret is correctly set in GitHub (not leaked or truncated).

### TTS Synthesis Fails

**Error:** Azure OpenAI returns 401 or 403.

**Solution:**
1. Verify the managed identity has `Cognitive Services OpenAI User` role on the OpenAI account.
2. Verify `disableLocalAuth: true` is set on the OpenAI account (managed identity auth only).
3. Check the TTS deployment is available: `az cognitiveservices account deployment list --name <account> -g <rg>`.

---

## Destroy Deployment (If Needed)

**Warning:** This is destructive and cannot be undone. Do not run on production.

```bash
az group delete \
  --name podcaster-prod \
  --yes \
  --no-wait
```

**Verify deletion:**
```bash
az group exists --name podcaster-prod
# Should return: false
```

---

## Production TTS Infrastructure (Azure OpenAI)

The production episode pipeline uses **Azure OpenAI** for the two-voice "Claracle"
episode (host A = `fable`, host B = `alloy`), per the provider decision recorded in #4.
These resources are defined in `infra/modules/openai.bicep` and always deployed by `infra/main.bicep`.

### Always deployed

The OpenAI account and model deployments are **always provisioned** in the ACA-only
architecture. The `deploy_openai` workflow input defaults to `true`. Both `tts`
(`gpt-4o-mini-tts`) and `chat` (`gpt-4o-mini`) deployments are created.

### What gets provisioned

| Resource | Purpose |
|----------|---------|
| `Microsoft.CognitiveServices/accounts` (kind `OpenAI`) | Azure OpenAI account with a custom subdomain for Entra ID auth |
| TTS model deployment (`tts`) | OpenAI TTS model that provides the `fable`/`alloy` voices |
| Chat model deployment (`chat`) | Writes the two-voice Claracle conversation script |
| Role assignment (`Cognitive Services OpenAI User`) | Grants the synthesis job's managed identity data-plane access |

The synthesis job receives `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_TTS_DEPLOYMENT`,
`AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_TTS_VOICE_HOST_A/B`, and
`AZURE_OPENAI_AUTH_MODE=managed_identity` as environment variables.

### Authentication & secrets

- **Managed identity only.** The account sets `disableLocalAuth: true`; the synthesis job
  authenticates with its user-assigned managed identity via the `Cognitive Services OpenAI User`
  role. **No account key is created, read, stored, logged, or emitted as a deployment output.**

### TTS stays blocked until editorial review

Provisioning this infrastructure does **not** enable publishable audio. Publication
remains blocked behind the human/editorial review gates (see `docs/editorial-standards.md`
and the review-gate workflow). Audio is produced for operator review only until a reviewer
records an approved decision.

---

## Cost Estimation

### Azure Resources (Monthly)

| Resource | Tier | Cost (Estimate) | Notes |
|----------|------|-----------------|-------|
| Container Apps Job | Consumption | ~$0–5 | Scale-to-zero; pay per execution (vCPU-seconds + memory) |
| Storage Account | Standard LRS | ~$5–10 | Depends on data stored (staging artifacts + queue) |
| Azure OpenAI | S0 (pay-per-use) | ~$1–5 | TTS: $15/1M chars; Chat: $0.15/$0.60 per 1M tokens |
| App Insights | Free tier | $0 | 1 GB/month included; overage $0.80/GB |
| Log Analytics | PerGB2018 | ~$5–15 | Depends on log volume (30-day retention) |
| Container Apps Environment | Consumption | ~$0 | No charge for idle environment |
| **Total (est.)** | | ~$15–40 | Assuming weekly episode cadence and <10 GB storage |

### Cost Optimization

- **Container Apps Job:** Scales to zero when no synthesis work is queued — no cost when idle.
- **Storage:** Lifecycle policy auto-expires job artifacts after 7 days.
- **OpenAI TTS:** ~$0.08 per episode at current script lengths (~5,000 chars).
- **App Insights:** Use sampling in production to reduce ingestion cost.
- **Logging:** 30-day retention is the default; reduce if archival is not needed.

---

## Security Best Practices

1. **Rotate API keys quarterly:** Generate a new `PODCASTER_API_KEY`, update GitHub secret, re-deploy, and sync/update SquadScope.
2. **Monitor costs:** Check Azure Cost Management monthly to detect unexpected usage.
3. **Enable alerts:** Set up Application Insights alerts for 5xx errors, high latency, or auth failures.
4. **Audit logs:** Enable Azure Activity Log to track who deployed what and when.
5. **Never print secrets:** Confirm that workflow logs and outputs never contain `PODCASTER_API_KEY` or storage keys.

---

## Support & Escalation

- **Deployment issues:** Check `docs/SECURITY.md` for pre-flight validation and troubleshooting.
- **Security concerns:** Contact Hermes (Safety & Security).
- **Integration issues:** Contact Leela (Coordinator) and the SquadScope team.
- **Cost overruns:** Contact leadership for budget approval or optimization.
