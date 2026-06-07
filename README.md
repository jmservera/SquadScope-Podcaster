# SquadScope Podcaster

SquadScope Podcaster is a sister project to `jmservera/SquadScope`. It receives a post-publish article URL or artifact reference, creates a podcast-generation job, stages generated artifacts (locally or in Azure Blob Storage), and returns links that SquadScope can display or use in follow-up automation.

This scaffold intentionally does not generate real audio yet. The Azure Functions API validates the integration contract, stages deterministic production-path placeholder artifacts (with 7-day expiry), and returns stable artifact URLs so SquadScope can integrate without affecting its existing publishing process.

## Architecture

- **Caller:** SquadScope GitHub Actions or backend automation after article publication.
- **API:** Python Azure Functions HTTP endpoint at `/api/generate`.
- **Auth:** API key supplied in `x-podcaster-api-key`. The key must be stored as a GitHub secret in SquadScope and must never be logged.
- **Storage:** Azure Blob Storage stages manifests, transcripts, show notes, audio placeholders, and publishing packets; local development falls back to `.podcaster-artifacts/`.
- **Observability:** Application Insights and Log Analytics collect platform telemetry; job manifests carry a safe correlation ID and log metadata only.
- **Publishing:** Spotify/podcast-host publishing is manual for the initial release. Future automation requires research and validation.

## Local development

Requirements:

- Python 3.11+
- Optional: Azure Functions Core Tools for local function hosting

Setup:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .
pytest
python -m compileall podcaster function_app.py
```

Run locally with Azure Functions Core Tools:

```bash
export PODCASTER_API_KEY=local-dev-key
# Optional: override local artifact staging path/base URL. Defaults shown.
export PODCASTER_LOCAL_STORAGE_PATH=.podcaster-artifacts
export PODCASTER_ARTIFACT_BASE_URL=https://example.invalid/podcaster-stub
func start
```

Without Azure storage settings, generated manifests, script drafts, transcripts, show notes, publishing packets, and audio placeholders are written under `.podcaster-artifacts/jobs/<job_id>/`. The ZIP packet is byte-stable for the same inputs and timestamp. In Azure, set `PODCASTER_STORAGE_ACCOUNT_URL` and `PODCASTER_STORAGE_CONTAINER`; the Function App uses managed identity for blob writes.

Example request:

```bash
curl -X POST http://localhost:7071/api/generate \
  -H 'content-type: application/json' \
  -H 'x-podcaster-api-key: local-dev-key' \
  -d '{"week":"2026-W23","article_url":"https://example.com/articles/week-23","dry_run":true}'
```

## Deployment

The deployment workflow is `.github/workflows/deploy-azure.yml`. It uses the GitHub environment named exactly `prod`, authenticates with GitHub OIDC via `azure/login`, deploys Bicep from `infra/main.bicep`, packages the Function App, deploys it, and prints only non-secret integration values.

Required `prod` environment variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_LOCATION` (for example, `eastus`)
- `AZURE_RESOURCE_GROUP`

Optional `prod` environment variables:

- `AZURE_FUNCTION_APP_NAME` - override the deterministic default Function App name.
- `AZURE_STORAGE_ACCOUNT_NAME` - override the deterministic default Storage Account name.

Optional `prod` environment secret:

- `PODCASTER_API_KEY` - if absent, the workflow generates a high-entropy key, masks it, and sets it only as an Azure app setting. Never print this value.

Optional `prod` environment secret for syncing integration values to SquadScope:

- `SQUADSCOPE_SYNC_TOKEN` - fine-grained token with permission to write variables and secrets in `jmservera/SquadScope`.

## Integration contract

SquadScope calls:

- Endpoint: `https://<function-app>.azurewebsites.net/api/generate`
- Method: `POST`
- Auth header: `x-podcaster-api-key: <secret>`
- Body fields: `week`, `article_url`, optional `article_sha256`, `source_artifacts`, `dry_run`, `force`, `callback`

The response contains `job_id`, `status`, artifact URLs, `expires_at`, `warnings`, and `errors`. See `docs/integration-contract.md` for the full contract.

## Secret handling

- Do not commit subscription IDs, tenant IDs, API keys, storage keys, or publish profiles.
- Prefer setting `PODCASTER_API_KEY` in this repository when you need stable manual rotation; otherwise the deploy workflow generates one per deployment.
- Store or sync the same API key as `PODCASTER_API_KEY` in `jmservera/SquadScope` for caller authentication.
- Use GitHub Actions masking and avoid shell tracing around secret operations.
- The API does not echo received API keys or include them in logs or responses.
