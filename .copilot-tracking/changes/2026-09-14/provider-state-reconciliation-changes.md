<!-- markdownlint-disable-file -->
# RPI Changes: Provider State Reconciliation

## Metadata

* Task ID: provider-state-reconciliation
* Task slug: provider-state-reconciliation
* Related plan: .copilot-tracking/plans/2026-09-14/provider-state-reconciliation-plan.md
* Phase details: .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md
* Status: Complete — P08 correction validated; awaiting independent review

## Implementation Opening

* Active scope: Full plan, P01-T01 through P04-T01; P04-T02 remains reserved for coordinator/reviewer delivery.
* First execution boundary: P01-T01, canonical outcome and compatibility model.
* Approved write boundary: Production, test, documentation, and tracking targets listed below, with at most one new production module and one new test module.
* Validation intent: Run the P04-T01 targeted pytest command, full `pytest tests/ -q`, `ruff check podcaster tests`, and `ruff format --check podcaster tests`, using and then removing a repository-local basetemp.
* Current blockers: None.
* Live operations: Provider, production, deployment, merge, commit, push, and PR operations are excluded from this implementation invocation.
* Required delivery trailers, corrected before source edits:

  ```text
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  Copilot-Session: d2b30d61-1562-4590-b298-16e187772282
  ```

## Independent Review Findings Implementation

* Active scope: Resolve the three supplied review findings in the existing dirty tree without changing the reviewer-owned review record.
* Source boundary: `podcaster/video/distribution.py`, `podcaster/video/job_runner.py`, and `podcaster/publish.py`.
* Test boundary: Focused existing tests for distribution, video job publication recording, and publish evidence/signal finalization.
* Required behavior: Preserve canonical provider outcomes; fail closed after post-mutation evidence failure without retrying distribution; treat signal failure as notification-only.
* Validation intent: Run only focused changed-area pytest selections plus Ruff on touched files.
* Prohibited operations: Reset/discard/redesign, commit, push, PR creation, live APIs, and production mutation.
* Current blockers: None.

## Livingston Rejection Revision

* Active scope: P05-T01 through P05-T04.
* Identity: canonical requests require and persist the four-field publication
  identity; fully identity-less requests are explicitly persisted as legacy.
* Provider records: normalized status, transport, provider ID, native state,
  verification, timestamps/evidence source, error code, and retry-blocked
  semantics are separate from legacy outcomes.
* Public completion: provider readback is not anonymous verification; draft,
  unlisted/private, gated/manual, pending, and unknown records cannot aggregate
  as publicly completed.
* Retention: P05 removed record-count eviction and declared the minimum window;
  P06 corrects the later-discovered seven-day infrastructure lifecycle conflict.
* Delivery: canonical lockfile regeneration and full local validation are
  complete; the revision is committed, pushed, reflected in PR #680, and left
  open/unmerged with CI started.

## Reviewer-Lockout Correction

* Active scope: P06-T01 through P06-T04.
* Independent owner: Frank; prior implementers are locked out from revising
  these newly rejected artifacts.
* Retention boundary: write canonical evidence under a dedicated durable prefix
  outside the deployed seven-day `jobs/` lifecycle match and retain legacy read
  compatibility.
* State boundary: explicit legacy mode cannot produce canonical evidence;
  normalized `public` requires `published` plus `external_verified`; Spotify RSS
  is expected but pending without independent verification.
* Mutation boundary: Spotify RSS participates in the accepted-identity
  pre-mutation claim/retry fence so a crash cannot append the episode twice.
* Validation intent: targeted and full tests, Ruff/format, infrastructure
  validation, lockfile recompilation/diff, and repository diff checks.
* Current blockers: None.

## Post-Delivery Retry-Safety Correction

* Completed scope: P07-T01 through P07-T03.
* Triggering evidence: PR #680 discussions `discussion_r4014202116` and
  `discussion_r4014202185`.
* Evidence migration: duplicate legacy records must populate the new durable
  blob instead of producing empty corrupt canonical evidence.
* Provider retry fence: `uploaded` blocks YouTube, Spotify RSS, and Spotify
  video mutations but remains unsuccessful for aggregate delivery.
* Validation intent: focused publication/distribution tests, full Ruff/format,
  diff integrity, commit/push, thread resolution, and fresh CI.
