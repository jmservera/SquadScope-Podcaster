# fix(distribution): provider terminal truth and outbox remediation

> [!WARNING]
> **BLOCKED — independent review outcome: Not accepted.** Review execution is **Complete**, implementation is **Partial**, and this draft is not merge-ready or accepted. It does not close or supersede `jmservera/SquadScope-Podcaster#682`.

## Incident summary and motivation

The active missed-publication incident is W39. Its upstream dispatch was blocked before Azure, so there is no W39 synthesis, recorder, video, outbox, or provider execution. The primary incident remediation is therefore W39-class upstream dispatch intent/evidence and missing-arrival detection, correlated end to end through Podcaster acceptance, Azure execution, and authoritative external-provider readback.

W38 was successfully published. A W38 attempt or provider path may have failed or been partial, but W38 is not a missed or unpublished week. It is used only as comparative evidence for partial attempts, retries, reconciliation, recovery behavior, and truthful observability.

Provider lifecycle, outbox, fencing, reconciliation, and truthful exit behavior remain independently required production hardening. They make retries safer and downstream status truthful after Azure arrival, but they are not presented as the root-cause fix for W39's pre-Azure dispatch blockage.

## Implemented scope and residual delivery work

The branch contains the Podcaster-side P00 receipt/absence evidence plus the local P01-P04 implementation:

- **P00 Podcaster boundary:** authenticated arrival and acceptance evidence, correlation identifiers, and absence diagnostics for the upstream-to-Azure handoff.
- **P01 outbox lifecycle:** versioned sanitized state, immutable artifact correlation, recoverable enqueue, fenced claims, consumed mutation intents, reconciliation scheduling, notification deduplication, and reference-safe cleanup.
- **P02 provider lifecycle:** draft-first YouTube processing/promotion/readback, fail-closed Spotify reconciliation/manual handoff, and durable provider intent, receipt, and verification evidence.
- **P03 truthful execution:** worker and ACA exit semantics, bounded execution and cleanup, telemetry, alert infrastructure, and the terminal-truth runbook.
- **P04 verification:** crash, concurrency, provider ambiguity, process-exit, infrastructure, and lifecycle coverage.

Implementation remains **Partial**. Residual external work is:

- **P00-T01:** implement and verify the exact upstream dispatch prevention in `jmservera/SquadScope`.
- **P05:** complete delivery, final-SHA review, provenance, deployment, canary, and rollback gates.
- **P06:** verify four future consecutive scheduled weeks end to end.

## Independent review: Not accepted

The completed independent review reports **0 Critical; 2 High open; 2 Medium open**.

Resolved findings:

- **RV-001 (High, resolved):** read-only YouTube promotion takeover now converges through authoritative readback or durable fail-closed evidence without issuing a duplicate mutation.
- **RV-005 (Medium, resolved):** ambiguous upload evidence no longer claims identity-bound readback when provider identity is unavailable.
- **RV-006 (High, resolved at planning level):** the `jmservera/SquadScope-Podcaster#682` closure inventory was expanded in the plan. Execution remains part of P05 and no GitHub thread is treated as resolved by this PR update.

Open findings:

- **RV-002 (High):** scheduler fairness stops at the fixed 5,000-path storage enumeration window, so later records can starve.
- **RV-003 (High):** warning/critical alert routing and missing-data behavior remain descriptive rather than fully deployable contracts.
- **RV-004 (Medium):** orphan cleanup is reference-safe within the enumerated set but can stop making progress at scale.
- **RV-007 (Medium):** stale artifact and PR handoff narrative remains an open consistency finding.

This PR body update addresses the **PR narrative portion** of RV-007 after review. It does not modify the review record, change the **Not accepted** verdict, claim RV-007 fully resolved, or claim acceptance.

## Validation evidence

Independent review recorded:

