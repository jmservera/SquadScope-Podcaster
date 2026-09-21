# fix(distribution): provider terminal truth and outbox remediation

> [!WARNING]
> **BLOCKED — independent review outcome: Not accepted.** This draft exposes the implementation and evidence for revision. It is not merge-ready, does not resolve any RV finding, and does not close or supersede `jmservera/SquadScope-Podcaster#682`.

## Incident evidence

This work addresses a production evidence gap where request acceptance and internal green status did not prove public provider delivery:

- W39 had no upstream dispatch.
- W38 recovery was accepted and synthesis succeeded, while the video application reported `processed=1 completed=0 failed=1` and the ACA execution surfaced `Succeeded`.
- The initial YouTube `public` configuration was incompatible with the safe lifecycle.
- Spotify returned an ambiguous 500-class outcome that required fail-closed manual handoff.
- Later evidence showed an unlisted YouTube item and a Spotify draft rather than externally verified public delivery.
- GitHub/API acceptance is treated only as handoff acceptance. Terminal success requires authoritative external provider readback.

## Implemented scope and remaining gates

The branch implements the local P01-P04 scope:

- **P01:** versioned sanitized outbox state, immutable artifact correlation, recoverable enqueue, fenced claims, consumed mutation intents, reconciliation scheduling, and disabled-by-default routing.
- **P02:** draft-first YouTube upload/processing/promotion/readback, fail-closed Spotify reconciliation/manual handoff, and durable provider intent/receipt/verification evidence.
- **P03:** truthful worker and ACA exit semantics, bounded one-item execution and cleanup, low-cardinality telemetry, alert infrastructure, and the terminal-truth runbook.
- **P04:** focused crash, concurrency, provider ambiguity, process-exit, infrastructure, and lifecycle validation.

The implementation remains **Partial**. P05 delivery/merge/provenance/canary work and P06 four-consecutive-week production verification remain externally gated and incomplete.

## Independent review: Not accepted

Independent review found **0 Critical / 4 High / 2 Medium** issues:

- **RV-001 (High, `rpi-implement`):** make read-only takeover after ambiguous YouTube promotion converge through bounded readback and durable public, pending, unknown, or manual evidence.
- **RV-002 (High, `rpi-implement`):** add durable notification deduplication and paginated/fair reconciliation scanning.
- **RV-003 (High, `rpi-implement`):** implement the documented warning/critical thresholds, windows, missing-data behavior, and explicit alert routing.
- **RV-004 (Medium, `rpi-implement`):** add bounded orphan artifact discovery, retention, repair, and cleanup.
- **RV-005 (Medium, `rpi-implement`):** perform genuine identity-bound YouTube reconciliation or record accurately named unprovable unknown/manual evidence.
- **RV-006 (High, `rpi-plan`):** reconcile the six currently unresolved `jmservera/SquadScope-Podcaster#682` review threads into the closure matrix and P05-T03 gate.

Under the reviewer rejection protocol, the original implementer is locked out of the next revision cycle. A different agent must own all revisions. This delivery does not implement or disposition any RV finding.

## Validation evidence

Author-recorded implementation validation:

- Targeted implementation contract: `571 passed in 53.16s`.
- Expanded semantic/lifecycle suite: `949 passed, 1 skipped, 2 deselected in 58.34s`.
- Full repository suite: `3059 passed, 2 skipped, 2 deselected, 1 warning in 80.91s`.
- Scale-out fanout regression after rebuilding the stale Compose image: `1 passed in 14.29s`.
- `python3 -m compileall -q podcaster`: passed.
- `ruff check podcaster tests`: passed.
- `ruff format --check podcaster tests`: 190 files already formatted.
- `az bicep build --file infra/main.bicep --stdout >/dev/null`: passed with the existing BCP318 warning.
- Exact unskipped Checkov command retained the seven documented pre-existing findings; the repository-standard skip-list gate reported `34 passed, 0 failed`.
- Container build produced `sha256:02bb1d7b7852cdb748125bbafe3d9572e8732c45fb75e26f39d22b273c62b2a9`; the rebuilt Compose integration image was `sha256:6087ecd924d9f0a8972f062430892753089807e3acc625e1f68344d4f83b77dc`.
- Distribution-worker container smoke without required queue configuration exited `2`, not success.
- `git diff --check` passed.

