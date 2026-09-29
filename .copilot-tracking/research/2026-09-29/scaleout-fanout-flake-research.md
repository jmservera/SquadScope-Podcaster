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

* Parent-prioritized material from Wave 1: reproduce failure rate, inspect compose image/build isolation, verify recorder/editor product ordering, verify CI selection.
* Plan and independent lanes: inspect recorder clip-before-manifest logic, editor fan-in/clipset logic, Docker compose image declaration, pytest/CI collection, and baseline compose logs.
* Worker evidence relationships or inline fallback: inline; C1-C8 record CI selection, baseline failure rate, stale image logs, compose image tag, and product sentinel behavior.
* Reflection: Evidence supports a deterministic harness-state failure. The recorder containers that failed did not match current worktree code, so changing product blob ordering would not address the observed `clip 0 missing` symptom.

#### Wave 3: Contrarian

* In-scope challenge targets and boundaries: challenge whether CI actually skips the test, whether this is a product queue/blob ordering race, and whether the harness fix masks recorder failures.
* Plan and independent lanes: run pytest collection, inspect workflow path filters/commands, compare logs to current source, and preserve strict post-recorder clip/manifest assertions.
* Worker evidence relationships or inline fallback: inline; C1-C2 disprove CI path-missing concern, C4-C8 reject current-product race for the observed local failure, and planned fix avoids sleeps/retries/skips.
* Reflection: Contrarian checks did not reveal a product write/read race. They did reveal that build failure must remain a test failure, not a skip, because otherwise the harness could mask regressions.

#### Parent Synthesis and Disposition

| Material / claim | Evidence IDs or worker pointers | Parent disposition | Evidence-based rationale | Primary-artifact treatment |
|------------------|---------------------------------|--------------------|--------------------------|----------------------------|
| CI runs/collects the test | C1, C2 | accepted | `pytest --collect-only` collected the target and the integration workflow path/command matches `tests/integration/`. | finding |
| Local baseline failure is reproducible | C3 | accepted | 20-run baseline loop failed every run with the requested symptom. | finding |
| Observed root cause is stale shared Docker image reuse | C4, C5 | accepted | Logs show stale recorder code not present in current source, and compose used a fixed image tag without forcing a current-worktree build. | finding/decision |
| Current product recorder/editor ordering is the cause | C6, C7 | rejected for observed failure | Current source writes/verifies clip before manifest and editor waits on manifests; failed containers never reached that code path. | alternative disposition |
| Harness fix must not mask build failures | C8 | accepted | Review evidence showed build failures inside the skip catch would weaken the test; build must occur after prerequisites and fail normally. | implementation constraint |

#### Cycle Re-entry Evaluation

* Another complete three-wave cycle needed: no.
* Trigger or stop basis: focused scope covered; material claims are backed and contrarian checks did not introduce a new implementation-changing uncertainty.
* Revised brief or revalidation required: no.
* Readiness effect: Ready for planning/implementation.

## Evidence Log

* Delegation: inline: focused repo investigation fits parent context.

### Codebase Evidence

| ID | Claim / finding | Location (`path:line`) | Tool | Confidence | Notes |
|----|-----------------|------------------------|------|------------|-------|
| C1 | Default CI `pytest` discovery includes `tests/integration/test_scaleout_fanout.py` because pytest `testpaths` is `tests` and `addopts` excludes only `slow`, not `integration`. | pyproject.toml:33,37 | view/collect-only | high | `pytest --collect-only -q` collected the target test. |
| C2 | Dedicated Integration Tests workflow also includes the target path and explicitly runs `pytest tests/integration/ -v --tb=short -m "integration"`. | .github/workflows/integration-tests.yml:5,31 | view | high | Pull request path filter and command match the file path. |
| C3 | Baseline local reproduction on current `origin/main` worktree failed 20/20 runs with `clip 0 missing`. | /tmp/scaleout-baseline-loop.log | pytest loop | high | First single run and 20-run loop reproduced the issue. |
| C4 | Recorder compose logs from baseline show containers executed stale/different recorder code (`RecoverableRecorderSetupError: incompatible recorder clipset`) while exiting 0, not this worktree's recorder implementation. | /tmp/scaleout-baseline-1.log; manual compose output | docker compose logs | high | Current source `podcaster/video/recorder.py` has no `RecoverableRecorderSetupError`; fixed image tag `podcaster-synthesis:test` allowed stale image reuse. |
| C5 | Fan-out harness used fixed image tag `podcaster-synthesis:test` and recorder `up` did not request rebuild, so Docker Compose could reuse a stale image from another branch/worktree. | docker-compose.fanout.yml:43; tests/integration/test_scaleout_fanout.py:153 | view | high | Explains local-only statefulness; CI fresh runner usually has no stale image. |
| C6 | Current recorder writes the clip, verifies blob size, and only then writes the terminal manifest sentinel. | podcaster/video/recorder.py:219-250 | view | high | Product ordering matches the intended clip-before-manifest contract. |
| C7 | Current editor fan-in waits on each expected manifest, not a bounded blob listing. | podcaster/video/editor.py:32-36,206-217 | view | high | Product fan-in barrier is per-index manifest based. |
| C8 | Review round 1 identified that image identity must use a full-path digest and build failures must fail rather than be converted into prerequisite skips. | PR review threads on jmservera/SquadScope-Podcaster#724 | gh graphql | high | Accepted and fixed before second push. |

