# W44/W45 video-only recovery — Leela implementation evidence

## Current scope and boundary

Full P01 offline implementation and P02-T01 validation on a dedicated clean worktree
and branch `leela/private-video-recovery-20261105`, based on `origin/main`
`7043ce1aec4624dc84de22be9f8f4f41b6ecac48`. P02-T02 merge/deployment
remains gated on independent review and human approval; no live recovery, queue,
generation, provider mutation, or public promotion is authorized.

P01-T01–T03 have offline implementation and targeted test evidence below.
P02-T01 full-suite and exact-head CI evidence are pending. Candidate outputs
remain private, attempt-isolated and selected only by the original job's
authoritative manifest CAS. Identity-wide provider intent fails closed.

## Planned validation

Clock/race/proof/provider-negative tests in existing tests, targeted and complete
`pytest -q`, `ruff check podcaster tests`, `ruff format --check podcaster tests`,
and exact-head CI on a new draft PR. Historical PRs #776/#778/#779 are evidence,
not accepted code or tests. The dependency lockfile is out of this PR's scope.

## Blockers and status

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
