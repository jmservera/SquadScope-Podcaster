<!-- markdownlint-disable-file -->
# RPI Plan: Provider State Reconciliation

## Task Metadata

* Task ID: provider-state-reconciliation
* Task slug: provider-state-reconciliation
* Planning status: Implemented and validated
* Plan date: 2026-09-14
* Phase details: .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md
* Changes record: .copilot-tracking/changes/2026-09-14/provider-state-reconciliation-changes.md

## Executive Summary

The implementation will add one additive, canonical provider-delivery outcome vocabulary—`uploaded`, `draft_created`, `manual_handoff_required`, `published`, and `publication_unknown`—without replacing the existing top-level job, `PublishResult.status`, `DistributionResult.status`, or Spotify promotion terminal-state fields. Provider-specific adapters will populate the new outcome while legacy fields keep their current accepted values.

The main safety correction is to distinguish “a mutation request was sent” from “the provider state is confirmed.” An ambiguous create, metadata, process, or publish mutation must be recorded as `publication_unknown` and block automatic repetition until reconciliation proves a safe next action. Confirmed provider rejection or an unavailable operator/capability step becomes `manual_handoff_required`; successful draft completion becomes `draft_created`; completed media transfer before draft finalization is `uploaded`; only independent provider confirmation becomes `published`.

The repository already has strong foundations: deterministic accepted `job_id`, pinned `article_sha256`, atomic storage updates, bounded append-style job logs/progress, Spotify reconcile-before-create, protected draft IDs, separate audio/video live gates, and YouTube review/privacy gates. The plan builds on those seams with a bounded publication-evidence document and deduplicated safe log signals. No live provider or production operation is part of implementation or validation.

### User Decisions and Requirements Highlights

* Full-plan scope is approved, including audit, implementation, tests, documentation, validation, commit, push, and an unmerged PR to `main`.
* Ambiguous provider mutations fail closed and must not be repeated without conclusive reconciliation evidence.
* Every current YouTube and Spotify safeguard is a regression invariant, not an optional cleanup target.
* A full reconciler is preferred. If a provider lacks trustworthy read-back or identity evidence, implementation must stop at safe foundations and open a narrow GitHub follow-up issue with explicit acceptance criteria rather than guessing.

### What You May Not Know

* Current status names conflate different facts. YouTube unlisted uploads and Spotify video drafts are persisted as per-platform `"status": "published"` even when they are not public; `DistributionResult.status="completed"` means configured distribution work completed, not that every provider artifact is publicly available.
* Spotify video promotion already has most safety controls, but its caller passes neither `job_id` nor `run_id`, terminal telemetry is process logging rather than durable evidence, and the legacy terminal name is `publication_state_unknown`.
* Spotify audio live publish and YouTube `videos.update` currently treat a successful mutation response as success without an independent post-mutation state confirmation. Spotify audio publish also uses the generic retry default for its publish POST.
* The explicit task request to open the final PR against `main` overrides the repository-local generic dev-first workflow for this task.

### Unresolved Decisions or Blockers

* None for implementation start. Provider read-back limitations are handled by the required `publication_unknown`/manual-handoff fallback and, if necessary, narrowly scoped follow-up issues.

## User Decisions and Requirements

* Audit distribution, job execution, publish orchestration, jobs/status, monitoring semantics, and tests before source edits.
* Implement distinct `uploaded`, `draft_created`, `manual_handoff_required`, `published`, and `publication_unknown` outcomes with backward compatibility.
* Fail closed on ambiguous provider mutations and avoid unsafe repeats.
* Preserve every existing YouTube and Spotify safeguard.
* Add bounded append-only reconciliation/status evidence keyed by week, `publish_run_id`, `article_sha256`, and accepted `job_id` where the architecture supports it.
* Add deduplicated safe monitoring signals.
* Add comprehensive targeted tests, including the named outcome, ambiguity, no-repeat, compatibility, provider-safeguard, evidence-bound, and monitoring-dedupe cases inferred from the request and repository.
* If a complete reconciler cannot safely fit, implement explicit safe foundations and create narrowly scoped GitHub follow-up issues with acceptance criteria.
* Do not call live provider APIs or production.
* Final delivery must run validation, create a conventional commit with the exact required trailers, push the current branch, and open an unmerged PR to `main`.
* The PR must document compatibility, rollback, tests, retained safeguards, and intentional deferrals.
* This planning task may modify only the three named `.copilot-tracking/` artifacts and must not modify production source or tests.

