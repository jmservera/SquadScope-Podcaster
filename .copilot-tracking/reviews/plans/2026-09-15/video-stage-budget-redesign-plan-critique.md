<!-- markdownlint-disable-file -->
# RPI Plan Critique: Video Stage Budget Redesign

## Metadata

* Task ID: video-stage-budget-redesign
* Critique date: 2026-09-15
* Plan: .copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md
* Phase details: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md
* Critique execution status: Complete

## Inputs and Criterion Boundary

* Task context and caller requirements: W38 video-pipeline stage-budget redesign with exact 300/1200/1500/3300/3600/4500/4920/5100 cutoffs; mutation admission strictly before 4500 and with at least 1800 seconds remaining; earliest clip abandonment at 720 seconds, two failed recorder executions, browser deadline, or T+20; one shared monotonic budget; deterministic browser-free fallback; process-tree cancellation; size/SHA-256/probe resume; durable `rendered_pending_distribution`; preservation of every baseline provider/public/queue/security safeguard; locked test ownership and file limits; infra 5400/5100; documentation, review lockout, follow-up issue, commit, push, and unmerged PR; no W38 production action, merge, or deploy.
* Research and evidence considered: .copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md and all seven delegated artifacts under .copilot-tracking/research/subagents/2026-09-15/.
* Decisions, dependencies, and acceptance criteria considered: selected shared-budget/conservative-projection design, in-process distribution with deferred worker, P01-P05 dependencies, all plan acceptance criteria, locked semantic/regression ownership, maximum two new Python test files, and delivery restrictions.
* Assessment boundary: This critique assesses planning credibility from the supplied plan, details, caller requirements, and repository-grounded research only. It does not re-research source code, validate runtime behavior, or authorize production/provider operations.

## Coverage Assessment

| Requirement, research, phase, or task ID | Coverage | Evidence or concern |
|---|---|---|
| Exact stage deadlines; P01-T01, P03-T01, P03-T03, P04-T02, P04-T03 | Partial | All constants are named, but mutation admission is not reconciled with the 5100-second parent deadline and archive-through-3600 schedule; T+4920 completion semantics are also weaker in validation than in the requirement. |
| Earliest clip abandonment; P02-T01; C11, C16-C17 | Partial | Two failed executions and T+20 are defined, but the 720-second origin and the browser-deadline formula/authority are not. |
| Shared monotonic deadline through every stage; P01-T01-P01-T02 | Covered | Local monotonic enforcement, durable UTC projection, earlier-of remaining time, and additive compatibility are planned. Production entry must still make the budget mandatory, as required by the acceptance criteria. |
| Deterministic browser-free fallback; P02-T02; C6, C18 | Partial | Browser exclusion, hash/probe binding, and race tests are present, but deterministic font/asset packaging is left conditional. |
| Process-tree cancellation; P01-T02, P03-T01; C4, C12 | Partial | FFmpeg-style owned process groups are planned, while Playwright/Chromium descendants may remain only cooperatively bounded or documented as a limitation. |
| Size+checksum+probe resume; P03-T02-P03-T03; C5, C13, C19 | Covered | Identity, schema, size, SHA-256, bounded probe, corruption, legacy cache-miss, and replay behavior are substantively specified. |
| Durable `rendered_pending_distribution`; P04-T01; C7, C14-C15, C20 | Covered | CAS persistence, verified artifact identity, pre-provider ordering, merge preservation, crash, and replay checks are planned. |
| Provider/public/queue/security safeguards; P04-T02-P04-T03 | Covered | Canonical identity, evidence/no-repeat, ambiguity, YouTube, Spotify, RSS, notification, public verification, lease, queue, poison, SSRF, and compatibility are retained, subject to the executable test-matrix concern below. |
| Mandatory tests and locked ownership; P05-T01-P05-T02 | Partial | Categories and commands are listed, but there is no exact scenario-to-owner-to-test-target matrix proving complete ownership without exceeding the two-new-file limit. |
| Infra, docs, review lockout, issue, commit/push/PR, prohibitions; P05 | Partial | Infra, docs, lockout, issue, PR, and no-operation/no-merge/no-deploy controls are present; the commit task hardcodes a stale session trailer. |

## Verdict

