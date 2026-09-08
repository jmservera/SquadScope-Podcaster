# PR676 storage preservation implementation plan

## User Decisions and Requirements
- Confirmed scope is limited to PR #676 / issue #675 storage network preservation.
- Preserve current production Storage `publicNetworkAccess=Disabled` on normal `deploy_vnet=false` deploys.
- Keep explicit opt-in support for fresh public/non-private installs through validated workflow inputs.
- Do not add new VNet/private endpoint/ACR paid changes, do not change OpenAI public posture, keep Spotify allow-live true and video false/draft posture untouched.
- Use the isolated worktree `.worktrees/fix-storage-network-675`.

## Executive Summary
Close the remaining PR #676 deploy-time guard gap, verify all reusable deploy callers preserve the selected storage posture, merge normally if GitHub gates allow it, then prove the merged template keeps production Storage private with a sanitized what-if and a no-cost preservation deployment/readback.

## Scope
- `.github/workflows/deploy-azure.yml`
- `.github/workflows/reusable-deploy-azure.yml`
- `.github/workflows/release.yml`
- `tests/test_deploy_workflow.py`
- directly related deployment docs
- GitHub PR/merge/deploy evidence for #676 / #675

## Non-Goals
- No backlog re-audit or unrelated repo fixes
- No new network resources, private endpoints, or SKU upgrades
- No Spotify canary/upload/re-render work

## Acceptance Criteria
- `deploy_vnet` is normalized and validated before the contradiction guard compares it to `true`.
- `storage_public_network_access` remains exact-case validated as `Enabled|Disabled`.
- All real reusable deploy callers expose/forward the new parameter so explicit fresh public installs remain supported.
- Focused regression tests cover normalization/validation and caller wiring.
- GitHub merge attempt records the real gate outcome instead of assuming it.
- Post-merge what-if and deployment evidence show Storage remains `Disabled`, ACR remains `Basic`, and no extra network resources are introduced.

## Current Checklist
- [x] <!-- rpi:task id=P01-T01 --> Inspect PR #676 diff, review thread, issue #675, and active workflow callers.
- [x] <!-- rpi:task id=P01-T02 --> Implement the workflow/input/root-guard fix in the isolated worktree and validate focused tests.
- [ ] <!-- rpi:task id=P01-T03 --> Re-check PR threads/checks after push, resolve addressed thread(s), and determine the actual normal-merge gate with `gh pr merge`.
- [ ] <!-- rpi:task id=P02-T01 --> Run a sanitized post-merge ARM what-if proving Storage stays `Disabled` with the intended no-private-network-expansion posture.
- [ ] <!-- rpi:task id=P02-T02 --> Run the preservation deployment on merged `main` and read back Storage/ACR/network state without secret output.
- [ ] <!-- rpi:task id=P02-T03 --> Close issue #675 only after verified merge/deploy evidence is current.

## Follow-Up Items
- None.
