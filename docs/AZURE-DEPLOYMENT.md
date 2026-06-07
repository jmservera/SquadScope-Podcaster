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
  - Example: `podcaster-app-prod-abc123` (append a random suffix if needed).
  - Constraint: 1–60 characters; lowercase letters, digits, hyphens allowed; must start with letter or digit.
  - **Test:** Run `az functionapp list --query "[].name"` to see existing names.

- [ ] **Storage Account name is globally unique, lowercase, alphanumeric only**.
  - Example: `podcasterstgprod123` (no hyphens or underscores in storage names).
  - Constraint: 3–24 characters; lowercase letters and digits only.
  - **Test:** Run `az storage account list --query "[].name"` to see existing names.

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
   - **Entity type:** `Branch`
   - **GitHub branch name:** `main` (or the branch you want to deploy from)
5. Click **Add**.
6. **Verify:** The credential appears in the list with a preview of the subject identifier (e.g., `repo:jmservera/SquadScope-Podcaster:ref:refs/heads/main`).

#### Step 3: Get Azure Subscription & Tenant IDs

1. Go to **Azure Portal > Subscriptions**.
2. Select the subscription where Podcaster will be deployed.
3. **Copy the Subscription ID** → this is `AZURE_SUBSCRIPTION_ID`.
4. Go to **Microsoft Entra ID > Overview**.
5. **Copy the Tenant ID** → this is `AZURE_TENANT_ID`.

#### Step 4: Grant the App Registration Subscription Access

1. Go to **Azure Portal > Subscriptions > Your Subscription > Access control (IAM)**.
2. Click **+ Add > Add role assignment**.
3. **Role:** `Contributor` (or `Owner` if you need to create resource groups and manage all resources).
4. **Members > Select members:** Search for the app registration name (e.g., `Podcaster-GitHub-Actions`).
5. Click **Assign**.

### GitHub Repository Configuration

#### Step 1: Add Repository Variables

Go to **Settings > Secrets and variables > Variables** and create:

```
AZURE_CLIENT_ID=<Application ID from step 1>
AZURE_TENANT_ID=<Tenant ID from step 3>
AZURE_SUBSCRIPTION_ID=<Subscription ID from step 3>
AZURE_LOCATION=eastus
AZURE_RESOURCE_GROUP=podcaster-prod
AZURE_FUNCTION_APP_NAME=podcaster-app-prod
AZURE_STORAGE_ACCOUNT_NAME=podcasterstgprod
```

**Important:** These are **variables**, not secrets. They are non-sensitive and appear in workflow summaries.

#### Step 2: Add Repository Secrets

Go to **Settings > Secrets and variables > Secrets** and create:

```
PODCASTER_API_KEY=<randomly-generated-key-at-least-32-characters>
```

**Generation:** Use a secure random generator:
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
8. **Copy the token** and paste into the Podcaster repository's `SQUADSCOPE_SYNC_TOKEN` secret.
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

# 3. Verify Function App name is globally unique
az functionapp list --query "[].name" | grep podcaster-app-prod
# Should return empty (good, name is available)

# 4. Verify Storage Account name is globally unique
az storage account list --query "[].name" | grep podcasterstgprod
# Should return empty (good, name is available)

# 5. Verify GitHub variables are set
gh variable list --repo jmservera/SquadScope-Podcaster
# Should show AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, AZURE_LOCATION, AZURE_RESOURCE_GROUP, AZURE_FUNCTION_APP_NAME, AZURE_STORAGE_ACCOUNT_NAME

# 6. Verify GitHub secret is set
gh secret list --repo jmservera/SquadScope-Podcaster | grep PODCASTER_API_KEY
# Should show PODCASTER_API_KEY in the list (value not shown)
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
   - `sync_squadscope`: Leave as `false` for the first deployment.
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
- ✓ Validate configuration (all variables/secrets present)
- ✓ Azure login (OIDC federated identity)
- ✓ Create resource group
- ✓ Deploy infrastructure (Bicep)
  - Creates Storage Account, Function App, App Insights, Log Analytics
  - Assigns managed identity to Function App
  - Outputs endpoint URL
- ✓ Set up Python and install dependencies
- ✓ Deploy Function App package (ZIP)
- ✓ Print integration summary:
  ```
  Endpoint: https://podcaster-app-prod.azurewebsites.net/api/generate
  API key: stored in secrets; not printed.
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
PODCASTER_API_KEY="<your-api-key>"

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

If `SQUADSCOPE_SYNC_TOKEN` is not configured, manually set up SquadScope:

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

### Subsequent Deployments (Code Updates)

#### Step 1: Update Code and Commit

```bash
cd /home/azureuser/source/SquadScope-Podcaster
# Make code changes
git add <files>
git commit -m "Update podcaster function" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
git push origin main
```

#### Step 2: Trigger Deploy (Automatic or Manual)

The deploy workflow can be triggered:
- **Manually:** `gh workflow run deploy-azure.yml -R jmservera/SquadScope-Podcaster`
- **On push (optional):** Configure a workflow trigger in `.github/workflows/deploy-azure.yml` to run on `push: branches: [main]`.

#### Step 3: Verify Deployment

Same as Step 3 from "First Deployment"; test the endpoint to confirm the new code is running.

### Sync to SquadScope (Optional, After First Deploy)

After the first deployment, if `SQUADSCOPE_SYNC_TOKEN` is configured, you can automate the sync:

```bash
gh workflow run deploy-azure.yml \
  -R jmservera/SquadScope-Podcaster \
  -f sync_squadscope=true
```

The workflow will:
1. Deploy Podcaster (if needed).
2. Sync `PODCASTER_ENDPOINT` variable and `PODCASTER_API_KEY` secret to SquadScope automatically.

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
3. Update the GitHub variables and re-deploy.

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

1. **Rotate API keys quarterly:** Generate a new `PODCASTER_API_KEY`, update GitHub secret, re-deploy.
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
