# YouTube OAuth2 refresh-token storage (#443)

The YouTube uploader authenticates with a long-lived OAuth2 **refresh token**
that it exchanges for short-lived access tokens on every run. This token is
security-critical, is held as an **encrypted deployment secret** — the same
posture as the Spotify `SP_DC`/`SP_KEY` cookies — and is never logged.

Two storage/resolution paths are supported (see "Runtime resolution" below):

- **This repo's production deployment (current default):** the token is
  stored as the `prod` GitHub Actions environment secret
  `VIDEO_YOUTUBE_REFRESH_TOKEN` and injected into the running Azure Container
  App as a Container Apps secret at deploy time (see
  `.github/workflows/reusable-deploy-azure.yml`,
  `infra/modules/aca-video.bicep`, `docs/youtube-oauth-setup.md`). No Azure
  Key Vault call happens at runtime for this path.
- **Direct Key Vault resolution (alternate, not currently wired in this
  repo's deploy workflow):** an operator configures
  `VIDEO_YOUTUBE_KEYVAULT_URL` and omits the direct env var injection, and the
  app fetches the secret from Key Vault at runtime via managed identity.

## Storage (direct Key Vault deployments only)

The command below applies to a deployment using the direct Key Vault
resolution path above. It has no effect on this repo's current production
deployment, which reads the token from the injected
`VIDEO_YOUTUBE_REFRESH_TOKEN` env var first and never reaches this Key Vault
lookup while that env var is set (see "Runtime resolution").

```bash
az keyvault secret set \
  --vault-name <vault> \
  --name youtube-oauth-refresh-token \
  --value "<refresh-token-from-consent-flow>"
```

The initial refresh token is minted by the one-time consent flow (#441 —
`scripts/youtube_oauth_setup.py`, `docs/youtube-oauth-setup.md`).

## Runtime resolution

`podcaster.youtube_credentials.load_youtube_refresh_token()` resolves the token
in this order (value never logged — only presence/length class):

1. **`VIDEO_YOUTUBE_REFRESH_TOKEN`** env var. Azure Container Apps can inject a
   Key Vault *secret reference* directly into this env var, in which case no
   in-process vault call is needed.
2. **Key Vault directly** via managed identity, when
   `VIDEO_YOUTUBE_KEYVAULT_URL` is set. The secret name comes from
   `VIDEO_YOUTUBE_REFRESH_TOKEN_SECRET` (default `youtube-oauth-refresh-token`).

`VideoDistributionConfig.from_env()` uses this resolver; if Key Vault is
unconfigured or unavailable it degrades gracefully to the env var (behavior
identical to before #443).

| Env var | Purpose |
| --- | --- |
| `VIDEO_YOUTUBE_REFRESH_TOKEN` | Token value or resolved KV reference. |
| `VIDEO_YOUTUBE_KEYVAULT_URL` | Vault URL for runtime fetch. |
| `VIDEO_YOUTUBE_REFRESH_TOKEN_SECRET` | Secret name (default `youtube-oauth-refresh-token`). |

Key Vault is read over REST using the project's managed-identity token flow
(`ManagedIdentityTokenCredential`, scope `https://vault.azure.net/.default`) — no
`azure-keyvault-secrets` SDK dependency is added.

## Auto-refresh & revocation handling

`refresh_access_token()` exchanges the refresh token for an access token on every
run — no manual step. A YouTube refresh token does **not** expire on a schedule;
it only stops working if revoked (password change, withdrawn consent, rotated
client secret, or 6 months unused).

When Google returns `invalid_grant`, the helper raises
`YouTubeTokenRevokedError` and fires a **re-authentication alert**: a GitHub
issue titled *"[YouTube] OAuth refresh token revoked/expired — re-authentication
required"* (label `credentials-expired`, de-duplicated against any open issue),
via `podcaster.credential_expiry.notify_youtube_credential_expiry`. Set
`CREDENTIAL_EXPIRY_NOTIFY_DISABLED=true` to suppress in non-prod.

After re-authenticating, the update path depends on how the token reaches this
deployment (see "Runtime resolution" above):

- **This repo's production deployment** injects `VIDEO_YOUTUBE_REFRESH_TOKEN`
  directly from the `prod` GitHub environment secret at deploy time (see
  `.github/workflows/reusable-deploy-azure.yml`,
  `infra/modules/aca-video.bicep`), and `load_youtube_refresh_token()` returns
  that env var immediately without ever consulting Key Vault when it is set
  (`podcaster/youtube_credentials.py`). For this path, update the **GitHub
  environment secret** and redeploy — updating only a Key Vault secret has
  **no effect** on the running app and leaves the old token active. See
  `docs/youtube-oauth-setup.md` for the exact steps.
- If a deployment instead relies on the direct Key Vault runtime resolution
  (`VIDEO_YOUTUBE_KEYVAULT_URL` set and no `VIDEO_YOUTUBE_REFRESH_TOKEN` env
  var injected), update the Key Vault secret — no app restart needed, since
  the token is read at runtime.

## Acceptance mapping

- ✅ Refresh token held as an encrypted deployment secret (GitHub environment
  secret injected into ACA for this repo's production deployment, or Key
  Vault for deployments that opt into direct runtime resolution); never
  logged.
- ✅ Access token auto-refreshes without manual steps.
- ✅ Revoked/expired token triggers a clear re-auth alert.
