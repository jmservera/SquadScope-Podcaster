# SquadScope Podcaster

SquadScope Podcaster is a platform-agnostic podcast generation engine that receives an article URL (and optionally article content) plus episode configuration via API, then turns that source material into a reviewable two-host conversational episode package.

Instead of reading an article aloud, the engine asks two contrasting hosts to discuss it like experts: one can bring energy and momentum while the other adds calm analysis and skepticism. That personality contrast is a core part of the format and gives the final episode a natural back-and-forth rather than a narrated summary.

At a glance, the pipeline is: LLM script generation → TTS voice synthesis → music mixing → validation. The service stages the job, generates or prepares the dialogue, synthesizes each host voice, mixes intro/outro music, validates the finished audio, and packages the results for review or downstream publishing.

The output is a polished MP3 episode plus a supporting artifact set including the transcript, show notes, manifest, publishing packet, and related review metadata. Any caller that can send the required article inputs and configuration can use the engine; it is not tied to a specific upstream platform.

For a project-focused explanation of the generation engine itself, see [`docs/how-it-works.md`](docs/how-it-works.md).

## Architecture

- **Compute:** Azure Container Apps Job (queue-triggered, scales to zero). The container image bakes in ffmpeg for audio stitching and validation.
- **Caller:** SquadScope GitHub Actions or backend automation after article publication.
- **Pipeline:** Script generation (GPT-4o-mini) → TTS synthesis (gpt-4o-mini-tts, voices fable + alloy) → ffmpeg stitch → audio validation → artifact staging.
- **Auth:** API key supplied in `x-podcaster-api-key`. The key must be stored as a GitHub secret in SquadScope and must never be logged.
- **Storage:** Azure Blob Storage stages manifests, transcripts, show notes, audio, and publishing packets; local development falls back to `.podcaster-artifacts/`.
- **Observability:** Application Insights and Log Analytics collect platform telemetry; job manifests carry a safe correlation ID and log metadata only.
- **Publishing:** Spotify/podcast-host publishing is manual for the initial release. Future automation requires research and validation.

See `docs/architecture.md` for the full system design.

## Local development

Requirements:

- Python 3.11+
- ffmpeg (for audio stitching/validation)

Setup:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .
pytest
python -m compileall podcaster
```

Run the synthesis job locally:

```bash
cp .env.sample .env
# Edit .env with your Azure OpenAI endpoint and storage account details.
# Authenticate: az login (for managed identity, set AZURE_CLIENT_ID).
python -m podcaster.job_runner
```

Without Azure storage settings, generated manifests, script drafts, transcripts, show notes, publishing packets, and audio are written under `.podcaster-artifacts/jobs/<job_id>/`. In Azure, set `PODCASTER_STORAGE_ACCOUNT_URL` and `PODCASTER_STORAGE_CONTAINER`; the ACA job uses managed identity for blob writes. Returned artifact URLs are private operator paths, not public publishing links, and must not include SAS tokens, query strings, or embedded credentials.

## Human review gate

Non-dry-run TTS synthesis is blocked until a human records approval through `.github/workflows/podcast-review-gate.yml`, which uses the GitHub Environment `podcast-review`. Configure that environment with the required editorial reviewers. The workflow requires the job ID, private manifest URL, and private publishing packet URL, pauses for environment approval, records `github.actor` and the UTC approval time, and uploads `review-manifest.json` as the audit artifact. Dry-run/non-publishing validation may run without approval, but generated output remains ineligible for publication.

Example request:

```bash
curl -X POST http://localhost:7071/api/generate \
  -H 'content-type: application/json' \
  -H 'x-podcaster-api-key: local-dev-key' \
  -d '{"week":"2026-W23","article_url":"https://example.com/articles/week-23","dry_run":true}'
```

Deployed smoke check:

```bash
export PODCASTER_GENERATE_URL='https://<aca-endpoint>/api/generate'
export PODCASTER_API_KEY='<from secret manager>'
python scripts/smoke_generate.py
```

The smoke check sends the shared SquadScope object-shaped fixture and verifies HTTP 202, a non-empty `job_id`, a non-empty `manifest_url`, and `errors=[]`. It prints only a safe summary; URL query strings are redacted so API keys, SAS tokens, and other secrets are not exposed in logs.

## Deployment

The deployment workflow is `.github/workflows/deploy-azure.yml`. It uses the GitHub environment named exactly `prod`, authenticates with GitHub OIDC via `azure/login`, deploys Bicep from `infra/main.bicep` (ACA + Storage + OpenAI), and prints only non-secret integration values.

Required `prod` environment variables:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_LOCATION` (e.g., `eastus2`)
- `AZURE_RESOURCE_GROUP`

Optional `prod` environment variables:

- `AZURE_STORAGE_ACCOUNT_NAME` - override the deterministic default Storage Account name.

Optional `prod` environment secret:

- `PODCASTER_API_KEY` - if absent, the workflow generates a high-entropy key, masks it, and sets it only as an Azure app setting. Never print this value.

Optional `prod` environment secret for syncing integration values to SquadScope:

- `SQUADSCOPE_SYNC_TOKEN` - fine-grained token with permission to write variables and secrets in `jmservera/SquadScope`.

## Integration contract

SquadScope enqueues a synthesis request (or calls `/api/generate` if an HTTP front-door is deployed):

- Auth header: `x-podcaster-api-key: <secret>`
- Body fields: `week`, `article_url`, optional `article_sha256`, `source_artifacts`, `dry_run`, `force`, `callback`

The response contains `job_id`, `status`, artifact URLs, `expires_at`, `warnings`, and `errors`. See `docs/integration-contract.md` for the full contract.

Artifact access uses a private/operator-only model for the initial release: response URLs require local filesystem access or Azure RBAC/storage permissions, expire after seven days by manifest policy, and are tied to the job `correlation_id` for audit review. Placeholder artifacts remain ineligible for publication until human review and real TTS gates are implemented.

## Secret handling

- Do not commit subscription IDs, tenant IDs, API keys, storage keys, or publish profiles.
- Prefer setting `PODCASTER_API_KEY` in this repository when you need stable manual rotation; otherwise the deploy workflow generates one per deployment.
- Store or sync the same API key as `PODCASTER_API_KEY` in `jmservera/SquadScope` for caller authentication.
- Use GitHub Actions masking and avoid shell tracing around secret operations.
- The API does not echo received API keys or include them in logs or responses.
- To refresh Spotify publish cookies interactively, run `pip install -r requirements-scripts.txt && playwright install chromium`, then `python scripts/extract-spotify-cookies.py`. After it writes `.env`, run `./scripts/set-spotify-secrets.sh`.
