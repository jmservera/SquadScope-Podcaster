<!-- markdownlint-disable-file -->
# RPI Phase Details: Provider State Reconciliation

## Metadata

* Task ID: provider-state-reconciliation
* Task slug: provider-state-reconciliation
* Related plan: .copilot-tracking/plans/2026-09-14/provider-state-reconciliation-plan.md
* Evidence sources: Relevant source/tests/docs listed in the plan

## Planning Direction

* Approved implementation write boundary: the exact likely targets below plus at most one new production module and one new test module; follow-up issue creation only when the safe reconciler fallback is triggered.
* Exact removals: none planned. Existing public/result/status fields, safeguards, tests, and docs are retained and extended.
* Maximum additions: one production module (`podcaster/publication_state.py`) and one test module (`tests/test_publication_state.py`); all other work edits existing relevant files.
* Canonical target: manifest/evidence state under the accepted job namespace. Generated targets: none.
* Test ownership: semantic outcome/evidence tests in `tests/test_publication_state.py`; provider behavior in existing provider tests; propagation/compatibility in existing runner/jobs/monitoring/integration tests.
* Current blockers: none.
* First dependency-ready implementation item: P01-T01.
* Validation constraint: no live provider or production calls; all tests use fakes/mocks/local or in-memory storage.

## Phase Index

| Phase ID | Name | Status | Detail sections |
|---|---|---|---|
| P01 | Establish canonical outcomes and durable evidence | Complete | P01, P01-T01, P01-T02 |
| P02 | Integrate fail-closed provider semantics | Complete | P02, P02-T01, P02-T02, P02-T03 |
| P03 | Expose compatible status and monitoring | Complete | P03, P03-T01, P03-T02 |
| P04 | Validate and deliver | In progress — validation complete | P04, P04-T01, P04-T02 |

<!-- rpi:phase id=P01 -->
## P01: Establish canonical outcomes and durable evidence

### Context

The codebase has several correct but incompatible status vocabularies. The accepted-job manifest already carries deterministic `job_id`, week, and pinned `article_sha256`; storage provides atomic updates; job logs/progress demonstrate bounded append-style documents. These are sufficient to introduce a shared additive outcome and evidence seam before provider changes.

### Intent

Create the common vocabulary, identity validation, bounded evidence, and signal dedupe primitives used by all later tasks.

### Boundaries

* Included: canonical outcome constants/type, compatibility mapping, evidence schema/path/read/write helpers, publish-run identity, safe signal dedupe.
* Excluded: provider API calls and provider-specific transition logic.

### Likely Targets

* podcaster/publication_state.py (new, maximum one production addition): shared model and evidence helpers.
* podcaster/job_logs.py: additive optional durable dedupe key support.
* tests/test_publication_state.py (new, maximum one test addition): semantic/evidence tests.
* tests/test_job_logs.py: signal dedupe and compatibility tests.

### Dependencies

* None.

### Validation Expectations

* Outcome validation rejects unknown values.
* Identity requires matching accepted manifest `job_id`, normalized week, 64-character lowercase `article_sha256`, and non-empty safe `publish_run_id`.
* Evidence append is atomic, monotonic, immutable, duplicate-safe, bounded to 100, resilient to malformed prior content without converting it to success, and dry-run inert.
* Evidence failure before mutation blocks mutation; failure after mutation is representable as `publication_unknown`.
* Log signal dedupe is atomic and does not alter behavior when `dedupe_key` is omitted.

### Completion Evidence

* Targeted new-module/log tests pass.
* Existing job log behavior and schema remain readable.

### Unresolved Items

* None.

<!-- rpi:task id=P01-T01 -->
### P01-T01: Add canonical outcome and compatibility model

#### Context

Existing legacy states must remain accepted while new code needs one precise vocabulary.

#### Intent

Define `uploaded`, `draft_created`, `manual_handoff_required`, `published`, and `publication_unknown`, plus provider/result metadata and legacy adapters.

#### Boundaries

* Included: additive data model, validation, serialization, mapping helpers.
* Excluded: renaming/removing existing fields or changing provider behavior.

#### Likely Targets

* podcaster/publication_state.py
* tests/test_publication_state.py

#### Dependencies

* None.

#### Validation Expectations

Named tests:

* `test_canonical_outcomes_are_exact_and_distinct`
* `test_unknown_outcome_is_rejected`
* `test_legacy_publish_status_mapping_is_additive`
* `test_legacy_distribution_status_mapping_is_unchanged`
* `test_legacy_spotify_publication_state_unknown_maps_to_publication_unknown`
* `test_legacy_manifest_without_outcome_remains_readable`