* Verdict: Revise
* Rationale: The plan has credible architecture, evidence traceability, and safeguard coverage, but it is not yet implementation-safe. The mutation window conflicts with the shared 5100-second budget and archive schedule, recorder/browser abandonment is not fully defined, Chromium process-tree cancellation remains optional, and the T+4920/test/delivery contracts need exact correction. All findings are direct planner corrections under already-confirmed caller requirements; no divergent user decision is needed.

## Findings

<!-- rpi:critique id=PC-001 -->
### PC-001 [Critical]: Reconcile the effective provider-mutation deadline with the archive schedule

* Related IDs: Caller deadlines and mutation rule; P01-T01, P03-T03, P04-T01, P04-T02; C14, C17, C20.
* Evidence: .copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md; .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md P03-T03 and P04-T02.
* Concern: With one application deadline at T+5100, at least 1800 seconds remaining makes T+3300 the effective latest mutation admission (`5100 - 1800`), although mutation is also required to be strictly before T+4500. The plan permits verified archive/readback to run through T+3600 and requires the durable render boundary before provider work. Its proposed 4499/4500 and 1801/1800/1799 tests treat conditions independently and do not define the integrated outcome when archive completes after T+3300.
* Impact: An implementation could incorrectly admit mutation after T+3300, violating the reserve, or assume the T+4500 cutoff is reachable. Conversely, a correct implementation may deny all provider work for renders archived between T+3300 and T+3600 without a planned durable pending disposition.
* Smallest useful change: State that admission uses `min(strict T+4500, parent remaining >=1800)`, which under the 5100 budget means admission at or before T+3300 when the conservative remaining value is exactly 1800 or greater. Require archive completion and `rendered_pending_distribution` persistence before that check; if completion is later, persist pending distribution and perform no mutation. Add integrated boundary tests around T+3300 and late archive completion, while retaining T+4500 as an independent absolute fail-closed guard.
* Action owner: Planning parent.
* Exact resolving evidence: Revised P03-T03/P04-T01/P04-T02 details and acceptance criteria explicitly state the effective T+3300 window and late-archive pending-only transition; the mandatory matrix includes 3299/3300/3300+ conservative-clock cases, archive completion at 3300/3301/3600, and proof that no provider intent or mutation occurs after denial.
* Decision route: Direct planner correction; no user decision required.

<!-- rpi:critique id=PC-002 -->
### PC-002 [High]: Define the authoritative browser deadline and 720-second abandonment origin

* Related IDs: Caller earliest-abandonment rule; P01-T01, P02-T01; C3, C11, C16-C17.
* Evidence: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md P01-T01 and P02-T01; .copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-recorder-deeper.md.
* Concern: P02-T01 names a “720-second clip wall time,” “browser deadline,” local hard deadline, and persisted executions, but it does not specify when the 720 seconds starts, whether it spans redeliveries, how the browser deadline is computed, or which durable/local bound wins. The research explicitly identified these as absent current semantics.
* Impact: Recorder, editor, and browser code could implement different clocks or reset the 720-second allowance on redelivery, defeating the earliest-of rule and making boundary tests non-authoritative.
* Smallest useful change: Define one durable per-clip first-admission/start fact plus local monotonic enforcement; define the browser deadline formula and its safety reserve relative to recorder ACA/visibility and T+20; require the effective abandonment deadline to be the earliest conservative local/durable value across 720 seconds, two failed executions, browser deadline, and T+1200.
* Action owner: Planning parent.
* Exact resolving evidence: Revised P01-T01/P02-T01 state schema and pseudocode define clock origins, durable fields, redelivery behavior, browser-deadline calculation, failure counting, and precedence; fake-clock tests cover exact equality, skew, delayed delivery, two failures, and every pairwise/racing earliest condition.
* Decision route: Direct planner correction; no user decision required.

<!-- rpi:critique id=PC-003 -->
### PC-003 [High]: Make Chromium process-tree termination a deliverable rather than a documented limitation

