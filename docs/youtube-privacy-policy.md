# Privacy Policy — SquadScope/Claracle YouTube Uploader

> **Purpose of this file.** Google OAuth app verification for the
> `youtube.upload` scope (#448) requires a publicly hosted privacy policy URL on
> the app's own domain. This is the canonical, reviewed content for that page —
> the operator publishes it (verbatim or adapted) at the privacy-policy URL
> configured on the OAuth consent screen. Coordinate edits with Hermes (security)
> and Leela. Replace the bracketed placeholders before publishing.

_Last updated: [DATE]_

## Who this covers

This policy describes how the **SquadScope/Claracle YouTube Uploader**
("the app") handles data when it uploads automatically generated podcast videos
to YouTube on behalf of the channel owner who authorizes it.

Operator / data controller: **[ORGANIZATION NAME]**
Contact: **[SUPPORT EMAIL]**

## What the app does

The app generates a weekly, AI-voiced technology-news podcast and an accompanying
video, and — after an editorial review gate — uploads the finished video to the
authorizing user's own YouTube channel as an **unlisted draft** for that user to
review and publish manually.

## Google user data we access

- **Scope:** `https://www.googleapis.com/auth/youtube.upload` only.
- **What that allows:** uploading a video to the authorizing user's YouTube
  channel. The app does **not** read, list, modify, delete, or manage any other
  YouTube or Google account data.
- **Whose data:** only the channel owned by the single consenting Google account.
  The app does not collect or process data about any other end users.

## How we use and store data

- The OAuth **refresh token** issued at consent is stored **encrypted in Azure
  Key Vault**. It is never written to logs, source control, analytics, or shared
  with third parties.
- The refresh token is exchanged for **short-lived access tokens** only at upload
  time. Access tokens are held in memory for the duration of an upload and are
  not persisted.
- The app uploads only content it generated itself. It does not read back or
  store your existing YouTube videos, comments, subscribers, or analytics.

## Sharing and disclosure

- We do **not** sell, rent, or share Google user data with third parties.
- The only data transfer is the upload of the generated video to YouTube via the
  YouTube Data API, performed on your behalf.
- Use of YouTube data complies with the
  [YouTube API Services Terms of Service](https://developers.google.com/youtube/terms/api-services-terms-of-service)
  and the
  [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
  including the **Limited Use** requirements. By using the app you are also bound
  by the [YouTube Terms of Service](https://www.youtube.com/t/terms) and the
  [Google Privacy Policy](https://policies.google.com/privacy).

## Data retention and revocation

- The refresh token is retained only as long as needed to operate the uploader
  and is deleted from Key Vault when the integration is decommissioned.
- You can revoke the app's access at any time at
  <https://myaccount.google.com/permissions>; revocation invalidates the stored
  refresh token and stops all uploads.

## Security

- Least-privilege scope (`youtube.upload` only).
- Secrets stored in Azure Key Vault; never logged or committed.
- OAuth `state` parameter validated to prevent CSRF during the consent flow.
- Access limited to the operating team.

## Changes to this policy

We may update this policy; material changes will be reflected by the
"Last updated" date above and, where required, by re-verification of the OAuth
app.

## Contact

Questions about this policy or the app's data handling: **[SUPPORT EMAIL]**.

---

_Related: `docs/youtube-oauth-verification.md` (#448),
`docs/youtube-token-storage.md` (#443), `docs/youtube-oauth-setup.md` (#441)._
