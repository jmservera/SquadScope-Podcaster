# How the Podcast Generation Engine Works

This project turns a published article into a reviewable podcast package. A caller sends an article URL, optional article text, and episode configuration to `POST /api/generate`. The service validates the request, stages job artifacts, generates or prepares a two-host script, synthesizes speech, mixes in music, validates the final audio, and writes the resulting assets back to storage.

The design is intentionally platform-agnostic. The engine does not depend on any particular upstream product. Its contract is simple: the caller provides article inputs plus configuration, and the engine returns a podcast episode artifact set that can be reviewed, distributed, or optionally published downstream.

## Level 1: High-Level Overview

### What the project does

At a high level, the engine converts source material into a conversational podcast episode. Instead of reading an article aloud, it creates a two-host discussion about the article's important points, synthesizes each line with distinct voices, and assembles a polished MP3 with intro/outro music, loudness normalization, and validation metadata.

It also produces the supporting package around the audio: transcript, show notes, manifest, review metadata, checksums, and publishing-oriented files. That makes the output useful not only for listening, but also for editorial review, traceability, and downstream publishing workflows.

### Simple flow

```text
Caller
  |
  | POST /api/generate
  v
API validation + auth
  |
  | stage manifest/script/artifacts
  v
Async synthesis job
  |
  +--> script generation
  +--> per-turn TTS
  +--> audio stitching
  +--> music mixing
  +--> loudness normalization
  +--> validation
  v
Storage + manifest update
  |
  +--> final MP3
  +--> transcript
  +--> show notes
  +--> manifest / packet metadata
  v
Optional downstream publishing
```

### Inputs and outputs

**What goes in**

- Required: `week`, `article_url`
- Common optional inputs:
  - `article_content` for full LLM script generation
  - `podcast_config` for show identity, hosts, voices, and style
  - `script_directions` for format/tone/opening cues
  - `music_mix` for intro/outro timing behavior
  - `dry_run`, `force`, `cost_override`, `callback`

**What comes out**

- Final mixed MP3 episode
- Episode metadata and manifest
- Transcript
- Show notes
- Publishing packet / review artifacts

---

## Level 2: Technical Deep-Dive

### 1. API entry point

The HTTP front door lives in `podcaster/api.py`.

- Endpoint: `POST /api/generate`
- Health check: `GET /healthz`
- Auth header: `x-podcaster-api-key`
- Auth check: constant-time compare via `hmac.compare_digest`

Required payload fields:

- `week`
- `article_url`

Optional fields accepted by the contract include:

- `article_content`
- `podcast_config`
- `dry_run`
- `force`
- `cost_override`
- `callback`

Normal production requests return **`202 Accepted`** and stage the job for asynchronous synthesis. A dry run returns a staged response without authorizing real synthesis. The API's job is intentionally small: authenticate, validate, stage, enqueue, and return stable artifact URLs quickly.

Notes:

- `cost_override` is an explicit operator escape hatch around budget guardrails.
- `callback` is accepted by the request contract, but the current pipeline does not invoke it yet.

Why this design: it keeps the HTTP path fast and predictable while moving the expensive creative/audio work into an async worker.

### 2. Configuration system

The configuration layer lives primarily in `podcaster/config.py`.

The intended model is payload-driven: the caller can supply episode identity and creative settings in the request instead of baking them into code. The engine also keeps fallback defaults so local/dev runs still work when the caller omits config, but the request payload is the main source of truth.

Core dataclasses:

- `PodcastConfig`
- `HostConfig`
- `ScriptDirections`
- `EpisodeStyle`
- `MusicMixConfig`

#### Host definition

Each host is defined by:

- `name`
- `voice`
- `style`

That split matters because the system uses:

- **name** for script labels
- **voice** for TTS routing
- **style** for both prompt personality and optional speech instructions

By default, the engine expects a deliberate contrast:

- **Host A**: bright, enthusiastic, high-energy
- **Host B**: calm, dry, analytical

That contrast is not cosmetic; it is what makes the conversation sound like discussion instead of duplicated narration.

#### Episode style and script directions

`EpisodeStyle` and `ScriptDirections` shape format without changing code:

- show intro
- cold open
- tone
- target format / length expectations
- segment order
- source article link / closing cues

These settings are injected into the LLM prompt so the same engine can produce different episode structures from request to request.

#### Music mix configuration

`MusicMixConfig` controls the intro/outro bed:

- intro full-volume timing
- duck-under behavior
- outro start offset
- outro fade-up timing
- whether the outro plays through

Why this design: music is part of the episode format, not a hardcoded post-effect. Treating it as config keeps the engine reusable across different shows and formats.

### 3. Script generation (the creative core)

The script generation path lives in `podcaster/script_gen.py`.

When `article_content` is present and the Azure OpenAI chat deployment is configured, the engine asks an LLM to write a **two-host conversation**, not a spoken article summary.

The system prompt enforces several hard rules:

- two distinct host personalities
- exact line format: `HostName: text`
- AI disclosure within the first 3 exchanges
- no headers or separators in the raw LLM dialogue
- no stage directions, sound effects, or bracketed cues
- hosts discuss the article; they do not read it verbatim

