# Distribution terminal truth operations

Provider delivery succeeds only after authoritative external readback proves every requested
provider item is public. Queue acceptance, mutation HTTP success, draft creation, private or
unlisted YouTube state, Spotify draft state, pending processing, unknown mutation state, manual
handoff, partial delivery, and poison exhaustion are non-success outcomes.

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

Missing telemetry is healthy only when authoritative outbox depth is zero. Missing telemetry while
active outbox records exist is itself a warning; missing claim heartbeat while a claim is active is
critical. Alerts clear only from authoritative terminal readback or an explicit operator resolution,
not from process success.

## Canary

Use one controlled publication with routing initially disabled, then enable one outbox item. Record
the release digest, outbox identity, execution and fence, consumed intents, sanitized receipts,
YouTube processing/privacy readback, Spotify expected-item readback, worker exit, and alert
fire/clear results. A manual Spotify handoff does not pass the canary until the expected provider
item is authoritatively read as public. Roll back immediately on a duplicate mutation, stale fenced
write, missing receipt, unbounded reconciliation, or success without public readback.
