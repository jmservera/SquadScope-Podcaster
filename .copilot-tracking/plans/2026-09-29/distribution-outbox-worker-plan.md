# Distribution outbox worker plan

Task: jmservera/SquadScope-Podcaster#681

Date: 2026-09-29

## Invariants

- Preserve production behavior by default.
- Keep `DISTRIBUTION_OUTBOX_ENABLED=false`.
- Add `DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED=false` and make it the deployment default.
- Do not reintroduce `PODCAST_AUTO_PUBLISH` or any automatic approval path.
- Treat queue messages as hints; the durable outbox record is authoritative.
- Do not let caller-controlled text reach paths or unsanitized log lines.

## Phase 1: Worker contract

Add `podcaster.distribution_worker` with:

- distribution queue message parsing via `parse_distribution_outbox_id`;
- a unique execution/owner identity;
- atomic `DistributionOutboxRepository.claim`;
- heartbeat while a claim is retained;
- `release` after safe processing;
- malformed-message deletion;
- active-lease contention as transient redelivery;
- max-dequeue poison handling that records `poisoned` provider verification and deletes the message;
- CLI `python -m podcaster.distribution_worker --max-messages 1`.

## Phase 2: Provider dispatch seam

Add a dispatcher interface that receives the claimed outbox document and returns a non-mutating result unless `DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED=true`.

Default behavior:

- no provider API calls;
- no durable mutation intent consumption;
- release the claim after verifying the worker path can safely claim/release;
- log sanitized lifecycle metrics only.

Enabled behavior for this PR remains intentionally skeletal and test-seamed; real provider mutation is deferred to follow-up issues.

## Phase 3: Infrastructure

Add `infra/modules/aca-distribution.bicep`:

- event-triggered ACA job;
- consumes `distributionQueueName`;
- runs `python -m podcaster.distribution_worker --max-messages 1`;
- uses existing managed identity and queue/blob environment variables;
- receives `DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED`, defaulting false from root.

Update `infra/main.bicep`:

- instantiate the new distribution worker module;
- instantiate existing `aca-distribution-scheduler.bicep`;
- output worker and scheduler job names;
- retain `distributionOutboxEnabled string = 'false'`.

## Phase 4: Tests

Add targeted tests for:

- malformed messages delete without claim/dispatch;
- successful claim/heartbeat/release deletes the message;
- duplicate delivery during an active lease leaves the message for redelivery and performs no dispatch;
- max dequeue poison marks providers `poisoned` and deletes the message;
- mutation flag default is false;
- infra deploys distribution worker and scheduler with default-off flags.

Run:

- `python3 -m pytest -q tests/test_distribution_worker.py tests/test_queue.py tests/test_deploy_workflow.py`
- `python3 -m pytest -q`
- `python3 -m ruff check podcaster tests`
- `python3 -m ruff format --check podcaster tests`

## Deferred follow-ups

- jmservera/SquadScope-Podcaster#725: YouTube provider dispatcher with reconcile-before-create.
- jmservera/SquadScope-Podcaster#726: YouTube playlist/public-promotion dispatcher.
- jmservera/SquadScope-Podcaster#727: Spotify video dispatcher.
- jmservera/SquadScope-Podcaster#728: Spotify RSS dispatcher and migration/backfill.
- jmservera/SquadScope-Podcaster#729: Production canary/rollback runbook evidence.
