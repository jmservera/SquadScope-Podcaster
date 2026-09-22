<!-- markdownlint-disable-file -->
# Review: Production Provider Terminal Truth

## Scope and Evidence

* Task ID: `2026-09-21 production-provider-terminal-truth`
* Review date: 2026-09-21
* Final-revision review date: 2026-09-22
* Final revision reviewed: pending exact-head gate for `da84b6b2c50f0226b2abb3b469a01c2158d41ef3`
* Final-revision reviewer: Basher, independent of latest tracking-only author Leela; Bender, Hermes, Amy, and Leela are excluded from this review
* Review scope: Final-SHA P05 delivery gate for PR #684, including ancestry from accepted implementation head `905a890c6a2176c799a5cd5f54fbb01d4c791aa9` and executable source revision `9204e139be485cb916ccd6e70b6fce355b656136`, upstream prerequisite PR #773, exact-W39 tracking requirements, hosted checks, and PR #682 disposition evidence
* Assessed boundary: Immutable attempt truth; deterministic weekly aggregation; exact provider proof; controlled recovery; unknown-mutation safety; RV-002 scheduler fairness/deduplication; RV-003 alert deployment; RV-004 cleanup; RV-007 terminology/tracking consistency; W38/W39 fixtures; four-cycle evaluation; validation; and residual P00-T01/P05/P06 work
* Plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
* Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
* Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`
* Changes: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
* Other evidence considered: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`; `.copilot-tracking/pr/pr.md`; complete `origin/main` delta; current uncommitted author corrections; source, tests, Bicep, runbook, and independently reproduced validation below

## Opening Review State

* Interpreted review goal: Independently determine whether exact PR #684 head `da84b6b2c50f0226b2abb3b469a01c2158d41ef3` is acceptable and merge-authorizable without executing W39, deploying, or mutating provider state.
* Review scope: P00-T01 and P05-T01–P05-T03 final-SHA delivery gates, with P05-T04–P05-T06 and P06 retained as future production work.
* Evidence readiness: One unambiguous artifact set and canonical review record exist. Local and remote PR #684 heads match. Upstream PR #773 is merged at `7a6d8811bf82507cbdd0b01ba1135bc42e5942f3` from reviewed head `d75e3f5523f4810edbcaeef9a217d34cd21825a2`, with 18 successful checks. Three PR #684 checks are initially pending and PR #682 thread disposition remains to be verified.
* Acceptance basis: Required upstream prevention/detection and durable dispatch-evidence contract; no executable drift after accepted source revision; exact-W39 reconcile-before-mutate/fail-closed/correlation/safe-report/no-automatic-P06-credit requirements; all hosted checks successful; no unresolved High/Critical finding; and authoritative #682 thread disposition evidence.
* First comparison boundary: Verify commit ancestry and classify every path changed after `9204e139be485cb916ccd6e70b6fce355b656136` and `905a890c6a2176c799a5cd5f54fbb01d4c791aa9`, then compare the exact-W39 gate and GitHub delivery state.
* Active read-only boundaries: Executable source/tests, deployment/provider state, W39 execution, and issue/review-thread state are read-only. During evidence comparison this canonical review record is the only writable artifact.
* Initial blockers: PR #684 has three pending hosted checks. P05-T03 depends on complete #682 thread disposition and replacement linkage. P05-T04–P05-T06 and P06 remain future and may not execute in this review.

## P05 Basher Final-SHA Delivery Review

* Independent reviewer: Basher. Leela authored the latest tracking-only exact-W39 commit and did not contribute to this review. Bender, Hermes, and Amy remained excluded.
* Exact reviewed head: `da84b6b2c50f0226b2abb3b469a01c2158d41ef3`.
* Accepted executable boundary: `9204e139be485cb916ccd6e70b6fce355b656136`; prior accepted implementation head: `905a890c6a2176c799a5cd5f54fbb01d4c791aa9`.
* Ancestry and drift: both accepted SHAs are ancestors of `da84b6b`. Changes after `905a890` touch only `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`, `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`, `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`, and `.copilot-tracking/pr/pr.md`. No executable source or tests changed.
* Upstream prerequisite: `jmservera/SquadScope#773` reviewed head `d75e3f5523f4810edbcaeef9a217d34cd21825a2` merged as `7a6d8811bf82507cbdd0b01ba1135bc42e5942f3` at `2026-09-22T09:20:43Z`. All 18 PR checks succeeded. Merge-SHA CI, security, Checkov, lint, release, and deploy workflows also succeeded.
* P00-T01 disposition: complete. The merged upstream code uses exact canonical publication identity, append-only trusted dispatch receipts, accepted/missing/terminal monitoring, immutable prior-attempt evidence, fail-closed non-green weekly states, and externally verified provider terminal status.
* Exact-W39 gate assessment: conformant. P05-T06 is dependency-ordered after review/merge/provenance/deployment/readiness/rollback/provider-authority gates; reconciles all W39 intent, dispatch, receipt, Podcaster, Azure, immutable-attempt, and provider-candidate evidence before mutation; fails closed on existing/conflicting/unknown/incomplete state; requires one sanitized GitHub-to-provider correlation chain and authoritative terminal readback; preserves historical W39=`missed_not_dispatched`; and grants no automatic P06 credit.
* P05-T01 disposition: complete after durable #773 cross-link and PR narrative reconciliation.
* P05-T02 disposition: conditionally conformant for the reviewed executable/tracking boundary, but not complete until every hosted check on the final pushed tracking head finishes successfully.
* P05-T03 disposition: blocked. GitHub reports 67/67 #682 threads resolved, but 17 have a Copilot reviewer finding as the final comment without an author disposition, and zero final thread replies link replacement PR #684. PR #682 remains open. Resolved UI state alone is insufficient replacement evidence.
* Draft/merge posture: retain draft. #684 is not merge-authorized while P05-T02 or P05-T03 is incomplete. Do not close #682 until durable disposition/replacement links exist and #684 is otherwise accepted and merge-ready.
* Production boundary: no merge, deployment, W39 dispatch, provider mutation, canary, issue closure, or review-thread mutation occurred.

<!-- rpi:review id=RV-010 -->
### RV-010 [Medium, open]: PR #682 resolution state lacks complete durable replacement disposition

* Related scope: P05-T03.
* Evidence: GitHub GraphQL reports 67 total and 67 resolved review threads, but 17 final comments are still reviewer findings without an author disposition; no final thread reply references #684.
* Impact: The replacement relationship cannot be audited, and closing #682 or authorizing #684 merge would prematurely treat UI resolution as evidence-backed supersession.
* Destination: `rpi-research`.
* Smallest useful next action: For each of the 17 threads, record a code/test/non-port disposition and durable #684 replacement link; then verify all authoritative rows and only close #682 after #684 is accepted/merge-ready.

### Current Review Outcome

* Review execution status: Complete for exact head `da84b6b2c50f0226b2abb3b469a01c2158d41ef3`; hosted finalization remains contingent on the post-review tracking head.
* Outcome: Not accepted for merge authorization because P05-T03 has a material evidence gap. The exact executable and exact-W39 tracking boundary is otherwise conformant with no High/Critical finding.
* Severity summary: 0 Critical, 0 High, 1 Medium open (`RV-010`).
* Remaining work: P05-T03 durable dispositions; P05-T04 merge/provenance; P05-T05 deployment/readiness/rollback/provider authority; P05-T06 exact real-W39 reconciliation/execution/readback; P06 four qualifying future cycles.

## P07 Leela Fresh Independent Final-SHA Review

* Independence: Leela did not author, advise, pair on, or contribute to Fry's revision. Fry was the sole revision author. Bender, Hermes, Amy, Farnsworth, Rusty, Basher, Ralph, Livingston, and Frank did not contribute.
* Exact boundary: comparison base `d7eb7ba53b6024812a33a1abc9d2961bd3ddd1b0`; Fry source commit `7f00b5795117f144cb23615d59f92029246162fe`; exact reviewed head `273f94e0d1fa773e108661f908aca6f34be132c4`. The final commit after source changed only the changes record and PR narrative.
* Opening state: clean incident worktree; local, origin, and PR head matched; base was the merge base; divergence was `0/0`; PR #684 was open, draft, mergeable/CLEAN, with 13 successful checks, no submitted reviews, and zero review threads.
* Verdict: **Not accepted (`request_changes`)** — 0 Critical, 1 High, 0 Medium, 0 Low.
* Resolved behavior: Fry's six authorization-envelope/cardinality bypasses fail closed. Every v4 envelope field removal and tested type/null mutation fails closed; legacy/unknown versions, non-empty extensions, duplicate/unrelated/conflicting records, reordered/missing records, orphan/cyclic supersession links, two-active chains, set field/value mutations, attempt/history mutation, timestamp spelling changes, and unknown nested JSON values fail closed. Exactly one final active authorization is selected for the exact predecessor/successor boundary. Full nested evidence and complete history binding remain intact. Concurrent authorization has one winner, concurrent claim has one mutation-capable winner, predecessors remain immutable, and replay is read-only.

<!-- rpi:review-leela id=RV-008 -->
### RV-008 [High, open]: authorization-set cardinality accepts boolean and float type mutations

* Evidence: `_authorization_set_document()` persists integer `authz_count` at `podcaster/distribution_outbox.py:892-906`, but `_validated_recovery_authorization_collection()` compares the persisted set with ordinary Python mapping equality at `podcaster/distribution_outbox.py:1027-1030`. Python considers `True == 1` and `1.0 == 1`.
* Exact failing case: create one failed predecessor and one authorized successor, mutate only `recovery_authz_set.authz_count` from integer `1` to boolean `true` or float `1.0`, leave the stored set digest unchanged, then call `claim()`. Both mutations returned `read_only=False`.
* Independent output: `mutation_capable_bypasses=["set:bool-count","set:float-count"]`; the persisted boolean case was `{"authz_count": true, "authz_count_type": "bool", "read_only": false, "attempts": 2}`.
* Impact: a type-mutated v1 authorization set remains mutation-authorizing, violating the exact field/type/cardinality and fail-closed contract. The digest does not cover the mutated persisted set value during validation because it is recomputed from the expected integer set and the final mapping comparison collapses numeric types.
* Required correction: validate the authorization-set field set and exact types before value comparison (`schema_version: str`, `authz_count: int` excluding `bool`, `ordered_authz_ids: list[str]`, `active_authz_id: str | None`, `digest: str`), then compare a canonical typed representation or otherwise ensure boolean/float numeric equivalents cannot satisfy the integer cardinality field. Add explicit boolean and float mutation tests for every integer-bearing set field.
* Disposition: RV-008 and P07-T01 remain open; P07 is rejected. P07-T07 remains open for a new corrected final-SHA review. RV-001, RV-002, RV-003, RV-004, RV-005, RV-007, and RV-009 remain resolved; RV-006 remains planning-resolved with P05 execution open.

### Leela Independent Probes

| Probe group | Result |
|---|---|
| Fry's six bypasses | All denied: source, reason, authorization time, unknown field, duplicate matching record, and unrelated appended record |
| Complete envelope | All 16 field removals and all 16 tested type/null mutations denied |
| Version/extensions | Legacy v3, unknown v999, and non-empty nested extension denied |
| Collection/supersession | Missing/reordered records, missing/extra/wrong set values, orphan/cyclic supersession, and two-active chain denied |
| Set exact type | **Failed:** integer count `1` changed to `true` or `1.0` remained mutation-capable |
| History/evidence | Timestamp spelling change and unknown nested JSON insertion denied; prior attempt/provider/receipt/readback bindings retained |
| Concurrency/reuse | Concurrent authorization `authorized/rejected`; concurrent claim one `read_only=False` winner and one stale loser; replay `read_only=True` |

