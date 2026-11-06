# W44/W45 video-only recovery — Leela implementation evidence

## 2026-11-06 preserved implementation resumption — P02-T01 / P02-T02 PR boundary

Canonical current scope: resume the existing dirty implementation on base
`3d2b558e5f10de9a6dfa747b4414b02717e8a78a` without reset/branch switch.
Owner already authorized fix/merge/deploy/private render; Hermes' 23:19 scope
acceptance is the authorization basis, not caller references or owner identity.
Live effective rules require zero approving reviews, but required checks and
thread resolution remain. Provider inventory is deferred only for private
rendering. Reconciled stale active coordinator plan/details before source edits;
historical critique and withdrawn/rejected evidence below remain historical.

Write boundary: surviving monitoring/recovery/runner implementation, focused
existing tests, integration documentation and tracking artifacts. Run focused
and full pytest, Ruff check/format, then normal child PR/exact-head CI and one
independent non-author read-only review. Return before deployment for Basher's
release-specialist validation. No cloud/queue/provider/TTS mutations, new
infrastructure, credentials, job or runner. No owner decision blocker.

## Resumed admission hardening and semantic tests — P02-T01

Completed the surviving source batch with queue-constructor failure returning a
safe 503 carrying the durable epoch ID, retaining pending intent for retry.
Startup now rejects unscoped, caller-reference, wrong-identity and multi-epoch
records rather than sending them. Added real validator tests using synthetic
MP3 bytes at each production original's exact size and full immutable identity,
changed four-field identity/actual bytes rejection, concurrent scoped CAS,
Bearer/API-key parity, invalid auth, queue factory/send failure and restart
authority rejection. These are offline synthetic media, not a live-original
SHA attestation. Live MP3 SHA/bytes must still be freshly read at release.

Final scoped selectors: 15 passed. Whole-tree Ruff check/format and diff
whitespace pass. Initial combined run passed 1201 tests; its trailing Ruff
observed an intermediate misplaced queue-failure handler, corrected immediately
and covered by the final selectors. The earlier full run was stopped because
its imported source predated this completed batch; final full run is active with
90-second faulthandler diagnostics. No gates/tests were skipped or weakened.

## Final source validation before PR — P02-T01 / P02-T02

The final combined monitoring/recovery/runner/budget/publication/Vault-regression
selection passed **1210 tests** (one existing httpx deprecation warning); final
whole-tree Ruff check/format and `git diff --check` passed.

The first final full suite completed: **6029 passed, 1 failed, 5 skipped,
2 deselected** in 903.15 seconds. The failure was the existing Docker scaleout
fixture's three recorder containers exiting 2 at their ffprobe startup
prerequisite, not a recovery assertion. The same unmodified integration test
subsequently passed **1 test in 22.26 seconds**; a complete unchanged rerun is
running without the earlier parallel focused load. This is not recorded as a
clean full-suite result. Retain both logs; exact-head CI remains mandatory.
No scanner/build definition, dependency, workflow or infrastructure changed.
Current full-run/CI/review status is maintained in the canonical coordinator
checkpoint, not inferred from historical results below.

## Historical 2026-11-05 resumption — owner-approved immutable private scope

Scope: P02-T01 only; P01 merged in #780, P02-T02 remains gated by exact-head
CI, independent security/QA review, release and human approval. Owner's 23:19
instruction and Hermes' independent verdict replace the prior authorization
blocker. Implement in this isolated child branch only: existing authenticated
manual-video route, exactly the two original publication identities, actual
manifest/MP3 proof, one server-owned private-only epoch each, same-epoch queue
reconciliation without Vault, stale ordinary callback fence, focused tests and
integration documentation. No production dispatch, provider upload, deployment,
new route, key, or identity. Run focused and full pytest and Ruff; review the
exact head before release. Historical notes below describe withdrawn earlier
work, not current authorization state.

## Existing authenticated admission and immutable epoch — P02-T01

`podcaster/monitoring.py` now routes only the two original job IDs through
`scoped_epoch` after authenticated access; the local auth-disabled mode cannot
admit them. `podcaster/video/recovery.py` binds the four immutable publication
fields, server-owned 2026-11-05 private-only directive, original review/script/
completed-claim WAV+MP3 checks including actual bytes and expected sizes,
historical expired budget and identity-wide provider quietness. One epoch per
identity is allowed, even after terminal status; concurrent same-intent callers
can reconcile the existing pending epoch and no retry renews its deadline.
API startup replays only already-authorized pending epochs without Vault,
including unavailable-queue and expired-intent handling. No separate route,
new provider action, or production invocation. Former Vault-dependent operator
endpoints were removed rather than leaving a second request-controlled mint
path. Existing worker claims and private selection/provider holds are retained.

