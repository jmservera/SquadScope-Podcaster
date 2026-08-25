# Privacy Policy — SquadScope/Claracle YouTube Uploader

> **Purpose of this file.** Google OAuth app verification for the
> `youtube` scope (#649, tracked under the #448 verification runbook) requires
> a publicly hosted privacy policy URL on the app's own domain. This is the
> canonical, reviewed content for that page — the operator publishes it
> (verbatim or adapted) at the privacy-policy URL configured on the OAuth
> consent screen. Coordinate edits with Hermes (security) and Leela. Replace
> the bracketed placeholders before publishing.

_Last updated: [DATE]_

## Who this covers

This policy describes how the **SquadScope/Claracle YouTube Uploader**
("the app") handles data when it uploads automatically generated podcast videos
to YouTube, verifies and updates their metadata, and manages the show's
playlist, on behalf of the channel owner who authorizes it.

Operator / data controller: **[ORGANIZATION NAME]**
Contact: **[SUPPORT EMAIL]**

## What the app does

The app generates a weekly, AI-voiced technology-news podcast and an accompanying
video, and — after an editorial review gate — uploads the finished video to the
authorizing user's own YouTube channel as an **unlisted video**. After upload,
the app reads the video back to verify it processed correctly, updates its
metadata as needed, and adds it to the show's YouTube playlist, leaving it for
that user to review and then change its visibility to public (publish)
manually.

## Google user data we access

- **Scope:** `https://www.googleapis.com/auth/youtube` (labeled "Manage your
  YouTube account" on Google's consent screen). This is the narrowest single
  scope covering every operation below; the app does not request
  `youtubepartner` or any other YouTube/Google scope.
- **What that allows and what we use it for:**
  - **Upload** (`videos.insert`): publish the generated video to the
    authorizing user's channel as unlisted.
  - **Read-back and metadata update** (`videos.list`, `videos.update`):
    confirm the upload processed successfully and correct its title,
    description, or other metadata before the user promotes it to public.
  - **Playlist membership** (`playlistItems.list`, `playlistItems.insert`):
    check and add the uploaded video to the show's existing playlist so it
    appears alongside prior episodes.
  - The app does **not** manage subscriptions, comments, ratings, channel
    settings, or any other account data, and does not use this scope to read,
    list, or modify videos it did not itself upload.
- **Whose data:** only the channel owned by the single consenting Google account.
  The app does not collect or process data about any other end users.

## How we use and store data

- The OAuth **refresh token** issued at consent is stored **encrypted in Azure
  Key Vault**. It is never written to logs, source control, analytics, or shared
  with third parties.
- The refresh token is exchanged for **short-lived access tokens** at upload,
  read-back/update, and playlist-management time. Access tokens are held in
  memory for the duration of each call and are not persisted.
- The app reads back and updates only the videos it uploaded itself, and reads
  or modifies playlist membership only for the show's own playlist. It does not
  read or store any other existing YouTube videos, comments, subscribers, or
  analytics belonging to the authorizing account.

## Sharing and disclosure

- We do **not** sell, rent, or share Google user data with third parties.
- The only data transfers are the upload, read-back/update, and
  playlist-management calls described above, made via the YouTube Data API on
  your behalf.
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
  refresh token and stops all uploads, read-back/update calls, and playlist
  management.

## Security

- Least-privilege scope: a single scope (`youtube`) rather than multiple or
  broader grants — the narrowest single scope that still covers upload,
  read-back/update, and playlist management. The app does not request
  `youtubepartner` or manage subscriptions, comments, ratings, or other
  channel/account settings.
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