Independent conformance artifact: `/home/azureuser/.copilot/session-state/22fb1c6e-0d30-4860-be00-bd605f2908c8/files/leela_rv008_conformance.py`.

### Leela Validation

| Command or gate | Result |
|---|---|
| Independent conformance matrix | 57 cases; 55 failed closed; 2 mutation-capable type bypasses |
| `TMPDIR="$PWD/.test-tmp" pytest tests/test_distribution_outbox.py -q` | `182 passed in 17.60s` |
| Locked terminal-truth command | `902 passed, 1 warning in 71.34s` |
| Full repository suite | `3232 passed, 2 skipped, 2 deselected, 1 warning in 97.53s`; no stale Compose failure and no rebuild required |
| Ruff, format, compile, diff | Passed |
| Bicep | Passed with the documented pre-existing BCP318 warning |
| Exact Checkov | Documented baseline retained: `36 passed, 7 failed` |
| CI Checkov | `34 passed, 0 failed`; Dockerfile baseline gate passed |
| Container | `sha256:001405ef2dee42fdb29d85ad91edc0ffb816b3e7671a4165b01808e8895bc1ad`; non-root user `synth`, ffmpeg/ffprobe, job-runner/outbox imports passed |
| Unconfigured distribution worker | Exited `2` with explicit queue-not-configured error |
| Changed-diff secret/PII scan | No private key, access key, JWT, credential URL, email, or SSN pattern |
| Remote state | 13 successful checks; no review threads; PR open/draft/CLEAN |

### Leela Blockers and Related State

* RV-008/P07-T01 remains the active in-repository High. P07-T07 requires a new corrected exact-head review.
* P00-T01 remains an upstream evidence blocker despite merged `jmservera/SquadScope#770`.
* P05 remains delivery/deployment/canary/rollback work. P06 remains four future elapsed production cycles.
* jmservera/SquadScope-Podcaster#682, jmservera/SquadScope-Podcaster#671, jmservera/SquadScope-Podcaster#678, jmservera/SquadScope-Podcaster#679, jmservera/SquadScope-Podcaster#681, and jmservera/SquadScope-Coordinator#17 remain open. No issue, thread, deployment, canary, merge, or production state was mutated.

## Execution Status

* Review execution status: Complete
* Assessed implementation execution status: Complete for Amy's declared in-repository P01-P04/RV correction scope
* Review execution evidence: Actual source and test behavior were inspected and probed; focused, locked, full, compile, Ruff, format, Bicep, diff, and Checkov validation were independently run.
* Scope boundary: P00-T01, P05, and P06 did not execute and remain distinct external or elapsed residual work, not incomplete Amy execution.

## Plan-to-Change Reconciliation

| Current plan scope | Descriptive changes-record summary | Current-state reconciliation | Gap or rationale |
|---|---|---|---|
| Immutable attempts and weekly aggregation | Append-only attempts plus separate weekly decision | Reconciled in structure and preservation behavior | Failed attempt identity, outcome, events, and evidence survive a later authorized attempt. |
| Exact green proof | Week, manifest, publication digest, canonical artifact, expected provider item, terminal readback, and no duplicate ambiguity | Missing | The implementation defaults omitted proof fields to success and accepts any non-empty provider item when no expected identity was persisted. See RV-008. |
| Controlled recovery | Failed/non-green predecessor plus distinct authorized success | Partial | A distinct attempt and predecessor are retained, but authorization is a caller-supplied `no_mutation_proven=True` assertion without durable proof, and W38-style fixtures can derive recovered green from labels alone. See RV-008. |
| Unknown mutation safety | No blind retry after `provider_unknown` | Reconciled | `authorize_recovery()` rejects a `provider_unknown` predecessor; read-only reconciliation remains the safe path. |
| RV-002 scheduler | Paginated fairness and notification deduplication | Partial | Continuation reaches records beyond 5,000, but selection and sent-marking are not atomic across concurrent scheduler runs. See RV-002. |
| RV-003 alerts | Distinct routes and executable missing-data/non-green rules | Partial | Route-specific action groups and scheduler absence query exist, but weekly critical rows use an event name that deployed weekly rules do not query. Active-depth semantics also alert on any active depth, not missing state. See RV-003. |
| RV-004 cleanup | Bounded, reference-safe, scalable cleanup | Partial | Pagination resolves the fixed 5,000 cap, but every run scans the complete outbox into an unbounded reference set and deletion is not protected from a concurrent new reference. See RV-004. |
| RV-007 consistency | Source/docs/tests/runbook/tracking terminology and dispositions | Partial | Terminology is substantially aligned, but plan/details/changes claim RV-002/RV-003/RV-004 complete and record a locked count of 754 while the current command collects 756. See RV-007. |
| W38/W39 | W38 evidence-conditional; W39 missed/not-dispatched | Partial | Narrative is correct, but executable W38-style fixtures permit recovered green without the required exact proof. W39 remains correctly `missed_not_dispatched`. |
| Four-cycle gate | Exactly four fully proven green cycles | Missing | `four_cycle_acceptance()` trusts weekly labels only and accepts documents with no provider proof or external readback. See RV-009. |
| P00-T01/P05/P06 | Upstream prevention, delivery/canary, four elapsed cycles | Correctly open | External residual work; no deployment or production acceptance is claimed. |

## Completed Work Assessment

| Related marker | Files | What changed and why | Completion evidence | Validation | Assessment |
|---|---|---|---|---|---|
| Attempt truth | `podcaster/distribution_outbox.py`, `tests/test_distribution_outbox.py` | Added attempt ledger, events, predecessor/authz links, and separate aggregation | Failed attempt equality and references survive recovery | Focused and full suites passed | Conformant for immutable preservation. |
| Unknown mutation | `podcaster/distribution_outbox.py` | Prevented recovery authorization from a `provider_unknown` predecessor | Independent inspection and existing test | Passed | Conformant. |
| RV-002 paging | `podcaster/storage.py`, `podcaster/distribution_outbox.py`, scheduler/tests | Added continuation-based scanning beyond 5,000 | Beyond-5,000 test passed | Passed | Prior fixed-window starvation defect resolved; concurrent dedup defect remains. |
| RV-003 routing | `infra/main.bicep`, `infra/modules/distribution-alerts.bicep`, telemetry/tests | Added route-specific action-group parameters and scheduled-query rules | Bicep and Checkov gate passed | Passed syntactically | Route wiring improved; weekly alert mismatch remains functional. |
| RV-004 pagination | outbox/storage/tests | Added complete reference pagination and metadata cursor | Pagination tests passed | Passed | Fixed-cap liveness improved; boundedness and concurrent reference safety remain incomplete. |
| RV-007 terminology | runbook, plan, details, changes, tests | Aligned attempt/weekly, W38/W39, and four-cycle vocabulary | Text comparison | No terminology contradiction found in source/runbook | Tracking completion and validation claims remain stale. |

## Implementation-Time Plan and Detail Update Assessment

| Affected area or marker | What changed and why | Triggering evidence and user decision | Reconciliation performed | Planning and critique state | Assessment |
|---|---|---|---|---|---|
| Attempt/weekly state model | Added immutable attempts and weekly aggregation | Authoritative QA gate | Plan, details, changes, runbook, source, and tests updated | Historical critique preserved | Direction is reconciled; implementation proof gate is incomplete. |
| RV-002/RV-003/RV-004 | Marked complete after Amy's correction | Prior review findings | Source/tests/tracking updated | No second critique required | Completion claims overstate behavior; findings remain open in narrowed current form. |
| RV-007 | Marked reconciled except PR body | Authoritative consistency requirement | Source/docs/tests/runbook/tracking updated | PR remains P05 | Terminology is improved, but current dispositions/counts are inconsistent. |
| W38/W39 | W38 changed to evidence-conditional candidate; W39 retained as missed/not-dispatched | Authoritative caller correction | Narrative artifacts updated | Historical critique unchanged | Narrative is correct; W38 fixture behavior is not. |

## Critique and Material Revision Assessment

* Latest critique dispositions: PC-001-PC-009 remain historical and were not mutated or repeated.
* Material revisions: The dual-level attempt/weekly model, exact proof requirement, W38/W39 correction, pagination, alert routing, and cleanup revisions preserve confirmed intent.
* Dependent-work pause assessment: P05 and P06 remain open, and no deployment acceptance is claimed.
* Justification assessment: Revision direction is supported, but current completion claims are not supported for RV-002/RV-003/RV-004/RV-007 or the exact green/four-cycle gates.

## Plan Follow-Up Assessment

| Follow-up item | Why outside immediate scope | Owner or next action | Assessment and route |
|---|---|---|---|
| Future authoritative Spotify mutation/idempotency support | Provider contract remains unsupported | Distinct provider-capability follow-up | Properly outside current acceptance. |
| Optional W38 deployed-image/revision forensics | Historical causal reconstruction does not establish current exact proof | Distinct incident-forensics follow-up | Optional; must not manufacture recovered-green evidence. |
| P00-T01 upstream prevention | Owning code is in `jmservera/SquadScope` | Upstream owner | External residual work. |
| P05 delivery/canary | Requires git/GitHub/deployment/provider authority | Delivery owner | External residual work. |
| P06 four elapsed cycles | Requires future calendar cycles and production evidence | Production verification owner | Elapsed residual work. |

## Findings

<!-- rpi:review id=RV-001 -->
### RV-001 [High, resolved]: Read-only YouTube promotion takeover convergence

* Related scope: P02-T01, P02-T03
* Evidence: Known-identity promotion takeover performs authoritative readback and does not issue a second mutation.
* Impact: Prior duplicate-promotion/stale-claim risk remains closed.
* Destination: Resolved in current implementation.
* Disposition: Resolved; retain regression coverage.

<!-- rpi:review id=RV-002 -->
### RV-002 [High, remains open]: Scheduler notification deduplication is not atomic across concurrent runs

* Related scope: P01-T03, P03-T02, P04
* Evidence: `due_reconciliations_page()` only reads due state. `distribution_scheduler.run_once()` enqueues first and calls `mark_reconciliation_notified()` afterward. Two independent pre-mark scans returned the identical outbox/provider/token, so concurrent schedulers can both enqueue the same reconciliation. Continuation paging does reach records beyond 5,000.
* Impact: Duplicate work can race provider reconciliation and violates the required bounded-concurrency deduplication contract.
* Destination: `rpi-implement`
* Smallest useful next action: Add a fenced/CAS notification reservation or equivalent durable single-winner transition before enqueue, with a concurrent two-run test and stale-reservation recovery.

<!-- rpi:review id=RV-003 -->
### RV-003 [High, remains open]: Deployed weekly non-green alerts do not match emitted telemetry

