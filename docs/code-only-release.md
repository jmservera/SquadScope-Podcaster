# Scoped W44/W45 code-only release

This manual workflow is limited to the reviewed application merge
`a30fbc599700d71b37532916e5813e3c2ddf27b6`. Build both images from that exact
source, label their full OCI revision, scan HIGH/CRITICAL OS and library
vulnerabilities with the existing fail-on-findings policy before pushing, import
by immutable digest, and independently verify registry manifest/config provenance
before dispatch. The workflow commit is separately reviewed release tooling,
not the application image source. Never pass a tag or an unverified digest.

Exact-source build `cg1x` scanned both images before either push. Immutable
production imports preserve these complete manifest digests:

* API: `sha256:76eb517d02f8b1c3a5881d416dc9074aa84deee6b5656b707a4c033aa2d7173d`
* All five workers: `sha256:0efeb80bdeb23ee76c5411c4c9ae5f75338cbc8b1237df32cfec501247065727`

Independent target-registry raw manifest/config-byte and full revision proof
is mandatory before dispatch; an import acknowledgement is not that proof.
The helper's original rollback references are the currently deployed
`56819d55…` API and `678878ab…` worker digests from reviewed source
`8e65a5355458c04a5fa659138f65b38c682ad1b2`, not the earlier release baseline.

The owner accepts an assessed exclusive deployment window rather than requiring
unavailable ACA conditional tokens. This is supported Microsoft.App JSON Merge
Patch of the **complete freshly retrieved containers array**, changing only its
one image. It is not CAS or atomic image-field mutation. The existing
`prod-deploy` mutex serializes ordinary release workflows, not portal or manual
writers. The operator must inspect active/queued release workflows, current
agents/manual actors and recent control-plane writes, communicate sole mutation
ownership, and obtain independent approval of this concrete procedure before
dispatch. An empty execution list alone does not establish exclusivity.

The helper keeps full snapshots and tokens only in memory. It compares all
protected resource properties, identity, tags and location before and after
every mutation, including images already updated. It ignores only explicitly
server-owned revision/provisioning metadata and semantically unset empty/absent
revision suffix; nonempty suffixes remain protected. Environment, secretRef,
registry, triggers, scale, networking and all flags are compared exactly.
Thirty-second stability plus fresh comparisons detect drift; they cannot remove
the read/write race. Stop on drift rather than overwrite another writer.

Workers are updated before exposing the new API admission route. Active event
workers defer the release; the scheduled reconciler may run under existing
provider holds. Preserve draft/false Spotify video, disabled provider mutation
and outbox. No queues/triggers/scale, UI, model deployments, VNet, secrets or
identities are changed. Operations are polled to terminal and the API must have
one healthy 100%-traffic revision. Independent actual postrelease readback must
verify all six digests, protected settings, UI/model/network invariants and
health before any recovery admission. W45's selected private output is preserved,
not replayed. Both prior W44 epochs remain terminal and unchanged. Reviewed PR788
supports at most one final exact-original W44 epoch under the recorded bounded
coordinator disposition, never caller approval strings: exactly three epochs
maximum, exact ordered predecessor history and unchanged original proof/budget,
fresh fixed-path trusted evidence of all predecessor/native/recorder exits bound
to each corresponding persisted epoch start, and no active editor/selection or
provider ambiguity. New fixed 5100s hard/3300s render/1500s fallback limits are
assigned once; no old or new deadline may be extended or reset. No fourth epoch.

Successful empty PATCH responses are accepted without JSON decoding errors,
but 202 alone never certifies deployment. The helper polls `Azure-AsyncOperation`
to `Succeeded` (rejecting `Failed`/`Canceled`), or the ARM `Location` completion
endpoint when the first header is absent. It follows the ARM asynchronous
operation contract, honors bounded numeric Retry-After delays, and retries only
transient polling reads within a fixed ten-minute deadline; it never replays
a PATCH. Poll URLs must be HTTPS on the exact ARM management host and current
subscription; HTTP redirects are refused and URLs/bodies are not logged.
When neither polling header exists, fresh resource reads must reach both
`provisioningState=Succeeded` **and the expected immutable image** within the
bounded readiness wait. An old Succeeded image cannot satisfy this fallback.
Terminal polling, resource readiness and API health each use a fixed ten-minute
monotonic deadline checked again after every response, before certifying success.
A response arriving at or after its deadline cannot certify completion; reads
and retries never refresh the deadline.
All paths then require fresh six-resource protected comparison and API health.
See [ARM asynchronous operations](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/async-operations).

Original rollback references are pinned in the helper and emitted in the
sanitized preflight. A partial failure leaves already updated images recorded
in per-resource milestones; **there is no automatic rollback** or restart from
a stale baseline. Freshly inspect and independently approve any reconciliation,
using the same exclusive window and current protected containers. Never replay
old arrays, change holds, or run the ordinary full-stack defaults.

The release neither admits recovery requests nor approves media/provider
publication. After deployment, refresh exact-original W44 proof, both terminal
predecessors and complete actual worker/recorder exit receipts, current ledger,
shared capacity, queue/editor leases and publication holds. Only then may the
parent write fresh trusted fixed-path evidence and admit the single final private
epoch. This release phase performs no admission. All provider uploads, drafts,
unlisted operations and public promotion remain held.
