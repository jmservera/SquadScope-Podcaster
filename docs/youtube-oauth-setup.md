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
4. **Scopes:** add only `https://www.googleapis.com/auth/youtube.upload`.
   Narrow scope eases app verification (#448) and limits blast radius if the
   token leaks. Do **not** add `youtube` or `youtube.force-ssl` unless a feature
   requires them.
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

The script:

1. Starts a loopback HTTP server on an ephemeral `127.0.0.1` port.
2. Opens the Google consent URL (`access_type=offline`, `prompt=consent` so a
   refresh token is always returned) — sign in as the channel owner and approve.
3. Captures the authorization code on the loopback redirect (validates `state`
   for CSRF), exchanges it for tokens, and prints the **refresh token**.

For piping into a secret store:

```bash
python scripts/youtube_oauth_setup.py --json | jq -r .refresh_token
```

## 7. Store the refresh token securely (→ #443)

The refresh token is a durable credential. Store it in **Azure Key Vault** and
expose it to the pipeline as `VIDEO_YOUTUBE_REFRESH_TOKEN` (see #443). Never
commit, log, or paste it into chat/issues.

The distribution path then exchanges it for short-lived access tokens at upload
time — see `podcaster/video/distribution.py` (`_get_youtube_access_token`).

---

## Scopes & environment summary

| Setting | Value |
| --- | --- |
| API | YouTube Data API v3 |
| Scope | `https://www.googleapis.com/auth/youtube.upload` |
| Auth endpoint | `https://accounts.google.com/o/oauth2/v2/auth` |
| Token endpoint | `https://oauth2.googleapis.com/token` |
| Client type | Desktop app (loopback redirect) |
| `VIDEO_YOUTUBE_CLIENT_ID` | OAuth2 desktop client id |
| `VIDEO_YOUTUBE_CLIENT_SECRET` | OAuth2 desktop client secret |
| `VIDEO_YOUTUBE_REFRESH_TOKEN` | Minted via step 6; stored in Key Vault (#443) |

## Security notes (Hermes)

- Refresh token = long-lived secret → Key Vault only (#443), never env-committed.
- Minimal `youtube.upload` scope; no broader grants.
- `state` parameter validated on the redirect to prevent CSRF code injection.
- Secrets are read from the environment only; the setup script prints the
  refresh token solely to the operator's terminal and redacts the access token.
- Production requires OAuth app verification before leaving Testing mode (#448).

Part of jmservera/SquadScope-Coordinator#28