## Goals

* Give callers and operators an accurate, stable answer to “what happened at the provider?” without changing existing accepted response shapes.
* Prevent duplicate or unsafe provider mutations after transport loss, ambiguous responses, worker crashes, or queue redelivery.
* Preserve provider-specific privacy, identity, routing, credential, validation, and manual-review protections.
* Persist enough bounded evidence to reconcile an accepted job safely across stateless workers.
* Surface actionable, deduplicated monitoring signals without secrets, signed URLs, response bodies, or unbounded cardinality.

## Scope and Non-Goals

### In Scope

* A shared provider-delivery outcome model and legacy compatibility adapters.
* Spotify audio publish, Spotify video draft upload/promotion, YouTube draft upload/manual promotion, video distribution, synthesis/video job runners, manifest status, monitoring, and directly related docs/tests.
* A bounded atomic evidence store under the accepted job namespace, plus deduplicated structured monitoring records.
* Queue/redelivery behavior needed to stop unsafe repeats after unknown publication state.
* Narrow GitHub follow-up issues when trustworthy provider reconciliation cannot be implemented from current local seams.
* Final local validation, conventional commit, push, and PR to `main`.

### Non-Goals

* Live YouTube, Spotify, Azure, storage-provider, or production calls.
* Retrospective publication, cleanup, or migration of historical provider artifacts.
* Publishing or altering protected W35 Spotify drafts.
* Broad UI redesign, unrelated observability changes, or replacement of provider integrations.
* Changing caller-owned configuration without compatibility coordination.
* Weakening/skipping tests, safety gates, credential controls, or CI.

## Current Semantics and Gaps

* `podcaster/jobs.py`
  * Accepts/stages deterministic jobs as `accepted` or `dry_run`; accepted identity includes `request.week`, computed/pinned `request.article_sha256`, and deterministic `job_id`.
  * Top-level response keys are contract-stable; richer state belongs in the manifest.
* `podcaster/job_runner.py`
  * Marks synthesis complete independently of publication.
  * Auto-publish uses `podcaster/orchestration.py`, which persists `publishing.result`.
  * Direct Spotify draft publish calls `publish_episode()` but currently only logs and discards `PublishResult`; publish failure intentionally does not block synthesis or video enqueue.
  * Duplicate synthesis delivery skips re-synthesis and can enqueue missing video work.
* `podcaster/orchestration.py`
  * Persists legacy publish statuses (`draft`, `scheduled`, `published`, `failed`) and sets the top-level manifest status to the same value or `publish_failed`.
  * `publishing.eligible` remains true after a legacy failed publish when review/audio gates pass, enabling retry.
* `podcaster/publish.py`
  * `PublishResult.status` is `published|scheduled|draft|failed`.
  * Spotify audio draft creation is not reconciled; `_create_episode()` sends one create POST and reports ambiguous create as failure, but the returned failure loses a canonical non-repeatable state.
  * Spotify video draft upload reconciles exact-title drafts, excludes the audio anchor, validates listing schema/state, claims new titles before upload, and bounds ambiguous-create recovery to at most two evidence-justified create POSTs.
  * Spotify video promotion already distinguishes gate denial, protected draft, published/already-published, manual handoff, and `publication_state_unknown`; it performs pre/post state reads and sends the publish POST once.
  * Audio publish may retry `_publish_episode_live()` and reports `published`/`scheduled` from request completion without provider read-back.
* `podcaster/video/distribution.py`
  * `DistributionResult.status` is aggregate `pending|completed|partial|failed`.
  * Durable per-platform records use legacy `"status": "published"` as an at-most-once marker for YouTube upload, Spotify RSS update, and Spotify upload—even when YouTube is unlisted or Spotify remains a draft.
  * A crash after YouTube create succeeds but before `on_published` persists can re-upload; the code documents this residual gap.
  * Spotify upload may return a promotion terminal state, but the call currently passes no accepted `job_id` or publish run ID to promotion.
