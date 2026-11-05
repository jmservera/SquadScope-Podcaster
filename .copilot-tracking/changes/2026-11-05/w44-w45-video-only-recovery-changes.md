# W44/W45 video-only recovery — implementation evidence

## Scope and first boundary

Full P01 then P02 of the approved coordinator plan. The isolated child branch
`frank/w44-w45-private-video-epoch` starts at live `origin/main` `7043ce1`.
P01-T01 starts with terminalizing expired historical video attempts while
retaining their budgets and unexpired retries. No production recovery, provider
mutation, workflow dispatch, deployment, or public promotion is authorized.

## Approved writes and validation

Child Podcaster video attempt/queue/clip isolation, narrowly authenticated
continuation, final upload adapters, focused existing tests and integration
contract; coordinator plan/details status tracking only. Validate focused
expiry, fencing, proof and provider zero-call cases, full `pytest tests/ -q`,
Ruff check/format, and applicable CI. Operational provider inventory remains
incomplete; this blocks invoking a production recovery epoch, not offline work.

## P01-T01: Historical attempt expiration

Completed: `podcaster/video/job_runner.py` terminalizes a spent original
`video-budget.json` before any clip fan-out or distribution, retaining the
original budget and previous video-runner failure once in attempt history;
an unexpired retry retains its budget and normal skip behavior. Focused
`tests/test_video_job_runner.py` clock-controlled redelivery tests: 2 passed.
Ruff check/format passed after formatting.

## P01-T02: Fenced recovery epoch

Complete offline. `podcaster/video/recovery.py`, `queue.py`, `monitoring.py`,
`video/job_runner.py` reserve a single original-job identity-bound epoch with
fixed deadline, pending send intent, deterministic queue body, CAS owner lease,
reclaim and terminal states. Human-admin bearer is required; API keys cannot
authorize the route. Expired old messages are fenced; epoch-specific scratch,
clipset, recorder and intermediate keys leave old attempts untouched. Same
request ID retries the pending send/ack without a second epoch. Rejected
authorized requests have a separate safe audit. Historical budget and failure
are preserved in the epoch record. Focused negative/positive tests cover
crash-before-ack, concurrent owner, expired second 5400s delivery, missing
visibility, stale historical message, changed proof and unknown provider
outcome.

## P01-T03: Exact proof and upload hold

Complete offline. The approved W44/W45 original job IDs and exact publication
identities and MP3 byte sizes are restricted to proof-checked continuation;
existing `validate_private_media_inputs` verifies the reviewed script and
completed WAV/MP3 bytes/digests in place. Recovery privately archives the
completed MP4 and binds its digest/epoch to the held outbox. The automatic
dispatch worker and final YouTube/Spotify adapter entry points re-read the
persisted default-deny marker before mutation. The exact-script review route
is unchanged for ordinary jobs, but recovered outboxes remain unreleasable
even after generic media review. `docs/integration-contract.md` documents
operator inventory and no-upload boundary. End-to-end offline reviewed
synthesis → recovery → MP4/outbox and resumed-worker/adapter zero-call tests
pass. No provider upload transition was implemented.

## P02-T01: Validation

In progress. Focused video/review/monitoring suites: 353 passed; provider,
budget, review and video regression suites: 765 passed (before the final
proof-rejection test). Ruff check `podcaster tests`, Ruff format check, and
`git diff --check`: passed. Initial full `pytest tests/ -q` was contaminated
by source edits while running and showed failures from transient imports;
it is not final-source validation. The Docker scaleout integration failure
was reproduced independently at clean `origin/main` `7043ce1`: recorder
containers exit `[2,2,2]` at `test_scaleout_fanout_end_to_end`. This is a
baseline failure, not waived or weakened; run final-source full suite and
report remaining failures.
