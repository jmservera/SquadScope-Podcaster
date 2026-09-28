# Project Context

- **Owner:** jmservera
- **Project:** SquadScope Podcaster — Azure-hosted sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts and returns links. Audio/TTS is future work; the initial API returns deterministic stub responses.
- **Stack:** Python 3.11 · Azure Functions (HTTP) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · App Insights / Log Analytics · pytest
- **Created:** 2026-06-07

## Learnings

<!-- Append new learnings below. Each entry is something lasting about the project. -->

- 2026-06-07: Chartered as Producer Lead during the Podcaster squad rebuild (Futurama cast, continued from the SquadScope sister project). Prime directive: never change or block SquadScope article publishing. Milestones live in `docs/PRD.md`.
- 2026-06-07: Triaged all 9 P1 open issues. Routed: #1 (review gate)→Leela, #2 (privacy/RAI)→Hermes, #3 (blob storage)→Bender, #4 (TTS bakeoff)→Farnsworth, #5 (Spotify research)→Amy, #6 (publishing packet)→Amy, #7 (deploy)→Bender, #8 (API contract)→Leela, #9 (Actions chore)→Bender.
- 2026-06-07: Wave 1/2 local readiness review found the diff cohesive in direction but not releasable: pytest currently fails on publishing packet MANIFEST.json missing nested review metadata expected by tests; Azure subscription work remains gated until Bender fixes packet manifest parity and Fry re-runs the suite.
- 2026-06-07: Final Wave 2 readiness gate passed locally: `.venv/bin/python -m pytest -q` reported 19 passed, compileall succeeded, and diff hygiene passed; approved to proceed to Azure subscription setup without deploying or committing from this review.
- 2026-06-07: Wave 3 release gate APPROVED and committed. Full Wave 1/2/3 local increment committed: production pipeline (jobs/generation/storage), infra bicep, docs, tests (19/19), squad files. No scope creep detected; RESPONSE_KEYS and all backward-compat identifiers unchanged; no secrets in responses. Remaining gate before Azure deploy: subscription setup + GitHub secrets; remaining gate before live TTS: Farnsworth bakeoff.
- 2026-06-07: Audit and planning: 9 open issues reviewed; 6 progressed locally (issues #1, #2, #3, #6, #8, #7 code-ready). 2 blockers remain (issues #4 TTS bakeoff, #5 Spotify research — both require specialist investigation). Local work is cohesive and ready for single PR `wave-1-2-3-contract-pipeline-docs` closing/progressing issues #8, #3, #2, #1, #6, #7. Azure subscription remains the only blocker for deployment; all code, tests, docs, and design work is complete and validated. Decision document written to `.squad/decisions/inbox/leela-issue-first-plan.md`.
📌 Team update (2026-06-07T19:49:59Z): Issue-first/PR workflow rule activated. PR #10 (wave-1-2-3-contract-pipeline-docs) closes #3, #8; progresses #1, #2, #6, #7. Inbox decisions merged (15 files). Post-merge tasks: #4 (TTS), #5 (Spotify) parallel, then #9 (CI) and #7 (deploy).
- 2026-06-07T20:52:01.950+00:00: Reviewer revision for PR #11/#7 caps deployment Function App names at 35 characters in workflow/Bicep so derived App Service Plan (`-plan`) and Log Analytics (`-law`) names remain Azure-compliant; optional app/storage overrides remain supported but unsafe values fail before deploy.

📌 Team update (2026-06-07T20:52:01Z): Non-Bender PR #11 revision approved; naming constraints prevent Azure silent failures. CI green; ready for live prod/OIDC deploy and endpoint smoke test. — consolidated by Scribe from Bender, Hermes, Fry

📌 Team update (2026-09-21T22:12:12.721+00:00): The durable lifecycle CAS-before-manifest-parsing fix was accepted and included in final approved commit `3b25ce995b55b140c9fd452b843fe901a0d14310`. Six requested threads were resolved; four new/unrelated threads remain and PR #682 was not merged.

📌 Team update (2026-09-22T07:47:54.369+00:00): Final PR #682 classification approved head `cfb0bb925838cf909bc6842743f18fa2147b3d1c`: OPEN, MERGEABLE, CLEAN, 15/15 checks passed, 0 unresolved threads, operator-only and unmerged. Preserve strict reviewer lockout: a rejection transfers revision ownership to a different agent, followed by independent re-review; unsupported new blockers should be rejected rather than expanded into scope.

📌 Team update (2026-09-22T14:52:36.169+00:00): PR #682 final remediation completed and was independently accepted after metadata invalidation was restored. Coordinator pushed final commit `2ca66d4d33d8ff5efda11ea9e7e5c6ff1bd041b5`; all hosted checks passed. The PR remains unmerged; no deployment or provider mutation occurred, and PR #684 was untouched.

📌 Team update (2026-09-22T17:40:25.729+00:00): Leela independently corrected Bender's rejected PR #684 provider work, but concurrent remote movement prevented push; Fry found two additional defects and enforced Leela's lockout. Later independent revisions reached approved commit `c8a4922a78af09ebc6c28cc69d795f035793cbcd`.

📌 Team update (2026-09-22T21:35:50.453+00:00): Leela independently revised Hermes-rejected PR #684 work, reconciled concurrent remote commits, finalized evidence, and pushed exact final SHA `ac7bbdb32c762b32be7d4cd388ce42177c5a6f96`. PR #684 remains draft and blocked behind operator-only #682; P05/P06 remain open, with no merge, deployment, dispatch, provider mutation, or #682 change.

📌 Team update (2026-09-22T19:38:30.536+00:00): PR #684 delivery candidate reached f164977089d76905d957c32eb071777b8371da58 after independent locked revisions, exact-head acceptance, and full validation; scheduler notification repair and due reconciliation use independent bounded cursors. — decided by Bender

📌 Team update (2026-09-23T14:19:30.910+00:00): jmservera externally merged PR #686 at 2026-09-23T15:40:18Z as `5f31a7064568c323f3127042578f14c5e0693a31` after final head `a7cf44cec4f25ee43ca01a116843176144622368` passed all 13 exact-head checks. Fry's final verdict remained REJECT because duplicate episode identity across pages can hide a missing item and falsely prove absence; documentation also conflicts on cursor pagination and verification state. The remote branch was deleted and four review threads remain unresolved. — final review by Fry

📌 Team update (2026-09-23T18:53:26.430+00:00): Leela recovered the post-merge PR #693 safety gap through corrective PR #695. After Fry rejected `9f7abaf8`, Leela moved the fail-closed guard before all credential/provider access in `62880f57`, strengthened tests, resolved the thread, and squash-merged the approved PR as `c52782e3c683647da1c1c1321e2a1f8c63141872` at 2026-09-23T19:20:21Z. — approved by Fry