* `podcaster/video/youtube_publish.py`
  * New uploads default to unlisted; packet construction rejects public draft privacy; approval is required before promotion.
  * Promotion reports success on HTTP 200 without a post-update state read.
* `podcaster/video/job_runner.py`
  * Persists a single mutable per-platform `generation.video_publish` snapshot and skips platforms whose legacy status is `"published"`.
  * Required YouTube failures are classified retryable/permanent; aggregate video completion does not expose canonical provider outcome detail.
* `podcaster/monitoring.py`
  * Job list/detail APIs pass through top-level manifest and `publishing`; episode list exposes legacy `publish_status`.
  * Logs combine manifest-derived entries with bounded structured logs but have no publication-signal dedupe contract.
* `podcaster/job_logs.py` and `podcaster/progress.py`
  * Provide the repository precedent for atomic append-style, bounded per-job documents (`MAX_RECORDS=1000`, `MAX_EVENTS=500`) across stateless workers.

## Outcome Semantics and Compatibility

| Canonical outcome | Required meaning | Retry rule | Legacy compatibility |
|---|---|---|---|
| `uploaded` | Provider accepted/processed media bytes, but draft metadata or final provider state is not yet confirmed. | Continue only with the next proven-safe, identity-bound step; never create/upload again. | Preserve legacy `status` and aggregate status; add `outcome`. |
| `draft_created` | The exact provider artifact exists as a non-public draft with known provider identity and completed required draft setup. | Reuse the known artifact; do not create/upload again. | Spotify `PublishResult.status="draft"` remains; YouTube/Spotify per-platform legacy `"status": "published"` remains temporarily as the existing at-most-once marker. |
| `manual_handoff_required` | Automation is safely blocked or provider state is known but requires operator action: denied capability, credential expiry, deterministic rejection, unconfirmed draft after one publish request, or protected/manual workflow. | No automatic mutation retry until operator action or explicit reconciler evidence changes the state. | Preserve current `failed`, `partial`, `draft_gate_denied`, `blocked_protected_historical_draft`, and `manual_handoff_required` fields as applicable. |
| `published` | The exact provider artifact is independently confirmed public/live (or scheduled only if the provider contract explicitly represents scheduled publication separately in legacy fields). | No mutation; verification-only reads are allowed. | Preserve `status="published"`/`already_published`; add canonical `outcome="published"`. |
| `publication_unknown` | A state-mutating request may have succeeded, or read-back is absent/contradictory/unreadable, so publication/draft state cannot be proven. | Hard stop for automatic mutation and queue redelivery; reconciliation/verification only. | Preserve legacy `status="failed"` or `terminal_state="publication_state_unknown"` as needed; never translate to success. |

Compatibility rules:

* Add fields; do not remove or rename existing response/manifest/result fields.
* Keep `JobResult.response.status`, top-level manifest lifecycle values, `PublishResult.status`, `DistributionResult.status`, and `VideoPromoteResult.terminal_state` accepted values.
* Add one canonical `outcome` field to result/evidence surfaces and document mappings.
* Existing manifests without canonical evidence remain readable and preserve current API defaults.
* Dry-run returns simulated legacy results but must not persist provider evidence, poison at-most-once guards, or emit success monitoring signals.
* `scheduled` remains a legacy Spotify audio status; canonical outcome is not `published` until provider confirmation. If provider verification cannot distinguish scheduled from draft safely, record `draft_created` plus schedule metadata.
* Top-level generation/synthesis success remains independent from provider publication failure so article publishing is never blocked; provider outcome is explicit in nested publishing/distribution state and monitoring.

## Required Safety Invariants

### Shared

* Persist mutation intent and identity before an external state-changing request where the accepted job/storage context exists.
* Never retry a state-changing provider request merely because it timed out, returned 408/429/5xx, returned unreadable success data, or lost the response.
* After ambiguity, perform only bounded, identity-bound reads. Mutate again only when evidence conclusively proves the prior mutation did not occur and an endpoint-specific safe policy permits it.
* A prior `publication_unknown`, `manual_handoff_required`, `draft_created`, or `published` record for the same accepted job/platform/artifact blocks duplicate create/upload/publish operations as defined by the outcome table.
* Logs/evidence contain only safe identifiers, normalized outcomes, booleans, timestamps, sanitized status/code, and bounded strings—never cookies, bearer tokens, signed URLs, raw provider bodies, or article content.
* Provider failure must not block the core SquadScope article publishing/generation contract.

