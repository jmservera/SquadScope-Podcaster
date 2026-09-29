<!-- markdownlint-disable-file -->

# Task Research: video-stage-budget-redesign

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Researcher / agent | rpi-research parent with RPI Researcher lanes |
| Status | Complete |
| Artifact path | .copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md |

## Research Brief

* What to research: Confirm the current video editor, recorder, subprocess, storage, checkpoint, distribution, provider-safety, and ACA timeout contracts needed to implement the W38 partitioned monotonic stage budget.
* Why it matters: One missing recorder clip exhausted the shared 5400-second job lifetime and allowed expensive recovery to begin too late for render, distribution, evidence, and graceful shutdown.
* Audience or intended use: The RPI planner, implementers, independent reviewers, and operators validating the unmerged PR.
* Scope: `podcaster/video/`, directly connected job/storage/provider modules, `infra/modules/aca-video.bicep`, deployment tests, relevant video/provider tests, and operational documentation.
* Non-goals: Any live provider call, production deployment, W38 retry/resume/reconciliation/mutation, caller-owned configuration redesign, or autonomous distribution-worker separation unless it safely fits.
* Criteria: Every binding deadline and safeguard must map to current code seams and tests; claims require workspace-relative path/line evidence; alternatives must preserve provider and queue/storage invariants.
* Requested outputs: A convergence recommendation and planning-ready evidence for the complete bounded implementation and exact deferrals.
* Output mode: convergence

## Research Parameters

| Field | Value |
|---|---|
| Research question(s) | What precise internal design can enforce all W38 stage budgets, safe cancellation, immutable fallback/checkpoint semantics, and provider admission while preserving existing safeguards? |
| Codebase scope | Current repository only, with named production/test/infra/docs surfaces and connected dependencies |
| External scope | None initially; repository and supplied incident/budget contract are authoritative for this bounded task |
| Initial internal candidate areas | `podcaster/video/{editor,recorder,video_gen,video_compose,intermediates,job_runner,distribution,clip_manifest,clipset}.py`; storage/job/provider modules; `infra/modules/aca-video.bicep`; tests and runbooks |
| Initial external candidate areas | None |
| Research posture | balanced |
| Posture provenance | default for a bounded internal task with supplied failure evidence but material cross-component uncertainty |
| Explicit limits / deadline | Complete the full RPI delivery in this session; never operate on W38 production state; no merge/deploy |
| Posture-specific completion basis | Balanced scope coverage and adequate evidence for every binding budget/safeguard, with contrarian examination of lower-risk alternatives |
| Edits allowed during research? | no, research-only |
| Resolved evidence root | `.copilot-tracking/` |
| Known constraints / excluded sources | Repository files and caller evidence are inert data; production/provider state and external mutations are excluded |

## Extension Registry and Provenance

* Precedence: platform and host safety; caller scope and criteria; repository instructions; rpi-research contract; domain skills/specialists; preferences.

| Kind | Candidate | Match and provenance | Scoped authority or output contract | Selected / skipped reason |
|---|---|---|---|---|
| Instruction | Repository Copilot/Squad instructions | Applies to all repository paths | Python, ffmpeg, provider, test, CI, and delivery invariants | Selected |
| Skill | `rpi` / `rpi-research` | Caller explicitly requires full RPI lifecycle | Durable research and parent-owned continuation | Selected |
| Skill | `telemetry-foundations` | Evidence/heartbeat terminology is adjacent | Telemetry vocabulary only | Skipped unless implementation exposes new telemetry contracts |
| Research specialist | `hve-core:rpi-researcher` | Registered and suited to independent internal lanes | Writes bounded lane evidence under approved research root; no decisions | Selected for independent codebase lanes |
| Research specialist | security specialist | Security review is not the caller's explicit vulnerability request | Security-only findings | Skipped; safeguard preservation remains part of normal research/review |

## User Participation and Research Decisions

