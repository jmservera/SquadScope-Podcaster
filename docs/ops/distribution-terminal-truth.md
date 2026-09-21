# Distribution terminal truth operations

## Incident boundary and correlation

W38 may be classified `published_verified_recovered` only when exact durable proof binds its weekly
identity, manifest and publication digest, canonical artifact, authorized succeeding attempt,
provider item identities, and authoritative terminal readback. Fixtures and operator summaries must
not assume that green state. Earlier failed or partial attempts remain immutable evidence.

W39 is the active missed-publication class. Its dispatch was blocked before Azure, so there is no
W39 synthesis, recorder, video, outbox, or provider execution to inspect. The upstream weekly
publisher must first register a sanitized `dispatch_correlation_id`, week, dispatch result, and
source at `POST /api/dispatch-intents`, then include that same correlation ID in `POST
/api/generate`. Podcaster persists server-side receipt times and the accepted job ID. The
correlation contains no article body, URL, credential, token, signed URL, title, account name, or
provider identity.

An intent without first durable Azure arrival emits `dispatch_missing_azure_arrival_seconds`
(warning after 10 minutes, critical after 30 minutes). A correlated accepted generate request
clears the condition. A direct/manual downstream invocation cannot create upstream dispatch
evidence and cannot clear the missing-arrival condition.

Provider delivery succeeds only after authoritative external readback proves every requested
provider item is public. Queue acceptance, mutation HTTP success, draft creation, private or
unlisted YouTube state, Spotify draft state, pending processing, unknown mutation state, manual
handoff, partial delivery, and poison exhaustion are non-success outcomes.

Attempt records are append-only and use unique attempt identities, lifecycle events, authorization
and predecessor references, mutation intents, receipts, and terminal evidence. A weekly decision is
a separate deterministic record. Its precedence is `identity_conflict`, `provider_unknown`,
`manual_action_required`, `partial`, `missed_not_dispatched`, `failed_terminal`, `pending`,
`published_verified_recovered`, then `published_verified`. Recovery never rewrites the failed
attempt. An unknown possible mutation permits read-only reconciliation only and cannot authorize a
blind retry.

## Rollout and rollback

`DISTRIBUTION_OUTBOX_ENABLED` is disabled by default. Enable it only after the dedicated
distribution queue and worker use the same release image and the alert queries below are active.
Rollback disables new outbox routing and claims; it must not delete outbox records, consumed
intents, receipts, provider identifiers, or reconciliation schedules. Never restore blind inline
mutation for an item with a consumed intent.

## Alert contract

| Signal | Warning | Critical | Owner / action |
|---|---:|---:|---|
| `distribution_pending_age_seconds` | >15m for 10m | >60m for 5m | Operations; inspect claim, intent, receipt, and next reconciliation |
| `distribution_claim_latency_seconds` / `distribution_lease_loss` | p95 >5m | any lease loss | Operations / production owner; inspect worker capacity and stale claims |
| `distribution_provider_unknown` | n/a | any for 5m | Production owner; stop mutation and reconcile identity read-only |
| `distribution_manual_handoff` | any for 5m | age >24h | Publication operator; perform provider action, then authoritative readback |
| `distribution_youtube_non_public` | >15m | >60m | Production owner; verify processing, promotion intent, and privacy readback |
| `distribution_spotify_draft` | >15m | >24h | Publication operator; publish manually when required, then read back expected item |
| `distribution_public_verification_lag_seconds` | >15m | >60m | Operations; run bounded read-only reconciliation |
| `distribution_poisoned` | n/a | any | Production owner; inspect invariant failure and perform explicit operator resolution |
| `distribution_identity_conflict` | n/a | any | Production owner; resolve manifest/digest, canonical artifact, provider identity, or duplicate ambiguity |
| `distribution_weekly_non_green` | n/a | any | Production owner; weekly acceptance is blocked |
| `distribution_scheduler_heartbeat` | missing >15m | missing >15m | Production owner; restore authoritative scheduler telemetry |
| `distribution_active_outbox_depth` | active without state rows | n/a | Operations; inspect scheduler and worker emission |
| `distribution_claim_heartbeat_missing` | n/a | any active overdue claim | Production owner; fence stale ownership and reconcile read-only where required |

Missing telemetry is healthy only when authoritative outbox depth is zero. Missing telemetry while
active outbox records exist is itself a warning; missing claim heartbeat while a claim is active is
critical. Alerts clear only from authoritative terminal readback or an explicit operator resolution,
not from process success.

## Canary

Start one controlled publication at the authoritative upstream weekly boundary. Record accepted
intent, dispatch result, Azure API acceptance, first durable Azure arrival, release digest, outbox
identity, execution and fence, consumed intents, sanitized receipts, YouTube processing/privacy
readback, Spotify expected-item readback, worker exit, and alert fire/clear results. A
Podcaster-only injection does not prove the W39-class boundary. A manual Spotify handoff does not
pass until the expected provider item is authoritatively read as public.

Separately exercise a W38-inspired partial-attempt/retry fixture to prove truthful reconciliation
and multi-attempt observability. The fixture is green only with the complete proof chain and must
retain both the failed attempt and authorized successful attempt. Never describe that fixture as
missed-week recovery. Four consecutive post-fix cycles must each end `published_verified` or
controlled `published_verified_recovered`; every other weekly state blocks acceptance. Roll back
immediately on a duplicate mutation, stale fenced write, missing receipt, unbounded reconciliation,
or success without public readback.
