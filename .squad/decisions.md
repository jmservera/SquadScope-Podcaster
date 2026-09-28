# Decisions

### 2026-09-22T16-44-07: Route final MP4 integrity decode through shared render runner
**By:** Bender
**What:** Route final MP4 integrity decode through shared render runner
**References:** jmservera/SquadScope-Podcaster#684, podcaster/video/video_compose.py
**Why:** For PR #684's truncation repair, the full ffmpeg decode is invoked through video_compose's existing CommandRunner instead of a standalone subprocess/timeout. This keeps the integrity pass inside the shared render stage admission, deadline, cancellation, process-tree cleanup, and bounded diagnostics model supplied by PR #682 after restack. Validation remains immediately before os.replace, so failures preserve the existing destination and staged cleanup remains in the existing finally block.

### 2026-09-22T19-09-27: Current operator assignment supersedes stale PR #684 author lockout text
**By:** Bender
**What:** Current operator assignment supersedes stale PR #684 author lockout text
**References:** jmservera/SquadScope-Podcaster#684, https://github.com/jmservera/SquadScope-Podcaster/pull/684#issuecomment-5782324730
**Why:** jmservera explicitly assigned Bender in an isolated worktree to implement the PR #684 ownership-boundary repair from exact head df473dc0c059680b9c454ddab263c5c454e2ef2b. Older tracking text naming Frank as sole author and excluding Bender is stale for this execution. Preserve the requested narrow write boundary, validate the persisted same owner/fence and fixed authoritative expiry immediately before every irreversible archive/outbox/notification/direct-provider boundary, and keep the PR draft with no merge, deploy, workflow dispatch, provider mutation, or production action.

### 2026-09-22T23-28-47: Use independent bounded cursors for scheduler notification repair and due reconciliation
**By:** Bender
**What:** Use independent bounded cursors for scheduler notification repair and due reconciliation
**References:** jmservera/SquadScope-Podcaster#684, jmservera/SquadScope-Podcaster#681, jmservera/SquadScope-Podcaster#682, podcaster/distribution_scheduler.py, podcaster/distribution_outbox.py
**Why:** For PR #684's legacy notification recovery, the deployed ACA distribution scheduler will run a 100-record durable notification-repair page before its existing bounded due-reconciliation scan, persisting a separate repair cursor beside the reconciliation cursor. Repair normalizes missing provider schedules and routes still-reserved initial notifications through the existing reserved -> sending -> accepted callbacks. Sending, legacy-consumed/ambiguous, and accepted intents are never replayed and recover only through provider reconciliation. Queue unavailability fails the scheduler run before cursor/heartbeat persistence. Existing scheduler identity and storage/queue permissions already cover these operations, so no Bicep, workflow, role, or permission changes are required.

### 2026-09-23T14:19:30.910+00:00: PR #686 Spotify reconciliation contract and revision provenance (consolidated)
**By:** Bender, Leela, Hermes, Amy, Farnsworth
**What:** Keep issue #679 reconciliation fail-closed across listing, pagination, draft reuse, and promotion. Accept only the owner-verified Spotify listing envelope and reject alternate aliases, arbitrary nesting, REST-shaped responses, malformed metadata, duplicate containers, absent or invalid show identity, ambiguous reusable candidates, and unknown overview state. Follow pagination only from explicit valid metadata, preserve bounded retries, require explicit draft evidence and a non-blank caller title before mutation, and allow blind create only through the explicit false operator escape hatch. The exact provider contract evolved during independent revisions: early live-disconfirmed envelopes were gated behind explicit enablement; owner commit `612b5b8` then became the authoritative verified WebGetIndexedEpisodeList base for Farnsworth's final revision.
**References:** jmservera/SquadScope-Podcaster#679, jmservera/SquadScope-Podcaster#686, jmservera/SquadScope-Podcaster#685, podcaster/publish.py, tests/test_publish.py, docs/spotify-video-upload.md, 612b5b8, review comments 4083840784, 4083841049, 4083927162, 4083927242
**Why:** Absence may authorize irreversible create/upload/process/metadata/publish operations, so malformed, incomplete, unauthorized, duplicated, or ambiguous readback can never count as proof of absence. HTTP 403 overview responses remain non-expiry but produce unknown state and abort promotion. Independent revisions progressively tightened schema, identity, configuration, title, candidate-count, and page-count checks while preserving the explicit operator escape hatch and reviewer lockout boundaries.

### 2026-09-23T17-11-15: Classify Spotify create failures by provider-side certainty
**By:** Bender
**What:** Classify Spotify create failures by provider-side certainty
**References:** jmservera/SquadScope-Podcaster#689, podcaster/publish.py, tests/test_publish.py
**Why:** For PR #689 remediation, preserve retry blocking when the ordinary publish path has crossed the create boundary without provider identity, but append a latest retryable evidence record when Spotify explicitly rejects the create before mutation (credential expiry, and deterministic video-create rejection per _create_episode contract). Explicit retry_blocked details override outcome-derived defaults. Ambiguous create failures and any resolved/reconciled provider ID remain retry-blocking.

### 2026-09-23T17-27-50: Use atomic re-armable publication mutation claims
**By:** Leela
**What:** Use atomic re-armable publication mutation claims
**References:** jmservera/SquadScope-Podcaster#689
**Why:** For PR #689 revision, mutation intents now use a storage.update_bytes-backed claim operation. A historical upload_intent or create_episode_intent may be appended again only when later non-intent evidence for the same canonical publication identity, platform, and media_kind explicitly has retry_blocked=false. Appending the new claim consumes that authorization atomically, so a concurrent contender sees the new claim and is denied. Retry-blocking provider evidence and unresolved intents continue to prevent duplicate provider creation.
