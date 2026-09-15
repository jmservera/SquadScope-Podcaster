# Cycle 1 / Wave 2 Deeper — Internal Lane Evidence

## Delegated contract

- **Topic:** W38 video stage budget redesign in `jmservera/SquadScope-Podcaster`.
- **Lane:** Internal distribution/infra: precise manifest/evidence transition, provider admission/readback timing, queue shutdown disposition, ACA/recorder timing assertions, and atomic distribution-worker feasibility.
- **Questions and criteria:** Determine an exact durable `rendered_pending_distribution` transition; admission only with >=1800 seconds remaining and never after T+75; readback/evidence through T+82; shutdown/lease/queue disposition by T+85; preserve ACA 5400, application <=5100, and bounded recorder visibility; map exact call sites/tests/docs; evaluate an atomic outbox/worker without widening provider mutation risk. Preserve canonical identity, evidence retention, unknown/no-repeat, YouTube/Spotify/RSS, and public-semantics safeguards.
- **Scope / non-goals:** Read-only workspace investigation; no live W38, provider, queue, or cloud operation; no source, configuration, production-doc, or parent-artifact edits; no parent decision.
- **Posture:** Balanced.
- **Limit / stop condition:** Evidence saturation.
- **Wave evidence goal:** Deeper — establish the exact existing contract and distinguish caller-proposed timing/state from currently implemented behavior.
- **Initial status:** Investigating.

## Research trail

1. Searched the workspace for the requested state name and T+75/T+82/T+85/5100/1800 timing terms, then traced publication-state CAS updates, provider calls, queue drains, Bicep, tests, and operational contracts.
2. No live W38, provider, queue, or cloud operation was run.
3. Stopped at saturation: the requested proposed state/time budgets are absent from the current implementation, while all currently implemented adjacent contracts have direct evidence.

## Findings, provenance, and evidence relationships

### Exact current state and proposed-state gap

1. **Q: Exact durable `rendered_pending_distribution` transition? — Unresolved in the present implementation.** No source, test, or documentation occurrence of `rendered_pending_distribution` was found. The closest current durable sequence is: validate local composed MP4 → create/load `generation.video_publish_run_id` → validate canonical identity when requested → append per-platform `upload_intent` evidence with `publication_unknown`, `mutation_attempted=False`, and `retry_blocked=True` → invoke provider distribution → CAS-write each platform snapshot/evidence → CAS-write `generation.video_runner` aggregate terminal state. Thus, an exact new durable transition would have to be introduced between MP4 validation and any `append_evidence(...operation="upload_intent")`/`distribute_video` call; it cannot be claimed as existing behavior.  
   Provenance: `podcaster/video/job_runner.py:537-564`, `podcaster/video/job_runner.py:1108-1279`, `podcaster/video/job_runner.py:1289-1418`; `podcaster/publication_state.py:301-430`.

2. **Q: Atomic outbox/worker safe fit? — Supports a potential atomic *manifest outbox-record* claim, but not atomic provider delivery or atomic enqueue.** `storage.update_bytes` gives the existing manifest/evidence CAS seam; queue `send_message` is a distinct operation. The provider mutation is necessarily outside those local writes, and current documentation records the post-create/pre-persistence YouTube at-least-once window. Therefore a worker can only be made safer if it consumes a uniquely CAS-created durable work record and preserves current pre-mutation blocking and post-mutation evidence behavior; an atomic “state + queue message + provider mutation” guarantee is unsupported by the current abstractions. Moving the current in-process call without that record/consumer design expands the known crash window rather than reducing it.  
   Provenance: `podcaster/video/job_runner.py:483-527`, `podcaster/video/job_runner.py:1177-1279`; `podcaster/queue.py:246-280`; `podcaster/video/distribution.py:1026-1030`; `docs/scaleout-recorder-rfc.md:182-190`.

### Admission, readback/evidence, and public semantics

3. **Q: Provider admission >=1800 seconds remaining and never after T+75? — Missing current enforcement; caller-proposed policy needs new clock/deadline input and tests.** No provider-admission remaining-time calculation, T+75 cutoff, or 1800-second admission threshold exists in the inspected runner/distribution modules, tests, Bicep, or documentation. The only 1800-second value found is the editor lease TTL, not a provider admission budget. Current admission is identity/evidence/config based: canonical identity errors block mutation; duplicate/failed intent evidence turns the affected platform into unknown/retry-blocked; distribution separately checks enabled targets and valid MP4.  
   Provenance: `podcaster/video/editor.py:64-67`, `podcaster/video/job_runner.py:1177-1249`; `podcaster/video/distribution.py:1036-1066`; `tests/test_video_job_runner.py:770-936`.

4. **Q: Readback/evidence through T+82? — Existing evidence model supports time-stamped readback but has no T+82 deadline orchestration.** Evidence appends are time-stamped, append-only, deduplicated by canonical identity/platform/media/operation/outcome/artifact, and retained at least 28 days. `provider_readback` and `external_verified` are distinct verification states; only external verification yields normalized public status. The distribution result and monitoring API expose provider records, aggregate outcomes, and public delivery state. The current code can carry readback evidence but does not schedule/require it through T+82.  
   Provenance: `podcaster/publication_state.py:30-45`, `podcaster/publication_state.py:301-430`, `podcaster/publication_state.py:440-467`; `podcaster/video/job_runner.py:567-619`, `podcaster/video/job_runner.py:1289-1396`; `podcaster/monitoring.py:360-418`, `podcaster/monitoring.py:679-698`; `docs/integration-contract.md:233-280`; `docs/distribution-ux.md:315-320`.

