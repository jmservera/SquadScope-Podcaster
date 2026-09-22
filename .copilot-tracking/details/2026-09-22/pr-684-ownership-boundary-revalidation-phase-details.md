<!-- markdownlint-disable-file -->
# RPI Phase Details: PR #684 Ownership Boundary Revalidation

## Metadata

* Related plan: `.copilot-tracking/plans/2026-09-22/pr-684-ownership-boundary-revalidation-plan.md`
* Declared scope: full plan, P01 through P04
* Execution owner: Bender, explicitly assigned by jmservera for this isolated worktree run

## P01 — Inspect ownership and mutation boundaries

### P01-T01

Trace the fixed queue visibility deadline, editor lease identity, persisted owner/run identity,
fence, and expiry through `run_video_generation()`. Inventory every call that can make media or
distribution state externally durable: immutable artifact commit, authoritative outbox create,
queue notification/sent marking, and direct provider distribution. Completion evidence is a
call-site inventory and an identified repository-standard authoritative revalidation pattern.

## P02 — Implement fail-closed boundary guards

### P02-T01

Use persisted readback to compare the exact original owner/run identity and fence immediately
before each irreversible call. Evaluate expiry conservatively against the already-fixed lifecycle
deadline and persisted lease expiry. Do not acquire, renew, or calculate a new lifetime. Raise a
non-green execution error before mutation on unavailable, malformed, mismatched, transferred, or
expired authority.

## P03 — Prove post-compose takeover safety

### P03-T01

Inject takeover only after the existing post-compose validation has succeeded. Exercise fan-out
and inline/direct-distribution paths as applicable. Assert zero calls to immutable archive commit,
repository outbox enqueue, queue notification/sent marking, `distribute_video()`, and provider
clients. Keep existing takeover assertions intact.

## P04 — Validate and deliver

### P04-T01

Run the smallest focused regression selectors first, then all directly relevant job-runner tests.
Run Ruff check and Ruff format check for every touched Python file, plus diff hygiene.

### P04-T02

Review every irreversible call site in the final diff for an adjacent authoritative guard.
Reconcile plan markers and changes evidence, commit with the exact requested trailers, push to
`origin/squad/incident-provider-terminal-truth`, and comment on PR #684 with exact SHA and
validation. Preserve draft/operator-only status and perform no merge, deployment, workflow
dispatch, provider mutation, production action, or PR state change.

## Current Blockers

None.

## Implementation Evidence

* P01-T01: the irreversible call-site inventory is immutable artifact commit, authoritative
  outbox enqueue, queue notification, notification-sent marking, direct `distribute_video()`,
  and terminal manifest persistence.
* P02-T01: `VideoOwnershipGuard` persists owner, execution identity, claim identity, monotonic
  fence, fixed queue visibility expiry, and fixed editor-lease expiry. Every boundary re-reads
  that record and also checks the original monotonic queue deadline.
* P03-T01: the new parameterized regression replaces the persisted claim after the post-compose
  check for both outbox and direct branches and proves archive, outbox, notifications,
  distribution, and provider mocks remain untouched.
* P04-T01: focused ownership tests passed `5`; the complete job-runner module passed `122`;
  Ruff check, Ruff format check, and diff hygiene passed.