#### Completion Evidence

* Shared outcome model is importable without side effects and mappings are explicitly tested.

#### Unresolved Items

* None.

<!-- rpi:task id=P01-T02 -->
### P01-T02: Add bounded identity-bound evidence and deduplicated signals

#### Context

Manifest snapshots can be overwritten and process logs are ephemeral. Existing atomic bounded JSON stores are the safe local pattern.

#### Intent

Persist immutable transition records for an accepted publish attempt and emit one safe monitoring signal per terminal state identity.

#### Boundaries

* Included: `jobs/{job_id}/publication-evidence.json`, max 100 retained records, atomic dedupe, optional log `dedupe_key`.
* Excluded: unbounded history, global cross-job indexes, provider polling daemons, article content.

#### Likely Targets

* podcaster/publication_state.py
* podcaster/job_logs.py
* tests/test_publication_state.py
* tests/test_job_logs.py

#### Dependencies

* P01-T01.

#### Validation Expectations

Named tests:

* `test_identity_uses_week_publish_run_article_hash_and_accepted_job_id`
* `test_identity_rejects_manifest_job_id_mismatch`
* `test_identity_rejects_nonaccepted_or_dry_run_manifest`
* `test_evidence_append_is_monotonic_and_preserves_prior_records`
* `test_evidence_duplicate_key_is_a_noop`
* `test_evidence_is_bounded_to_100_newest_records`
* `test_corrupt_evidence_fails_closed_before_mutation`
* `test_evidence_never_contains_article_content_credentials_or_signed_urls`
* `test_emit_log_without_dedupe_key_is_backward_compatible`
* `test_publication_signal_dedupe_is_atomic`
* `test_changed_outcome_emits_a_new_signal`

#### Completion Evidence

* Evidence and logs pass focused unit tests with concurrent-update-capable fake storage.

#### Unresolved Items

* None.

<!-- rpi:phase id=P02 -->
## P02: Integrate fail-closed provider semantics

### Context

Spotify video promotion already demonstrates the desired one-shot/read-back pattern, while Spotify audio and YouTube promotion still equate accepted mutation requests with publication. Video distribution also persists uploaded drafts as legacy “published.”

### Intent

Apply the canonical model and evidence gate to every relevant provider path without removing established safeguards.

### Boundaries

* Included: provider result fields, one-shot mutation classification, bounded read-back, skip logic, safe handoff.
* Excluded: unsupported provider API invention, live capability probing, historical cleanup.

### Likely Targets

* podcaster/publish.py
* podcaster/video/distribution.py
* podcaster/video/youtube_publish.py
* tests/test_publish.py
* tests/test_video_distribution.py
* tests/test_youtube_publish.py
* tests/test_youtube_distribute_integration.py

### Dependencies

* P01.

### Validation Expectations

* Every mutation path proves zero blind repeat after ambiguity.
* Existing safeguard tests remain unchanged or are strengthened.
* No test uses network credentials or live endpoints.

### Completion Evidence

* Provider-focused targeted suites pass and results contain compatible legacy plus canonical state.

### Unresolved Items

* If Spotify audio or YouTube lacks sufficient read-back to prove a safe automatic next mutation, stop at `publication_unknown` and create a narrow follow-up issue in P04-T02.

<!-- rpi:task id=P02-T01 -->
### P02-T01: Integrate Spotify audio and video outcomes

#### Context

`PublishResult` currently uses `draft|scheduled|published|failed`; video promotion uses a richer terminal state but is not durably keyed to the accepted job/run. Draft-create safety is strong on the video path and weaker on the audio path.

#### Intent

Expose canonical outcomes, preserve legacy statuses, persist transitions, and prevent unsafe Spotify create/process/metadata/publish repeats.

#### Boundaries

* Included: additive result fields; accepted identity arguments; run threading; one-shot ambiguous mutations; bounded read-back; existing reconcile/manual fallback.
* Excluded: broad UI automation, provider endpoint discovery, changing separate audio/video episode architecture.

#### Likely Targets

* podcaster/publish.py
* podcaster/video/distribution.py
* tests/test_publish.py
* tests/test_video_distribution.py

#### Dependencies

* P01-T01 and P01-T02.

#### Validation Expectations

Retain all existing tests covering:

