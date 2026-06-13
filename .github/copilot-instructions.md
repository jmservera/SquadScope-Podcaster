# Copilot Instructions for SquadScope-Podcaster

This repository uses the **Squad agent** as the default for all AI-assisted work.

## Default Agent
Always use `--agent squad` when running Copilot CLI on this repository.

## Repository Context
- **Project:** SquadScope-Podcaster — Config-driven podcast generation engine
- **Architecture:** See `architecture.md` in repo root
- **Squad team:** See `.squad/team.md` for current roster

## Key Conventions
- All application code is in `podcaster/` (Python >=3.11)
- Infrastructure is Bicep in `infra/`
- Config is received from SquadScope (never hardcoded in this repo)
- Host personalities, show intro, episode style — all from config, not code
- audio.py uses eval=frame on ALL ffmpeg volume filters with time expressions
- Music never exceeds 10% volume when voice is playing
- Tests: pytest tests/ must pass before merge
- CI must be correct, not just green

## Cross-Repo Impact
This repo CONSUMES config from SquadScope:
- SquadScope's `config/podcast.json` defines all editorial direction
- Changes to PodcastConfig/ScriptDirections/MusicMixConfig dataclasses in config.py must stay compatible with SquadScope's JSON
- If new config fields are needed, coordinate with SquadScope repo to add them to config/podcast.json

## Critical Technical Notes
- ffmpeg volume filter: ALWAYS use eval=frame with time-based expressions
- amix: use 2-input chain, not N-input (avoids amplitude dilution)
- Azure OpenAI auth: managed identity via az login --identity
- Upload type for Spotify: "default" (not "audio") for mp3 files
- ETag from GCS must be stripped of surrounding quotes

## Testing
- Run `pytest tests/ -q` (297+ tests)
- CI: `.github/workflows/ci.yml`
- Smoke: podcaster-handoff-smoke (from SquadScope repo)
