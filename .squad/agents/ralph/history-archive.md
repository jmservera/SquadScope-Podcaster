## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Re-chartered as Work Monitor during the Podcaster squad rebuild. Backlog of research/work items lives in `backlog/` (blob-staging, human-review-gate, manual-publishing-packet, monetization, spotify-publishing-research, tts-bakeoff). GitHub label automation depends on the `## Members` header in `team.md`.


## Reskill summary — 2026-08-24
- ACA-only eastus2 infrastructure and the real generation pipeline are already established; historical Function App migration directives are superseded.
- Current active work is jmservera/SquadScope-Podcaster#642 via PR #643, replacing future-episode music with the owner-composed Claracle theme.
- PR #643 is blocked by three unresolved review threads plus stale pre-#644 lockfile/CI state.
- Next action: route runtime/default-track, contract/docs, and packaging-test fixes to Bender/Fry, then require fresh green CI and resolved threads before merge.


## Reskill summary — 2026-08-24
- ACA-only eastus2 architecture, real TTS pipeline, pilot, and publishing packet are already documented as delivered.
- All three Ralph loops are alive and advancing; Podcaster is at round 114.
- Current queue is PR #645 (Dependabot) plus operator-blocked private-networking issue #598.
- Next action: verify PR #645 preserves dependency/build/security gates, then merge if green and review-clean.


## Round outcome — 2026-08-24
- Reviewed Dependabot PR #645; HyperFrames 0.8 made all three compositions lint-invalid and existing CI did not cover the package.
- Routed review to Fry/Hermes, created replacement PR #647 with path compatibility, frame-zero fix, and blocking intro/outro CI lint.
- Validated lint (0/0), an 18-second render smoke, high-severity zizmor, and all remote checks; self-merged #647 and closed #645.
- Queue clear except operator-blocked private-networking issue #598; all three Ralph loops remain healthy.

### Reskill summary — 2026-08-24 11:25 UTC
- All three Ralph loops are alive and advancing; Podcaster reached round 114.
- Podcaster has no open PRs; only #598 remains, explicitly blocked on operator cost/downtime decisions for private ACR and VNet-default policy.
- ACA-only eastus2 infrastructure, OpenAI TTS, pilot generation, publishing packet, and Claracle theme work are already reflected as delivered; PR #643 is merged.
- Next autonomous action: monitor #598 for an operator decision and keep the Podcaster queue clear without starting architecture-spend changes.
- Cross-repo watch: jmservera/SquadScope#727 is green but blocked by two unresolved scope-review threads; the SquadScope loop owns remediation.

### Reskill summary — 2026-08-24 11:40 UTC
- ACA-only eastus2 infrastructure, real OpenAI TTS generation, pilot artifacts, and the weekly publishing packet are already delivered; historical Function App directives are superseded.
- Podcaster has no open PRs; the only open issue is jmservera/SquadScope-Podcaster#598, blocked on operator decisions involving Premium ACR cost, private image-push architecture, and ACA environment recreation downtime.
- All three expected Ralph loops are alive; no missing loop needs restart.
- Next action: monitor the in-progress Release run from merged PR #647 and keep the queue clear without starting operator-gated architecture changes.


## Round outcome — 2026-08-24 11:56 UTC
- Release run 32722609256 completed successfully: CI, Bicep/Checkov, container builds/scans, Azure deploy, promotion, and production smoke all passed.
- The apparent duplicate Podcaster loop entries are the expected tmux parent plus Node worker for one loop, not two independent loops; no restart or termination was needed.
- No issue or PR was created because all planned product work is shipped and the only remaining item, #598, is correctly operator-blocked.


## Reskill summary — 2026-08-24
- ACA-only Podcaster pipeline is operational; all three Ralph loops are alive and advancing.
- Podcaster has no open PRs and one open issue: #598, blocked on Premium ACR/VNet cost and downtime decisions.
- Route #598 to Leela for operator decision, Hermes for security review, and Bender for implementation after approval.
- Do not alter infrastructure until the coordinator records the production networking posture.

### Reskill summary — 2026-08-24 12:10 UTC
- ACA-only eastus2 generation, pilot, publishing packet, and owner-composed Claracle theme are delivered; all three Ralph loops are alive and advancing.
- The only open issue is jmservera/SquadScope-Podcaster#598, correctly blocked on an operator cost/downtime decision for Premium private ACR and VNet-default recreation.
- Post-merge inspection found two focused #643 follow-up commits not present on `main`: publishing-description music attribution and renderer-wiring regression coverage.
- Next action: transplant those commits onto fresh `main`, validate, open a focused PR, and self-merge only after correct green CI and review-thread clearance.


