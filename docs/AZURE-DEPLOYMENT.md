# Azure Deployment Prerequisites & Runbook

This document specifies the exact Azure setup required before deploying Podcaster, and the runbook to execute deployment.

## Pre-Deployment Checklist

### Azure Subscription & Access

- [ ] **Azure subscription exists** and you have Contributor or equivalent role.
- [ ] **Test access:** Run `az account list` to verify credentials are configured.
- [ ] **Target region decided** (e.g., `eastus`, `westus2`, `westeurope`). Must be a valid Azure region.

### Naming & Resource Constraints

- [ ] **Resource group name decided** (e.g., `podcaster-prod`, `podcaster-staging`).
  - Constraint: 1–90 characters; alphanumeric, underscore, hyphen allowed; globally unique within subscription.
  - If it doesn't exist, the deploy workflow creates it.

- [ ] **Function App name is globally unique** (Azure enforces global uniqueness for `.azurewebsites.net` domain).
  - The workflow derives a deterministic default from `AZURE_RESOURCE_GROUP` and `AZURE_SUBSCRIPTION_ID`.
  - Example default shape: `podcaster-podcaster-prod-<12-hex-hash>`.
  - Workflow constraint: 2–35 characters; letters, digits, hyphens allowed; must start and end with a letter or digit.
  - Azure allows longer Function App names, but Podcaster reserves room for derived App Service Plan and Log Analytics names (`-plan`, `-law`) so deployment cannot fail on resource-name length limits.
  - If the derived name conflicts globally, set `AZURE_FUNCTION_APP_NAME` as an override.

- [ ] **Storage Account name is globally unique, lowercase, alphanumeric only**.
  - The workflow derives a deterministic default from `AZURE_RESOURCE_GROUP` and `AZURE_SUBSCRIPTION_ID`.
  - Example default shape: `podcasterprod<12hex>`.
  - Constraint: 3–24 characters; lowercase letters and digits only.
  - If the derived name conflicts globally, set `AZURE_STORAGE_ACCOUNT_NAME` as an override.

- [ ] **Avoid naming conflicts:** If any name is already in use in your subscription, choose a different name.

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

The deployment also creates Azure role assignments for the Function App managed identity and for the GitHub Actions deploy identity. The deploy identity therefore needs permission to write role assignments at the target scope during bootstrap (for example `Owner`, or `Contributor` plus `User Access Administrator`). After the first successful deployment, keep the scope as narrow as possible.

For code deployment, the workflow uses Entra-authenticated storage data-plane operations, not account keys. The Bicep template assigns the GitHub Actions service principal `Storage Blob Data Contributor` on the deployed Storage Account so it can:

- upload `app.zip` to the private `function-packages` container with `--auth-mode login`
- set the Function App to read that private package blob with its managed identity
- avoid using or printing Storage Account keys

### GitHub Environment Configuration

#### Step 1: Add `prod` Environment Variables

The deploy workflow uses the GitHub environment named exactly `prod`. Go to **Settings > Environments > prod > Environment variables** and create:

```
AZURE_CLIENT_ID=<Application ID from step 1>
AZURE_TENANT_ID=<Tenant ID from step 3>
AZURE_SUBSCRIPTION_ID=<Subscription ID from step 3>
AZURE_LOCATION=eastus
AZURE_RESOURCE_GROUP=podcaster-prod
```

**Important:** These are **environment variables**, not secrets. They are non-sensitive, but the workflow only checks that they are present and does not print their values.

Optional override variables:

```text
AZURE_FUNCTION_APP_NAME=podcaster-app-prod
AZURE_STORAGE_ACCOUNT_NAME=podcasterstgprod
```

If omitted, the workflow computes safe deterministic defaults. Function App override names must be 2–35 characters so derived Azure resource names stay compliant. Function App names are globally unique in Azure DNS and Storage Account names are globally unique across Azure; a rare collision still requires setting the override variable.

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
az group exists --name podcaster-prod
# Returns: false (good, it will be created) or true (already exists, safe to reuse)

# 3. If using an override, verify Function App name is globally unique
az functionapp list --query "[].name" | grep podcaster-app-prod
# Should return empty (good, name is available)

# 4. If using an override, verify Storage Account name is globally unique
az storage account list --query "[].name" | grep podcasterstgprod
# Should return empty (good, name is available)

# 5. Verify GitHub prod environment variables are set (names only)
gh variable list --repo jmservera/SquadScope-Podcaster --env prod
# Should show AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, AZURE_LOCATION, AZURE_RESOURCE_GROUP
# AZURE_FUNCTION_APP_NAME and AZURE_STORAGE_ACCOUNT_NAME are optional overrides.

# 6. Optionally verify GitHub prod environment secret names are set (values are never shown)
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
  -f sync_squadscope=false
