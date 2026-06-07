# SquadScope Podcaster — Security & Operational Readiness

This document defines secret-handling policy, logging guarantees, and pre-release security gates for SquadScope Podcaster.

## Secret Handling Policy

### Secrets in This Repository

- **`PODCASTER_API_KEY`** (GitHub repository secret, Azure app setting)
  - The bearer token for cross-repo callers to authenticate requests.
  - Optionally stored as a GitHub secret in this repository for stable deployment/rotation.
  - If the secret is absent, the deploy workflow generates a 256-bit key during deployment, masks it immediately, and sets it only as an Azure Function App app setting.
  - Transmitted to Azure as a secure parameter and configured as a Function App setting.
  - Never logged, echoed, printed to outputs, or included in workflow summaries.
  - **Rotating:** Generate a new key, update GitHub/SquadScope secrets, re-deploy via `deploy-azure.yml`. If using generated-per-deploy keys, run the optional SquadScope sync during the same deployment.

- **`SQUADSCOPE_SYNC_TOKEN`** (GitHub repository secret, optional)
  - Fine-grained personal access token with permission to write variables and secrets in `jmservera/SquadScope`.
  - Used by the deploy workflow's optional sync step to configure `PODCASTER_ENDPOINT` (variable) and `PODCASTER_API_KEY` (secret) in the caller repository.
  - If not configured, the sync step is skipped unless `sync_squadscope=true` is explicitly requested.
  - Must have scope: `repository` and permissions: `secrets:write, variables:write`.

### Secrets Passed to SquadScope

- **`PODCASTER_ENDPOINT`** (variable in SquadScope)
  - The URL of the `/api/generate` endpoint, non-sensitive and read-safe to store as a variable.
  - Example: `https://podcaster-app.azurewebsites.net/api/generate`
  
- **`PODCASTER_API_KEY`** (secret in SquadScope)
  - The same API key configured in the Podcaster Function App.
  - Must be read from GitHub secrets, never hard-coded or committed.

### Auth Bootstrap Decision

- **Current release:** keep `x-podcaster-api-key` for compatibility and bootstrap safely. If a stable `PODCASTER_API_KEY` secret is unavailable, deployment generates a high-entropy key, masks it, and writes it only to the Function App app setting.
- **Handoff:** prefer `sync_squadscope=true` with the gated `SQUADSCOPE_SYNC_TOKEN` during the same run, or pre-create a stable key and store it in both repositories. Do not print generated keys for manual copy/paste.
- **Future hardening:** migrate SquadScope caller authentication to Azure federated identity/OIDC or EasyAuth while accepting the API-key header during a compatibility window.

### Second Federated Identity Guidance

A second Azure federated identity is **not** useful for writing GitHub secrets or variables; GitHub sync still needs a GitHub credential such as a tightly scoped fine-grained token or GitHub App installation. A second Azure federated identity is appropriate only for future keyless caller auth from `jmservera/SquadScope` to Azure. If adopted, configure:

- Azure app registration or user-assigned managed identity dedicated to the SquadScope caller.
- Federated credential subject: `repo:jmservera/SquadScope:environment:prod` (or the exact protected environment/branch used by the caller).
- Audience: `api://AzureADTokenExchange`.
- Permissions: no subscription Contributor/Owner and no Storage roles; grant only the app role or Function/App Service authentication audience needed to invoke `/api/generate`.
- Compatibility: keep `x-podcaster-api-key` until SquadScope has deployed and verified OIDC token acquisition and Podcaster validates it without logging token contents.

### Azure Resource Secrets (Deployment Only)

- **Storage Account Key**: Used by the Bicep deployment to configure the Function App's `AzureWebJobsStorage` connection string.
  - Never committed to git.
  - Never printed by workflow steps (masked by GitHub Actions).
  - Only passed as a secure parameter to Bicep.

- **`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`**: OIDC federation credentials for GitHub Actions.
  - Stored as repository variables (not secrets—these are non-sensitive in OIDC flow).
  - GitHub Actions exchanges these for a short-lived Azure access token; the token is never committed.

## Logging & Observability Policy

### What Is Safe to Log

- Request metadata: `week`, `article_url`, `article_sha256` (digest, not content)
- Request state: validation results (success/failure reason), job ID, timestamps
- Infrastructure metrics: response time, status codes, resource utilization
- API contract: request/response shape and field counts (for debugging)

### What Must Never Be Logged

- The `x-podcaster-api-key` header value or any representation of the API key
- The derived API key from app settings (PODCASTER_API_KEY)
- The `SQUADSCOPE_SYNC_TOKEN` or any fine-grained token
- Azure storage connection strings or account keys
- Request or response bodies that contain secrets (validate this during response marshaling)
- User credentials or temporary SAS URL tokens returned by storage operations