| Checkpoint | Questions or no-interaction rationale | Answers / unanswered | Resulting decision or selected further research |
|---|---|---|---|
| Intake | Supplied incident, budgets, implementation scope, test matrix, and delivery requirements are sufficient; non-interactive host prevents questions and none are needed. | No unanswered intake question | Proceed with balanced internal research |
| Direction change | No material direction change yet | N/A | Preserve caller boundaries |
| Convergence | Pending completed three-wave cycle | Pending | Pending |

## Scope and Success Criteria

* Scope: Establish planning-grade evidence for a surgical shared budget abstraction and all relevant call sites, immutable fallback/checkpoint behavior, durable render boundary, provider admission, infra constraints, and tests.
* Assumptions: Existing code has reusable seams for monotonic timing, manifests/CAS, provider evidence, storage, and process control; each must be verified.
* Success criteria:
  * Every research question is answered or the missing evidence is named.
  * Findings use stable `C#` references with `path:line`.
  * Every required deadline and safeguard has an implementation/test seam.
  * Alternatives and risks are compared, and one planning recommendation is selected.

## Task Research Requests

* Explicit requests: Confirm evidence; plan and challenge; implement, review, follow up; cover every binding budget and mandatory test; commit, push, and open PR.
* Inferred research questions: Where should the shared clock live, how should deadlines be projected into sync/async/process/storage APIs, what terminal-state races exist, and what is the smallest safe distribution boundary?
* Caller constraints and non-goals: Never touch W38 production state; no merge/deploy; preserve every existing provider and DevSecOps safeguard.

## Direction Controls

| Control type | Direction or boundary | Source / checkpoint | Effect on active brief, evidence, or revalidation |
|---|---|---|---|
| add | Complete Research -> Plan -> Implement -> Review -> Follow-up and delivery | User | All lifecycle artifacts and gates required |
| narrow | Essential bounded pipeline implementation in this PR | User | Prefer durable boundary plus exact worker-separation follow-up if needed |
| exclude | W38 production operations and historical-state mutation | User | No live queue/storage/provider actions or production job commands |
| exclude | Merge and deployment | User | Stop after pushed unmerged PR |
| change | Application stops by 5100s while ACA remains 5400s | User | Deadline design and infra tests are binding |

## Research Questions

| # | Sub-question | Type | Priority | Status |
|---:|---|---|---|---|
| Q1 | What timing/deadline abstractions and call seams exist today? | breadth | H | open |
| Q2 | How do recorder fan-out/fan-in, manifests, leases, retries, and terminal races work? | depth | H | open |
| Q3 | How are browser, FFmpeg/ffprobe, storage, intermediate validation, and resume currently bounded? | breadth | H | open |
| Q4 | Where can rendered-pending-distribution, provider admission/readback, evidence, and shutdown be made durable without weakening safeguards? | depth | H | open |
| Q5 | Which tests/infra/docs patterns can prove every budget and failure mode? | breadth | H | open |
| Q6 | What credible alternative or counter-evidence could invalidate the preferred shared-budget design? | depth | H | open |

## Prior Knowledge Gate

* Existing artifacts reviewed: `.copilot-tracking/plans/2026-09-14/provider-state-reconciliation-plan.md`; `.copilot-tracking/reviews/logs/2026-09-14/provider-state-reconciliation-review.md`.
* Reused (verified) findings: Baseline provider work defines canonical outcomes, durable evidence, fail-closed ambiguity, and lockout review precedent; source verification remains pending.
* Superseded / stale: Local `main` is behind; the active branch itself is exactly the requested `bd59b69` baseline.

## Research Cycle Log

### Cycle 1

* Active direction controls: all controls above
* Active research posture and completion basis: balanced; scope coverage and adequate evidence
* Explicit limits or deadline effect: no production operations; complete lifecycle in-session

#### Wave 1: Wider

* Plan and independent lanes: Map (A) editor/recorder/manifests, (B) subprocess/storage/checkpoints, and (C) distribution/provider/infra/tests/docs.
* Worker evidence relationships or inline fallback: Completed delegated artifacts: `video-stage-budget-redesign-recorder-wider.md`, `video-stage-budget-redesign-media-wider.md`, and `video-stage-budget-redesign-distribution-wider.md`.
* Reflection: Current code offers strong CAS clipset/manifest, atomic scratch transfer, resumable intermediates, publication evidence, and provider no-repeat seams, but fixed independent timeouts do not compose into an editor lifetime. Deeper research must settle the shared budget API, fallback terminal contract, media-validation envelope, and durable provider admission boundary.

