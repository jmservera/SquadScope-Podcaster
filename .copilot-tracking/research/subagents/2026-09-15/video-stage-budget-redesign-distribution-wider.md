# Cycle 1 / Wave 1 Wider — Internal Lane Evidence

## Delegated contract

- **Topic:** W38 video stage budget redesign in `jmservera/SquadScope-Podcaster`.
- **Lane:** Internal — video job runner, distribution/provider boundary, evidence and admission/readback safeguards, queue disposition, ACA Bicep, monitoring/runbooks, and relevant tests.
- **Questions:** Identify the rendered-to-provider boundary; durable checkpoint/evidence/outbox seams; provider canonical identity and unknown/no-repeat safeguards; public semantic aggregation; queue lease and shutdown paths; ACA 5400 and recorder timeout/visibility settings; tests/docs needing change; and one-PR feasibility/risk for a separate distribution worker.
- **Evidence criteria:** Workspace-relative `path:line` provenance for each factual finding; preserve all caller-named safeguards; distinguish facts from inferences; no live W38, provider, or cloud operations.
- **Scope / non-goals:** Read-only internal workspace evidence only. No source, configuration, production-documentation, parent-artifact, provider, queue, or cloud changes; no parent decisions.
- **Posture:** Balanced.
- **Limit / stop condition:** Stop at internal-evidence saturation.
- **Wave evidence goal:** Wider — map current contracts, candidate seams, risks, safeguards, and gaps rather than choose a design.
- **Initial status:** Investigating.

## Research trail

1. Read the video runner, distribution, queue, recorder/editor, monitoring, ACA Bicep, RFC, and focused tests. No live provider, queue, W38, or cloud operation was run.
2. Saturation reached: the current boundary, persistence/evidence guards, queue dispositions, deployment budgets, observability surfaces, and existing split topology are directly represented by the inspected workspace evidence.

## Findings, provenance, and evidence relationships

### Rendered-to-provider boundary and durable seams

1. **Q: Current rendered-to-provider boundary? — Supports a single in-process boundary after composition.** The editor runner validates the composed MP4 and then derives publication metadata, creates/loads a publish run and publication identity, claims per-platform intent, and calls `distribute_video`; the same runner persists the aggregate result. `distribute_video` archives the MP4 and handles YouTube, RSS, and Spotify upload. This is not presently a separate distribution queue/worker boundary.  
   Provenance: `podcaster/video/job_runner.py:1108-1279`; `podcaster/video/distribution.py:985-1032`.

2. **Q: Durable checkpoint/evidence/outbox seams? — Supports manifest/scratch/evidence seams; weakens a claim that a durable distribution outbox already exists.** Intermediate checkpoints use the scratch store; per-platform snapshots are CAS-written to `generation.video_publish`; a run ID is persisted; canonical evidence is appended around mutation. The code records a pre-mutation `upload_intent` with `retry_blocked=True`; unavailable evidence becomes `publication_unknown` and remains retry-blocked. These are credible handoff ingredients, but no inspected queue schema, Bicep job, or module implements a dedicated distribution outbox/consumer.  
   Provenance: `podcaster/video/job_runner.py:483-564`, `podcaster/video/job_runner.py:567-619`, `podcaster/video/job_runner.py:1173-1279`; `podcaster/publication_state.py:17-45`, `podcaster/publication_state.py:58-94`; `tests/test_video_job_runner.py:110-142`, `tests/test_video_job_runner.py:770-827`.

### Provider identity, no-repeat, and public semantics

3. **Q: Provider canonical identity and unknown/no-repeat safeguards? — Supports strong admission safeguards with a documented residual risk.** Invalid requested canonical identity blocks provider mutation. Before enabled platform calls, a duplicate evidence claim or evidence-write failure marks that platform as unknown/retry-blocked. Distribution skips prior published, unknown, or manual-handoff platform snapshots. The implementation explicitly documents a remaining YouTube at-least-once window if a process dies after provider create but before the persistence callback; Spotify reconciles drafts by title before creating.  
   Provenance: `podcaster/video/job_runner.py:1177-1249`; `podcaster/video/distribution.py:1026-1030`, `podcaster/video/distribution.py:1075-1102`; `tests/test_video_job_runner.py:832-874`, `tests/test_video_job_runner.py:879-936`, `tests/test_video_distribution.py:670-769`.

4. **Q: Public semantic aggregation? — Supports a canonical evidence-led public state, not a mere provider-success flag.** Canonical outcomes and verification states include `publication_unknown`, `provider_readback`, and `external_verified`; a `published` outcome is public only when externally verified. The runner persists provider records/outcomes plus `public_delivery_status`, and the authenticated job endpoint exposes evidence and aggregated outcomes.  
   Provenance: `podcaster/publication_state.py:17-45`, `podcaster/publication_state.py:106-119`; `podcaster/video/job_runner.py:1289-1319`, `podcaster/video/job_runner.py:1375-1396`; `podcaster/monitoring.py:360-418`, `podcaster/monitoring.py:679-698`.

### Queue leases, graceful disposition, and timing budgets

