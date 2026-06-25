# Architecture

## Overview
Config-driven podcast generation engine. Receives article content and editorial configuration via API from any caller.

## Tech Stack
- Python >=3.11
- Azure Container Apps (synthesis job + API app)
- Azure OpenAI (TTS: gpt-4o-mini-tts, Chat: gpt-4o-mini)
- Azure Blob Storage + Storage Queue
- Bicep IaC (infra/)
- GitHub Actions CI/CD
- ffmpeg for audio mixing

## Directory Structure
- podcaster/ — Core application modules
  - api.py — HTTP API (POST /api/generate, GET /healthz)
  - job_runner.py — ACA queue consumer, runs synthesis
  - jobs.py — Job orchestration, budget checks, artifact staging
  - config.py — API payload config parsing (PodcastConfig, ScriptDirections, MusicMixConfig)
  - script_gen.py — LLM script generation
  - hooks.py — LLM-generated conversational hooks from host personalities
  - episode.py — Episode assembly + synthesis orchestration
  - audio.py — ffmpeg audio stitching, music mixing, loudness normalization
  - music.py — Bundled music asset registry
  - tts.py — Azure OpenAI TTS client
  - queue.py — Azure Storage Queue client
  - storage.py — Blob/local storage abstraction
  - validation.py — Request auth + validation
  - publish.py — Spotify for Creators auto-publish (draft mode, scheduling, metadata)
  - costs.py — Monthly budget/ledger guardrails
  - sanitization.py — Prompt injection neutralization
  - claim_extraction.py — Claim ledger from articles
- infra/ — Bicep templates
  - main.bicep — Main deployment orchestrator
  - modules/aca.bicep — Container App synthesis job
  - modules/api.bicep — Container App API
  - modules/openai.bicep — Azure OpenAI provisioning
  - modules/acr.bicep — Container Registry
- tests/ — pytest suite (comprehensive)
- scripts/ — Helper/dev scripts
- assets/music/ — Bundled audio bed (summer-sport.mp3)
- docs/ — Integration contract, operations docs

## Pipeline Flow
1. Caller sends POST /api/generate with article + config
2. validation.py authenticates and validates request
3. jobs.py stages artifacts, runs budget checks, generates script via LLM
4. hooks.py generates personality-matched conversational hooks via LLM
5. Job enqueued to Azure Storage Queue
6. job_runner.py dequeues, runs TTS synthesis per turn via Azure OpenAI
7. audio.py stitches segments + mixes intro/outro music (ffmpeg, eval=frame volume expressions)
8. Validation pass (duration, file size, format checks)
9. Artifacts stored to Azure Blob Storage
10. publish.py auto-publishes to Spotify (opt-in, non-blocking on failure)

## API Contract
### Config Contract (received via API payload)
The caller provides config in the API payload. See dataclass definitions below:
- PodcastConfig: name, url, spoken_site, ai_voice_disclosure, host_a, host_b (name/voice/style), style_guide
- ScriptDirections: episode_style (format/tone/segment_order), show_intro, cold_open, ai_disclosure_cue, corrections_path
- MusicMixConfig: track, intro params (full_volume_seconds, pre_voice_fade, etc.), outro params (start_position, fade_in, play_to_end)

### Handoff Payload (received from caller)
Required: week, article_url
Optional: article_content, article_title, article_sha256, source_artifacts, podcast_config, script_directions, music_mix, dry_run, force, callback

### Response Contract
Returns: job_id, status, manifest_url, mp3_url, transcript_url, show_notes_url, warnings, errors

## Audio Mixing Architecture
- Music track: assets/music/summer-sport.mp3 (105s, used for both intro and outro)
- Intro: Full vol 0-8s → fade to 10% → duck under speech → fade to 0% after intros
- Outro: atrim from 75s, fade in 0%→10% under farewell → ramp to 100% after voices end
- CRITICAL: eval=frame on all volume filters with time expressions
- Voice guardrail: music NEVER exceeds 10% when voice is playing
- 2-input amix chain (not N-input) to avoid amplitude dilution

## Azure Infrastructure
- Resource Group: squadscope-podcaster
- Container App: podcaster-yqabcnkm2junu-api
- OpenAI: podcaster-yqabcnkm2junu-openai (deployments: tts, chat)
- Storage: squadscopepo3f9a07d60de7
- Auth: Managed Identity (DefaultAzureCredential)

## Environment Variables
Required:
- AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_TTS_DEPLOYMENT, AZURE_OPENAI_CHAT_DEPLOYMENT
- AZURE_OPENAI_AUTH_MODE (managed_identity)
- PODCASTER_API_KEY
- SPOTIFY_SHOW_ID (for publish module)
Optional:
- AZURE_OPENAI_TTS_VOICE_HOST_A/B, AZURE_OPENAI_TTS_STYLE_HOST_A/B
- PODCASTER_STORAGE_ACCOUNT_URL, PODCASTER_STORAGE_QUEUE_URL
- PODCASTER_VIDEO_SCRATCH_CONTAINER (video pipeline intermediates container; enables blob checkpoint/resume, #410)
- SPOTIFY_PUBLISH_ENABLED, SPOTIFY_PUBLISH_DRY_RUN, SP_DC, SP_KEY

## Video Pipeline Intermediates (checkpoint/resume, #410)
- The video job stores all intermediates (segment recordings, normalized clips,
  composed video) in the `video-scratch` blob container under
  `video-jobs/{job-id}/intermediates/` instead of local /tmp.
- Each stage checks blob for its output and resumes from the last checkpoint on
  restart; local disk only holds the file currently being processed.
- Intermediates are deleted on successful publish; a 7-day storage lifecycle
  policy reclaims any abandoned scratch blobs.
- Disabled automatically when `PODCASTER_VIDEO_SCRATCH_CONTAINER` is unset
  (local dev / tests fall back to the legacy all-local-disk path).

## Key Commands
- Run tests: pytest tests/ -q
- Local pipeline: python scripts/run_full_pipeline.py
- Start API: python -m podcaster.api
- Deploy: .github/workflows/deploy-azure.yml