The creative goal is deliberate:

- **Host A** brings energy and momentum
- **Host B** brings calm analysis and dry counterweight

That contrast produces a more natural rhythm: setup, reaction, skepticism, payoff.

`ScriptDirections` then layers in caller-specific constraints:

- `show_intro`
- `cold_open`
- `tone`
- target format
- segment order

Before the article reaches the model, it is sanitized as untrusted input and truncated to fit the context budget. The main limit is `MAX_ARTICLE_CHARS = 12000`, which keeps the prompt inside a practical window while still giving the model enough substance to react to. The generated dialogue is also length-capped before formatting.

The final output is a structured script:

```text
metadata header
---
Host A: ...
Host B: ...
```

Why this design: a strict line-based format makes the script easy to parse into speaker turns for downstream TTS.

### 4. Hook generation

The hook subsystem lives in `podcaster/hooks.py`.

It asks the LLM for **10 short lead-in phrases per host personality**. If that fails, the engine falls back to a generic built-in phrase set.

When the structured episode-builder path uses hooks, they are:

- generated once per episode
- shuffled for variety
- injected into segment openings

This prevents every segment from sounding templated while still keeping openings brief and parseable.

Why this design: very small phrasing changes make repeated episodes feel less robotic without adding much complexity or cost.

### 5. TTS voice synthesis

The TTS layer lives in `podcaster/tts.py`.

It uses the Azure OpenAI speech endpoint, typically backed by a deployment such as `gpt-4o-mini-tts`, and synthesizes **each dialogue turn independently**.

Key behaviors:

- two distinct voices, one per host
- voices are request-configurable
- optional per-host style instructions reinforce personality
- if style instructions are rejected by the model, the call retries without them

Typical mapping:

- Host A -> a more upbeat voice
- Host B -> a calmer voice

The engine builds a voice plan from parsed dialogue lines, then synthesizes turn-by-turn MP3 segments. That turn-level segmentation matters because it preserves speaker boundaries and makes later assembly predictable.

#### Synthesis gating

The strict production gate is fail-closed:

- production config must be present
- request must not be `dry_run`
- review must be approved

The queue runner also supports a narrower **review-artifact** path: it can render private audio for operator review while still keeping publication eligibility blocked. Either way, the system is designed so speech synthesis never happens accidentally.

### 6. Audio assembly and music mixing

This logic lives in `podcaster/audio.py` and is the most technically dense part of the system.

#### Concatenation

Each synthesized turn arrives as its own MP3 blob. The engine:

1. writes each turn to a temporary segment file
2. resamples every segment to **mono 44.1 kHz**
3. inserts short silence gaps between turns (default `0.35s`)
4. concatenates the timeline with an `ffmpeg` concat filter

Internally, the concat filter uses:

- `aresample=44100`
- mono channel formatting
- generated silence via `aevalsrc`

Why this design: synthesizing per turn keeps speakers discrete; concatenating after resampling guarantees a uniform downstream format.

#### Music mixing: the differentiator

The bundled music source is:

- `assets/music/summer-sport.mp3`
- license: **CC BY-SA 3.0**

The engine uses the same track for intro and outro, but not as simple prepend/append audio. In mix mode, it builds a timeline envelope around speech.

##### Intro behavior

The intro track:

1. plays at full volume for the opening window
2. begins fading down just before speech starts
3. drops to **10% gain under speech**
4. stays under the first configured speech segments
5. fades to silence after the intro section completes

In practice, the default timeline is:

- full music up front
- speech starts after the intro window
- music ducks under the first few spoken turns
- music exits cleanly instead of hard-cutting

##### Outro behavior

The outro track:

1. starts later in the source song (trimmed by configured offset)
2. fades in from zero underneath the closing dialogue
3. remains capped at ducked level while voices are still present
4. ramps toward full volume only **after** speech finishes
5. can continue playing after the final spoken line

##### Critical intelligibility guardrail

When voice is present, music is never allowed to overpower it. The core guardrail is:

- **music stays at or below 10% gain while speech is active**

That guardrail exists in both intro and outro envelopes and is the reason the mix remains podcast-first instead of music-first.

##### ffmpeg details that matter

Two implementation choices are especially important:

- the mix uses `amix=inputs=2:normalize=0`
- time-varying volume filters use `eval=frame`

Why they matter:

- `inputs=2` keeps the mix focused on exactly speech + one music bed at a time
- `normalize=0` avoids automatic amplitude dilution that would flatten the mix
- `eval=frame` is required so volume expressions react continuously over time instead of being treated as static values

Without those choices, the ducking envelope would be much less predictable.

#### Two-pass loudnorm

After speech/music assembly, the engine normalizes loudness in **two passes**:

**Pass 1: measure**

```text
loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json
```

This pass extracts measured values such as input integrated loudness and true peak.

**Pass 2: apply**

Those measured values are fed back into a second `loudnorm` run so the output lands consistently near the target.

Target:

- **-16 LUFS**

Why this design: single-pass normalization is good enough for rough output; two-pass normalization is better for repeatable podcast loudness.

