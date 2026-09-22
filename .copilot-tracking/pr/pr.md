# fix(distribution): provider terminal truth and outbox remediation

> [!WARNING]
> **OPEN / DRAFT / BLOCKED — Ralph independently rejected final head `fcfa40015ed68d9e38d8432425b7cbd15171e835`; RV-008 remains High.** Fry's rejection of Leela's revision `02241a1`, Livingston's rejection of Farnsworth source candidate `601d36a`, and Rusty's rejection of Frank's `1efa749` remain historical evidence. This PR is not merge-ready, deployment-ready, canary-accepted, or production-accepted.

Basher revision base: `97c9520b7c365b078a50df113154bb1e66b1ecbb`.
Current source/tests/artifacts commit: `eaaac5706985d0df4058f46d25e4aa4d9217f41e`.
Fresh independent reviewer: Ralph, completed with verdict **Not accepted**.

## Incident and acceptance truth

Attempt-level truth is immutable and distinct from weekly identity delivery. Every failed, partial, unknown, manual-action, or conflicting attempt must remain visible beneath any later weekly recovery; a later success must not overwrite attempt history.

W38 may be classified `published_verified_recovered` only if evidence binds the exact week/publication identity, manifest and publication digest, canonical artifact, authorized succeeding attempt, expected provider item, authoritative terminal provider readback, and resolved duplicate state. That proof has not been established by the current executable fixtures, so this PR does not assert W38 recovered-green.

W39 remains the incident and is `missed_not_dispatched`. It has no synthesis, recorder, video, outbox, or provider attempt because dispatch was blocked before Azure.

Green weekly success requires all of the following:

- Exact week and publication identity.
- Exact manifest/digest identity and canonical artifact.
- Expected provider item identity.
- Authoritative external readback proving terminal visibility and state.
- No unresolved duplicate ambiguity.
- Authorized recovery backed by durable proof, with no blind retry after an unknown mutation.

Four future post-fix cycles must each end `published_verified` or controlled `published_verified_recovered`. `partial`, `provider_unknown`, `manual_action_required`, `identity_conflict`, missing readback, duplicate ambiguity, or any other incomplete proof is non-green.

## Implemented scope

The branch contains the Podcaster-side P00 receipt/absence boundary and Amy's declared in-repo P01-P04 implementation:

- Durable dispatch acceptance and absence evidence with cross-boundary correlation.
- Immutable attempt history, separate weekly aggregation, fenced claims, recovery records, reconciliation scheduling, and cleanup changes.
- Provider intent, receipt, verification, readback, and fail-closed/manual-handoff behavior.
- Truthful worker/ACA exits, telemetry, alert infrastructure, runbook updates, and fault/concurrency/lifecycle coverage.

Completion of the declared implementation scope does not imply acceptance. Fry's final review resolved the alert and tracking findings but rejected `02241a1` for scheduler lease recovery, legacy cleanup safety, recovery authorization, and four-cycle identity binding.

## Basher correction after Rusty rejection

Basher is the sole author of the current correction. Ralph is reserved for fresh independent review without source/test contribution. Bender, Hermes, Amy, Leela, Fry, Farnsworth, Livingston, Frank, and Rusty did not contribute or advise.

The latest `provider_unknown` attempt now supplies recovery identity only through its immutable provider-evidence snapshot. Each provider must have a consumed, provider-matching intent with one expected item; receipts must belong to that intent, be non-ambiguous, and not conflict with the expected identity. Each provider must then have exactly one failed-terminal post-readback for that same item. Missing identity, provider-kind mismatch, stale receipt, conflicting item evidence, duplicate readback, or a different readback item fails closed.

Rusty's exact bypass using intents for `youtube-unknown`/`spotify-unknown` and failed readbacks for `youtube-DIFFERENT-ITEM`/`spotify-DIFFERENT-ITEM` can no longer create authorization or a third mutation-capable attempt. However, Ralph proved the exact-match path still accepts zero durable provider receipts and creates a mutation-capable successor.

## Ralph final-SHA rejection

Ralph independently reviewed `97c9520b7c365b078a50df113154bb1e66b1ecbb..fcfa40015ed68d9e38d8432425b7cbd15171e835`, focused on source commit `eaaac5706985d0df4058f46d25e4aa4d9217f41e`. No executable changes followed that source commit.

