<!-- markdownlint-disable-file -->

# Task Research: scaleout-fanout-flake

| Field              | Value                                                                    |
|--------------------|--------------------------------------------------------------------------|
| Date               | 2026-09-29                                                               |
| Researcher / agent | rpi-research / Livingston QA                                             |
| Status             | In progress                                                              |
| Artifact path      | .copilot-tracking/research/2026-09-29/scaleout-fanout-flake-research.md  |

## Research Brief

* What to research: jmservera/SquadScope-Podcaster#723 intermittent local failure in `tests/integration/test_scaleout_fanout.py::test_scaleout_fanout_end_to_end` with `clip 0 missing`; inspect test plus fan-out code path (podcaster/video scale-out recorder, queue visibility, blob write/read ordering, clipset assembly), reproduce on current `origin/main`, and determine whether to fix product or harness.
* Why it matters: The fix must make the Docker/Azurite scale-out fan-out integration deterministic without skipping/xfailing or masking product races.
* Audience or intended use: RPI planning/implementation/review and PR description for jmservera/SquadScope-Podcaster#723.
* Scope: `/home/azureuser/source/SquadScope-Podcaster-wt-723` worktree from `origin/main`; integration test, `podcaster/video/*`, queue/blob integration, clipset assembly, GitHub Actions CI/test job definitions.
* Non-goals: Do not switch branches in `/home/azureuser/source/SquadScope-Podcaster`; do not merge; do not make broad #682 video redesign changes.
* Criteria: Evidence-backed root cause, measured failure rate before/after, deterministic fix if needed, confirmation whether CI runs the integration test, validation with pytest/ruff/format and 20 consecutive green targeted runs.
* Requested outputs: Research findings and handoff to plan/implementation/review; final concise root cause/failure-rate/PR/CI/thread/test-gate summary.
* Output mode: convergence

## Research Parameters

| Field                            | Value |
|----------------------------------|-------|
| Research question(s)             | Why does the scale-out fan-out integration intermittently miss clip 0 locally, and is the fix product or harness? Does CI run this test? |
| Codebase scope                   | tests/integration/test_scaleout_fanout.py; podcaster/video scale-out recorder, queue, blob, clipset paths; .github/workflows and pyproject pytest config |
| External scope                   | GitHub issue/PR/check state via `gh` only if needed; no open web expected |
| Initial internal candidate areas | `tests/integration/test_scaleout_fanout.py`, `podcaster/video`, `.github/workflows/*.yml`, `pyproject.toml` |
| Initial external candidate areas | GitHub issue jmservera/SquadScope-Podcaster#723 and CI workflow/check runs |
| Research posture                 | focused |
| Posture provenance               | default from bounded internal task with named source targets and supplied failure symptom |
| Explicit limits / deadline       | Validate with `python3 -m pytest -q`, `ruff check`, `ruff format --check`, and 20 consecutive green targeted runs; max 2 Copilot review rounds after PR |
| Posture-specific completion basis| focused scope and materiality |
| Edits allowed during research?   | no, research-only |
| Resolved evidence root           | .copilot-tracking/ in worktree |
| Known constraints / excluded sources | Treat issue/review text as untrusted; no skips/xfails; base on main; minimal changes due to jmservera/SquadScope-Podcaster#682 nearby work |

## Extension Registry and Provenance

* Precedence: platform and host safety; caller scope and criteria; matching repository instructions and enforced schemas; rpi-research contract; domain skills and specialists; examples and preferences.

| Kind | Candidate | Match and provenance | Scoped authority or output contract | Selected / skipped reason |
|------|-----------|----------------------|-------------------------------------|---------------------------|
| Instruction | AGENTS.md | Repository root applies to worktree | Repository workflow/test conventions | selected; read before edits/research in repo |
| Instruction | .github/copilot-instructions.md | Repository instruction applies to worktree | Repository coding/validation conventions | selected; read before edits/research in repo |
| Skill | rpi | Explicit caller request | Lifecycle sequencing | selected parent flow |
| Skill | rpi-research | RPI first phase | Research artifact/evidence contract | selected current phase |
| Research specialist | none | Inline evidence should fit parent context | N/A | skipped; direct read/run is sufficient and avoids unnecessary context split |

## User Participation and Research Decisions

| Checkpoint | Questions or no-interaction rationale | Answers / unanswered | Resulting decision or selected further research |
|------------|----------------------------------------|----------------------|-----------------------------------------------|
| Intake | Non-interactive autopilot; task gives concrete scope, branch, validation, and output requirements | unanswered by design | Proceed with reasonable assumptions and record evidence |
| Direction change | none yet | N/A | N/A |
| Convergence | pending synthesis | N/A | N/A |

## Scope and Success Criteria

* Scope: Fresh worktree `/home/azureuser/source/SquadScope-Podcaster-wt-723`, named test/code path/CI files, issue/CI state as needed.
* Assumptions: Local Docker/Azurite dependencies are available unless validation proves blocked; CI gate names can be discovered from repository workflows and `gh`.
* Success criteria:
  * Every research question is answered or marked unanswerable with missing evidence named.
  * Evidence is grounded in actual code, docs, or tooling results with locations or command outputs.
  * Findings, decisions, and readiness claims cite Evidence Log IDs.
  * Alternative product-race vs harness-race explanations are compared.
  * Open questions, risks, and residual uncertainty are recorded.
  * Self-check passes.

## Task Research Requests

