# fix(distribution): provider terminal truth and outbox remediation

> [!WARNING]
> **OPEN / DRAFT / BLOCKED — Basher rejected head `df473dc0c059680b9c454ddab263c5c454e2ef2b` because ownership could transfer after the post-compose check and before archive, outbox/notification, direct-provider, or terminal-success mutation. Frank completed the sole-author durable ownership/fence correction; fresh independent Rusty acceptance is pending.** Upstream `jmservera/SquadScope#773` is merged/check-green, completing P00-T01. P05-T03, merge authorization, P05-T04–P05-T06, and P06 remain pending. #682 remains open. This PR is not merge-ready, deployment-ready, canary-accepted, or production-accepted.

Livingston revision base: `fa3426fa030193e89a58cdb927c81a360df24a03`.
Rejected Ralph source: `e16963243973707ea2557f75f925d3c6935d49ee`.
Rejected final-media head: `d050d68c3590f9a00b60dee925452f971cbaf0d2`.
Current source/tests commit: `8cc5da21b85b0ec73dab0f39293aab06149ac7a7`.
Lifecycle correction source/tests commit: `f6b713530947236c04f822289343d3f105cc6dc9`.
Final delivery commit: the pushed PR head; exact SHA is recorded in the delivery return.
Current sole revision author: Frank.
Fresh independent reviewer: Rusty is reserved and pending.

## Frank stale-owner downstream-boundary correction

One persisted video execution claim now binds the exact job, owner, claim/execution identity,
monotonic fence, queue visibility expiry, editor lease expiry, and durable per-boundary permits.
CAS plus authoritative readback fails closed on mismatch, expiry, corrupt/unavailable state, or
ambiguity.

Final candidate promotion, immutable archive, outbox creation, notification reservation/send/sent
marking, direct-provider intent/mutation, and terminal success each consume a current permit.
Artifact/outbox/notification records carry the source permit. Direct provider mutation is
non-takeover: a successor may reconcile the consumed intent but cannot issue a duplicate mutation.
Idempotent archive/outbox handoffs may be adopted and reconciled by the new owner.

Validation passed: exact ownership probes `13`; focused race/provider regressions `416`; locked
terminal-truth contract `940`; full repository `3302 passed, 2 skipped, 2 deselected, 1 warning`;
Ruff/format/compile/diff; Bicep with existing BCP318; exact Checkov `36/7`; CI Checkov `34/0`;
Dockerfile baseline; container
`sha256:0324e4661e5b22038c3dd1ce54aebb44b2314d4acb83d664c6a7e880f925fc7a`
with UID `999`, ffmpeg/ffprobe/import smoke and worker exit `2`; rebuilt Compose integration `3`;
and changed-line secret/PII scan.

P05-T03 remains pending Rusty. #684 stays open/draft/blocked and #682 stays open. No merge,
deployment, workflow dispatch, provider mutation, W39 execution, or P06 credit is authorized.

## Livingston lifecycle-budget and bounded-shutdown correction

The queue receive visibility deadline now flows through the job context into final-media
validation. Fan-out validation renews the editor lease once immediately before decode and retains
that fixed lease deadline for the post-decode ownership check. The complete decode timeout is
`min(configured maximum, remaining ownership budget - cleanup/promotion reserve)`; insufficient,
invalid, lost, or expired ownership fails before ffmpeg launch or before atomic promotion.

Timeout cleanup targets the ffmpeg process group with SIGTERM and a bounded wait, then SIGKILL and
a second bounded wait. A still-unreaped process is explicitly logged and reported without an
unbounded wait; stderr closes on every path and remains capped at 16 KiB. Full-stream decode,
real truncation/corruption detection, existing-destination preservation, staged-only cleanup, and
no archive/outbox/provider continuation remain intact.

Validation passed: exact lifecycle/shutdown probes `30`; compose and job runner `442`; locked
lifecycle/provider contract `726`; full repository `3283 passed, 2 skipped, 2 deselected, 1
warning`; Ruff/format/compile/diff; Bicep with existing BCP318; exact Checkov `36/7`; CI Checkov
`34/0`; Dockerfile baseline; container
`sha256:a1550c94691e117ce15f54345567cf074000653f5e75ceb9b6d0dcc0224011e1`
with UID `999`, ffmpeg/ffprobe/import smoke and worker exit `2`; rebuilt Compose integration `3`;
and changed-line secret/PII scan.

During validation the shared remote branch gained unrelated provider-transition commit `a8f4730`.
Its history was preserved and its out-of-scope net changes were reverted by `2c12655` before the
authorized correction. The net diff from rejected head `86f96bb` contains only this lifecycle
correction and required evidence updates.

P05-T03 remains pending Rusty. #684 stays open/draft/blocked and #682 stays open. No merge,
deployment, workflow dispatch, provider mutation, W39 execution, or P06 credit is authorized.