### External Evidence

No external sources used.

### Contradictions / Conflicts

* Initial CI-missing hypothesis conflicted with actual file path and collect-only output; resolved in favor of C1-C2.
* Product-race hypothesis conflicted with stale-image logs and current source ordering; resolved in favor of harness isolation C4-C7.

## Findings Mapped to Questions and Evidence

| Question | Finding | Evidence IDs | Confidence | Decision or readiness implication |
|----------|---------|--------------|------------|-----------------------------------|
| Q1 | The failure was reproducible locally at 20/20 failures before the fix. | C3 | high | Baseline rate is measurable and severe. |
| Q2 | The missing clip occurs before current recorder product logic runs because stale recorder image code failed setup and exited 0. | C4, C5 | high | Investigate/fix harness image isolation rather than blob ordering. |
| Q3 | Root cause is a harness issue: fixed shared image tag plus no current-worktree build. | C4, C5, C6, C7 | high | Minimal deterministic harness fix selected. |
| Q4 | CI does run/collect this test through default pytest and the dedicated Integration Tests workflow path/marker selection. | C1, C2 | high | No CI path gate change indicated. |
| Q5 | Fix should build/use a current-worktree-specific image and keep build failures hard failures after Docker prerequisite setup succeeds. | C5, C8 | high | Implementation constraints set. |

## Key Discoveries

* Baseline local failure rate was 20/20 on current local Docker state.
* The failed containers ran stale/different recorder code from a shared Docker image tag, not the current worktree code.
* CI path selection includes this test; initial contrary hypothesis was disproved by path existence and collect-only output.
* A robust harness fix must isolate image identity by full worktree path and fail on current-image build errors.

## Alternatives and Decision State

### Selected Recommendation (convergence only)

* Approach: Fix the integration harness to build the current worktree recorder image under a worktree-specific digest tag before scale-out, with build failures surfacing as test failures after Docker/Azurite prerequisites are available.

### Alternatives Considered

| Alternative | Evidence | Trade-offs | Disposition |
|-------------|----------|------------|-------------|
| Product synchronization race | C4, C6, C7 | Would risk unnecessary product changes in code adjacent to jmservera/SquadScope-Podcaster#682; does not explain stale-code logs. | rejected for observed local failure |
| Harness image/build isolation issue | C3, C4, C5, C8 | Minimal deterministic test harness fix; explains local failure and CI pass on fresh runner. | selected |

## Current Decisions

* Use fresh worktree `/home/azureuser/source/SquadScope-Podcaster-wt-723` on branch `squad/723-scaleout-flake`.
* Treat root cause as harness image/build isolation, not current product queue/blob ordering.
* Do not change CI path selection because evidence shows CI runs/collects the test.

## Unresolved Decisions

* None for current scope.

## Open Questions, Risks, and Residual Uncertainty

* Docker availability can still cause a prerequisite skip before Azurite startup, preserving the existing test contract.
* The fix does not remove old stale images from developers' machines; it avoids selecting them for this worktree.

## Planning Readiness

* Status: Ready.
* Rationale: Baseline failure, code path, harness cause, CI selection, and implementation constraints are evidence-backed.

## Research Disposition

* Disposition: executed.

## Self-check

* Brief recorded: yes.
* Extension registry recorded: yes.
* Three-wave cycle complete: yes.
* Evidence IDs mapped to findings: yes.
* Ready for planning: yes.
