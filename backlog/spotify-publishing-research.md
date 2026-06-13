# Backlog: Spotify Publishing Research

Issue: #5

## Research conclusion

This research item is now implemented for Podcaster's Spotify-hosted publishing path.

Podcaster now uses the `spotifyconnector` OSS library against the unofficial Spotify for Creators API to publish episodes automatically. The merged implementation landed in PR #183 as `podcaster/publish.py` and is opt-in, runs after generation plus validation, and degrades gracefully so episode generation still succeeds if Spotify publishing is disabled or fails.

## Direct Spotify upload API

Podcaster now publishes to Spotify for Creators through the unofficial reverse-engineered API exposed by the `spotifyconnector` library.

- **Library:** `spotifyconnector` (`higuchiki/spotify-for-creators-api`)
- **Authentication:** operator-supplied `SP_DC` + `SP_KEY` browser cookies are exchanged for a Bearer JWT
- **Show targeting:** the Spotify show ID is stored as `SPOTIFY_SHOW_ID` in GitHub and Azure Container App secrets/configuration
- **Execution model:** publishing is enabled only when `SPOTIFY_PUBLISH_ENABLED=true`
- **Pipeline:** resolve IDs → create episode → signed Google Cloud Storage upload → process upload → set metadata → publish
- **Implementation details:** requests require `?isMumsCompatible=true`; mutation flows require Spotify `Origin`/`Referer` headers; upload finalization uses `uploadType: "default"` rather than `"audio"`
- **Failure handling:** if publish fails, the generation workflow still returns its normal artifact/result payload

The public Spotify Web API still does not document podcast episode upload or publish endpoints. This implementation therefore depends on an unofficial API surface and should be treated as operationally fragile compared with official host/RSS integrations.

## Supported automation paths

### Implemented path: Spotify for Creators auto-publish

The current implementation performs Spotify publication after episode generation and validation complete. It is intentionally not part of the core generation step and does not block the operator from receiving a completed episode package if Spotify publishing is unavailable.

### Still-valid alternative: podcast host API plus RSS

An official podcast host API plus RSS distribution path would still be operationally safer than the current unofficial Spotify integration if a suitable host is selected later.

### Not selected: Podcaster-managed RSS feed

Podcaster could theoretically generate and host an RSS feed, but that would turn Podcaster into a public podcast hosting service. That is not selected for MVP because it adds public hosting, feed availability, takedown, analytics, artwork, email exposure, retention, and platform compliance responsibilities.

## Manual MVP process

Manual upload remains the fallback when `SPOTIFY_PUBLISH_ENABLED` is unset/false or when Spotify auto-publish fails. In that case, the operator uses the generated publishing packet and uploads the episode through Spotify for Creators manually.

## Platform constraints

- **AI voice / impersonation:** Do not use real-person voice cloning. Spotify has reaffirmed that unauthorized impersonation of a creator or host's likeness, including AI voice cloning, can be removed. Podcaster should keep the project-level requirement that AI-generated voices are disclosed in audio and show notes before any public publication.
- **RSS email exposure:** Spotify's RSS help states the feed email can become public when RSS distribution is enabled. Operators should use a distribution mailbox, not a personal address.
- **Episode propagation:** Spotify's creator docs say new or updated episodes usually appear on submitted platforms within a few hours, but operators should allow up to 24 hours.
- **External platform control:** Public RSS feeds can be scraped by third-party apps. Operators may not be able to remove all downstream copies through Spotify alone.
- **Video/music limitations:** Spotify notes video episodes and Music + Talk content have platform-specific availability limits. Podcaster MVP should stay audio-only and avoid licensed music.
- **Analytics:** Spotify/platform analytics remain in the selected host or Spotify for Creators. Podcaster should not promise listener analytics until a platform integration is designed.
- **Monetization:** Do not enable or imply monetization automation. Any monetization setting remains a human operator/platform decision.
- **Cookie expiry risk:** the current automation depends on operator-managed `SP_DC` and `SP_KEY` browser cookies, which expire periodically and require manual rotation.

## Future automation architecture

The post-generation Spotify publish workflow is now implemented in `podcaster/publish.py` and was merged in PR #183.

Remaining work:

1. Add an operator-friendly cookie rotation mechanism for `SP_DC` and `SP_KEY`.
2. Add monitoring and alerting for publish failures or repeated auth expiry.
3. Add episode scheduling support; the current implementation publishes immediately.
4. Evaluate whether a future official host/RSS integration should replace the unofficial Spotify-specific path.

## Sources reviewed

- Spotify Web API documentation: https://developer.spotify.com/documentation/web-api
- spotifyconnector / Spotify for Creators API library: https://github.com/higuchiki/spotify-for-creators-api
- Spotify for Creators RSS feed help: https://support.spotify.com/us/creators/article/finding-and-enabling-your-rss-feed/
- Spotify for Creators distribution help: https://support.spotify.com/us/creators/article/distributing-your-show-to-other-platforms/
- Spotify for Creators external hosting help: https://support.spotify.com/us/creators/article/getting-your-show-on-spotify/
- Spotify newsroom on podcast verification and impersonation policy: https://newsroom.spotify.com/2026-05-19/podcast-verification-trust-creators-listeners/