5. **Q: Queue lease/shutdown paths? — Supports explicit terminal/transient/foreign-lease queue disposition; leaves graceful process-termination handling unresolved.** The video runner receives with a configurable 5400-second default visibility timeout. Malformed and permanent messages are deleted, transient messages remain until retry exhaustion, and an unexpired foreign editor lease leaves the message for later redelivery. Recorder behavior parallels this: retry leaves the message, poison creates a terminal fallback manifest then deletes it. Both entrypoints drain and exit; the inspected code does not establish an explicit SIGTERM/ACA graceful-shutdown handler or lease/visibility renewal for a process terminated mid-provider call.  
   Provenance: `podcaster/video/job_runner.py:1677-1751`, `podcaster/video/job_runner.py:1754-1800`; `podcaster/video/recorder.py:349-396`, `podcaster/video/recorder.py:490-522`; `podcaster/video/editor.py:126-176`; `tests/test_video_job_runner.py:1830-1885`; `tests/test_recorder.py:223-314`.

6. **Q: ACA 5400 and recorder timeout/visibility settings? — Supports alignment as deployed configuration.** The video ACA default `replicaTimeoutSeconds` and `videoVisibilityTimeoutSeconds` are both 5400, passed to ACA and the runner env; it caps editor executions at one. The recorder defaults both replica and clip visibility timeout to 900, has a 600-second clip cap, and permits up to ten executions. The RFC explains these relationships and the fan-in timeout/bounded fallback expectation.  
   Provenance: `infra/modules/aca-video.bicep:68-85`, `infra/modules/aca-video.bicep:176-225`, `infra/modules/aca-video.bicep:262-284`; `infra/modules/aca-recorder.bicep:53-81`, `infra/modules/aca-recorder.bicep:86-183`; `docs/scaleout-recorder-rfc.md:211-231`.

### Tests, documentation, and separate-worker feasibility

7. **Q: Tests/docs that must change? — Supports focused additions/updates rather than an unbounded test rewrite.** Existing unit coverage protects evidence-persistence failure, canonical identity denial, duplicate mutation claim, post-mutation unknown propagation, platform-skip behavior, terminal/transient/lease queue paths, and recorder poison fallback. Existing RFC test plan calls for an Azurite fan-out/fan-in integration test, redelivery/no-duplicate assertions, fallback convergence, and cleanup. A distribution-worker change must extend these tests to cover durable handoff/admission, separately consumed disposition, cross-worker public aggregation/readback, and termination/redelivery; update the RFC/runbook material that currently describes the editor as the distributor.  
   Provenance: `tests/test_video_job_runner.py:110-211`, `tests/test_video_job_runner.py:770-1014`, `tests/test_video_job_runner.py:1827-1885`; `tests/test_video_distribution.py:670-815`; `tests/test_recorder.py:223-327`; `docs/scaleout-recorder-rfc.md:49-100`, `docs/scaleout-recorder-rfc.md:233-257`.

8. **Q: Separate distribution worker feasible in one PR? — Supports feasibility only with elevated delivery risk.** The repository already has a working two-role editor/recorder split, shared scratch/state patterns, KEDA queue provision, durable publication evidence, and monitoring reads, so a new worker has recognizable seams. However, the same sources establish that external side effects are currently sequenced inside the editor and guarded by its lease, manifest snapshots, intent claims, and aggregate write. Moving them in one PR would require a new durable handoff and independent consumer semantics while retaining all named safeguards—canonical identity admission, pre/post-mutation evidence, unknown/no-repeat blocking, provider readback/public aggregation, 5400 visibility alignment, terminal/transient/foreign-lease disposition, and shutdown recovery. The stated pre-persist provider-create crash window is an additional risk, especially for YouTube.  
   Provenance: `podcaster/video/job_runner.py:785-793`, `podcaster/video/job_runner.py:1177-1418`; `podcaster/video/distribution.py:1026-1030`; `infra/modules/aca-video.bicep:20-23`, `infra/modules/aca-video.bicep:68-85`; `docs/scaleout-recorder-rfc.md:159-190`.

## Gaps and conflicts

- **Missing evidence:** No inspected internal source defines a distribution queue, distribution-worker entrypoint, durable outbox record/consumer, or a graceful ACA SIGTERM/shutdown protocol. A design/implementation proposal needs the exact handoff state machine and termination semantics before feasibility can be proven.
- **Conflict / scope note:** `infra/modules/aca-video.bicep:278-281` calls visibility configuration inert until an editor refactor, whereas `podcaster/video/job_runner.py:1763-1768` currently uses it. The source indicates the comment is stale rather than proof of deployed behavior; deployment verification was outside scope.
- **Provider uncertainty:** The internal code itself records the post-create/pre-persistence YouTube duplicate window. No live provider readback/reconciliation behavior was tested, by instruction.

## Stop decision

**Stopped at evidence saturation.** All delegated internal evidence areas yielded direct source/test/documentation provenance. Further internal reads would repeat the same boundary and safeguard contracts; the remaining questions require a bounded design choice or prohibited live/deployment/provider evidence.
