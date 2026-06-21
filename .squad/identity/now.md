---
updated_at: 2026-06-20T21:27:41Z
focus_area: Video epic complete — board clear, ready for new work
active_issues: []
---

# What We're Focused On

The video epic is fully landed (PRs #303–#311 all merged to main). The Podcaster now produces synced screen-recording video episodes alongside audio. Recent wins:

- **Video pipeline** (`podcaster/video/`): visuals synced to audio boundaries, varied transitions, dynamic zoom/pan on page elements, end-credits sequence, and screen recordings synced to script segments.
- **ffmpeg drawtext** now detected at runtime and degrades gracefully when unavailable (#304).
- **SPOTIFY_CLIENT_ID** is configurable rather than hardcoded (#303).
- Offline integration smoke tests (tests/integration/) run with local fakes — no Docker, network, or credentials.

Baseline: ~867 test functions across tests/. Backend state: FSStorageProvider (local). Board is clear — no open issues or PRs. Team is napped, reskilled, and ready for the next milestone.

Updated by coordinator at session start.
