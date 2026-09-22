<!-- markdownlint-disable-file -->
# RPI Phase Details: Production Provider Terminal Truth

## Metadata

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Task slug: `production-provider-terminal-truth`
* Related plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
* Evidence sources: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`; caller requirements; `jmservera/SquadScope-Podcaster#671`, `#678`, `#679`, `#681`; PRs `#680`, `#682`.

## Task-Level Context

The authoritative QA correction separates attempt-level truth from weekly publication-identity truth. Every attempt remains immutable even when a later authorized attempt publishes successfully. Weekly state is a separate deterministic aggregation decision backed by exact identity, manifest/digest, canonical artifact, provider item, and terminal external-provider readback.

W39 remains `missed_not_dispatched`: it has no synth, recorder, video, outbox, or provider attempt. W38 may be classified `published_verified_recovered` only if its exact proof chain satisfies this plan; otherwise that state is an allowed candidate, not assumed proof.

The canonical review subsequently proved six active defects: RV-002, RV-003, RV-004, RV-007, RV-008, and RV-009. P07 is a distinct review-follow-up lifecycle phase and is the only current in-repository implementation boundary. Historical P01–P04 completion is retained as context, not accepted as closure for those findings.

Rusty's 2026-09-22 exact-head review of Frank's revision preserved RV-001/RV-002/RV-003/RV-004/RV-005/RV-007/RV-009 as resolved but rejected P07 because RV-008 remained High. Basher then closed the different-item readback bypass. Ralph independently reviewed final head `fcfa40015ed68d9e38d8432425b7cbd15171e835` and rejected it because exact-item failed readback still authorizes recovery when the latest `provider_unknown` attempt has no durable provider receipts.

Ralph's rejected revision requires exactly one usable receipt per provider, but Livingston's independent review of `e16963243973707ea2557f75f925d3c6935d49ee` proved that any non-empty consumed operation is accepted and exact intent/receipt identity is omitted from durable authorization. Livingston now owns the sole-author correction from `fa3426fa030193e89a58cdb927c81a360df24a03`; Frank is reserved for independent final-SHA review and may not contribute. The correction must retain and recompute an auditable exact structured binding across provider, operation, intent, receipt, predecessor, owner/fence/time/order, publication/artifact identity, terminal readback, and the succeeding attempt/provider expectation.

Frank independently reviewed exact final head `f3c5e643d9068a83e87bd2ef6c8ac120d312519f` and rejected it. The v2 authorization recomputes `prior_attempt_ids`, but not the complete durable content of earlier attempts. After authorization, mutating the first attempt's claimed event timestamp, execution identity, and fencing token left the specifically authorized successor mutation-capable (`read_only=False`). RV-008 and P07-T01 therefore remain open; P07-T06 validation remains complete and P07-T07 cannot close.

Frank now owns the sole-author correction from review head `3529a027d68c3811274237a49202dafc87d33c70`. Livingston is locked out for this cycle, Fry is reserved for fresh independent final-SHA review, and Bender, Hermes, Amy, Leela, Farnsworth, Rusty, Basher, and Ralph may not contribute. The correction must define a versioned canonical attempt-history evidence schema derived from authoritative durable records, persist structured evidence plus digest/version, recompute both at use, bind a precise predecessor boundary and successor expectation, and reject every semantic mutation, duplicate, omission, insertion, reorder, unexpected append, legacy/unknown version, or cross-publication replay.

Fry independently reviewed source commit `bfead2572ae7c98bf82122281ac3a26ff3b91edc` at final head `98eae68fe25b429cc59a36fff97a7154979d2bda` and rejected it. Complete predecessor attempt/event mutation, omission, insertion, duplication, reorder, replay, version drift, nested provider evidence drift, and successor reuse fail closed. However, `_recovery_authorization_binding_is_valid()` selects the first matching authorization and validates only its evidence digest plus winner linkage; it does not bind the authorization record's own `source`, `reason`, `authorized_at`, additional fields, or list cardinality. Mutating those fields, duplicating the matching record, or appending an unrelated record still leaves the successor's first claim mutation-capable. RV-008/P07-T01 therefore remains open.

Fry now owns the sole-author correction from review head `d7eb7ba53b6024812a33a1abc9d2961bd3ddd1b0`; Frank is locked out after the rejected revision. Leela is reserved for fresh independent final-SHA review and may not contribute. Bender, Hermes, Amy, Farnsworth, Rusty, Basher, Ralph, and Livingston remain locked out. The correction must retain the complete v3 predecessor/successor binding while introducing a new versioned canonical complete authorization-envelope schema with explicit field types/null handling, exact unknown-field rejection, a bound extensions map if supported, exact authorization-set cardinality and order/identity, and deterministic rejection of duplicate, reordered, conflicting, unrelated, legacy, incomplete, or type-mutated records. Exactly one unchanged envelope may authorize exactly one mutation-capable claim; concurrent use has one winner and all reuse is read-only.

Leela independently reviewed Fry's exact final head `273f94e0d1fa773e108661f908aca6f34be132c4` and rejected it. The envelope, history, ordering, supersession, concurrency, and replay cases pass, but the v1 set manifest is compared with ordinary Python equality without exact field types. For a one-authorization set, mutating only `authz_count` from integer `1` to boolean `true` or float `1.0` still yields a mutation-capable claim. RV-008/P07-T01 therefore remain High/open. The next sole author must add exact set field/type validation and typed comparison while retaining all current behavior; Leela is locked out after this review and a new eligible independent reviewer is required for the corrected final SHA.

The new cycle assigns Leela as sole revision author from review head `c59669405018f7fa7f9b470568d22e8e474d6f6c`; Basher independently accepted exact reviewed head `905a890c6a2176c799a5cd5f54fbb01d4c791aa9` without contributing. The correction replaces loose Python equality with exact type-tagged canonical byte comparison across the set, envelope, evidence, history, attempt/event records, and successor expectations; validates exact versioned schemas before digest/equality checks; and rejects floats, exponent-overflow/non-finite values, negative zero, non-string keys, unsupported containers, duplicate JSON fields, coercions, and legacy/unknown structures. Focused `241`, locked `916`, and full `3246` tests pass with all static, infrastructure, Checkov, container, exit, and secret-scan gates. P07-T01/P07-T06/P07-T07 are complete for this revision.

Basher's later final-head review rejected `5fdd69f5210053daa742f3690d0fa34c5795f1bc`.
Three #682 disposition claims were not supported by the executable boundary: exhausted chunk
transport/status ambiguity was recorded as terminal failure, checkpoint size verification passed
open when unavailable, and final output was not media-probed before success. Livingston is the
sole revision author for the correction; Frank and Basher are excluded from contribution, Rusty
is reserved for fresh independent review, and Bender, Hermes, and Amy remain excluded.

Source commit `b7e3615ee5c2f0ab904350d581b9fd32938e0f3a` closes the three implementation
gaps. Chunk exhaustion that may follow provider mutation is non-retryable
`mutation_ambiguous` and persists `provider_unknown`; redelivery is read-only. Checkpoint upload
trust now requires an exact size result and otherwise retains local source for recomputation while
removing only the unverified checkpoint. Final output is written to a unique sibling staged MP4,
validated for a positive-duration video stream and requested audio stream, then atomically
promoted; failure preserves any existing destination and cleans the staged candidate. P00-T01 is
complete at upstream reviewed head `d75e3f5523f4810edbcaeef9a217d34cd21825a2` and merge SHA
`7a6d8811bf82507cbdd0b01ba1135bc42e5942f3`, with 18 successful checks. P05-T03,
P05-T04–P05-T06, P06, and merge authorization remain pending Rusty and later gates.

## Cross-Phase Invariants

1. **Recoverably atomic visibility:** upload an immutable content-addressed artifact, verify integrity/readability, then conditionally create the single authoritative outbox record referencing its hash. Queue notification is an idempotent hint; claim revalidates the artifact; verified orphan artifacts are repaired or garbage-collected without provider mutation.
2. **Idempotent enqueue:** the logical key is canonical publication identity plus media kind and provider objective; duplicate enqueue returns the existing item or a conflict.
3. **Fenced ownership:** every repository write, mutation authorization, acknowledgment, and finalization compares owner, claim/attempt ID, lease, and monotonically increasing fence; repository fencing is not claimed to revoke a provider call already in flight.
4. **Consumed intent before I/O:** provider operation intent is durable and atomically marked consumed before network mutation. A call starts only when `remaining_lease > configured_provider_timeout + receipt_persistence_margin`; receipt/ambiguity/readback is durable before queue acknowledgment.
5. **Reconcile before mutate:** every provider transition first performs bounded identity-bound readback; ambiguous absence or identity is never treated as permission to create.
6. **At-most-once mutation:** each consumed intent permits one mutation call. Lease expiry or takeover after intent consumption authorizes read-only reconciliation only, even when provider absence is observed; lost/ambiguous response is quarantined as pending/unknown/manual and never receives a second mutation authorization.
7. **Immutable attempt truth:** attempt lifecycle events and terminal outcomes are append-only. Recovery creates a new authorized attempt and never edits or hides an earlier result.
8. **Truthful weekly success:** a weekly identity succeeds only as `published_verified` or controlled `published_verified_recovered` after exact external verification.
9. **Safe actionable terminal states:** `provider_unknown`, `manual_action_required`, poison, identity conflict, and deterministic non-public states are durably acknowledged from the mutation queue to prevent replay, but produce non-zero execution and alerts.
10. **Deterministic aggregation:** the weekly decision records the evaluated attempt set, rule version, precedence result, proof references, and unresolved conditions.
11. **Sanitized evidence:** identifiers/hashes/state/timestamps/error codes are allowed; secrets, tokens, cookies, signed URLs, bodies, content, and unnecessary PII are rejected.
12. **Rollback preservation:** disabling new routing/claims never deletes attempts, intents, receipts, aggregation decisions, or unknown/manual records and never restores inline blind mutation.

## Identity and Correlation Schema

The implementation may refine field names but must preserve these meanings and uniqueness constraints:

| Record | Required correlation |
|---|---|
| Publication identity | stable weekly identity ID, `accepted_job_id`, `week`, `publish_run_id`, requested provider objectives, `article_sha256`, `manifest_sha256`, publication digest |
| Canonical artifact | stable artifact ID/path, media kind, hash, size, manifest reference, canonical-selection reason/version, creation timestamp |
| Attempt identity | stable `attempt_id`, parent weekly identity, authorization source/reason/time, predecessor attempt when recovery applies, lifecycle event sequence, terminal outcome |
| Outbox identity | stable `outbox_id`, schema version, attempt ID, artifact reference, enqueue timestamp, enqueue source/version |
| Claim/execution | `claim_owner`, `claim_id`, `execution_id`, attempt ID, verification attempt, `fencing_token`, `claimed_at`, `lease_expires_at`, heartbeat, provider timeout, receipt margin, remaining deadline |
| Provider intent | provider, operation, stable mutation intent ID, consumed flag/timestamp/fence, mutation authorization ID, expected publication/artifact identity, expected provider item ID when known, precondition/readback fingerprint |
| Provider receipt | attempt and intent IDs, mutation request class, transport class/status, provider item ID, native state, ambiguity class, sanitized code, receipt timestamp |
| Terminal readback | attempt/provider item IDs, external readback source, visibility/publication/processing state, identity-match result, `checked_at`, `confirmed_at`, terminal classification |
| Reconciliation schedule | `next_reconcile_at`, verification attempt/budget/horizon, active schedule token, last scheduled/executed timestamps, exhaustion reason |
| Weekly aggregation | stable decision ID, rule/version, evaluated attempt IDs, precedence state, winning proof references, unresolved ambiguity list, decision timestamp, worker exit class |

Provider item IDs are treated as operational identifiers and never combined with channel/account names, email addresses, cookies, titles, or payload content in metric dimensions.

## Safe State Model

### Attempt lifecycle and terminal states

`pending → claimed → reconciling → intent_persisted → mutating → receipt_persisted → verifying`

An attempt terminates as exactly one of:

* `published_verified` — exact identity, manifest/digest, canonical artifact, provider item, and terminal external state are proven for this attempt.
* `partial` — some requested objective or proof is incomplete.
* `provider_unknown` — mutation may have occurred but identity/state is unprovable; no automatic mutation retry.
* `manual_action_required` — unsupported or deterministic provider/operator action is required; no automatic mutation retry.
* `failed_retryable_pre_mutation` — no mutation intent was consumed and a bounded retry is safe.
* `failed_terminal` — deterministic non-provider/transformation/configuration failure.
* `cancelled_safe` — cancellation occurred with retained proof that no mutation was issued.

Terminal attempt outcomes are immutable. `pending_provider` remains a non-terminal lifecycle condition with a separate reconciliation budget. A safe retry creates a new attempt with explicit authorization and predecessor linkage.

### Weekly identity states and precedence

Weekly states are `identity_conflict`, `provider_unknown`, `manual_action_required`, `partial`, `missed_not_dispatched`, `failed_terminal`, `pending`, `published_verified_recovered`, and `published_verified`.

Aggregation evaluates in that order:

1. `identity_conflict` for manifest/digest mismatch, wrong canonical artifact, conflicting provider item, or unresolved duplicate ambiguity.
2. `provider_unknown` when any possibly mutated attempt remains unproven.
3. `manual_action_required` while operator action or post-action readback remains open.
4. `partial` when requested objectives remain incomplete.
5. `missed_not_dispatched` after cutoff without an authorized downstream attempt or proven Azure arrival.
6. `failed_terminal` when all attempts failed deterministically with no safe continuation.
7. `pending` while an authorized attempt remains inside its reviewed processing/reconciliation window.
8. `published_verified_recovered` when a succeeding authorized attempt satisfies all green proof and earlier non-green attempts remain referenced.
9. `published_verified` when the clean verified proof exists with no earlier non-green attempt.

Every non-green state exits non-zero and blocks canary/four-cycle acceptance. Queue acknowledgment is independent from process success. ACA retry never grants mutation authority.

### Controlled recovery

Recovery requires bounded reconciliation proving the prior attempt safe for a new mutation, or explicit operator authorization based on that proof. Unknown mutation cannot be blindly retried. The recovery authorization, predecessor attempt, succeeding attempt, receipts, and terminal readback are all retained. A `published_verified_recovered` weekly decision is invalid if any earlier possibly-mutated attempt remains unresolved.

## Phase Index

| Phase ID | Name | Status | Detail sections |
|---|---|---|---|
| P00 | Prevent and detect W39-class dispatch blockage | Complete through upstream PR #773 and the Podcaster receipt/absence boundary | P00, P00-T01–P00-T03 |
| P01 | Establish the durable outbox and immutable attempt contract | Complete in Amy correction cycle | P01, P01-T01–P01-T04 |
| P02 | Implement reconcile-first provider state machines | Complete in Amy correction cycle | P02, P02-T01–P02-T03 |
| P03 | Make execution, cleanup, and weekly aggregation truthful | Complete in Amy correction cycle | P03, P03-T01–P03-T03 |
| P04 | Prove safety with focused tests and repository validation | Complete in Amy correction cycle | P04, P04-T01–P04-T03 |
| P07 | Review-follow-up closure for terminal truth | Complete for the current correction; Basher accepted exact reviewed head `905a890` | P07, P07-T01–P07-T07 |
| P05 | Deliver reviewed, reversible implementation and exact W39 production acceptance | P05-T01–P05-T02 complete; P05-T03 rejected by Basher at `86f96bb` and under Livingston lifecycle-budget correction; P05-T04–P05-T06 future | P05, P05-T01–P05-T06 |
| P06 | Verify four consecutive post-fix production cycles | Blocked by accepted exact-W39 production run and elapsed cycles | P06, P06-T01–P06-T02 |

## Implementation Execution Boundary

* Declared scope: P05-T03 stale-owner downstream mutation correction after Basher rejected exact head `df473dc0c059680b9c454ddab263c5c454e2ef2b`, plus focused/full validation, #682/#684 evidence, tracking, commit, and push.
* Current task: Frank completed one authoritative persisted video execution claim and claim-bound boundary permits spanning final promotion, immutable archive, outbox creation, queue notification/sent marking, direct-provider durable intent/mutation, and terminal success in source/evidence commit `6bba1275efbe30754230db7cfe3e67522f7033df`. Final narrative commit, push, GitHub evidence, and Rusty final-SHA review remain.
* Ownership invariants: exact job/owner/claim/execution/fence and persisted visibility/lease expiries are validated by durable readback and CAS. Missing, corrupt, unavailable, mismatched, expired, or ambiguous authority fails closed. Target records carry the source permit identity, ordinary takeover cannot cross an active permit, and forced transfer invalidates stale completion.
* Idempotency/takeover: immutable artifacts and deterministic outbox identity remain reusable; consumed direct-provider intent never grants a second mutation; a successor reconciles existing artifacts/intents instead of recreating provider effects.
* Delivery boundary: the incident worktree and existing `squad/incident-provider-terminal-truth` branch/PR only. Keep #684 draft/open and #682 open. Do not merge, deploy, dispatch W39, mutate a provider, or claim P05-T04–P05-T06/P06 credit.
* Validation result: exact ownership probes `13`; focused race/provider suite `416`; locked contract `940`; full repository `3302 passed, 2 skipped, 2 deselected, 1 warning`; Ruff/format/compile/diff, Bicep, exact/CI Checkov, Dockerfile baseline, container/worker smoke, rebuilt Compose integration, and secret/PII scan passed or retained the documented baseline without weakening.

* Declared scope: P05-T03 lifecycle-budget and process-shutdown correction after Basher rejected exact head `86f96bb03c006bf0b307cd461b15cd18cfab5ed1`, plus required validation, #682/#684 evidence, tracking, PR narrative, commit, and push.
* Current task: implementation and validation complete in source commit `f6b713530947236c04f822289343d3f105cc6dc9`; corrected GitHub evidence, final push, and Rusty review remain.
* Revision author: Livingston alone.
* Fresh independent reviewer: Rusty is reserved and has not contributed.
* Excluded contributors: Leela and Basher may not author, advise, pair, inspect, suggest, or contribute during this correction. Bender, Hermes, and Amy remain excluded. Rusty may perform only the fresh independent final-SHA review after implementation and validation complete.
* Source boundary: only `/home/azureuser/source/worktrees/SquadScope-Podcaster-incident`, limited to narrowly identified downstream owners, tests, operator documentation, and RPI/PR tracking artifacts. Do not modify `/home/azureuser/source/SquadScope-Podcaster`, switch/create branches, create a replacement PR, deploy, or mutate production.
* Validation boundary: remaining-budget cap, insufficient-budget pre-launch rejection, bounded TERM/KILL/reap branches, process-group targeting, pipe closure, queue visibility deadline propagation, fixed renewed lease ownership, lifecycle/lease expiry no-downstream behavior, generated/truncated/corrupt media, upload ambiguity, checkpoint verification, P07/RV, W39, locked, full repository, static, infrastructure, container, Compose, and secret/PII gates without weakening.
* Delivery boundary: commit and push only the existing branch and refresh only PR #684 after validation. P05-T03 remains pending Rusty acceptance of the corrected final SHA. Keep #684 open/draft/blocked and #682 open. Do not merge, deploy, dispatch workflows, mutate providers, execute W39, or claim P05-T04–P05-T06/P06 credit.
* Validation result: exact probes `30`; compose/job runner `442`; locked lifecycle/provider contract `726`; full repository `3283 passed, 2 skipped, 2 deselected, 1 warning`; all static, Bicep, exact/CI Checkov, Dockerfile, container, worker-exit, Compose, and secret/PII gates passed or retained their documented baseline without weakening.

## Implementation Marker Reconciliation