* Delivery evidence: committed and pushed
  `49b9c6e9f204d04c3d7fab2e68292b66b52c153e`; focused suite **212 passed**;
  full suite **3027 passed, 2 skipped, 2 deselected**; Ruff and diff checks
  passed; both triggering review threads were answered and resolved.

## PR #693 Retry-Safe Claim Correction Opening

* Active scope: P08-T01 through P08-T03.
* Approved write boundary: this plan/details/changes set,
  `podcaster/publication_state.py`, `podcaster/publish.py`,
  `tests/test_publication_state.py`, and `tests/test_publish.py`.
* First execution boundary: make retry authorization exact-identity,
  single-claim, and repeatable only after a later retryable failure.
* Required behavior: preserve deterministic create rejection as blocked;
  preserve credential expiry during post-POST ambiguous recovery as blocked;
  retain explicit pre-create credential rejection as retryable.
* Validation intent: run exactly the caller-required targeted pytest command,
  full Ruff check, Ruff format check, and `git diff --check`.
* Excluded operations: commit, push, review-thread reply/resolution, branch
  changes, live provider calls, and production mutation.
* Current blockers: None.

### Exact single-use claim authorization — P08-T01

* `append_evidence` now treats any later re-armable claim as consuming earlier
  authorization, regardless of claim operation.
* A duplicate retryable failure is retained when a later claim proves it belongs
  to a new attempt, allowing two consecutive credential failures to authorize
  two distinct retry claims without authorizing concurrent contenders.
* Spotify video retry authorization now requires exact accepted job ID, week,
  publish run ID, article digest, and manifest digest.

### Deterministic and post-POST failure fences — P08-T02

* Deterministic create rejection appends `create_episode_failure` with
  `code=create_rejected` and `retry_blocked=true`, so its complete pre-create
  snapshot cannot be interpreted as an ambiguous create on the next attempt.
* The video reconcile path marks entry into ambiguous-create recovery. Credential
  expiry during its follow-up listing appends
  `ambiguous_recovery_credentials_expired` with `retry_blocked=true` and returns
  `publication_unknown`; direct credential rejection from the create request
  remains retryable.

### Focused regression implementation — P08-T03

* Added one regression for cross-claim authorization consumption, one for two
  consecutive identical credential failures, one for exact identity binding,
  one for deterministic create rejection with a later untitled draft, and one
  for credential expiry during post-POST ambiguous recovery.
* Initial targeted validation passed **523 tests** before formatting.
* Ruff lint and Git whitespace checks passed; the first format check identified
  two changed files, which were formatted before the required clean rerun.

### Final P08 validation and handoff

* `pytest tests/test_publication_state.py tests/test_publish.py
  tests/test_video_job_runner.py -q` — passed, **523 passed in 53.40s**.
* `ruff check podcaster tests` — passed, **All checks passed**.
* `ruff format --check podcaster tests` — passed, **183 files already
  formatted**.
* `git diff --check` — passed with no output.
* P08-T01, P08-T02, P08-T03, and P08 are complete. Commit, push, and
  review-thread actions were not performed; Fry owns the independent review
  gate.

### Infrastructure-backed durable publication evidence — P06-T01

* Canonical evidence now writes to
  `publication-evidence/{accepted_job_id}.json`, outside the deployed
  seven-day `jobs/` and `bakeoff/` lifecycle matches.
* The lifecycle prefix list is deployment-owned rather than caller-configurable,
  so the durable evidence prefix cannot be added accidentally through a
  deployment parameter.
* Existing `jobs/{accepted_job_id}/publication-evidence.json` remains readable
  and is migrated into the durable document on the next append.
* Infrastructure and storage regressions prove the path separation, legacy
  compatibility, and retention contract.

### Fail-closed identity and public projections — P06-T02

* Explicit `publication_identity_mode=legacy` is rejected before canonical
  evidence can be created, even when all four identity fields are present.
* Persisted `provider_status=public` is clamped unless the canonical outcome is
  `published` and verification is `external_verified`.
* Spotify RSS is a normalized expected public target; a successful feed write
  remains `pending` until independent external verification.

### Identity-bound Spotify RSS mutation fence — P06-T03

* Canonical video execution now claims and checks `spotify_rss:video` evidence
  before feed mutation, alongside YouTube and Spotify upload claims.
* Existing `publication_unknown` and `manual_handoff_required` RSS markers
  suppress another append but do not count as succeeded or completed.
