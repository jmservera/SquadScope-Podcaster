<!-- markdownlint-disable-file -->
# RPI Phase Details: Production Provider Terminal Truth

## Metadata

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Task slug: `production-provider-terminal-truth`
* Related plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
* Evidence sources: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`; caller requirements; `jmservera/SquadScope-Podcaster#671`, `#678`, `#679`, `#681`; PRs `#680`, `#682`.

## Task-Level Context

Current `origin/main` includes #680 canonical publication identity, append-only provider evidence, sanitization, provider readback, and fail-closed ambiguity handling. It does not include #681’s atomic fenced outbox, and `podcaster.video.job_runner.main()` still treats only literal `failed` as a non-zero exit. The plan therefore establishes durable retry safety before tightening process exit, then proves provider truth through authoritative readback and production verification.

## Cross-Phase Invariants

1. **Recoverably atomic visibility:** upload an immutable content-addressed artifact, verify integrity/readability, then conditionally create the single authoritative outbox record referencing its hash. Queue notification is an idempotent hint; claim revalidates the artifact; verified orphan artifacts are repaired or garbage-collected without provider mutation.
2. **Idempotent enqueue:** the logical key is canonical publication identity plus media kind and provider objective; duplicate enqueue returns the existing item or a conflict.
3. **Fenced ownership:** every repository write, mutation authorization, acknowledgment, and finalization compares owner, claim/attempt ID, lease, and monotonically increasing fence; repository fencing is not claimed to revoke a provider call already in flight.
4. **Consumed intent before I/O:** provider operation intent is durable and atomically marked consumed before network mutation. A call starts only when `remaining_lease > configured_provider_timeout + receipt_persistence_margin`; receipt/ambiguity/readback is durable before queue acknowledgment.
5. **Reconcile before mutate:** every provider transition first performs bounded identity-bound readback; ambiguous absence or identity is never treated as permission to create.
6. **At-most-once mutation:** each consumed intent permits one mutation call. Lease expiry or takeover after intent consumption authorizes read-only reconciliation only, even when provider absence is observed; lost/ambiguous response is quarantined as pending/unknown/manual and never receives a second mutation authorization.
7. **Truthful success:** a requested production provider succeeds only with authoritative external verification of its required public state.
8. **Safe actionable terminal states:** `publication_unknown`, `manual_handoff_required`, poison, and deterministic non-public states are durably acknowledged from the mutation queue to prevent replay, but produce non-zero execution and alerts. Due reconciliation is represented by one deduplicated durable token, not ACA redelivery.
9. **Sanitized evidence:** identifiers/hashes/state/timestamps/error codes are allowed; secrets, tokens, cookies, signed URLs, bodies, content, and unnecessary PII are rejected.
10. **Rollback preservation:** disabling new routing/claims never deletes intent, receipts, or unknown/manual records and never restores inline blind mutation.

## Identity and Correlation Schema

The implementation may refine field names but must preserve these meanings and uniqueness constraints:

| Record | Required correlation |
|---|---|
| Publication identity | `accepted_job_id`, `week`, `publish_run_id`, `article_sha256`, `manifest_sha256` from #680 |
| Outbox identity | stable `outbox_id`, schema version, media kind, requested providers/objectives, artifact path/hash/size, enqueue timestamp, enqueue source/version |
| Claim/execution | `claim_owner`, `claim_id`, `execution_id`, mutation attempt, verification attempt, `fencing_token`, `claimed_at`, `lease_expires_at`, heartbeat, provider timeout, receipt margin, remaining deadline |
| Provider intent | provider, operation, stable intent ID, consumed flag/timestamp/fence, expected publication identity, expected provider item ID when known, precondition/readback fingerprint |
| Provider receipt | transport class/status, provider item/artifact ID, native state, mutation ambiguity, sanitized code, response/readback timestamp |
| Verification | processing/upload state, privacy/publication state, verification source, `checked_at`, `confirmed_at`, verification class |
| Reconciliation schedule | `next_reconcile_at`, verification attempt/budget/horizon, active schedule token, last scheduled/executed timestamps, exhaustion reason |
| Aggregate | per-provider objective/result, requested/required flags, public-verification completeness, terminal reason, worker exit class |