### YouTube safeguards to retain

* Fresh uploads remain `unlisted` by default (or explicitly `private`), never public.
* `PublishingPacket` rejects `draft_privacy="public"` and starts unapproved.
* `approve_and_publish()` performs no mutation without approval.
* Pre-promotion checks retain non-empty title/description, not-already-public, and playlist membership verification with API failure distinguished from absence.
* Scheduled publish remains `privacyStatus=private` plus `publishAt`.
* Resumable uploads retain minimum-file validation, >128 MiB chunked delegation, bounded transient handling within the resumable session, token/header secrecy, and no token logging.
* Language allow-listing, per-locale playlist selection, required-YouTube config checks, sanitized OAuth subtype reporting, and transient/permanent queue classification remain.
* Blob archive remains first/best-effort independent storage; playlist repair remains independently idempotent.
* A provider upload response alone means `draft_created`, not public `published`; YouTube promotion requires post-update read-back before canonical `published`.

### Spotify safeguards to retain

* Publishing remains disabled unless `SPOTIFY_PUBLISH_ENABLED=true`; unsupported live audio requests remain downgraded to draft unless separately authorized.
* Video live intent and `SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH` remain independent two-key gates; audio live permission never authorizes video.
* Missing/malformed video intent defaults to draft/denied; dry-run never promotes.
* Protected IDs `124658107`, `124658398`, and `124662333` remain immutable pre-session/pre-mutation exclusions.
* Video/audio anchor collision is rejected; promotion targets only the exact video ID and never creates, uploads, searches by title, selects newest, or touches the audio episode.
* Audio and video remain separate episodes; the audio anchor is excluded before draft classification.
* Exact-title reconcile, recognized-container/schema validation, strict state typing, contradiction handling, sanitized diagnostics, and optional strict pagination behavior remain.
* Draft create uses one initial POST; ambiguous create recovery remains evidence-based, bounded, excludes pre-existing orphans/audio IDs, and never exceeds the current two-POST ceiling.
* Newly created untitled video drafts are claimed with real metadata before upload; reconciled/already-titled drafts are not rewritten.
* Video upload remains multipart at 30 MiB parts; GCS signed requests omit bearer credentials, ETags are stripped of quotes, and audio `process_upload` keeps `uploadType="default"`.
* Media validation/polling, per-language show routing with no cross-language fallback, credential-expiry notification, safe URL/error sanitization, and no secret logging remain.
* Promotion performs preflight state read, at most one publish POST, and post-mutation confirmation; a 2xx alone is not canonical publication.

## Evidence and Monitoring Contract

* Add at most one new production module, likely `podcaster/publication_state.py`, and one matching new test module, likely `tests/test_publication_state.py`.
* Store a per-job document at `jobs/{accepted_job_id}/publication-evidence.json` using `StorageBackend.update_bytes`.
* Schema: `squadscope-podcaster-publication-evidence-v1`; bounded to the newest **100** immutable records, with monotonic `seq`. Existing records are never edited; duplicate writes are no-ops; oldest records may be evicted only to enforce the declared bound, matching repository log/progress precedent.
* Every record includes: UTC timestamp, `week`, `publish_run_id`, `article_sha256`, accepted `job_id`, platform, media kind, operation/phase, canonical outcome, provider artifact ID when known, mutation-attempted boolean, confirmation source/time, retry-blocked boolean, and sanitized code/details.
* Identity construction must reject a mismatched passed `job_id`, missing/non-accepted manifest identity, malformed week/hash/run ID, or dry-run persistence. Legacy manifests lacking sufficient identity do not gain unsafe persistence; they return compatible results and fail closed before an otherwise repeatable mutation where evidence is required.
* `publish_run_id` is created once per publish/distribution attempt, persisted before the first provider mutation, threaded through provider calls, results, manifest snapshots, and logs, and reused for all transitions in that attempt.
* Evidence dedupe key: `(accepted_job_id, week, article_sha256, publish_run_id, platform, media_kind, operation, outcome, provider_artifact_id-or-empty)`.
* Monitoring signal dedupe key: `(accepted_job_id, platform, media_kind, canonical_outcome, provider_artifact_id-or-empty)`. A transition to a different canonical outcome is signal-worthy; repeated writes/reads of the same state are not.
* Emit through the existing durable job log seam with an additive optional `dedupe_key`, bounded by `MAX_RECORDS`; persist only the dedupe key and safe context. Monitoring API remains backward-compatible and may expose additive `publication_outcome`, `publication_evidence`, or signal fields.
* Evidence write failure must never be treated as publication success. Before a mutation it blocks the mutation; after a provider mutation it forces `publication_unknown` and blocks automatic repeat.

