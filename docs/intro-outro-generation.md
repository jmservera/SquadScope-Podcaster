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
├── render.sh                  # render all compositions to output/*.mp4
└── package.json               # hyperframes, gsap, webgl-max-headroom
```

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
| Music | "Summer Sport" by AudioCoffee |
| License | CC-BY-SA-3.0 |
| Music link | https://www.audiocoffee.net/ |
| Promoted by | https://www.chosic.com/free-music/all/ |
| Platform | Claracle — www.claracle.com |

The outro must also disclose AI-generated voice narration (e.g. "This episode
uses AI-generated voice narration").

## Design Principles

- **Max Headroom aesthetic:** neon colors, a rotating cube/WebGL background,
  glitch effects — retro-futuristic 80s cyberpunk.
- **Clarabel & Joracle are Max Headroom-like personas:** synthetic, digital,
  energetic.
- **Font:** Orbitron (or a similar geometric/tech typeface), vendored locally.
- **Color palette:** `#00ffff` (cyan), `#ff00ff` (magenta), `#ffff00` (yellow)
  over a dark background.