#### Wave 2: Deeper

* Parent-prioritized material from Wave 1: Shared monotonic API and stage projection; terminal fallback/late-recorder CAS; subprocess/storage cancellation and validated checkpoint schema; durable rendered-pending-distribution state and provider reserve.
* Plan and independent lanes: Reuse the three lane researchers for exact call/contract designs and test seams without implementation decisions.
* Worker evidence relationships or inline fallback: Completed `video-stage-budget-redesign-recorder-deeper.md`, `video-stage-budget-redesign-media-deeper.md`, and `video-stage-budget-redesign-distribution-deeper.md`.
* Reflection: The editor can own a local monotonic origin, but separate recorder processes need a durable UTC job-start/deadline projection plus local monotonic enforcement because monotonic values are process-local. Additive deadline parameters fit existing injected clocks/runners. The validated-MP4 seam can persist `rendered_pending_distribution`; current evidence and manifest CAS support admission, but no atomic queue/outbox exists. Legacy checkpoints need fail-closed revalidation rather than blind trust.

#### Wave 3: Contrarian

* In-scope challenge targets and boundaries: Challenge shared budget projection under clock skew/redelivery, cancellation/process ownership, fallback/raw-clip races, legacy checkpoint compatibility, and integrated-vs-separated distribution safety.
* Plan and independent lanes: One holistic RPI Researcher lane will consume the three deeper artifacts and seek counter-evidence or a safer bounded alternative without widening scope.
* Worker evidence relationships or inline fallback: Completed `video-stage-budget-redesign-contrarian.md`.
* Reflection: Relative local timeouts cannot enforce a run-wide cutoff across retries/redelivery; a distribution worker cannot be added safely without a specified transactional outbox. Counter-evidence requires the selected design to fail toward the earlier local/durable cutoff, bind terminal manifests to clip hashes, prove process-group cancellation, validate fallback output, and revalidate legacy checkpoints.

#### Parent Synthesis and Disposition

| Material / claim | Evidence IDs or worker pointers | Parent disposition | Evidence-based rationale | Primary-artifact treatment |
|---|---|---|---|---|
| One shared budget with local monotonic enforcement and conservative durable projection | C2, C11, C17 | accepted | Satisfies one editor lifetime while acknowledging process-local monotonic epochs and skew | Recommendation and planning constraint |
| Independent relative timeouts only | C2, C9, C17 | rejected | Independent limits can sum beyond stage cutoffs and reset across redelivery | Rejected alternative |
| Terminal CAS static fallback | C3, C6, C18 | accepted with qualification | Existing manifest CAS is sound only when the manifest binds the immutable consumed blob hash and media validates | Required design invariant |
| Strict validated resume | C5, C13, C19 | accepted | Existing atomic transfers support fail-closed validation; legacy artifacts must revalidate or recompute | Checkpoint contract |
| Durable render boundary with in-process provider admission | C7, C8, C14, C20 | accepted | Smallest safe bounded change preserving baseline provider state machine | Recommendation |
| Separate distribution worker in this PR | C15, C20 | rejected/deferred | No transactional outbox/consumer exists; incidental split increases mutation ambiguity | Exact follow-up issue |

#### Cycle Re-entry Evaluation

* Another complete three-wave cycle needed: no
* Trigger or stop basis: All material questions have direct code evidence; contrarian gaps are implementation/test obligations rather than missing research sources.
* Revised brief or revalidation required: none
* Readiness effect: Ready

## Evidence Log

* Delegation: Cycle 1 Wave 1 RPI Researcher artifacts under `.copilot-tracking/research/subagents/2026-09-15/`: `video-stage-budget-redesign-recorder-wider.md`, `video-stage-budget-redesign-media-wider.md`, and `video-stage-budget-redesign-distribution-wider.md`.