## Functional Requirements

* Shared constants/types validate only the five canonical outcomes and provide explicit legacy mappings.
* Spotify audio/video and YouTube upload/promotion results expose canonical outcomes while retaining existing status fields.
* Every provider mutation is classified as safe-to-retry, deterministic failure, or ambiguous; ambiguous state-changing requests do not use generic retries.
* Job/orchestration paths persist canonical outcome, `publish_run_id`, and evidence references without changing the accepted response body.
* Video distribution skips a provider mutation based on canonical evidence first and legacy `"status": "published"` second.
* Monitoring list/detail/log consumers remain compatible with legacy manifests and expose additive canonical state when present.
* Duplicated provider terminal signals are suppressed atomically across worker retries.
* Unsupported full reconciliation produces `manual_handoff_required` or `publication_unknown` plus a narrow follow-up issue; it never silently falls back to a blind mutation.

## Non-Functional Requirements

* Backward compatibility: no removal/rename of existing public fields or accepted status values.
* Safety: zero blind retries after ambiguous provider mutation.
* Boundedness: maximum 100 publication evidence records per accepted job; existing log/progress bounds remain.
* Concurrency: evidence and signal dedupe use atomic `update_bytes`, not process-local sets.
* Privacy/security: no credentials, raw provider bodies, article text, signed URLs, or authorization headers in evidence/log/API responses.
* Isolation: no provider failure can invalidate generated article/audio/video artifacts or block SquadScope article publication.
* Testability: all provider behavior uses fakes/mocks and local/in-memory storage.

## Acceptance Criteria

* All five outcomes are represented and tested as semantically distinct.
* Legacy callers still receive existing top-level and result status values; manifests without new fields remain readable.
* YouTube unlisted/private upload is not canonically reported as `published`.
* Spotify video upload completion is `draft_created` unless live state is independently confirmed.
* Spotify/YouTube publish mutation transport loss, retryable HTTP, unreadable 2xx body, failed read-back, and contradictory read-back produce `publication_unknown` and no blind second mutation.
* Deterministic provider rejection or unavailable required operator/capability action produces `manual_handoff_required`.
* A confirmed provider read produces `published`; an already-published artifact produces no mutation.
* Direct synthesis Spotify publishing persists its result/evidence instead of only logging it, while synthesis/video enqueue behavior remains non-blocking.
* Evidence rejects identity mismatch, requires week/run/hash/accepted-job keys, is atomic, deduplicated, monotonic, bounded to 100, and does not persist in dry-run.
* Monitoring signals are emitted once per dedupe key, include safe correlation fields, and surface through existing job logs/status without secret leakage.
* All provider safeguards listed above remain covered.
* If any full reconciler is deferred, each GitHub issue names the exact provider gap, affected mutation, safe current behavior, implementation boundary, tests, and acceptance criteria; the PR links it.
* Targeted tests and the full test suite pass; Ruff checks pass.
* A conventional commit is created with exactly these required trailers:

  ```text
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  Copilot-Session: d2b30d61-1562-4590-b298-16e187772282
  ```

* The branch is pushed and an unmerged PR to `main` documents compatibility, rollback, tests, retained safeguards, and intentional deferrals.

## Implementation Context Record

| Context item | Current artifact or record |
|---|---|
| Plan | .copilot-tracking/plans/2026-09-14/provider-state-reconciliation-plan.md |
| Phase details | .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md |
| Latest critique | Planner-owned final evidence review recorded below; separate critique artifact/worker was not permitted by the caller's three-file write boundary and parent no-nested-delegation rule |
| Relevant research | Direct repository audit recorded in Current Semantics, Sources, and phase details; no separate research artifact needed |
| Changes-record role | .copilot-tracking/changes/2026-09-14/provider-state-reconciliation-changes.md is the canonical implementation evidence record |
| Planning execution and readiness | Complete and implementation-ready |
| Continuation context | Bender starts with P01-T01 and maintains marker order through P04 |

