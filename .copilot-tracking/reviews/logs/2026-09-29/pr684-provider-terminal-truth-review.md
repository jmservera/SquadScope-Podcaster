# RPI review: jmservera/SquadScope-Podcaster#684 provider terminal truth and outbox remediation

- Reviewer: Frank, Integration Engineer
- Date: 2026-09-29
- Scope: restack draft PR #684 after #682 squash-merge, preserve merged main behavior, address unresolved review threads, validate, and prepare for ready-for-review.
- Base reviewed: `origin/main` at `6bf44417d68636bf6ac90ae17d8205adb28b5563`
- Head reviewed: final pushed PR head recorded in the PR status and final handoff.

## Evidence reviewed

- PR body and acceptance context for #684.
- Linked follow-up issue jmservera/SquadScope-Podcaster#681; outbox worker remains follow-up and was not implemented here.
- Review threads via GraphQL `reviewThreads(first: 100)`: no unresolved review threads at the time of the final push.
- Merged-main behavior from #682 and later main commits, including disabled legacy `PODCAST_AUTO_PUBLISH` behavior and newest publish/orchestration hardening.

## Implementation review outcome

- Rebasing/cherry-picking #684-specific work onto `origin/main` completed in an isolated worktree; the main checkout was not switched.
- Conflicts resolved by preserving current `origin/main` behavior where overlap existed and retaining #684-specific provider-terminal-truth, ownership fencing, outbox remediation, budget, final-media validation, and playlist ambiguity semantics.
- Latest additional conflict in `podcaster/publish.py` preserved main's bounded local Spotify upload path guards and #684's terminal-truth/reconciliation flow.
- CI exposed that the readable-metadata corruption fixture in `tests/test_video_compose.py` was not deterministic under the hosted runner ffmpeg; the fixture now truncates inside `mdat` while preserving faststart metadata, matching the existing decode-failure contract without weakening validation.
- Copilot review round 1 was addressed by restoring current-main Spotify draft GraphQL pagination and publish tests, preserving staged media probing before final promotion, adding dispatch receipt CAS/type guards, preserving playlist ambiguous outcomes and budget admission, restoring a 300-second bounded Spotify upload window, removing deployed/triggered #681 distribution-worker infrastructure and worker module/tests from this PR, and enforcing consecutive alert evaluation periods for 10/15-minute windows.
- Production behavior changes intentionally present in #684:
  - Provider ambiguity and accepted-but-unreadable provider responses fail closed as `publication_unknown`/retry-blocked instead of being treated as success.
  - Final media is validated before promotion so invalid output cannot replace an existing destination.
  - Spotify/playlist/publication terminal evidence is persisted and blocks unsafe retries when provider state is unknown.
  - Legacy `PODCAST_AUTO_PUBLISH` remains disabled/no-op; no legacy auto-publish path was reintroduced.

## Validation

- `python3 -m pytest -q`: `4199 passed, 4 skipped, 2 deselected, 1 warning` after the Copilot review round fixes.
- `ruff check podcaster tests`: passed.
- `ruff format --check podcaster tests`: passed.
- Prior CI after the RPI evidence commit failed one deterministic compose fixture assertion and the separately tracked `tests/integration/test_scaleout_fanout.py` path. The compose fixture was fixed here; scale-out fanout remains tracked separately under jmservera/SquadScope-Podcaster#723 if it recurs.

## Review decision

Locally accepted pending GitHub CI completion and post-push Copilot review-thread check. Do not merge from this review record.