Provider item IDs are treated as operational identifiers and never combined with channel/account names, email addresses, cookies, titles, or payload content in metric dimensions.

## Safe State Model

### Outbox states

`pending → claimed → reconciling → intent_persisted → mutating → receipt_persisted → verifying`

From `verifying`, a provider leg transitions to one of:

* `externally_verified_public` — only provider success.
* `pending_provider` — processing/readback not terminal; set `next_reconcile_at` under a separate verification budget and emit one deduplicated schedule token; current execution is non-zero.
* `publication_unknown` — mutation may have occurred but identity/state is unprovable; no automatic mutation retry.
* `manual_handoff_required` — unsupported or deterministic provider/operator action required; no automatic mutation retry.
* `failed_retryable_pre_mutation` — no mutation intent was consumed and a bounded retry is safe.
* `failed_terminal` — deterministic non-provider/transformation/configuration failure.
* `poisoned` — bounded attempts exhausted or invariant violation; operator action required.

The aggregate becomes `completed_public` only when every requested production provider is `externally_verified_public`. Every other aggregate exits non-zero. Queue acknowledgment is independent from process success: durable unknown/manual/poison/non-public outcomes acknowledge the mutation message after evidence persistence. A scheduler conditionally creates one token for due records; the one-item ACA execution claims that token, performs read-only reconciliation unless a fresh unconsumed intent is explicitly safe, persists the next due/terminal state, and acknowledges the token. ACA platform retries do not grant mutation authority. An empty scheduled drain returns a distinct no-work success only for an expected timer poll; an expected work execution with a missing token is non-success and alerts.

## Phase Index

| Phase ID | Name | Status | Detail sections |
|---|---|---|---|
| P01 | Establish the durable outbox contract | Complete | P01, P01-T01–P01-T04 |
| P02 | Implement reconcile-first provider state machines | Complete | P02, P02-T01–P02-T03 |
| P03 | Make execution, cleanup, and provider aggregation truthful | Complete | P03, P03-T01–P03-T03 |
| P04 | Prove safety with focused tests and repository validation | Complete | P04, P04-T01–P04-T03 |
| P05 | Deliver reviewed, reversible implementation | Ready | P05, P05-T01–P05-T05 |
| P06 | Verify four consecutive production weeks | Ready | P06, P06-T01–P06-T02 |

## Implementation Execution Boundary

* Declared scope: Full approved plan, P01-T01 through P06-T02.
* Current task: P05-T01, ready after complete local P01–P04 validation but blocked from execution in this invocation by the caller's explicit delivery restriction.
* Source boundary: production source, tests, infrastructure, workflows, and operator documentation in this worktree only.
* Validation boundary: focused semantic/fault checks per task, then the complete locked validation contract.
* Delivery boundary: no push, PR mutation, issue mutation, merge, deployment, or production-week claim during this invocation; those markers remain open until their external evidence exists.

<!-- rpi:phase id=P01 -->
## P01: Establish the durable outbox contract

### Context

#680 evidence is append-only and CAS-protected, but provider work is inline and lacks claim fencing. Tightening exits before this phase would allow ACA retries to revisit provider mutation.

### Intent

Create a backward-compatible durable boundary where artifact integrity, enqueue, claim ownership, intent, receipt, and terminal provider evidence are authoritative.

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

* Changes record contains schema, transition table, compatibility result, targeted tests, and no provider mutation enabled.

### Unresolved Items

* None.

<!-- rpi:task id=P01-T01 -->
### P01-T01: Define outbox and correlation schemas

#### Context

Research C13 shows an adequate canonical identity foundation; the new schema must extend rather than duplicate it.

#### Intent

Implement versioned allowlisted records described in `Identity and Correlation Schema`, including retention and aggregate state.

#### Boundaries

* Included: field validation, uniqueness, serialization, retention, sanitization, backward read compatibility.
* Excluded: raw provider payload/body persistence and secret-bearing identifiers.

#### Likely Targets