* live publishing disabled/downgraded by default;
* video-specific gate independence;
* every protected W35 ID;
* audio/video anchor collision;
* already-published preflight skip;
* pre/post publish state checks and one publish POST;
* unknown read-back/manual handoff;
* exact-title reconciliation, audio exclusion, schema/state fail-closed behavior, strict paging option;
* one initial create, bounded ambiguous recovery, no candidate guessing, no credential/body leaks;
* immediate title claim, no re-title of adopted drafts;
* separate video draft, multipart parts, validation, audio `uploadType="default"`, quoted ETag stripping, per-language show isolation, and credential notification.

Add/adjust named tests:

* `test_spotify_video_upload_reports_draft_created_not_published`
* `test_spotify_media_processed_before_metadata_failure_reports_uploaded`
* `test_spotify_deterministic_publish_rejection_requires_manual_handoff`
* `test_spotify_ambiguous_publish_reports_publication_unknown_without_retry`
* `test_spotify_confirmed_publish_reports_published`
* `test_spotify_unknown_evidence_blocks_redelivery_mutation`
* `test_spotify_promotion_threads_job_and_publish_run_identity`
* `test_spotify_legacy_status_and_terminal_state_are_preserved`
* `test_spotify_dry_run_writes_no_evidence_or_signal`
* `test_spotify_evidence_failure_before_mutation_prevents_provider_call`
* `test_spotify_evidence_failure_after_mutation_reports_publication_unknown`

#### Completion Evidence

* Existing and added Spotify tests pass; mutation call counts prove no blind repeats.

#### Unresolved Items

* Full audio draft reconciliation may be deferred if exact identity/state cannot be proven without unsupported provider behavior. The safe implementation must still record unknown/handoff and stop repeats.

<!-- rpi:task id=P02-T02 -->
### P02-T02: Integrate YouTube upload and promotion outcomes

#### Context

YouTube uploads are intentionally unlisted/private drafts, but distribution’s legacy at-most-once record calls them published. Manual promotion reports HTTP success without post-update confirmation.

#### Intent

Represent uploads as canonical drafts and require provider read-back for canonical publication while preserving the manual review flow.

#### Boundaries

* Included: additive outcomes, publish-run evidence, one-shot promotion update, read-back confirmation, existing upload skip compatibility.
* Excluded: auto-approval, public-by-default upload, live API validation.

#### Likely Targets

* podcaster/video/distribution.py
* podcaster/video/youtube_publish.py
* tests/test_video_distribution.py
* tests/test_youtube_publish.py
* tests/test_youtube_distribute_integration.py

#### Dependencies

* P01-T01 and P01-T02.

#### Validation Expectations

Retain all existing tests covering:

* default unlisted privacy and rejection of public draft packets;
* approval gate and scheduled private+`publishAt`;
* title/description/already-public/playlist readiness checks;
* token secrecy and real bearer header behavior;
* minimum MP4 size, resumable/chunked upload, language gating, per-locale playlist;
* required delivery transient/permanent/OAuth classification;
* already-persisted upload skip, partial target retry, dry-run no persistence, blob-only target, and no-target failure.

Add/adjust named tests:

* `test_youtube_unlisted_upload_reports_draft_created`
* `test_youtube_legacy_published_marker_still_skips_duplicate_upload`
* `test_youtube_canonical_draft_evidence_skips_duplicate_upload`
* `test_youtube_publish_200_without_confirmed_readback_is_publication_unknown`
* `test_youtube_publish_transport_loss_is_publication_unknown_and_not_retried`
* `test_youtube_publish_readback_confirms_published`
* `test_youtube_scheduled_publish_remains_draft_created_until_confirmed_public`
* `test_youtube_required_failure_contract_is_unchanged`

#### Completion Evidence

* YouTube suites pass with unchanged privacy/review guarantees and explicit canonical outcomes.

#### Unresolved Items

* If scheduled state cannot be confirmed with current read-back, preserve schedule metadata and use `draft_created`, not `published`.

<!-- rpi:task id=P02-T03 -->
### P02-T03: Thread outcomes through jobs and distribution

#### Context

The accepted job contains the required identity, but publish paths do not consistently propagate or persist it. Direct synthesis publishing discards its result; video distribution uses one mutable snapshot and aggregate completion.

#### Intent

Carry `publish_run_id` and canonical provider outcomes through orchestration, manifests, queue retries, and aggregate distribution without blocking core generation.

#### Boundaries

* Included: accepted identity extraction, additive manifest/result fields, direct-publish persistence, skip/retry policy, legacy aggregate status.
* Excluded: changing accepted response keys or making provider failure fail article generation.