* Related scope: P03-T03, P04-T02, P05-T05
* Evidence: `signal_rows()` emits `distribution_identity_conflict` and `distribution_weekly_non_green` with event `distribution_provider_state`; `distribution-alerts.bicep` queries event `distribution_weekly_state` for both rules. Independent output confirmed the mismatched event. The active-depth rule also matches every warning active-depth row rather than detecting active depth without state rows.
* Impact: Required identity-conflict and weekly non-green critical alerts cannot fire from actual emitted rows, and the active-depth rule does not implement its documented missing-state condition.
* Destination: `rpi-implement`
* Smallest useful next action: Align emitted/query event contracts, implement the active-depth-without-state join/absence condition, and add tests that evaluate representative emitted rows against generated rule queries.

<!-- rpi:review id=RV-004 -->
### RV-004 [Medium, remains open]: Cleanup pagination is complete but not bounded or concurrency-safe

* Related scope: P01-T02, P03-T02, P04
* Evidence: `cleanup_orphan_artifacts()` paginates every outbox record and retains every referenced artifact path in one in-memory set before deleting metadata-page candidates. No durable reference index, snapshot, or conditional deletion prevents an artifact from becoming referenced after the scan and before deletion.
* Impact: Work and memory grow with the complete retained outbox corpus, and a concurrent enqueue can create a new reference after the scan, violating the required bounded/reference-safe cleanup contract.
* Destination: `rpi-implement`
* Smallest useful next action: Use a durable reference index/refcount or bounded snapshot/mark-and-sweep generation with conditional deletion, plus scale and concurrent-reference tests.

<!-- rpi:review id=RV-005 -->
### RV-005 [Medium, resolved]: Ambiguous upload evidence remains accurately fail-closed

* Related scope: P02-T01, P02-T03
* Evidence: Missing provider identity remains `youtube_identity_unprovable`/non-green rather than being described as identity-bound readback.
* Impact: Durable evidence does not invent provider observations.
* Destination: Resolved in current implementation.
* Disposition: Resolved; retain fail-closed behavior.

<!-- rpi:review id=RV-006 -->
### RV-006 [High, resolved at planning level]: PR #682 closure inventory

* Related scope: P05-T03
* Evidence: The plan retains the historical and current review-thread closure matrix without claiming GitHub resolution.
* Impact: Planning coverage is present; actual replies/resolution remain delivery work.
* Destination: Residual P05-T03 execution.
* Disposition: Planning-level finding remains resolved.

<!-- rpi:review id=RV-007 -->
### RV-007 [Medium, remains open]: Canonical tracking overstates resolved findings and current validation counts

* Related scope: Source/docs/tests/runbook/tracking consistency and P05 handoff
* Evidence: Phase details and plan state that RV-002/RV-003/RV-004 are complete, but this review reproduces current defects. The changes record says the locked contract is 754 tests, while the exact current command passes 756. The PR body remains intentionally outside Amy's scope.
* Impact: A delivery owner could publish incorrect dispositions and validation evidence.
* Destination: `rpi-plan` for canonical tracking reconciliation; P05 for PR narrative.
* Smallest useful next action: After implementation corrections, update plan/details/changes with the exact dispositions and current command outputs; rewrite the PR narrative during P05 without claiming deployment acceptance.

<!-- rpi:review id=RV-008 -->
### RV-008 [High]: Exact provider proof and controlled-recovery authorization are not enforced

* Related scope: P01-P04 exact green gate; W38 fixture; controlled recovery
* Evidence: `_verification_proof()` treats omitted week, manifest, publication digest, and artifact digest as matches; defaults canonical selection from the local record; and accepts any observed provider item when no expected provider ID exists. An independent probe with no intent/receipt/expected provider ID and no supplied proof returned all proof fields green and produced `published_verified`. `authorize_recovery()` persists only a caller assertion `no_mutation_proven=True`, not durable safety evidence. `weekly_state_from_attempts()` derives `published_verified_recovered` from outcome labels without authorization or exact proof.
* Impact: W38 or any week can be classified green/recovered without the exact identity, expected provider item, duplicate-resolution, and proven-safe authorization required by the authoritative gate.
* Destination: `rpi-implement`
* Smallest useful next action: Require explicit identity-bound proof values, a persisted expected provider identity, authoritative readback source/state, explicit duplicate reconciliation, and durable authorization proof references; make label-only W38/recovery fixtures non-green.

<!-- rpi:review id=RV-009 -->
### RV-009 [High]: Four-cycle acceptance trusts weekly labels without external proof

* Related scope: P06 evaluator and acceptance gate
* Evidence: `four_cycle_acceptance()` checks only that four `weekly_aggregation.state` values are green. An independent probe of four label-only dictionaries returned `True`; no attempts, publication identity, manifest/digest, canonical artifact, provider item, duplicate resolution, aggregate provider state, or external readback was present.
* Impact: The production acceptance evaluator can accept four cycles containing missing external readback or other absent proof merely because their labels are green.
* Destination: `rpi-implement`
* Smallest useful next action: Evaluate each cycle's complete proof envelope and reject missing/partial/unknown/manual/identity-conflict/duplicate-ambiguous/no-readback evidence, with a parameterized negative matrix.

## Defects

* High: RV-002, RV-003, RV-008, RV-009.
* Medium: RV-004, RV-007.
* Resolved: RV-001 and RV-005.
* Resolved at planning level with external execution remaining: RV-006.
* No critical defect was identified.

## Routed Findings

| Finding | Destination | Owner or next action | Reason for route |
|---|---|---|---|
| RV-002 | `rpi-implement` | Add atomic scheduler reservation/deduplication | In-repository implementation defect |
| RV-003 | `rpi-implement` | Align telemetry/query contracts and absence semantics | In-repository implementation defect |
| RV-004 | `rpi-implement` | Make cleanup bounded and concurrent-reference-safe | In-repository implementation defect |
| RV-007 | `rpi-plan` plus P05 handoff | Reconcile tracking and PR narrative | Canonical artifact/handoff gap |
| RV-008 | `rpi-implement` | Enforce exact proof and durable recovery authorization | In-repository acceptance defect |
| RV-009 | `rpi-implement` | Validate complete proof per four-cycle row | In-repository acceptance defect |

Later implementation of routed findings does not require another Review.

## Residual Work

* P00-T01 remains cross-repository residual work owned by `jmservera/SquadScope`.
* P05 remains external delivery work: commit/push/PR, final-SHA independent review, checks, approval/merge, release provenance, deployment, canary, alert fire/clear, and rollback evidence.
* P06 remains elapsed production work: four consecutive future post-fix cycles with full upstream, Azure, attempt, aggregation, and provider-readback evidence.
* These residual items do not reduce the severity of current in-repository defects and do not constitute deployment acceptance.

## Blockers and Remaining Work

* Blockers: P00-T01 upstream ownership; P05 repository/deployment/provider authority; P06 elapsed calendar cycles.
* Remaining implementation defects: RV-002, RV-003, RV-004, RV-008, RV-009.
* Remaining planning/handoff reconciliation: RV-007.
* Remaining external work: P00-T01, P05, P06.

## Validation Evidence

| Command | Scope | Status | Summary |
|---|---|---|---|
| Focused correction command | Outbox/worker/telemetry/deploy | Passed | `81 passed in 2.03s`. |
| Locked contract command | Dispatch/API/outbox/worker/provider/publication/monitoring/deployment | Passed with current count | `756 passed, 1 warning in 55.69s`; current worktree count differs from the recorded 754. |
| Initial `pytest tests/ -q` | Full repository | Failed from stale Compose image | `1 failed, 3085 passed, 2 skipped, 2 deselected`; missing fanout clip. |
| `docker compose -f docker-compose.fanout.yml build --quiet` plus focused integration | Existing scale-out image | Passed | Rebuilt existing test image; integration `1 passed in 29.65s`. |
| Final `pytest tests/ -q` | Full repository | Passed | `3086 passed, 2 skipped, 2 deselected, 1 warning in 85.64s`. |
| `python3 -m compileall -q podcaster` | Production Python | Passed | No compile failures. |
| `ruff check podcaster tests --quiet` | Production and tests | Passed | No lint findings. |
| `ruff format --check podcaster tests --quiet` | Production and tests | Passed | Formatting check passed. |
| `az bicep build --file infra/main.bicep --stdout >/dev/null` | Infrastructure | Passed with existing warning | Existing BCP318 warning only. |
| `git diff --check` and deleted-file query | Worktree safety | Passed | No whitespace errors; no deleted files in the implementation delta. |
| Exact Checkov | Infrastructure baseline | Baseline retained | `36 passed, 7 failed`; failures are the documented ACR/storage/OpenAI baseline. |
| CI-equivalent Checkov skip-list gate | Blocking infrastructure policy | Passed | `34 passed, 0 failed`. |
| Exact-proof independent probe | Green provider gate | Failed | Missing explicit proof and expected IDs still produced all-green proof and `published_verified`. |
| Concurrent scheduler pre-mark probe | RV-002 deduplication | Failed | Two scans selected the identical outbox/provider/token. |
| Weekly telemetry/query probe | RV-003 alert contract | Failed | Emitted event was `distribution_provider_state`; deployed weekly rule expects `distribution_weekly_state`. |
| Label-only four-cycle probe | P06 evaluator | Failed | Four green labels with no external evidence returned `True`. |

## Unsafe-Narrowing Assessment

* Truthful non-green exits remain preserved in worker aggregation.
* Immutable attempt preservation is implemented.
* Unknown mutations remain blocked from blind recovery authorization.
* Provider green proof is unsafely broadened by inferred/omitted fields; RV-008 is material.
* Four-cycle acceptance is unsafely narrowed to labels; RV-009 is material.
* CI/security gates were not weakened.
* No secret/PII regression was identified in the reviewed boundary.

## PR Narrative Changes Required

Before `.copilot-tracking/pr/pr.md` is used:

1. State review execution `Complete`, implementation execution `Complete for Amy's declared in-repository scope`, and outcome `Not accepted`.
2. List exact dispositions: RV-001 resolved; RV-002 high open; RV-003 high open; RV-004 medium open; RV-005 resolved; RV-006 planning-level resolved with P05 execution open; RV-007 medium open; RV-008 high open; RV-009 high open.
3. Use independent validation: focused 81; locked 756/1 warning; final full 3086/2 skipped/2 deselected/1 warning after stale-image refresh; compile/Ruff/format/Bicep/diff passed; exact Checkov 36/7 baseline; gate 34/0.
4. Keep W38 evidence-conditional and explicitly state current fixtures do not yet enforce the full recovered-green proof contract. Keep W39 `missed_not_dispatched`.
5. State P00-T01 is owned by `jmservera/SquadScope`; P05 and P06 remain external/elapsed residual work.
6. Do not claim merge readiness, deployment readiness, canary acceptance, four-cycle completion, or production acceptance.

## Outcome

* Outcome: Not accepted
* Outcome rationale: Amy completed the declared in-repository execution and materially improved attempt preservation, paging, routing, and terminology. However, RV-002 and RV-003 remain high operational defects, RV-008 and RV-009 allow false green/recovered and four-cycle acceptance without exact external proof, RV-004 remains a boundedness/reference-safety defect, and RV-007 leaves canonical claims inaccurate. High-severity defects prevent acceptance.

## Closeout Routing Record

| Finding class | Destination | Owner or next action |
|---|---|---|
| Implementation defect | `rpi-implement` | Resolve RV-002, RV-003, RV-004, RV-008, and RV-009. |
| Decision/artifact gap | `rpi-plan` | Reconcile RV-007 after implementation, with PR narrative handled in P05. |
| Material evidence gap | None | Evidence is sufficient for this verdict. |
| Non-blocking residual work | P00-T01, P05, P06 | Upstream owner, delivery owner, and production verification owner respectively. |

