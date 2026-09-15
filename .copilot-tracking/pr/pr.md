# feat(video): enforce a shared stage budget and safe provider lifecycle

This PR replaced independent video-pipeline timeouts with one durable 5100-second lifecycle. It bounded fan-out recording, deterministic fallback, rendering, archive verification, provider mutation/readback, queue disposition, and cleanup while preserving the existing provider-state and public-verification safeguards.

## Changes

### Shared budget and owned cancellation

- Added a centralized stage model with conservative monotonic and durable UTC timing across redelivery.
- Enforced the W38 lifecycle:

| Boundary | Elapsed time | Final behavior |
|---|---:|---|
| Preflight | T+300 | Stopped new preflight work |
| Recorder fan-in | T+1200 | Stopped waiting for missing clips |
| Deterministic fallback | T+1500 | Completed browser-free fallback or failed `recording_insufficient` |
| Render/provider reserve | T+3300 | Stopped new rendering and required 1800 seconds for provider work |
| Verified archive | T+3600 | Completed archive upload/readback/probe or remained pending |
| Provider mutation | T+4500 | Denied every new provider mutation or retry |
| Readback/evidence | T+4920 | Cancelled remaining provider readback/evidence work |
| Shutdown | T+5100 | Completed terminal evidence, lease, queue disposition, and bounded cleanup |

- Added owned process and callable runners that terminated, killed, joined, and reaped timed-out work instead of abandoning it.
- Removed partial browser, ffmpeg, and ffprobe outputs after timeout or failure.

### Recorder convergence and validated replay

- Persisted immutable clipsets, durable first recorder admission, explicit attempt outcomes, and content-addressed terminal clip media.
- Limited each clip by the earliest of its 720-second lifetime, two explicit failed executions, browser limit, recorder/visibility reserve, and the parent fan-in deadline.
- Replaced late browser capture with deterministic local fallback media and protected the winning manifest/blob identity from late recorders.
- Added size, SHA-256, bounded probe, schema, and logical-identity validation for audio, clips, normalized segments, composed video, and archives.
- Reused valid checkpoints on redelivery and rejected legacy, corrupt, equal-size-altered, zero-byte, unprobeable, or identity-mismatched artifacts.

### Durable distribution boundary and provider safeguards

- Persisted and revalidated `rendered_pending_distribution` before provider intent so pre-mutation crashes resumed without rerendering.
- Rechecked provider admission immediately before every YouTube, playlist, RSS, and Spotify mutation or retry.
- Preserved ambiguous post-mutation outcomes as `publication_unknown` with retry blocking rather than repeating create/insert operations.
- Kept canonical publication identity, approval, privacy, quota, resumable upload, Spotify draft/live/pagination/protected-ID, RSS CAS/exactly-once, partial retry, and externally verified public-state behavior.
- Made large YouTube uploads create exactly one resumable session while carrying prior-mutation state through chunk and status boundaries.

### Shutdown, infrastructure, and operations

- Recorded terminal timing/state and released the owned editor lease before mandatory bounded queue disposition.
- Ran optional scratch cleanup only after queue disposition and only within remaining shutdown time.
- Kept the ACA editor hard timeout at 5400 seconds while enforcing the application-owned 5100-second deadline.
- Set recorder replica, visibility, and capture defaults below the parent fan-in window.
- Updated the scale-out RFC, Azure deployment runbook, Compose/Azurite integration, Bicep modules, and implementation evidence.

## Validation

- `pytest tests/ -q` — 3141 passed, 2 skipped, 2 deselected; one pre-existing httpx deprecation warning
- Mandatory focused video/provider/publication/infra/integration matrix — 1323 passed, 2 deselected
- `pytest tests/integration/test_scaleout_fanout.py -q` — 1 passed against real Azurite and Docker Compose
- `ruff check podcaster tests` — passed
- `ruff format --check podcaster tests` — passed
- `python3 -m compileall -q podcaster tests` — passed
- Changed Bicep module builds — passed
- CI-equivalent Checkov Bicep scan — 34 passed, 0 failed
- Dependency lock assertion and `git diff --check` — passed
- Independent lockout implementation review — accepted with no open defects

## Rollout and rollback

- **Canary:** Run one non-production, provider-disabled or dry-run episode and inspect timing evidence, archive validation, queue disposition, and scratch cleanup before enabling normal provider delivery.
- **Rollout:** Enable the updated editor/recorder images without changing the 5400-second ACA hard timeout or the application stage constants.
- **Rollback:** Route jobs back to the prior image while preserving `rendered_pending_distribution` and provider evidence. Do not blind-retry ambiguous provider mutations; reconcile or hand off according to the recorded state.

No production W38 job, provider state, deployment, merge, or live publication was changed while preparing this PR.

## Related Issues

- Related to #552
- Follow-up: #681

## Follow-up Tasks

- Implement the atomic outbox/claim/reconcile distribution worker, independent queue/lease/budget, crash matrix, monitoring, migration, canary, and rollback defined in #681.
