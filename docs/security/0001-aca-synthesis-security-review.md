# ACA synthesis job + queue security review

- Issue: #80 (#67 follow-up 5/5)
- ADR: `docs/adr/0001-production-audio-ffmpeg-hosting.md` Option C
- Review scope: `infra/modules/aca.bicep`, `infra/main.bicep`, `infra/modules/openai.bicep`, `podcaster/job_runner.py`, `podcaster/queue.py`, `podcaster/storage.py`, `podcaster/tts.py`, `podcaster/jobs.py`, `podcaster/artifact_access.py`, `podcaster/sanitization.py`, and `function_app.py`.

## Threat notes

The sensitive assets are Azure OpenAI access, Storage Blob artifacts, Storage Queue messages, signed operator-download URLs, source article content, and generated artifacts. The primary threats reviewed were credential substitution (keys/connection strings instead of managed identity), over-broad RBAC, secret or PII leakage in logs, replay/duplicate queue delivery, poison messages, and artifacts becoming public or outliving the intended review window.

## Managed identity / secret findings

Static review found identity-only data-plane access for the ACA synthesis path:

- Azure OpenAI TTS uses `ManagedIdentityTokenCredential` with `https://cognitiveservices.azure.com/.default`; `openai.bicep` sets `disableLocalAuth: true`.
- Blob and Queue access use Bearer tokens for `https://storage.azure.com/.default` and endpoint URIs, not account keys or Storage connection strings.
- Queue messages are base64 JSON containing only `schema_version` and `job_id`.
- User-delegation SAS generation in `storage.py` uses `az storage blob generate-sas --auth-mode login --as-user`; account-key SAS is not used. SAS URLs are marked secret and not logged.

Notes: `APPLICATIONINSIGHTS_CONNECTION_STRING` and the Container Apps Log Analytics shared key are platform telemetry configuration, not OpenAI/Blob/Queue data-plane credentials. Live Azure settings still require operator confirmation before production enablement.

## Verified role-assignment table

| Principal | Role | Scope in template | Justification | Status |
| --- | --- | --- | --- | --- |
| ACA synthesis user-assigned managed identity | Cognitive Services OpenAI User | Azure OpenAI account (`openAiAccount`) | Allows TTS data-plane calls without account keys; no management-plane grant. | Verified statically in `infra/modules/openai.bicep`; live assignment requires operator confirmation. |
| ACA synthesis user-assigned managed identity | Storage Blob Data Contributor | Artifacts container (`artifactContainer`) | Allows reading/staging/updating job artifacts and manifests in the private artifact container only. | Tightened from storage-account scope in this change; live assignment requires operator confirmation. |
| ACA synthesis user-assigned managed identity | Storage Queue Data Contributor | Synthesis queue (`synthesisQueue`) | Allows KEDA queue-length checks and runner get/delete/send operations on the synthesis queue only. | Tightened from storage-account scope in this change; live assignment requires operator confirmation. |
| Function App system identity | Storage Queue Data Contributor | Storage account | Existing enqueue path grant. It is not RG/subscription-wide, but broader than a single queue; consider a future least-privilege follow-up once function queue dependencies are separated. | Reviewed, unchanged to avoid touching deploy/smoke path. |

No Owner/Contributor assignment at resource-group or subscription scope was found in the reviewed ACA synthesis templates.

## Logging review

Reviewed every log statement in the scoped Python files. Logged fields are limited to `job_id`, week, status, counts, validation status, deployment/voice names, input character count, message id, dequeue count, exception type, and safe configuration summaries. The code does not log bearer tokens, API keys, account keys, connection strings, SAS URLs, full OpenAI endpoints, callback secrets, queue bodies, or full source-article/script content.

This change adds explicit audit logs keyed by `job_id`:

- `synthesis audit event=start job_id=... message_id=... dequeue_count=...`
- `synthesis audit event=success|skipped job_id=... status=... reason=... terminal=true`
- `synthesis audit event=failure job_id=... reason=... dequeue_count=... terminal=...`

Malformed queue messages are logged without body content because no trusted `job_id` is available.

## Poison-message / failure path

`podcaster.queue.encode_synthesis_message()` emits only `schema_version` and `job_id`; no article text, source artifacts, secrets, or SAS tokens are placed on the queue.

Malformed messages that cannot produce a `job_id` are treated as poison and deleted after a body-free error log. Transient job failures remain on the queue for redelivery. This change caps repeated transient failures with `MAX_DEQUEUE_COUNT = 5`; at the threshold the message is logged as terminal failure, deleted, and surfaced as `retry_exhausted` so the queue cannot spin forever on a poison job.

Duplicate delivery is idempotent: a manifest already marked `generation.synthesis_runner.status == completed` is skipped and the message is deleted.

## Artifact-access confirmation (#18)

Job-produced artifacts continue to use the private operator-path model from `podcaster/artifact_access.py`:

- `publicly_accessible: false`; generated response URLs are private operator locators, not publishing links.
- Manifest metadata records `expires_at`, `cleanup_after`, and cleanup owner (`operator_or_storage_lifecycle_policy`).
- Operator review downloads, when minted, use read-only HTTPS user-delegation SAS with `account_key_used: false` and explicit expiry.
- The synthesis runner preserves the human-review publication block (`publishing.eligible: false`, `human_review` remains in `blocked_by`).

Retention/lifecycle cleanup policy deployment is operator/Azure-side and must be confirmed before production enablement.

## Sign-off checklist

- [x] Threat notes recorded.
- [x] Managed-identity-only data plane verified statically for Azure OpenAI TTS, Blob, and Queue.
- [x] No account-key, connection-string, or account-SAS usage found in ACA synthesis data plane; user-delegation SAS only for downloads.
- [x] ACA synthesis job role assignments verified statically and tightened to OpenAI account, artifact container, and synthesis queue scope.
- [x] No RG/subscription Owner or Contributor grants found in reviewed ACA synthesis templates.
- [x] Logging reviewed; no secrets, SAS tokens, connection strings, full source content, or PII payloads are logged.
- [x] `job_id` audit correlation added for synthesis start, terminal success/skip, and failure.
- [x] Queue messages carry no sensitive payload (only `schema_version` + `job_id`).
- [x] Poison/failure path documented and retry exhaustion implemented.
- [x] Artifact access semantics (#18) hold for job-produced artifacts: private locators, user-delegation SAS for review downloads only, expiry metadata, and human-review publication block.
- [ ] Operator confirmed live Azure RBAC assignments match the templates after deployment.
- [ ] Operator confirmed Storage lifecycle/retention cleanup policy is deployed/enforced for expired artifacts.

Sign-off: Static code/Bicep security review is complete. Production enablement remains blocked on the operator confirmations above.
