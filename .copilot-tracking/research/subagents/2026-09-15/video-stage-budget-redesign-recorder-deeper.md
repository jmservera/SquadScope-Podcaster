# Cycle 1 / Wave 2 Deeper — Internal evidence lane

## Input contract and preflight

- **Topic:** W38 video stage budget redesign in `jmservera/SquadScope-Podcaster`.
- **Lane:** the same bounded editor/recorder/fan-out/fan-in/clip-manifest/clipset/queue lease/retry/terminal-state architecture.
- **Questions:** determine exact current function signatures, call chains, state schema, test seams, and compatibility risks relevant to one monotonic editor budget; requested T+5/T+20/T+25 and recorder earliest-of 12m/two failures/browser deadline/fan-in cutoff controls; queue visibility/retry eligibility; immutable fallback; and preservation of SSRF, poison ceiling, leases, CAS, and clip-before-manifest.
- **Evidence criteria:** workspace-relative `path:line` citations; facts distinct from design implications; gaps and contradictions recorded. No parent recommendation.
- **Scope / non-goals:** balanced posture; static workspace evidence only. No live operation, production mutation, or source edit. Prior Wave 1 lane artifact is evidence context only.
- **Limit:** stop after sufficient source/test evidence answers the exact integration surfaces and identifies unresolved requested policy semantics.
- **Wave-specific evidence goal:** Deeper — trace concrete contracts and compatibility constraints for parent synthesis.
- **Path preflight:** this artifact is distinct from `.copilot-tracking/research/2026-09-15/video-stage-budget-redesign-research.md`, distinct from Wave 1 evidence, and is under the caller-approved `.copilot-tracking/research/subagents/` path.
- **Final status:** complete; exact static integration surfaces and tests have been traced.

## Research actions

1. Traced the editor entrypoint through fan-out and inline recording, recorder delivery, browser recording, queue receive/delete, storage CAS, and ACA deployment configuration.
2. Read the relevant implementation and focused unit/integration tests, including existing timeout, fan-out, duplicate, lease, navigation, cap, and retry tests.
3. Searched the permitted workspace for literal requested T+5/T+20/T+25, 12-minute, and two-failure policy definitions. None were found; those values are prospective policy inputs, not current behavior.
4. Did not operate any queue/provider/storage service or edit source, configuration, production docs, or the parent artifact.

## Exact current contracts and call chains

### Editor entry to terminal queue disposition

```text
drain(queue, storage, config=None, *, max_messages=32)
  -> queue.receive_messages(max_messages=1, visibility_timeout=_video_visibility_timeout())
  -> process_message(message, *, storage, queue, config=None, now=None)
     -> parse_job_id(message.body)
     -> run_video_generation(job_id, storage, *, config=None, now=None,
                             compose_runner=None, fanout=None,
                             fanout_scratch=None, clip_producer=None)
        -> claim_pipeline(...)
        -> [fan-out] acquire_or_renew_lease(...)
           -> record_via_fanout(job_id, segments, output_dir, *, scratch,
                                 producer=None, timeout_seconds=5400,
                                 poll_seconds=15, sleep=time.sleep,
                                 monotonic=time.monotonic, fill_gap=None,
                                 heartbeat=None)
              -> plan_or_load_clipset(...)
              -> enqueue_missing_clips(...)
              -> wait_for_fanin(... timeout_seconds=5400 ...)
              -> assemble_recording(...)
        -> [legacy] record_episode(...)
  -> delete immediately for successful/skipped outcomes and permanent/malformed/
     exhausted failures; leave transient and foreign-editor-lease messages visible
     for redelivery.
```

