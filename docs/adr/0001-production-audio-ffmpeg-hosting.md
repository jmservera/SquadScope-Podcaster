# ADR 0001: Production hosting for audio synthesis + ffmpeg

- Status: **Superseded** — ACA-only architecture adopted (PR #112 removed Function App; #109 confirmed ACA as primary compute)
- Date: 2026-06-10
- Owner: Bender (API / Functions / Bicep / CI / deploy)
- Issue: #67
- Related: #60 (production `/api/generate`), #34 (first reviewed episode)

## Context

The podcast pipeline stitches per-voice TTS segments (operator-selected
`fable` + `alloy`) into a single episode MP3 and runs an audio-validation gate.
`podcaster/audio.py` shells out to `ffmpeg`/`ffprobe` (concatenation, two-pass
`loudnorm` to −16 LUFS, and `ffprobe` metadata validation), and
`podcaster/episode.py` orchestrates *parse → voice plan → gated synthesis →
stitch → validate*.

The deployed host is a **Linux Consumption Azure Function App** (Python). That
host does not include `ffmpeg`/`ffprobe`, and on Consumption / Flex Consumption
plans we cannot `apt-get install` native packages or supply a custom container
image. Consequently the audio assembly + validation gate **cannot run in the
deployed Function App today**.

This is currently masked because `/api/generate` returns a **deterministic
placeholder** audio artifact (`podcaster/generation.py` →
`placeholder_audio_validation`, error `audio artifact is a deterministic
placeholder, not publishable audio`) and keeps the output blocked from
publication. The real synthesis path (`episode.py`) only runs where `ffmpeg`
exists — currently the dev/build host, used to produce the first episode for
operator review (#34).

We must decide how to provide `ffmpeg` + native deps in production so the gated
real-synthesis pipeline can run in the deployed path without weakening the
cost / human-review gates or leaking secrets.

## Constraints and facts

- **Azure Functions Consumption / Flex Consumption**: no custom container
  image, no root/`apt` access. You can only run binaries you package into the
  deployment. A statically-linked `ffmpeg` *may* run but is fragile (codec/
  kernel-feature coupling, large package, manual update path) and unsupported.
  - Consumption hard timeout: 5 min default, up to 10 min via `host.json`
    (longer timeouts require Premium/Dedicated).
  - Flex Consumption: no enforced timeout, but no duration guarantee
    (executions may be cancelled under platform pressure).
  - Full custom-container `ffmpeg` requires **Premium or Dedicated** plans,
    which remove scale-to-zero and raise idle cost.
- **Azure Container Apps (ACA) Jobs**: container image with `ffmpeg` baked in;
  `replicaTimeout` up to 48 h; **scale-to-zero**; system/user-assigned managed
  identity; event-driven triggers (Storage Queue / Service Bus / Event Grid /
  schedule). Pay only while a replica runs.
- Synthesis is inherently **asynchronous and bursty** (one episode per published
  weekly article). A ~5 min episode already approaches the Consumption timeout;
  longer or retried episodes would exceed it.
- Managed identity must reach **Azure OpenAI TTS** (data-plane:
  *Cognitive Services OpenAI User*) and **Blob Storage** (artifact staging). No
  keys; nothing secret logged.

## Options considered

### A. Package a static `ffmpeg` binary inside the Function App
- **Pros**: no new Azure resource; smallest infra change.
- **Cons**: unsupported and brittle (codec coverage, glibc/kernel coupling,
  ~70–100 MB added to the package, manual security patching); still bounded by
  the Consumption 10-min cap and Flex's no-guarantee cancellation; CPU/memory
  for two-pass `loudnorm` is constrained on Consumption. **Rejected** as a
  production path; acceptable only as a stopgap if needed.

### B. Azure Functions custom container (Premium / Dedicated)
- **Pros**: keep the Functions programming model; bake `ffmpeg` into the image.
- **Cons**: Premium/Dedicated lose scale-to-zero → continuous idle cost for a
  workload that runs minutes per week; over-provisioned for the duty cycle.
  **Rejected** on cost/duty-cycle grounds.

### C. Split: HTTP/orchestration on Functions, synthesis+ffmpeg on an ACA Job (RECOMMENDED)
- Keep the existing Function App as the thin, fast **HTTP front door**:
  validate request, create `job_id`, run cost / safety / human-review gates,
  stage manifest, enqueue a synthesis message, and return the **stable 202
  response shape** (`job_id`, `manifest_url`, `errors=[]`) — no `ffmpeg` needed,
  no timeout risk.
- A **Storage Queue–triggered ACA Job** (image with `ffmpeg` baked in) performs
  parse → gated synthesis → stitch → `loudnorm` → `ffprobe` validate → stage
  artifacts + manifest update, using **managed identity** for OpenAI TTS and
  Blob.
- **Pros**: native deps solved cleanly and supportably; scale-to-zero (cost ≈ 0
  when idle); generous `replicaTimeout` removes timeout risk; strong fault
  isolation (a synthesis crash can't take down the API); identity-only access.
- **Cons**: new resource type (ACA environment + job) and a queue contract; a
  container image to build/scan/push in CI; modest added operational surface.

### D. Status quo (local-only generation for review)
- **Pros**: zero spend; already proven for the first episode.
- **Cons**: production `/api/generate` can never produce real audio; doesn't
  satisfy #60. **Not viable** as the end state.

## Decision

Adopt **Option C** — a split architecture: thin Functions HTTP front door plus a
queue-triggered **Azure Container Apps Job** that owns `ffmpeg` and heavy
synthesis. This is the only option that simultaneously provides supported native
deps, scale-to-zero economics matching a weekly duty cycle, headroom beyond
Functions timeouts, and identity-only (secret-free) access to OpenAI TTS and
Blob.

The cost / safety / human-review gates and the publication block are unchanged:
the ACA Job stages a **review-pending** artifact; nothing becomes publishable
until the human-review gate records approval.

> ⚠️ **Operator decision required before provisioning.** Option C introduces new
> Azure resources (ACA managed environment + job, container registry usage,
> queue). Per #67 this must be flagged before any large new Azure spend. Expected
> cost is low (scale-to-zero; minutes of compute per week + registry storage),
> but provisioning is **gated on operator approval**. Until approved, production
> continues to return the placeholder artifact and real episodes are produced on
> the build host for review.

## Consequences

- New CI step to build, scan (Trivy/Checkov as applicable), and push the
  synthesis container image; new Bicep for the ACA environment + job, queue, and
  role assignments (OpenAI User + Storage Blob Data Contributor + Queue Data
  contributor) — all kept in the deploy/infra isolation lane, separate from any
  `squad upgrade` payload.
- `/api/generate` becomes asynchronous-by-design behind the same 202 contract
  (it already returns `job_id`/`manifest_url`); the job updates the manifest
  status. No breaking change to SquadScope callers.
- Shared synthesis code (`podcaster/audio.py`, `podcaster/episode.py`) is reused
  unchanged by the job runner, so existing tests keep covering it.

## Follow-up (created as focused issues, blocked on operator approval)

1. Bicep: ACA managed environment + queue-triggered Job + managed-identity role
   assignments (Bender). ✅ Done (#112).
2. Containerfile + CI build/scan/push for the synthesis image with `ffmpeg`
   baked in (Bender). ✅ Done (#77). Push gated on registry approval (#129).
3. Job runner entrypoint that consumes the queue message and invokes the
   existing `episode.py` pipeline; manifest status updates (Bender). ✅ Done.
4. Wire `/api/generate` to enqueue the synthesis message behind the existing
   gates; async response-shape regression tests (Fry). **Open** — the Function
   App was fully removed in PR #112 (ACA-only migration). A replacement HTTP
   ingress ACA App is needed to restore the `/api/generate` endpoint (#131).
5. Secrets/identity/audit review for the job's data-plane access and queue
   permissions (Hermes). ✅ Done (security review in `docs/security/`).

## Architecture evolution (2026-06-11)

PR #112 migrated to a fully ACA-only architecture, removing the Function App
entirely. The original Option C "split" (Function App front door + ACA Job) was
simplified to ACA-only because the operator decided against maintaining the
Function App for a single-endpoint workload. The HTTP front door still needs to
be restored as an ACA App with HTTP ingress (#131) before the integration
contract with SquadScope is functional end-to-end.
