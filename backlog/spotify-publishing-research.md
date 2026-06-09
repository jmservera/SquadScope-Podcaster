# Backlog: Spotify Publishing Research

Issue: #5

## Research conclusion

Spotify should be treated as an RSS distribution target for Podcaster MVP, not as a direct publish API target.

As of this research pass, Spotify's public developer Web API is for Spotify app integrations such as content metadata, playlist management, and playback control. It does not document a podcast episode upload or publish endpoint. Spotify for Creators publishes shows hosted on Spotify automatically, and externally hosted shows reach Spotify through host/provider submission and RSS ingestion.

## Direct Spotify upload API

No direct Spotify upload path is selected for MVP.

- **Direct API publish:** Not supported by the public Spotify Web API documentation reviewed for this issue.
- **Spotify-hosted show:** A human operator can upload and manage the episode in Spotify for Creators.
- **Externally hosted show:** A human operator or host automation publishes the episode through the podcast host; Spotify ingests it from the show RSS feed after the show is submitted to Spotify.

## Supported automation paths

### MVP path: manual upload

1. Podcaster generates a reviewed publishing packet.
2. Operator downloads the packet before artifact URL expiry.
3. Operator confirms the episode is approved, audio is final, and required disclosures are present.
4. Operator uploads the MP3 and metadata in Spotify for Creators or the selected podcast host UI.
5. Operator records the public platform URL, publish time, and any corrections outside Podcaster's current API response.

This keeps publication human-gated and avoids storing platform credentials in Podcaster.

### Preferred future automation path: podcast host API plus RSS

If automation is later approved, select a podcast host that provides an episode publishing API or secure workflow for updating the show feed. Podcaster would publish to that host, and Spotify would receive the episode through RSS ingestion.

Required design work before implementation:

- Host/vendor selection and terms review.
- Credential model, preferably OAuth or short-lived tokens owned by the operator.
- Secret storage in Azure or GitHub environments with no logging of tokens.
- Idempotency key for episode creation/update.
- Rollback/removal workflow owned by the operator.
- Audit trail recording requester, reviewer, publish target, publish time, public URL, and checksum of the submitted audio.

### Not selected: Podcaster-managed RSS feed

Podcaster could theoretically generate and host an RSS feed, but that would turn Podcaster into a public podcast hosting service. That is not selected for MVP because it adds public hosting, feed availability, takedown, analytics, artwork, email exposure, retention, and platform compliance responsibilities.

## Manual MVP process

The operator packet must contain or point to:

- Final MP3.
- Episode title.
- Episode description/show notes.
- Transcript.
- Source article URL.
- Corrections/contact link.
- AI/synthetic voice disclosure text when TTS or synthetic narration is used.
- Rights and attribution notes for article content, TTS provider, voice, music, and artwork.
- Review approval evidence and reviewer identity.
- Cost ledger and audio validation status.

Manual upload checklist:

1. Confirm `MANIFEST.json` has `review_status: approved`.
2. Confirm placeholder audio has been replaced by final reviewed audio.
3. Confirm ffmpeg/audio validation has passed.
4. Confirm the monthly cost ledger is within guardrails or has an explicit operator override.
5. Copy title, description, transcript, and disclosure from the packet into Spotify for Creators or the selected podcast host.
6. Upload the MP3.
7. Schedule or publish.
8. Record the public episode URL and publish timestamp in the operator audit trail.

## Platform constraints

- **AI voice / impersonation:** Do not use real-person voice cloning. Spotify has reaffirmed that unauthorized impersonation of a creator or host's likeness, including AI voice cloning, can be removed. Podcaster should keep the project-level requirement that AI-generated voices are disclosed in audio and show notes before any public publication.
- **RSS email exposure:** Spotify's RSS help states the feed email can become public when RSS distribution is enabled. Operators should use a distribution mailbox, not a personal address.
- **Episode propagation:** Spotify's creator docs say new or updated episodes usually appear on submitted platforms within a few hours, but operators should allow up to 24 hours.
- **External platform control:** Public RSS feeds can be scraped by third-party apps. Operators may not be able to remove all downstream copies through Spotify alone.
- **Video/music limitations:** Spotify notes video episodes and Music + Talk content have platform-specific availability limits. Podcaster MVP should stay audio-only and avoid licensed music.
- **Analytics:** Spotify/platform analytics remain in the selected host or Spotify for Creators. Podcaster should not promise listener analytics until a platform integration is designed.
- **Monetization:** Do not enable or imply monetization automation. Any monetization setting remains a human operator/platform decision.

## Future automation architecture

If automation is approved after MVP:

1. Add a separate `publish` capability or workflow after human review, not to `/api/generate` by default.
2. Use a selected podcast host API as the primary integration target.
3. Keep Spotify distribution indirect through RSS ingestion unless Spotify publishes an official podcast episode upload API.
4. Store platform credentials outside artifacts and logs.
5. Require explicit operator approval per episode.
6. Return additive `publication_urls` and `publication_audit_url` fields only after the contract is reviewed.

## Sources reviewed

- Spotify Web API documentation: https://developer.spotify.com/documentation/web-api
- Spotify for Creators RSS feed help: https://support.spotify.com/us/creators/article/finding-and-enabling-your-rss-feed/
- Spotify for Creators distribution help: https://support.spotify.com/us/creators/article/distributing-your-show-to-other-platforms/
- Spotify for Creators external hosting help: https://support.spotify.com/us/creators/article/getting-your-show-on-spotify/
- Spotify newsroom on podcast verification and impersonation policy: https://newsroom.spotify.com/2026-05-19/podcast-verification-trust-creators-listeners/