- **Provenance:** `podcaster/video/job_runner.py:761-793`, `podcaster/video/job_runner.py:808-865`, `podcaster/video/job_runner.py:1029-1076`, `podcaster/video/job_runner.py:1677-1773`; `podcaster/video/editor.py:179-390`.
- **Fact:** `now` is UTC wall-clock input for state and lease coordination, whereas only `wait_for_fanin` accepts injectable `monotonic`. No current top-level budget/deadline parameter is propagated through this chain.
- **Compatibility implication:** Adding optional keyword-only values at `run_video_generation`, `record_via_fanout`, and `wait_for_fanin` preserves current direct calls/tests; changing their default behavior or positional parameter order would affect the numerous calls documented in `tests/test_video_job_runner.py:594-2785`, `tests/test_editor.py:186-477`, and `tests/integration/test_scaleout_fanout.py:140-224`.

### Clip recorder delivery to terminal sentinel

```text
recorder.drain(queue, scratch, *, max_messages=256, env=None)
  -> queue.receive_messages(max_messages=1, visibility_timeout=_visibility_timeout(env))
  -> process_clip_message(message, *, scratch, queue, record_segment=None, env=None)
     -> parse_clip_job(message.body)
     -> if dequeue_count >= 5:
          write_fallback_manifest(job_id, clip_index, *, scratch, reason) -> delete
        else:
          record_clip(job_id, clip_index, *, scratch, record_segment=None, env=None)
            -> load_clipset(scratch, job_id)
            -> record_segment(segment, output_dir)
            -> upload clip; verify size; _write_manifest_if_absent(...)
          -> delete on recorded/skipped; leave on exception for queue retry
```

- **Provenance:** `podcaster/video/recorder.py:121-124`, `podcaster/video/recorder.py:160-395`, `podcaster/video/recorder.py:479-506`; `podcaster/queue.py:117-190`.
- **Fact:** `RecordSegmentFn` is `Callable[[VideoSegment, Path], RecordResult]`; the production implementation is `_production_record_segment(segment, output_dir)`, which opens Playwright and calls `_record_segment(browser, segment, output_dir, check_accessibility=True, source_url=segment.source_url)`. `podcaster/video/recorder.py:72-75`, `podcaster/video/recorder.py:443-477`.
- **Compatibility implication:** A deadline/budget needs propagation through both the injectable function type and fake test recorders, or an adapter/default must preserve existing two-argument test doubles. Current recorder tests use exactly `(segment, output_dir)`. `tests/test_recorder.py:61-75`, `tests/test_recorder.py:154-169`, `tests/test_recorder.py:299-311`.

### Browser recording and local task retry

```text
record_episode(plan, output_dir=None, headless=True, check_accessibility=True,
               source_url=None, intermediates=None, concurrency=None, brand_name=None)
  -> _record_one(browser, index, segment)
     -> retry_call(lambda: _record_segment(browser, segment, output_dir,
                                            check_accessibility,
                                            source_url=source_url,
                                            brand_name=brand_name),
                   attempts=RECORD_TASK_RETRIES)
  -> _record_segment(...) / _record_generic_segment(...)
     -> Playwright navigation / recovery / waits / scroll
     -> _finalize_segment(...)
        -> screenshot mode: context.close(), subprocess.run(ffmpeg, timeout=600)
        -> legacy mode: context.close(), video.path(), rename
```

- **Provenance:** `podcaster/video/video_gen.py:2432-2610`, `podcaster/video/video_gen.py:2090-2148`, `podcaster/video/video_gen.py:2205-2430`, `podcaster/video/video_gen.py:1146-1269`; `podcaster/retry.py:36-130`.
- **Fact:** `RECORD_TASK_RETRIES` reads `VIDEO_RECORD_TASK_RETRIES`, defaulting to `DEFAULT_TASK_RETRIES`; `DEFAULT_TASK_RETRIES` reads `PODCASTER_TASK_RETRIES` and defaults to **3 total attempts**, not two failures. `podcaster/video/video_gen.py:142-167`; `podcaster/retry.py:27-45`.
- **Fact:** a two-failures cutoff is ambiguous against present terminology: `retry_call(attempts=2)` permits two total failures only when both attempts fail, while “two failures then terminal” might mean at most two failures plus another attempt. No present failure counter is persisted in clip state.

## Current timing and requested-policy fit