```

**Or via GitHub UI:**
1. Go to **SquadScope-Podcaster > Actions > Deploy Azure**.
2. Click **Run workflow**.
3. **Inputs:**
   - `sync_squadscope`: Use `true` only when `SQUADSCOPE_SYNC_TOKEN` is configured and you want the endpoint/key pushed to SquadScope. If `PODCASTER_API_KEY` is absent and you need SquadScope to call this deployment, use `true` during the same run.
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
- ✓ Validate configuration (required variables present; optional names/key may be derived)
- ✓ Resolve deployment names and API key (API key value is masked and not printed)
- ✓ Azure login (OIDC federated identity)
- ✓ Create resource group
- ✓ Deploy infrastructure (Bicep)
  - Creates Storage Account, Function App, App Insights, Log Analytics
  - Assigns managed identity to Function App
  - Outputs endpoint URL
- ✓ Set up Python 3.11 and install dependencies into `.python_packages/lib/site-packages`
- ✓ Build `app.zip` with the Function App, host file, `podcaster/`, and Python dependencies
- ✓ Upload `app.zip` to the private `function-packages` blob container with OIDC/Entra auth
- ✓ Set `WEBSITE_RUN_FROM_PACKAGE` to the private package blob URL, enable managed-identity package access, and restart the Function App
- ✓ Print integration summary:
  ```
  Endpoint: https://podcaster-app-prod.azurewebsites.net/api/generate
  API key: configured as an app setting; not printed.
  ```

#### Step 3: Verify the Deployment

```bash
# 1. Check that the Function App is running
az functionapp show \
  --resource-group podcaster-prod \
  --name podcaster-app-prod \
  --query state

# Should output: "Running"

# 2. Test the endpoint
PODCASTER_ENDPOINT="https://podcaster-app-prod.azurewebsites.net/api/generate"
PODCASTER_API_KEY="<your-api-key-or-synced-secret>"

curl -X POST "$PODCASTER_ENDPOINT" \
  -H 'content-type: application/json' \
  -H "x-podcaster-api-key: $PODCASTER_API_KEY" \
  -d '{
    "week": "2026-W23",
    "article_url": "https://example.com/article",
    "dry_run": true
  }'

# Should return HTTP 202 with response:
# {
#   "job_id": "podcast-2026-W23-...",
#   "status": "dry_run",
#   "manifest_url": "https://...",
#   ...
# }
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