* Review execution status: Complete
* Assessed implementation execution status: Complete for Amy's declared in-repository scope
* Outcome: Not accepted
* Severity summary: 0 Critical; 4 High open; 2 Medium open; 2 resolved prior findings; 1 planning-level prior finding resolved
* Validation coverage: Focused/locked/full pytest, compile, Ruff, format, diff safety, Bicep, exact and blocking Checkov, and independent negative probes
* Blockers: P00-T01 upstream ownership; P05 delivery/deployment authority; P06 elapsed cycles

## Relevant Artifacts

| Artifact | Description |
|---|---|
| [.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md](.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md) | Current requirements, acceptance gates, markers, and residual phases |
| [.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md](.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md) | Detailed state model and task contracts |
| [.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md](.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md) | W38/W39 and provider evidence research |
| [.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md](.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md) | Preserved historical critique |
| [.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md](.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md) | Amy's implementation and validation claims |
| [docs/ops/distribution-terminal-truth.md](docs/ops/distribution-terminal-truth.md) | Operational truth, alert, canary, and rollback contract |
| [.copilot-tracking/pr/pr.md](.copilot-tracking/pr/pr.md) | Draft PR narrative requiring the listed corrections |
| [.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md](.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md) | Canonical independent review result |

## Next Steps

Route RV-002, RV-003, RV-004, RV-008, and RV-009 to `/rpi-implement`; route RV-007 tracking reconciliation to `/rpi-plan` after those corrections. Separately clear P00-T01 in `jmservera/SquadScope` and retain P05/P06 as external residual work. Do not run another review.

---

## Fresh Independent Final-Revision Review — 2026-09-22

### Identity, Boundary, and Repository State

* Reviewer: **Fry, QA / Tester**.
* Independence: Fry did not author revision `02241a1`; Leela was the sole revision author. Bender, Hermes, Amy, and Leela did not advise, pair, or contribute to this review.
* Review boundary: exact committed delta `5cd84c4c29f7f597f0a5b03a2c23e5b79b5ed7f7..02241a1707c8a5d2a17120185e988634de188d21`.
* Ancestry: both `5cd84c4` and implementation commit `dd7b265` are ancestors of `02241a1`.
* Opening state: clean worktree; local branch and `origin/squad/incident-provider-terminal-truth` both at `02241a1`.
* PR state inspected: `jmservera/SquadScope-Podcaster#684` open, draft, mergeable, no reviews, no review threads, and checks still running at review start.
* Scope truth retained: W39 is `missed_not_dispatched`; W38 was published, but recovered-green requires exact identity-bound authoritative provider readback and durable recovery authorization.

### Final Verdict

**Not accepted (`request_changes`).** RV-003 is resolved and RV-007 is reconciled by this review-only update. RV-002, RV-004, RV-008, and RV-009 remain implementation findings. RV-002, RV-008, and RV-009 remain High; RV-004 is escalated from Medium to High because the new reference index can delete an artifact still referenced by a pre-index outbox record. PR #684 must remain draft and blocked.

### Final Finding Dispositions

<!-- rpi:review-final id=RV-002 -->
#### RV-002 [High, remains open]: an expired `enqueue_started` reservation is permanently hidden from scheduler recovery

* Evidence: `due_reconciliations_page()` treats every `notification_reservation.stage == "enqueue_started"` as fresh without considering `lease_expires_at`. The independent fake-clock probe advanced beyond the lease and still returned no due reconciliation.
* Impact: a crash or ambiguous exception after `begin_reconciliation_enqueue()` but before completion can strand the reconciliation token indefinitely. Concurrent initial reservation is single-winner, but the required bounded abandoned-reservation recovery is not satisfied.

<!-- rpi:review-final id=RV-003 -->
#### RV-003 [High, resolved]: emitted weekly events and deployed alert queries now agree

* Evidence: representative identity-conflict and weekly-non-green rows emitted `distribution_provider_state`; both Bicep rules query that event; the stale `distribution_weekly_state` literal is absent. The active-depth rule now requires active depth and zero provider-state rows in the same window.
* Disposition: Resolved in `02241a1`; retain executable row/query and fire/clear regression coverage.

<!-- rpi:review-final id=RV-004 -->
#### RV-004 [High, remains open; escalated]: cleanup can delete artifacts referenced by pre-index outbox records

* Evidence: cleanup now trusts only `distribution-artifact-references/<digest>.json`. There is no migration or fallback that registers artifacts already referenced by outbox records created before this index existed. The independent probe retained an outbox record, removed only its reference-index document to model the deployed pre-index state, aged the metadata, and cleanup reported `removed=1` with `artifact_exists=False`.
* Impact: rollout against existing durable records can delete a live referenced artifact. Per-run budgets and the new-reference CAS fence work for records created under the new schema, but backward-compatible reference safety is not satisfied.

<!-- rpi:review-final id=RV-007 -->
#### RV-007 [Medium, resolved by review reconciliation]: canonical review state and counts are current

* Evidence: this artifact, plan, phase details, changes record, and PR #684 now report the same final-SHA verdict, dispositions, `83` focused tests, final `3088 passed, 2 skipped, 2 deselected, 1 warning`, and retained P00-T01/P05/P06 blockers.
* Disposition: Resolved as a tracking-only review update; no source or test was altered.

<!-- rpi:review-final id=RV-008 -->
#### RV-008 [High, remains open]: recovery classification still accepts label-only/opaque authorization evidence

* Evidence: exact `record_verification()` proof now fails closed, and unknown/ambiguous predecessor evidence is rejected. However, public `weekly_state_from_attempts()` still returns `published_verified_recovered` from only `terminal_outcome="published_verified"`, `proof_complete=True`, and an arbitrary non-empty `recovery_evidence_reference`. `authorize_recovery()` likewise validates an opaque token and a source enum rather than a durable evidence object bound to the predecessor/provider identity.
* Impact: W38-style data can still become recovered green without the exact provider identity/readback and authorization proof required by the authoritative gate.

<!-- rpi:review-final id=RV-009 -->
#### RV-009 [High, remains open]: four-cycle evaluation trusts persisted proof booleans after identity mutation

* Evidence: four label-only rows are now correctly rejected. However, `_authoritative_green_document()` trusts stored proof booleans and does not rebind them to raw proof values. Four complete synthetic envelopes passed; changing one cycle's `accepted_job_id` after proof creation still returned `True`.
* Impact: contradictory or corrupted persisted identity/proof envelopes can satisfy the four-cycle gate. The evaluator does not independently establish exact identity-bound external proof for each cycle.

### Independent Negative Probes

| Finding | Probe | Result |
|---|---|---|
| RV-002 | Two-thread reservation barrier from the owner suite | Passed: one reservation winner and one stale loser |
| RV-002 | Begin enqueue, advance fake clock beyond lease, query due work | **Failed closed-loop recovery:** returned `[]`; token remained stranded |
| RV-003 | Emit identity-conflict rows and compare event/metric to Bicep; check stale event absence and active-depth absence query | Passed |
| RV-004 | Bounded/page/restart/concurrent-new-reference owner tests | Passed for new-schema records |
| RV-004 | Model a retained pre-index outbox by removing only its reference-index row, age metadata, run cleanup | **Failed:** `removed=1`, referenced artifact deleted |
| RV-007 | Compare plan/details/changes/review/PR statuses and current command counts | Initially stale; reconciled in this review-only update |
| RV-008 | Parameterized missing/contradictory exact-verification owner tests | Passed: `identity_conflict` |
| RV-008 | Two attempts with only labels, `proof_complete=True`, and opaque evidence token | **Failed:** returned `published_verified_recovered` |
| RV-009 | Four label-only weekly rows | Passed: rejected |
| RV-009 | Four complete envelopes, then mutate one `accepted_job_id` without recomputing proof | **Failed:** still accepted |

### Independent Validation

| Command | Result |
|---|---|
| `TMPDIR="$PWD/.test-tmp" pytest tests/test_distribution_outbox.py tests/test_distribution_worker.py tests/test_distribution_telemetry.py tests/test_deploy_workflow.py -q` | `83 passed in 2.15s` |
| Initial `TMPDIR="$PWD/.test-tmp" pytest tests/ -q` | `1 failed, 3087 passed, 2 skipped, 2 deselected, 1 warning`; stale Compose recorder image reproduced |
| `docker compose -f docker-compose.fanout.yml build --quiet` then focused fanout test | `1 passed in 14.74s` |
| Final `TMPDIR="$PWD/.test-tmp" pytest tests/ -q` | `3088 passed, 2 skipped, 2 deselected, 1 warning in 82.22s` |
| `ruff check podcaster tests` | Passed |
| `ruff format --check podcaster tests` | Passed; `192 files already formatted` |
| `python3 -m compileall -q podcaster` | Passed |
| `git diff --check 5cd84c4..02241a1` | Passed |
| `az bicep build --file infra/main.bicep --stdout >/dev/null` | Passed with pre-existing BCP318 warning |
| `checkov --directory infra --framework bicep --quiet` | Baseline reproduced: `36 passed, 7 failed` |
| CI-equivalent Bicep Checkov skip-list command | `34 passed, 0 failed` |
| `docker build -f Containerfile -t podcaster-synthesis:fry-review . --quiet` | Passed; image ID `sha256:d35b69747463d706227b30dedeaf1ecc22bac8248c6fb45a30f45d7de2ee6291` |
| Container smoke | Passed: ffmpeg/ffprobe present, UID `999`, pipeline imports pass, unconfigured worker exits `2` |
| Changed-diff credential/secret pattern scan | No suspected secret, credential value, private key, signed URL, or raw PII found; matches were documentation/identifier terms and image hashes |

### GitHub and External Relationships

* `jmservera/SquadScope-Podcaster#684`: open and draft; no review threads; keep draft/blocked because High implementation findings and external gates remain.
* `jmservera/SquadScope-Podcaster#682`: open and non-draft; not superseded, closed, merged, or approved by this review. Its separate review history remains intact.
* `jmservera/SquadScope-Podcaster#671`: open; Spotify/manual-handoff provider dependency remains.
* `jmservera/SquadScope-Podcaster#678`: open; YouTube ambiguous-create identity reconciliation relationship remains.
* `jmservera/SquadScope-Podcaster#679`: open; Spotify exact reconciliation relationship remains.
* `jmservera/SquadScope-Podcaster#681`: open; atomic outbox work item remains the parent implementation relationship.

### Blockers and Clearing Evidence

| Blocker | Owner | Evidence required to clear |
|---|---|---|
| RV-002 | Podcaster revision owner | Expired/abandoned `enqueue_started` recovery that preserves single-winner semantics, with crash-before-send, ambiguous-send, stale-fence, and restart probes |
| RV-004 | Podcaster revision owner | Safe migration/backfill or fallback proof for every pre-index retained outbox artifact, plus rollout regression proving no referenced deletion |
| RV-008 | Podcaster revision owner | Recovery authorization bound to durable, validated predecessor/provider safety evidence; label-only helper path removed or made exact-proof aware |
| RV-009 | Podcaster revision owner | Four-cycle revalidation against immutable raw proof values/references so post-proof identity contradiction fails |
| P00-T01 | `jmservera/SquadScope` owner | Exact W39 upstream blocked-stage fix and durable dispatch-to-Azure evidence |
| P05 | Delivery/deployment owners | Accepted final-SHA review, all checks, approval/merge provenance, authorized deployment, provider canary, alert fire/clear, and rollback evidence |
| P06 | Production verification owner | Four consecutive future post-fix cycles with complete authoritative external proof |

