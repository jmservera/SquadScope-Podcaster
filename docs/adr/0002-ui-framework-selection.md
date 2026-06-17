# ADR-0002: UI Framework Selection

**Status:** Accepted  
**Date:** 2026-06-17  
**Context:** Issue #251 — Evaluate ToolJet, Refine, and custom React for the management UI.

## Decision

**Continue with custom React** (the existing `ui/` scaffold) enhanced with a lightweight component library as needed.

## Context

The SquadScope-Podcaster management UI needs:
- Azure Entra ID (MSAL) authentication
- Job monitoring with status tracking
- Episode list with audio preview/streaming
- Podcast-specific workflows (quality review, publish approval)

The `ui/` directory already contains a functional scaffold:
- React 19 + Vite + TypeScript
- MSAL auth fully integrated (`@azure/msal-browser` + `@azure/msal-react`)
- Authenticated API client with token management
- Job monitoring (list, detail, logs) connected to the monitoring API
- React Router navigation + protected routes
- Vitest test suite

## Evaluation

### Option 1: ToolJet (~33k★)

| Criteria | Assessment |
|----------|------------|
| Azure Entra ID | Supported via OAuth2 connector |
| Blob streaming | Native Azure Blob connector |
| Customization | Limited — drag-and-drop builder, hard to extend for podcast workflows |
| License | **AGPL-3.0** — requires open-sourcing derivative works or purchasing enterprise |
| Dev speed | Fast for simple CRUD, slow for custom logic |
| Migration cost | **High** — discard entire existing scaffold |

**Verdict:** AGPL license is incompatible with this project's needs. Would also require rebuilding all existing auth and API integration work from scratch.

### Option 2: Refine (~30k★)

| Criteria | Assessment |
|----------|------------|
| Azure Entra ID | Supported via custom auth provider |
| Blob streaming | No built-in connector; would use existing API proxy |
| Customization | Good — React-based, headless approach |
| License | MIT ✓ |
| Dev speed | Faster for CRUD-heavy apps |
| Migration cost | **Medium** — must restructure to Refine's data provider/resource pattern |

**Verdict:** Refine excels at admin panels with many CRUD resources. Our UI has only 2-3 views with podcast-specific logic (audio preview, quality scoring, publish workflows) that don't map cleanly to CRUD. The abstraction overhead outweighs benefits given the existing scaffold.

### Option 3: Custom React (current scaffold) ✅

| Criteria | Assessment |
|----------|------------|
| Azure Entra ID | **Already working** — MSAL fully integrated |
| Blob streaming | Via existing authenticated API proxy |
| Customization | Maximum flexibility for podcast workflows |
| License | No framework license concerns |
| Dev speed | Fastest — builds on existing working code |
| Migration cost | **Zero** — already built |

**Verdict:** The existing scaffold already satisfies 2 of the 3 acceptance criteria (auth + job list). Adding an episode list with audio preview completes the prototype with minimal effort.

## Consequences

### Positive
- Zero migration cost — auth, API client, job monitor already functional
- Full control over podcast-specific UX (audio waveforms, quality review, publish approval)
- No framework lock-in or license constraints
- Can adopt any component library (Radix, shadcn/ui, etc.) independently

### Negative
- No built-in CRUD scaffolding (acceptable given our few views)
- Must build data-fetching patterns ourselves (already done in `api/` layer)

### Risks & Mitigations
- **Risk:** UI grows complex without structure → **Mitigation:** Keep component boundaries clear, add a component library when styling needs grow
- **Risk:** Re-inventing patterns → **Mitigation:** Use TanStack Query or SWR for data fetching if complexity grows

## References
- [ToolJet](https://github.com/ToolJet/ToolJet) — AGPL-3.0
- [Refine](https://github.com/refinedev/refine) — MIT
- Existing scaffold: `ui/src/` in this repository
