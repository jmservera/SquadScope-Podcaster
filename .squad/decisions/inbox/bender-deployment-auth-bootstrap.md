---
date: 2026-06-07T20:52:01.950+00:00
by: Bender (Platform / Backend)
---

# Deployment auth bootstrap

## Decision

Keep the current shared `x-podcaster-api-key` contract for issue #7, but make the deploy path bootstrap-safe: `PODCASTER_API_KEY` is optional in the Podcaster `prod` environment. If it is absent, the deploy workflow generates a 256-bit key, masks it immediately, sets it as the Function App app setting, and never prints it.

## Rationale

This lets Azure deployment succeed without pre-existing secret material while preserving SquadScope compatibility. Manual caller handoff still needs a stable pre-created secret, because generated keys are intentionally unrecoverable from logs. Automated SquadScope sync remains explicitly gated by `sync_squadscope=true` and `SQUADSCOPE_SYNC_TOKEN` because Azure OIDC cannot write GitHub secrets in another repository.

## Operational impact

- `AZURE_FUNCTION_APP_NAME` and `AZURE_STORAGE_ACCOUNT_NAME` are optional overrides; deterministic defaults are derived from resource context and validated against Azure naming rules.
- If generated names collide globally, set the corresponding override variable and rerun deployment.
- If using a generated key and SquadScope needs to call this deployment, run deployment with `sync_squadscope=true` and a scoped `SQUADSCOPE_SYNC_TOKEN`, or set a known `PODCASTER_API_KEY` secret and redeploy.
- No workflow step may echo or summarize the API key value.