No GitHub thread, issue, or related PR was resolved or closed by this review.

---

## Fresh Independent Farnsworth-Revision Review — Livingston — 2026-09-22

### Reviewer Identity, Independence, and Exact Boundary

* Reviewer: **Livingston, QA / Verification**.
* Independence: Livingston did not author the revision. Farnsworth was the sole revision author. Bender, Hermes, Amy, Leela, Fry, and Farnsworth did not advise, pair, or contribute during review.
* Exact comparison: `2e87d9bf2596df491494a3160b127e79e8f0f301..601d36afc62d745c6a67d917b63bcd89e8c18737`.
* Source focus: Farnsworth commit `0f489b12ae93b8e5f479fb9278f369f99e89190f`.
* Opening repository state: clean worktree; local and `origin/squad/incident-provider-terminal-truth` both at `601d36a`; comparison base is an ancestor; remote divergence `0/0`.
* PR state at opening: #684 open, draft, merge state `CLEAN`, 13 successful checks, no reviews, and no review threads.
* Incident truth retained: W39 is `missed_not_dispatched`. W38 was published, but `published_verified_recovered` remains evidence-conditional on exact weekly identity-bound authoritative provider proof.

### Livingston Final Verdict

**Not accepted (`request_changes`).** The Farnsworth revision resolves RV-002, RV-004, and RV-009, while RV-003 and RV-007 remain resolved. RV-008 remains **High** because recovery authorization can branch around a later `provider_unknown` attempt and restore mutation authority. Severity: 0 Critical, 1 High, 0 Medium, 0 Low.

### Finding Dispositions

<!-- rpi:review-livingston id=RV-002 -->
#### RV-002 [High, resolved]: expired notification reservations recover with one fenced owner

* Evidence: the two-thread barrier produced one winner and one stale loser; both expired `reserved` and `enqueue_started` stages became due; replacement incremented the fence; stale owners could neither abort nor complete the replacement.
* Disposition: Resolved. Retain exact fake-clock, concurrent-winner, and stale-owner coverage.

<!-- rpi:review-livingston id=RV-003 -->
#### RV-003 [High, remains resolved]: emitted and deployed alert contracts stay aligned

* Evidence: no source change in the Farnsworth revision reopened the previously verified event/query and active-depth absence semantics; focused telemetry/deployment tests passed.
* Disposition: Remains resolved.

<!-- rpi:review-livingston id=RV-004 -->
#### RV-004 [High, resolved]: legacy reference migration is bounded, resumable, fail-closed, and fenced

* Evidence: cleanup backfilled a removed legacy reference before deletion; incomplete one-record pages returned without deleting; persisted cursor resumed; current-schema concurrent references defeated the CAS cleanup claim.
* Disposition: Resolved. The migration completion gate prevents pre-index referenced deletion.

<!-- rpi:review-livingston id=RV-007 -->
#### RV-007 [Medium, remains resolved]: canonical tracking preserves history and current ownership

* Evidence: Fry/Leela rejection history remains intact; Farnsworth/Livingston ownership is explicit; this cycle reconciles exact final-head findings and validation without rewriting prior cycles.
* Disposition: Remains resolved by review-only tracking reconciliation.

<!-- rpi:review-livingston id=RV-008 -->
#### RV-008 [High, open]: an older predecessor can authorize mutation after a later `provider_unknown`

* Evidence: a first attempt terminated `failed_terminal` and received valid structured recovery evidence. Its succeeding attempt then terminated `provider_unknown`. Reusing the older predecessor and its evidence was accepted, appended a third attempt, and `claim()` returned `read_only=False`.
* Root cause: `_validated_recovery_authorization_evidence()` compares `prior_attempt_ids` only through the selected predecessor. `authorize_recovery()` rejects unknown state only on that selected predecessor and does not require it to be the latest terminal attempt or reject later unresolved/unknown attempts.
* Impact: the branch can regain provider mutation authority after an unknown mutation, violating the authoritative no-blind-retry invariant and the exact complete-attempt-history binding required for controlled recovery.
* Required clearing evidence: authorization rejects any non-latest predecessor and any history containing a later `provider_unknown`, ambiguous receipt, identity conflict, or unresolved mutation; a succeeding claim remains reconciliation-only or is not created; focused concurrent/history-tamper probes and full validation pass.

<!-- rpi:review-livingston id=RV-009 -->
#### RV-009 [High, resolved]: proof envelopes rebind identity and require four exact cycles

* Evidence: label-only, tampered week/job/manifest/artifact/provider/readback/duplicate, and unauthorized-recovery cycles were rejected; four consecutive exact complete envelopes passed.
* Disposition: Resolved. Retain raw-evidence rebinding and exact four-cycle positive coverage.

### Independent Negative Probes

| Target | Command or probe | Result |
|---|---|---|
| RV-002/RV-004/RV-008/RV-009 owner matrix | `TMPDIR="$PWD/.test-tmp" pytest tests/test_distribution_outbox.py -q -k 'reconciliation_notification_reservation_is_single_winner_and_fenced or cleanup_backfills_legacy_reference_before_deletion or cleanup_fails_closed_while_legacy_reference_scan_is_incomplete or recovery_authorization_rejects_mismatched_structured_evidence or recovery_intent_rejects_provider_identity_outside_authorization or recovery_authorization_rejects_opaque_evidence or four_cycle_acceptance_requires_complete_authoritative_envelopes or four_cycle_acceptance_rejects_identity_tampering or weekly_w38_recovery_candidate_and_w39_missed_fixture'` | `22 passed, 35 deselected` |
| RV-008 history-order bypass | Create failed predecessor, authorize recovery, terminate successor `provider_unknown`, then reuse older predecessor evidence | **Failed safety:** `RV008_BYPASS ... read_only=False attempts=3` |
| Changed executable diff secret/PII scan | Private-key, token, connection-string, JWT, and email patterns | `NO_SUSPECTED_SECRETS_OR_PII` |

### Independent Full Validation

| Command | Result |
|---|---|
| Focused outbox suite | `57 passed in 1.88s` |
| Focused correction suite | `102 passed in 5.62s` |
| Locked targeted contract | `777 passed, 1 warning in 60.48s` |
| Initial full `pytest tests/ -q` | `1 failed, 3106 passed, 2 skipped, 2 deselected, 1 warning`; only stale Compose recorder image |
| Compose rebuild plus focused fanout integration | `1 passed in 16.08s` |
| Final full `pytest tests/ -q` | `3107 passed, 2 skipped, 2 deselected, 1 warning in 85.77s` |
| Ruff, format, compile, exact diff safety, deleted-file check | Passed |
| Bicep build | Passed with pre-existing BCP318 warning |
| Exact Checkov | Documented baseline retained: `36 passed, 7 failed` |
| CI-equivalent Bicep Checkov | `34 passed, 0 failed` |
| Dockerfile Checkov baseline | Passed |
| Container build | `sha256:826e759f3685a781d185a334f86014b71238ab84fda4896776b5f396392622f6` |
| Container smoke | UID `999`; ffmpeg/ffprobe and pipeline imports passed; unconfigured distribution worker exited `2` |

No test, assertion, validation gate, or security gate was weakened.

### GitHub and External Relationships

* #684: open, draft, merge state `CLEAN`; 13 checks successful at review opening; no reviews or review threads. It remains draft/blocked and is not approved by this review.
* #682: open, non-draft, not superseded or mutated; its separate stage-budget work and review history remain unchanged.
* #671: open; Spotify video publication contract/manual-handoff dependency remains unchanged.
* #678: open; YouTube ambiguous-create identity reconciliation dependency remains unchanged.
* #679: open; Spotify audio exact-reconciliation dependency remains unchanged.
* #681: open; atomic outbox worker remains the parent implementation relationship.

### Blockers and Clearing Evidence

| Blocker | Owner | Evidence required to clear |
|---|---|---|
| RV-008 | Podcaster revision owner | Bind authorization to the complete current attempt history and latest eligible predecessor; reject later unknown/ambiguous/conflicting attempts; prove no mutation-capable claim can follow `provider_unknown` |
| P00-T01 | `jmservera/SquadScope` owner | Exact W39 upstream blocked-stage fix and durable dispatch-to-Azure evidence |
| P05 | Delivery/deployment owners | Accepted final-SHA review, all checks, approval/merge provenance, authorized deployment, provider canary, alert fire/clear, and rollback evidence |
| P06 | Production verification owner | Four consecutive future post-fix cycles with complete authoritative external proof |

No issue, PR, review thread, deployment, canary, or production state was resolved, closed, or mutated by this review.

---

## Frank Canonical-History Revision Review — Fry — 2026-09-22

### Reviewer Independence and Exact Boundary

* Reviewer: **Fry, independent QA reviewer**.
* Independence: Fry did not author, advise, pair, or contribute to Frank's revision. Frank was the sole author. Bender, Hermes, Amy, Leela, Farnsworth, Rusty, Basher, Ralph, Livingston, and the remaining named agents provided no input.
* Exact comparison: `3529a027d68c3811274237a49202dafc87d33c70..98eae68fe25b429cc59a36fff97a7154979d2bda`.
* Source commit: `bfead2572ae7c98bf82122281ac3a26ff3b91edc`.
* Final reviewed head: `98eae68fe25b429cc59a36fff97a7154979d2bda`.
* Post-source scope: `bfead25..98eae68` changes only plan/details/changes/PR narrative.
* Opening state: clean incident worktree; local, origin, and PR head matched; comparison base was the merge base; source was an ancestor; branch divergence was `0/0`.
* PR state: #684 open, draft, merge state `CLEAN`, 13 successful checks, no submitted reviews, and zero review threads.

### Fry Verdict

**Not accepted (`request_changes`).** Severity: 0 Critical, 1 High, 0 Medium, 0 Low. Frank resolves the complete predecessor attempt/event history defect, but RV-008 remains High because the durable recovery-authorization record is not itself exact or unique.

### RV Dispositions

| Finding | Fry disposition |
|---|---|
| RV-002 | Remains resolved; focused, locked, and full reservation/fencing regressions pass. |
| RV-003 | Remains resolved; telemetry/deployment regressions, Bicep, and CI Checkov pass. |
| RV-004 | Remains resolved; migration/cleanup regressions pass in the locked/full suites. |
| RV-007 | Remains resolved at tracking level after this append-only reconciliation; every prior cycle remains intact. |
| RV-008 | **High, open.** Predecessor history and successor are bound, but authorization-record metadata and cardinality are not. |
| RV-009 | Remains resolved; authoritative four-cycle regressions pass. |

### RV-008 High: Authorization Envelope Is Mutable and Non-Unique

`_recovery_authorization_binding_is_valid()` locates the first authorization whose `authz_id`, successor `attempt_id`, and `predecessor_attempt_id` match the winner. It recomputes the nested evidence and digest, but does not compare the authorization record's top-level `source`, `reason`, or `authorized_at` with the successor's immutable authorization identity. It also does not reject extra fields, multiple matching authorizations, or unrelated additional authorization records.