| Marker(s) | Current disposition | Completion expectation |
|---|---|---|
| P00-T01 | Complete | `jmservera/SquadScope#773` reviewed head `d75e3f5523f4810edbcaeef9a217d34cd21825a2` merged as `7a6d8811bf82507cbdd0b01ba1135bc42e5942f3` at `2026-09-22T09:20:43Z`; 18 PR checks and merge-SHA CI/security/release/deploy workflows succeeded; canonical identity, append-only trusted receipts, missing/terminal monitoring, and fail-closed weekly evidence satisfy the prevention/detection and durable dispatch-evidence contract |
| P00-T02–P00-T03 | Complete | Durable sanitized intent/arrival correlation, missing-arrival signal/alerts, API and terminal-provider fixture proof |
| P07-T01 | Complete for Leela correction | Exact recursive typed canonical contracts cover set/envelope/evidence/history/attempt-event/successor structures; boolean/float count and all scalar substitutions fail closed |
| P07-T02 | Complete; RV-002 resolved | Expired/abandoned `enqueue_started` reservations are due again while CAS fencing leaves one current owner |
| P07-T03 | Complete; RV-004 resolved | Bounded resumable migration backfills pre-index references and cleanup fails closed until completeness is proven |
| P02-T01, P04-T01–P04-T02 | Complete for RV-001/RV-005; regression required | Preserve read-only promotion convergence and accurate unprovable identity evidence |
| P07-T04 | Complete; RV-003 resolved | Emitted/query vocabulary and active-depth absence semantics independently verified |
| P07-T05 | Complete; RV-009 resolved | Four-cycle evaluation recomputes proof from raw evidence and rejects identity/readback/duplicate/auth mismatch |
| P07-T06 | Complete for Leela correction | Focused `241`, locked `916`, full `3246`, Ruff/format/compile/diff, Bicep/Checkov, container/exit, and secret/PII gates passed without weakening |
| P07-T07; P05-T01 | Complete | Basher accepted exact executable head `905a890`; all later commits through `da84b6b` changed tracking/PR narrative only; #684 is linked to #773 |
| P05-T02 | Complete | Final-SHA review found no executable drift or High/Critical issue; all 13 hosted checks on the final pushed tracking head succeeded |
| P05-T03 | Livingston implementation complete; Rusty acceptance pending | All 67 #682 threads remain resolved. Structural ffprobe plus complete ffmpeg decode now derives admission and timeout from the authoritative queue visibility deadline and fixed renewed editor-lease window, reserves bounded cleanup/promotion time, and uses bounded TERM/KILL/reap stages. Source commit `f6b713530947236c04f822289343d3f105cc6dc9`; corrected evidence and Rusty's independent acceptance remain required before this marker closes. |
| P05-T05 | Expanded prerequisite gate | Exact merge-SHA-derived upstream and Podcaster artifacts must be deployed and every review/deployment/readiness/rollback gate cleared before real W39 execution |
| P05-T06 | New authoritative gate | Reconcile all W39 history/provider candidates before mutation, then prove exact GitHub-to-provider execution and authoritative terminal external readback; ambiguity fails closed/manual-action |
| P06-T01–P06-T02 | Preserved | Four future qualifying cycles remain required; exact W39 does not automatically count |
| P01-T04, P02-T02 | Implemented; dependency verification | Preserve safe migration and Spotify fail-closed behavior; extend only for schema compatibility |
| W38 classification | Evidence-conditional | `published_verified_recovered` only with exact proof; otherwise retain candidate status and all attempt evidence |
| W39 classification | Settled | `missed_not_dispatched` |
| Original PC-001–PC-009 | Historical; no change | Preserve existing critique and dispositions; no second critique |

Leela alone authored the active P07-T01 correction. Basher completed the fresh independent review after implementation and validation without contributing. Bender, Hermes, Amy, Farnsworth, Rusty, Ralph, Livingston, Frank, and Fry were excluded entirely from contribution or advice.

### Implemented Surface Disposition

* **State-model and narrative correction:** later update `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`, `.copilot-tracking/pr/pr.md`, PR handoff, and production evidence summaries to preserve every attempt, use W38 recovery only with exact proof, keep W39 `missed_not_dispatched`, and separate downstream hardening from W39 root cause.
* **W39 additions:** P00-T01 selects the exact upstream dispatch owner; then extend its workflow/client/status store plus the selected Podcaster/Azure ingress-arrival metadata, monitoring/alert infrastructure, and cross-boundary tests.
* **Review-follow-up additions:** implement only RV-002/RV-003/RV-004/RV-008/RV-009 behavior in existing scheduler/outbox/storage/telemetry/aggregation owners, then reconcile RV-007 delivery evidence; preserve resolved RV-001/RV-005 behavior.
* **No-change safety surfaces:** preserve publication schema/sanitization, disabled-by-default routing, Spotify fail-closed/manual handoff, and externally-verified-public exit semantics except for compatible P00 correlation.
* **Removed/narrowed:** no assumed W38 recovery, no W39 downstream-cause claim, no Podcaster-only incident canary, no blind retry, and no acceptance credit for provider hardening without upstream dispatch/Azure evidence.

<!-- rpi:phase id=P00 -->
## P00: Prevent and detect W39-class dispatch blockage

### Context

W39 was blocked before Azure dispatch and has no synth, recorder, video, outbox, or provider execution. Downstream hardening cannot establish or fix that root boundary.

### Intent

Identify the owning blocked stage, make accepted weekly intent through first Azure durable arrival observable and alertable, and prove the same boundary in integration and production canary evidence.

### Boundaries

* Included: upstream intent, dispatch attempt/result, Azure API acceptance, first Azure-side durable arrival, correlation, missing-arrival alerts, and cross-repository handoff.
* Excluded: attributing W39 to downstream execution without evidence; direct modification of `/home/azureuser/source/SquadScope`; using a Podcaster-only injection as W39 remediation proof.

### Likely Targets

* The owning upstream repository/worktree and workflow/client that dispatches weekly publication.
* Podcaster/Azure ingress, queue, execution metadata, monitoring, integration tests, and coordinated PR documentation.

### Dependencies

* Corrected research and read-only production/GitHub/Azure evidence sufficient to identify the blocked stage.

### Validation Expectations

* Accepted intent, successful dispatch, blocked dispatch, duplicate dispatch, delayed Azure arrival, and no-arrival alert fire/clear are independently diagnosable.

### Completion Evidence

* Exact owning component, correlation schema, reviewed arrival SLO, code/test links in the owning repository, and sanitized cross-boundary trace in the changes record.

### Unresolved Items

* Exact W39 blocked stage is intentionally resolved during P00-T01 from durable evidence.

<!-- rpi:task id=P00-T01 -->
### P00-T01: Trace and control the W39 dispatch boundary

#### Context

The authoritative correction establishes location of failure relative to Azure but not the exact upstream component or mechanism.

#### Intent

Trace the accepted W39-class weekly intent to the blocked stage, implement prevention/fail-closed detection in the owning repository, and preserve evidence when dispatch cannot occur.

#### Boundaries

* Included: metadata/log/workflow trace, ownership, deterministic failure classification, bounded retry only where dispatch absence is provable.
* Excluded: provider/outbox changes presented as W39 root-cause remediation.

#### Likely Targets

* Upstream scheduled workflow/action, dispatch client, status persistence, and owner tests.

#### Dependencies

* Access to upstream/Azure evidence.

#### Validation Expectations

* Reproduced blocked-dispatch class records terminal dispatch evidence and cannot silently report success.

#### Completion Evidence

* Root boundary evidence, owning PR/task, and focused prevention/detection tests.

#### Unresolved Items

* None after the owning stage is established.

<!-- rpi:task id=P00-T02 -->
### P00-T02: Persist dispatch-to-Azure correlation and missing-arrival alerts

#### Context

W39 lacked Azure execution, so internal downstream evidence alone cannot distinguish never-dispatched work from downstream failure.

#### Intent

Bind accepted intent, dispatch result, Azure API acceptance, and first durable Azure arrival using one sanitized stable correlation; alert when arrival is absent beyond a reviewed window.

#### Boundaries

* Included: idempotent correlation, duplicate handling, clock/skew-safe timestamps, low-cardinality telemetry, alert route/runbook, fire/clear.
* Excluded: article bodies, credentials, tokens, signed URLs, provider content, and direct/manual invocation as substitute evidence.

#### Likely Targets

* Upstream dispatch metadata, Podcaster ingress/queue/execution metadata, monitoring and infrastructure.

#### Dependencies

* P00-T01 ownership and correlation key.

#### Validation Expectations

* Missing arrival fires; authoritative first arrival clears; duplicate dispatch does not create duplicate publication identity; downstream failure remains separately classified.

#### Completion Evidence

* Correlation example, rule ID/route/runbook, and deterministic alert tests.

#### Unresolved Items

* Arrival SLO is selected from observed scheduling/dispatch latency and recorded in implementation evidence.

<!-- rpi:task id=P00-T03 -->
### P00-T03: Prove the upstream-to-provider integration boundary

#### Context

A Podcaster-only injection bypasses the W39 failure mode.

#### Intent

Add an integration scenario beginning at the authoritative upstream weekly-publication boundary and ending in correlated Azure arrival plus a deterministic terminal-provider fixture/readback.

#### Boundaries

* Included: upstream accepted intent, dispatch, first Azure durable arrival, downstream correlation, provider state fixture, and failure localization.
* Excluded: live provider credentials in ordinary CI and provider-only canary acceptance.

#### Likely Targets

* Cross-repository integration workflow/tests and Podcaster deployment assertions.

#### Dependencies

* P00-T01–P00-T02 and downstream identity schema.

#### Validation Expectations

* The test fails independently for blocked dispatch, missing Azure arrival, downstream execution failure, and missing external terminal readback.

#### Completion Evidence

* Coordinated workflow/test run URLs and correlation evidence.

#### Unresolved Items

* None.

## Revision Governance

* The authoritative correction supersedes every assumption that W38 was unpublished, missed, or awaiting recovery.
* W39 is the only active missed-publication incident and ends before Azure arrival; no W39 downstream execution may be inferred.
* The original critique remains unchanged as historical evidence. No second critique was run because this is an authoritative post-implementation user correction.
* Historical note: Amy's correction cycle claimed RV-002, RV-003, and RV-004 complete, but the canonical independent review rejected those claims. Fry's review of Leela's P07 final revision resolved RV-003/RV-007 and left RV-002/RV-004/RV-008/RV-009 open. RV-001/RV-005 remain resolved; RV-006 remains resolved at planning level without claiming any GitHub thread resolved.

<!-- rpi:phase id=P01 -->
## P01: Establish the durable outbox contract

### Context

#680 evidence is append-only and CAS-protected, but provider work is inline and lacks claim fencing. Tightening exits before this phase would allow ACA retries to revisit provider mutation.

### Intent

Create a backward-compatible durable boundary where artifact integrity, immutable attempt history, enqueue, claim ownership, mutation authorization, receipt, terminal provider evidence, and weekly aggregation decisions are authoritative.

### Boundaries