## Sources

* User request and Leela charter: authoritative scope, safety, delivery, and write-boundary requirements.
* .copilot/skills/test-discipline/SKILL.md: interface changes and tests must land together.
* .copilot/skills/git-workflow/SKILL.md: general workflow guidance; explicit caller direction selects `main` for this PR.
* podcaster/jobs.py and docs/integration-contract.md: deterministic accepted job identity and stable response contract.
* podcaster/publish.py, tests/test_publish.py, docs/spotify-video-upload.md, and docs/prd-spotify-video-live-publish.md: Spotify draft, upload, promotion, reconciliation, mutation, and safeguard evidence.
* podcaster/video/distribution.py, podcaster/video/job_runner.py, tests/test_video_distribution.py, tests/test_video_job_runner.py, and tests/test_youtube_distribute_integration.py: aggregate distribution, retry, at-most-once, and required-YouTube behavior.
* podcaster/video/youtube_publish.py, tests/test_youtube_publish.py, and docs/youtube-publish-workflow.md: YouTube privacy, review, verification, and publish semantics.
* podcaster/job_runner.py, podcaster/orchestration.py, tests/test_job_runner.py, and tests/integration/test_publish_flow.py: synthesis/publish orchestration and non-blocking behavior.
* podcaster/job_logs.py, podcaster/progress.py, podcaster/monitoring.py, tests/test_job_logs.py, and tests/test_monitoring.py: atomic bounded observability and monitoring API seams.

## Phase Checklist

<!-- rpi:phase id=P01 -->
### [x] P01: Establish canonical outcomes and durable evidence

* Intent: Add the additive outcome/identity/evidence foundation before changing provider orchestration.
* Dependencies: None.

<!-- rpi:task id=P01-T01 -->
#### [x] P01-T01: Add canonical outcome and compatibility model

* Requirement and evidence: Outcome table and existing result/status classes.
* Expected result: One shared model validates the five outcomes, provides explicit legacy mappings, and introduces no breaking status changes.
* Detail section: P01-T01 in .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md

<!-- rpi:task id=P01-T02 -->
#### [x] P01-T02: Add bounded identity-bound evidence and deduplicated signals

* Requirement and evidence: Existing atomic `StorageBackend.update_bytes`, bounded logs/progress, accepted job identity, and evidence contract.
* Expected result: Atomic, deduplicated, bounded publication evidence and safe monitoring signal emission are unit tested.
* Detail section: P01-T02 in .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md

<!-- rpi:phase id=P02 -->
### [x] P02: Integrate fail-closed provider semantics

* Intent: Apply canonical outcomes and mutation safety to Spotify and YouTube while retaining every safeguard.
* Dependencies: P01.

<!-- rpi:task id=P02-T01 -->
#### [x] P02-T01: Integrate Spotify audio and video outcomes

* Requirement and evidence: Current `PublishResult`, video promotion states, draft reconciliation, and provider mutation behavior.
* Expected result: Spotify paths distinguish upload/draft/manual/published/unknown, persist identity-bound evidence, and never blindly repeat ambiguous mutations.
* Detail section: P02-T01 in .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md

<!-- rpi:task id=P02-T02 -->
#### [x] P02-T02: Integrate YouTube upload and promotion outcomes

* Requirement and evidence: Unlisted draft workflow, required-delivery classification, and current HTTP-success-only promotion.
* Expected result: YouTube uploads are canonical drafts, promotions require read-back confirmation, and ambiguous mutations stop safely.
* Detail section: P02-T02 in .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md

<!-- rpi:task id=P02-T03 -->
#### [x] P02-T03: Thread outcomes through jobs and distribution

* Requirement and evidence: Existing manifest, orchestration, direct synthesis publish, video publish snapshots, and queue retry behavior.
* Expected result: Accepted identity/run context and canonical outcomes persist end-to-end without blocking core generation or breaking legacy APIs.
* Detail section: P02-T03 in .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md

