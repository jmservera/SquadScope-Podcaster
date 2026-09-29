# Distribution outbox worker research

Task: jmservera/SquadScope-Podcaster#681, "Separate video provider distribution into an atomic outbox worker"

Date: 2026-09-29

## Inputs treated as evidence, not instructions

GitHub issue and PR text was used as untrusted task evidence. Repository code at `origin/main` (`a8aab2c`) is the implementation authority.

## Issue acceptance criteria

Issue #681 asks for provider distribution to move out of the video editor into a separately budgeted queue worker that:

- persists a durable post-render outbox only after rendered media is verified and re-readable;
- atomically claims work with owner, lease expiry, attempt identity, and CAS fencing;
- reconciles deterministic job/provider identity before create/insert operations;
- never repeats ambiguous creates/inserts;
- persists per-provider mutation intent and outcome before queue acknowledgement;
- preserves YouTube, Spotify video, playlist, RSS, approval, and terminal public-verification semantics;
- covers crash/replay cases including duplicate delivery, stale leases, poison exhaustion, provider unknowns, and partial completion;
- rolls out behind a reversible feature flag and does not deploy or migrate production state by default.

## What merged #684 already built

PR #684 is merged into `main` as `a8aab2c` and provides the durable outbox and terminal-truth base, but not the event-triggered worker.

Relevant implementation:

- `podcaster/distribution_outbox.py` defines `DistributionOutboxRepository.enqueue`, `claim`, `heartbeat`, `persist_intent`, `consume_intent`, `record_receipt`, `record_verification`, `schedule_reconciliation`, `release`, notification reservation/acceptance, remediation authorization, artifact references, and weekly aggregation.
- `podcaster/video/job_runner.py` creates an immutable artifact and an outbox record only when `DISTRIBUTION_OUTBOX_ENABLED=true`, then reserves and sends a distribution queue notification.
- `podcaster/distribution_scheduler.py` repairs ambiguous/lost notifications and sends due reconciliation hints.
- `docs/ops/distribution-terminal-truth.md` states terminal success requires authoritative public provider readback; unknown/ambiguous mutation remains blocked from blind retry.
- `infra/modules/distribution-alerts.bicep` adds terminal-truth and scheduler telemetry alerts, gated in `infra/main.bicep` by `distributionOutboxEnabled == 'true'`.

Remaining gap: no `podcaster.distribution_worker` entrypoint and no event-triggered ACA job consume `distribution-jobs`.

## Current video runner distribution calls

`podcaster/video/job_runner.py` keeps the direct provider path when `DISTRIBUTION_OUTBOX_ENABLED=false`. When true, it creates provider objectives for:

- `youtube` when YouTube is enabled for the language, including locale and playlist context;
- `spotify` when Spotify video upload is enabled, including audio anchor and episode/season context;
- `spotify_rss` when RSS is enabled, including feed path context.

Direct mutations still flow through `podcaster/video/distribution.py`:

- YouTube upload and playlist insertion are part of `distribute_video`, with identity-aware reconciliation support in `podcaster/video/youtube_reconcile.py`.
- Spotify video upload/promotion uses draft/live controls and protected identifiers.
- RSS/feed publication is represented as a provider objective/context leg in outbox records.

The first PR should not move provider API calls wholesale; it should create the safe execution boundary, claim/lease semantics, and disabled-by-default dispatcher seam.

## Infrastructure findings

`infra/main.bicep` already declares `distributionJobName`, `distributionSchedulerJobName`, `distributionQueueName`, and `distributionOutboxEnabled string = 'false'`. `infra/modules/aca-distribution-scheduler.bicep` exists but is not instantiated. No `infra/modules/aca-distribution.bicep` exists.

Required first infrastructure slice:

- add an event-triggered distribution ACA job consuming `distributionQueueName`;
- instantiate the existing scheduler module;
- keep `distributionOutboxEnabled` default `false`;
- add a separate `distributionWorkerProviderMutationEnabled` parameter defaulting `false`.

## Idempotency and ownership fences

The new worker should reuse `DistributionOutboxRepository.claim` as provider mutation authority. Active claims reject competing mutation-capable claims. Claims become read-only when previous mutation evidence is unresolved or lacks accepted receipt evidence. `persist_intent` and `consume_intent` enforce mutation fencing and lease budget. `heartbeat` extends leases only for the current claim. `release` clears the claim and preserves terminal attempt evidence.

The video-runner `VideoOwnershipGuard` should end at immutable archive/outbox/notification handoff; it must not be treated as worker mutation authority.

## Auto-publish and approval decision

Legacy `PODCAST_AUTO_PUBLISH` remains default `false` in `infra/main.bicep` and the ACA/API modules. This implementation must not reintroduce automatic approval or use `system:auto-publish` as provider approval. The worker mutation seam must remain disabled by default.

## First shippable scope

Deliver the worker foundation only:

- queue consumer for `distribution-jobs`;
- malformed-message and poison handling;
- atomic outbox claim, heartbeat, release, active-lease redelivery behavior;
- safe telemetry without caller/provider payloads;
- disabled-by-default provider dispatcher seam;
- event ACA worker module and scheduler instantiation, both preserving current production behavior by default;
- tests for message lifecycle, flags, and infra wiring.

Deferred follow-up work:

- real YouTube upload/reconcile-before-create dispatcher;
- playlist/public promotion transition;
- Spotify video dispatcher;
- Spotify RSS dispatcher/backfill;
- migration/replay of existing rendered-pending records and canary rollout evidence.