* Included: schema/versioning, immutable artifact/conditional outbox protocol, storage/CAS, queue contract, lease/fence/consumed intent, reconciliation scheduler, migration, feature routing.
* Excluded: provider mutation behavior and truthful exit activation.

### Likely Targets

* `podcaster/publication_state.py`: schema compatibility, sanitized evidence, aggregate definitions.
* `podcaster/storage.py`, `podcaster/queue.py`: immutable artifact verification, conditional create/CAS, queue/schedule contract, acknowledgment.
* New focused module such as `podcaster/distribution_outbox.py`: outbox repository and claim state machine.
* `podcaster/video/job_runner.py`, `podcaster/orchestration.py`: enqueue boundary and feature routing.

### Dependencies

* Current main/#680 and verified rendered archive.

### Validation Expectations

* Unit/fault tests prove every artifact/outbox interruption state, idempotent enqueue/notification, lease/fence/consumed-intent correctness, stale-owner rejection, and reconciliation scheduling.

### Completion Evidence

* Phase completion is restored by the immutable attempt ledger, exact weekly proof aggregation, paginated scheduler/cleanup corrections, and focused/full validation recorded in the changes artifact.

### Unresolved Items

* None.

<!-- rpi:task id=P01-T01 -->
### P01-T01: Define outbox and correlation schemas

#### Context

Research C13 shows an adequate canonical identity foundation; the new schema must extend rather than duplicate it.

#### Intent

Implement versioned allowlisted records described in `Identity and Correlation Schema`, including immutable attempt events/outcomes, recovery authorization, terminal readback, retention, and separate weekly aggregation decisions.

#### Boundaries

* Included: field validation, uniqueness, serialization, retention, sanitization, backward read compatibility.
* Excluded: raw provider payload/body persistence and secret-bearing identifiers.

#### Likely Targets

* `podcaster/publication_state.py`; new outbox model/repository module; `tests/test_publication_state.py`.

#### Dependencies

* #680 canonical identity semantics.

#### Validation Expectations

* Round-trip, malformed identity, unsafe key/value, schema upgrade, immutable-history, duplicate key, deterministic re-aggregation, out-of-order receipt, and retention tests.

#### Completion Evidence

* Versioned schema and field-level sanitization tests pass; changes record includes a redacted example.

#### Unresolved Items

* None.

<!-- rpi:task id=P01-T02 -->
### P01-T02: Implement atomic enqueue and fenced claim lifecycle

#### Context

The outbox must make redelivery safe before provider work leaves the editor flow.

#### Intent

Implement the concrete recoverable commit protocol and claim lifecycle:

1. write the rendered artifact once to an immutable content-addressed path;
2. read it back and verify expected hash/size;
3. conditionally create the authoritative outbox record referencing that artifact and canonical identity;
4. idempotently notify the work queue after record creation;
5. claim only after artifact revalidation;
6. repair missing notifications and garbage-collect verified orphans after a retention window when no outbox record exists.

Claims persist heartbeat/expiry/fence and atomically consume mutation intent. Before provider I/O, require `remaining_lease > provider_timeout + receipt_persistence_margin`; after consumption, takeover is reconciliation-only.

#### Boundaries

* Included: immutable artifact commit, conditional authoritative outbox create, deterministic enqueue key, idempotent queue notification, orphan repair, one-item claim, stale-owner rejection, consumed intent, poison transition.
* Excluded: unsupported cross-store distributed transactions; revoking an already-issued provider request; success based solely on queue receipt.

#### Likely Targets

* `podcaster/storage.py`, `podcaster/queue.py`, new outbox module, `tests/test_distribution_outbox.py`.

#### Dependencies

* P01-T01.

#### Validation Expectations

* Concurrent claims yield one owner; takeover increments fence; stale owners cannot persist/ack; lease expiry before I/O permits a separately authorized new attempt, while expiry during/after a consumed intent permits read-only reconciliation only. Tests interrupt after artifact upload, verification, outbox create, and queue notify and prove repair without duplicate provider intent. Reference enumeration and orphan cleanup remain safe and make progress beyond 5,000 records.

#### Completion Evidence

* Fake-clock/concurrency/commit-protocol suite and W21/W22 outbox portions pass.

#### Unresolved Items

* None.

<!-- rpi:task id=P01-T03 -->
### P01-T03: Schedule deduplicated provider reconciliation

#### Context

Pending processing/readback and durable unknown states must continue progressing after mutation queue acknowledgment without using ACA retry as mutation permission.

#### Intent

Persist `next_reconcile_at`, verification attempt/budget/horizon, storage continuation/shard position, and one active schedule token. A scheduler scans due authoritative records through bounded pages/shards that eventually visit every retained record, conditionally creates one token, and launches/feeds a one-item reconciliation worker under normal claim/fence rules.

#### Boundaries

* Included: due-time scheduling, token deduplication, read-only takeover, restart recovery, terminal exhaustion/manual transition, expected timer no-work semantics, alert fire/clear.
* Excluded: blind mutation redelivery and concurrent reconciliation for one provider intent.

#### Likely Targets

* New outbox/scheduler module, `podcaster/queue.py`, dedicated worker entrypoint, ACA/Bicep trigger configuration, monitoring.

#### Dependencies

* P01-T01–P01-T02.

#### Validation Expectations

* Fake-clock tests prove due/not-due selection, one active token, process restart, lost notification repair, traversal beyond the 5,000-record boundary, verification budget exhaustion, successful clearing, and no second mutation after consumed intent.

#### Completion Evidence

* Scheduler transition table, ACA retry/empty-drain semantics, and passing test matrix in the changes record.

#### Unresolved Items

* None.

<!-- rpi:task id=P01-T04 -->
### P01-T04: Preserve #680 evidence and migrate routing safely

#### Context

Historical evidence may contain verified, unknown, manual, or ambiguous states and must not be blindly replayed.

#### Intent

Keep existing evidence readable, gate new routing behind a disabled-by-default flag, and create only reconciliation-safe migration/backfill records.

#### Boundaries

* Included: compatibility reader, idempotent backfill, skip/mark rules for historical ambiguity, flag-off behavior.
* Excluded: automatic mutation for historical records or destructive evidence rewrite.

#### Likely Targets

* `podcaster/publication_state.py`, `podcaster/video/job_runner.py`, config/Bicep settings, monitoring read paths.

#### Dependencies

* P01-T01–P01-T03.

#### Validation Expectations

* #680 fixtures remain readable; repeated backfill is stable; unknown/manual history never becomes mutation-ready; flag-off preserves current routing.

#### Completion Evidence

* Compatibility and migration tests plus rollback notes in the changes record.

#### Unresolved Items

* None.

<!-- rpi:phase id=P02 -->
## P02: Implement reconcile-first provider state machines

### Context

YouTube has authoritative status/processing readback but ambiguous resumable-session initiation. Spotify’s creator mutation interface is unsupported/unverified and lacks reliable immutable identity in all cases.

### Intent

Run every provider operation under a valid fence and sufficient lease budget, atomically consume mutation intent before I/O, use bounded identity-bound reconciliation before mutation, and make authoritative readback the success proof. A takeover after consumed intent is always read-only.

### Boundaries

* Included: YouTube draft/process/promote/readback; Spotify reconcile/manual; provider receipts.
* Excluded: exactly-once claims and unsupported Spotify unattended promotion.

### Likely Targets

* `podcaster/video/distribution.py`, `podcaster/video/youtube.py`, `podcaster/video/youtube_publish.py`, `podcaster/publish.py`, provider tests.

### Dependencies

* P01 complete.

### Validation Expectations

* No provider mutation can run before intent consumption, fence validation, and lease-budget check; ambiguity or in-flight expiry never leads to a second mutation.

### Completion Evidence

* Existing provider transition evidence remains valid for completed surfaces; RV-001 and RV-005 corrections and tests are required before phase completion is restored.

### Unresolved Items

* None.

<!-- rpi:task id=P02-T01 -->
### P02-T01: Enforce the YouTube draft-processing-public lifecycle

#### Context

Current upload validates privacy late and promotion checks privacy but not processing details. W25 identifies ambiguous resumable-session recreation.

#### Intent

Reject invalid initial privacy at configuration/payload parsing; persist and consume one upload-session intent; require lease margin before the call; verify expected video identity, upload status, and processing success; consume one promotion intent; verify final `public`. Expiry/lost response after either consumed intent schedules read-only reconciliation.

#### Boundaries

* Included: `private`/`unlisted` initial values, bounded polling/readback, expected identity metadata/hash checks where provider supports them, public promotion/readback.
* Excluded: initial public upload; recreation after ambiguous session initiation; success from 2xx mutation response alone.

#### Likely Targets

* `podcaster/video/distribution.py`, `podcaster/video/youtube.py`, `podcaster/video/youtube_publish.py`, related YouTube tests.

#### Dependencies

* P01 fencing and intent/receipt records.

#### Validation Expectations

* Bad public config causes zero provider calls; timeout/500/lost initiation becomes unknown; lease expiry before I/O may retry under a fresh claim, while expiry during/after I/O can only reconcile; processing failure blocks promotion; public readback mismatch is non-success.

#### Completion Evidence

* YouTube state-machine tests pass and W25 has exact replacement evidence.

#### Unresolved Items

* None.

<!-- rpi:task id=P02-T02 -->
### P02-T02: Enforce Spotify bounded reconciliation and manual handoff

#### Context

Existing single-attempt behavior is safer than blind retry, but title/listing and optional incomplete pagination cannot prove identity/absence.

#### Intent

Require complete bounded listings/readback when production automation is attempted; mutate once only when exact identity/absence is provable and intent is consumed with sufficient lease margin; otherwise persist unknown/manual handoff. Operator publication after handoff must be followed by authoritative readback of the expected provider item before acceptance.

#### Boundaries

* Included: immutable/provider ID when known, complete pagination proof, bounded settle reads, draft readback, explicit manual instructions.
* Excluded: title-only identity as proof, incomplete absence proof, repeated create/publish, unsupported automatic public promotion.

#### Likely Targets

* `podcaster/publish.py`, `tests/test_publish.py`.

#### Dependencies

* P01.

#### Validation Expectations

* 408/429/500/timeout/unreadable accepted responses and in-flight lease expiry do not retry mutation; ambiguous/multiple/incomplete candidates become unknown/manual; draft and handoff are not success; post-handoff external readback is required.