Independent exact reproduction kept the complete evidence and successor unchanged, then applied each of these durable-state mutations before the first claim:

1. Change authorization `source`.
2. Change authorization `reason`.
3. Change authorization `authorized_at`.
4. Add an unbound authorization audit field.
5. Duplicate the matching authorization record.
6. Append an unrelated authorization record.

Every case still returned `read_only=False`. This violates the requested exact evidence consistency, duplicate/additional evidence rejection, and one-bound-successor authorization contract.

Required correction: canonicalize and bind the complete selected authorization record, require exactly one matching authorization for the successor/predecessor/authz identity, define and enforce the permitted authorization set cardinality/order, and make every changed, missing, additional, duplicated, reordered, or unrelated authorization record fail closed before mutation authority is granted.

### Independent Conformance Results

| Boundary | Result |
|---|---|
| Deterministic typed serialization and JSON round trip | Passed |
| Complete ordered predecessor attempts/events | Passed |
| Precise predecessor ID/index/count boundary | Passed |
| Attempt record version and legacy/unknown version rejection | Passed |
| Attempt identity/state/classification/proof/provider fields | Passed; mutation denied |
| Event type/state/sequence/time/owner/claim/execution/fence/lease | Passed; mutation denied |
| Nested intent and receipt identity/additional fields | Passed; mutation denied |
| Provider evidence and terminal/post-terminal readback | Passed; mutation denied |
| Publication identity/objective and cross-week replay | Passed; mutation denied |
| Missing/additional/duplicate/reordered attempts/events | Passed; mutation denied |
| Successor additional field/unexpected event/reuse | Passed; first exact use only, later read-only |
| Concurrent authorization | Passed; one authorization appended, one rejected |
| Concurrent claim | Passed; one mutation-capable claim, one stale loser |
| Failed predecessors | Passed; remained immutable before and after claim/replay |
| Authorization source/reason/time/additional field | **Failed:** mutation-capable successor remained |
| Duplicate/additional authorization records | **Failed:** mutation-capable successor remained |

Owner regression selection: `54 passed, 72 deselected`. Independent artifact: `/home/azureuser/.copilot/session-state/22fb1c6e-0d30-4860-be00-bd605f2908c8/files/fry_rv008_conformance.py`.

### Full Validation

| Command or gate | Result |
|---|---|
| Focused correction suite | `171 passed in 9.41s` |
| Locked contract suite | `846 passed, 1 warning in 61.73s` |
| Initial full suite | `1 failed, 3175 passed, 2 skipped, 2 deselected, 1 warning`; documented stale Compose recorder image only |
| Compose rebuild and focused fanout | `1 passed in 14.46s` |
| Final full suite | `3176 passed, 2 skipped, 2 deselected, 1 warning in 87.86s` |
| Ruff, format, compile, exact diff safety, deleted-test check | Passed; `192 files already formatted` |
| Bicep build | Passed with the existing BCP318 warning |
| Exact Bicep Checkov | Documented baseline retained: `36 passed, 7 failed` |
| CI-equivalent Bicep Checkov | `34 passed, 0 failed` |
| Dockerfile Checkov baseline | Passed |
| Review container | `sha256:5fe2fd6ee70a30882635e98eab2fcd62c99b1c5a4ba739e328fb96ec1c765049` |
| Container smoke | UID `999`; ffmpeg/ffprobe and pipeline/outbox imports passed; unconfigured distribution worker exited `2` |
| Exact added-line secret/PII scan | `NO_SUSPECTED_SECRETS_OR_PII` |

No implementation, test, assertion, quality gate, security gate, issue, review thread, deployment, canary, or production state was changed.

### GitHub and Residual State

* #684 remains open, draft, mergeable/CLEAN, and blocked. It is not approved.
* jmservera/SquadScope-Podcaster#671, jmservera/SquadScope-Podcaster#678, jmservera/SquadScope-Podcaster#679, jmservera/SquadScope-Podcaster#681, and jmservera/SquadScope-Podcaster#682 remain open and unmodified.
* jmservera/SquadScope-Coordinator#17 remains open and unmodified.
* jmservera/SquadScope#770 remains merged but does not clear P00-T01.
* P07 is not accepted. P00-T01, P05, and P06 remain open.

### Blockers and Clearing Evidence

| Blocker | Required evidence |
|---|---|
| RV-008 / P07-T01 | Complete authorization-record canonical binding; exactly one matching authorization; changed/missing/additional/duplicate/reordered/unrelated authorization rejection; preserved exact predecessor/successor positive path |
| P07-T07 | Fresh independent acceptance of the corrected final pushed SHA |
| P00-T01 | Exact W39 upstream blocked-stage prevention/detection and durable dispatch-to-Azure evidence |
| P05 | Accepted final-SHA review, merge/release provenance, authorized deployment, provider canary, alert fire/clear, and rollback evidence |
| P06 | Four consecutive future post-fix cycles with complete authoritative external proof |

---

## Fresh Independent Livingston-Revision Review — Frank — 2026-09-22

### Reviewer Identity, Independence, and Exact Boundary

* Reviewer: **Frank, Integration Engineer**.
* Independence: Frank did not author, advise, pair, or contribute to Livingston's revision. Livingston was the sole author. Bender, Hermes, Amy, Leela, Fry, Farnsworth, Rusty, Basher, and Ralph did not author, advise, pair, or contribute.
* Exact comparison: `fa3426fa030193e89a58cdb927c81a360df24a03..f3c5e643d9068a83e87bd2ef6c8ac120d312519f`.
* Source focus: Livingston commit `829fae69c4f20da18d34bae15f53c1cb21794808`.
* Post-source scope: `829fae6..f3c5e64` changes only plan/details/changes/PR narrative; no executable source or tests changed after Livingston's source commit.
* Opening state: clean worktree; local, origin, and PR head all matched `f3c5e643d9068a83e87bd2ef6c8ac120d312519f`; the comparison base was the merge base; remote divergence was `0/0`.
* PR state: #684 open, draft, mergeable/CLEAN, 13 successful checks, no submitted reviews, and zero review threads.

### Frank Final Verdict

**Not accepted (`request_changes`).** Severity: 0 Critical, 1 High, 0 Medium, 0 Low. Livingston closes the wrong-operation, intent/receipt identity, provider swap, receipt cardinality, provider item/readback, successor, legacy, and serialization gaps, but RV-008 still fails the complete immutable attempt-history contract.

### Finding Dispositions

| Finding | Frank disposition |
|---|---|
| RV-002 | Remains resolved; focused, locked, and full regression suites pass. |
| RV-003 | Remains resolved; telemetry/deployment regressions, Bicep, and CI Checkov pass. |
| RV-004 | Remains resolved; bounded cleanup and reference-fencing regressions pass. |
| RV-007 | Remains resolved at tracking level after this append-only reconciliation; all prior cycles remain intact. |
| RV-008 | **High, open.** Authorization v2 binds prior attempt IDs, not the complete ordered durable content of earlier attempts. |
| RV-009 | Remains resolved; proof-envelope and four-cycle regressions pass. |

### High Finding: Complete Earlier Attempt Evidence Is Not Bound

`exact_recovery_authorization_evidence()` records `prior_attempt_ids` for prior history. It does not record or digest the complete ordered durable attempt records or their event owner/execution/fence/timestamp/order evidence. `_validated_recovery_authorization_evidence()` consequently recomputes the same IDs after earlier attempt evidence is mutated.

Independent exact reproduction:

```text
ROUND_TRIP_EQUAL True
ATTEMPTS_BEFORE 3
RV008_COMPLETE_HISTORY_MUTATION_BYPASS {"attempts": 3, "bound_prior_attempt_ids": ["<first>", "<second>"], "mutated_event": {"at": "1999-01-01T00:00:00Z", "execution_id": "forged-earlier-owner", "fencing_token": 999999, "sequence": 2, "state": "claimed"}, "read_only": false}
```

The probe created two terminal attempts, authorized a third exact successor, then mutated the first attempt's durable claimed event. The successor remained mutation-capable. This violates the required immutable complete-history binding and the explicit rule that mutated historical evidence fails closed.

Required correction: serialize and recompute a canonical complete ordered attempt-history binding sufficient to detect missing, extra, duplicate, reordered, swapped, or mutated earlier attempt evidence, including event ordering and owner/execution/fence/timestamp fields. Retain the current exact provider kind/item, operation name/type, intent ID, receipt ID, predecessor, provider evidence, publication/digests/artifact, readback, and successor bindings. Prove the exact positive recovery remains accepted only once and all predecessor attempts remain immutable.

### Independent Probes

| Probe | Result |
|---|---|
| Wrong operation / operation type | Denied |
| Mutated intent ID / receipt ID | Denied |
| Cross-provider receipt swap | Denied |
| Missing, extra, duplicate, partial, stale, malformed, or wrong-bound receipt | Denied |
| Consumed owner/fence/time and receipt timestamp/fence mutation | Denied for the bound predecessor evidence |
| Provider item, terminal readback state/item, and duplicate readback | Denied |
| Stale predecessor, wrong successor, and non-latest authorization | Denied |
| Week/publication/manifest/digest/artifact and authorization binding mutation | Denied |
| Omitted/reordered prior attempt IDs | Denied |
| Legacy-v1 evidence | Denied |
| Replay against another successor/attempt | Denied |
| JSON serialization round trip | Exact equality preserved |
| Exact positive recovery | Accepted for the specifically bound successor only; existing immutable-predecessor regression passes |
| Earlier attempt event owner/fence/timestamp mutation | **Failed safety: successor remained `read_only=False`** |

### Independent Validation

| Command or gate | Result |
|---|---|
| Focused correction suite | `133 passed` |
| Locked contract suite | `808 passed, 1 warning` |
| Initial full suite | `1 failed, 3137 passed, 2 skipped, 2 deselected, 1 warning`; known stale Compose recorder image only |
| Compose rebuild and final full suite | `3137 passed, 3 skipped, 2 deselected, 1 warning` |
| Ruff, format, compile, exact diff safety, deleted-test check | Passed |
| Bicep build | Passed with the existing BCP318 warning |
| Exact Checkov | Existing baseline reproduced: `36 passed, 7 failed` |
| CI-equivalent Checkov | `34 passed, 0 failed` |
| Baseline-aware Dockerfile Checkov | Passed |
| Review container | `sha256:da9825c04e9248453e5925c02367e52d1db62726f50e035c2cd8176f4a37f2a3` |
| Container smoke | UID `999`; ffmpeg/ffprobe and pipeline/outbox imports passed; unconfigured distribution worker exited `2` |
| Exact-diff secret/PII scan | No suspected private key, access key, JWT, signed credential URL, or email-address pattern |

No source, test, assertion, validation gate, security gate, issue, review thread, deployment, canary, or production state was changed.

### GitHub State, Blockers, and Relationships

* #684 remains open, draft, mergeable/CLEAN, with 13 successful checks, no reviews, and zero review threads at the reviewed SHA. It is not approved.
* P07-T01 and P07-T07 remain open because RV-008 is High. P07-T06 validation remains complete.
* P00-T01 remains owned by `jmservera/SquadScope`; P05 remains delivery/deployment work; P06 remains four elapsed future production cycles.
* jmservera/SquadScope-Podcaster#682, jmservera/SquadScope-Podcaster#671, jmservera/SquadScope-Podcaster#678, jmservera/SquadScope-Podcaster#679, jmservera/SquadScope-Podcaster#681, and jmservera/SquadScope-Coordinator#17 remain open and unmodified.
* jmservera/SquadScope#770 remains merged but does not clear P00-T01.