#### Validation

After normalization, the engine validates the finished MP3 with `ffprobe` and `ebur128`.

Checks include:

- content type: MP3
- channels: mono
- sample rate: `44.1 kHz`
- bitrate: `64-96 kbps`
- loudness: near `-16 LUFS`
- duration: `< 10 minutes` unless manually overridden
- file size: `< 10 MB`

`ffprobe` verifies the file's technical metadata. `ebur128` measures actual loudness in LUFS. The result is stored as structured validation metadata and written back to the manifest.

Why this design: validation is the last protection against bad renders, broken ffmpeg runs, or audio that is technically playable but not publication-ready.

### 7. Spotify publishing (optional)

Optional Spotify publishing lives in `podcaster/publish.py`.

It is opt-in:

- `SPOTIFY_PUBLISH_ENABLED=true`

The repository includes the open-source `spotifyconnector` dependency, and the current publishing module implements the same unofficial Spotify for Creators flow by exchanging browser cookies for a short-lived Bearer token against Spotify Accounts, then calling the internal `api-v5.anchor.fm` REST API:

- `SP_DC`
- `SP_KEY`

The publishing flow is:

1. resolve legacy IDs
2. create draft episode
3. get signed GCS upload URL
4. upload MP3
5. trigger processing and poll
6. set metadata and publish/schedule

Most importantly, publishing is **non-fatal**. If publishing is disabled, misconfigured, or fails, episode generation still succeeds. The module returns a `PublishResult` instead of throwing pipeline-breaking exceptions.

Why this design: podcast generation is the core product; distribution integrations should degrade gracefully.

### 8. Environment variables reference

#### Core generation

| Variable | Required | Purpose |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | Yes | Base Azure OpenAI endpoint for chat + speech |
| `AZURE_OPENAI_TTS_DEPLOYMENT` | Yes | Speech deployment name |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | Yes for LLM script generation | Chat deployment name |
| `AZURE_OPENAI_TTS_VOICE_HOST_A` | Yes | Voice for host A |
| `AZURE_OPENAI_TTS_VOICE_HOST_B` | Yes | Voice for host B |
| `AZURE_OPENAI_TTS_STYLE_HOST_A` | Optional | Style instructions for host A speech |
| `AZURE_OPENAI_TTS_STYLE_HOST_B` | Optional | Style instructions for host B speech |
| `AZURE_OPENAI_AUTH_MODE` | Yes | Auth mode; production expects managed identity |
| `PODCASTER_API_KEY` | Yes | Shared secret for `x-podcaster-api-key` auth |

#### Optional Spotify publishing

| Variable | Required | Purpose |
|---|---|---|
| `SPOTIFY_PUBLISH_ENABLED` | Optional, default `false` | Enables publishing module |
| `SPOTIFY_SHOW_ID` | Required if publishing enabled | Target Spotify show |
| `SP_DC` | Required if publishing enabled | Spotify browser session cookie |
| `SP_KEY` | Required if publishing enabled | Spotify browser session cookie |

#### Runtime note

Common runtime plumbing variables:

| Variable | Purpose |
|---|---|
| `PODCASTER_STORAGE_ACCOUNT_URL` | Blob account URL for staged artifacts |
| `PODCASTER_STORAGE_CONTAINER` | Blob container name |
| `PODCASTER_STORAGE_QUEUE_URL` | Queue URL consumed by the async synthesis job |
| `PODCASTER_LOCAL_ARTIFACT_DIR` | Local artifact staging directory for development |
| `PODCASTER_ARTIFACT_BASE_URL` | Base URL used when returning local/dev artifact locators |
| `PODCASTER_API_PORT` | Port for the HTTP API server |

Those are deployment/runtime plumbing. The earlier tables are the generation-specific settings that most directly shape episode behavior.

### 9. Output artifacts

The pipeline produces more than one file because audio generation alone is not enough for review and distribution.

Primary outputs:

- **Final MP3** — normalized, mixed, validated episode audio
- **Episode metadata** — job manifest, generation status, validation results, lifecycle state
- **Transcript** — speaker-labelled text for the episode
- **Show notes** — episode summary, source links, attribution, and AI disclosure
- **Publishing packet** — bundled operator-facing artifact set

Two manifest forms are important:

- staged job metadata: `jobs/<job_id>/manifest.json`
- packet copy: `MANIFEST.json` inside the ZIP packet

The manifest carries the audio validation result, artifact checksums, and synthesis status so downstream systems can tell the difference between:

- placeholder output
- synthesized review audio
- publication-ready audio

The show notes also carry two policy-critical disclosures:

- AI voice disclosure
- music/source attribution

Why this design: a trustworthy podcast pipeline needs provenance and review metadata, not just an MP3 blob.

---

## Summary

The engine is built around one idea: **generate a conversational, reviewable, technically consistent podcast episode from an article-plus-config request**. Its main strengths are payload-driven configuration, strict script formatting, turn-level two-voice synthesis, careful music ducking, two-pass loudness normalization, and explicit validation/manifest tracking all the way to the final artifact set.
