# Work Routing

How to decide who handles what.

## Routing Table

| Work Type | Route To | Examples |
|-----------|----------|----------|
| API, ACA, Bicep, CI/CD, secrets automation | Bender | `podcaster/api.py`, `infra/main.bicep`, deploy workflow, contract wiring |
| Script, transcript, show notes, TTS-ready copy | Farnsworth | Episode scripts, prompts, transcript standards, TTS bakeoff criteria |
| Secrets, logging policy, permissions, threat model | Hermes | API-key handling, workflow permission scoping, release safety checklist |
| SquadScope integration UX, publishing packet, distribution research | Amy | Link-only integration, packet usability, Spotify/host research framing |
| Test coverage, dry runs, response-shape regression | Fry | `tests/`, edge cases, smoke scripts, validation scenarios |
| Code review | Fry / Leela / Hermes | Review PRs, check quality, security, contract stability |
| Scope, priorities, acceptance criteria, review gate | Leela | What to build next, trade-offs, launch readiness, human review gate |
| Backlog visibility, ambiguity checks, keep-alive | Ralph | Untriaged issues, broken assumptions, work-queue monitoring |
| Session logging, decisions, changelog | Scribe | Automatic — never needs routing |

## Issue Routing

| Label | Action | Who |
|-------|--------|-----|
| `squad` | Triage: analyze issue, assign `squad:{member}` label | Leela (Lead) |
| `squad:{name}` | Pick up issue and complete the work | Named member |

### How Issue Assignment Works

1. When a GitHub issue gets the `squad` label, **Leela** triages it — analyzing content, assigning the right `squad:{member}` label, and commenting with triage notes.
2. When a `squad:{member}` label is applied, that member picks up the issue in their next session.
3. Members can reassign by removing their label and adding another member's label.
4. The `squad` label is the "inbox" — untriaged issues waiting for Lead review.

## Rules

1. **Eager by default** — spawn all agents who could usefully start work, including anticipatory downstream work (e.g., Fry writing tests from the contract while Bender implements).
2. **Scribe always runs** after substantial work, always as `mode: "background"`. Never blocks.
3. **Quick facts → coordinator answers directly.** Don't spawn an agent for "what header does auth use?"
4. **When two agents could handle it**, pick the one whose domain is the primary concern.
5. **"Team, ..." → fan-out.** Spawn all relevant agents in parallel as `mode: "background"`.
6. **Protect the contract.** Any change to the `/api/generate` response shape routes through Leela (scope) and Fry (regression test); Hermes reviews anything touching secrets or permissions.
7. **Never block SquadScope publishing.** Work that could affect the sister project's publishing pipeline is out of scope — flag it to Leela.

## Squad Upgrade Payload Isolation

When platform-only Squad upgrade payload work is needed, keep it on its own branch/PR and route it with this sequence:

1. **Ralph gate:** hold the platform branch until Podcaster deploy/product dependency PRs are green and ready to merge.
2. **Scope isolation:** exclude deploy/product files from the platform PR; include only workflow/platform payload files.
3. **Primary review:** request **Bender** for workflow/platform mechanics.
4. **Security/access review:** request **Hermes** whenever permissions, secrets, MCP config, or automation access are touched.
5. **PR scope log:** include a concise inventory of new/changed workflows, templates, skills, MCP config, memory, and casting files in the PR body.