No merge, readiness, deployment, canary, elapsed-cycle, or production acceptance is claimed.

---

## Ralph Exact-Receipt Revision Review — Livingston — 2026-09-22

### Reviewer Independence and Exact Boundary

* Reviewer: **Livingston, QA / Verification**.
* Independence: Livingston did not author Ralph's revision and received no contribution or advice from Bender, Hermes, Amy, Leela, Fry, Farnsworth, Frank, Rusty, Basher, or Ralph during review.
* Exact comparison: `21a3fa0da9f3a6752d96e1f6db17386e8dabaf6e..e16963243973707ea2557f75f925d3c6935d49ee`.
* Opening repository state: clean worktree; local branch, remote branch, and PR head all at `e16963243973707ea2557f75f925d3c6935d49ee`; comparison base is the merge base; divergence `0/0`.
* PR state at opening: #684 open, draft, mergeable/CLEAN, 13 successful checks, no reviews, and zero review threads.
* Review boundary: exact receipt cardinality, operation identity, provider-specific completeness, duplicate normalization, stale evidence, time/fence binding, serialization compatibility, races, prior RV regressions, and delivery evidence.

### Livingston Verdict

**Not accepted (`request_changes`).** Ralph's revision closes the zero-receipt, duplicate-receipt, partial-provider, wrong-item, wrong-provider, wrong-fence, malformed-receipt, and stale-history paths covered by the owner matrix. RV-008 remains **High** because the recovery path accepts any non-empty intent operation and the durable authorization omits the consumed intent/receipt identity. Replacing the implicated YouTube intent operation with an unrelated operation still authorizes a third mutation-capable attempt. Severity: 0 Critical, 1 High, 0 Medium, 0 Low.

### Finding Dispositions

| Finding | Livingston disposition |
|---|---|
| RV-002 | Remains resolved; reservation single-winner, lease recovery, and stale-owner fencing regressions pass. |
| RV-003 | Remains resolved; telemetry/deployment tests, Bicep, and CI-equivalent Checkov pass. |
| RV-004 | Remains resolved; legacy migration, incomplete-scan fail-closed behavior, and concurrent-reference regressions pass. |
| RV-007 | Remains resolved at the tracking level after this review-only reconciliation; all earlier rejection cycles remain intact. |
| RV-008 | **High, open.** Receipt cardinality and item/fence/time checks improved, but exact operation identity and durable intent/receipt binding are not enforced. |
| RV-009 | Remains resolved; proof-envelope rebinding and four-cycle regressions pass. |

### RV-008 High: Wrong Operation Still Authorizes Mutation

`_recovery_predecessor_provider_evidence()` requires only a truthy `intent["operation"]`. It does not verify the operation against an expected provider mutation or include the operation, intent ID, receipt ID, receipt fence, or receipt timestamp in the durable recovery authorization evidence. The owner test labelled `wrong-operation-fence` changes only the fence and does not probe the operation value.

Independent reproduction:

```text
RV008_WRONG_OPERATION_BYPASS read_only=False attempts=3 operation=unrelated_read_only_probe
```

The probe created the latest `provider_unknown` attempt with one otherwise exact accepted receipt per provider, recorded exact failed-terminal readbacks, then changed the persisted YouTube intent operation to `unrelated_read_only_probe`. `exact_recovery_authorization_evidence()` and `authorize_recovery()` accepted it, appended a third attempt, and `claim()` returned `read_only=False`.

This violates the target requirement that recovery require the exact consumed operation and exact durable receipt for every implicated provider mutation. Required clearing evidence: define and validate the expected operation for each provider leg; bind provider, operation, intent ID, receipt ID, item, attempt, fence, consumed/receipt times, publication/week/digests, and artifact into the authorization evidence/digest; reject a wrong or substituted operation before any successor is appended or mutation authority is granted. Preserve provider-specific intent/receipt legs rather than reusing loop-final values when constructing resolved evidence.

### Required and Regression Probes

| Probe | Result |
|---|---|
| Zero receipt | Denied; successor not appended and takeover remains read-only |
| Exactly one receipt with exact item/readback | Accepted only for the specifically authorized continuation |
| Duplicate identical receipt | Denied |
| Partial provider receipts when both providers are implicated | Denied |
| Conflicting receipt/item candidate | Denied |
| Wrong provider item or provider kind | Denied |
| Wrong fence, malformed receipt ID/timestamp, or non-accepted transport | Denied |
| Duplicate authoritative readback | Denied |
| Rusty's different-item bypass | Denied |
| Older predecessor, stale authorization, omitted history, or reordered history | Denied |
| RV-002/RV-003/RV-004/RV-007/RV-009 focused regressions | Passed |
| Wrong consumed operation | **Failed safety:** third attempt appended and claim returned `read_only=False` |

### Independent Validation

| Command or gate | Result |
|---|---|
| Required adversarial/regression selection | `40 passed, 35 deselected in 3.43s` |
| Focused correction suite | `120 passed in 4.73s` |
| Locked targeted contract | `795 passed, 1 warning in 59.10s` |
| Full repository suite | `3124 passed, 3 skipped, 2 deselected, 1 warning in 79.92s`; no Compose rebuild was needed |
| Ruff check and format | Passed; `192 files already formatted` |
| Python compile and exact diff safety | Passed; no deleted tests |
| Bicep build | Passed with the documented pre-existing BCP318 warning |
| Exact Bicep Checkov | Documented baseline retained: `36 passed, 7 failed` |
| CI-equivalent Bicep Checkov | `34 passed, 0 failed` |
| Dockerfile Checkov baseline | Passed |
| Container build | `sha256:30c8be5535617b86db502c8ab6feb8399ffff2b790a7c528d373b2e96b4ab5a0` |
| Container smoke | UID `999`; ffmpeg/ffprobe and pipeline/outbox imports passed; unconfigured distribution worker exited `2` |
| Exact-diff suspected secret/PII scan | No private key, access key, JWT, signed credential URL, or email-address pattern found |

No source, test, assertion, validation gate, security gate, baseline, issue, review thread, deployment, canary, or production state was modified by this review.

### GitHub and Residual Gates

* #684 remains open, draft, mergeable/CLEAN, and blocked; its 13 checks are successful, but checks do not override RV-008.
* jmservera/SquadScope-Podcaster#671, jmservera/SquadScope-Podcaster#678, jmservera/SquadScope-Podcaster#679, jmservera/SquadScope-Podcaster#681, jmservera/SquadScope-Podcaster#682, and jmservera/SquadScope-Coordinator#17 remain open.
* jmservera/SquadScope#770 remains merged and does not clear P00-T01.
* P07-T01 remains open. P07-T07 records a completed independent review with rejection, not acceptance.
* P00-T01, P05, and P06 remain open. No merge, deployment, canary, four-cycle completion, or production acceptance is claimed.

## Ralph Fresh Independent Final-SHA Review — 2026-09-22

### Independence and Exact Boundary

* Reviewer: Ralph.
* Independence: Ralph did not author Basher's revision and received no contribution or advice from Bender, Hermes, Amy, Leela, Fry, Farnsworth, Livingston, Frank, Rusty, or Basher.
* Comparison: `97c9520b7c365b078a50df113154bb1e66b1ecbb..fcfa40015ed68d9e38d8432425b7cbd15171e835`.
* Source commit: `eaaac5706985d0df4058f46d25e4aa4d9217f41e`.
* Final reviewed head: `fcfa40015ed68d9e38d8432425b7cbd15171e835`.
* Ancestry and divergence: comparison base is the merge base; local, origin, and PR head matched with divergence `0/0`; the worktree was clean before review tracking updates.
* Post-source changes: `cef1aab` and `fcfa400` changed tracking/PR narrative only; no executable source or tests changed after `eaaac570`.

### Ralph Verdict

**Not accepted (`request_changes`).** Basher closes Rusty's different-item bypass, but RV-008 remains **High** because exact-item failed-terminal readback can authorize a new mutation-capable attempt when the latest `provider_unknown` attempt has no durable provider receipt. Severity: 0 Critical, 1 High, 0 Medium, 0 Low.

### Finding Dispositions

| Finding | Ralph disposition |
|---|---|
| RV-002 | Remains resolved; atomic reservation, stale-owner fencing, and race probes pass. |
| RV-003 | Remains resolved; telemetry/deployment regressions, Bicep, and CI Checkov pass. |
| RV-004 | Remains resolved; bounded cleanup and reference-fencing regressions pass. |
| RV-007 | Remains resolved at tracking level; all rejection cycles and exact author/reviewer identities are preserved. |
| RV-008 | **High, open.** Different-item, missing identity, duplicate, conflict, wrong-kind, stale receipt, stale authorization, wrong binding, and history tampering fail closed, but missing receipt does not. |
| RV-009 | Remains resolved; four-cycle proof-envelope regressions pass. |

### High Finding: Missing Provider Receipt Authorizes Recovery

`_recovery_predecessor_provider_evidence()` requires `receipts` to be a list but does not require it to contain an exact provider receipt. The repository's accepted exact-match fixture consumes each mutation intent, records no receipt, terminates `provider_unknown`, and later accepts an exact-item failed-terminal readback. Authorization then appends a third attempt and `claim()` returns mutation authority.

Exact reproduction:

```text
RV008_MISSING_RECEIPT_BYPASS receipts={'youtube': 0, 'spotify': 0} attempts=3 read_only=False
```

This violates the authoritative requirement that resolution be bound to durable consumed intent, receipt, and provider evidence. An expected item in intent is not evidence that the provider accepted, rejected, or identified the mutation. Required correction: each provider must have exactly one usable, non-ambiguous receipt bound to the consumed intent, provider kind, and expected provider item before exact failed-terminal readback can authorize the specifically bound successor. Missing or duplicate receipts must fail closed.

### Independent Probes

| Probe | Result |
|---|---|
| Rusty different-item reproduction | Denied; no successor authorization |
| Exact matching item | Accepted only under existing history/item bindings, but exposes the missing-receipt High finding |
| Missing identity | Denied |
| Duplicate readback identity | Denied |
| Conflicting provider items | Denied |
| Provider-kind mismatch | Denied |
| Stale receipt / wrong intent | Denied |
| Wrong week/publication/digest/artifact/binding fields | Denied by structured authorization regressions |
| Stale authorization / non-latest predecessor | Denied |
| Reordered or omitted terminal history | Denied |
| Scheduler race and fencing | Passed; one fenced winner and stale-owner rejection |
| Missing provider receipt | **Failed safety:** successor created with `read_only=False` |

### Independent Validation