#### Likely Targets

* podcaster/jobs.py
* podcaster/job_runner.py
* podcaster/orchestration.py
* podcaster/video/job_runner.py
* podcaster/video/distribution.py
* tests/test_jobs.py
* tests/test_job_runner.py
* tests/test_video_job_runner.py
* tests/test_video_distribution.py
* tests/integration/test_publish_flow.py

#### Dependencies

* P02-T01 and P02-T02.

#### Validation Expectations

Named tests:

* `test_accepted_manifest_exposes_publication_identity_inputs_without_response_shape_change`
* `test_direct_synthesis_publish_persists_canonical_result`
* `test_publish_failure_does_not_block_synthesis_or_video_enqueue`
* `test_duplicate_synthesis_delivery_does_not_repeat_unknown_publish`
* `test_video_runner_persists_publish_run_and_provider_outcomes`
* `test_video_runner_unknown_provider_state_is_terminal_for_mutation_but_not_article_generation`
* `test_distribution_aggregate_status_remains_completed_partial_failed`
* `test_legacy_video_publish_snapshot_is_still_honored`
* `test_new_evidence_takes_precedence_over_legacy_snapshot`
* `test_publish_flow_audio_and_video_remain_independent`

#### Completion Evidence

* Runner/job/integration tests prove identity propagation, legacy compatibility, and non-blocking generation behavior.

#### Unresolved Items

* None.

<!-- rpi:phase id=P03 -->
## P03: Expose compatible status and monitoring

### Context

Monitoring currently returns manifest status/publishing fields and merges manifest/structured logs. Canonical evidence can be projected additively without changing existing consumers.

### Intent

Expose accurate provider state and deduplicated actionable signals while remaining safe for legacy manifests.

### Boundaries

* Included: additive fields/projections, evidence reads, safe signal display, documentation.
* Excluded: new alerting vendor, dashboards, UI redesign, credentials/raw provider payloads.

### Likely Targets

* podcaster/monitoring.py
* podcaster/job_logs.py
* tests/test_monitoring.py
* tests/test_job_logs.py
* docs/integration-contract.md
* docs/distribution-ux.md
* docs/spotify-video-upload.md
* docs/prd-spotify-video-live-publish.md
* docs/youtube-publish-workflow.md

### Dependencies

* P01 and P02.

### Validation Expectations

* Legacy manifest tests remain passing.
* Canonical fields are absent/null safely when evidence is unavailable.
* Repeated terminal records produce one monitoring signal.

### Completion Evidence

* Monitoring and documentation tests/checks pass; contract docs match implementation.

### Unresolved Items

* None.

<!-- rpi:task id=P03-T01 -->
### P03-T01: Add additive monitoring/status projections

#### Context

Top-level job status must not be overloaded with provider publication details. Nested additive state is the stable compatibility path.

#### Intent

Expose current canonical provider outcome, bounded evidence, and deduplicated signals through existing authenticated job endpoints/logs.

#### Boundaries

* Included: additive optional fields and safe derived entries.
* Excluded: changing endpoint URLs, auth, pagination defaults, or existing response fields.

#### Likely Targets

* podcaster/monitoring.py
* podcaster/job_logs.py
* tests/test_monitoring.py
* tests/test_job_logs.py

#### Dependencies

* P02-T03.

#### Validation Expectations

Named tests:

* `test_job_detail_exposes_canonical_publication_outcome`
* `test_job_detail_legacy_manifest_remains_compatible`
* `test_episode_summary_exposes_additive_publication_outcome`
* `test_publication_unknown_signal_is_warning_or_error_and_actionable`
* `test_manual_handoff_signal_is_actionable`
* `test_repeated_same_terminal_signal_is_deduplicated`
* `test_outcome_transition_emits_distinct_signal`
* `test_monitoring_signal_omits_secrets_raw_bodies_and_signed_urls`
* `test_corrupt_or_absent_evidence_does_not_500`

#### Completion Evidence

* Monitoring suite proves compatibility, dedupe, safe fields, and resilience.

#### Unresolved Items

* None.

<!-- rpi:task id=P03-T02 -->
### P03-T02: Update provider and integration documentation

#### Context

Current docs accurately describe many safeguards but use older status terminology and describe some residual gaps.

#### Intent

Document the canonical outcome model, compatibility map, evidence bound/key, monitoring dedupe, fail-closed rules, rollback, and any deliberate reconciler deferral.

#### Boundaries

* Included: directly related provider and integration docs.
* Excluded: unrelated architecture or product documentation.