`podcaster/video/job_runner.py` fences the old ordinary attempt's terminal
callback under the manifest guard before it can modify a reserved epoch.
`docs/integration-contract.md` describes the scoped API, deferred inventory
exception, removed routes and same-epoch retry. Tests updated in the existing
monitoring, review/security, publication-status and Vault-key suites for
the new startup and authorization boundary. Focused API/recovery/worker tests
passed 420; the added scoped negative/positive tests passed in the 292-test
monitoring/security run; publication-status/Vault startup tests passed 743.
Ruff check and format and `git diff --check` pass. The first complete suite
identified 11 assertions for the expected new startup storage read, not code
failures in admission; each was reconciled in its existing tests. The corrected
complete suite is running; exact-head CI and independent review are pending.

## 2026-11-05 owner supersession — original queue continuation

The original #780 implementation is merged, not reviewer-rejected; Leela is not
locked out of this new, separately scoped amendment. #781's subsequently merged
Key Vault/operator-key route and automatic reconciler are superseded by the
owner's direction to use existing authenticated generation/review/video-queue
paths. This worktree starts at current `origin/main` and must not reproduce
historical rejected #776/#778/#779 branches.

The original ordinary queue body contains only `job_id`; the #780 epoch body
adds an epoch ID. Both original durable video budgets are expired, so resending
the ordinary body cannot revive fan-in/fallback deadlines. The existing
authenticated manual video enqueue explicitly rejects exact-script jobs.
The proposed narrow alternative was to admit only the two exact original,
validated-MP3 identities through that route, reusing #780's manifest CAS,
immutable selection, deadline and provider hold with same-epoch operator retry.
Admission is **blocked** pending the distinct owner-authorization decision
below. No provider, production queue, media generation or deployment action
is authorized.

Validation intent: focused original identity, expired budget, same-request
retry/queue failure, stale writer, CAS conflict and provider-hold regressions;
then the repository pytest and Ruff checks. The historical #780 changes below
remain historical, not evidence that the superseding change is complete.

The existing coordinator RPI plan and phase details at
`operational-artifacts/w45-recommendations-20261102/video-only-recovery-{plan,phase-details}-20261105.md`
were discovered after the first offline code edit and reconciled in place:
P01 is merged #780 evidence; P02-T01 is **blocked before source delivery**;
P02-T02 remains blocked on P02-T01, exact-head CI/review/release. The historical
critique remains historical.

## P02-T01 — exploratory route withdrawn; authorization blocker

An offline draft wired the existing manual `video/generate` route to the #780
epoch reservation; same-inventory CAS retry was idempotent and old malformed
originals could not fall through to ordinary enqueue. However
`podcaster/auth.py` `verify_auth` accepts the shared machine API key, UI bearer
or explicit local auth-disabled mode. The draft's original-job allowlist,
caller-supplied `inventory_reference` and original audio review do **not**
establish a *separate owner approval for private rendering*. Any holder of
the machine key could mint an epoch. Existing `/api/review` also accepts that
key and a caller-chosen reviewer, so routing approval through review alone
cannot attest owner identity. #781's old operator-key route is Vault-only and
the owner explicitly superseded that dependency; removing it without a
replacement authorization would weaken #780's gate. This is a material
authorization blocker, not a budget/CAS limitation.

All exploratory source, test and documentation edits were **withdrawn** with
`git restore` in the dedicated worktree. No code change, commit or PR was
delivered; the original #780 CAS/provider hold and dormant #781 Vault paths
remain exactly as in `origin/main`. The owner must designate an existing
protected, distinguishable owner-only approval record and who may write it,
or expressly accept an alternative auth scope after independent security
review. Only then can same-epoch retry be safely wired without new
infrastructure; if existing credentials cannot distinguish owner from machine
callers, do not activate the route. One narrow candidate, **conditional on
owner confirmation of exclusive UI credential custody**, is a Bearer-only
private-render approval recorded under the original manifest CAS through the
existing review API, then require that exact approval at epoch reservation;
reject shared API keys on this scoped operation. Existing JWT validation alone
is not an attestation that the token holder is the owner, so this is not yet
an accepted implementation.

Exploratory draft validation (not deliverable validation): `uv run --extra dev
python -m pytest` on monitoring, review/security, video runner/budget and
Vault-key unit tests **467 passed**, one httpx warning; Ruff check and format
passed. The full-suite started, then was stopped after discovery of the
authorization blocker. No final-source tests, exact-head CI, or PR exist.

## Historical #780 implementation scope and boundary

Full P01 offline implementation and P02-T01 validation on a dedicated clean worktree
and branch `leela/private-video-recovery-20261105`, based on `origin/main`
`7043ce1aec4624dc84de22be9f8f4f41b6ecac48`. P02-T02 merge/deployment
remains gated on independent review and human approval; no live recovery, queue,
generation, provider mutation, or public promotion is authorized.

