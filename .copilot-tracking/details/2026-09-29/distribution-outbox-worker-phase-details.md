# Distribution outbox worker phase details

Task: jmservera/SquadScope-Podcaster#681

Date: 2026-09-29

## P01 Worker contract

- P01-T01: create `podcaster/distribution_worker.py` with queue consumer, CLI, and sanitized logging.
- P01-T02: use `parse_distribution_outbox_id` and delete malformed messages.
- P01-T03: claim with `DistributionOutboxRepository.claim(owner, execution_id, lease_seconds)`.
- P01-T04: heartbeat once before dispatch and release handled claims.
- P01-T05: treat `StaleClaimError` active lease as transient; do not delete the message.
- P01-T06: when `dequeue_count >= max_dequeue_count`, claim if possible, record each actionable provider as `poisoned`, release, and delete.

## P02 Provider seam

- P02-T01: define `ProviderDispatcher` callable type.
- P02-T02: when the mutation flag is false, use a non-mutating handled path and return `provider_mutation_disabled`.
- P02-T03: expose `provider_mutation_enabled(env)` with false default.
- P02-T04: if the flag is false, never call mutating provider code.
- P02-T05: if a canary explicitly enables mutation before a real dispatcher is injected, fail closed; while an injected dispatcher runs, renew the outbox lease until dispatch returns.

## P03 Infrastructure

- P03-T01: add `infra/modules/aca-distribution.bicep` for event-triggered job.
- P03-T02: pass storage/blob/queue env vars, managed identity, image, registry, and mutation flag.
- P03-T03: instantiate worker and existing scheduler from `infra/main.bicep`.
- P03-T04: add root parameter `distributionWorkerProviderMutationEnabled string = 'false'`.
- P03-T05: output `distributionJobName` and `distributionSchedulerJobName`.

## P04 Tests

- P04-T01: in-memory queue fake for worker message disposition.
- P04-T02: outbox fixture using `LocalStorageBackend`.
- P04-T03: assertions for claim release, poison state, transient lease behavior, and flag defaults.
- P04-T04: deploy workflow/static infra tests for module wiring.

## Acceptance gate

Implementation is acceptable only if the first PR can be deployed without changing production distribution behavior and local pytest/ruff checks pass.