#### Completion Evidence

* Spotify semantic tests and sanitized handoff evidence pass.

#### Unresolved Items

* None; the supported-contract gap is deliberately resolved as manual handoff.

<!-- rpi:task id=P02-T03 -->
### P02-T03: Persist provider intent, receipts, and terminal readback

#### Context

Provider mutations and verification must be reconstructable without storing unsafe payloads.

#### Intent

Append attempt identity, mutation authorization/intent, transport classification, provider item/state, ambiguity, verification source, timestamps, terminal attempt outcome, and weekly decision proof references to the canonical evidence chain.

#### Boundaries

* Included: allowlisted receipt summaries and correlation.
* Excluded: authorization material, payload bodies, signed URLs, cookies, creator account PII, titles/descriptions.

#### Likely Targets

* `podcaster/publication_state.py`, outbox module, provider adapters, `podcaster/monitoring.py`.

#### Dependencies

* P01-T01 and provider tasks.

#### Validation Expectations

* Crash after mutation but before receipt leaves durable consumed intent and read-only reconciliation schedule; crash after receipt reuses receipt; a later authorized success leaves the failed/unknown predecessor unchanged; sequence/CAS remains append-only.

#### Completion Evidence

* Mutation-to-receipt fault tests and sanitization tests pass.

#### Unresolved Items

* None.

<!-- rpi:phase id=P03 -->
## P03: Make execution, cleanup, and provider aggregation truthful

### Context

ACA sees only process exit. The execution must distinguish mutation queue acknowledgment from business success and must close #682 boundedness/cleanup risks rather than porting unsafe lifecycle code.

### Intent

Make process exit authoritative from deterministic weekly aggregation, keep retry paths safe through new authorized attempts, bound affected operations, and alert on all actionable non-green states.

### Boundaries

* Included: aggregation, exit/reporting, queue disposition, relevant #682 lifecycle fixes, telemetry and alerts.
* Excluded: treating operational noise reduction as permission to return success.

### Likely Targets

* `podcaster/video/job_runner.py`, dedicated outbox worker entrypoint, affected lifecycle modules, `podcaster/monitoring.py`, `infra/modules/aca-video.bicep`, alert infrastructure.

### Dependencies

* P01–P02.

### Validation Expectations

* Process/ACA outcomes and alerts agree with durable provider evidence.

### Completion Evidence

* Historical P03 evidence was later rejected for RV-002, RV-003, and RV-004. Fry's P07 review resolved RV-003 but left RV-002 and RV-004 open. Expanded RV-006 thread closure evidence remains P05 delivery work.

### Unresolved Items

* None.

<!-- rpi:task id=P03-T01 -->
### P03-T01: Implement truthful aggregation and queue disposition

#### Context

Current `main()` counts only `failed`; drafts/pending/readback-only may be completed upstream.

#### Intent

Aggregate requested provider objectives from immutable attempts using the plan's precedence. Return zero only for `published_verified` or controlled `published_verified_recovered`; return non-zero for every other weekly state, unexpected empty drains, or skipped required work. Timer-driven polls may return no-work success only when no publication decision was expected; ACA retries never create mutation authority.

#### Boundaries

* Included: partial, pending, draft/private/unlisted, unknown, manual, failed, poison, skipped-required, and empty-drain behavior.
* Excluded: reprocessing unknown/manual by mutation-message redelivery.

#### Likely Targets

* `podcaster/video/job_runner.py`, new outbox worker entrypoint, failure reporting, ACA command/config.

#### Dependencies

* P02 terminal states.

#### Validation Expectations

* Exit 0 only for the two green weekly states with complete exact proof; failed attempts remain queryable after recovered success; unknown/manual messages are acknowledged after durable evidence but execution exits non-zero; safe pre-mutation recovery creates a new authorized attempt.

#### Completion Evidence

* Entry-point tests cover complete lattice and ACA command executes the truthful entrypoint.

#### Unresolved Items

* None.

<!-- rpi:task id=P03-T02 -->
### P03-T02: Bound execution, cleanup, and one-item worker behavior

#### Context

W17–W24 and W26–W29 cover cleanup scope, partial artifacts, visibility budgets, subprocess/storage deadlines, resume cleanup, finalization, and multi-message drain.

#### Intent

Rework only required lifecycle safety behavior from current main, with remaining-deadline propagation, deterministic cleanup/finalization, complete bounded scheduler enumeration, and reference-safe orphan cleanup that progresses at scale.

#### Boundaries

* Included: scoped prefix cleanup, partial file cleanup, visibility/budget invariants, bounded subprocess/storage/finalization, replay guard behavior, terminal cleanup, one message.
* Excluded: wholesale #682 stage-budget architecture.

#### Likely Targets

* `podcaster/video/editor.py`, `podcaster/video/edl_render.py`, `podcaster/video/intermediates.py`, `podcaster/video/process.py`, `podcaster/video/video_gen.py`, `podcaster/video/recorder.py`, `podcaster/video/job_runner.py`, `infra/modules/aca-recorder.bicep`, `infra/modules/aca-video.bicep`.

#### Dependencies

* P01 outbox decouples provider distribution from editor visibility.

#### Validation Expectations

* Each W17–W24/W26–W29 row has a focused regression test or explicit current-main non-port proof; operations terminate inside remaining budget; scheduler and cleanup tests cross the former 5,000-path boundary without starvation or unsafe deletion.

#### Completion Evidence

* Thread matrix evidence is recorded before replies/resolution in P05-T03.

#### Unresolved Items

* None.

<!-- rpi:task id=P03-T03 -->
### P03-T03: Add provider-state telemetry and alerts

#### Context

Current monitoring can display evidence but lacks provider-age/lag alerts.

#### Intent

Emit low-cardinality attempt/weekly/provider metrics and actionable logs; deploy the plan’s exact alert contract with distinct action routes, executable absence/depth/heartbeat rules, configurable thresholds, rule IDs, runbooks, and deterministic fire/clear fixtures.

#### Boundaries

* Included: pending age, claim latency/lease loss, unknown, manual, YouTube non-public, Spotify draft, poison, verification lag.
* Excluded: raw provider IDs as metric dimensions and internal success as provider proof.

#### Likely Targets

* `podcaster/monitoring.py`, logging/signals, `infra/`, deployment tests, existing operator docs.

#### Dependencies

* P01 schema and P02 state vocabulary.

#### Validation Expectations

* Each alert contract row is deployed with its threshold/window, distinct enforceable action route, executable missing-data behavior, deterministic fire fixture, and authoritative clear fixture.

#### Completion Evidence

* Telemetry tests, Bicep validation, alert inventory, severity/owner/runbook, and sample sanitized events in changes.

#### Unresolved Items

* None.

<!-- rpi:phase id=P04 -->
## P04: Prove safety with focused tests and repository validation

### Context

Existing tests cover portions of ambiguity and evidence, but not fenced outbox crash boundaries, YouTube processing, full exit lattice, or production routing/rollback.

### Intent

Prove semantics under faults and retain the full regression/quality baseline defined in the plan’s locked test contract.

### Boundaries

* Included: owned tests, fault injection, full validation.
* Excluded: skipped/weakened gates and mock-only production acceptance.

### Likely Targets

* Test files enumerated in `Locked Test and Validation Contract`.

### Dependencies

* P01–P03 implementation complete.

### Validation Expectations

* No test removals; new-file/case/helper ceilings respected; semantic and regression suites pass.

### Completion Evidence

* Prior command/SHA/image evidence is historical implementation evidence; final completion requires targeted additions and a full rerun after P00 and RV-001–RV-005.

### Unresolved Items

* None.

<!-- rpi:task id=P04-T01 -->
### P04-T01: Add outbox, fencing, crash, and concurrency fault tests

#### Context

#681 requires a crash/replay matrix and stale takeover proof.

#### Intent

Inject faults before/after artifact upload, artifact verification, conditional outbox create, queue notification, scheduling/pagination, claim, intent consumption, mutation, response, receipt, verification, aggregation, and acknowledgment; use fake clocks for heartbeat/expiry/takeover before, during, and after provider I/O.

#### Boundaries

* Included: orphan repair, concurrent claims/workers, stale writers, consumed-intent read-only takeover, scheduler deduplication/restart/exhaustion, CAS conflict, poison, retry budget.
* Excluded: tests that assert only call counts without durable state.

#### Likely Targets

* New `tests/test_distribution_outbox.py`; optional integration outbox test; publication/storage fixtures.

#### Dependencies

* P01–P02.

#### Validation Expectations

* Every crash point has expected durable state and allowed next action; any possibly issued mutation permanently removes second-mutation authority; failed-attempt evidence survives a succeeding recovery attempt; scheduler/cleanup traversal beyond 5,000 is proven.

#### Completion Evidence

* Matrix included in changes with passing test selectors.

#### Unresolved Items

* None.

<!-- rpi:task id=P04-T02 -->
### P04-T02: Add provider and terminal-exit semantic tests

#### Context

Provider timeout/500, ambiguous create, processing/readback, partial/manual/unknown, bad config, and ACA exit are mandatory.

#### Intent

Prove provider, attempt, weekly aggregation, alert, and process behavior through state-lattice scenario tables and adapter faults.

#### Boundaries

* Included: YouTube/Spotify and exit/queue disposition.
* Excluded: live credentials in CI.

#### Likely Targets

* Existing provider and job-runner owner suites listed in the plan.

#### Dependencies

* P02–P03.

#### Validation Expectations

* Initial public configuration produces zero provider calls; ambiguous mutation never retries; precedence is deterministic for identity conflict/unknown/manual/partial/missed/failure/pending; only the two externally proven green weekly states exit 0.

#### Completion Evidence

* Targeted provider/entrypoint command passes and changes record maps scenarios to requirements.

#### Unresolved Items

* None.

<!-- rpi:task id=P04-T03 -->
### P04-T03: Run locked repository-standard validation

#### Context

The repository standard includes pytest, compileall, Ruff, Bicep, Checkov, and container build.

#### Intent

Run targeted and full validation exactly as locked in the plan and resolve failures without weakening gates.

#### Boundaries

* Included: test, lint, format, infra/security, workflow assertions, image build.
* Excluded: production deployment.

#### Likely Targets

* Whole repository validation surfaces.

#### Dependencies

* P04-T01–P04-T02.

#### Validation Expectations

