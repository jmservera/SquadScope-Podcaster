# SquadScope Podcaster — Management UI

Custom React dashboard for managing podcast generation pipelines.

## Architecture Decision

**Framework: Custom React** (see [ADR-0002](../docs/adr/0002-ui-framework-selection.md))

Evaluated ToolJet, Refine, and custom React. Chose to continue with the existing
scaffold due to: zero migration cost, working MSAL auth, maximum flexibility for
podcast-specific workflows, and no license constraints.

## Stack

- **React 19** + TypeScript + Vite
- **Azure MSAL** for Entra ID authentication
- **React Router** for navigation
- **Vitest** for testing

## Features

- Azure Entra ID (MSAL) sign-in/sign-out
- Job monitoring (list, detail, logs)
- Episode list with audio preview
- Protected routes with token-based API access

## Development

```bash
cp .env.sample .env   # Configure Azure client/tenant IDs
npm install
npm run dev           # http://localhost:5173
npm run test          # Run tests
npm run build         # Production build
```
