# YouTube OAuth App Verification + Compliance Audit (#448)

Operator + Hermes (security/compliance) runbook to move the YouTube uploader
OAuth app from **Testing** to **In production / Verified**, so it runs without
the unverified-app warning, drops the 100-test-user cap, and is eligible to
request a quota increase.

> Builds on the OAuth client + consent flow from #441
> (`docs/youtube-oauth-setup.md`) and the token storage from #443
> (`docs/youtube-token-storage.md`). Parent epic:
> jmservera/SquadScope-Coordinator#28.

---

## 0. Scope classification — what review we actually face

| | Our app |
| --- | --- |
| Requested scope | `https://www.googleapis.com/auth/youtube.upload` (only) |
| Google classification | **Sensitive** scope (not *restricted*) |
| App verification required | **Yes** |
| Brand verification (logo + domain ownership) | **Yes** |
| Privacy policy URL | **Yes** |
| Demo video | **Yes** (full OAuth + upload workflow) |
| Third-party security assessment (CASA / restricted-scope audit) | **No** — not required for sensitive YouTube scopes |

Keeping the **single narrowest scope** (`youtube.upload`) is the deliberate
choice that keeps us in the *sensitive* lane and out of the much heavier
*restricted* security-assessment lane. Do **not** add `youtube`,
`youtube.force-ssl`, or `youtube.readonly` unless a feature truly needs them —
broadening scope can re-trigger and lengthen verification.

References:
- OAuth verification: <https://developers.google.com/identity/protocols/oauth2/verification>
- YouTube API scopes: <https://developers.google.com/identity/protocols/oauth2/scopes#youtube>
- Demo video guidance: <https://support.google.com/cloud/answer/9110914>
- YouTube API Services Terms of Service: <https://developers.google.com/youtube/terms/api-services-terms-of-service>

---

## 1. Prerequisites (gather before submitting)

- [ ] Google Cloud project from #441 (`squadscope-podcaster-youtube` or equivalent).
- [ ] OAuth consent screen exists with the **`youtube.upload`** scope only.
- [ ] A **team-owned** support email and developer-contact email (not a personal
      account) configured on the consent screen.
- [ ] An **app logo** (120×120 px PNG, <1 MB, no copyrighted/placeholder art).
- [ ] A **homepage URL** that describes the app and is on a domain you control.
- [ ] A **privacy policy URL** on the **same domain** as the homepage
      (content: `docs/youtube-privacy-policy.md`).
- [ ] Ownership of that domain verifiable in **Google Search Console** by the
      same Google account / org that owns the Cloud project.
- [ ] A recorded **demo video** (see §4) hosted unlisted on YouTube or similar.

> **Domain note.** Brand verification requires demonstrating ownership of the
> homepage/privacy-policy domain via Search Console. Decide early which domain
> hosts these pages (e.g. a Claracle-owned domain) and verify it; this is often
> the longest-lead item.

---

## 2. Configure the OAuth consent screen for production

1. **APIs & Services → OAuth consent screen** in the #441 project.
2. **User type: External**, Publishing status **Testing** (current).
3. Fill every field — incomplete fields are the most common rejection cause:
   - App name, user support email, app logo.
   - **App home page** (the homepage URL above).
   - **Application privacy policy link** (the privacy policy URL above).
   - Authorized domains (the registrable domain of the URLs above).
   - Developer contact email.
4. **Scopes:** confirm **only** `.../auth/youtube.upload` is listed.
5. Save. Do **not** click "Publish app" until §3 materials are ready — publishing
   starts the verification clock and an incomplete submission gets bounced.

---

## 3. Scope justification (paste into the verification request)

Google asks *why* the app needs the scope and *how* user data is used. Use this
text (Hermes owns the wording; keep it truthful and specific):