* Callback evidence preserves normalized RSS provider fields and keeps RSS
  evidence distinct from Spotify upload evidence.

### Delivered the reviewer-lockout correction — P06-T04

* Committed the retention, state, identity, RSS fencing, regressions,
  infrastructure, documentation, and tracking changes as
  `3aa87f60b821bb8bc7a88c44b6cc51aa8d7140b4`.
* Pushed `squad/provider-state-reconciliation` to PR #680 with the required
  trailers and without deployment, provider mutation, dispatch, canary, or
  merge.
* Fresh PR CI is owned by the pushed head. All six current review threads were
  answered with exact evidence and resolved; the PR remained open and unmerged
  with zero unresolved threads.

### Closed final migration and uploaded-state review gaps

* Related phase or task: P06-T01, P06-T03, P06-T04
* Files: `podcaster/publication_state.py`,
  `podcaster/video/distribution.py`, `tests/test_publication_state.py`,
  `tests/test_video_distribution.py`
* What changed and why: An exact duplicate found only in the legacy evidence
  document now writes the legacy bytes to the canonical durable path instead of
  creating an empty document. `uploaded` snapshots now block YouTube, Spotify
  RSS, and Spotify video mutations and do not count as successfully delivered.
* Completion evidence: Migration preserves the complete legacy record set on a
  duplicate append, while all three provider mutation mocks remain untouched
  for prior `uploaded` evidence.
* Validation: Focused publication/distribution/video-runner suite passed
  **212 tests**. Final full repository suite passed **3027 tests, 2 skipped,
  2 deselected**, with the pre-existing httpx warning; Ruff and diff checks
  passed.

### Canonical versus bounded legacy identity — P05-T01

* Canonical requests are selected by either new identity field or explicit
  `publication_identity_mode=canonical` and require exact `YYYY-WNN`, a
  decimal-string run ID, and both lowercase 64-hex digests.
* Accepted manifests persist all four values unchanged and record canonical
  mode. Partial canonical inputs cannot be relabeled as legacy; explicit legacy
  mode rejects canonical-only fields.
* Publication identity rejects run-ID conflicts. Direct synthesis publishing,
  review-gated/automatic publishing, and video distribution block malformed
  canonical identity before provider mutation while retaining the
  identity-less legacy compatibility path.

### Normalized provider state and public aggregation — P05-T02

* Evidence and distribution records keep normalized status, transport status,
  canonical outcome, provider ID/native state, verification, checked time,
  evidence source, last error code, and retry-blocked state independently.
* Existing `status=published` video snapshots remain readable as at-most-once
  markers and add `provider_status`; the legacy field alone never proves
  visibility.
* Mutation/upload response uses `verification=none`; provider/API readback uses
  `provider_readback`; only anonymous `external_verified` proof can set
  normalized `status=public`. Every applicable target must be public for
  `public_delivery_status=completed`.

### Count-safe evidence history — P05-T03

* Removed count eviction from the immutable append-only accepted-job evidence
  document. It declares a 28-day minimum and
  `append_only_no_count_eviction`, preserving all 120 records in the retention
  regression rather than dropping the earliest identities. The later P06
  correction moves canonical writes outside the seven-day `jobs/` lifecycle.

### Generated lock and revision validation — P05-T04

* Regenerated `requirements.lock` with `uv 0.10.11` using
  `uv pip compile pyproject.toml --extra dev --extra video --python-version
  3.11 --no-header -o requirements.lock`.
* CI-equivalent lock recompilation and diff passed (68 packages; the existing
  `myst-parser` extra warning remains informational).
* Final targeted revision suite: **742 passed**, 1 pre-existing httpx
  deprecation warning.
* Final full repository suite: **3013 passed, 2 skipped, 2 deselected**, with
  the same pre-existing httpx deprecation warning.
* The first full-suite attempt exposed a compatibility-fixture `MagicMock`
  value in the additive public-delivery field. The field is now type-sanitized
  to `pending`; the scale-out fanout regression passed independently before
  the clean full-suite rerun.
* Full Ruff: `ruff check podcaster tests` passed; `ruff format --check
  podcaster tests` reported 183 files formatted.

### Review-thread reconciliation and branch review — P05-T04

* Reviewed all branch files against `origin/main` and retained the prior
  provider, queue, approval, quota, budget, lease, secret, and rollback
  safeguards.