| Requested control | Current control / exact location | Evidence relationship and gap |
| --- | --- | --- |
| One monotonic budget at editor entry | `run_video_generation(... now=None ...)` establishes only `current = now or datetime.now(timezone.utc)`. `podcaster/video/job_runner.py:774-805` | **Leaves unresolved:** no `monotonic` argument or budget object at editor entry; a new object would need injection there and remaining-time propagation through both fan-out and inline branches. |
| T+5 / T+20 / T+25 | No literal or equivalent staged timing state found in code, tests, infra, or docs searched. | **Unavailable:** intended event meanings and resulting dispositions are absent. Existing milestones are unrelated independent caps (10s/60s/8s nav, 2.5s consent, 600s capture, 900s recorder, 5400s fan-in/editor). |
| Fan-in cutoff | `wait_for_fanin(... timeout_seconds=5400, poll_seconds=15, sleep, monotonic, on_poll)`. `podcaster/video/editor.py:245-288` | **Supports:** direct remaining-time input seam exists. **Risk:** timeout currently composes partial output rather than raising/marking a distinct cutoff state. `podcaster/video/editor.py:347-390`. |
| Recorder earliest-of 12m / two failures / browser deadline | Deployment hard replica/visibility default is 900s; effective record length defaults 600s; local retry defaults 3 attempts; navigation and finalization each have independent caps. `infra/modules/aca-recorder.bicep:59-69`, `podcaster/video/video_gen.py:110-215`, `podcaster/retry.py:36-130` | **Supports:** per-component cap seams. **Leaves unresolved:** there is no shared “earliest-of” evaluator, browser deadline parameter, persisted failure count, or typed cutoff exception/disposition. A 12-minute (720s) policy is below current 900-second platform and visibility defaults but has no current code representation. |
| Queue visibility bounded to work | Editor: `_video_visibility_timeout(env=None) -> int`, default 5400; recorder: `_visibility_timeout(env) -> int`, default 900. `podcaster/video/job_runner.py:761-771`; `podcaster/video/recorder.py:479-504` | **Supports:** receive-time environment seams. **Leaves unresolved:** queue protocol has only receive/delete/send—no visibility-renew/update method—so an active lease cannot be extended under this interface. `podcaster/queue.py:72-81`, `podcaster/queue.py:225-280`. |
| Retry eligibility | `process_message` leaves only `TransientVideoError` below count 5 and foreign lease messages; recorder leaves any `record_clip` exception below clip count 5. `podcaster/video/job_runner.py:1685-1749`; `podcaster/video/recorder.py:339-395` | **Supports:** existing single classification boundary. **Compatibility risk:** a budget expiry must be explicitly mapped to a current `PermanentVideoError`, `TransientVideoError`, or outcome; otherwise the generic video exception handler converts it to transient, causing redelivery. `podcaster/video/job_runner.py:1417-1521`. |
| Immutable fallback / late recorder | `write_fallback_manifest` and normal manifest write both use `_write_manifest_if_absent`; Azure `update_bytes` retries ETag CAS. `podcaster/video/recorder.py:160-182`, `podcaster/video/recorder.py:185-332`, `podcaster/storage.py:307-333` | **Supports:** fallback is terminal once its manifest wins. **Risk:** raw `.webm` upload remains unconditionally replaceable before terminal-manifest create; comments regard it as harmless content-addressed duplicate work, but source does not prove byte equality. `podcaster/video/recorder.py:239-269`. |

## State and wire schema: exact current state, and integration constraints

### Current durable locations