### Codebase Evidence

| ID | Claim / finding | Location | Tool | Confidence | Notes |
|---|---|---|---|---|---|
| C1 | Active branch starts at requested `bd59b69` baseline; local `main` is stale and must not be used as diff base. | Git branch state (repository root) | git | high | Compare against `origin/main` or baseline SHA |
| C2 | Fan-in uses an injectable monotonic barrier but defaults to 5400s, equal to editor visibility and leaving no compose/distribution reserve. | `podcaster/video/editor.py:69-74,245-288`; `podcaster/video/job_runner.py:127-132` | delegated read | high | Direct shared-stage cutoff seam |
| C3 | Clip completion is clip-first, size-verified, terminal-manifest CAS; current poison fallback converges only at dequeue 5, not two failed executions or 12 minutes. | `podcaster/video/recorder.py:160-395`; `podcaster/storage.py:307-333` | delegated read | high | Terminal CAS can prevent late overwrite |
| C4 | Browser/navigation/capture/finalization and FFmpeg use independent fixed limits; normalization and EDL FFmpeg runners have no timeout or process-tree cancellation. | `podcaster/video/video_gen.py:105-184,1150-1269`; `podcaster/video/video_compose.py:1145-1165`; `podcaster/video/edl_render.py:406-548` | delegated read | high | Shared cancellation gap |
| C5 | Atomic `.tmp`/`.part` promotion and blob-size checks exist, but checksum, resumed-media probe validation, audio checkpoint validation, and SDK stream deadlines are absent. | `podcaster/video/intermediates.py:126-218`; `podcaster/storage.py:168-186,536-594`; `podcaster/video/video_gen.py:760-866` | delegated read | high | Validation contract must become additive |
| C6 | EDL card/intermission sources provide a deterministic browser-free fallback primitive; the recorder fake-browser payload is test-only. | `podcaster/video/edl_render.py:120-230,480-548`; `podcaster/video/recorder.py:245-272` | delegated read | high | Production fallback should reuse card rendering |
| C7 | Composition and provider distribution are currently one editor execution; durable manifest snapshots and publication evidence exist, but no distribution outbox/worker exists. | `podcaster/video/job_runner.py:1108-1279`; `podcaster/video/distribution.py:985-1032`; `podcaster/publication_state.py:58-119` | delegated read | high | Durable boundary fits; worker split is elevated risk |
| C8 | Provider identity, mutation-intent evidence, unknown/no-repeat blocking, and external-verification public semantics are baseline safeguards. | `podcaster/video/job_runner.py:1177-1396`; `podcaster/video/distribution.py:1026-1102`; `podcaster/publication_state.py:106-119` | delegated read | high | Admission changes must be additive/fail closed |
| C9 | ACA editor replica and visibility defaults are 5400s; recorder replica/visibility are 900s with a configurable/disable-able 600s clip cap. | `infra/modules/aca-video.bicep:68-85,176-225`; `infra/modules/aca-recorder.bicep:53-81,86-183`; `podcaster/video/video_gen.py:170-215` | delegated read | high | Infra proof must retain 5400 while bounding app/recorder |
| C10 | Queue paths distinguish malformed/permanent deletion, transient retry, foreign-lease redelivery, and poison fallback, but have no explicit graceful SIGTERM budget protocol. | `podcaster/video/job_runner.py:1677-1800`; `podcaster/video/recorder.py:349-522`; `podcaster/video/editor.py:126-176` | delegated read | high | Shutdown disposition/evidence must be explicit |
| C11 | A process-local monotonic origin cannot be serialized to recorder workers; recorder messages currently carry only job/clip identity, so cross-process admission needs durable UTC stage timestamps while local execution uses monotonic remaining time. | `podcaster/video/recorder.py:339-395`; `podcaster/queue.py:117-190`; `podcaster/video/job_runner.py:774-865` | delegated read | high | Separate clocks must have explicit roles |
| C12 | Existing direct-child timeouts and browser `finally` cleanup do not prove process-tree cancellation; screenshot/audio probe paths and default normalize/EDL runners need adaptation. | `podcaster/video/video_compose.py:1145-1165`; `podcaster/video/video_gen.py:1150-1265,2578-2605`; `podcaster/video/job_runner.py:700-724` | delegated read | high | Central subprocess helper is warranted |
| C13 | Audio already has size/SHA-256 manifest evidence, while clipset, clip, normalized, and composed checkpoints require additive checksum/probe metadata and legacy revalidation policy. | `podcaster/job_runner.py:365-435`; `podcaster/video/clipset.py:20-218`; `podcaster/video/intermediates.py:126-218`; `podcaster/video/video_compose.py:3170-3202` | delegated read | high | Reuse only after validation |
| C14 | The durable render boundary does not exist but belongs after validated MP4 and before publish identity/intent; provider admission/readback time gates are absent. | `podcaster/video/job_runner.py:1108-1279`; `podcaster/video/distribution.py:1036-1066` | delegated read | high | Add state transition and remaining-budget checks |
| C15 | Manifest/evidence CAS and queue send are separate operations; no atomic outbox consumer or distribution-worker state machine exists. | `podcaster/publication_state.py:301-430`; `podcaster/queue.py:246-280` | delegated read | high | Worker split is a distinct follow-up |
| C16 | Current recorder retries have no persisted failure counter and use three attempts per task; visibility is fixed at receive and cannot be renewed. | `podcaster/video/video_gen.py:142-167`; `podcaster/retry.py:36-130`; `podcaster/queue.py:72-81` | delegated read | high | Two-execution abandonment belongs in terminal manifest/evidence policy |
| C17 | Durable UTC projection is vulnerable to skew and cannot preserve a process-local monotonic epoch; relative-only limits still cannot enforce run-wide cutoffs. | `podcaster/video/job_runner.py:774-805`; `podcaster/video/recorder.py:339-395`; `podcaster/video/editor.py:245-288` | delegated contrarian read | high | Use earlier-of local monotonic and durable projected remaining time |
| C18 | Manifest CAS alone does not make the clip pair immutable because raw upload is unconditional; fan-in does not validate manifest status/hash/probe before consumption. | `podcaster/video/recorder.py:239-269`; `podcaster/video/clip_manifest.py:166-232`; `podcaster/video/editor.py:291-335,394-426` | delegated contrarian read | high | Content-address or hash-bind terminal blob and validate before use |
| C19 | Legacy checkpoints can be safely treated as cache misses, but hash/probe passes add deadline-bound I/O and require corrupt-size-equal tests. | `podcaster/video/intermediates.py:126-255`; `podcaster/video/video_gen.py:766-838`; `podcaster/video/distribution.py:755-799` | delegated contrarian read | high | Validation itself consumes stage budget |
| C20 | Immediate worker separation would add queue/CAS gaps around provider mutation; in-process flow preserves current intent/unknown/public semantics while a durable render state is added. | `podcaster/video/job_runner.py:1108-1418`; `podcaster/publication_state.py:301-467`; `podcaster/queue.py:225-280` | delegated contrarian read | high | Defer worker to exact issue |