* `podcaster/publication_state.py`; new outbox model/repository module; `tests/test_publication_state.py`.

#### Dependencies

* #680 canonical identity semantics.

#### Validation Expectations

* Round-trip, malformed identity, unsafe key/value, schema upgrade, duplicate key, and retention tests.

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

* Concurrent claims yield one owner; takeover increments fence; stale owners cannot persist/ack; lease expiry before I/O permits a fresh claim, while expiry during/after a consumed intent permits read-only reconciliation only. Tests interrupt after artifact upload, verification, outbox create, and queue notify and prove repair without duplicate provider intent.

#### Completion Evidence

* Fake-clock/concurrency/commit-protocol suite and W21/W22 outbox portions pass.

#### Unresolved Items

* None.

<!-- rpi:task id=P01-T03 -->
### P01-T03: Schedule deduplicated provider reconciliation

#### Context

Pending processing/readback and durable unknown states must continue progressing after mutation queue acknowledgment without using ACA retry as mutation permission.

#### Intent

Persist `next_reconcile_at`, verification attempt/budget/horizon, and one active schedule token. A scheduler scans due authoritative records, conditionally creates one token, and launches/feeds a one-item reconciliation worker under normal claim/fence rules.

#### Boundaries

* Included: due-time scheduling, token deduplication, read-only takeover, restart recovery, terminal exhaustion/manual transition, expected timer no-work semantics, alert fire/clear.
* Excluded: blind mutation redelivery and concurrent reconciliation for one provider intent.

#### Likely Targets

* New outbox/scheduler module, `podcaster/queue.py`, dedicated worker entrypoint, ACA/Bicep trigger configuration, monitoring.

#### Dependencies

* P01-T01–P01-T02.

#### Validation Expectations

* Fake-clock tests prove due/not-due selection, one active token, process restart, lost notification repair, verification budget exhaustion, successful clearing, and no second mutation after consumed intent.

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

* Provider transition tables and semantic tests recorded in changes.

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

Append intent, transport classification, provider identifiers/state, ambiguity, verification source, timestamps, and terminal outcome to the canonical evidence chain.

#### Boundaries

* Included: allowlisted receipt summaries and correlation.
* Excluded: authorization material, payload bodies, signed URLs, cookies, creator account PII, titles/descriptions.

#### Likely Targets

* `podcaster/publication_state.py`, outbox module, provider adapters, `podcaster/monitoring.py`.

#### Dependencies

* P01-T01 and provider tasks.

#### Validation Expectations

* Crash after mutation but before receipt leaves durable consumed intent and read-only reconciliation schedule; crash after receipt reuses receipt; sequence/CAS remains append-only.

#### Completion Evidence

* Mutation-to-receipt fault tests and sanitization tests pass.

#### Unresolved Items

* None.

<!-- rpi:phase id=P03 -->
## P03: Make execution, cleanup, and provider aggregation truthful

### Context

ACA sees only process exit. The execution must distinguish mutation queue acknowledgment from business success and must close #682 boundedness/cleanup risks rather than porting unsafe lifecycle code.

### Intent

Make process exit authoritative, keep retry paths safe, bound affected operations, and alert on all actionable non-public states.

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

* State-lattice/entrypoint tests and W17–W29 mapping evidence.

### Unresolved Items

* None.

<!-- rpi:task id=P03-T01 -->
### P03-T01: Implement truthful aggregation and queue disposition

#### Context

Current `main()` counts only `failed`; drafts/pending/readback-only may be completed upstream.

#### Intent

Aggregate requested provider objectives from durable evidence and return non-zero for any non-public leg, including unexpected empty drains or skipped required work. Timer-driven scheduler polls may return a distinct successful no-work result only when no work was expected; ACA retries never create mutation authority.

#### Boundaries

* Included: partial, pending, draft/private/unlisted, unknown, manual, failed, poison, skipped-required, and empty-drain behavior.
* Excluded: reprocessing unknown/manual by mutation-message redelivery.

#### Likely Targets

* `podcaster/video/job_runner.py`, new outbox worker entrypoint, failure reporting, ACA command/config.

#### Dependencies