<!-- rpi:phase id=P03 -->
### [x] P03: Expose compatible status and monitoring

* Intent: Make canonical provider state observable and deduplicated while preserving existing monitoring clients.
* Dependencies: P01 and P02.

<!-- rpi:task id=P03-T01 -->
#### [x] P03-T01: Add additive monitoring/status projections

* Requirement and evidence: Existing manifest passthrough, episode summary, structured logs, and evidence contract.
* Expected result: Job detail/log/episode views expose safe canonical state when present and continue to parse legacy manifests.
* Detail section: P03-T01 in .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md

<!-- rpi:task id=P03-T02 -->
#### [x] P03-T02: Update provider and integration documentation

* Requirement and evidence: Current contract/workflow docs and new compatibility/safety semantics.
* Expected result: Docs define outcomes, evidence bounds, retained safeguards, rollback, and manual/follow-up fallback.
* Detail section: P03-T02 in .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md

<!-- rpi:phase id=P04 -->
### [ ] P04: Validate and deliver

* Intent: Prove behavior locally and deliver a reviewable, reversible PR without merge.
* Dependencies: P01–P03.

<!-- rpi:task id=P04-T01 -->
#### [ ] P04-T01: Run targeted and full validation

* Requirement and evidence: Locked test matrix and repository quality commands.
* Expected result: All targeted and full tests/Ruff checks pass without live services; failures are fixed without weakening gates.
* Detail section: P04-T01 in .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md

<!-- rpi:task id=P04-T02 -->
#### [ ] P04-T02: Commit, push, and open the PR

* Requirement and evidence: Caller delivery requirements and exact trailers.
* Expected result: Conventional commit, pushed branch, and unmerged PR to `main` with required compatibility/rollback/test/safeguard/deferral sections.
* Detail section: P04-T02 in .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md

## Dependencies

* P01-T02 depends on P01-T01.
* P02-T01 and P02-T02 depend on P01-T01 and P01-T02.
* P02-T03 depends on provider result shapes from P02-T01/P02-T02.
* P03-T01 depends on persisted evidence and integrated outcomes.
* P03-T02 depends on final implemented semantics.
* P04 depends on all code, tests, and docs.

## Critique Disposition

| Critique run and finding | Disposition | Plan response or residual risk |
|---|---|---|
| PC-001: Existing legacy `"published"` markers conflate uploaded drafts and public state. | Resolved by planner | Canonical `outcome` is additive; legacy fields remain for compatibility and are explicitly mapped. |
| PC-002: Evidence persistence after a provider call can itself fail and recreate the crash window. | Resolved by planner | Intent/evidence must be persisted before mutation; post-mutation persistence failure becomes `publication_unknown` and blocks repeats. |
| PC-003: “Append-only” and bounded retention can conflict. | Resolved by planner | Records are immutable and only appended; deterministic oldest-record eviction at 100 follows existing bounded log/progress precedent and is documented. |
| PC-004: Generic retry currently covers some state-changing requests. | Resolved by planner | P02 requires endpoint classification and one-shot mutation plus read-back for ambiguous operations; known safe resumable part PUTs and idempotent playlist repair remain allowed. |
| PC-005: A full provider reconciler may require unsupported APIs. | Accepted with explicit fallback | Stop at `publication_unknown`/manual handoff and create a narrow issue; never infer or call live services. |
| PC-006: Separate critique artifact and worker are normally expected. | Accepted constraint | Caller permits only three artifacts, and the parent prohibits nested delegation absent explicit request; this complete planner-owned final evidence review is recorded here. |

## Follow-Up Items

* None at planning time. During implementation, create an issue only when a specific provider read-back or identity gap prevents safe reconciliation. Each issue must include the exact mutation, current fail-closed behavior, required provider evidence, bounded implementation scope, targeted tests, and acceptance criteria; link it from the changes record and PR.

## Handoff

* Implementation artifact: .copilot-tracking/changes/2026-09-14/provider-state-reconciliation-changes.md
* Ready phase or task: P01-T01.
* Marker order: P01-T01 → P01-T02 → P02-T01 → P02-T02 → P02-T03 → P03-T01 → P03-T02 → P04-T01 → P04-T02.
* Remaining provisional question or blocker: None.
