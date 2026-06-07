# SquadScope Podcaster

SquadScope Podcaster is a sister project to `jmservera/SquadScope`. It receives a post-publish article URL or artifact reference, creates a podcast-generation job, stages generated artifacts in Azure Blob Storage, and returns links that SquadScope can display or use in follow-up automation.

This scaffold intentionally does not generate audio yet. The Azure Functions API validates the integration contract and returns deterministic stub artifact URLs so SquadScope can integrate without affecting its existing publishing process.

## Architecture

- **Caller:** SquadScope GitHub Actions or backend automation after article publication.
- **API:** Python Azure Functions HTTP endpoint at `/api/generate`.
- **Auth:** API key supplied in `x-podcaster-api-key`. The key must be stored as a GitHub secret in SquadScope and must never be logged.
- **Storage:** Azure Blob Storage stages manifests, transcripts, show notes, audio, and publishing packets.
- **Observability:** Application Insights and Log Analytics collect platform telemetry.
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
func start
```

Example request:

```bash
curl -X POST http://localhost:7071/api/generate \
  -H 'content-type: application/json' \
  -H 'x-podcaster-api-key: local-dev-key' \
  -d '{"week":"2026-W23","article_url":"https://example.com/articles/week-23","dry_run":true}'
```

## Deployment

The deployment workflow is `.github/workflows/deploy-azure.yml`. It uses GitHub OIDC with `azure/login`, deploys Bicep from `infra/main.bicep`, packages the Function App, deploys it, and prints only non-secret integration values.

Required repository variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_LOCATION` (for example, `eastus`)
- `AZURE_RESOURCE_GROUP`
- `AZURE_FUNCTION_APP_NAME`
- `AZURE_STORAGE_ACCOUNT_NAME`

Required repository secret:

- `PODCASTER_API_KEY` - the API key configured as an app setting. Never print this value.

Optional secret for syncing integration values to SquadScope:

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
- Store the Podcaster API key as `PODCASTER_API_KEY` in this repository for deployment.
- Store the same API key as `PODCASTER_API_KEY` in `jmservera/SquadScope` for caller authentication.
- Use GitHub Actions masking and avoid shell tracing around secret operations.
- The API does not echo received API keys or include them in logs or responses.