It discovers the Function App (→ `/api/generate` URL), the Function App's
`PODCASTER_API_KEY` app setting (the value SquadScope must send as
`x-podcaster-api-key`), the storage account, and any Azure OpenAI / Cognitive
Services account (endpoint + key for the `/api/generate` generation work in #60).
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

### Subsequent Deployments (Code Updates)

#### Step 1: Update Code and Commit

```bash
cd /home/azureuser/source/SquadScope-Podcaster
# Make code changes
git add <files>
git commit -m "Update podcaster function" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
git push origin <feature-branch>
```

#### Step 2: Trigger Deploy (Automatic or Manual)

The deploy workflow can be triggered:
- **Manually:** `gh workflow run deploy-azure.yml -R jmservera/SquadScope-Podcaster --ref main`
- **On push (optional):** Configure a workflow trigger in `.github/workflows/deploy-azure.yml` to run on `push: branches: [main]`.

#### Step 3: Verify Deployment

Same as Step 3 from "First Deployment"; test the endpoint to confirm the new code is running.

### Function App Package Deployment Design

Podcaster intentionally does **not** use `az functionapp deployment source config-zip` or `az webapp deploy --type zip`. Those deployment APIs failed in the target Function App environment. The durable workflow uses the pattern that was verified manually:

1. Build dependencies on the GitHub runner with Python 3.11, matching the Function App runtime.
2. Create `app.zip` locally, excluding git metadata, local settings, virtualenvs, caches, `.env*`, and bytecode.
3. Upload the package to the private `function-packages` container using `az storage blob upload --auth-mode login`.
4. Set `WEBSITE_RUN_FROM_PACKAGE` to the private blob URL and set `WEBSITE_USE_MANAGED_IDENTITY=true` plus `WEBSITE_RUN_FROM_PACKAGE_BLOB_MI_RESOURCE_ID=SystemAssigned` so the Function App reads the package with its system-assigned managed identity.
5. Restart the Function App.

The package URL does not include a SAS token. Blob public access remains disabled, and the Function App managed identity already has storage data-plane access from the Bicep deployment.

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

### Function App Deployment Fails

**Error:** `ERROR: Name ... already exists in global Azure storage.`

**Solution:**
1. The Function App or Storage Account name is already in use.
2. Choose a different name (e.g., append `-v2` or a timestamp).
3. Set `AZURE_FUNCTION_APP_NAME` or `AZURE_STORAGE_ACCOUNT_NAME` in the `prod` environment and re-deploy.

**Error:** package upload or managed-identity package read fails with authorization errors.

**Solution:**
1. Verify the deploy identity has role-assignment write permission for bootstrap.
2. Verify the Storage Account has a `Storage Blob Data Contributor` assignment for the GitHub Actions service principal.
3. Verify the Function App managed identity has storage blob read access on the package container or storage account.
4. Wait a few minutes for Azure RBAC propagation, then re-run the workflow.

**Error:** `config-zip` or `az webapp deploy --type zip` examples fail.

**Solution:** Do not use those paths for this app. Use `deploy-azure.yml`, which deploys by private run-from-package blob.

### Endpoint Returns 401 Unauthorized

**Error:** `{"error": "Invalid API key"}`

**Solution:**
1. Verify the `x-podcaster-api-key` header matches the `PODCASTER_API_KEY` secret.
2. Verify the secret is correctly set in GitHub (not leaked or truncated).
3. Check Application Insights logs:
   ```bash
   az monitor app-insights query \
     --app podcaster-app-prod \
     --analytics-query "traces | where message contains 'auth' | project timestamp, message" \
     --resource-group podcaster-prod
   ```

### Endpoint Timeout (>30 seconds)

**Issue:** The Function App times out on requests.

**Solution:**
1. Check Application Insights for slow requests:
   ```bash
   az monitor app-insights query \
     --app podcaster-app-prod \
     --analytics-query "requests | where duration > 30000 | project timestamp, name, duration" \
     --resource-group podcaster-prod
   ```
2. Check for errors in the logs.
3. Verify the Function App has enough memory (Y1 tier is the minimum; consider upgrading for production).

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

The production `/api/generate` path uses **Azure OpenAI** for the two-voice
"Claracle" episode (host A = `fable`, host B = `alloy`), per the provider decision
recorded in #4. These resources live in `infra/openai.bicep` and are deployed by the
conditional `module openAi` in `infra/main.bicep`.

### Opt-in by design

The OpenAI account, model deployments, and role assignment are **off by default**
(`deployOpenAi=false`). This keeps the core storage + Function App deployment green in
regions/SKUs where the selected TTS model is unavailable. Enable it explicitly:

```bash
# From the Actions UI: Run workflow → Deploy Azure → deploy_openai = true
# Or with the Azure CLI:
az deployment group create \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters deployOpenAi=true \
  --parameters podcasterApiKey="$PODCASTER_API_KEY" \
  --parameters location="$AZURE_LOCATION"
```

> **Region/model availability:** OpenAI TTS (`gpt-4o-mini-tts`, voices `fable`/`alloy`)
> and the script model (`gpt-4o-mini`) are only available in some Azure regions. Before
> enabling, confirm the configured `ttsModelName`/`ttsModelVersion` and
> `chatModelName`/`chatModelVersion` are offered in your `AZURE_LOCATION`, or override
> the model parameters. Deploying into an unsupported region will fail the run.

### What gets provisioned

| Resource | Purpose |
|----------|---------|
| `Microsoft.CognitiveServices/accounts` (kind `OpenAI`) | Azure OpenAI account with a custom subdomain for Entra ID auth |
| TTS model deployment (`tts`) | OpenAI TTS model that provides the `fable`/`alloy` voices |
| Chat model deployment (`chat`) | Writes the two-voice Claracle conversation script |
| Role assignment (`Cognitive Services OpenAI User`) | Grants the Function App's managed identity data-plane access |

The Function App receives `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_TTS_DEPLOYMENT`,
`AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_TTS_VOICE_HOST_A/B`, and
`AZURE_OPENAI_AUTH_MODE=managed_identity` as app settings.

### Authentication & secrets

- **Managed identity only.** The account sets `disableLocalAuth: true`; the Function App
  authenticates with its system-assigned identity via the `Cognitive Services OpenAI User`
  role. **No account key is created, read, stored in app settings, logged, or emitted as a
  deployment output.**
- Checkov `CKV_AZURE_134` (disable public network access) is intentionally deferred: the
  Consumption (Y1) Function App reaches the account over the public endpoint and cannot use
  VNet integration / private endpoints. Managed-identity-only auth remains enforced.

### TTS stays blocked until editorial review

Provisioning this infrastructure does **not** enable publishable audio. Non-dry-run TTS
synthesis and any publication path remain blocked behind the human/editorial review gates
(see `docs/editorial-standards.md` and the review-gate workflow). The deployed endpoint
continues to return the reviewed placeholder packet until #60 wires the production
generation path and a reviewer records an approved decision.

---

## Cost Estimation

### Azure Resources (Monthly)

| Resource | Tier | Cost (Estimate) | Notes |
|----------|------|-----------------|-------|
| Function App | Y1 (Dynamic) | $0.17 | Pay-per-execution; 1M free invocations/month |
| Storage Account | Standard LRS | ~$5–10 | Depends on data stored (staging artifacts) |
| App Insights | Free tier | $0 | 1 GB/month included; overage $0.80/GB |
| Log Analytics | PerGB2018 | ~$5–15 | Depends on log volume (30-day retention) |
| **Total (est.)** | | ~$15–40 | Assuming light traffic and <10 GB storage |

### Cost Optimization

- **Function App:** Y1 is serverless and free-tier friendly; no cost if unused.
- **Storage:** Clean up expired artifacts weekly (implement lifecycle management).
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