* Closed the remaining fail-closed gaps identified on PR #680: `uploaded`
  blocks retries and eligibility; atomic intent duplicates suppress concurrent
  Spotify/audio/video mutation; RSS and Spotify upload evidence keys remain
  distinct; post-mutation evidence failure propagates into distribution state;
  failed/partial distribution is not collapsed to completed; YouTube unknown
  readback is not reported as success; legacy identity remains optional while
  malformed canonical identity blocks.
* Also reconciled the review summary's suppressed findings: ASCII-only run IDs,
  language-aware YouTube intent, requested run-ID precedence, RSS outcome
  projection, deterministic Spotify failure outcome, safe malformed YouTube
  readback, and monitoring fallbacks to persisted manifest outcomes.

## Approved Write Boundary

* Existing production targets:
  * podcaster/publish.py
  * podcaster/video/distribution.py
  * podcaster/video/youtube_publish.py
  * podcaster/job_runner.py
  * podcaster/video/job_runner.py
  * podcaster/orchestration.py
  * podcaster/jobs.py
  * podcaster/job_logs.py
  * podcaster/monitoring.py
* Maximum one new production module: podcaster/publication_state.py.
* Existing test targets:
  * tests/test_publish.py
  * tests/test_youtube_publish.py
  * tests/test_video_distribution.py
  * tests/test_job_runner.py
  * tests/test_video_job_runner.py
  * tests/test_jobs.py
  * tests/test_job_logs.py
  * tests/test_monitoring.py
  * tests/integration/test_publish_flow.py
  * tests/test_youtube_distribute_integration.py
* Maximum one new test module: tests/test_publication_state.py.
* Documentation targets:
  * docs/integration-contract.md
  * docs/distribution-ux.md
  * docs/spotify-video-upload.md
  * docs/prd-spotify-video-live-publish.md
  * docs/youtube-publish-workflow.md
* GitHub follow-up issues only when a specific safe-reconciliation gap triggers the approved fallback.
* No live provider APIs, production operations, deployment, canary, or merge.
* No unrelated source, test, infrastructure, workflow, or dependency changes.

## Marker Order

1. P01-T01 — canonical outcome and compatibility model
2. P01-T02 — bounded evidence and deduplicated signals
3. P02-T01 — Spotify integration
4. P02-T02 — YouTube integration
5. P02-T03 — jobs/distribution propagation
6. P03-T01 — monitoring/status projection
7. P03-T02 — documentation
8. P04-T01 — validation
9. P04-T02 — commit, push, PR

## Implementation Checklist

* [x] Record baseline/targeted validation without live services.
* [x] Record each completed `Pxx-Txx` item and exact changed files.
* [x] Record legacy compatibility mappings and evidence.
* [x] Record every retained YouTube and Spotify safeguard test.
* [x] Record mutation ambiguity/no-repeat evidence.
* [x] Record evidence bound, key, atomicity, and monitoring dedupe evidence.
* [x] Record targeted and full validation results.
* [x] Record narrowly scoped follow-up issues and acceptance criteria if fallback is triggered.
* [ ] Record conventional commit hash and exact trailers.
* [ ] Record pushed branch and unmerged PR to `main`.

## Compatibility Evidence

### Canonical outcome and legacy adapters — P01-T01

* Added `podcaster/publication_state.py` with the exact five canonical outcomes and strict validation.
* Added explicit additive mappings for legacy `PublishResult.status`, `DistributionResult.status`, and Spotify `publication_state_unknown`; no legacy accepted value was removed or renamed.
* Added `tests/test_publication_state.py` for exact vocabulary, rejection, mapping, and legacy-manifest behavior.

## Safety and Safeguard Evidence

### Atomic evidence and signal primitives — P01-T02

* Added accepted-job identity validation for week, decimal `publish_run_id`, pinned article SHA-256, manifest SHA-256, accepted lifecycle evidence, matching `job_id`, and dry-run rejection.
* Added immutable atomic append at `publication-evidence/{job_id}.json`,
  outside the deployed seven-day `jobs/` lifecycle rule, with legacy-path read
  compatibility, monotonic sequence numbers, exact dedupe, no count-based
  eviction, a 28-day minimum retention contract, corrupt-document fail-closed
  behavior, and safe detail filtering.