* Related IDs: Caller process-tree cancellation requirement; P01-T02, P03-T01; C4, C12.
* Evidence: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md P01-T02 and P03-T01; .copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-contrarian.md.
* Concern: P01-T02 permits blocking Playwright behavior merely to be “called out,” and P03-T01 excludes claiming externally managed Playwright descendants are killed unless tests prove ownership. The caller requires process-tree cancellation, and the research found cooperative Playwright cleanup insufficient for blocking calls or Chromium descendants.
* Impact: A browser hang can survive the stage cutoff, consume the render/provider/shutdown reserves, and force ACA hard kill—the exact control-flow failure the redesign must prevent.
* Smallest useful change: Require browser work that can block beyond its operation deadline to run behind an owned, cancellable process boundary or another proved mechanism that terminates and reaps Chromium descendants. Preserve bounded Playwright API timeouts as the first layer, but do not accept documentation-only residual behavior for the hard deadline.
* Action owner: Planning parent.
* Exact resolving evidence: Revised P01-T02/P03-T01 name the ownership/isolation mechanism and shutdown escalation; tests launch a controlled browser-like child/grandchild tree, force a blocked operation, and prove typed timeout, bounded terminate/kill/reap, no surviving descendants, no partial checkpoint, and completion before the parent stage deadline.
* Decision route: Direct planner correction; no user decision required.

<!-- rpi:critique id=PC-004 -->
### PC-004 [High]: Require readback and evidence operations to finish or be cancelled by T+4920

* Related IDs: Caller T+4920 readback/evidence deadline and T+5100 shutdown; P04-T03; C8, C10, C14.
* Evidence: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md P04-T03.
* Concern: The task intent says readback/evidence stops by 4920, but its validation only asserts that no readback/evidence begins after 4920. An operation started shortly before T+4920 could continue into or beyond the 180-second shutdown reserve.
* Impact: Lease, heartbeat, evidence finalization, queue disposition, and process cleanup may not complete by T+5100, making ACA hard termination normal rather than exceptional.
* Smallest useful change: Make T+4920 an operation deadline, not only an admission cutoff. Clamp every readback/evidence operation to the remaining T+4920 budget, cancel it at expiry, persist the correct unknown/pending disposition with bounded safety writes, and reserve T+4920-T+5100 solely for shutdown/disposition.
* Action owner: Planning parent.
* Exact resolving evidence: Revised P04-T03 acceptance criteria and fake-clock/hanging-transport tests prove an operation begun before T+4920 is cancelled by T+4920, no provider/readback work continues afterward, and lease/heartbeat/queue/evidence shutdown completes by T+5100.
* Decision route: Direct planner correction; no user decision required.

<!-- rpi:critique id=PC-005 -->
### PC-005 [Medium]: Lock an executable scenario-to-owner-to-test-target matrix

* Related IDs: Caller mandatory matrix and locked test ownership; P05-T01, P05-T02.
* Evidence: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md P05-T01 and P05-T02.
* Concern: The plan lists semantic and regression categories and candidate files, but it does not enumerate each required scenario with its owning suite/file, focused command, and reviewer evidence. “All named tests” is not actionable because the supplied requirements name behaviors rather than existing test names.
* Impact: Critical boundary, race, cancellation, resume, provider, shutdown, queue, SSRF, lease, API, or infra behavior can be omitted or duplicated, and implementation may exceed the maximum two new Python test files without an early ownership check.
* Smallest useful change: Add a mandatory matrix assigning every caller scenario to semantic or regression ownership, an existing test file or one of the two allowed new helper files, the focused command, and the P05-T02 reviewer checklist row. Record exact removals as none.
* Action owner: Planning parent.
* Exact resolving evidence: Revised P05-T01 contains a complete row-per-scenario matrix covering deadlines, inequalities, clock skew, retries, races, process cancellation, fallback determinism, corrupt/equal-size resume, provider ambiguity/readback/public semantics, shutdown, queue/SSRF/lease/API regressions, infra 5400/5100, and all repository validation; the matrix names no more than `tests/test_video_budget.py` and one process/helper file as new Python tests.
* Decision route: Direct planner correction; no user decision required.

<!-- rpi:critique id=PC-006 -->
### PC-006 [Medium]: Make fallback determinism independent of optional host fonts and assets

* Related IDs: Caller deterministic browser-free fallback; P02-T02; C6, C18-C19.
* Evidence: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md P02-T02; .copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-media-deeper.md.
* Concern: P02-T02 says to use repository fonts/assets “where available.” Research identifies the current EDL fallback's host font path as a compatibility risk. Conditional host assets cannot support the plan's hash-stable identical-input guarantee across development, tests, and the container.
* Impact: Fallback may fail, render differently, or produce different hashes across environments, weakening immutable terminal manifests and replay.
* Smallest useful change: Require a repository/container-owned deterministic asset contract, fixed rendering inputs/metadata, and fail-closed `recording_insufficient` behavior when the required local renderer or asset is unavailable; never fall back to Chromium/network.
* Action owner: Planning parent.
* Exact resolving evidence: Revised P02-T02 names the packaged font/asset and deterministic ffmpeg inputs; unit/container tests prove no Playwright/network use, stable bytes/hash for identical inputs, ffprobe-decodable output, and safe `recording_insufficient` for missing asset or renderer timeout.
* Decision route: Direct planner correction; no user decision required.

