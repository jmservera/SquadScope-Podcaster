# Distribution outbox worker changes

Task: jmservera/SquadScope-Podcaster#681

Date: 2026-09-29

## Implemented

- Added `podcaster.distribution_worker`, a queue consumer for `distribution-jobs` outbox hints.
- The worker parses only the durable outbox ID, claims the authoritative outbox record with owner/execution/lease, heartbeats, and releases handled claims.
- Malformed messages are deleted without claim or dispatch.
- Active outbox leases are treated as transient contention and left for queue redelivery.
- Queue poison exhaustion records `poisoned` verification on unresolved provider legs before deleting the message, while preserving stronger terminal/ambiguous provider truth such as `publication_unknown`.
- Exhausted hints for missing outbox records are deleted as orphan hints while non-exhausted missing records remain retryable.
- Queue message pop receipts are hashed into safe execution IDs before writing claim evidence.
- Added `DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED`, default false, so provider mutation dispatch cannot run by default.
- Added an injected dispatcher seam for future provider-specific follow-up work, with lease renewal while injected dispatch runs and a fail-closed default dispatcher.
- Added `infra/modules/aca-distribution.bicep` and instantiated it from `infra/main.bicep`.
- The ACA worker passes a distribution queue visibility timeout longer than the hard replica timeout.
- Instantiated the existing distribution scheduler ACA module from `infra/main.bicep`.
- Added static infra assertions and distribution-worker tests.

## Defaults and production behavior

- `distributionOutboxEnabled` remains default `false`.
- `distributionWorkerProviderMutationEnabled` defaults `false`.
- `PODCAST_AUTO_PUBLISH` was not changed and no auto-approval path was added.

## Deferred

- jmservera/SquadScope-Podcaster#725: YouTube upload/reconcile-before-create dispatcher.
- jmservera/SquadScope-Podcaster#726: YouTube playlist and public-promotion dispatcher.
- jmservera/SquadScope-Podcaster#727: Spotify video dispatcher.
- jmservera/SquadScope-Podcaster#728: Spotify RSS dispatcher.
- jmservera/SquadScope-Podcaster#729: Migration/backfill-adjacent rollout, rollback, and runbook evidence.