- Targeted correction suite: **741 passed**.
- Full repository suite: **3070 passed, 3 skipped, 2 deselected**; the scale-out integration skipped because Docker Compose startup was unavailable in the review environment.
- Ruff check and format, Python compile, Bicep build, and diff safety checks: **passed**.
- Exact Checkov baseline: **36 passed, 7 failed**, matching the documented pre-existing baseline.
- Repository Checkov wrapper: **34 passed, 0 failed**.
- Required container digest: `sha256:ac60e9065a3ccbdd77f26253b88bb61ae610926424878f2d92eb0a7372fe6b1d`.
- Distribution-worker smoke without required queue configuration: **exit 2**, not success.

No validation gate was weakened. Compose unavailability is reported as an environment variance rather than converted into a passing integration result.

## Canary, rollback, and production verification

Do not deploy or merge this blocked draft.

After the open findings and P05 gates are completed, the canary must prove this exact chain:

1. **Upstream dispatch intent and dispatch evidence** at the scheduled weekly boundary.
2. **Podcaster authenticated arrival and acceptance** correlated to that upstream intent.
3. **Enqueue and execution correlation** across the durable outbox and Azure workload.
4. **Azure terminal result** with truthful process and workload exit semantics.
5. **Authoritative external-provider readback** for the expected YouTube and Spotify publication state.

The canary must also exercise a W38-inspired partial-attempt/retry path as an observability and recovery comparison. That comparison is not missed-week recovery and must not describe W38 as unpublished.

Rollback is required on duplicate provider mutation, stale fenced writes, missing durable receipt, unbounded reconciliation, missing upstream-to-Azure correlation, or terminal success without authoritative provider readback. Rollback disables new routing and claims while preserving outbox records, consumed intents, receipts, provider identifiers, reconciliation schedules, and correlation evidence for diagnosis.

P06 requires **four future consecutive scheduled weeks**, each with upstream dispatch evidence, Azure arrival and execution correlation, and authoritative external-provider readback. Internal CI, API, workflow, queue, or ACA green status alone is insufficient. W38 does not count as missed-week recovery or as one of these future verification weeks.

## Related work

- jmservera/SquadScope-Coordinator#17
- jmservera/SquadScope#770
- jmservera/SquadScope-Podcaster#671
- jmservera/SquadScope-Podcaster#678
- jmservera/SquadScope-Podcaster#679
- jmservera/SquadScope-Podcaster#681
- jmservera/SquadScope-Podcaster#682

`jmservera/SquadScope-Podcaster#682` remains open and is not superseded. Do not resolve its review threads or mutate that issue from this delivery.

## Security and idempotency

No safety, infrastructure, test, or security gate was weakened. Provider mutations remain at-most-once per consumed intent. Ambiguous mutation outcomes remain fail-closed and reconciliation-only or manual-handoff; they are never permission for a blind retry. Durable state excludes credentials, tokens, request/response bodies, URLs, and PII.

## Delivery checklist

- [x] Existing correction diff and worktree status inspected.
- [x] Changed content reviewed for suspected secrets and PII before staging.
- [x] W38 recorded as successfully published and comparative-only.
- [x] W39 recorded as the pre-Azure missed-publication incident with no downstream execution.
- [x] Independent review status, severity, dispositions, validation, and residual work recorded.
- [x] PR narrative portion of RV-007 corrected without changing the review verdict.
- [ ] RV-002 scheduler fairness implemented and independently verified.
- [ ] RV-003 deployable alert contract implemented and independently verified.
- [ ] RV-004 scalable orphan cleanup implemented and independently verified.
- [ ] Remaining RV-007 artifact-state inconsistency reconciled through the routed follow-up.
- [ ] P00-T01 exact upstream prevention completed in `jmservera/SquadScope`.
- [ ] P05 final-SHA review, provenance, deployment, canary, and rollback gates completed.
- [ ] P06 four-future-week verification completed.
- [ ] `jmservera/SquadScope-Podcaster#682` superseded or closed only after its own evidence and delivery gates pass.

## Artifacts

- Research: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`
- Plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
- Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
- Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`
- Changes record: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
- Independent review: `.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md`