* All commands exit 0; generated output remains uncommitted; git diff contains no accidental secrets/artifacts.

#### Completion Evidence

* Prior baseline/SHA/image/test counts in the changes record are historical only. Final completion requires a fresh targeted and full validation record after the current QA and review-driven corrections.

#### Unresolved Items

* None.

<!-- rpi:phase id=P07 -->
## P07: Review-follow-up closure for terminal truth

### Context

The canonical review is complete and supplies deterministic negative probes showing that current completion claims are unsound for six findings. No new research or critique is required. This phase is distinct from the historical implementation and must not be collapsed into P01–P04 status edits.

### Intent

Correct the six active findings in dependency order, prove fail-closed behavior with deterministic negative probes, reconcile current delivery evidence without rewriting historical conclusions, and obtain the designated fresh independent review.

### Boundaries

* Included: existing Podcaster source/test/infrastructure/runbook owners necessary for RV-002/RV-003/RV-004/RV-008/RV-009; current delivery evidence updates necessary for RV-007.
* Excluded: any new finding, new critique, historical review/critique edits, P00-T01 implementation, git/GitHub/deployment/production actions during planning, and implementation contribution from the designated reviewer before review.

### Likely Targets

* Existing outbox, scheduler, storage/cleanup, telemetry, aggregation/evaluator, infrastructure query generation, runbook, and locked owner suites identified in the plan.
* Implementation-time delivery updates to the changes record and PR #684 evidence summary after validation.

### Dependencies

* Canonical review and its negative probes.
* Historical P01–P04 implementation as the correction baseline.

### Validation Expectations

* Deterministic concurrency uses barriers/fake clocks/CAS outcomes, not sleeps.
* Omitted, mismatched, ambiguous, or label-only proof fails closed.
* No test removal, skip, weakening, or non-blocking gate.
* Full locked validation passes before the designated independent reviewer assesses the final diff/evidence.

### Completion Evidence

* One closure row per RV finding links exact implementation, focused negative probes, full validation evidence, current delivery disposition, and the designated independent finding disposition.

### Unresolved Items

* RV-002, RV-004, RV-008, and RV-009 remain open after Fry's final-SHA review. P00-T01, P05, and P06 remain explicit residual acceptance gates.

<!-- rpi:task id=P07-T01 -->
### P07-T01: Enforce exact proof and durable recovery authorization

#### Context

RV-008 proved `_verification_proof()` can synthesize green from omitted values and that recovery authorization can be a caller assertion rather than durable evidence.

#### Intent

Require persisted, exact, identity-bound proof for every green outcome and evidence-backed authorization for every recovery attempt.

#### Boundaries

* Included: week/publication identity, requested objectives, manifest and publication digests, canonical artifact selection/digest, expected provider identity, terminal authoritative readback source/state/time, duplicate reconciliation, authorization evidence references, and immutable predecessor attempts.
* Excluded: default-to-match behavior, inferred expected provider identity, caller booleans as sole authorization, label-derived recovery, and mutation retry after unknown state.

#### Likely Targets

* Existing outbox proof, aggregation, recovery-authorization, provider receipt/readback, and owner test surfaces.

#### Dependencies

* Existing immutable attempt schema; compatible schema migration where required.

#### Validation Expectations

* Missing or mismatched proof is non-green.
* `provider_unknown` never authorizes another mutation.
* Recovery authorization references durable reconciliation/operator evidence, the complete ordered attempt history, the latest relevant state, and the succeeding attempt.
* The structured authorization binds exact provider kind/item, allowed mutation operation name/type, consumed intent ID, exact receipt ID, predecessor attempt, consumption owner/fence/time, receipt timestamp/order, publication/week/manifest/digests/artifact, terminal readback identity/state/time, and succeeding attempt/provider expectation.
* Authorization creation and every mutation-capable use recompute the exact binding from current immutable durable records; extra keys, missing keys, duplicates, reordering, stale values, provider-leg swaps, and legacy v1 evidence fail closed.
* Stale authorization, non-latest predecessor selection, and omitted or reordered attempt history fail closed.
* A later unknown or possibly mutated attempt permits only read-only reconciliation until exact authoritative terminal readback resolves that attempt; only the resulting specifically authorized safe continuation may mutate.
* Earlier failures and all possibly mutated attempts remain immutable and visible.

#### Completion Evidence

* Parameterized negative proof matrix, durable authorization round-trip, branch-around-unknown denial, exact-readback resolution, stale-authorization denial, omitted/reordered-history denial, and exact successful/recovered-green fixtures.

#### Unresolved Items

* Fry review rejection: schema v3 closes the complete predecessor-history defect, but the containing authorization record remains only partially selected. Exact reproduction changed `recovery_authz[-1].source`, `reason`, and `authorized_at`, added an unbound field, duplicated the matching authorization, or appended an unrelated authorization; each successor claim still returned `read_only=False`. Bind every authorization-record field, require exactly one matching record, reject additional/duplicate/unrelated records, and preserve the current complete history/successor behavior. P07-T01 remains open.

<!-- rpi:task id=P07-T02 -->
### P07-T02: Make scheduler notification enqueue single-winner

#### Context

RV-002 proved two schedulers can read the same due token, enqueue twice, and only later mark it notified.

#### Intent

Move ownership/idempotency before enqueue so one durable winner may notify while stale or concurrent workers cannot duplicate the logical notification.

#### Boundaries

* Included: atomic CAS reservation/claim or broker-supported idempotency key, owner/fence/lease, bounded reservation expiry, retryable pre-enqueue release, post-enqueue durable completion, and abandoned-reservation recovery.
* Excluded: read-then-enqueue-then-mark choreography, process-local locks, timing assumptions, and unbounded stuck claims.

#### Likely Targets

* Existing scheduler, due-reconciliation state, outbox persistence, queue adapter, and scheduler/outbox tests.

#### Dependencies

* P07-T01 schema compatibility and immutable notification identity.

#### Validation Expectations

* A deterministic two-worker barrier allows exactly one enqueue.
* A stale owner cannot complete or re-enqueue after lease/fence loss.
* Crash before enqueue permits bounded recovery; crash after acknowledged enqueue does not duplicate.

#### Completion Evidence

* Concurrent single-winner probe, stale-fence probe, pre/post-enqueue crash probes, and durable restart recovery evidence.

#### Unresolved Items

* Farnsworth correction: `reserved` and `enqueue_started` are hidden only while their lease is live. Expired work is recoverable by one new fenced owner; stale owners cannot abort or complete the replacement.

<!-- rpi:task id=P07-T03 -->
### P07-T03: Bound and fence resumable cleanup

#### Context

RV-004 proved cleanup loads the complete retained outbox reference corpus and can delete after a new concurrent reference is created.

#### Intent

Make cleanup explicitly bounded per execution, resumable across executions, and safe against concurrent reference creation.

#### Boundaries

* Included: page/item/time/work budgets, durable cursor/generation, bounded reference lookup/index, claim/fence or atomic conditional deletion, retention checks, crash recovery, and reference-wins semantics.
* Excluded: full-corpus in-memory reference sets, unbounded scans, best-effort check-then-delete races, and deletion without revalidated ownership/reference state.

#### Likely Targets

* Existing storage pagination, outbox reference persistence/index, orphan metadata, cleanup worker, and cleanup tests.

#### Dependencies

* P07-T01 exact artifact/reference identity.

#### Validation Expectations

* Each run stays within every configured budget and makes measurable progress.
* Cursor/generation survives restart without skipping or repeatedly monopolizing one page.
* A concurrent enqueue/reference appearing before deletion prevents deletion.

#### Completion Evidence

* Scale/budget probe, restart/resume probe, deterministic concurrent-reference barrier, stale-fence rejection, and retained-reference invariant evidence.

#### Unresolved Items

* Farnsworth correction: cleanup performs a bounded resumable reference-index migration and refuses deletion until the outbox scan is complete. Legacy references are backfilled, and concurrent current-schema references win the deletion fence.

<!-- rpi:task id=P07-T04 -->
### P07-T04: Unify emitted telemetry and deployable alert vocabulary

#### Context

RV-003 proved weekly critical rows emit one event name while deployed rules query another; the active-depth rule also does not implement the documented absence condition.

#### Intent

Define one canonical telemetry vocabulary and generate or validate all emission, query, test, and runbook consumers from it.

#### Boundaries

* Included: event/state constants or schema, dimensions, generated Bicep query parameters/fragments, representative row-to-query evaluation, missing-state joins/absence windows, runbook examples, fire/clear tests, and drift checks.
* Excluded: duplicated string literals without validation and syntax-only infrastructure assertions.

#### Likely Targets

* Existing telemetry module, scheduler emissions, distribution alert Bicep, deploy workflow assertions, telemetry tests, and terminal-truth runbook.

#### Dependencies

* Existing sanitized low-cardinality telemetry contract.

#### Validation Expectations

* Representative emitted identity-conflict and weekly non-green rows match deployed queries.
* Active depth alerts only when state/heartbeat evidence is absent under the documented window.
* A deliberate vocabulary mismatch fails generation or tests.

#### Completion Evidence

* Canonical vocabulary mapping, generated-query assertions, representative fire/clear evaluations, missing-data negative probes, and Bicep/Checkov success.

#### Unresolved Items

* None.

<!-- rpi:task id=P07-T05 -->
### P07-T05: Require authoritative proof for four-cycle acceptance

#### Context

RV-009 proved four label-only dictionaries are accepted as four green cycles.

#### Intent

Evaluate each cycle from persisted authoritative proof receipts/readbacks and the complete attempt/aggregation envelope rather than trusting stored labels.

#### Boundaries

* Included: exact week/publication identity, deployed provenance, manifest/publication/artifact digests, complete immutable attempts, recovery authorization, provider objectives/items/states, authoritative readbacks, duplicate resolution, aggregation rule/version, and consecutiveness.
* Excluded: status-label-only rows, internal ACA/API/CI green, missing provider proof, ambiguous recovery, and manual action without later readback.

#### Likely Targets

* Existing four-cycle evaluator, dispatch receipt/proof loaders, aggregation verifier, and owner tests.

#### Dependencies

* P07-T01.

#### Validation Expectations

* Any missing, mismatched, ambiguous, partial, unknown, manual, identity-conflict, duplicate-unresolved, no-readback, nonconsecutive, or wrong-count envelope is non-green.
* Exactly four consecutive fully proven cycles pass.

