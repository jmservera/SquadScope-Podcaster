# Scoped W44/W45 code-only release

This manual workflow is limited to the reviewed application merge
`5a526f7165c3e18b825a1d5a98e6c5864882a933`. Build both images from that exact
source, label their full OCI revision, scan HIGH/CRITICAL OS and library
vulnerabilities with the existing fail-on-findings policy before pushing, import
by immutable digest, and independently verify registry manifest/config provenance
before dispatch. The workflow commit is separately reviewed release tooling,
not the application image source. Never pass a tag or an unverified digest.

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
health before admitting the two original private recovery epochs.

Original rollback references are pinned in the helper and emitted in the
sanitized preflight. A partial failure leaves already updated images recorded
in per-resource milestones; **there is no automatic rollback** or restart from
a stale baseline. Freshly inspect and independently approve any reconciliation,
using the same exclusive window and current protected containers. Never replay
old arrays, change holds, or run the ordinary full-stack defaults.

The release neither admits recovery requests nor approves media/provider
publication. W44 private selected-result byte/digest/identity/playability
verification must precede W45. All provider uploads, drafts, unlisted operations
and public promotion remain held.