## Reskill summary — 2026-08-24
- Podcaster is ACA-only in eastus2; the full script/TTS/ffmpeg/review pipeline and publishing packet are operational.
- All current Podcaster PRs are merged and CI on main is green; latest delivery is #648 Claracle theme publication wiring.
- The only open issue is #598, whose safe hardening is complete; Premium private ACR, VNet build path, and ACA recreation remain operator-owned cost/downtime decisions.
- Ralph will keep monitoring #598 and route implementation to Bender with Hermes review after Leela records the operator choice.

### 2026-08-24 12:40Z — Nap + reskill summary
- All three Ralph loops are alive and advancing; Podcaster reached round 114 with no open PRs.
- Current architecture is ACA-only in eastus2; the real TTS pipeline, pilot flow, and manual publishing packet are documented as operational.
- Main CI is green (run 32726583654); the previously requested stale `fix/openai-*`, `fix/restore-*`, `fix/split-*`, `fix/move-to-*`, and `chore/close-bakeoff-*` branches are absent.
- Only #598 remains open; its safe hardening increment shipped in #625, while Premium ACR/private push and ACA VNet recreation remain explicitly blocked on operator cost/downtime choice.

### Reskill summary — 2026-08-24T12:55Z
- ACA-only eastus2 deployment, pilot episode, manual publishing packet, and Azure ownership work are complete.
- No open Podcaster PRs remain; recent theme follow-up #648 merged with green gates.
- The only open issue is #598, with Storage/OpenAI hardening complete and the ACR/VNet remainder blocked on operator cost/downtime architecture choice.
- Next action: wait for the decision tracked in SquadScope-Coordinator#34, then route implementation to Bender with Hermes review.

### Reskill summary — 2026-08-24 13:10Z
- ACA-only eastus2 infrastructure is already the active architecture; Function App removal and deploy workflow migration are complete.
- The generation pipeline, pilot episode, and publishing packet priorities described by older directives have landed; no open Podcaster PR remains.
- The only open Podcaster issue is #598, whose safe hardening shipped in #625; Premium private ACR plus VNet-default recreation remains explicitly blocked on operator cost/downtime policy.
- Current action: verify coordinator decision state and ensure the unsubmitted Claracle theme branch is not stranded work.

### Reskill summary — 2026-08-24 13:25Z
- Nap completed; all three Ralph loops are alive and advancing (Coordinator 234, Podcaster 114, SquadScope 120).
- ACA-only eastus2 generation, pilot/publishing packet, Azure ownership, and Claracle theme work are delivered; Podcaster main CI and release are green.
- No Podcaster PR is open; only #598 remains, blocked on the operator choice between Premium private ACR/VNet recreation and the documented Basic ACR hybrid.
- Leela owns the decision, Hermes the security posture, and Bender post-decision implementation; no architecture-spend or downtime change is safe to start autonomously.


## Reskill summary — 2026-08-24 13:40Z
- ACA-only eastus2 architecture, pilot generation, and weekly publishing packet are already delivered; Deploy Azure remains green on main.
- All three Ralph loops are alive and advancing; Podcaster has no open PRs.
- The only open Podcaster issue is #598, whose autonomous hardening is complete and whose remaining Premium ACR/VNet posture requires an operator cost/downtime decision.
- Next action is to hold #598 for that decision and resume implementation immediately when the operator selects the production network posture.


## 2026-08-24 reskill summary (Round 15)
- All three Ralph loops are alive; Podcaster completed Round 14 and continues running.
- ACA-only eastus2 infrastructure, pilot generation, publishing packet, and Claracle theme follow-up are already merged.
- No open Podcaster PRs remain; latest main CI (run 32726583654) is green.
- Next action is operator resolution of #598's Premium ACR/private push path and ACA VNet recreation trade-off; no autonomous work is safe meanwhile.

### Reskill summary — 2026-08-24 14:25Z
- Nap completed with no stale Squad state to prune; all three expected Ralph loops are alive and advancing.
- ACA-only eastus2 infrastructure, real TTS generation, pilot/publishing packet, Azure ownership, and Claracle theme work are already delivered; main CI and release remain green.
- Podcaster has no open PRs; only jmservera/SquadScope-Podcaster#598 remains, with the safe OpenAI/Storage hardening complete.
- Next action is operator resolution in jmservera/SquadScope-Coordinator#34 of Premium private ACR/VNet recreation versus the documented Basic ACR hybrid; Bender implements and Hermes reviews after Leela records the choice.