5. **Safeguard relationship: Canonical identity + unknown/no-repeat + provider-specific/public rules — Supports retention as non-negotiable if work moves.** Canonical identity requires accepted non-dry-run manifest identity and validated week/run/hashes. Any latest terminal/blocking outcome (uploaded, draft, published, manual handoff, or unknown) with retry blocking prevents another provider call. YouTube starts private/unlisted rather than public; Spotify video creates a separate draft episode rather than modifying its audio anchor; RSS tracks feed-update state. Public delivery remains incomplete/pending without external verification, so a worker must preserve these normalized records rather than infer publicness from transport acceptance.  
   Provenance: `podcaster/publication_state.py:170-224`, `podcaster/publication_state.py:455-467`; `podcaster/video/distribution.py:1075-1157`, `podcaster/video/distribution.py:1242-1351`; `tests/test_video_distribution.py:840-1105`.

### Shutdown, leases, queues, and infrastructure budgets

6. **Q: Shutdown/lease/queue disposition by T+85? — Current disposition is defined by outcome, not an elapsed T+85 policy.** The video consumer leaves transient failures and foreign-lease skips undeleted for redelivery; it deletes malformed, permanent, retry-exhausted, and completed/ordinary skipped messages. The lease is CAS-acquired/renewed on fan-in polls and released only if still owned. Recorder retry similarly leaves a message, while poison writes terminal fallback then deletes. Entrypoints drain and exit; no explicit signal/deadline-aware graceful shutdown handler or T+85 cutoff was found.  
   Provenance: `podcaster/video/job_runner.py:1677-1751`, `podcaster/video/job_runner.py:1754-1800`; `podcaster/video/editor.py:126-176`, `podcaster/video/editor.py:249-377`; `podcaster/video/recorder.py:349-396`, `podcaster/video/recorder.py:490-522`; `tests/test_video_job_runner.py:1827-1885`; `tests/test_recorder.py:223-327`.

7. **Q: ACA 5400, application <=5100, bounded recorder visibility? — ACA 5400 and bounded recorder values exist; <=5100 is absent and must be asserted by new code/tests.** Video Bicep sets both replica and receive visibility defaults to 5400 and caps editor executions at one. `job_runner.drain` actually passes its environment-derived default of 5400 to the queue. Recorder Bicep sets both replica and visibility defaults to 900, caps recording to 600 seconds, and limits replicas to ten. These enforce relative/bounded infrastructure values but have no application deadline parameter, `5100`, or validation tying application completion to 5100.  
   Provenance: `infra/modules/aca-video.bicep:68-85`, `infra/modules/aca-video.bicep:176-225`, `infra/modules/aca-video.bicep:278-284`; `podcaster/video/job_runner.py:761-771`, `podcaster/video/job_runner.py:1754-1773`; `infra/modules/aca-recorder.bicep:53-81`, `infra/modules/aca-recorder.bicep:86-183`; `tests/test_video_job_runner.py:2808-2835`; `docs/scaleout-recorder-rfc.md:211-231`.

### Exact change/test/documentation surfaces implied by the gaps

8. **Q: Exact call sites/tests/docs? — Supports a bounded cross-cut surface, with elevated one-PR risk.** Any proposed implementation must alter or wrap the runner’s post-compose block (`job_runner.py:1108-1418`), publication-state CAS/evidence functions (`publication_state.py:301-467`), queue producer/consumer configuration (`queue.py:246-280` and ACA module), and the distribution orchestrator (`distribution.py:985-...`). Existing test anchors cover intent failure, canonical-identity denial, duplicate claim blocking, evidence-failure unknown propagation, consumer dispositions, visibility use, provider public semantics, and evidence retention. Documentation requiring revision is the recorder/editor topology/retry RFC and distribution/integration contracts; neither documents the requested timing stages. This is feasible as a contained set of files, but cannot safely be represented as a small mechanical extraction: it introduces an inter-process state machine across admission, execution, evidence/readback, and shutdown.  
   Provenance: `tests/test_video_job_runner.py:110-211`, `tests/test_video_job_runner.py:770-1014`, `tests/test_video_job_runner.py:1827-1885`, `tests/test_video_job_runner.py:2808-2835`; `tests/test_publication_state.py:130-250`, `tests/test_publication_state.py:300-345`; `tests/test_monitoring.py:250-340`; `docs/scaleout-recorder-rfc.md:159-257`; `docs/distribution-ux.md:315-320`; `docs/integration-contract.md:200-280`.

## Gaps and conflicts

- **Missing requested transition/times:** `rendered_pending_distribution`, >=1800 seconds remaining, T+75, T+82, T+85, and application <=5100 have no current source contract. They cannot be mapped to an existing manifest value, provider admission function, deadline source, retry rule, test, or runbook.
- **Atomicity boundary:** Existing CAS protects one blob/document update; queue send and provider mutations are separate calls. The new outbox record could be atomic locally, but provider delivery cannot be atomic with it. The known YouTube post-create/pre-persist duplicate risk must remain explicit or be mitigated by provider reconciliation.
- **Operational conflict:** ACA video Bicep comments call visibility configuration inert until an editor refactor (`infra/modules/aca-video.bicep:278-281`), but the current runner reads and uses it (`podcaster/video/job_runner.py:1763-1768`). Treat the comment as stale pending deployment confirmation; no cloud verification was permitted.
- **Graceful shutdown:** No SIGTERM handler or elapsed-budget-driven handoff exists. A T+85 requirement needs a defined pre/post-mutation shutdown state and queue deletion rule.

## Stop decision

**Stopped at evidence saturation.** The existing code gives precise adjacent call sites and safeguards, but the requested transition and T+ deadlines are new design inputs rather than latent behavior. Further workspace reads would not establish absent runtime policies; resolving them requires parent design decisions and later implementation/test evidence.