| Location | Current shape / writer | Constraint for budget-related state |
| --- | --- | --- |
| Main job manifest `generation.video_runner` | `_record_video_state(storage, job_id, state)` replaces the entire `video_runner` mapping with the passed `state`. Present examples contain `status`, `at`, optional `reason`, `error`, `transient`, `failure_family`, `segment_count`, `duration_seconds`, `performance`, and `distribution`. `podcaster/video/job_runner.py:483-503`, `podcaster/video/job_runner.py:1375-1402`, `podcaster/video/job_runner.py:1438-1520` | **Compatibility risk:** any budget progress/checkpoint placed in this mapping must be included by every later whole-map write or it is erased. A sibling generation key avoids that replacement behavior, but selection is parent-owned. |
| Main job manifest `generation.pipeline_lock` | `{pipeline, claimed_at}` in a CAS update; `claimed_at` is wall-clock string. `podcaster/pipeline_lock.py:38-105` | It does not encode a run deadline and must remain compatible with audio/video handoff. |
| Scratch editor lease blob | `{run_id, claimed_at, expires_at}`; immutable only relative to foreign owner via CAS; timestamps are UTC wall-clock. `podcaster/video/editor.py:87-176` | Lease is distinct from a monotonic budget. A monotonic value cannot be meaningful across fresh processes/redelivery if persisted; source has no current stable process-relative budget representation. |
| Scratch `clipset.json` | `{schema_version, job_id, count, clips:[{clip_index,start_seconds,duration_seconds,repo_owner,repo_name,source_url,removed_reason}]}`. `podcaster/video/clipset.py:81-218` | Editor CAS loads existing bytes as authority. Changing plan schema requires preserving `from_dict` compatibility or bumping schema; current parser reads supplied/default schema string but does not reject unknown versions. |
| Scratch clip terminal manifest | `ClipManifest` fields plus recorder-only extras `status` (`success`/`fallback`), optional `failure_reason`, `has_pages`, `website_url`, `is_removed`, `recovery_path`. `podcaster/video/clip_manifest.py:166-232`; `podcaster/video/recorder.py:127-154` | `ClipManifest.from_dict` ignores unknown extras, providing additive-field compatibility. Editor treats manifest *presence*, not `status`, as terminal and permissively reads extras. `podcaster/video/editor.py:245-288`, `podcaster/video/editor.py:394-426`. |
| Clip queue body | Base64 JSON normally `{schema_version,job_id,clip_index}`; parser accepts raw JSON and absent schema. `podcaster/queue.py:117-190` | A deadline/failure count in queue body would be a wire-schema expansion and must preserve raw/absent-schema compatibility or deliberately tighten it. The current record path gets no per-message policy state except `dequeue_count`. |

### Evidence-grounded schema needs, without selecting a design

For the requested controls to survive the currently separate boundaries, parent synthesis needs a decision on **where** each of these facts lives:

1. **Editor-owned budget identity/start and policy version:** current entry has no durable budget state; `generation.video_runner` replacement semantics mean a new mapping there needs merged writes, while a sibling state avoids collision. `podcaster/video/job_runner.py:483-503`.
2. **Recorder-receivable deadline/policy:** the recorder process only receives `(job_id, clip_index)`, queue dequeue count, clipset, and environment. `podcaster/video/recorder.py:339-395`; `podcaster/queue.py:117-190`. A shared editor monotonic deadline cannot be reconstructed after delivery without either a durable wall-clock translation or an explicit recorder-local deadline/policy.
3. **Terminal cutoff reason and failure count:** existing terminal manifest permits additive `failure_reason`, but no counter; queue `dequeue_count` is transport delivery count and includes redelivery causes besides browser failure. `podcaster/video/recorder.py:279-332`; `podcaster/queue.py:63-78`. Thus it is not source evidence for a “two browser failures” counter.
4. **Terminal state validation:** present sentinel presence causes fan-in success even if recorder `status` is absent/invalid, and manifests do not cryptographically bind blob content or plan version. `podcaster/video/editor.py:245-288`, `podcaster/video/editor.py:394-426`. This preserves backwards compatibility but is a constraint on any stronger state contract.

## Preservation requirements and exact safeguards

