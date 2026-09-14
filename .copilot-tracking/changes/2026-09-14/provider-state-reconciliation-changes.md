<!-- markdownlint-disable-file -->
# RPI Changes: Provider State Reconciliation

## Metadata

* Task ID: provider-state-reconciliation
* Task slug: provider-state-reconciliation
* Related plan: .copilot-tracking/plans/2026-09-14/provider-state-reconciliation-plan.md
* Phase details: .copilot-tracking/details/2026-09-14/provider-state-reconciliation-phase-details.md
* Status: Complete — validated and independently accepted; delivery metadata pending

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
* Added immutable atomic append at `jobs/{job_id}/publication-evidence.json`, monotonic sequence numbers, exact dedupe key, newest-100 retention, corrupt-document fail-closed behavior, and safe detail filtering.
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

* Commit, pushed branch, and unmerged PR metadata are recorded in Git/GitHub and the final delivery handoff because a commit cannot contain its own final SHA or later PR URL.
* Merge/deployment/live provider activity: prohibited.