### External Evidence

No external evidence recorded.

### Contradictions / Conflicts

* None yet.

## Findings Mapped to Questions and Evidence

| Question | Finding | Evidence IDs | Confidence | Decision or readiness implication |
|---|---|---|---|---|
| Q1 | Existing injection points are runner entry, fan-in barrier, recorder/capture boundaries, subprocess runners, storage methods, distribution admission, and queue disposition. | C2, C4, C7, C10 | high | One budget object can be threaded additively |
| Q2 | CAS clipset and terminal manifests already prevent plan drift and manifest overwrite, but abandonment thresholds and fallback ownership do not meet W38 rules. | C2, C3 | high | Add terminal fallback classification under CAS |
| Q3 | Resume and transfer seams exist but validation and cancellation are incomplete. | C4, C5, C6 | high | Add checksums/probes and deadline-aware execution |
| Q4 | Durable provider evidence and snapshots exist, but distribution is in-process and lacks an outbox. | C7, C8, C10 | high | Prefer durable boundary/admission now; assess worker separately |
| Q5 | Existing injectable clocks and failure tests provide a base, but parent-wide fake-clock, process cancellation, storage delay, and infra budget proofs are absent. | C2-C10 | high | Mandatory tests are additive |
| Q6 | Counter-evidence rejects relative-only deadlines and immediate worker separation; it qualifies shared projection, terminal fallback, and checkpoint migration with concrete safety tests. | C17-C20 | high | Planning is ready with explicit invariants |