- **Fallback terminality / races:** retain manifest-last ordering (upload → size verify → conditional manifest) and `_write_manifest_if_absent` for both success and fallback. The pre-record and post-record sentinel checks plus conditional write prevent a late worker from changing the terminal manifest. `podcaster/video/recorder.py:185-275`, `podcaster/video/recorder.py:279-332`. Tests: `tests/test_recorder.py:95-111`, `tests/test_recorder.py:203-270`.
- **Poison ceiling:** preserve `MAX_DEQUEUE_COUNT = 5` in both editor and recorder flows, malformed-message deletion, and fallback-at-poison behavior. `podcaster/video/recorder.py:56-58`, `podcaster/video/recorder.py:339-395`; `podcaster/video/job_runner.py:119-165`, `podcaster/video/job_runner.py:1685-1749`. Tests: `tests/test_recorder.py:231-258`, `tests/test_video_job_runner.py:1830-1905`.
- **Leases:** preserve editor lease claim before expensive work, fresh-time heartbeat at fan-in polls, CAS release only by owner, and foreign-lease redelivery. `podcaster/video/editor.py:126-176`; `podcaster/video/job_runner.py:851-865`, `podcaster/video/job_runner.py:1032-1055`, `podcaster/video/job_runner.py:1738-1749`. Tests: `tests/test_editor.py:312-477`; `tests/test_video_job_runner.py:2640-2774`.
- **Clip-before-manifest:** preserve no-manifest-on-failed size verification and re-record/gap-fill treatment of blob-without-sentinel. `podcaster/video/recorder.py:225-275`; `podcaster/video/editor.py:318-332`. Tests: `tests/test_recorder.py:128-201`; `tests/test_editor.py:273-309`.
- **SSRF:** Python `safe_urlopen` guards scheme, initial host, redirects, and connect-time DNS addresses. `podcaster/ssrf.py:109-190`, `podcaster/ssrf.py:190-391`. This safeguard is used for watermark handling; browser `page.goto` remains outside demonstrated guarded-fetch coverage. `podcaster/video/video_gen.py:1890-1939`, `podcaster/video/video_gen.py:2090-2148`, `podcaster/video/video_gen.py:2315-2373`. Tests: `tests/test_ssrf.py:39-80`, `tests/test_video_job_runner.py:2827-3098`.

## Exact test seams and required compatibility coverage

| Change surface | Existing seam / tests to preserve | Missing test needed for requested policy |
| --- | --- | --- |
| Editor budget and fan-in cutoff | Injectable `sleep`, `monotonic`, `on_poll`, `heartbeat`, `fill_gap`; `tests/test_editor.py:186-477` | Deterministic shared-clock tests for start, T+5/T+20/T+25 semantics (once defined), remaining-time clamp, zero/negative remaining time, and no compose/publish after a specified cutoff if that is the intended disposition. |
| Video entry and queue eligibility | `now` injectable on `run_video_generation`/`process_message`; mockable queue and fanout; `tests/test_video_job_runner.py:1830-1905`, `tests/test_video_job_runner.py:2640-2825` | Budget expiration classification test at `process_message`, including delete vs leave-for-redelivery and lease release; configured visibility must be tested against requested maximum. |
| Recorder cap / browser deadline | injected `record_segment`; fake browser; patchable `MAX_CLIP_RECORD_SECONDS`; `tests/test_recorder.py:61-75`, `tests/test_recorder.py:279-358`; `tests/test_video_gen.py:1170-1260` | Earliest-of test with an injectable monotonic clock / fake browser, 720-second boundary, two-failure semantics, cleanup/fallback write, and no accidental legacy retry beyond the policy. |
| Per-task retry compatibility | `retry_call` has injectable sleep and `RECORD_TASK_RETRIES` is patchable; `tests/test_video_gen.py:2479-2565`, `tests/test_retry.py` | Explicit test that prospective two-failure rule is not silently defeated by current three-total-attempt default/backoff. |
| Immutability / late recorder | local fake scratch and direct conditional-write tests; `tests/test_recorder.py:203-270`; barrier tests `tests/test_editor.py:223-309` | Race test where fallback wins while a recorder is between upload and manifest write; assert manifest bytes/reason remain unchanged and fan-in accepts only the terminal winner. |
| Infrastructure contract | recorder `replicaTimeout=900`, visibility=900, cap=600; `infra/modules/aca-recorder.bicep:59-99`, `infra/modules/aca-recorder.bicep:164-179`; editor defaults 5400; `infra/modules/aca-video.bicep:68-83`, `infra/modules/aca-video.bicep:189-282` | Deployment/config validation for any changed 12-minute relation, including whether visibility is <= or >= replica timeout. Current recorder Bicep says visibility must be <= replica timeout, while RFC prose says clip visibility >= record time and ACA timeout above the processing budget. `docs/scaleout-recorder-rfc.md:205-220`. |

