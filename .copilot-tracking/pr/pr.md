# fix(distribution): provider terminal truth and outbox remediation

> [!WARNING]
> **OPEN / DRAFT / BLOCKED — Rusty's independent review rejected Frank's exact head.** Fry's rejection of Leela's revision `02241a1` and Livingston's rejection of Farnsworth source candidate `601d36a` remain historical evidence. RV-008 remains High. This PR is not merge-ready, deployment-ready, canary-accepted, or production-accepted.

Current Frank source/tests/artifacts commit: `502807d562996ecf6c8cd4213afd4cdf454aa5c3`.
Exact reviewed head: `1efa74956e23b51512b7eff1ded4e809b79566e1`.
Fresh independent reviewer: Rusty, verdict **Not accepted**.

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

## Frank correction after Livingston rejection

Frank is the sole author of the current correction. Rusty is reserved for fresh independent review without source/test contribution. Bender, Hermes, Amy, Leela, Fry, Farnsworth, and Livingston did not contribute or advise.

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

Rusty's current verdict is **Not accepted**: 0 Critical, 1 High, 0 Medium, 0 Low at `1efa749`. Livingston's earlier rejection at `601d36a` remains historical evidence.

## Independent validation evidence

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

Frank's probes prove that a later `provider_unknown` blocks reuse of the older failed predecessor and that stale, omitted, and reordered history fail closed. Rusty's independent probe found the remaining gap: an unknown attempt expecting `youtube-unknown`/`spotify-unknown` accepted failed readback for `youtube-DIFFERENT-ITEM`/`spotify-DIFFERENT-ITEM`, appended a third attempt, and returned a mutation-capable claim. Latest-unknown readback must be bound to the exact provider item implicated by that attempt.

## Residual external gates

- **P00-T01 — `jmservera/SquadScope`:** implement and verify prevention of the W39-class upstream dispatch blockage.
- **RV-008 / P07-T01:** bind post-terminal readback of the latest unknown attempt to its exact persisted provider item/intent/receipt identity and obtain a new accepted final-SHA review.
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
- [ ] RV-008 exact latest-unknown provider-item readback binding corrected and accepted.
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
