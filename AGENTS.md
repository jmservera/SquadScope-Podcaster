# AI Agents

This repository is managed by an AI Squad team. The default agent for all work is **Squad**.

## Usage

```bash
# Default: use Squad agent for all tasks
copilot --agent squad -p "your task here" --allow-all-tools

# For automated issue work (Ralph loop)
squad triage --execute --interval 20 --copilot-flags "--allow-all-tools"
```

## Team Roster
See `.squad/team.md` for the full team composition (Futurama universe). Key members:
- **Leela** — Lead (architecture, decisions)
- **Bender** — Backend (API, infrastructure, deployment)
- **Farnsworth** — Analyst (audio pipeline, algorithms)
- **Hermes** — Security (auth, validation, sanitization)
- **Fry** — Tester (pytest, quality gates)
- **Amy** — Frontend/Integration

## Related Repositories
This engine is currently used by SquadScope/Claracle but is designed to be platform-agnostic.
- [SquadScope](https://github.com/jmservera/SquadScope) — Current caller/integration platform
- [SquadScope-Coordinator](https://github.com/jmservera/SquadScope-Coordinator) — Current orchestration layer