Corrected evidence:
[r4074996598](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4074996598)
on the relevant #682 final-media thread and
[issuecomment-5781771685](https://github.com/jmservera/SquadScope-Podcaster/pull/684#issuecomment-5781771685)
in response to Basher's #684 rejection.

## Leela final-media complete-decode correction

Final promotion now requires both the existing exact ffprobe stream/duration contract and a
successful complete ffmpeg decode of all audio/video streams to null. The decode uses `-xerror`,
exploding error detection, no stdin, a restricted local protocol whitelist, process-group
termination, a 30-minute timeout within the 90-minute stage budget, and a bounded 16 KiB stderr
tail. Missing ffmpeg, launch failure, timeout, corrupt packets, invalid NAL data, or any non-zero
decode fails closed before `os.replace`.

Real generated fast-start H.264/AAC coverage accepts the intact file and rejects 99%, 90%, 75%,
50%, and 25% truncations plus middle-byte corruption while metadata remains readable. Existing
destination preservation, staged-only cleanup, and no archive/outbox/provider continuation are
also asserted.

Validation passed: exact probes `22`; complete compose `320`; focused media/provider safety `677`;
locked terminal-truth `1241` with one existing warning; full repository `3275 passed, 2 skipped,
2 deselected, 1 warning`; Ruff/format/compile/diff; Bicep with existing BCP318; exact Checkov
`36/7`; CI Checkov `34/0`; Dockerfile baseline; container
`sha256:659d0808a77b136c35088ab7042a3cdae67731df3f53cf866ee936b89d5dc7ac`
with UID `999`, ffmpeg/ffprobe/import smoke and worker exit `2`; rebuilt Compose integration `3`;
and changed-line secret/PII scan.

P05-T03 remains pending Basher. #684 stays open/draft/blocked and #682 stays open. No merge,
deployment, workflow dispatch, provider mutation, W39 execution, or P06 credit is authorized.

Corrected #682 evidence reply:
[r4074434076](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4074434076)
supersedes the metadata-only statement in r4073299841 and cites the exact complete-decode source,
real-media probes, destination/staged cleanup proof, and no-downstream-visibility regression.

Upstream prerequisite: jmservera/SquadScope#773, reviewed head
`d75e3f5523f4810edbcaeef9a217d34cd21825a2`, merged as
`7a6d8811bf82507cbdd0b01ba1135bc42e5942f3` at `2026-09-22T09:20:43Z`, with all
18 PR checks and merge-SHA CI/security/release/deploy workflows successful.

## Leela RV-008 exact typed canonical correction

Leela alone authored the new cycle from review head `c59669405018f7fa7f9b470568d22e8e474d6f6c`.
The correction replaces ordinary Python equality with deterministic type-tagged canonical JSON byte
comparison for recovery authorization sets, envelopes, evidence, histories, attempt/event records,
and successor expectations. Exact versioned schemas are enforced before digest/equality checks.
Floats (including `1.0` and `-0.0`), NaN/infinity, unsupported containers, non-string keys,
coercions, duplicate JSON fields, legacy versions, and invalid types fail closed.

The explicit `authz_count=true` and `authz_count=1.0` bypass probes now deny mutation. A
parameterized bool/int/float/string/null matrix traverses every scalar leaf across the authorization
set, envelope, evidence, history, predecessor attempt/events, successor, and versions. Valid
canonical JSON round-trip still grants exactly one mutation-capable claim and replay is read-only.

Validation passes: focused `241`; locked `916` with one existing warning; full `3246 passed, 2
skipped, 2 deselected, 1 warning`; Ruff/format/compile/diff; Bicep with existing BCP318; exact
Checkov `36/7`; CI Bicep Checkov `34/0`; Dockerfile baseline; rebuilt container
`sha256:89f3dad52ff9583ec92a6501e6132a67d96383fcacef5ded6c95153088c2eecc` with UID `999`,
ffmpeg/ffprobe/import smoke; worker exit `2`; and changed executable diff secret/PII scan.

Basher independently accepted exact reviewed head `905a890c6a2176c799a5cd5f54fbb01d4c791aa9`
with no blocking findings. PR #684 remains open/draft/blocked pending P00-T01, P05, P06, and
operator-only #682. No issue, review thread, deployment, canary, merge, or production state was
mutated.

## P05-T03 PR #682 disposition evidence

Basher's review found three prior dispositions were not source-backed. Livingston corrected them
through `b7e3615` and posted exact replacement evidence:

- [r4073299493](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4073299493):
  exhausted chunk transport/status uncertainty is mutation-ambiguous, persists
  `provider_unknown`, and cannot issue a duplicate mutation on redelivery.
- [r4073299679](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4073299679):
  unavailable, throwing, null, and incorrect checkpoint size probes fail closed.
- [r4073532947](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4073532947):
  boolean/string/float size reports also fail closed instead of coercing.
- [r4073299841](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4073299841):
  final output is staged, strictly media-probed, and atomically promoted only after validation.
- [r4073533159](https://github.com/jmservera/SquadScope-Podcaster/pull/682#discussion_r4073533159):
  non-finite final duration is explicitly rejected.

P05-T03 remains pending Rusty's independent acceptance of the corrected final SHA.

Validation: focused `422`; locked terminal-truth contract `920` with one existing warning;
rebuilt Compose integration `3`; full pytest `3261 passed, 2 skipped, 2 deselected, 1 warning`;
Ruff/format/compile/diff; Bicep with existing BCP318; exact Checkov `36/7`; CI Checkov `34/0`;
Dockerfile baseline; container `sha256:e12f2423bb418d66e8c7b2d601ebe3f0f2f77df35d86a8933b483cebeca15b37`
with UID `999`, ffmpeg/ffprobe/import smoke; worker exit `2`; changed-content secret/PII scan.

All 67 review threads on #682 were inspected and remain resolved. The earlier 17 replies are
historical evidence; the three corrected replies above supersede their inaccurate dispositions.
The plan's authoritative ledger covers every W17–W29, RV-006, and later-current row. No thread was
resolved, reopened, or re-resolved during this revision.

P05-T03 remains pending Rusty. #682 may be closed as superseded only after #684 is independently
accepted and merge-ready with required checks green. P05-T04–P05-T06 and P06 remain future. This
evidence update does not authorize merge, deployment, workflow dispatch, provider mutation, W39
execution, or P06 credit.

## Leela final-SHA rejection

Leela independently reviewed comparison base `d7eb7ba53b6024812a33a1abc9d2961bd3ddd1b0`, Fry source commit `7f00b5795117f144cb23615d59f92029246162fe`, and exact final head `273f94e0d1fa773e108661f908aca6f34be132c4`.

Verdict: **Not accepted (`request_changes`)** — 0 Critical, 1 High, 0 Medium, 0 Low.

Fry's six bypasses and the full envelope/history/supersession/concurrency/replay matrix otherwise fail closed. The remaining High is exact authorization-set scalar typing: `_validated_recovery_authorization_collection()` compares the persisted set with ordinary Python equality, so `True == 1` and `1.0 == 1`. With exactly one recovery authorization, mutating only `recovery_authz_set.authz_count` to boolean `true` or float `1.0` returned `read_only=False`.

Required correction: enforce the exact v1 set field schema and scalar/container types before comparison, and compare canonically typed values so boolean/float cardinality values cannot satisfy integer count. Preserve every current v4 envelope/history, active/superseded chain, concurrency, and single-use guarantee.

Independent validation: conformance `57` cases with `55` fail-closed and two exact type bypasses; outbox `182`; locked `902`; full `3232 passed, 2 skipped, 2 deselected, 1 warning`; Ruff/format/compile/diff, Bicep, CI Checkov `34/0`, Dockerfile Checkov, container smoke, worker exit `2`, and secret/PII scan passed. Exact Checkov retained the documented `36/7` baseline. Review image: `sha256:001405ef2dee42fdb29d85ad91edc0ffb816b3e7671a4165b01808e8895bc1ad`.

PR #684 remains open/draft/CLEAN with 13 successful checks and zero review threads. No related issue, thread, deployment, canary, merge, or production state was mutated.

## Fry RV-008 complete-envelope correction

The correction introduces `distribution-recovery-authz-v4` and
`distribution-recovery-authz-set-v1`. Every authorization field is exact and type checked,
explicit-null actor/owner fields and the extensions map are bound, structured evidence and digest
are recomputed, predecessor/successor expectations are exact, and the complete ordered collection
is bound by count, IDs, active ID, and digest. Historical authorizations are deterministically
superseded and linked; exactly one final active authorization may match the boundary.

All six reported bypasses now fail closed: source, reason, and authorization-time mutation; unknown
field insertion; duplicate matching authorization; and unrelated appended authorization. Every
envelope field removal and type/null mutation, unknown/legacy version, non-empty extensions,
duplicate/reordered/extra/conflicting collection, and set-manifest mutation is denied. Exact
unchanged evidence grants one mutation-capable claim; concurrent authorization and claim each have
one winner; reuse is read-only; post-claim tampering is rejected.

Validation passes: outbox `182`, focused `227`, locked `902`, full `3232 passed, 2 skipped, 2
deselected, 1 warning`; Ruff/format/compile/diff, Bicep, exact Checkov `36/7`, CI Checkov `34/0`,
Dockerfile Checkov, container smoke, worker exit `2`, and changed-diff secret/PII scan. Container:
`sha256:fb1af564ee7386379fa700886b2d26367c4857d1743a4bca8053953b3ab7ba8a`.

## Fry final-SHA rejection

Fry independently reviewed exact comparison `3529a027d68c3811274237a49202dafc87d33c70..98eae68fe25b429cc59a36fff97a7154979d2bda`, focused on Frank source commit `bfead2572ae7c98bf82122281ac3a26ff3b91edc`. The later final-head commit changed tracking and PR narrative only. Local, origin, and PR head matched with divergence `0/0`; the PR was open/draft/CLEAN with 13 successful checks, no reviews, and zero review threads.

RV-008 remains **High**. The complete predecessor attempt/event history, nested provider evidence, publication identity, record versions, and exact successor now fail closed under mutation, omission, insertion, duplication, reorder, replay, and reuse. Exact unchanged evidence grants one mutation-capable claim; concurrent claim has one winner; later reuse is read-only; failed predecessors remain immutable.

However, the durable `recovery_authz` record itself is not exact or unique. Changing its `source`, `reason`, or `authorized_at`, adding an unbound audit field, duplicating the matching authorization, or appending an unrelated authorization record still leaves the successor's first claim mutation-capable:

`RV008_AUTHORIZATION_ENVELOPE_BYPASS cases=6 read_only=False`

Required correction: bind and recompute the complete authorization record, require exactly one matching authorization for the bound successor, and reject changed, missing, additional, duplicated, reordered, or unrelated authorization records while preserving the complete predecessor-history and single-use successor behavior.

Independent validation passed: owner conformance `54/72`, focused `171`, locked `846` with one warning, and final full repository `3176 passed, 2 skipped, 2 deselected, 1 warning` after rebuilding the documented stale Compose recorder image. Ruff, format, compile, diff safety, Bicep, exact Checkov `36/7`, CI Checkov `34/0`, Dockerfile Checkov, container smoke, worker exit `2`, and changed-diff secret/PII scan passed. Review image: `sha256:5fe2fd6ee70a30882635e98eab2fcd62c99b1c5a4ba739e328fb96ec1c765049`.

## Incident and acceptance truth

Attempt-level truth is immutable and distinct from weekly identity delivery. Every failed, partial, unknown, manual-action, or conflicting attempt must remain visible beneath any later weekly recovery; a later success must not overwrite attempt history.

W38 may be classified `published_verified_recovered` only if evidence binds the exact week/publication identity, manifest and publication digest, canonical artifact, authorized succeeding attempt, expected provider item, authoritative terminal provider readback, and resolved duplicate state. That proof has not been established by the current executable fixtures, so this PR does not assert W38 recovered-green.

W39 remains the incident and is `missed_not_dispatched`. It has no synthesis, recorder, video, outbox, or provider attempt because dispatch was blocked before Azure.

Green weekly success requires all of the following:

- Exact week and publication identity.
- Exact manifest/digest identity and canonical artifact.
- Expected provider item identity.
- Authoritative external readback proving terminal visibility and state.
- No unresolved duplicate ambiguity.
- Authorized recovery backed by durable proof, with no blind retry after an unknown mutation.

Four future post-fix cycles must each end `published_verified` or controlled `published_verified_recovered`. `partial`, `provider_unknown`, `manual_action_required`, `identity_conflict`, missing readback, duplicate ambiguity, or any other incomplete proof is non-green.

## Authoritative exact-W39 production acceptance gate

After every required upstream `jmservera/SquadScope` and Podcaster fix is reviewed and merged, exact merge-SHA-derived artifacts are proven deployed, and all review/deployment/readiness/rollback/provider-authority gates are clear, P05-T06 must execute the actual W39 generation and publishing pipeline.

Before any mutation, the operator must reconcile every existing W39 intent, dispatch, receipt, Podcaster job, Azure synth/recorder/video execution, immutable attempt, and provider candidate against exact W39 identity. The run may proceed only when authoritative evidence proves no existing or ambiguous W39 provider publication. Existing, conflicting, unknown, or incomplete state must fail closed to `manual_action_required`; duplicate publication is forbidden.

Acceptance requires one sanitized correlation chain containing:

- actual GitHub dispatch/run IDs and safe URLs;
- upstream publication identity and dispatch result;
- Podcaster accepted job and correlation IDs;
- Azure synth, recorder, and video execution/job IDs;
- immutable attempt IDs and reconciliation decisions;
- provider item identity/state, safe provider URLs where non-sensitive, and authoritative terminal external readback;
- any manual-action blocker.

Tests, CI, GitHub workflow success, Azure internal success, queue completion, ACA exit 0, and weekly labels are not production acceptance. The historical W39 incident remains `missed_not_dispatched` until later recovery evidence exists and remains distinct from that recovery execution. The mandatory W39 run does not automatically count toward P06; it counts only if it independently meets the future scheduled-cycle timing and proof criteria, and the four-cycle requirement is not reduced.

## Implemented scope

The branch contains the Podcaster-side P00 receipt/absence boundary and Amy's declared in-repo P01-P04 implementation:

- Durable dispatch acceptance and absence evidence with cross-boundary correlation.
- Immutable attempt history, separate weekly aggregation, fenced claims, recovery records, reconciliation scheduling, and cleanup changes.
- Provider intent, receipt, verification, readback, and fail-closed/manual-handoff behavior.
- Truthful worker/ACA exits, telemetry, alert infrastructure, runbook updates, and fault/concurrency/lifecycle coverage.

Completion of the declared implementation scope does not imply acceptance. Fry's final review resolved the alert and tracking findings but rejected `02241a1` for scheduler lease recovery, legacy cleanup safety, recovery authorization, and four-cycle identity binding.

## Frank correction after rejecting Livingston

Frank is the sole author of this correction. Livingston is locked out for this cycle. Fry is reserved for fresh independent final-SHA review. Bender, Hermes, Amy, Leela, Farnsworth, Rusty, Basher, and Ralph did not author, advise, pair, or contribute.

Recovery authorization now uses `distribution-recovery-authorization-v3` with a nested `distribution-attempt-history-evidence-v1` envelope. The envelope records exact publication/outbox/provider context, predecessor boundary ID/index and counts, a deterministic typed canonical representation of every complete durable predecessor attempt/event in order, and its SHA-256 digest. New attempts carry `distribution-attempt-record-v1`; missing/unknown versions and malformed or ambiguous attempt/event order fail closed.

The complete durable attempt mapping is bound, so identity, sequence, status/classification, provider evidence and mutation possibility, event type/state, owner/execution/claim, fence/lease/time, intent/receipt/readback, proof references, recovery linkage/authorization, and future durable fields cannot change undetected. The exact pending successor record is separately bound before claim, while its recovery-proof digest is independently checked to avoid circular hashing. Only the first exact authorized successor claim can be mutation-capable; any later claim or unexpected appended event is read-only.

The exact Frank bypass is denied, as are parameterized semantic field mutations, duplicate/omit/reorder/insert attempt/event changes, unexpected post-authorization events, legacy schema, and cross-week/publication replay. Canonical round-trip is stable and exact unmodified history is accepted once. All prior RV-008 and RV-002/RV-003/RV-004/RV-007/RV-009 probes remain green.

Validation passed: outbox `126`, focused correction `171`, locked contract `846` with one warning, and final full repository `3176 passed, 2 skipped, 2 deselected, 1 warning`. The first full run reproduced only the documented stale Compose recorder image; rebuilding it made the focused fanout and final full suite pass. Ruff, format, compile, diff safety, Bicep, exact Checkov baseline `36/7`, CI Checkov `34/0`, Dockerfile Checkov, container smoke, worker exit `2`, and changed-diff secret scan passed. Validation image: `sha256:cb3630ceb8910c65a65f82e93fbe4f4a08eafa34cebdf0d2b15ead1e2b9a005c`.

## Livingston correction after rejecting Ralph

Livingston is the sole author of the current correction. Frank is reserved for fresh independent final-SHA review and did not contribute. Bender, Hermes, Amy, Leela, Fry, Farnsworth, Rusty, Basher, and Ralph did not author, advise, pair, or contribute.

Recovery authorization now uses an auditable `distribution-recovery-authorization-v2` structure rather than relying on a partial field set plus an opaque digest. It binds the exact provider kind/objective/item, allowed operation name and type, consumed intent ID, exact receipt ID, predecessor attempt, consumption owner/fence/time, receipt provider/operation/attempt/owner/time/order, complete week/publication/manifest/digest/artifact identity, terminal readback identity/state/time/fence, ordered attempt history, and specifically authorized succeeding attempt/provider expectation.

Authorization creation recomputes and compares the exact caller structure. The stored successor-enriched structure is recomputed again before a successor claim may mutate. Missing, extra, duplicate, reordered, stale, conflicting, swapped, mutated, or legacy-v1 evidence makes the claim read-only. `unrelated_read_only_probe`, mutated intent/receipt IDs, provider-swapped receipts, changed owner/fence/time/item/readback/binding fields, and successor aliasing are denied. Exact evidence is accepted only for its bound successor. Immutable predecessor attempts and all resolved findings are preserved.

Validation passed: required RV-008 probes `20/68`, outbox `88`, focused correction `133`, locked contract `808` with one warning, and final full repository `3137 passed, 3 skipped, 2 deselected, 1 warning`. The first full run reproduced only the documented stale Compose recorder image; rebuilding it made the final suite pass. Ruff, format, compile, diff safety, Bicep, exact Checkov baseline `36/7`, CI Checkov `34/0`, Dockerfile Checkov, container smoke, worker exit `2`, and changed-diff secret/PII scan passed. Validation image: `sha256:c427f35291962193a83890f94549485745830d2008ea9d7231caf7931a4ae9fc`.

## Frank final-SHA rejection

Frank independently reviewed exact comparison `fa3426fa030193e89a58cdb927c81a360df24a03..f3c5e643d9068a83e87bd2ef6c8ac120d312519f`, focused on Livingston source commit `829fae69c4f20da18d34bae15f53c1cb21794808`. The later final-head commit changed tracking and PR narrative only. Local, origin, and PR head matched; divergence was `0/0`; the PR was open/draft/CLEAN with 13 successful checks, no reviews, and no review threads.

RV-008 remains **High**. Authorization v2 stores `prior_attempt_ids` rather than a canonical binding of the complete ordered durable attempt records. After a valid third-attempt authorization, Frank changed the first attempt's claimed event timestamp to `1999-01-01T00:00:00Z`, execution identity to `forged-earlier-owner`, and fence to `999999`. The successor still claimed mutation authority:

`RV008_COMPLETE_HISTORY_MUTATION_BYPASS attempts=3 read_only=False`

Required correction: bind and recompute complete ordered attempt history, including durable event owner/execution/fence/timestamp/order evidence, so missing, extra, duplicate, reordered, swapped, or mutated earlier history fails closed. Preserve the current exact provider/operation/intent/receipt/publication/artifact/readback/successor bindings and the exact positive single-use path.

Frank independently reproduced focused `133`, locked `808` with one warning, and final full `3137 passed, 3 skipped, 2 deselected, 1 warning` after rebuilding the known stale Compose recorder image. Ruff, format, compile, diff safety, Bicep, exact Checkov `36/7`, CI Checkov `34/0`, baseline-aware Dockerfile Checkov, container smoke, worker exit `2`, and exact-diff secret/PII scan passed. Review image: `sha256:da9825c04e9248453e5925c02367e52d1db62726f50e035c2cd8176f4a37f2a3`.

## Ralph correction after Basher rejection

Ralph is the sole author of the reviewed correction. Livingston did not contribute and independently completed the final-SHA review with verdict Not accepted. Bender, Hermes, Amy, Leela, Fry, Farnsworth, Frank, Rusty, and Basher did not contribute or advise.

Recovery of the latest `provider_unknown` attempt now requires one consumed explicit operation identity and exactly one usable durable receipt for every requested provider. The receipt must match the intent ID, consumption fence and timestamp, accepted transport class, exact expected provider item, non-ambiguous classification, and native provider state. Zero, duplicate, conflicting, stale, malformed, wrong-kind, wrong-item, wrong-operation, or provider-partial receipts fail closed.

The authoritative post-terminal failed readback must uniquely resolve that same provider item. Terminal attempts remain reconciliation-only; only the successor created by the exact authorization can become mutation-capable. Immutable history and all week/publication/digest/artifact and authorization bindings remain intact.

Validation passed: focused RV-008 `30/45`, outbox `75`, focused correction `119`, locked contract `794` with one warning, and final full repository `3125 passed, 2 skipped, 2 deselected, 1 warning`. The first final full run reproduced only the documented stale Compose recorder image; rebuilding it made the fanout integration and final full suite pass. Ruff, format, compile, diff safety, Bicep, exact Checkov baseline `36/7`, CI Checkov `34/0`, Dockerfile Checkov, container smoke, worker exit `2`, and changed-file secret/PII scan passed. Validation image: `sha256:1c3f35e4d36d78660b746fca802aec1da9cf04289fccff9c7567322125e4b421`.

## Livingston final-SHA rejection

Livingston independently reviewed exact comparison `21a3fa0da9f3a6752d96e1f6db17386e8dabaf6e..e16963243973707ea2557f75f925d3c6935d49ee`. Local, remote, and PR head matched with divergence `0/0`; the PR was open/draft/CLEAN with 13 successful checks, no reviews, and no review threads.

RV-008 remains **High**. `_recovery_predecessor_provider_evidence()` requires only a non-empty intent operation, while the durable authorization evidence omits operation, intent ID, and receipt ID. Replacing the implicated YouTube operation with an unrelated operation still created a third mutation-capable attempt:

`RV008_WRONG_OPERATION_BYPASS read_only=False attempts=3 operation=unrelated_read_only_probe`

Required correction: validate the exact expected operation and bind provider, operation, intent, receipt, attempt, fence/time, publication/week/digests, and artifact into the durable authorization/digest. A substituted operation or aliased provider intent/receipt must fail closed before a successor is appended.

Independent validation passed: required matrix `40/35`, focused correction `120`, locked contract `795` with one warning, and full repository `3124 passed, 3 skipped, 2 deselected, 1 warning`. Ruff, format, compile, diff safety, Bicep, exact Checkov baseline `36/7`, CI Checkov `34/0`, Dockerfile Checkov, container smoke, worker exit `2`, and exact-diff secret/PII scan passed. Review image: `sha256:30c8be5535617b86db502c8ab6feb8399ffff2b790a7c528d373b2e96b4ab5a0`.

## Basher correction after Rusty rejection

Basher is the sole author of the current correction. Ralph is reserved for fresh independent review without source/test contribution. Bender, Hermes, Amy, Leela, Fry, Farnsworth, Livingston, Frank, and Rusty did not contribute or advise.

The latest `provider_unknown` attempt now supplies recovery identity only through its immutable provider-evidence snapshot. Each provider must have a consumed, provider-matching intent with one expected item; receipts must belong to that intent, be non-ambiguous, and not conflict with the expected identity. Each provider must then have exactly one failed-terminal post-readback for that same item. Missing identity, provider-kind mismatch, stale receipt, conflicting item evidence, duplicate readback, or a different readback item fails closed.

Rusty's exact bypass using intents for `youtube-unknown`/`spotify-unknown` and failed readbacks for `youtube-DIFFERENT-ITEM`/`spotify-DIFFERENT-ITEM` can no longer create authorization or a third mutation-capable attempt. However, Ralph proved the exact-match path still accepts zero durable provider receipts and creates a mutation-capable successor.

## Ralph final-SHA rejection

Ralph independently reviewed `97c9520b7c365b078a50df113154bb1e66b1ecbb..fcfa40015ed68d9e38d8432425b7cbd15171e835`, focused on source commit `eaaac5706985d0df4058f46d25e4aa4d9217f41e`. No executable changes followed that source commit.

RV-008 remains **High**. `_recovery_predecessor_provider_evidence()` accepts `receipts=[]`; exact-item failed-terminal readback then authorizes a third attempt:

`RV008_MISSING_RECEIPT_BYPASS receipts={'youtube': 0, 'spotify': 0} attempts=3 read_only=False`

Required correction: require exactly one usable, non-ambiguous receipt bound to each consumed intent, provider kind, and expected provider item before failed-terminal readback can authorize the specifically bound successor. Missing or duplicate receipts must fail closed.

RV-002, RV-003, RV-004, RV-007, and RV-009 remain resolved. P07 is not accepted.

## Historical Frank correction and Rusty rejection

Frank was the sole author of that correction. Rusty performed its fresh independent review without source/test contribution. Bender, Hermes, Amy, Leela, Fry, Farnsworth, and Livingston did not contribute or advise.

| Finding | State | Review result |
|---|---|---|
| RV-001 | Resolved | Read-only YouTube promotion takeover converges through authoritative readback without a duplicate mutation. |
| RV-002 | Resolved | Expired `reserved`/`enqueue_started` leases become recoverable by one fenced owner; stale owners cannot release or complete the replacement. |
| RV-003 | Resolved | Emitted weekly rows, deployed rules, and active-depth absence semantics remain aligned. |
| RV-004 | Resolved | A bounded resumable migration backfills legacy references, and cleanup refuses deletion until reference completeness is proven. |
| RV-005 | Resolved | Missing provider identity remains accurately fail-closed rather than inventing identity-bound readback. |
| RV-006 | Planning-resolved | The closure inventory is present; P05 execution remains outstanding, and no issue or review thread is treated as resolved by this delivery. |
| RV-007 | Resolved | Current tracking and PR narrative preserve Fry's rejection and identify Farnsworth/Livingston as the new author/reviewer pair. |
| RV-008 | **High, open** | Complete-history/latest-attempt checks pass, but failed-terminal readback of the latest unknown attempt is not bound to that attempt's exact provider item. Readback for a different item can authorize a mutation-capable continuation. |
| RV-009 | Resolved | Every cycle recomputes proof from raw evidence and rejects label-only, tampered, mismatched, or unauthorized recovered envelopes. |

Rusty's verdict on Frank's revision remains **Not accepted**: 0 Critical, 1 High, 0 Medium, 0 Low at `1efa749`. Livingston's earlier rejection at `601d36a` remains historical evidence.

## Basher validation evidence

- Required RV-008 probes: **19 passed, 49 deselected**.
- Focused correction suite: **113 passed**.
- Locked contract suite: **788 passed, 1 warning**.
- Full repository suite: **3118 passed, 2 skipped, 2 deselected, 1 warning**.
- Python compile, Ruff check, Ruff format check, Bicep build, and diff safety: **passed**.
- Exact Checkov baseline: **36 passed, 7 failed**, matching the documented pre-existing baseline.
- CI-equivalent Checkov gate: **34 passed, 0 failed**.
- Dockerfile Checkov baseline: **passed**.
- Container image `sha256:0e4d101baaca7f4b4ebc2b84f78f103c8451aff4710ea9bb1d239897a34bdfff` passed UID `999`, ffmpeg/ffprobe, and pipeline import smoke; the unconfigured distribution worker exited `2`.
- Changed-file suspected secret/PII scan found no private key, access key, JWT, signed credential URL, or email-address pattern.

Ralph's initial full run reproduced the known stale Compose recorder image; after rebuilding the recorder, the fanout probe and full suite passed. Independent counts were focused `113`, locked `788`, and full `3118`; exact Checkov remained `36/7`, CI Checkov passed `34/0`, and review image `sha256:5baa9f5828ab055362ba8e5b25d3fae388ab5cb85c5029c335cce4d79f2ccf10` passed smoke. No validation or security gate was weakened.

## Historical independent validation evidence

- Required RV-008 probes: **17 passed, 45 deselected**.
- Focused correction suite: **107 passed**.
- Locked contract suite: **782 passed, 1 warning**.
- Full repository suite: **3112 passed, 2 skipped, 2 deselected, 1 warning**.
- Python compile, Ruff check, Ruff format check, Bicep build, and diff safety: **passed**.
- Exact Checkov baseline: **36 passed, 7 failed**, matching the documented pre-existing baseline.
- CI-equivalent Checkov gate: **34 passed, 0 failed**.

Rusty's initial full suite reproduced only a stale Compose recorder image; after rebuilding, the focused fanout test passed and the final full suite returned **3112 passed, 2 skipped, 2 deselected, 1 warning**. Review image `sha256:72257821fdc2c45de68d98857a35d8d2f72fced688c829d75dccfe651d38d37b` passed the standard non-root/dependency/import smoke, and the unconfigured distribution worker exited `2`. No validation or security gate was weakened.

## Negative probes

The focused suite proves expired reservation recovery with one fenced winner, fail-closed cleanup until legacy reference migration completes, rejection of opaque or mismatched selected-predecessor evidence, rejection of label-only/tampered/mismatched proof envelopes, and acceptance of exact complete proof only.

Frank's probes prove that a later `provider_unknown` blocks reuse of the older failed predecessor and that stale, omitted, and reordered history fail closed. Basher's probes close Rusty's remaining gap: different-item readback, missing item identity, provider-kind mismatch, stale receipt, conflicting item candidate, and duplicate post-terminal readback all fail closed; exact matching readback remains the only path to a specifically authorized successor.

## Residual external gates

- **P00-T01 — complete:** `jmservera/SquadScope#773` reviewed head `d75e3f5523f4810edbcaeef9a217d34cd21825a2` merged as `7a6d8811bf82507cbdd0b01ba1135bc42e5942f3`; all 18 PR checks and merge-SHA CI/security/release/deploy workflows succeeded.
- **P07-T07 — complete:** Basher independently accepted Leela's corrected exact reviewed head `905a890c6a2176c799a5cd5f54fbb01d4c791aa9`.
- **P05 — delivery and exact W39 acceptance:** complete Podcaster/#682 disposition, required reviews and merges, exact upstream/Podcaster merge-SHA artifact deployment, all rollout gates, P05-T06 reconcile-first real-W39 execution, authoritative external provider readback, safe evidence reporting, alert fire/clear evidence, and rollback evidence.
- **P06 — four elapsed cycles:** record four consecutive future post-fix weekly cycles with complete upstream, Azure, immutable-attempt, weekly-aggregation, provider-identity, and authoritative external-readback evidence.

No Podcaster merge, deployment, W39 dispatch, provider mutation, canary, four-cycle completion, or production acceptance is claimed.

## Related work

- jmservera/SquadScope-Coordinator#17
- jmservera/SquadScope#770
- jmservera/SquadScope#771
- jmservera/SquadScope#772
- jmservera/SquadScope-Podcaster#671
- jmservera/SquadScope-Podcaster#678
- jmservera/SquadScope-Podcaster#679
- jmservera/SquadScope-Podcaster#681
- jmservera/SquadScope-Podcaster#682

`jmservera/SquadScope-Podcaster#682` remains open and is not superseded. This delivery does not mutate issues, review threads, deployments, canaries, or production state.

## Security and delivery controls

The current change set and public PR text were checked for suspected secrets and PII; none were identified. Durable state is intended to exclude credentials, tokens, request/response bodies, URLs, and PII. Ambiguous provider mutation remains fail-closed and must never authorize a blind retry.

## Delivery checklist

- [x] Current status, branch diff, independent review, and existing draft PR inspected.
- [x] Public PR content checked under the content-policy citation rules; no citation was required.
- [x] Review execution and Amy's declared in-repo implementation execution recorded as Complete.
- [x] Fry's historical outcome retained as Not accepted with 0 Critical and 4 High findings open at `02241a1`.
- [x] W38 retained as evidence-conditional and W39 retained as `missed_not_dispatched`.
- [x] Exact real-W39 production acceptance added as dependency-ordered P05-T06 with reconcile-before-mutate and fail-closed duplicate prevention.
- [x] P06 remains four future qualifying cycles; W39 receives no automatic cycle credit.
- [x] Positive validation and independent negative probes recorded exactly.
- [x] Existing PR retained OPEN, DRAFT, and BLOCKED.
- [x] RV-002, RV-004, and RV-009 independently resolved.
- [x] RV-003 and RV-007 preserved as resolved.
- [x] Livingston's rejection of source candidate `601d36a` preserved.
- [x] RV-008 branch-around-older-unknown recovery defect corrected and validated by Frank.
- [x] Rusty independently reviewed exact head `1efa749`; verdict Not accepted.
- [x] RV-008 different-item latest-unknown readback bypass corrected and validated by Basher.
- [x] Ralph independently reviewed final head `fcfa40015ed68d9e38d8432425b7cbd15171e835`; verdict Not accepted.
- [x] RV-008 missing/duplicate/wrong-bound receipt authorization corrected and fully validated by Ralph.
- [x] Livingston independently reviewed final source SHA `e16963243973707ea2557f75f925d3c6935d49ee`; verdict Not accepted.
- [x] RV-008 exact operation and durable intent/receipt authorization binding corrected.
- [x] RV-008 exact recursive typed canonical set/envelope/evidence/history/successor correction completed and validated by Leela.
- [x] Basher independently accepted corrected exact reviewed head `905a890c6a2176c799a5cd5f54fbb01d4c791aa9`.
- [x] P00-T01 completed in `jmservera/SquadScope` by reviewed head `d75e3f5523f4810edbcaeef9a217d34cd21825a2`, merge SHA `7a6d8811bf82507cbdd0b01ba1135bc42e5942f3`, and 18 successful checks.
- [ ] P05-T03 accepted by Rusty and P05-T04–P05-T05 merge/provenance/deployment/readiness gates completed.
- [ ] P05-T06 exact real-W39 production run reconciled, executed, and externally verified.
- [ ] P06 four future elapsed cycles proven green with authoritative external evidence.

## Artifacts

- Research: `.copilot-tracking/research/2026-09-21/production-provider-terminal-truth-research.md`
- Plan: `.copilot-tracking/plans/2026-09-21/production-provider-terminal-truth-plan.md`
- Phase details: `.copilot-tracking/details/2026-09-21/production-provider-terminal-truth-phase-details.md`
- Plan critique: `.copilot-tracking/critiques/2026-09-21/production-provider-terminal-truth-plan-critique.md`
- Changes record: `.copilot-tracking/changes/2026-09-21/production-provider-terminal-truth-changes.md`
- Independent review: `.copilot-tracking/reviews/logs/2026-09-21/production-provider-terminal-truth-review.md`