P01-T01–T03 have offline implementation and targeted test evidence below.
P02-T01 full-suite and exact-head CI evidence are pending. Candidate outputs
remain private, attempt-isolated and selected only by the original job's
authoritative manifest CAS. Identity-wide provider intent fails closed.

## Historical #780 planned validation

Clock/race/proof/provider-negative tests in existing tests, targeted and complete
`pytest -q`, `ruff check podcaster tests`, `ruff format --check podcaster tests`,
and exact-head CI on a new draft PR. Historical PRs #776/#778/#779 are evidence,
not accepted code or tests. The dependency lockfile is out of this PR's scope.

## Historical #780 blockers and status

No unresolved owner design choice for offline implementation. Live recovery still
requires owner-visible provider inventory; release requires independent review
and all required exact-head CI. No live resource was read or mutated for this work. Record completed work and
check results below.

## Spent attempt terminalization — P01-T01

The ordinary runner now resolves its original persisted budget before creating
scratch/clip clients or claiming the pipeline. An expired redelivery atomically
records a distinct terminal attempt outcome without resetting `video-budget.json`
or overwriting the original failure. Ordinary unexpired attempts retain their
budget and continue down the same path.

Validation: `python3 -m pytest tests/test_video_job_runner.py -q -k
'expired_attempt or short_retry_reuses_original_budget or already_processed_skips or
expired_lifecycle_budget'` passed (5 tests). Ordinary regressions remain in the
focused suite below.

## Manifest-bound operator epoch and durable dispatch — P01-T02

The operator route requires normal API authentication and a separate configured
operator key. A reservation verifies the exact four-field original publication
identity, immutable review/script/completed audio claim and WAV/MP3 bytes,
digest and size; only the two specified old jobs may pass. It CAS-persists a
single ordered epoch, deterministic queue message, fixed 5100-second deadline,
lease owner and pending dispatch in the original manifest. It never renews the
historical budget or runs TTS. Expired redelivery cannot create an epoch. Recovery
uses an epoch-specific clip and downstream-ownership namespace, including when
an older downstream permit still has unexpired queue visibility.

After reservation-but-before-send failure, the API's operator-key-configured
background reconciler retries pending sends on startup and every 60 seconds;
an authenticated manual reconciliation route provides the same idempotent
retry. Send-before-ack is acknowledged from a claimed worker without a second
epoch. With the operator key set, an unavailable queue leaves pending intent
retryable until expiry, when unselected epochs become terminal even after
dispatch acceptance. A new attempt requires new authorization and a different
inventory reference. Only authorized pending intentions can be sent by
reconciliation. None was invoked against production.

## Immutable private candidates and provider fence — P01-T03

Each ordinary and recovery attempt writes immutable archive and held-outbox
candidate keys bound to the original identity, attempt/epoch, proof and MP4
digest. Writes alone confer no authority. The manifest CAS records a pending
selection attempt and then a single authoritative selected-private-output
pointer after readback and owner/proof/deadline validation. CAS retry reloads
the whole manifest; losers and pre-commit crash leftovers are inert. Post-CAS
replay, including 5400-second redelivery, resolves that same pointer and verifies
the persisted candidate identities, outbox, MP4 bytes and digest. Missing,
ambiguous or corrupt data fail closed. Old-worker late writes cannot overwrite
or select the recovery candidate.

Persisted denial on both YouTube/Spotify adapter boundaries and automatic
distribution rejects these originals even from resumed workers. Prior, in-flight,
unknown or ambiguous provider intent/outcome anywhere in the original identity
blocks recovery. There is no transition from held private output to upload,
script-only approval cannot authorize upload, and other ordinary exact-script
jobs retain their existing contract.

## Validation and release boundary — P02-T01 pending full run

Targeted `tests/test_video_budget.py tests/test_video_job_runner.py
tests/test_synthesis_review_security.py tests/test_monitoring.py` passed
**401 tests** after fixing a budget-persistence regression found in a complete
suite (the preceding full run: 6020 passed, 4 failed, 5 skipped, 2 deselected).
The added restart, queue-unavailable, accepted-epoch expiry and API startup
tests pass. Ruff check and format on `podcaster tests`, plus `git diff --check`,
pass. The corrected complete `uv run pytest tests/ -q
--basetemp=.scratch/pytest-full-v13` passed **6025 tests, 5 skipped,
2 deselected**. No container, infra or workflow definitions changed, so their
specialized scanners/builds were not applicable. Exact-head CI, eligible
independent review and release remain pending.
Open issue #777 concerns the lockfile gate; it must not be waived or patched
in this focused change. No merge, deployment, production epoch, provider write
or public promotion occurred.