* Extended durable job logs with optional atomic `dedupe_key`; callers omitting it retain append behavior.
* Added outcome-transition monitoring signals keyed by accepted job, platform, media kind, outcome, and provider artifact ID.

### Spotify fail-closed integration — P02-T01

* Added canonical outcomes to `PublishResult` and `VideoPromoteResult` without changing legacy statuses or terminal states.
* Spotify create, process, metadata, and publish POSTs are one-shot where response ambiguity can duplicate state. Audio live publication now requires provider read-back for canonical `published`.
* Accepted-job audio attempts persist mutation intent before provider calls, stop on prior blocking evidence, write terminal evidence/signals, remain inert in dry-run, and convert post-mutation evidence failure to `publication_unknown`.
* Spotify video promotion now carries the accepted job and publish run through distribution. Existing live gates, protected IDs, audio/video collision rejection, read-before/write-once/read-after flow, exact-title draft reconciliation, multipart behavior, `uploadType=default`, ETag handling, language isolation, and credential handling remain covered by `tests/test_publish.py` and `tests/test_video_distribution.py`.

### YouTube fail-closed integration — P02-T02

* YouTube upload records retain legacy `status: published` as the existing at-most-once marker while adding `outcome: draft_created`.
* Canonical evidence and legacy snapshots both skip duplicate upload. `videos.update` remains one-shot and now performs `videos.list` read-back; only confirmed public state is `published`, scheduled private state is `draft_created`, and transport/HTTP/read-back ambiguity is `publication_unknown`.
* Existing unlisted defaults, public-draft rejection, approval gate, scheduled private+`publishAt`, metadata/playlist checks, bearer secrecy, upload validation/chunking, locale routing, required-delivery classification, blob-first archive, and idempotent playlist repair remain intact.

### Job and distribution propagation — P02-T03

* Orchestration persists one `publish_run_id` before provider mutation and stores additive outcome/run fields in `publishing.result`.
* Direct synthesis publishing now stores the previously discarded result under `generation.publish_result` while preserving non-blocking synthesis and video enqueue.
* Video jobs persist/reuse one run ID, write pre-mutation intent, prefer canonical evidence over the legacy snapshot, thread identity into Spotify promotion, and persist per-provider outcomes plus the unchanged aggregate distribution status.

### Monitoring projection — P03-T01

* Authenticated job detail adds optional `publication_outcome`, per-provider `publication_outcomes`, and bounded `publication_evidence`.
* Episode summaries add optional `publication_outcome`; absent/corrupt evidence remains non-fatal and legacy fields are unchanged.
* Durable publication signals use warning/error severity for operator-action states and atomic dedupe without secrets, raw bodies, article content, or signed URLs.

## Change Evidence

### Independent review findings — post-mutation delivery state

* Changed `podcaster/video/distribution.py` so a failed Spotify upload is always
  `publication_unknown`, and an unrecognized promotion terminal state also fails closed
  instead of defaulting to `draft_created`.
* Changed `podcaster/video/job_runner.py` so post-provider evidence failure overwrites the
  compatibility snapshot to `publication_unknown` with `retry_blocked: true`, logs the
  failure, and returns without raising. Signal failure is caught separately and leaves the
  persisted provider outcome unchanged.
* Changed `podcaster/publish.py` so evidence persistence and signal emission are separate
  failure domains. Only evidence failure rewrites the result to `publication_unknown`;
  signal failure is logged and returns the original provider result.
* Added focused regressions in `tests/test_video_distribution.py`,
  `tests/test_video_job_runner.py`, and `tests/test_publish.py`.
* Reviewer-owned canonical review artifact was not edited.
* Follow-up lockout fixes make evidence-read failure block Spotify audio before provider
  I/O and ensure unknown/manual-handoff video skips suppress mutation without counting as
  successful delivery or producing aggregate `completed`.

### Foundation implementation — P01

* Completed P01-T01 and P01-T02.
* Changed: `podcaster/publication_state.py`, `podcaster/job_logs.py`, `tests/test_publication_state.py`, and `tests/test_job_logs.py`.
* Focused evidence: `pytest tests/test_publication_state.py tests/test_job_logs.py -q --basetemp=.pytest-basetemp` — 45 passed.

### Provider, propagation, monitoring, and docs — P02 and P03