### Implementation

- Application code uses structured logging (Application Insights) without secret data.
- Python validation module does not echo or log the incoming key header.
- Response bodies never include received API keys or transient tokens.
- Workflow steps disable shell tracing (`set +x`) before any secret operations.

## Artifact Staging & Retention

### Blob Storage Access

**Current phase (local artifact staging):** The service stages deterministic placeholder artifacts under `jobs/<job_id>/`. Local runs use filesystem-backed storage when `PODCASTER_STORAGE_ACCOUNT_URL` is not configured; no Azure credentials or live TTS provider are required.

**Azure deployment path:**
- Podcaster Function App uses **managed identity** to access the Storage Account when Azure storage settings are configured.
- The Bicep template already assigns `Storage Blob Data Contributor` role to the Function App's system-assigned managed identity (line 117–125, `main.bicep`).
- **Returned URLs must be short-lived SAS URLs or private URLs brokered by managed identity**, never public storage URLs.
- The Storage Account has `allowBlobPublicAccess: false` to enforce private-by-default.

### Retention Policy

- **Artifact expiration:** `expires_at` in the response and packet metadata is set to 7 days after job creation.
- Podcaster will implement lifecycle management to automatically delete expired artifacts.
- SquadScope must download and store the publishing packet (ZIP) before expiration.
- If a URL expires and the artifact is needed, SquadScope must request regeneration by calling `/api/generate` with `force: true`.

### Access Control

- **Private:** Only SquadScope and authorized reviewers access artifacts.
- **No public listing:** The Storage Account does not enable public blob enumeration.
- **Managed identity only:** The Function App uses its assigned identity to read/write blobs; no shared keys are used in application code.

## Human Review Gate — Security Requirements

When the human review feature is implemented (backlog: `human-review-gate.md`), the following security controls apply:

### Authentication & Authorization

- Reviewers must authenticate to GitHub and have write permission to the Podcaster repository.
- Review actions (approve, reject) are recorded as commits or pull requests with reviewer attribution.
- No reviewer credentials (tokens, keys) are stored in review records.

### Audit Trail

- All review actions record:
  - Reviewer GitHub username (from GitHub Actions `github.actor` or API call context)
  - Timestamp (ISO 8601 UTC)
  - Reviewed artifact (job ID, week identifier)
  - Decision (approve, request changes, regenerate)
  - Reason (optional free-text comment)
- Audit trail is stored in the repository as immutable records or in Azure Table Storage.

### Artifact Integrity

- When regeneration is requested during review, the prior artifacts' URLs are invalidated (future: via SAS URL expiration or storage delete).
- Review status is part of the job record; a job is not eligible for manual publishing until approved.
- If regenerated audio differs from prior audio (e.g., TTS settings change), reviewers must re-approve before publishing.

### Secrets in Review

- Review comments and diffs **must not contain** the API key, caller credentials, or generated SAS URLs.
- Artifact diffs (e.g., transcript changes) are safe to review; only metadata (title, speaker) and non-authentication data appear in diffs.

## TTS Provider Disclosure & Security

Before integrating any Text-to-Speech provider, a security review must cover:

### Provider Transparency

- **Privacy:** Does the provider retain article content or voice samples? What are the data retention and deletion terms?
- **SSML safety:** If using SSML, validate that user-supplied content is sanitized to prevent injection (e.g., `<voice>` tag confusion with other attributes).
- **Geographic compliance:** Where is the generated audio stored or processed? Does it comply with SquadScope's data residency requirements?

### Cost & Rate Limiting

- **Pricing model:** Pay-per-request? Subscription? Does cost scale with article length or voice options?
- **Rate limits:** How many concurrent requests can Podcaster issue? What happens if a limit is exceeded (queueing, backoff, error)?

### Integration Checklist

- [ ] Legal review of terms of service and data processing agreement (DPA)
- [ ] Pricing and cost projections for the expected volume (articles/week, audio length/week)
- [ ] Sensitivity data classification: article content is internal; audio is non-public until manual release
- [ ] Error handling: what does Podcaster do if a TTS request fails or times out?
- [ ] Credentials: are provider API keys stored as repository secrets and never logged?
- [ ] Testing: can TTS be toggled off for local development and CI?

### Default Providers (To Evaluate)

- **Azure AI Speech (Cognitive Services)**
  - Pro: Integrated with Azure; managed identity support
  - Con: Must evaluate data retention and SSML safety

- **Other candidates:** To be evaluated post-legal-review (see `backlog/tts-bakeoff.md`)

## Endpoint Handoff to SquadScope