* P02 terminal states.

#### Validation Expectations

* Exit 0 only for all-requested externally verified public; unknown/manual messages are acknowledged after durable evidence but execution exits non-zero; safe pre-mutation transient paths retain bounded retry.

#### Completion Evidence

* Entry-point tests cover complete lattice and ACA command executes the truthful entrypoint.

#### Unresolved Items

* None.

<!-- rpi:task id=P03-T02 -->
### P03-T02: Bound execution, cleanup, and one-item worker behavior

#### Context

W17–W24 and W26–W29 cover cleanup scope, partial artifacts, visibility budgets, subprocess/storage deadlines, resume cleanup, finalization, and multi-message drain.

#### Intent

Rework only required lifecycle safety behavior from current main, with remaining-deadline propagation and deterministic cleanup/finalization.

#### Boundaries

* Included: scoped prefix cleanup, partial file cleanup, visibility/budget invariants, bounded subprocess/storage/finalization, replay guard behavior, terminal cleanup, one message.
* Excluded: wholesale #682 stage-budget architecture.

#### Likely Targets

* `podcaster/video/editor.py`, `podcaster/video/edl_render.py`, `podcaster/video/intermediates.py`, `podcaster/video/process.py`, `podcaster/video/video_gen.py`, `podcaster/video/recorder.py`, `podcaster/video/job_runner.py`, `infra/modules/aca-recorder.bicep`, `infra/modules/aca-video.bicep`.

#### Dependencies

* P01 outbox decouples provider distribution from editor visibility.

#### Validation Expectations

* Each W17–W24/W26–W29 row has a focused regression test or explicit current-main non-port proof; operations terminate inside remaining budget.

#### Completion Evidence

* Thread matrix evidence is recorded before replies/resolution in P05-T03.

#### Unresolved Items

* None.

<!-- rpi:task id=P03-T03 -->
### P03-T03: Add provider-state telemetry and alerts

#### Context

Current monitoring can display evidence but lacks provider-age/lag alerts.

#### Intent

Emit low-cardinality provider/outbox metrics and actionable logs; deploy the plan’s exact alert contract with configurable reviewed thresholds, rule IDs, routes, runbooks, and deterministic fire/clear fixtures.

#### Boundaries

* Included: pending age, claim latency/lease loss, unknown, manual, YouTube non-public, Spotify draft, poison, verification lag.
* Excluded: raw provider IDs as metric dimensions and internal success as provider proof.

#### Likely Targets

* `podcaster/monitoring.py`, logging/signals, `infra/`, deployment tests, existing operator docs.

#### Dependencies

* P01 schema and P02 state vocabulary.

#### Validation Expectations

* Each alert contract row is deployed with its threshold/window, severity/route, missing-data behavior, deterministic fire fixture, and authoritative clear fixture.

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

* Changes record contains command/SHA/image digest results.

### Unresolved Items

* None.

<!-- rpi:task id=P04-T01 -->
### P04-T01: Add outbox, fencing, crash, and concurrency fault tests

#### Context

#681 requires a crash/replay matrix and stale takeover proof.

#### Intent

Inject faults before/after artifact upload, artifact verification, conditional outbox create, queue notification, scheduling, claim, intent consumption, mutation, response, receipt, verification, and acknowledgment; use fake clocks for heartbeat/expiry/takeover before, during, and after provider I/O.

#### Boundaries

* Included: orphan repair, concurrent claims/workers, stale writers, consumed-intent read-only takeover, scheduler deduplication/restart/exhaustion, CAS conflict, poison, retry budget.
* Excluded: tests that assert only call counts without durable state.

#### Likely Targets

* New `tests/test_distribution_outbox.py`; optional integration outbox test; publication/storage fixtures.

#### Dependencies

* P01–P02.

#### Validation Expectations

* Every crash point has expected durable state and allowed next action; any possibly issued mutation permanently removes second-mutation authority and has an explicit read-only reconciliation assertion.

#### Completion Evidence

* Matrix included in changes with passing test selectors.

#### Unresolved Items

* None.