* Completed P02-T01, P02-T02, P02-T03, P03-T01, and P03-T02.
* Changed provider/runtime files: `podcaster/publish.py`, `podcaster/video/youtube_publish.py`, `podcaster/video/distribution.py`, `podcaster/orchestration.py`, `podcaster/job_runner.py`, `podcaster/video/job_runner.py`, and `podcaster/monitoring.py`.
* Changed tests: `tests/test_publish.py`, `tests/test_youtube_publish.py`, `tests/test_video_distribution.py`, `tests/test_job_runner.py`, `tests/test_monitoring.py`, and `tests/integration/test_publish_flow.py`.
* Updated all five approved integration/provider documents with outcome semantics, fail-closed retry rules, evidence bounds, compatibility, and rollback.

## Validation Evidence

* Prescribed targeted provider/publication/job/monitoring suite:
  `PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider --basetemp=.pytest-tmp/provider-state-reconciliation-targeted ... -q`
  — **705 passed**, 1 warning.
* Reviewer-fix focused suite:
  `PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider --basetemp=.pytest-tmp/provider-state-review-fixes tests/test_publish.py tests/test_video_distribution.py tests/test_video_job_runner.py -q`
  — **425 passed**.
* Final lockout-fix focused suite over the same modules — **429 passed**.
* Delivery-final full suite:
  `PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider --basetemp=.pytest-tmp/provider-state-reconciliation-delivery-final tests/ -q`
  — **2994 passed, 2 skipped, 2 deselected**, 1 pre-existing deprecation warning.
* `ruff check podcaster tests` — passed.
* `ruff format --check podcaster tests` — 183 files already formatted.
* `git diff --check` — passed.
* No live provider API, production, deployment, or canary operation was performed.

### Reviewer-lockout correction validation

* Changed-surface provider/publication/job/monitoring/infrastructure suite:
  **775 passed**, 1 pre-existing httpx deprecation warning.
* Focused six-finding suite: **300 passed**.
* Full repository suite: **3027 passed, 2 skipped, 2 deselected**, with the
  pre-existing httpx deprecation warning.
* Scale-out fanout regression: **1 passed**.
* `ruff check podcaster tests` and `ruff format --check podcaster tests` passed;
  183 files were already formatted.
* `az bicep build --file infra/main.bicep --stdout` passed with the existing
  nullable-module BCP318 warning; exact storage network-access contract passed.
* Checkov Bicep scan: **34 passed, 0 failed**.
* CI-equivalent `uv 0.10.11` lock recompilation and diff passed.
* `python3 -m compileall -q podcaster` and `git diff --check` passed.

### Independent review finding validation

* `pytest tests/test_video_distribution.py::TestDistributeVideo::test_spotify_video_upload_reports_draft_created_not_published tests/test_video_distribution.py::TestDistributeVideo::test_failed_spotify_video_upload_never_reports_draft_created tests/test_video_job_runner.py::test_video_publication_evidence_failure_overwrites_snapshot_unknown tests/test_video_job_runner.py::test_video_publication_signal_failure_preserves_provider_outcome tests/test_publish.py::TestPublishEpisode::test_spotify_signal_failure_preserves_persisted_provider_outcome -q --basetemp=.pytest-tmp/review-findings`
  — 5 passed.
* `ruff check podcaster/video/distribution.py podcaster/video/job_runner.py podcaster/publish.py tests/test_video_distribution.py tests/test_video_job_runner.py tests/test_publish.py`
  — passed.
* `ruff format --check podcaster/video/distribution.py podcaster/video/job_runner.py podcaster/publish.py tests/test_video_distribution.py tests/test_video_job_runner.py tests/test_publish.py`
  — 6 files already formatted.

## Follow-Up Issues

* None created. The contract-deferred shared decisions remain outside this PR: authoritative
  YouTube production privacy/review policy, final normalized-record schema location/version,
  job-identity migration, Spotify pagination completeness, YouTube create-window resolution,
  possible future Spotify video live automation, and final retention/external-oracle policy.

## Delivery Evidence

* Implementation commit:
  `3aa87f60b821bb8bc7a88c44b6cc51aa8d7140b4`.
* Pushed branch: `squad/provider-state-reconciliation`.
* PR: https://github.com/jmservera/SquadScope-Podcaster/pull/680 remains open
  and unmerged.
* Merge/deployment/live provider activity: prohibited.