### SquadScope Setup (After First Deploy)

1. **In SquadScope repository settings:**
   - Create or update the `PODCASTER_ENDPOINT` variable with the deploy output (e.g., `https://podcaster-app.azurewebsites.net/api/generate`).
   - Create or update the `PODCASTER_API_KEY` secret with the same key used in Podcaster.

2. **Verify variable and secret are present before enabling automation:**
   ```bash
   gh variable get PODCASTER_ENDPOINT --repo jmservera/SquadScope
   gh secret list --repo jmservera/SquadScope | grep PODCASTER_API_KEY
   ```

3. **Sync via deploy workflow (optional but recommended):**
   - Configure `SQUADSCOPE_SYNC_TOKEN` in Podcaster (fine-grained token with `secrets:write, variables:write` in SquadScope).
   - Run `deploy-azure.yml` with `sync_squadscope: true` to auto-populate SquadScope variables and secrets.
   - Verify SquadScope variables/secrets after sync.

### Caller Verification

Before relying on auto-sync, SquadScope engineers must manually verify:
- The endpoint is reachable and returns HTTP 202 on `/api/generate` (with a valid API key).
- The API key is correctly stored as a secret and used in the `x-podcaster-api-key` header.
- No API key appears in workflow logs or artifacts.

## Azure Deployment Prerequisites

### Pre-Deployment Checklist

- [ ] **Azure subscription exists** with Contributor or equivalent access.
- [ ] **Resource group name decided** (e.g., `podcaster-prod`). If it doesn't exist, the deploy workflow creates it.
- [ ] **Location confirmed** (e.g., `eastus`). Must be a valid Azure region.
- [ ] **Function App name is globally unique** (Azure enforces global uniqueness for `.azurewebsites.net`). The workflow derives a deterministic default; set `AZURE_FUNCTION_APP_NAME` only to override a collision or naming preference.
- [ ] **Storage Account name is globally unique and lowercase** (Azure storage names must be 3–24 characters, lowercase letters and digits only). The workflow derives a deterministic default; set `AZURE_STORAGE_ACCOUNT_NAME` only to override a collision or naming preference.
- [ ] **Avoid naming conflicts:** If Azure reports a global name conflict, set the relevant override variable and re-deploy.

### Repository Variables (Non-Secret)

These are stored as repository variables (not secrets) and appear in workflow summaries. Set them in Settings > Secrets and variables > Variables:

```
AZURE_CLIENT_ID=<client-id-from-app-registration>
AZURE_TENANT_ID=<tenant-id>
AZURE_SUBSCRIPTION_ID=<subscription-id>
AZURE_LOCATION=eastus
AZURE_RESOURCE_GROUP=podcaster-prod
```

Optional overrides:

```text
AZURE_FUNCTION_APP_NAME=podcaster-app-prod
AZURE_STORAGE_ACCOUNT_NAME=podcasterstg
```

### Repository Secrets

Optionally set a stable API key in Settings > Secrets and variables > Secrets:

```
PODCASTER_API_KEY=<randomly-generated-api-key-at-least-32-chars>
```

If omitted, deployment generates the API key without logging it. Manual SquadScope setup then requires a redeploy with a known secret or an automated sync during the same deploy because the generated value is intentionally unrecoverable from logs.

Optional (required only if syncing to SquadScope):

```
SQUADSCOPE_SYNC_TOKEN=<fine-grained-personal-access-token>
```

### OIDC Federation (GitHub ↔ Azure)

1. **Create an app registration in Azure** dedicated to Podcaster deployment, or reuse only if its permissions are already limited to the Podcaster deployment scope.
2. **Add a federated credential** to the app:
   - **Scenario:** GitHub Actions deploying Azure resources
   - **Organization/repository:** `jmservera/SquadScope-Podcaster`
   - **Entity type:** `Environment`
   - **GitHub environment:** `prod`
   - **Subject identifier:** `repo:jmservera/SquadScope-Podcaster:environment:prod`
   - **Audience:** `api://AzureADTokenExchange`
3. **Get the app credentials:**
   - **Application ID** → `AZURE_CLIENT_ID`
   - **Tenant ID** → `AZURE_TENANT_ID`
   - **Subscription ID** → `AZURE_SUBSCRIPTION_ID` (from the subscription you want to deploy to)
4. **Role assignment:** grant only the minimum Azure role needed for deployment at the target resource group or subscription. Contributor is acceptable for bootstrap; reduce scope after resources exist.
5. **Verify OIDC works:** Run the deploy workflow; it should authenticate without requiring a stored Azure credential.

### First Deploy

