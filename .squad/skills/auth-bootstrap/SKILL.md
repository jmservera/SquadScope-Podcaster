---
name: auth-bootstrap
description: Safe bootstrap pattern for service-to-service API keys and future OIDC migration
domain: security, deployment, github-actions, azure
confidence: high
source: earned (Podcaster issue #7 deployment bootstrap)
---

## Pattern

When a service contract still requires a shared API key but the production secret is not bootstrapped yet:

1. Prefer a pre-created stable secret in the protected GitHub environment for normal rotation.
2. If missing, generate a high-entropy key inside the deploy workflow, mask it immediately, and pass it only through in-memory workflow environment/outputs needed by deployment and gated sync.
3. Do not print the generated key for manual handoff. Manual handoff should use a pre-created known secret or a privileged operator reading the deployed app setting without logging it.
4. If syncing to another repository, require an explicit workflow input plus a tightly scoped GitHub credential; never assume Azure OIDC can write GitHub secrets.
5. Document a future OIDC migration path separately and preserve the shared-key contract until both caller and service have deployed token validation safely.

## Azure federated identity guidance

Use one Azure federated identity for deployment and a separate identity only for future caller authentication. The caller identity should have no Azure resource-management permissions; grant only the app role/audience needed to invoke the service.
