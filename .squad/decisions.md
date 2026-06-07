# Decisions

- Podcaster is a sister project and must not change SquadScope article publishing.
- Initial public distribution is manual; Spotify/podcast-host automation remains research.
- SquadScope integration is link-only and does not host or embed audio.
- The API key lives in GitHub/Azure secrets and must not be logged.
- Stub responses keep the final response shape stable while generation is implemented.

### 2026-06-07T18:26:33.954+00:00: Security handoff review (hermes)

**By:** Hermes

**What:** 
- Auth header is `x-podcaster-api-key`
- Podcaster deploy secret/app setting is `PODCASTER_API_KEY`
- Optional cross-repo sync token is `SQUADSCOPE_SYNC_TOKEN`
- SquadScope receives variable `PODCASTER_ENDPOINT` and secret `PODCASTER_API_KEY`
- Reviewed function_app.py, podcaster/validation.py, deploy workflow, CI workflow, bicep infra, integration docs, README, and sample local settings
- No secret values were recorded in outputs
- No release-blocking secret echo path found in API responses or workflow summaries

**Why:**
- Ensure secrets are properly handled in deploy workflow and application code
- Verify cross-repo sync token requirements and fallback behavior
- Confirm no sensitive data is leaked in logs or responses
- Residual operational gate: auto-sync is optional and silently skips when SQUADSCOPE_SYNC_TOKEN is missing
- Handoff must verify SquadScope variable/secret presence before relying on automation