## Material gaps and contradictions

1. **Requested timing semantics absent:** no source establishes what T+5, T+20, or T+25 measure, what actions occur, or whether T+25 is terminal/retryable. This is the smallest required parent clarification.
2. **“Earliest-of” precedence is unspecified:** 12m, two failures, browser deadline, and fan-in cutoff occur in different processes/layers; there is no current shared clock, counter, typed timeout, or state machine to establish precedence.
3. **Monotonic persistence boundary:** a Python `time.monotonic()` value is process-local; recorder and editor redelivery run in different processes. The workspace has UTC lease timestamps but no durable budget conversion contract. A single *editor-local* monotonic deadline is directly testable; cross-worker sharing needs a separately defined representation.
4. **Queue visibility limitation:** `QueueBackend` does not expose an update/renew visibility operation. Any design requiring extension during a long active record needs a protocol/backend change, not only a policy parameter.
5. **Infrastructure inequality ambiguity:** recorder Bicep documents visibility `<= replicaTimeout` and sets both to 900, whereas RFC prose says visibility should cover max record time and ACA should be above the processing budget. The current equality is consistent with Bicep but does not resolve the prospective 720-second/two-failure policy relation.
6. **Fallback preservation is manifest-level:** CAS makes the terminal manifest immutable, but not the preceding raw clip blob. Stronger raw-blob immutability would be a behavioral/security compatibility choice beyond evidence.
7. **SSRF scope gap:** guarded Python HTTP is robustly tested; Playwright/browser navigation URL enforcement is not demonstrated in this lane.

## Provenance

- Retrieved from workspace on 2026-09-15: `podcaster/video/job_runner.py:438-565`, `podcaster/video/job_runner.py:730-793`, `podcaster/video/job_runner.py:808-865`, `podcaster/video/job_runner.py:1029-1076`, `podcaster/video/job_runner.py:1370-1521`, `podcaster/video/job_runner.py:1677-1773`.
- Retrieved from workspace on 2026-09-15: `podcaster/video/editor.py:60-466`, `podcaster/video/recorder.py:50-510`, `podcaster/video/clipset.py:1-224`, `podcaster/video/clip_manifest.py:160-232`, `podcaster/queue.py:63-280`, `podcaster/storage.py:307-333`, `podcaster/pipeline_lock.py:1-185`, `podcaster/retry.py:1-130`.
- Retrieved from workspace on 2026-09-15: `podcaster/video/video_gen.py:100-215`, `podcaster/video/video_gen.py:500-705`, `podcaster/video/video_gen.py:809-890`, `podcaster/video/video_gen.py:1146-1269`, `podcaster/video/video_gen.py:1813-2033`, `podcaster/video/video_gen.py:2090-2610`; `podcaster/ssrf.py:109-391`.
- Retrieved from workspace on 2026-09-15: `infra/modules/aca-recorder.bicep:45-190`, `infra/modules/aca-video.bicep:45-300`, `docs/scaleout-recorder-rfc.md:155-230`, `tests/test_editor.py:186-477`, `tests/test_recorder.py:1-358`, `tests/test_video_gen.py:1170-1260`, `tests/test_video_gen.py:1420-1495`, `tests/test_video_gen.py:2470-2565`, `tests/test_video_job_runner.py:1830-1905`, `tests/test_video_job_runner.py:2640-2825`, `tests/integration/test_scaleout_fanout.py:120-235`.

## Stop decision

- **Reason:** deeper lane criteria met. The exact current signatures, call chains, durable schemas, platform settings, test seams, and compatibility constraints are captured. Requested T+ meanings and earliest-of precedence are missing inputs rather than discoverable static evidence.