1. **Set required variables** in the repository. Set `PODCASTER_API_KEY` only if you need a stable known key; otherwise the workflow generates one.
2. **Run the deploy workflow manually:**
   ```bash
   gh workflow run deploy-azure.yml -R jmservera/SquadScope-Podcaster
   ```
3. **Monitor the workflow run:**
   - Should create the resource group, storage account, Function App, App Insights, and Log Analytics workspace.
   - Should print the endpoint URL (e.g., `Endpoint: https://podcaster-app-prod.azurewebsites.net/api/generate`).
   - Should never print the API key.
4. **Verify the deployment:**
   ```bash
   az functionapp list --resource-group podcaster-prod
   curl -X POST https://podcaster-app-prod.azurewebsites.net/api/generate \
     -H 'content-type: application/json' \
     -H 'x-podcaster-api-key: <your-api-key>' \
     -d '{"week":"2026-W23","article_url":"https://example.com"}'
   ```
   - Should return HTTP 202 with the stable response shape and deterministic placeholder artifact URLs (real TTS is not implemented).

### Re-Deploy (After Code Changes)

1. **Update code and commit to the main branch** (or trigger a feature branch).
2. **Run the deploy workflow:**
   ```bash
   gh workflow run deploy-azure.yml -R jmservera/SquadScope-Podcaster
   ```
3. **The workflow will:**
   - Pull the latest code.
   - Re-deploy the Bicep template (idempotent; updates existing resources).
   - Re-package and deploy the Function App code.
   - Print the endpoint (no changes if names/locations are the same).

### Destroy Resources (If Needed)

**Warning:** This is destructive. Do not run on a production deployment.

```bash
az group delete --name podcaster-prod --yes --no-wait
```

## Release Checklist (Before Publishing)

Use this checklist before marking a release as ready for SquadScope consumption:

### 1. Secrets & Deployment ✓

- [ ] The deployed API key is randomly generated (minimum 32 characters) either from the GitHub secret or by the deploy workflow.
- [ ] No secrets appear in workflow logs, summaries, or artifacts.
- [ ] The deploy workflow validates required variables before proceeding and does not require optional app/storage names or a pre-existing API key.
- [ ] OIDC federation is configured and working (no long-lived Azure credentials stored).
- [ ] The Function App is deployed with HTTPS-only and `minimumTlsVersion: TLS1_2`.

### 2. API Security ✓

- [ ] The `/api/generate` endpoint requires the `x-podcaster-api-key` header.
- [ ] Invalid or missing API keys return HTTP 401 with no hint about valid keys.
- [ ] The API never echoes the received API key in response bodies or logs.
- [ ] Request validation errors return structured HTTP 400 responses with no secret data.

### 3. Logging & Observability ✓

- [ ] Application Insights is configured and ingesting telemetry.
- [ ] Logs do not include API keys, storage keys, or caller credentials.
- [ ] Structured logging uses field names that make sense to operators (e.g., `job_id`, `week`, `status`).
- [ ] Error messages do not leak implementation details (e.g., SQL injection attempts).

### 4. Artifact Staging ✓

- [ ] The Storage Account has `allowBlobPublicAccess: false`.
- [ ] The Function App's system-assigned managed identity has `Storage Blob Data Contributor` role.
- [ ] (Future) Returned artifact URLs are short-lived SAS URLs or brokered by managed identity.
- [ ] (Future) Lifecycle management or a cleanup job deletes expired artifacts after 7 days.

### 5. Integration with SquadScope ✓

- [ ] `PODCASTER_ENDPOINT` variable and `PODCASTER_API_KEY` secret are present in SquadScope repository.
- [ ] SquadScope CI/CD can read both without error.
- [ ] A test call to the endpoint with the API key succeeds.
- [ ] The response shape matches the contract in `docs/integration-contract.md`.

### 6. Documentation ✓

- [ ] `docs/integration-contract.md` is up-to-date and matches the actual API.
- [ ] `docs/architecture.md` describes current resource configuration and security model.
- [ ] `README.md` includes local setup, deployment, and secret handling.
- [ ] This file (`SECURITY.md`) is current and accessible to operators.

### 7. Test Coverage ✓

- [ ] All validation tests pass (invalid weeks, missing fields, malformed requests).
- [ ] No test fixtures commit real API keys or storage credentials.
- [ ] Tests use deterministic stubs or mocks for external services.

### 8. Human Review ✓

- [ ] Hermes (Safety & Security) has reviewed the deployment workflow, secret handling, and logging.
- [ ] No security issues remain unresolved.
- [ ] All decisions are recorded in `.squad/decisions.md`.

---

**Questions or concerns?** Contact Hermes. Release is blocked until all checklist items are complete and signed off.
