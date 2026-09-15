## PR Reference Analysis

### Summary

The branch replaced independent video-pipeline timeouts with one durable 5100-second stage budget. It bounded recorder fan-in, fallback, rendering, archival, provider mutation/readback, queue disposition, and cleanup while preserving provider-state and public-verification safeguards.

### Changes by Significance

#### Shared lifecycle and cancellation

- Added `podcaster/video/budget.py` with immutable stage cutoffs, conservative monotonic/UTC redelivery projection, per-clip admission, provider reserve checks, and bounded timing evidence.
- Added `podcaster/video/process.py` with owned process/callable execution, terminate/kill/reap behavior, partial-output cleanup, SHA-256/size/probe evidence, and validation records.
- Routed browser, ffmpeg, ffprobe, storage, provider, queue, lease, and cleanup work through stage-aware admission and actual cancellation boundaries.

#### Recorder convergence and replay

- Persisted immutable clipsets, durable first recorder admission, explicit attempt outcomes, content-addressed media, and terminal manifest CAS.
- Stopped fan-in at T+1200 and produced deterministic browser/network-free fallback by T+1500, or failed closed as `recording_insufficient`.
- Added validated replay for audio, clips, normalized segments, composed video, and archive artifacts; legacy, corrupt, equal-size-altered, zero-byte, and unprobeable artifacts were rejected.

#### Render and provider boundary

- Persisted a verified `rendered_pending_distribution` record before provider intent and revalidated it on redelivery.
- Required at least 1800 seconds of conservative reserve and elapsed time before T+4500 at every YouTube, playlist, RSS, and Spotify mutation/retry.
- Preserved canonical identity, approval/privacy/quota/resumable upload, Spotify draft/live/pagination/protected-ID, RSS CAS/exactly-once, ambiguity/no-repeat, and externally verified public-state semantics.
- Ensured large YouTube uploads used one resumable session and retained prior-mutation ambiguity across later admission denial.

#### Shutdown, infrastructure, and operations

- Persisted terminal evidence and released the owned lease before bounded queue disposition and optional cleanup.
- Kept the ACA editor timeout at 5400 seconds while enforcing the application deadline at 5100 seconds; reduced recorder replica/visibility defaults to preserve the parent window.
- Updated the deployment runbook, scale-out RFC, Compose fanout integration, Bicep modules, and RPI evidence.

#### Validation

- Final full suite: 3141 passed, 2 skipped, 2 deselected, with one pre-existing httpx warning.
- Ruff lint/format, compileall, Bicep builds, CI-equivalent Checkov (34 passed, 0 failed), dependency-lock assertion, diff integrity, and real Azurite/Compose fanout integration passed.
- Independent lockout review accepted P01-P04 and P05-T01 with no open defects after separate implementers resolved every rejected finding.

### Issue References

- Related to #552.
- Follow-up: #681.

### Verification Notes

- The public PR content omits the W38 production job identifier and provider state.
- No merge, deployment, provider mutation, or production-state validation was performed.
- The separate distribution worker remains intentionally deferred to #681.
