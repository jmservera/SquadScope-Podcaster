# Scoped W44/W45 code-only release

This manual workflow is limited to the reviewed application merge
`8e65a5355458c04a5fa659138f65b38c682ad1b2`. Build both images from that exact
source, label their full OCI revision, scan HIGH/CRITICAL OS and library
vulnerabilities with the existing fail-on-findings policy before pushing, import
by immutable digest, and independently verify registry manifest/config provenance
before dispatch. The workflow commit is separately reviewed release tooling,
not the application image source. Never pass a tag or an unverified digest.

Exact-source build `cg1v` scanned both images before either push. Immutable
production imports preserve these complete manifest digests:

* API: `sha256:56819d553629b39d7c4079fee6bcbb5338543a0f84f8c6c72a14796c52abb395`
* All five workers: `sha256:678878ab19778c85e72d977fe4f6ac9f9932b87dcd4ff71c01b8411839193b95`

Independent target-registry raw manifest/config-byte and full revision proof
is mandatory before dispatch; an import acknowledgement is not that proof.
The helper's original rollback references are the currently deployed
`53cb8105…` API and `297877df…` worker digests, not the earlier release baseline.

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
health before admitting the first W45 private recovery epoch. Both authorized
W44 epochs are terminal and preserved; no additional W44 epoch is authorized.
The owner's W45-first sequencing does not require a selected W44 MP4;
fresh complete trusted execution/replica/native-redelivery termination evidence
and unchanged original media proof are mandatory at successor admission.

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
publication. After deployment, refresh the original W45 proof, worker exit
receipts, shared capacity, scratch lease and segment load before its already
authorized first scoped epoch. All provider uploads, drafts, unlisted operations
and public promotion remain held.