<!-- rpi:critique id=PC-007 -->
### PC-007 [Medium]: Remove the stale hardcoded Copilot session trailer from delivery instructions

* Related IDs: P05-T03; commit/push/PR delivery requirement.
* Evidence: .copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md P05-T03.
* Concern: P05-T03 hardcodes `Copilot-Session: d2b30d61-1562-4590-b298-16e187772282`, which is not the active delivery session and conflicts with session-specific commit-trailer requirements.
* Impact: The final commit can carry false provenance and fail the requested auditable delivery contract.
* Smallest useful change: Replace the literal session ID with an instruction to use the active implementation session's required trailer at commit time.
* Action owner: Planning parent.
* Exact resolving evidence: Revised P05-T03 contains no stale UUID and the delivery checklist verifies the actual commit message includes the active required `Co-authored-by` and `Copilot-Session` trailers before push/PR.
* Decision route: Direct planner correction; no user decision required.

## Strengths and Residual Risk

* The plan correctly selects one shared budget with conservative cross-process projection, immutable hash/probe-bound terminal media, strict checkpoint validation, a merge-safe rendered boundary, and in-process provider safeguards rather than prematurely introducing a distribution worker.
* The plan explicitly preserves W38 lockout, provider ambiguity/no-repeat/public semantics, queue/lease/poison/SSRF behavior, infrastructure headroom, full validation, independent review lockout, and unmerged delivery.
* Residual implementation risk remains in blocking Azure SDK behavior and real browser/process ownership; after PC-003 and PC-004 are corrected, this risk is testable rather than an accepted planning exception.

## Questions or Blocking Evidence Gaps

* None. The supplied caller requirements resolve the required direction for every finding.

## Limitations

* This critique used only the supplied planning and research artifacts. It did not independently inspect current source, execute tests, validate Azure/Playwright runtime behavior, or assess an implementation diff.

## Recommended Next Action

* Highest-impact finding: PC-001
* Action owner: Planning parent
* Smallest next action: Revise P03-T03, P04-T01, and P04-T02 first to define the effective T+3300 provider-admission window and pending-only late-archive outcome, then apply PC-002-PC-007 without another serialized critique pass.
* User response required: No

## Artifacts

| Artifact | Description |
|---|---|
| [.copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md](.copilot-tracking/plans/2026-09-15/video-stage-budget-redesign-plan.md) | Read-only plan assessed by this critique. |
| [.copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md](.copilot-tracking/details/2026-09-15/video-stage-budget-redesign-phase-details.md) | Read-only phase/task details assessed by this critique. |
| [.copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md](.copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md) | Primary supplied research synthesis and C1-C20 evidence. |
| [.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-recorder-wider.md](.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-recorder-wider.md) | Recorder/editor wider evidence lane. |
| [.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-recorder-deeper.md](.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-recorder-deeper.md) | Recorder deadline/state/test deeper evidence lane. |
| [.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-media-wider.md](.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-media-wider.md) | Media/process/checkpoint wider evidence lane. |
| [.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-media-deeper.md](.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-media-deeper.md) | Media/process/checkpoint deeper evidence lane. |
| [.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-distribution-wider.md](.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-distribution-wider.md) | Distribution/provider/infra wider evidence lane. |
| [.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-distribution-deeper.md](.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-distribution-deeper.md) | Distribution/provider/infra deeper evidence lane. |
| [.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-contrarian.md](.copilot-tracking/research/subagents/2026-09-15/video-stage-budget-redesign-contrarian.md) | Contrarian evidence and resolving-test obligations. |
| [.copilot-tracking/reviews/plans/2026-09-15/video-stage-budget-redesign-plan-critique.md](.copilot-tracking/reviews/plans/2026-09-15/video-stage-budget-redesign-plan-critique.md) | This complete critique and actionable finding set. |

## Next Steps

The active planning parent should revise the plan/details for PC-001-PC-007 directly, then continue the automatic RPI lifecycle after its normal planning gate. No user action or decision is required.