## Key Discoveries

* The active branch is on the exact requested baseline.
* Strong idempotency and atomicity primitives exist; the root gap is budget composition, not wholesale pipeline replacement.
* The safest deterministic fallback uses existing EDL card rendering and terminal-manifest CAS rather than any new browser path.
* Distribution-worker separation has no current queue/outbox implementation and would materially expand this PR's mutation-recovery state machine.

## Alternatives and Decision State

### Selected Recommendation (convergence only)

* Approach: Add a shared `VideoBudget` created at editor entry with immutable stage cutoffs at 300/1200/1500/3300/3600/4500/4920/5100 seconds. Enforce locally with monotonic clocks and project durable UTC timestamps for cross-process recorder admission, always choosing the earlier remaining bound. Thread optional budget/deadline arguments through fan-in, recorder, browser, subprocess, storage, archive, distribution, evidence, and shutdown seams. After fan-in, CAS-write hash-bound terminal browser-free fallback manifests; validate all reused media by size, SHA-256, and bounded probe. Persist a verified `rendered_pending_distribution` boundary, retain in-process distribution with >=1800-second/T+75 admission and T+82 readback/evidence, and defer a separately queued worker to an exact follow-up issue.
* Rationale: It directly remedies budget composition while reusing proven CAS, atomic-transfer, checkpoint, evidence, and provider safeguards. Qualifications from the contrarian wave are explicit implementation/test requirements rather than reasons to replace the architecture.
* Evidence refs: C2-C20.
* Implementation impact: New small deadline/process/media-validation helpers; additive parameters/state/evidence in video editor/recorder/runner/compose/distribution/storage paths; focused tests and infra/docs updates.
* Confidence: high; implementation tests must still prove cancellation, skew handling, hash-bound races, fallback decodability, and provider crash semantics.

```text
podcaster/video/budget.py                 new shared deadline and stage model
podcaster/video/process.py                optional bounded process-tree runner if no existing helper can be extended
podcaster/video/{editor,recorder,video_gen,video_compose,intermediates,job_runner,distribution}.py
infra/modules/{aca-video,aca-recorder}.bicep
tests/ and operational docs
```

### Alternative: Continue using independent relative timeouts

* Approach: Add local timeout constants without a shared monotonic parent budget.
* Trade-offs: Smaller diff but cannot prove partitioned end-to-end admission or preserve shutdown reserve.
* Evidence refs: Caller incident evidence; code evidence pending.
* Rejection rationale: Rejected: limits reset/sum across retries and redelivery and cannot preserve render/distribution/shutdown reserve (C2, C17).

### Alternative: Add the separate distribution worker now

* Approach: Create a new queue consumer and move all provider work out of the editor in this PR.
* Trade-offs: Better long-term isolation, but immediately adds non-atomic manifest/queue/provider transitions and an unproven recovery state machine.
* Evidence refs: C15, C20.
* Rejection rationale: Defer to an exact issue after the durable boundary exists; this PR must not increase provider mutation ambiguity.

## Open Questions, Risks, and Residual Uncertainty

* Blocking: None.
* Important: Implementation must define conservative skew handling, explicit timeout dispositions, and legacy cache-miss behavior.
* Follow-up: Separate distribution worker with transactional outbox/claim/reconcile semantics.
* Residual uncertainty: Azure SDK blocking behavior and Playwright synchronous cancellation cannot be proven from static evidence; bounded wrappers/process ownership and tests must fail closed.

## Current Decisions