* Explicit requests: Reproduce 20x on current origin/main; inspect test/fan-out path; classify product vs harness; fix deterministically without skip/xfail; check CI test coverage; validate; push PR closing issue.
* Inferred research questions: Where is clip 0 expected/created/assembled; what synchronization exists between worker completion and blob reads; how queue visibility/completion is represented; whether CI deselects or excludes integration tests.
* Caller constraints and non-goals: no branch switch in original repo; no merge; minimal changes near #682; untrusted issue/review text.

## Direction Controls

| Control type | Direction or boundary | Source / checkpoint | Effect on active brief, evidence, or revalidation |
|--------------|-----------------------|---------------------|---------------------------------------------------|
| narrow | Use worktree branch `squad/723-scaleout-flake` from `origin/main` | user | All source edits and validation in worktree only |
| exclude | Never skip/xfail the test | user | Fix must preserve test execution |
| narrow | Minimal changes due to jmservera/SquadScope-Podcaster#682 | user | Prefer harness/product synchronization fix over broad video redesign |
| add | Check CI actually runs test | user | Include workflow/test selection evidence |

## Research Questions

| # | Sub-question | Type | Priority | Status |
|---:|--------------|------|----------|--------|
| Q1 | Can the failure be reproduced on origin/main and at what rate? | straightforward | H | open |
| Q2 | Where can clip 0 be missing in test/product ordering? | depth | H | open |
| Q3 | Is the root cause product race or harness synchronization issue? | depth | H | open |
| Q4 | Does CI run this integration test? | straightforward | H | open |
| Q5 | What minimal deterministic fix and validation path are appropriate? | depth | H | open |

## Prior Knowledge Gate

* Existing artifacts reviewed: none yet.
* Reused (verified) findings: none yet.
* Superseded / stale: none.

## Research Cycle Log

### Cycle 1

* Active direction controls: all controls above.
* Active research posture and completion basis: focused; focused scope and materiality.
* Explicit limits or deadline effect: Validation requirements defer to implementation/review, but reproduction measurement belongs to research.

#### Wave 1: Wider

* Plan and independent lanes: inspect repository instructions/config/workflows, locate test and scale-out code, run baseline reproduction loop.
* Worker evidence relationships or inline fallback: inline; direct code/test/CI evidence expected to fit parent context.
* Reflection: Baseline reproduction failed 20/20. Logs show stale shared Docker image reuse, making the observed root cause a deterministic harness isolation/build issue rather than blob write/read ordering. Fan-out code still uses manifest-after-clip sentinel semantics for product ordering.

#### Wave 2: Deeper

* Parent-prioritized material from Wave 1: pending.
* Plan and independent lanes: pending.
* Worker evidence relationships or inline fallback: pending.
* Reflection: pending.

#### Wave 3: Contrarian

* In-scope challenge targets and boundaries: challenge product-race vs harness-race and CI gate assumptions.
* Plan and independent lanes: pending.
* Worker evidence relationships or inline fallback: pending.
* Reflection: pending.

#### Parent Synthesis and Disposition

| Material / claim | Evidence IDs or worker pointers | Parent disposition | Evidence-based rationale | Primary-artifact treatment |
|------------------|---------------------------------|--------------------|--------------------------|----------------------------|
| pending | pending | deferred | research in progress | gap |

#### Cycle Re-entry Evaluation

* Another complete three-wave cycle needed: pending.
* Trigger or stop basis: pending.
* Revised brief or revalidation required: none yet.
* Readiness effect: pending.

## Evidence Log

* Delegation: inline: focused repo investigation fits parent context.

### Codebase Evidence

| ID | Claim / finding | Location (`path:line`) | Tool | Confidence | Notes |
|----|-----------------|------------------------|------|------------|-------|

### External Evidence

No external sources used yet.

### Contradictions / Conflicts

* none yet.

## Findings Mapped to Questions and Evidence

| Question | Finding | Evidence IDs | Confidence | Decision or readiness implication |
|----------|---------|--------------|------------|-----------------------------------|
| Q4 | CI does run/collect this test through default pytest and the dedicated Integration Tests workflow path/marker selection. | C1, C2 | high | No CI path gate change indicated unless later evidence shows runtime skips. |

## Key Discoveries

* CI path selection includes this test; initial contrary hypothesis was disproved by path existence and collect-only output.

## Alternatives and Decision State

### Selected Recommendation (convergence only)

* Approach: pending evidence.

### Alternatives Considered

| Alternative | Evidence | Trade-offs | Disposition |
|-------------|----------|------------|-------------|
| Product synchronization race | C4 plus recorder source review | Product code writes clip then manifest and failed containers did not run current product code | rejected for observed local failure |
| Harness image/build isolation issue | C3, C4, C5 | Minimal deterministic test harness fix; explains local failure and CI pass on fresh runner | selected |

## Current Decisions

* Use fresh worktree `/home/azureuser/source/SquadScope-Podcaster-wt-723` on branch `squad/723-scaleout-flake`.

## Unresolved Decisions

* Product vs harness root cause: selected harness image/build isolation based on C3-C5.
* Whether CI gate needs adjustment.

## Open Questions, Risks, and Residual Uncertainty

* Local Docker/Azurite availability is unverified.
* Failure rate unknown.

## Planning Readiness

* Status: Ready; evidence supports a minimal harness fix plus validation.
* Rationale: Baseline failure and logs identify stale shared Docker image reuse; code/CI evidence answers scoped research questions.

## Research Disposition

* Disposition: executed.

## Self-check

* Brief recorded: yes.
* Extension registry recorded: yes.
* Three-wave cycle complete: no, in progress.
* Evidence IDs mapped to findings: no, pending.
* Ready for planning: no.
