# PR676 storage preservation phase details

## P01 — Fix and merge PR #676
- P01-T01: Verify the live PR state from GitHub, including unresolved review threads, ruleset behavior, required checks, and actual reusable deploy callers.
- P01-T02: Land the smallest safe fix in the isolated worktree using a different specialist than the previously rejected artifact, keeping Storage public-network preservation compatible with explicit fresh-install opt-in. Validate with focused deploy workflow tests.
- P01-T03: Push, re-check the live thread/check state, resolve only genuinely addressed threads, and attempt a normal squash merge to discover the real remaining gate if any.

## P02 — Prove and apply the preservation deploy on merged main
- P02-T01: Use a sanitized ARM what-if from `main` with the production-safe inputs (`deploy_vnet=false`, `storage_public_network_access=Disabled`) and capture whether Storage, ACR SKU, or network resources would change.
- P02-T02: If the what-if proves no Storage/network flip, run the normal deployment path and read back the deployed Storage `publicNetworkAccess`, ACR SKU, and private-endpoint inventory.
- P02-T03: Close issue #675 only after the verified post-deploy evidence is captured.