RV-008 remains **High**. `_recovery_predecessor_provider_evidence()` accepts `receipts=[]`; exact-item failed-terminal readback then authorizes a third attempt:

`RV008_MISSING_RECEIPT_BYPASS receipts={'youtube': 0, 'spotify': 0} attempts=3 read_only=False`

Required correction: require exactly one usable, non-ambiguous receipt bound to each consumed intent, provider kind, and expected provider item before failed-terminal readback can authorize the specifically bound successor. Missing or duplicate receipts must fail closed.

RV-002, RV-003, RV-004, RV-007, and RV-009 remain resolved. P07 is not accepted.

## Historical Frank correction and Rusty rejection

Frank was the sole author of that correction. Rusty performed its fresh independent review without source/test contribution. Bender, Hermes, Amy, Leela, Fry, Farnsworth, and Livingston did not contribute or advise.

| Finding | State | Review result |
|---|---|---|
| RV-001 | Resolved | Read-only YouTube promotion takeover converges through authoritative readback without a duplicate mutation. |
| RV-002 | Resolved | Expired `reserved`/`enqueue_started` leases become recoverable by one fenced owner; stale owners cannot release or complete the replacement. |
| RV-003 | Resolved | Emitted weekly rows, deployed rules, and active-depth absence semantics remain aligned. |
| RV-004 | Resolved | A bounded resumable migration backfills legacy references, and cleanup refuses deletion until reference completeness is proven. |
| RV-005 | Resolved | Missing provider identity remains accurately fail-closed rather than inventing identity-bound readback. |
| RV-006 | Planning-resolved | The closure inventory is present; P05 execution remains outstanding, and no issue or review thread is treated as resolved by this delivery. |
| RV-007 | Resolved | Current tracking and PR narrative preserve Fry's rejection and identify Farnsworth/Livingston as the new author/reviewer pair. |
| RV-008 | **High, open** | Complete-history/latest-attempt checks pass, but failed-terminal readback of the latest unknown attempt is not bound to that attempt's exact provider item. Readback for a different item can authorize a mutation-capable continuation. |
| RV-009 | Resolved | Every cycle recomputes proof from raw evidence and rejects label-only, tampered, mismatched, or unauthorized recovered envelopes. |

Rusty's verdict on Frank's revision remains **Not accepted**: 0 Critical, 1 High, 0 Medium, 0 Low at `1efa749`. Livingston's earlier rejection at `601d36a` remains historical evidence.

## Basher validation evidence

- Required RV-008 probes: **19 passed, 49 deselected**.
- Focused correction suite: **113 passed**.
- Locked contract suite: **788 passed, 1 warning**.
- Full repository suite: **3118 passed, 2 skipped, 2 deselected, 1 warning**.
- Python compile, Ruff check, Ruff format check, Bicep build, and diff safety: **passed**.
- Exact Checkov baseline: **36 passed, 7 failed**, matching the documented pre-existing baseline.
- CI-equivalent Checkov gate: **34 passed, 0 failed**.
- Dockerfile Checkov baseline: **passed**.
- Container image `sha256:0e4d101baaca7f4b4ebc2b84f78f103c8451aff4710ea9bb1d239897a34bdfff` passed UID `999`, ffmpeg/ffprobe, and pipeline import smoke; the unconfigured distribution worker exited `2`.
- Changed-file suspected secret/PII scan found no private key, access key, JWT, signed credential URL, or email-address pattern.

Ralph's initial full run reproduced the known stale Compose recorder image; after rebuilding the recorder, the fanout probe and full suite passed. Independent counts were focused `113`, locked `788`, and full `3118`; exact Checkov remained `36/7`, CI Checkov passed `34/0`, and review image `sha256:5baa9f5828ab055362ba8e5b25d3fae388ab5cb85c5029c335cce4d79f2ccf10` passed smoke. No validation or security gate was weakened.

## Historical independent validation evidence

- Required RV-008 probes: **17 passed, 45 deselected**.
- Focused correction suite: **107 passed**.
- Locked contract suite: **782 passed, 1 warning**.
- Full repository suite: **3112 passed, 2 skipped, 2 deselected, 1 warning**.
- Python compile, Ruff check, Ruff format check, Bicep build, and diff safety: **passed**.
- Exact Checkov baseline: **36 passed, 7 failed**, matching the documented pre-existing baseline.
- CI-equivalent Checkov gate: **34 passed, 0 failed**.

