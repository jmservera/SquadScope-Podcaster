# YouTube Data API v3 — OAuth2 setup (#441)

One-time operator runbook to stand up the Google Cloud project and OAuth2 client
needed for automated YouTube uploads, and to mint the refresh token the
distribution pipeline uses.

> **Why not a service account?** YouTube uploads act on behalf of a channel-owning
> Google account. Service accounts cannot own or post to a YouTube channel, so
> the Data API rejects service-account uploads. We must use OAuth2 **user
> consent** → refresh token. This is a hard YouTube constraint, not a choice.

---

## 1. Create the Google Cloud project

1. Go to <https://console.cloud.google.com/> and create a project, e.g.
   `squadscope-podcaster-youtube`.
2. Note the project ID — it scopes quota and the OAuth client.

## 2. Enable the YouTube Data API v3

1. APIs & Services → **Library** → search "YouTube Data API v3" → **Enable**.
2. Default quota is **10,000 units/day**. A resumable video upload costs
   **~1,600 units**, so the default allows ~6 uploads/day. Quota monitoring and
   rate limiting are tracked in #447; a quota increase request may be needed for
   multi-language fan-out (#439).

## 3. Configure the OAuth consent screen

1. APIs & Services → **OAuth consent screen**.
2. User type: **External** (unless all uploaders are in a Google Workspace org).
3. App name, support email, developer contact — use a team-owned address.
4. **Scopes:** add `https://www.googleapis.com/auth/youtube` (label: *"Manage
   your YouTube account"*). This is the narrowest single scope that covers
   every call this app makes: `videos.insert` (upload), `videos.list`
   (read-back verification of the uploaded video's status/metadata),
   `videos.update` (`part=status` — promoting an approved draft to public or
   scheduling a future publish), and `playlistItems.list`/`playlistItems.insert`
   (show playlist management). `youtube.upload` alone only authorizes
   `videos.insert` — every other call above 403s with `insufficientPermissions`
   (#649). Google also accepts `youtube.force-ssl` for the same operations, but
   this app standardizes on the `youtube` scope. Do **not** add `youtubepartner`;
   it is for content-partner asset management the app does not do.
5. **Test users:** while the app is in *Testing* mode, add the Google account
   that owns the target YouTube channel as a test user. Testing-mode refresh
   tokens expire after 7 days — fine for a spike, but **production needs the app
   moved to *In production*** via OAuth app verification (#448).

## 4. Create the OAuth2 client (Desktop app)

1. APIs & Services → **Credentials** → **Create credentials** → **OAuth client ID**.
2. Application type: **Desktop app** (uses the loopback redirect, no hosted
   callback to operate).
3. Download the client ID and client secret.

## 5. Confirm channel ownership / linkage

- Sign in to <https://studio.youtube.com/> with the account you added as a test
  user and confirm it owns (or manages) the channel you intend to publish to.
- If the channel is a **Brand Account**, the consenting Google account must have
  Owner/Manager access to that brand channel; the consent screen lets you pick
  the brand channel during authorization.

## 6. Mint the refresh token (one-time consent run)

```bash
export VIDEO_YOUTUBE_CLIENT_ID="<client id from step 4>"
export VIDEO_YOUTUBE_CLIENT_SECRET="<client secret from step 4>"
python scripts/youtube_oauth_setup.py
```

> **Replacing an upload-only token (#649):** OAuth scopes cannot be widened in
> place — a refresh token minted with `youtube.upload` stays upload-only
> forever, even if you later change the consent screen's configured scopes.
> If production is running on such a token (visible as
> `"scope": "https://www.googleapis.com/auth/youtube.upload"` in the token
> response, and as 403 `insufficientPermissions` on playlist/read-back calls),
> you must:
> 1. Re-run this script to mint a **new** refresh token with the `youtube`
>    scope (the default — `access_type=offline` + `prompt=consent` force a
>    fresh grant even if the account previously consented).
> 2. Replace `VIDEO_YOUTUBE_REFRESH_TOKEN` in Key Vault (#443) with the new
>    value and confirm the pipeline works with it.
> 3. Revoke **only the old token value** via Google's revocation endpoint,
>    passing the token through stdin so it never appears in shell history or
>    a process listing:
>    ```bash
>    read -rs -p "Old refresh token to revoke: " OLD_REFRESH_TOKEN; echo
>    curl -s -X POST https://oauth2.googleapis.com/revoke \
>      --data-urlencode token@- <<< "$OLD_REFRESH_TOKEN"
>    ```
>    Do **not** use <https://myaccount.google.com/permissions> for this — that
>    page revokes the app's *entire* grant for the account, which would also
>    invalidate the new token you just stored, since both share the same
>    OAuth client. Only use the account permissions page if you are fully
>    decommissioning the integration.

The script:

1. Starts a loopback HTTP server on an ephemeral `127.0.0.1` port.
2. Opens the Google consent URL (`access_type=offline`, `prompt=consent` so a
   refresh token is always returned) — sign in as the channel owner and approve.
3. Captures the authorization code on the loopback redirect (validates `state`
   for CSRF), exchanges it for tokens, and prints the **refresh token**.

By default the script requests `https://www.googleapis.com/auth/youtube`. Pass
`--upload-only` to explicitly request the narrower `youtube.upload` scope
instead — only do this if you are certain the pipeline will never call
`videos.list` (read-back verification), `videos.update` (public-promotion
status changes), or any `playlistItems` endpoint, since that token cannot be
upgraded later without repeating this whole flow.

For piping into a secret store:

```bash
python scripts/youtube_oauth_setup.py --json | jq -r .refresh_token
```

## 7. Store the refresh token securely (→ #443)

The refresh token is a durable credential. Store it in **Azure Key Vault** and
expose it to the pipeline as `VIDEO_YOUTUBE_REFRESH_TOKEN` (see #443). Never
commit, log, or paste it into chat/issues.

The distribution path exchanges it for short-lived access tokens at upload,
read-back verification, and playlist-management time — see
`podcaster/video/distribution.py` (`_get_youtube_access_token`). The same
refresh token is also used by the promotion step
(`scripts/youtube_promote.py`) to verify and promote a draft to public.

---

## Scopes & environment summary

| Setting | Value |
| --- | --- |
| API | YouTube Data API v3 |
| Scope | `https://www.googleapis.com/auth/youtube` (`--upload-only` opts into `https://www.googleapis.com/auth/youtube.upload`, not recommended) |
| Auth endpoint | `https://accounts.google.com/o/oauth2/v2/auth` |
| Token endpoint | `https://oauth2.googleapis.com/token` |
| Client type | Desktop app (loopback redirect) |
| `VIDEO_YOUTUBE_CLIENT_ID` | OAuth2 desktop client id |
| `VIDEO_YOUTUBE_CLIENT_SECRET` | OAuth2 desktop client secret |
| `VIDEO_YOUTUBE_REFRESH_TOKEN` | Minted via step 6; stored in Key Vault (#443) |

## Security notes (Hermes)

- Refresh token = long-lived secret → Key Vault only (#443), never env-committed.
- Scope is `youtube` (not `youtubepartner`) — the narrowest single scope that
  still covers upload, read-back/update, and playlist management; no broader
  grants. Google classifies `youtube` the same as `youtube.upload` — a
  *sensitive*, not *restricted*, scope (re-confirm current classification in
  the Google verification flow — see docs/youtube-oauth-verification.md).
- `state` parameter validated on the redirect to prevent CSRF code injection.
- Secrets are read from the environment only; the setup script prints the
  refresh token solely to the operator's terminal and redacts the access token.
- Production requires OAuth app verification before leaving Testing mode (#448).

Part of jmservera/SquadScope-Coordinator#28