> **Scope requested:** `https://www.googleapis.com/auth/youtube.upload`
>
> **What the app does:** SquadScope/Claracle automatically generates a weekly,
> AI-voiced tech news podcast and an accompanying video. After the episode is
> produced and passes an editorial review gate, the app uploads the finished
> video to the channel owner's own YouTube channel as an **unlisted draft** for
> the owner to review and publish manually.
>
> **Why this scope:** `youtube.upload` is the single narrowest scope that permits
> programmatic upload of a finished video. The app does not read, list, modify,
> delete, or manage any other channel data; it does not need `youtube` or
> `youtube.force-ssl`.
>
> **Whose account / data:** The app acts only on the channel owned by the
> consenting Google account. No third-party end-user data is accessed. There is
> exactly one consenting account (the show's own channel owner).
>
> **Data handling:** The OAuth **refresh token** is stored encrypted in **Azure
> Key Vault** and is never logged, committed, or shared. It is exchanged for
> short-lived access tokens at upload time only. The app stores no YouTube user
> data beyond the credentials needed to upload, and uploads only content the app
> itself generated.
>
> **Compliance:** Use complies with the YouTube API Services Terms of Service and
> the Google API Services User Data Policy, including Limited Use.

---

## 4. Demo video script (record before publishing)

Keep it **under 5 minutes**, screen-recorded, narrated. The reviewer must see the
**OAuth consent screen with the exact scope** and the **upload result**.

1. **Intro (15s):** State the app name and that it uploads an AI-generated weekly
   tech podcast video to the owner's YouTube channel using `youtube.upload`.
2. **OAuth flow (60–90s):** Run `python scripts/youtube_oauth_setup.py`. Show:
   - The Google sign-in.
   - The **consent screen** clearly displaying the app name **and the
     `.../auth/youtube.upload` scope**.
   - Granting consent and the success result.
3. **What we do with the grant (60s):** Show the pipeline performing an upload
   (`distribute_video()` / `podcaster/video/distribution.py`) and the resulting
   **unlisted draft** appearing in YouTube Studio.
4. **Data handling (30s):** State on-camera that the refresh token is stored in
   Azure Key Vault, never logged, and used only to mint short-lived upload
   tokens. Show the privacy policy page briefly.
5. **Close (15s):** Reiterate single narrow scope, single channel, no third-party
   data.

Host the recording unlisted and paste the link into the verification request.

---

## 5. Submit for verification

1. Consent screen → **Publish app** → confirm moving to **In production**.
2. Google prompts to **prepare for verification** because a sensitive scope is
   requested → start the verification request.
3. Provide: scope justification (§3), demo video link (§4), and confirm the
   homepage + privacy-policy URLs.
4. Complete **brand verification** (logo + Search Console domain ownership) if
   prompted.
5. Submit.

**Timeline:** sensitive-scope verification typically takes **a few days to
several weeks**, with one or more reviewer back-and-forths. Respond promptly —
unanswered reviewer questions auto-close the request.

---

## 6. Audit tracking

Track the request to closure. Update this table (or the linked issue) as it moves:

| Step | Owner | Status | Date | Notes |
| --- | --- | --- | --- | --- |
| Domain chosen + Search Console verified | Operator | ☐ | | |
| Homepage URL live | Operator | ☐ | | |
| Privacy policy URL live (`youtube-privacy-policy.md`) | Operator/Hermes | ☐ | | |
| App logo prepared | Operator | ☐ | | |
| Consent screen fully filled | Operator | ☐ | | |
| Scope confirmed = `youtube.upload` only | Hermes | ☐ | | |
| Scope justification finalized | Hermes/Leela | ☐ | | |
| Demo video recorded + hosted | Operator | ☐ | | |
| Verification submitted | Operator | ☐ | | |
| Reviewer questions answered | Operator/Hermes | ☐ | | |
| **App moved to In production / Verified** | Google | ☐ | | |
| Unverified-app warning gone (re-consent test) | Operator | ☐ | | |
| Quota-increase request prepared (→ #447) | Bender | ☐ | | |

---

## 7. After verification

- **Re-test consent** in an incognito session: the unverified-app warning must be
  gone. If you re-mint the refresh token, store it in Key Vault (#443).
- **Quota increase:** verification unblocks the path to request more than the
  default 10,000 units/day. The default allows ~6 resumable uploads/day; the
  multi-language fan-out (#439) and quota monitoring (#447) determine whether to
  request an increase. File the request via the project's YouTube Data API quota
  page with traffic justification.
- **Keep it verified:** material changes (new scopes, new domain, ownership
  transfer) can require re-verification. Keep the consent screen, privacy policy,
  and brand verification current.

---

## Acceptance criteria mapping (#448)

- **App moves from "testing" to "verified/production"** → §2–§5 procedure; final
  state recorded in the §6 audit table (the *submission* is a manual operator
  action requiring the Google account that owns the channel — no CI path).
- **No unverified-app warning on consent** → §7 re-consent test.
- **Path to request quota increase unblocked** → §7 quota-increase step (feeds
  #447).

## Security / compliance notes (Hermes)

- Single narrowest scope (`youtube.upload`) — keeps us out of restricted-scope
  security assessment and limits blast radius.
- Refresh token: Key Vault only (#443); never logged, committed, or pasted.
- Privacy policy and Limited-Use compliance reviewed before submission.
- Demo video must not expose secrets — never show the refresh/access token or
  client secret on screen.

Part of jmservera/SquadScope-Coordinator#28