#### Completion Evidence

* Parameterized negative matrix covering every rejection class, authoritative receipt/readback loading tests, and one exact four-cycle positive fixture.

#### Unresolved Items

* Farnsworth correction: persisted proof includes raw evidence and is recomputed against the current weekly record. Identity/readback/duplicate mismatches and recovered cycles without exact authorization fail closed. P06 still requires four real elapsed production cycles.

<!-- rpi:task id=P07-T06 -->
### P07-T06: Run the locked review-follow-up validation contract

#### Context

Current historical validation counts cannot close findings discovered afterward.

#### Intent

Run focused negative probes and the complete repository quality/security/container contract after P07-T01–P07-T05.

#### Boundaries

* Included: locked owner suites, full pytest, compileall, Ruff check/format, Bicep build, Checkov, diff check, container build, and applicable entrypoint/container exit smoke.
* Excluded: removed/skipped tests, weakened assertions, expected-failure conversions, non-blocking gates, and stale counts.

#### Likely Targets

* Commands and owner paths in the plan's `Locked Test and Validation Contract`.

#### Dependencies

* P07-T01–P07-T05 complete.

#### Validation Expectations

* All required commands pass; negative exit smoke remains non-zero for non-green/missing configuration; generated output and local artifacts are not committed.

#### Completion Evidence

* Exact command, exit status, test count, relevant summary, commit SHA, image digest, and artifact-cleanup record in the implementation delivery update.

#### Unresolved Items

* Frank independently reproduced the same validation posture at final head `f3c5e643d9068a83e87bd2ef6c8ac120d312519f`: focused `133 passed`; locked `808 passed, 1 warning`; initial full `1 failed, 3137 passed, 2 skipped, 2 deselected, 1 warning` from the known stale Compose recorder image; rebuild followed by final full `3137 passed, 3 skipped, 2 deselected, 1 warning`. Ruff, format, compile, exact diff safety, Bicep, exact Checkov `36/7`, CI Checkov `34/0`, baseline-aware Dockerfile Checkov, container image `sha256:da9825c04e9248453e5925c02367e52d1db62726f50e035c2cd8176f4a37f2a3`, UID/tool/import smoke, worker exit `2`, and exact-diff secret/PII scan passed. Validation does not clear the independently reproduced RV-008 High; P07-T07 remains open.

<!-- rpi:task id=P07-T07 -->
### P07-T07: Reconcile delivery evidence and obtain Basher's independent final-SHA review

#### Context

RV-007 proved current tracking overstates finding closure and contains stale locked-test counts. Historical review conclusions must remain immutable.

#### Intent

Publish a truthful current delivery update for PR #684 and have Basher independently assess Leela's final validated implementation/evidence.

#### Boundaries

* Included: exact current finding count/status, implementation and negative-probe links, validation commands/counts, residual P00/P05/P06 and operator-only #682 gates, draft/blocked PR state, and Basher's final review.
* Excluded: editing the historical review conclusion, erasing historical implementation claims, claiming deployment/canary/cycle acceptance, or accepting self-review.

#### Likely Targets

* Implementation-owned changes delivery update, current review-status summary, PR #684 handoff/body, and Basher's independent review artifact/comment.

#### Dependencies

* P07-T06.

#### Validation Expectations

* Plan/details/current delivery evidence/PR report identical counts and dispositions.
* Every RV maps to implementation, focused negative probes, and full validation.
* The designated fresh independent reviewer assesses the final validated diff; later executable content change requires revalidation and re-review.

#### Completion Evidence

* Six-row closure matrix, exact validation record, independent verdict/finding dispositions, and explicit statement that P00-T01/P05/P06 still block final acceptance.

#### Unresolved Items

* Leela's exact typed-canonical correction and validation completed. Boolean/float authorization-set cardinality and the systematic scalar/numeric/unsupported/duplicate-key matrix fail closed. Basher independently accepted exact reviewed head `905a890`, closing P07-T07 and RV-008. PR #684 remains draft/blocked because P00-T01, P05, P06, and operator-only #682 remain open. RV-001/RV-002/RV-003/RV-004/RV-005/RV-007/RV-008/RV-009 remain resolved; RV-006 remains planning-resolved.

<!-- rpi:phase id=P05 -->
## P05: Deliver reviewed, reversible implementation and exact W39 production acceptance

### Context

Implementation acceptance requires independent review, durable GitHub linkage/thread closure, exact merge-SHA deployment provenance, rollback readiness, reconcile-first exact-W39 production execution, and authoritative external provider evidence.

### Intent

Push/open coordinated upstream and Podcaster replacement PRs as required, pass required checks, preserve or refresh independent review for the final pushed SHA, supersede unsafe prior work, merge without content drift, prove exact merge-SHA artifact provenance, deploy through a controlled reversible path, and only then execute the actual W39 production recovery.

### Boundaries

* Included: branch/PR, required checks, independent final-SHA review, approval/merge, upstream and Podcaster artifact provenance, #682 closure, deployment/rollback readiness, W39 reconciliation, exact production execution, and external readback.
* Excluded: accepting critical risk to meet schedule.

### Likely Targets

* Git branch/PR, `.github/workflows/release.yml`, `.github/workflows/deploy-azure.yml`, deployment docs, changes record.

### Dependencies

* P07 complete; required upstream and Podcaster fixes reviewed/merged; P00-T01 delivery evidence resolved. Historical P04 validation is not a substitute for P07-T06.

### Validation Expectations

* Review is independent and covers the final pushed SHA; merge content is equivalent; deployed artifacts derive from exact upstream and Podcaster merge SHAs; no real W39 run begins until all review/deployment gates clear; W39 acceptance uses authoritative external readback.

### Completion Evidence

* PR/check/review/approval/merge records, reviewed and merged SHAs, release workflow/digest provenance, thread resolutions, deployment execution, rollback drill, W39 reconciliation, dispatch/execution IDs, provider identity/state, and terminal external readback.

### Unresolved Items

* The coordinated upstream W39 dispatch PR must exist before P05-T01 completes; metadata-only discovery remains acceptable for locating pre-existing related PRs, not as a substitute for required P00 implementation.

<!-- rpi:task id=P05-T01 -->
### P05-T01: Push branch and open the implementation PR

#### Context

The durable handoff must make validation, operations, cross-repository relationships, and all six active review-follow-up dispositions discoverable before review.

#### Intent

Commit and push the validated Podcaster implementation, open or update PR #684 against current main, and link the coordinated upstream W39 dispatch PR created from its dedicated owning-repository worktree. Replace the superseded RV-007 handoff narrative with the immutable-attempt/weekly-aggregation model and exact RV-002/RV-003/RV-004/RV-007/RV-008/RV-009 dispositions.

#### Boundaries

* Included: commits, push, coordinated PR bodies/checklists/links, and reciprocal cross-repository traceability.
* Excluded: force-pushing unrelated shared branches or modifying `/home/azureuser/source/SquadScope`.

#### Likely Targets

* Current worktree branch; GitHub PR and issue metadata.

#### Dependencies

* P04.

#### Validation Expectations

* PR handoff links Coordinator #17, #681, #680, #682, and the coordinated upstream W39 dispatch PR. It states W38's evidence-conditional `published_verified_recovered` rule, W39's `missed_not_dispatched` state, immutable attempt preservation, current RV-002/RV-003/RV-004/RV-007/RV-008/RV-009 dispositions, exact validation evidence, four-cycle gate, canary, rollback, and changes-record pointers.

#### Completion Evidence

* Pushed SHA, PR URL, cross-link discovery evidence, and PR body in changes.

#### Unresolved Items

* None.

<!-- rpi:task id=P05-T02 -->
### P05-T02: Pass required checks and independent final-SHA review

#### Context

The caller prohibits accepted critical findings, and production provenance must start from the exact reviewed PR content.

#### Intent

Run all required PR checks and have Fry independently review the final pushed SHA, covering correctness, dispatch correlation, concurrency/fencing, provider ambiguity, evidence safety, operations, tests, and docs.

#### Boundaries

* Included: required checks, final-SHA review, finding correction and revalidation/re-review after content changes.
* Excluded: self-review as the only gate or acceptance of a critical finding.

#### Likely Targets

* PR checks, implementation diff, changes record, review comments/artifact.

#### Dependencies

* P05-T01.

#### Validation Expectations

* Required checks pass; every finding is fixed or explicitly dispositioned; critical findings are fixed and re-reviewed; any post-review content change triggers targeted/full revalidation as applicable and renewed independent review of the new SHA.

#### Completion Evidence

* Required-check run URLs, reviewed SHA, review verdict, finding dispositions, and final green state in changes.

#### Unresolved Items

* None.

<!-- rpi:task id=P05-T03 -->
### P05-T03: Resolve or supersede every authoritative PR #682 safety thread

#### Context

W17–W29 remain relevant, and RV-006 adds six current unresolved threads that were absent from the historical matrix.

#### Intent

Reply with replacement evidence, resolve each thread only where evidence permits, and close #682 as superseded once the replacement PR exists.

#### Boundaries

* Included: all W17–W29 rows, all six RV-006 rows, and later unresolved rows found in current metadata in the plan matrix.
* Excluded: resolving without linked code/test/non-port evidence.

#### Likely Targets

* PR #682 discussions and replacement PR description.

#### Dependencies

* P03-T02, P04, P05-T01.

#### Validation Expectations

* Thread IDs, reply URLs, actual resolution state, and replacement task/test or non-port rationale are recorded; outdated W24 still receives explicit disposition. No unresolved thread is pre-marked resolved.

#### Completion Evidence

* GitHub GraphQL inventory covered all 67 threads. Before and after replies the state was
  `67 total / 67 resolved / 0 unresolved`.
* The 17 reviewer-final threads received durable replacement replies
  `r4072863167`, `r4072863344`, `r4072863563`, `r4072863767`, `r4072863985`,
  `r4072864217`, `r4072864494`, `r4072864723`, `r4072864920`, `r4072865106`,
  `r4072865276`, `r4072865450`, `r4072865681`, `r4072865926`, `r4072866127`,
  `r4072866389`, and `r4072866652`.
* The plan's authoritative disposition ledger records every W17–W29, RV-006, later-current,
  and previously undispositioned row against #684 and accepted executable boundary
  `9204e139be485cb916ccd6e70b6fce355b656136`.
