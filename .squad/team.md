# Podcaster Squad

> SquadScope Podcaster — an Azure-hosted sister service that turns a published SquadScope article into podcast-production artifacts and returns links.

## Coordinator

| Name | Role | Notes |
|------|------|-------|
| Squad | Coordinator | Routes work, enforces handoffs and reviewer gates. Does not generate domain artifacts. |

## Members

| Name | Role | Charter | Status |
|------|------|---------|--------|
| Leela | Producer Lead | `.squad/agents/leela/charter.md` | 🏗️ Active |
| Bender | Platform / Backend | `.squad/agents/bender/charter.md` | 🔧 Active |
| Farnsworth | Script & Audio Editor | `.squad/agents/farnsworth/charter.md` | 📝 Active |
| Hermes | Safety & Security · DevSecOps | `.squad/agents/hermes/charter.md` | 🔒 Active |
| Amy | Distribution UX | `.squad/agents/amy/charter.md` | ⚛️ Active |
| Fry | QA / Tester | `.squad/agents/fry/charter.md` | 🧪 Active |
| Scribe | Session Logger | `.squad/agents/scribe/charter.md` | 📋 Silent |
| Ralph | Work Monitor | `.squad/agents/ralph/charter.md` | 🔄 Monitor |

## Coding Agent

<!-- copilot-auto-assign: false -->

| Name | Role | Charter | Status |
|------|------|---------|--------|
| @copilot | Coding Agent | — | 🤖 Coding Agent |

### Capabilities

**🟢 Good fit — auto-route when enabled:**
- Bug fixes with clear reproduction steps
- Test coverage (adding missing tests, fixing flaky tests)
- Lint/format fixes and code style cleanup
- Dependency updates and version bumps
- Small isolated features with clear specs
- Boilerplate/scaffolding generation
- Documentation fixes and README updates

**🟡 Needs review — route to @copilot but flag for squad member PR review:**
- Medium features with clear specs and acceptance criteria
- Refactoring with existing test coverage
- API endpoint additions following established patterns
- Migration scripts with well-defined schemas

**🔴 Not suitable — route to squad member instead:**
- Architecture decisions and system design
- Multi-system integration requiring coordination
- Ambiguous requirements needing clarification
- Security-critical changes (auth, secrets, key handling)
- Changes to the SquadScope integration contract or response shape
- Anything touching distribution rights/licensing

## Issue Source

**Repository:** jmservera/SquadScope-Podcaster  
**Connected:** 2026-06-07  
**Platform:** GitHub  
**Filters:**
- Labels: `squad`

## Project Context

- **Owner:** jmservera
- **Stack:** Python 3.11 · Azure Container Apps (ACA) · Azure OpenAI (gpt-4o-mini-tts) · Bicep · GitHub Actions (OIDC) · Azure Blob Storage · ACR · App Insights / Log Analytics · pytest
- **Description:** Sister service to `jmservera/SquadScope` that turns a published article into podcast-production artifacts (script, transcript, show notes, audio, publishing packet) and returns links. Full pipeline operational: LLM script gen → TTS synthesis (fable + alloy) → ffmpeg audio assembly → review gate → manual publishing packet.
- **Created:** 2026-06-07
- **Universe:** Futurama