#### Likely Targets

* docs/integration-contract.md
* docs/distribution-ux.md
* docs/spotify-video-upload.md
* docs/prd-spotify-video-live-publish.md
* docs/youtube-publish-workflow.md

#### Dependencies

* Implemented semantics from P02 and P03-T01.

#### Validation Expectations

* Docs do not promise live-provider validation.
* Rollback says disable new evidence/outcome consumption or revert the commit while legacy fields remain authoritative; do not delete provider artifacts.
* Any deferred issue is linked with exact acceptance criteria.

#### Completion Evidence

* Documentation matches tested behavior and PR claims.

#### Unresolved Items

* None.

<!-- rpi:phase id=P04 -->
## P04: Validate and deliver

### Context

The change affects provider safety and stable integration contracts. Both focused semantic checks and the full repository suite are required before delivery.

### Intent

Validate locally without live services, record evidence, commit with exact trailers, push, and open the requested unmerged PR.

### Boundaries

* Included: local tests/lint, changes record, follow-up issues if triggered, git commit/push, PR.
* Excluded: deployment, live smoke, provider canary, merge.

### Likely Targets

* .copilot-tracking/changes/2026-09-14/provider-state-reconciliation-changes.md
* Git commit/branch and GitHub PR metadata.

### Dependencies

* P01–P03.

### Validation Expectations

* Use repository-local `--basetemp=.pytest-tmp/provider-state-reconciliation` and remove it after testing; never use `/tmp`.
* Disable pytest bytecode/cache writes where practical.

### Completion Evidence

* Commands/results, commit hash, branch push, PR URL, and deferrals are recorded in the changes file.

### Unresolved Items

* None.

<!-- rpi:task id=P04-T01 -->
### P04-T01: Run targeted and full validation

#### Context

Targeted tests provide fast semantic diagnosis; the full suite protects unrelated contract behavior.

#### Intent

Prove correctness without weakening gates or invoking services.

#### Boundaries

* Included: tests and Ruff.
* Excluded: deployment, provider calls, production smoke.

#### Likely Targets

* All changed source/test/docs plus the changes record.

#### Dependencies

* P01–P03 complete.

#### Validation Expectations

Run, in order:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider \
  --basetemp=.pytest-tmp/provider-state-reconciliation-targeted \
  tests/test_publication_state.py \
  tests/test_publish.py \
  tests/test_youtube_publish.py \
  tests/test_video_distribution.py \
  tests/test_job_runner.py \
  tests/test_video_job_runner.py \
  tests/test_jobs.py \
  tests/test_job_logs.py \
  tests/test_monitoring.py \
  tests/integration/test_publish_flow.py \
  tests/test_youtube_distribute_integration.py -q

PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider \
  --basetemp=.pytest-tmp/provider-state-reconciliation-full tests/ -q

ruff check podcaster tests
ruff format --check podcaster tests
```

Remove `.pytest-tmp/provider-state-reconciliation-*` after validation. If touched docs have repository checks, run them. Do not install tools unless a validation command fails because an existing declared dependency is unavailable.

#### Completion Evidence

* Exact commands, pass counts, duration when available, and any fixed failures in the changes record.

#### Unresolved Items

* None.

<!-- rpi:task id=P04-T02 -->
### P04-T02: Commit, push, and open the PR

#### Context

The caller explicitly requires an unmerged PR to `main`.

#### Intent

Deliver one conventional, reviewable, reversible commit and PR.

#### Boundaries

* Included: issue dedupe/search and creation only if fallback triggered; commit; push; PR.
* Excluded: merge, deployment, provider canary.

#### Likely Targets

* Current branch `squad/provider-state-reconciliation`
* GitHub PR targeting `main`

#### Dependencies

* P04-T01 passes.

#### Validation Expectations

* Confirm only approved files changed.
* Suggested commit subject: `feat(publish): reconcile provider delivery states`
* Commit message ends with exactly:

  ```text
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  Copilot-Session: d2b30d61-1562-4590-b298-16e187772282
  ```

* Push `squad/provider-state-reconciliation`.
* Open PR to `main`, do not merge.
* PR sections: Summary; Outcome semantics; Backward compatibility; Safety and retained YouTube/Spotify safeguards; Evidence/monitoring bounds; Tests; Rollback; Intentional deferrals/follow-up issues; No-live-services statement.

#### Completion Evidence

* Commit hash, push confirmation, PR URL/base/head/state, and any issue URLs in the changes record.

#### Unresolved Items

* None.