| Command or gate | Result |
|---|---|
| Focused RV regression selection | `29 passed, 39 deselected` |
| Focused correction suite | `113 passed` |
| Locked contract suite | `788 passed, 1 warning` |
| Initial full suite | `1 failed, 3117 passed, 2 skipped, 2 deselected, 1 warning`; stale Compose recorder image only |
| Rebuilt Compose recorder and fanout probe | `1 passed` |
| Final full suite | `3118 passed, 2 skipped, 2 deselected, 1 warning` |
| Ruff, format, compile, diff safety, deleted-test check | Passed; `192 files already formatted` |
| Bicep build | Passed with existing BCP318 warning |
| Exact Checkov | Existing baseline reproduced: `36 passed, 7 failed` |
| CI-equivalent Checkov | `34 passed, 0 failed` |
| Dockerfile Checkov baseline | Passed |
| Review container | `sha256:5baa9f5828ab055362ba8e5b25d3fae388ab5cb85c5029c335cce4d79f2ccf10` |
| Container smoke | UID `999`; ffmpeg/ffprobe and pipeline imports passed; unconfigured distribution worker exited `2` |
| Suspected secret/PII scan | No private key, access key, JWT, signed credential URL, email-address pattern, or raw PII found |

No test, assertion, quality gate, or security gate was weakened.

### GitHub and Residual Gates

* #684: open, draft, PR head matched reviewed head, mergeable/CLEAN, 13 successful checks, no reviews, and zero review threads before this tracking-only update. It remains blocked and is not approved.
* jmservera/SquadScope-Podcaster#682, jmservera/SquadScope-Podcaster#671, jmservera/SquadScope-Podcaster#678, jmservera/SquadScope-Podcaster#679, jmservera/SquadScope-Podcaster#681, and jmservera/SquadScope-Coordinator#17 remain open.
* jmservera/SquadScope#770 remains merged but does not clear P00-T01.
* RV-008/P07-T01 remains owned by the Podcaster revision owner and requires exact durable receipt binding plus fresh independent final-SHA review.
* P00-T01 remains owned by `jmservera/SquadScope`; P05 remains owned by delivery/deployment; P06 remains owned by production verification after four elapsed cycles.

No issue, review thread, deployment, canary, or production state was mutated.

---

## Fresh Independent Frank-Revision Review — Rusty — 2026-09-22

### Reviewer Identity, Independence, and Exact Boundary

* Reviewer: **Rusty, Lead / Orchestration**.
* Independence: Rusty did not author the revision. Frank was the sole current revision author. Bender, Hermes, Amy, Leela, Fry, Farnsworth, Livingston, and Frank did not advise, pair, or contribute during this review.
* Exact comparison: `1b057c0ea9073fb195c56cc884625216e18e49a7..1efa74956e23b51512b7eff1ded4e809b79566e1`.
* Source focus: Frank commit `502807d562996ecf6c8cd4213afd4cdf454aa5c3`.
* Opening repository state: clean worktree; local branch, remote branch, and PR head all at `1efa74956e23b51512b7eff1ded4e809b79566e1`; both comparison base and source commit are ancestors; remote divergence `0/0`.
* Post-source scope: `502807d..1efa749` changes only the changes record and PR narrative; no source or test changed after Frank's source commit.
* PR state at opening: #684 open, draft, merge state `CLEAN`, mergeable, 13 successful checks, no reviews, and no review threads.
* Incident truth retained: W39 is `missed_not_dispatched`. W38 was published, but `published_verified_recovered` remains conditional on exact identity-bound authoritative provider readback and safe recovery authorization.

### Rusty Final Verdict

**Not accepted (`request_changes`).** Frank's revision resolves Livingston's branch-around-later-attempt defect, and RV-002, RV-003, RV-004, RV-007, and RV-009 remain resolved. RV-008 remains **High** because the latest-unknown readback is not bound to the provider item identity implicated by that unknown attempt. A failed readback for a different provider item authorizes a new mutation-capable attempt. Severity: 0 Critical, 1 High, 0 Medium, 0 Low.

### Finding Dispositions

<!-- rpi:review-rusty id=RV-001 -->
#### RV-001 [High, remains resolved]: read-only YouTube promotion takeover convergence

* Evidence: no source change reopens the previously verified authoritative-readback convergence path; focused and full regression suites pass.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-002 -->
#### RV-002 [High, remains resolved]: expired reconciliation reservations recover with one fenced owner

* Evidence: owner regression coverage remains present and the locked suite passes.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-003 -->
#### RV-003 [High, remains resolved]: emitted and deployed alert contracts remain aligned

* Evidence: no source change affects telemetry or Bicep alert semantics; telemetry/deployment regressions, Bicep build, and CI-equivalent Checkov pass.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-004 -->
#### RV-004 [High, remains resolved]: legacy reference migration and cleanup remain fail-closed and fenced

* Evidence: no source change affects cleanup; locked and full regressions pass.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-005 -->
#### RV-005 [Medium, remains resolved]: missing provider identity remains fail-closed

* Evidence: unknown provider identity remains non-green and reconciliation-only.
* Disposition: Remains resolved.

<!-- rpi:review-rusty id=RV-006 -->
#### RV-006 [High, remains resolved at planning level]: closure inventory retained

* Evidence: historical issue/thread inventory remains present; no issue or thread is represented as resolved by this review.
* Disposition: Planning-level resolution remains; P05 execution is open.

<!-- rpi:review-rusty id=RV-007 -->
#### RV-007 [Medium, remains resolved]: rejection history and current ownership remain explicit

* Evidence: Leela/Fry and Farnsworth/Livingston rejection history is preserved; this section appends Frank/Rusty evidence without rewriting prior cycles.
* Disposition: Remains resolved by tracking-only reconciliation.

<!-- rpi:review-rusty id=RV-008 -->
#### RV-008 [High, open]: latest-unknown readback is not bound to the unknown attempt's provider item

* Evidence: the latest attempt consumed mutation intents for `youtube-unknown` and `spotify-unknown`, then terminated `provider_unknown`. Read-only reconciliation recorded authoritative-looking `failed_terminal` readbacks for different item identities, `youtube-DIFFERENT-ITEM` and `spotify-DIFFERENT-ITEM`. `exact_recovery_authorization_evidence()` accepted those readbacks, `authorize_recovery()` appended a third attempt, and `claim()` returned `read_only=False`.
* Root cause: `_recovery_predecessor_provider_evidence()` requires a non-empty provider item, native state, failed result, and source containing `readback`, but does not require the post-terminal readback item to equal the unknown attempt's persisted expected provider item, mutation receipt identity, or prior observed identity. `_validated_recovery_authorization_evidence()` then self-consistently validates the mismatched readback rather than rebinding it to the unknown mutation.
* Impact: authoritative readback about an unrelated failed provider item can be used to declare the actual unknown mutation safe and restore mutation authority. This violates the exact readback-binding and no-new-mutation contract.
* Required clearing evidence: bind each post-terminal failed readback to the latest unknown attempt's exact persisted provider identity/intent/receipt; reject missing, conflicting, duplicate, or different item identities; prove the mismatched-item reproduction cannot append or claim a succeeding attempt while exact matching readback still permits only the specifically authorized continuation.

<!-- rpi:review-rusty id=RV-009 -->
#### RV-009 [High, remains resolved]: four-cycle proof envelopes remain rebound and exact

* Evidence: focused and full regressions pass; no source change affects the four-cycle evaluator.
* Disposition: Remains resolved.

### Independent Probes

| Target | Probe | Result |
|---|---|---|
| RV-008 older authorization | Failed terminal, authorized successor becomes `provider_unknown`, reuse older predecessor | Passed: stale older predecessor is rejected and takeover remains read-only |
| RV-008 latest exact readback owner test | Matching failed-terminal readback of latest unknown, fresh authorization, succeeding claim | Passed |
| RV-008 stale/non-latest authorization | Select a non-latest predecessor | Passed: rejected |
| RV-008 omitted/reordered history | Remove or reorder terminal attempt IDs | Passed: rejected |
| RV-008 exact safe recovery | Failed predecessor plus exact authorized succeeding provider identities | Passed: immutable failed attempt retained and recovered state accepted |
| RV-008 readback identity binding | Unknown intent for `*-unknown`, then failed readback for `*-DIFFERENT-ITEM` | **Failed safety:** `RV008_READBACK_BINDING_BYPASS read_only=False attempts=3` |
| RV-002/RV-003/RV-004/RV-007/RV-009 regressions | Focused, locked, and full suites | Passed |

The ordered list and latest-predecessor checks reject stale, omitted, and reordered histories. Atomic storage updates fence concurrent authorization writers. Attempt timestamps are descriptive rather than the ordering authority. Generated attempt IDs are unique in normal repository operations, but the safe readback decision still fails because it is not bound to the exact provider item of the latest unknown attempt.

### Independent Full Validation

| Command | Result |
|---|---|
| Required RV-008 focused selection | `15 passed, 47 deselected` |
| Focused correction suite | `107 passed in 3.18s` |
| Locked targeted contract | `782 passed, 1 warning in 56.02s` |
| Initial full `pytest tests/ -q` | `1 failed, 3111 passed, 2 skipped, 2 deselected, 1 warning`; stale Compose recorder image only |
| Compose rebuild plus focused fanout integration | `1 passed in 13.39s` |
| Final full `pytest tests/ -q` | `3112 passed, 2 skipped, 2 deselected, 1 warning in 82.67s` |
| Ruff, format, compile, and exact diff safety | Passed; `192 files already formatted` |
| Bicep build | Passed with pre-existing BCP318 warning |
| Exact Checkov | Documented baseline retained: `36 passed, 7 failed` |
| CI-equivalent Bicep Checkov | `34 passed, 0 failed` |
| Dockerfile Checkov baseline | Passed |
| Container build | `sha256:72257821fdc2c45de68d98857a35d8d2f72fced688c829d75dccfe651d38d37b` |
| Container smoke | UID `999`; ffmpeg/ffprobe and pipeline imports passed; unconfigured distribution worker exited `2` |
| Changed diff secret/PII scan | No suspected secret, credential value, private key, signed credential URL, JWT, email address, or raw PII identified |

No test, assertion, validation gate, security gate, or baseline was weakened.

### GitHub and External Relationships

* #684: open, draft, merge state `CLEAN`, mergeable; 13 successful checks; no reviews or review threads. It remains draft/blocked and is not approved by this review.
* jmservera/SquadScope-Podcaster#682: open, non-draft; not superseded, closed, merged, approved, or otherwise mutated by this review.
* jmservera/SquadScope-Podcaster#671: open; Spotify/manual-handoff dependency remains.
* jmservera/SquadScope-Podcaster#678: open; YouTube ambiguous-create identity reconciliation remains.
* jmservera/SquadScope-Podcaster#679: open; Spotify exact reconciliation remains.
* jmservera/SquadScope-Podcaster#681: open; atomic outbox worker remains the parent implementation relationship.
* jmservera/SquadScope#770: merged; it does not clear the still-open P00-T01 evidence requirement recorded by this plan.
* jmservera/SquadScope-Coordinator#17: open.

### Blockers and Clearing Evidence

| Blocker | Owner | Evidence required to clear |
|---|---|---|
| RV-008 / P07-T01 | Podcaster revision owner | Exact latest-unknown provider-item binding for post-terminal readback; mismatched-item rejection; exact-match safe continuation; focused and full validation |
| P00-T01 | `jmservera/SquadScope` owner | Durable evidence that the W39 blocked dispatch stage and recurrence controls are implemented and verified |
| P05 | Delivery/deployment owners | Accepted final-SHA review, approval/merge provenance, authorized deployment, provider canary, alert fire/clear, and rollback evidence |
| P06 | Production verification owner | Four consecutive future post-fix cycles with complete authoritative external proof |

No issue, PR, review thread, deployment, canary, or production state was resolved, closed, or mutated by this review.