<!-- rpi:task id=P04-T02 -->
### P04-T02: Add provider and terminal-exit semantic tests

#### Context

Provider timeout/500, ambiguous create, processing/readback, partial/manual/unknown, bad config, and ACA exit are mandatory.

#### Intent

Prove provider and process behavior through state-lattice scenario tables and adapter faults.

#### Boundaries

* Included: YouTube/Spotify and exit/queue disposition.
* Excluded: live credentials in CI.

#### Likely Targets

* Existing provider and job-runner owner suites listed in the plan.

#### Dependencies

* P02–P03.

#### Validation Expectations

* Initial public configuration produces zero provider calls; ambiguous mutation never retries; only external public verification exits 0.

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

* Baseline `0752d1a`, command summaries, image digests, stale-Compose-image diagnosis, focused scale-out rerun, and final `3059 passed, 2 skipped, 2 deselected` full-suite accounting are recorded in changes.

#### Unresolved Items

* None.

<!-- rpi:phase id=P05 -->
## P05: Deliver reviewed, reversible implementation

### Context

Implementation acceptance requires independent review, durable GitHub linkage/thread closure, and real-provider canary/rollback evidence.

### Intent

Push/open the replacement PR, pass required checks, independently review the final pushed SHA, supersede unsafe prior work, merge without content drift, prove merge-SHA image provenance, and deploy through a controlled reversible path.

### Boundaries

* Included: branch/PR, required checks, independent final-SHA review, approval/merge, image provenance, #682 closure, canary/rollback.
* Excluded: accepting critical risk to meet schedule.

### Likely Targets

* Git branch/PR, `.github/workflows/release.yml`, `.github/workflows/deploy-azure.yml`, deployment docs, changes record.

### Dependencies

* P04 passes.

### Validation Expectations

* Review is independent and covers the final pushed SHA; merge content is equivalent; deployed digest derives from the merge SHA; canary uses external readback.

### Completion Evidence

* PR/check/review/approval/merge records, reviewed and merged SHAs, release workflow/digest provenance, thread resolutions, deployment execution, rollback drill.

### Unresolved Items

* Metadata-only discovery must identify and link the upstream SquadScope PR when available; verified absence is recorded because the caller explicitly conditioned the link on availability.

<!-- rpi:task id=P05-T01 -->
### P05-T01: Push branch and open the implementation PR

#### Context

The durable handoff must make validation, operations, and cross-repository relationships discoverable before review.

#### Intent

Commit the validated implementation, push the incident branch, open a replacement PR against current main, and discover the upstream SquadScope PR using GitHub metadata only.

#### Boundaries

* Included: commits, push, PR body/checklist/links, metadata-only upstream discovery.
* Excluded: force-pushing unrelated shared branches or modifying `/home/azureuser/source/SquadScope`.

#### Likely Targets

* Current worktree branch; GitHub PR and issue metadata.

#### Dependencies

* P04.

#### Validation Expectations

* PR links Coordinator #17, #681, #680, #682, and the authoritative upstream SquadScope PR when available; if metadata proves none exists, the PR/changes record says so. It includes exact commands/results, schema/state machine, canary, rollback, and changes-record pointer.

#### Completion Evidence

* Pushed SHA, PR URL, cross-link discovery evidence, and PR body in changes.

#### Unresolved Items

* None.

<!-- rpi:task id=P05-T02 -->
### P05-T02: Pass required checks and independent final-SHA review

#### Context

The caller prohibits accepted critical findings, and production provenance must start from the exact reviewed PR content.

#### Intent

Run all required PR checks and dispatch independent implementation review against the final pushed SHA, covering correctness, concurrency/fencing, provider ambiguity, evidence safety, operations, tests, and docs.

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
### P05-T03: Resolve or supersede every PR #682 safety thread

#### Context

All W17–W29 findings remain relevant to deciding whether any #682 behavior is portable.

#### Intent

Reply with replacement evidence, resolve each thread where permitted, and close #682 as superseded once the replacement PR exists.

#### Boundaries

* Included: all 13 rows in the plan matrix.
* Excluded: resolving without linked code/test/non-port evidence.