Rusty's initial full suite reproduced only a stale Compose recorder image; after rebuilding, the focused fanout test passed and the final full suite returned **3112 passed, 2 skipped, 2 deselected, 1 warning**. Review image `sha256:72257821fdc2c45de68d98857a35d8d2f72fced688c829d75dccfe651d38d37b` passed the standard non-root/dependency/import smoke, and the unconfigured distribution worker exited `2`. No validation or security gate was weakened.

## Negative probes

The focused suite proves expired reservation recovery with one fenced winner, fail-closed cleanup until legacy reference migration completes, rejection of opaque or mismatched selected-predecessor evidence, rejection of label-only/tampered/mismatched proof envelopes, and acceptance of exact complete proof only.

Frank's probes prove that a later `provider_unknown` blocks reuse of the older failed predecessor and that stale, omitted, and reordered history fail closed. Basher's probes close Rusty's remaining gap: different-item readback, missing item identity, provider-kind mismatch, stale receipt, conflicting item candidate, and duplicate post-terminal readback all fail closed; exact matching readback remains the only path to a specifically authorized successor.

## Residual external gates

- **P00-T01 — `jmservera/SquadScope`:** implement and verify prevention of the W39-class upstream dispatch blockage.
- **RV-008 / P07-T01 / P07-T07:** require exact durable provider receipts, rerun validation, and obtain a new independent final-SHA review.
- **P05 — deployment/canary:** complete final-SHA delivery review, provenance, authorized deployment, canary evidence, alert fire/clear evidence, and rollback evidence after the open findings are corrected.
- **P06 — four elapsed cycles:** record four consecutive future post-fix weekly cycles with complete upstream, Azure, immutable-attempt, weekly-aggregation, provider-identity, and authoritative external-readback evidence.

No merge, deployment, canary, four-cycle completion, or production acceptance is claimed.

## Related work

- jmservera/SquadScope-Coordinator#17
- jmservera/SquadScope#770
- jmservera/SquadScope-Podcaster#671
- jmservera/SquadScope-Podcaster#678
- jmservera/SquadScope-Podcaster#679
- jmservera/SquadScope-Podcaster#681
- jmservera/SquadScope-Podcaster#682

`jmservera/SquadScope-Podcaster#682` remains open and is not superseded. This delivery does not mutate issues, review threads, deployments, canaries, or production state.

## Security and delivery controls

The current change set and public PR text were checked for suspected secrets and PII; none were identified. Durable state is intended to exclude credentials, tokens, request/response bodies, URLs, and PII. Ambiguous provider mutation remains fail-closed and must never authorize a blind retry.

## Delivery checklist

- [x] Current status, branch diff, independent review, and existing draft PR inspected.
- [x] Public PR content checked under the content-policy citation rules; no citation was required.
- [x] Review execution and Amy's declared in-repo implementation execution recorded as Complete.
- [x] Fry's historical outcome retained as Not accepted with 0 Critical and 4 High findings open at `02241a1`.
- [x] W38 retained as evidence-conditional and W39 retained as `missed_not_dispatched`.
- [x] Positive validation and independent negative probes recorded exactly.
- [x] Existing PR retained OPEN, DRAFT, and BLOCKED.
- [x] RV-002, RV-004, and RV-009 independently resolved.
- [x] RV-003 and RV-007 preserved as resolved.
- [x] Livingston's rejection of source candidate `601d36a` preserved.
- [x] RV-008 branch-around-older-unknown recovery defect corrected and validated by Frank.
- [x] Rusty independently reviewed exact head `1efa749`; verdict Not accepted.
- [x] RV-008 different-item latest-unknown readback bypass corrected and validated by Basher.
- [x] Ralph independently reviewed final head `fcfa40015ed68d9e38d8432425b7cbd15171e835`; verdict Not accepted.
- [ ] RV-008 missing-receipt authorization corrected and independently accepted.
- [ ] P00-T01 completed in `jmservera/SquadScope`.
- [ ] P05 deployment/canary gates completed.
- [ ] P06 four future elapsed cycles proven green with authoritative external evidence.

## Artifacts

- Research: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`
- Plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
- Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
- Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`
- Changes record: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
- Independent review: `.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md`