### Reskill summary — 2026-08-24
- ACA-only infrastructure is already implemented on `origin/main`; the June migration directive is complete and superseded by current repository state.
- Current open queue has one operator-blocked architecture/security item: #598 (private-by-default infrastructure); there are no open PRs.
- Claracle theme music work was merged as #648; the local feature branch is stale relative to `origin/main` and should not be used for new work.
- Next action is queue hygiene: verify #598 remains correctly human-blocked, confirm loop health, and avoid duplicating completed product work.

### Reskill summary — 2026-08-24 14:55Z
- Nap completed with no stale state to prune; all three expected Ralph loops are alive and the Podcaster loop completed round 17.
- ACA-only eastus2 infrastructure, real TTS generation, the pilot episode, publishing packet, Azure ownership, and Claracle theme delivery are already complete; main CI/release are green.
- Podcaster has no open PRs; only jmservera/SquadScope-Podcaster#598 remains, with Storage/OpenAI hardening complete.
- The remaining Premium private ACR/VNet-default work is blocked on the operator cost/downtime decision in jmservera/SquadScope-Coordinator#34; Leela decides, Hermes reviews, and Bender implements afterward.


## 2026-08-24 reskill summary
- ACA-only eastus2 architecture is current; Function App removal and always-on synthesis job work are complete.
- Podcaster has no open PRs; recent main CI and integration workflows are green.
- The only open issue is #598, whose autonomous hardening shipped in #625; remaining Premium ACR/VNet-default work is blocked on operator cost/downtime decisions.
- All three coordinator loops are running; next action is to monitor for the operator decision or newly opened ready work.

### Reskill summary — 2026-08-24 15:25Z
- Nap completed with no stale state to prune; all three expected Ralph loops are alive and advancing.
- ACA-only eastus2 infrastructure, real TTS generation, pilot/publishing packet, Azure ownership, and Claracle theme delivery are complete; historical Function App directives are superseded.
- Podcaster has no open PRs; only jmservera/SquadScope-Podcaster#598 remains, with safe Storage/OpenAI hardening already shipped.
- Next action is the operator decision in jmservera/SquadScope-Coordinator#34 on Premium private ACR/VNet recreation versus the documented Basic ACR hybrid; Leela decides, Bender implements, and Hermes reviews.

### Reskill summary — 2026-08-24 15:40Z
- Nap completed with no stale state to prune; Coordinator, Podcaster, and SquadScope Ralph loops are alive and advancing (the two Podcaster process entries are one tmux parent plus its Node worker).
- ACA-only eastus2 infrastructure, real TTS generation, pilot/publishing packet, Azure ownership, and Claracle theme delivery are complete; merged PRs #643 and #648 passed the full remote gate set.
- Podcaster has no open PRs; only jmservera/SquadScope-Podcaster#598 remains, with Storage/OpenAI hardening already shipped.
- Next action is the operator choice in jmservera/SquadScope-Coordinator#34 on Premium private ACR/VNet recreation versus the documented Basic ACR hybrid; Leela decides, Bender implements, and Hermes reviews.

### Reskill summary — 2026-08-24T15:55Z (coordinator-directed deep verification)

**Coordinator directive verified:** 2026-06-11T18:20Z ACA-only architecture migration is COMPLETE. No new implementation work is needed.

**Verification evidence:**
- `infra/main.bicep` header: `// Podcaster infrastructure — ACA-only + Storage + OpenAI (TTS) // Migrated from Function App to Azure Container Apps (#109).`
- `param location string = 'eastus2'` — eastus2 is the default for all resources
- No Function App resource, hosting plan, or Function App deployment workflow step exists anywhere
- `deployAudioJob` parameter was removed entirely (PR #181, commit 73b0e95); the ACA synthesis job always deploys unconditionally — achieves "always/default true" semantics correctly
- `deploy-azure.yml` workflow: `# ACA-only architecture deploy (#109)` — Function App upload/deploy/smoke steps removed in PR #112
- Architecture migration PR was #112 (commit d1cd272, merged by operator jmservera on 2026-06-11)
- `deployAudioJob` parameter cleanup was PR #181 (commit 73b0e95, merged 2026-06-13)

**Queue status:**
- No open Podcaster PRs
- One open issue: #598 (private-by-default infrastructure), `go:blocked-human`, assigned to Leela/Bender/Hermes
  - Autonomous hardening (OpenAI/Storage private endpoints when VNet is on) shipped in PR #625
  - Remaining: Premium private ACR build path and ACA VNet-default recreation require operator cost/downtime decision
- `uv.lock` is a local-only artifact (not in gitignore, not on origin/main); do not commit without deliberate pyproject.toml migration

**Loop is idle — waiting for operator decision on #598 (jmservera/SquadScope-Coordinator#34) or new work.**