Independent-review validation:

- Focused outbox/provider/telemetry/exit checks: `29 passed in 0.59s`.
- Direct promotion-takeover fault reproduction raised `StaleClaimError: takeover claim is reconciliation-only` before durable convergence, supporting RV-001.
- Full repository suite: `3059 passed, 2 skipped, 2 deselected, 1 warning in 103.24s`.
- Compile, Ruff check/format, diff safety, and Bicep build passed.
- Exact Checkov baseline: `36 passed, 7 failed`; the failures matched the documented pre-existing ACR/storage/OpenAI baseline, and no gate was weakened.
- The independently inspected image digest matched `sha256:02bb1d7b7852cdb748125bbafe3d9572e8732c45fb75e26f39d22b273c62b2a9`.

## Deployment, canary, rollback, and production verification

Do not deploy this draft.

After all RV findings are addressed by a different agent and delivery gates pass, the plan requires:

1. Merge only the independently reviewed final SHA, then prove the release image digest derives from the merge SHA.
2. Deploy that exact image with `DISTRIBUTION_OUTBOX_ENABLED` disabled and with the dedicated queue, worker, scheduler, and alert rules active.
3. Enable one controlled outbox item and record sanitized outbox/execution/fence evidence, consumed intents, receipts, YouTube processing/privacy readback, Spotify expected-item readback after automation or manual handoff, ACA exit, and alert fire/clear behavior.
4. Roll back immediately on duplicate mutation, stale fenced write, missing receipt, unbounded reconciliation, or success without public readback. Rollback disables new routing and claims while preserving outbox records, consumed intents, receipts, provider identifiers, and reconciliation schedules.
5. Record four consecutive production weeks tied to the deployed merge SHA and image digest. Each week must independently prove provider public state through external readback; internal CI, API, workflow, queue, or ACA green status is insufficient. A failed or unprovable week restarts the consecutive count unless an explicit alternative is approved.

## Related work

- jmservera/SquadScope-Coordinator#17
- jmservera/SquadScope-Podcaster#671
- jmservera/SquadScope-Podcaster#678
- jmservera/SquadScope-Podcaster#679
- jmservera/SquadScope-Podcaster#681
- jmservera/SquadScope-Podcaster#682
- Upstream dispatch/terminal-reconciliation PR: jmservera/SquadScope#770

This PR is intended to become the safe replacement path for `jmservera/SquadScope-Podcaster#682`, but it does **not** yet supersede or close it because the review findings remain open. Do not resolve its review threads or close it from this draft. Supersession and closure occur only after RV-001 through RV-006 are addressed and the delivery gates pass.

## Security and idempotency

No safety, test, infrastructure, or security gate was weakened. Provider mutations remain at-most-once per consumed intent. Ambiguous mutation outcomes remain fail-closed and reconciliation-only or manual-handoff; they are never treated as permission for blind retry. Durable state excludes credentials, tokens, request/response bodies, URLs, and PII.

## Delivery checklist

- [x] Branch content and staged delta reviewed for suspected secrets/PII.
- [x] Local P01-P04 implementation and validation evidence recorded.
- [x] Independent review outcome and RV-001 through RV-006 routing recorded.
- [x] Upstream SquadScope metadata link identified as `jmservera/SquadScope#770`.
- [ ] RV-001 through RV-005 implemented by a different agent and revalidated.
- [ ] RV-006 incorporated by a different planning owner.
- [ ] Current `jmservera/SquadScope-Podcaster#682` review threads explicitly dispositioned without premature resolution.
- [ ] Required hosted checks pass on the final revision SHA.
- [ ] P05 final-SHA review, approval, merge, image provenance, canary, and rollback gates pass.
- [ ] P06 records four consecutive weeks of authoritative external-provider readback.
- [ ] `jmservera/SquadScope-Podcaster#682` is superseded/closed only after all review and delivery gates pass.

## Artifacts

- Research: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`
- Plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
- Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
- Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`
- Changes record: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
- Independent review: `.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md`