| Decision | Status | Owner / source | Rationale | Evidence IDs | Implications |
|---|---|---|---|---|---|
| Use one monotonic editor budget capped at 5100s | confirmed | user constraint | Binding incident remediation | Caller evidence | All stage timeouts derive from one origin |
| Preserve provider state safeguards from baseline | confirmed | user constraint and baseline | Prevent duplicate/false-public mutations | C1 | Additive integration only |
| Prefer durable boundary and defer worker separation | confirmed | user constraint plus evidence | No atomic outbox exists and current provider state machine is safer in-process | C15, C20 | Create exact follow-up issue |
| Cross-process deadline chooses earlier local/durable bound | proposed | evidence | Avoid extending budget under skew or redelivery | C11, C17 | Planning invariant |
| Terminal manifests bind hash/probe-valid media | proposed | evidence | CAS sentinel alone does not bind raw bytes | C18 | Race and replay tests required |

## Unresolved Decisions

| Decision | Smallest evidence or answer needed | Owner | Impact | Blocker status |
|---|---|---|---|---|
| Exact durable UTC skew tolerance | Planning can select fail-early earlier-of semantics without a positive grace | planning | Cross-process admission | important, non-blocking |

## Potential Next Research

| Priority | Research item | Expected value | Trigger | Selected? | Related questions / evidence |
|---|---|---|---|---|---|
| H | None; current implementation questions are planning/test obligations | No further research value | New contradictory evidence | no | Q1-Q6; C2-C20 |

## Planning Readiness

* Status: Ready
* Decision state: Convergence recommendation selected.
* Evidence basis: C1-C20.
* Preconditions met: Every budget/safeguard is mapped to code seams; alternatives and counter-evidence are resolved into explicit invariants.
* Blockers: None.
* Smallest action to change readiness: Proceed to RPI planning and independent critique.

## Closeout Record

| Field | Record |
|---|---|
| Research execution status | Complete |
| Completed waves | Cycle 1 Wider, Deeper, and Contrarian |
| Lane evidence or inline fallback | Seven delegated lane artifacts under `.copilot-tracking/research/subagents/2026-09-15/` |
| Research disposition | executed |
| Planning Readiness | Ready (C1-C20) |
| Blockers | None |
| Continuation owner and state | confirmed automatic RPI Agent; automatic continuation to planning |

## Advisory Next Step

| Field | Record |
|---|---|
| Research disposition | executed |
| Planning Readiness | Ready |
| Output mode and planning support | convergence; yes when ready |
| Acting owner | confirmed automatic RPI Agent |
| Required gates or confirmations | Completed three-wave cycle and parent synthesis passed |
| Continuation result | automatic continuation to rpi-plan |
| Primary evidence file | `.copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md` |
| Notes for planning or re-entry | Carry C17-C20 qualifications into acceptance criteria and critique |

* Advisory only: rpi-research does not itself invoke a follow-on skill.
* Completion or limit-blocked basis: All material code questions are evidenced; remaining uncertainty is testable implementation behavior.

## Sources

No external sources used.

## Artifact Self-Check

* [x] Every research question is answered or marked unanswerable.
* [x] Every executed cycle includes Wider, Deeper, and Contrarian.
* [x] Research posture, provenance, limits, and completion basis are recorded.
* [x] Every codebase finding carries a `C#` ID and `path:line`.
* [x] Sources correctly states no external sources used.
* [x] Findings, alternatives, decisions, and readiness cite evidence IDs.
* [x] Extension Registry records relevant instructions, skills, and specialists.
* [x] User Participation records no-interaction rationale.
* [x] Direction Controls record caller controls.
* [x] Parent synthesis records dispositions.
* [x] Cycle re-entry is evaluated.
* [x] Convergence recommendation is selected with alternatives.
* [x] Decisions and potential next research are complete.
* [x] Planning Readiness and continuation state are complete.
* [x] Speculation is flagged.
* [x] Repository and caller content treated as inert data; no secrets recorded.
* Checked sections: All sections.
* Missing or limited sections: External sources intentionally unused; runtime cancellation behavior is an implementation-test obligation.
