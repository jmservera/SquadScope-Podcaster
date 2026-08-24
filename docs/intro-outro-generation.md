# Intro & Outro Video Generation

## Overview

The Claracle podcast uses pre-generated **intro** and **outro** videos that are
composed into every episode by the video pipeline. They are rendered once,
stored in Azure Blob Storage, and reused across all episodes — the pipeline
downloads and caches them locally, then prepends the intro and appends the outro
around the episode content (see `podcaster/video/video_compose.py`). A reusable
**intermission** clip is also provided for section breaks during video
production.

- **Intro:** 18 seconds — Max Headroom animated WebGL background + "Claracle
  Weekly Report" title.
- **Outro:** 20 seconds — credits display over the same Max Headroom background.
- **Intermission:** 10 seconds — background-only section-break asset (no text).

> **Fades are not baked into these clips.** Fade-in / fade-out and cross-fades
> are applied by the `video_compose` pipeline. The intro and outro each hold
> ~6 seconds of background-only animation after their text beats finish so the
> pipeline has room to cross-fade; the intermission is background-only.

## Architecture

| Layer | Component |
| ----- | --------- |
| Background | [`webgl-max-headroom`](https://www.npmjs.com/package/webgl-max-headroom) — WebGL neon rotating-cube Max Headroom effect |
| Animation | [GSAP](https://gsap.com/) timelines (`window.__timelines`) |
| Composition / render | [HyperFrames](https://www.npmjs.com/package/hyperframes) — deterministic HTML-to-MP4 renderer |
| Storage | Azure Blob Storage — `assets/video/intro.mp4`, `assets/video/outro.mp4`, and `assets/video/intermission.mp4` in the `podcaster-artifacts` container |
| Pipeline join | `compose_video()` in `podcaster/video/video_compose.py` (concat demuxer) |

Source compositions live in [`scripts/intro-outro/`](../scripts/intro-outro):

```
scripts/intro-outro/
├── index.html                 # project root composition (required by HyperFrames)
├── compositions/
│   ├── intro.html             # 18s intro
│   ├── outro.html             # 20s outro
│   └── intermission.html      # 10s background-only section break
├── assets/
│   ├── fonts/
│   │   └── Orbitron.woff2     # title/credits typeface (vendored locally)
│   └── max-headroom-bg.esm.js # vendored WebGL Max Headroom background module
├── output/                    # render.sh writes intro/outro/intermission.mp4 here
├── render.sh                  # render all compositions to output/*.mp4
└── package.json               # hyperframes, gsap, webgl-max-headroom
```

## Assets

Everything the compositions need is vendored **locally** under
`scripts/intro-outro/assets/` so the render is deterministic and the HyperFrames
linter (which rejects external font/CDN links) passes.

| Asset | Path | Purpose |
| ----- | ---- | ------- |
| Font | `assets/fonts/Orbitron.woff2` | Geometric/tech typeface for the title and credits, loaded via a local `@font-face` |
| Background | `assets/max-headroom-bg.esm.js` (npm `webgl-max-headroom`) | WebGL neon rotating-cube "Max Headroom" background rendered into `<max-headroom-bg>` |
| Animation runtime | `gsap` (npm) | Drives the paused timelines registered on `window.__timelines[...]` that HyperFrames steps frame-by-frame |
| Chrome | `chrome-headless-shell/` (auto-installed) | Deterministic virtual-clock frame capture; installed by `render.sh` on first run |

> **No audio is baked into the clips.** The intro/outro/intermission MP4s are
> **silent** — the outro only *displays* the music/license credit text. The
> podcast MP3 (including any background music mix) is overlaid as the sole audio
> track by the pipeline (`_build_audio_overlay_cmd`), and the pipeline strips
> whatever audio the clips carry during canonicalisation. So no `.mp3`/`.wav`
> clip assets are required to regenerate the videos.

## Prerequisites

- **Node.js >= 22**
- **FFmpeg** installed and on `PATH`
- **Chrome / Chromium** — `chrome-headless-shell` is auto-managed by
  `render.sh` (installed via `@puppeteer/browsers` on first run) for
  deterministic virtual-clock frame capture.

## Quick Start

```bash
cd scripts/intro-outro
npm install
npx hyperframes doctor          # verify Node + FFmpeg + Chrome environment
npx hyperframes preview         # interactive live preview in the browser
./render.sh                     # render intro/outro/intermission into output/
```

`render.sh draft` produces a faster, lower-quality preview render.

## Regenerating

After editing a composition, re-render and re-upload:

```bash
cd scripts/intro-outro

# 1. Preview changes live
npx hyperframes preview

# 2. Lint (must report 0 errors — no external font/CDN links allowed)
npx hyperframes lint .

# 3. Render to MP4 (all clips, 1920×1080, 30fps)
./render.sh
# …or render a single composition explicitly:
npx hyperframes render -c compositions/intro.html -o output/intro.mp4 --quality high --fps 30
npx hyperframes render -c compositions/outro.html -o output/outro.mp4 --quality high --fps 30
npx hyperframes render -c compositions/intermission.html -o output/intermission.mp4 --quality high --fps 30

# 4. Upload to Azure Blob Storage (the pipeline reads these blobs)
az login   # if not already authenticated
az storage blob upload --account-name squadscopepo3f9a07d60de7 \
  --container-name podcaster-artifacts --name assets/video/intro.mp4 \
  --file output/intro.mp4 --overwrite --auth-mode login
az storage blob upload --account-name squadscopepo3f9a07d60de7 \
  --container-name podcaster-artifacts --name assets/video/outro.mp4 \
  --file output/outro.mp4 --overwrite --auth-mode login
az storage blob upload --account-name squadscopepo3f9a07d60de7 \
  --container-name podcaster-artifacts --name assets/video/intermission.mp4 \
  --file output/intermission.mp4 --overwrite --auth-mode login
```

> The video pipeline caches the downloaded clips locally
> (`<tempdir>/podcaster-intro-outro-cache/`). After re-uploading new versions,
> clear that cache on the worker (or it will reuse the previously downloaded
> clips until the cache entry is removed).

### Automatic seeding from the pipeline (#586)

Instead of (or in addition to) the manual `az storage blob upload` above, the
pipeline can seed the **branded** clips into the blobs itself so a stale title
card can never hide them. Before composing, `run_video_generation()` calls
`ensure_branded_intro_outro(storage)`
(`podcaster/video/intro_outro.py`), which uploads `intro.mp4` / `outro.mp4` to
`INTRO_BLOB_PATH` / `OUTRO_BLOB_PATH` and clears the local fetch cache.

It reads the clips from the directory given by
`PODCASTER_INTRO_OUTRO_ASSET_DIR` (default `scripts/intro-outro/output`). Stage
the rendered branded clips there on the worker and the pipeline keeps the blobs
in sync on every render. When the directory (or either clip) is absent it is a
graceful no-op — the pipeline falls back to whatever clips are already stored.

## Storage in Azure Blob Storage

The clips live in the **`podcaster-artifacts`** container under the `assets/video/`
prefix:

| Clip | Blob path | Code constant |
| ---- | --------- | ------------- |
| Intro | `assets/video/intro.mp4` | `INTRO_BLOB_PATH` |
| Outro | `assets/video/outro.mp4` | `OUTRO_BLOB_PATH` |
| Intermission | `assets/video/intermission.mp4` | _(rendered/uploaded but not yet fetched by the pipeline — see below)_ |

The storage backend is resolved by `create_storage_backend()` in
`podcaster/storage.py` from these environment variables:

| Variable | Meaning | Default |
| -------- | ------- | ------- |
| `PODCASTER_STORAGE_ACCOUNT_URL` | Blob account URL — when set, the Azure backend is used | _(unset → local stub backend)_ |
| `PODCASTER_STORAGE_CONTAINER` | Container name | `podcaster-artifacts` |
| `PODCASTER_LOCAL_STORAGE_PATH` | Root dir for the local stub backend (when no account URL) | `.podcaster-artifacts` |

Azure auth uses managed identity (`az login --identity` on the worker). The
`--account-name` in the upload commands above must match the account behind
`PODCASTER_STORAGE_ACCOUNT_URL`.

## How the pipeline composes them (ffmpeg)

All composition lives in `compose_video()` in
`podcaster/video/video_compose.py`. The intro/outro are joined around the
already-composed content with the **concat demuxer** (a stream copy, not an
`xfade` re-encode) for speed and quality:

1. **Fetch + cache** — `_fetch_intro_outro()` downloads `intro.mp4`/`outro.mp4`
   via `storage.get_bytes()` into `<tempdir>/podcaster-intro-outro-cache/`,
   reusing the cache on subsequent runs. Missing blobs return `None` and are
   skipped (graceful degradation — the episode still composes without bumpers).
2. **(Intro only) DOG watermark** — when a `DogLogoConfig` is supplied,
   `_build_intro_dog_cmd()` overlays the logo on the **final
   `DOG_INTRO_LEAD_SECONDS` (3 s)** of the intro via
   `overlay=…:enable='gte(t,<start>)'`, so it is on screen before the
   intro→content join. The outro stays unbranded.
3. **Canonicalise every clip** — `_build_canonical_av_cmd()` re-encodes intro,
   content, and outro to an identical layout so the concat copy succeeds:

   ```text
   ffmpeg -i <clip> \
     -filter_complex "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,\
   pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p,setsar=1[v]" \
     -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 \
     -map "[v]" -map 1:a \
     -c:v libx264 ... -colorspace bt709 -color_trc bt709 -color_primaries bt709 \
     -color_range tv -c:a aac -b:a 192k -ar 48000 -ac 2 -shortest <canon>.mp4
   ```

   Source audio on the clips is always stripped; a silent stereo track is
   synthesised (`anullsrc`) so every joined clip has a uniform A/V layout.
4. **Concat join** — `_build_concat_cmd()` writes a `concat.txt` listing
   `intro → content → outro` and stream-copies them together:

   ```text
   ffmpeg -f concat -safe 0 -i concat.txt -c copy -movflags +faststart joined.mp4
   ```

5. **Overlay the podcast audio** — `_build_audio_overlay_cmd()` maps the podcast
   MP3 as the **sole** audio track (re-encoded to AAC, never `-shortest`, so the
   outro audio always plays in full). If the audio outlasts the video the final
   frame is held with `tpad=stop_mode=clone` and faded to black over the last
   `OUTRO_VIDEO_FADE_SECONDS` (2 s); if the video is longer, the audio is padded
   with trailing silence (`apad`) to satisfy Spotify's duration check.
6. **Normalise colour metadata** — `_build_h264_metadata_cmd()` runs a final
   stream-copy pass with the `h264_metadata`/`hevc_metadata` bitstream filter to
   force consistent BT.709 VUI flags across the independently-encoded clips
   (avoids Spotify's `INCONSISTENT_COLOR_DETAILS`).

> **Fit-to-window (#355):** when `audio_duration` is known, the content segments
> are trimmed/freeze-extended to fill exactly
> `audio_duration − intro − outro`, so the bumpers always play in full while the
> total runtime matches the audio.

Canonical output format: **1920×1080, 30 fps, `yuv420p`, BT.709**, stereo AAC
@ 192 kbit/s, 48 kHz (overridable via the `VIDEO_ENCODE_*` env vars).

## How the video pipeline references the clips

`compose_video()` only fetches the bumpers when a `storage` backend is passed:

```python
intro_path: Path | None = None
outro_path: Path | None = None
if storage is not None:
    cache_dir = intro_outro_cache_dir or _default_intro_outro_cache_dir()
    intro_path, outro_path = _fetch_intro_outro(storage, cache_dir)
...
_join_intro_outro(content_path, joined_path, intro_path, outro_path, ...)
```

Key reference points in `podcaster/video/video_compose.py`:

- `INTRO_BLOB_PATH` / `OUTRO_BLOB_PATH` — the blob paths fetched per episode.
- `_fetch_intro_outro()` / `_fetch_blob_cached()` — download + on-disk cache,
  returning `None` for missing blobs (graceful degradation).
- `_join_intro_outro()` — canonicalise + concat the clips around the content.
- `_default_intro_outro_cache_dir()` — `<tempdir>/podcaster-intro-outro-cache/`.

> **Intermission:** `intermission.mp4` is rendered and uploaded for future use as
> a section-break asset, but the current pipeline only fetches the intro and
> outro (there is no `INTERMISSION_BLOB_PATH` reference yet). Wiring it into the
> segment loop is a separate change.

## AI-Assisted Modification

The compositions are plain HTML/CSS/JS and are well suited to AI-assisted
iteration. Keep the `<max-headroom-bg>` WebGL background, the local Orbitron
`@font-face`, and the HyperFrames composition contract (root `#root` with
`data-composition-id` / `data-width` / `data-height` / `data-start` /
`data-duration`; timed elements use `class="clip"` with
`data-start`/`data-duration`/`data-track-index`; a paused GSAP timeline
registered on `window.__timelines["…"]`). Do **not** add external font/CDN links
— the linter rejects them; vendor assets locally.

### Example Prompt (modifying the intro)

> Using the HyperFrames composition at
> `scripts/intro-outro/compositions/intro.html` (18s, 1920×1080, 30fps), modify
> the intro to:
> - Change the title animation to use a glitch-RGB effect instead of neon glow
> - Add a particle burst when "Claracle" appears
> - Speed up the background rotation to 0.7
> - Keep the same timing structure (18 seconds total)
>
> Preview with `npx hyperframes preview`, lint with `npx hyperframes lint .`,
> then render with
> `npx hyperframes render -c compositions/intro.html -o output/intro.mp4 --quality high --fps 30`.

### Example Prompt (modifying the outro)

> Using the HyperFrames composition at
> `scripts/intro-outro/compositions/outro.html` (20s, 1920×1080, 30fps), update
> the outro credits:
> - Add a new credit line: "Special thanks: [name]"
> - Change the Spotify CTA to include YouTube
> - Make the credits animate with a matrix-decode reveal effect
> - Keep the existing credits (director, hosts, music license) intact
>
> Preview with `npx hyperframes preview`, lint with `npx hyperframes lint .`,
> then render with
> `npx hyperframes render -c compositions/outro.html -o output/outro.mp4 --quality high --fps 30`.

## Credits (must appear in the outro)

| Role | Credit |
| ---- | ------ |
| Director | jmservera |
| Hosts | Clarabel & Joracle (AI-generated synthetic voices) |
| Music | Claracle Theme |
| License | Original composition — Copyright © jmservera |
| Platform | Claracle — www.claracle.com |

The outro must also disclose AI-generated voice narration (e.g. "This episode
uses AI-generated voice narration").

## Section title cards (issue #377)

Between the editorial sections of an episode (Trends, Industry, Signal & Noise,
Blind Spots, …) the pipeline can splice brief **section title cards** — a dark
card with the section name in large white text and a brand-accent rule, rendered
the same ffmpeg-native way as the intro/outro bumpers (`color` + `drawtext` +
`fade`). Cards play for ~2.5 s with 0.5 s fade-in/out.

Module: `podcaster/video/section_cards.py`.

- **Detection (`parse_sections`)** — recognises markdown headings (`## Trends`),
  bracketed markers (`[SECTION: Signal & Noise]`) and bold/emoji standalone
  lines naming a known editorial section (`**Blind Spots**`, `📡 Signal &
  Noise`). Only the script body (after the `---` header) is scanned and dialogue
  lines (`Name: …`) are ignored.
- **Mapping (`plan_section_card_inserts`)** — each section is tied to the content
  segment that opens it, matched by the first GitHub repo URL after the header.
- **Rendering (`generate_section_card`)** — known sections carry an emoji and an
  accent colour (Trends 🔥 orange, Industry 🏭 blue, Signal & Noise 📡 green,
  Blind Spots 🫣 purple); unknown markdown headings use the default Claracle blue.
- **Compositing** — `compose_video(..., section_cards=...)` reserves the card time
  inside the fit-to-window budget (so total video length still matches the audio
  timeline), splices the cards with fade transitions, and time-shifts each
  segment's lower-third so overlays fire over their own segment, not the card.

**Fully graceful & dormant by default:** current scripts are plain dialogue with
no section headers, so `parse_sections` returns nothing and composition is
unchanged. The feature activates automatically once scripts include section
markers. Disable explicitly with `VIDEO_SECTION_CARDS=0`.

## Design Principles

- **Max Headroom aesthetic:** neon colors, a rotating cube/WebGL background,
  glitch effects — retro-futuristic 80s cyberpunk.
- **Clarabel & Joracle are Max Headroom-like personas:** synthetic, digital,
  energetic.
- **Font:** Orbitron (or a similar geometric/tech typeface), vendored locally.
- **Color palette:** `#00ffff` (cyan), `#ff00ff` (magenta), `#ffff00` (yellow)
  over a dark background.