#### Likely Targets

* PR #682 discussions and replacement PR description.

#### Dependencies

* P03-T02, P04, P05-T01.

#### Validation Expectations

* Thread IDs, reply URLs, resolution state, and replacement task/test are recorded; outdated W24 still receives explicit disposition.

#### Completion Evidence

* 13/13 rows closed and #682 state/reason recorded in changes.

#### Unresolved Items

* None.

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
### P05-T05: Deploy feature-flagged canary and verify rollback

#### Context

Current release validates API health only and does not prove provider terminal truth.

#### Intent

Deploy the exact merge-derived image with outbox routing disabled, run migration/readiness checks, enable a bounded real-provider canary, verify external state and alert contracts, then stage routing while proving rollback.

#### Boundaries

* Included: config flag, queue routing, one controlled publication identity, provider readback, alert fire/clear, rollback drill.
* Excluded: broad enablement before canary evidence, accepting manual handoff without later external readback, or rollback by deleting outbox state.

#### Likely Targets

* Release/deploy workflows, Bicep settings, runbook/docs, provider canary integration.

#### Dependencies

* P05-T04 and approved deployment authority/provider credentials.

#### Validation Expectations

* Exact merge-derived image digest; YouTube processing/public readback; Spotify externally read expected item/state after automated or manual publication; ACA non-zero for non-public legs; every alert contract has deployed rule ID and controlled fire/clear evidence; disabling routing stops new claims and preserves records.

#### Completion Evidence

* Sanitized canary correlation, provider readbacks, execution status, alert rule/fire/clear records, flag transitions, and rollback drill in changes/PR.

#### Unresolved Items

* None.

<!-- rpi:phase id=P06 -->
## P06: Verify four consecutive production weeks

### Context

A single canary cannot establish sustained provider terminal truth or operational stability.

### Intent

Keep the task open through four consecutive publication weeks and verify each using external provider readback and durable correlations.

### Boundaries

* Included: weekly readback, alert/lease/reconciliation review, incident handling, final closure.
* Excluded: counting internal CI/API/ACA green status without provider proof.

### Likely Targets

* Production provider APIs/readback tooling, monitoring, changes record, linked issues/PR.

### Dependencies

* P05-T05 canary accepted and staged routing enabled.

### Validation Expectations

* Four consecutive weeks have complete evidence; a failed week is remediated and restarts the consecutive count unless the caller explicitly accepts another rule.

### Completion Evidence

* Week 1–4 rows and final summary in changes; issue/PR closure links.

### Unresolved Items

* Calendar dates derive from the first accepted production week.

<!-- rpi:task id=P06-T01 -->
### P06-T01: Record four weekly external-readback verification windows

#### Context

Each week must prove provider truth independently from internal execution status.

#### Intent

Record sanitized canonical/outbox/execution correlation, provider IDs/states, YouTube processing/privacy, Spotify authoritative state after automated or manual publication, ACA exit, pending age, alerts, and reconciliation. Manual-handoff status alone cannot accept a week.

#### Boundaries

* Included: externally read state and operational evidence.
* Excluded: secrets, account PII, content bodies, and unsupported success inference.

#### Likely Targets

* Changes record and existing monitoring/provider readback mechanisms.

#### Dependencies

* P05-T05.

#### Validation Expectations

* Each row is time-stamped, tied to deployed merge SHA/image digest, and independently confirms provider state. If Spotify requires manual publication, bounded readback must confirm the expected item/state/timestamp; unavailable or unprovable readback fails the week and restarts the consecutive count.

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
* Excluded: folding unrelated Spotify contract evolution or W38 forensics into acceptance.

#### Likely Targets

* Changes record, implementation PR, #681 closure comment/state, #682 supersession, Coordinator #17 updates.

#### Dependencies

* P06-T01 complete.

#### Validation Expectations

* No unresolved critical implementation finding; no missing weekly readback; all acceptance criteria trace to evidence.

#### Completion Evidence

* Final changes-record acceptance summary, #681 closed with replacement PR/canary/four-week evidence links, and durable GitHub closure links.

#### Unresolved Items

* None.