* No thread was resolved, re-resolved, or reopened by this work.

#### Unresolved Items

* P05-T03 has no remaining evidence blocker. PR #682 remains open because operator closure as
  superseded is deferred until #684 is finally accepted and merge-ready. P05-T04–P05-T06 and
  P06 remain future and are not implied complete by thread disposition.

<!-- rpi:task id=P05-T04 -->
### P05-T04: Approve, merge, and prove release image provenance

#### Context

The exact reviewed PR content must enter main and produce the production image through the repository’s required release path.

#### Intent

Obtain approval, merge without content drift, run post-merge/release checks, and prove the selected image digest derives from the merge SHA.

#### Boundaries

* Included: approval, required merge gates, reviewed-SHA/merge-SHA comparison, release workflow, digest/revision provenance.
* Excluded: production deployment from an unmerged branch or locally built untraceable image.

#### Likely Targets

* GitHub PR/merge, `.github/workflows/release.yml`, image registry metadata, changes record.

#### Dependencies

* P05-T02–P05-T03.

#### Validation Expectations

* Approval and required checks gate merge; no unreviewed content drift; reviewed SHA, merge SHA, workflow run, image digest, and revision provenance are recorded and consistent. Any content drift restarts P05-T02.

#### Completion Evidence

* Merge URL/SHA, release run URL, image digest, provenance/equality checks in changes.

#### Unresolved Items

* None.

<!-- rpi:task id=P05-T05 -->
### P05-T05: Deploy exact merge-SHA artifacts and clear rollout gates

#### Context

Current release validates API health only and proves neither exact W39 upstream dispatch arrival nor provider terminal truth. Real W39 execution must not begin until both repositories' required fixes are reviewed/merged and their exact merge-SHA artifacts are proven deployed.

#### Intent

Deploy exact upstream and Podcaster merge-SHA-derived artifacts with routing disabled. Complete migration/readiness checks, deployment verification, alert fire/clear evidence, provider authority/credential readiness, and a rollback drill. Non-mutating or provider-disabled probes may validate readiness, but they cannot satisfy production acceptance.

#### Boundaries

* Included: exact upstream and Podcaster merge-SHA provenance, deployment/revision identity, config flag, queue routing disabled state, migration/readiness checks, alert fire/clear, provider authority, and rollback drill.
* Excluded: any real W39 mutation, treating tests/CI/GitHub/Azure/queue/ACA success as production acceptance, broad enablement, or rollback by deleting durable evidence.

#### Likely Targets

* Release/deploy workflows, Bicep settings, runbook/docs, provider canary integration.

#### Dependencies

* P05-T04, completed upstream review/merge evidence, and approved deployment authority.

#### Validation Expectations

* Exact upstream and Podcaster merge SHAs map to the deployed workflow/revision/image digests; all required review and deployment gates are green; readiness does not mutate W39; every alert contract has deployed route/query and controlled fire/clear evidence; rollback preserves records.

#### Completion Evidence

* Merge/release/deploy run URLs, merge SHAs, image/revision digests, equality/provenance checks, alert rule/fire/clear records, flag state, authority confirmation, and rollback drill in changes/PR.

#### Unresolved Items

* None.

<!-- rpi:task id=P05-T06 -->
### P05-T06: Execute and verify the exact real-W39 production recovery

#### Context

The historical W39 incident remains `missed_not_dispatched`. A later recovery execution is distinct evidence and must not rewrite that incident record. A generic W39-class canary, green internal workflow, or weekly label is not acceptance.

#### Intent

After P05-T01–P05-T05 and all upstream delivery gates are complete, reconcile every existing W39 intent, dispatch, receipt, Podcaster job, Azure execution, immutable attempt, and provider candidate before mutation. Prove no existing or ambiguous provider publication. If proof is incomplete or conflicting, fail closed to `manual_action_required`; never create a duplicate. When safe, execute the actual W39 GitHub dispatch and correlate it through upstream publication identity/dispatch result, Podcaster accepted job/correlation IDs, Azure synth/recorder/video job or execution IDs, immutable attempts, provider item identity, and authoritative terminal external readback.

#### Boundaries

* Included: exact W39 identity, pre-mutation reconciliation, dispatch, Azure execution, provider publication/readback, safe evidence reporting, and manual-action handoff.
* Excluded: duplicate publication, blind retry, acceptance from tests/CI/GitHub success/Azure internal success/queue completion/ACA exit 0/weekly labels, unsafe provider URLs, credentials, tokens, signed URLs, or response bodies.

#### Likely Targets

* Existing production dispatch workflow, upstream and Podcaster status/receipt stores, Azure execution records, provider readback tooling, changes record, and PR evidence.

#### Dependencies

* P05-T01–P05-T05 complete.
* All required upstream and Podcaster fixes reviewed and merged.
* Exact merge-SHA-derived artifacts proven deployed.
* Required checks, approvals, deployment/readiness/rollback gates, provider authority, and safe credentials clear.

#### Validation Expectations

* Reconciliation enumerates and binds every known W39 intent/attempt/receipt/job/provider record to exact identity and records the mutation/no-mutation decision.
* No mutation occurs unless authoritative evidence proves no existing or ambiguous W39 provider publication.
* One sanitized chain records GitHub dispatch/run IDs and URLs, upstream publication identity and dispatch result, Podcaster accepted job/correlation IDs, Azure synth/recorder/video execution or job IDs, immutable attempt IDs, provider item identity/state and safe provider URLs, and authoritative terminal external readback.
* Any ambiguity, identity conflict, unknown mutation, unsafe duplicate risk, missing external readback, partial provider state, or manual action remains non-green and blocks acceptance.

#### Completion Evidence

* Timestamped reconciliation inventory and decision; exact safe run/job/execution/attempt/provider identifiers and URLs; terminal external readback; duplicate-resolution proof; resulting weekly recovery classification; and any manual-action blocker.

#### Unresolved Items

* This task is blocked now. PR #684 and #682 remain open; exact Podcaster merge SHA and deployed artifact do not exist; upstream merged commits `9074afa0cd90c09049836df6e7ad79951ae71519`, `2ce5dff50f4ac7ec67d9fd2514420ae1efb91a02`, and `574e4e463c81ad5d1e2d90290b70352596d9b9dc` have not been proven here as the exact deployed upstream artifact set; deployment/provider authority and full W39 reconciliation have not been established.

<!-- rpi:phase id=P06 -->
## P06: Verify four consecutive post-fix production cycles

### Context

A single canary cannot establish sustained dispatch reliability, Azure correlation, provider terminal truth, or operational stability.

### Intent

Keep the task open through four future consecutive post-fix scheduled cycles and verify each from upstream intent through immutable attempts, deterministic aggregation, Azure execution correlation, and external provider readback.

### Boundaries

* Included: weekly dispatch evidence, Azure arrival/execution correlation, provider readback, alert/lease/reconciliation review, incident handling, final closure.
* Excluded: counting internal CI/API/ACA green status without provider proof.

### Likely Targets

* Production provider APIs/readback tooling, monitoring, changes record, linked issues/PR.

### Dependencies

* P05-T06 exact W39 production acceptance complete and production routing approved.

### Validation Expectations

* The evaluator reloads persisted authoritative receipts/readbacks and independently validates each proof envelope; labels alone are ignored. Each of four future consecutive post-fix cycles ends `published_verified` or controlled `published_verified_recovered`. Any `partial`, `provider_unknown`, `manual_action_required`, `missed_not_dispatched`, `failed_terminal`, `identity_conflict`, unresolved duplicate ambiguity, identity mismatch, or missing readback blocks acceptance and restarts the gate after correction. The P05-T06 W39 recovery run counts only if it independently satisfies these future scheduled-cycle timing and evidence criteria; otherwise it provides no P06 cycle credit.

### Completion Evidence

* Week 1–4 rows and final summary in changes; issue/PR closure links.

### Unresolved Items

* Calendar dates derive from the first accepted production week.

<!-- rpi:task id=P06-T01 -->
### P06-T01: Record four weekly external-readback verification windows

#### Context

Each week must prove provider truth independently from internal execution status.

#### Intent

Record sanitized upstream intent/dispatch result, Azure API acceptance and first durable execution correlation, canonical artifact and manifest/digest, complete attempt set, recovery authorization when used, provider items/states, terminal external readbacks, aggregation decision/proof, ACA exit, pending age, alerts, and reconciliation. Manual action alone cannot accept a cycle.

#### Boundaries

* Included: externally read state and operational evidence.
* Excluded: secrets, account PII, content bodies, and unsupported success inference.

#### Likely Targets

* Changes record and existing monitoring/provider readback mechanisms.

#### Dependencies

* P05-T06.

#### Validation Expectations

* Each row is time-stamped, tied to upstream and Podcaster merge SHAs plus deployed image digest, proves dispatch and first Azure arrival, references immutable attempts, and independently confirms exact provider state. If Spotify requires manual publication, bounded readback must confirm the expected item/state/timestamp. Any non-green state, unavailable correlation, identity mismatch, duplicate ambiguity, or missing readback fails the cycle and restarts the consecutive count after correction.

#### Completion Evidence

* Four consecutive accepted weekly records.

#### Unresolved Items

* None after dates are established.

<!-- rpi:task id=P06-T02 -->
### P06-T02: Close production verification and residual actions

#### Context

Final completion must distinguish sustained proof from deferred enhancements.

#### Intent

Summarize four-week outcomes, reconciliations/incidents, alert quality, rollback readiness, PR/issue/thread states, close #681 with its acceptance-matrix evidence, and record follow-up items.

#### Boundaries

* Included: completion evidence and closure.
* Excluded: folding unrelated Spotify contract evolution or W38 missed-week/recovery framing into acceptance.

#### Likely Targets

* Changes record, implementation PR, #681 closure comment/state, #682 supersession, Coordinator #17 updates.

#### Dependencies

* P06-T01 complete.

#### Validation Expectations

* No unresolved critical implementation finding; no missing cycle dispatch/Azure/readback evidence; all four cycles are green under the exact state model; W38 is classified only according to available proof; all acceptance criteria trace to evidence.

#### Completion Evidence

* Final changes-record acceptance summary, #681 closed with replacement PR/exact-W39/four-week evidence links, and durable GitHub closure links.

#### Unresolved Items

* None.
