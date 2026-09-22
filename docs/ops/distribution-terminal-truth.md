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

YouTube playlist insertion and public promotion require a durable human approval copied from the
review manifest into the outbox and bound to the publication digest and manifest hash. Automated
actors such as `system:auto-publish` are not accepted as distribution approval. An unapproved item
may upload an unlisted/private draft, but it cannot consume playlist-insert or public-promotion
intent.

Spotify video draft creation always performs strict, complete reconcile-before-create. The
`PODCASTER_SPOTIFY_RECONCILE` escape hatch is ignored; disabling it cannot restore blind create.
Video upload retains separate audio/video episode identities, protected historical IDs, the live
mode/operator gates, `uploadType=default`, and fail-closed handling for incomplete pagination or
ambiguous provider state.

Spotify RSS is a separate fenced provider leg. It requires
`VIDEO_SPOTIFY_RSS_PUBLIC_MEDIA_ORIGIN`, `VIDEO_SPOTIFY_RSS_PUBLIC_FEED_URL`, and
`VIDEO_SPOTIFY_RSS_FEED_PATH`. Both public locators must be HTTPS without credentials, query
parameters, or fragments; the media origin must not contain a path prefix. The worker appends the
immutable content-addressed artifact path, hashes the complete externally fetched media, and
requires its byte count and SHA-256 to match before feed mutation. Terminal success additionally
requires an external feed fetch containing the exact outbox GUID and immutable enclosure URL. A
private storage marker, signed SAS URL, inaccessible origin, redirect, content mismatch, or
unverified feed never becomes `externally_verified_public`.

Attempt records are append-only and use unique attempt identities, lifecycle events, authorization
and predecessor references, mutation intents, receipts, and terminal evidence. A weekly decision is
a separate deterministic record. Its precedence is `identity_conflict`, `provider_unknown`,
`manual_action_required`, `partial`, `missed_not_dispatched`, `failed_terminal`, `pending`,
`published_verified_recovered`, then `published_verified`. Recovery never rewrites the failed
attempt. An unknown possible mutation permits read-only reconciliation only and cannot authorize a
blind retry.

Recovery authorization uses the exact `distribution-recovery-authz-v4` envelope and
`distribution-recovery-authz-set-v1` collection manifest. Every envelope has an exact field set,
explicit version and null policy, immutable source/reason/time and predecessor/successor identity,
structured evidence plus digest, an empty bound extensions map, and active/superseded linkage.
The ordered collection count, IDs, active ID, and digest must match the attempt chain exactly.
Every versioned recovery structure is validated against its exact recursive JSON type/schema before
digest or equality checks. Canonical comparison distinguishes null, boolean, integer, string,
object, and array values; floats, non-finite numbers, negative zero, non-string keys, unsupported
containers, duplicate JSON fields, coercions, and legacy/unknown structures fail closed.
Missing, legacy, unknown-version, type-mutated, extra-field, duplicate, reordered, conflicting, or
unrelated authorizations fail closed. Exactly one active authorization may grant the successor's
first mutation-capable claim; concurrent claims have one winner and all later claims are
reconciliation-only.

## Rollout and rollback

`DISTRIBUTION_OUTBOX_ENABLED` is disabled by default. Enable it only after the dedicated
distribution queue and worker use the same release image and the alert queries below are active.
Rollback disables new outbox routing and claims; it must not delete outbox records, consumed
intents, receipts, provider identifiers, or reconciliation schedules. Never restore blind inline
mutation for an item with a consumed intent.

The deployed distribution scheduler advances separate bounded cursors for notification repair and
due reconciliation. Repair durably adds missing provider schedules before dispatch, may replay only
a still-reserved initial notification, and never replays a `sending`, ambiguous, or accepted send.
Queue unavailability or durable repair failure fails the scheduler run instead of advancing its
cursor or reporting a successful heartbeat.

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
