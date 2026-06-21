# Claracle Intro / Outro Video Compositions

Max Headroom–style **intro** (10s) and **outro** (12s) bumpers for the Claracle
podcast, built with [HyperFrames](https://www.npmjs.com/package/hyperframes) and
a WebGL [`webgl-max-headroom`](https://www.npmjs.com/package/webgl-max-headroom)
background, animated with [GSAP](https://gsap.com/).

Implements epic #314 (issues #315 environment, #316 intro, #317 outro).

| Composition | File | Duration | Resolution | FPS |
| ----------- | ---- | -------- | ---------- | --- |
| Intro | `compositions/intro.html` | 10s | 1920×1080 | 30 |
| Outro | `compositions/outro.html` | 12s | 1920×1080 | 30 |

## Layout

```
scripts/intro-outro/
├── index.html                 # project root composition (required by hyperframes)
├── compositions/
│   ├── intro.html             # 10s intro
│   └── outro.html             # 12s outro
├── assets/
│   ├── max-headroom-bg.esm.js # vendored WebGL background component
│   └── fonts/Orbitron.woff2   # vendored cyberpunk font (offline-safe)
├── render.sh                  # render both to output/*.mp4
├── output/                    # rendered MP4s (gitignored)
└── package.json
```

Assets (font + WebGL component) are **vendored** so renders are fully offline and
deterministic — the lint rules reject external Google-Fonts links, and the
deterministic capture path needs local assets.

## Setup

```bash
cd scripts/intro-outro
npm install
npx hyperframes doctor          # verify Chrome + FFmpeg + Node
```

The deterministic render path (smooth, reproducible WebGL animation) uses
`chrome-headless-shell`. `render.sh` installs it automatically on first run; to
install manually:

```bash
npx @puppeteer/browsers install chrome-headless-shell@stable --path "$PWD/chrome-headless-shell"
```

## Render

```bash
./render.sh           # both, high quality → output/intro.mp4, output/outro.mp4
./render.sh draft     # faster preview
```

Under the hood each composition renders with:

```bash
export HYPERFRAMES_BROWSER_PATH="$PWD/chrome-headless-shell/<ver>/chrome-headless-shell-linux64/chrome-headless-shell"
npx hyperframes render -c compositions/intro.html -o output/intro.mp4 --quality high --fps 30
```

> Without `HYPERFRAMES_BROWSER_PATH` set to chrome-headless-shell, hyperframes
> falls back to screenshot capture using system Chrome, which is **not
> deterministic** for the rAF-driven rotating cube.

## Preview / Lint

```bash
npx hyperframes preview        # interactive preview server
npx hyperframes lint .         # lint the whole project (pass the directory)
```

## Design notes

- **Background:** `<max-headroom-bg>` neon grid/cube. Intro `speed="0.5"`,
  `fisheye-strength="0.2"`; outro `speed="0.3"` (calmer).
- **Palette:** cyan `#00ffff`, magenta `#ff00ff`, yellow `#ffe14d` on dark.
- **Type:** Orbitron (variable, weights 400–900) via local `@font-face`.
- **Glitch/glow:** RGB-split via CSS `::before`/`::after` + `clip-path`
  keyframes; `neon-pulse` text-shadow. CSS animations run on hyperframes'
  virtual clock under deterministic capture.
- **Timing:** GSAP timelines are `gsap.timeline({ paused: true })` registered on
  `window.__timelines[<composition-id>]`; position params are absolute
  composition-time seconds. FPS is a CLI flag, not a meta attribute.

### Intro beats
- t=1s — "Claracle" title, neon-glow glitch entrance
- t=3s — "WEEKLY REPORT" subtitle fade-in
- t=5s — "with Clarabel & Joracle"
- t=7s — "AI-generated voice narration • Data is real" (small, lower area)

### Outro beats
Credits appear section-by-section (not scrolling): CREDITS → Director jmservera →
Hosts Clarabel & Joracle (AI-generated synthetic voices) → Music "Summer Sport"
by AudioCoffee, CC-BY-SA-3.0, audiocoffee.net | Promoted by chosic.com → Platform
Claracle www.claracle.com → "Follow us on Spotify". Credits fade, then a finale
"Claracle" neon-glow pulse with "This episode uses AI-generated voice narration".

## Upload to Azure (review copies)

```bash
az login   # if not already authenticated
az storage blob upload --account-name squadscopepo3f9a07d60de7 \
  --container-name podcaster-artifacts --name assets/video/intro.mp4 \
  --file output/intro.mp4 --overwrite --auth-mode login
az storage blob upload --account-name squadscopepo3f9a07d60de7 \
  --container-name podcaster-artifacts --name assets/video/outro.mp4 \
  --file output/outro.mp4 --overwrite --auth-mode login
```

## Regenerating with an AI assistant

These compositions are plain HTML/CSS/JS and are well suited to AI-assisted
iteration. Example prompt:

> Edit `scripts/intro-outro/compositions/intro.html` (a HyperFrames composition,
> 10s, 1920×1080, 30fps). Keep the `<max-headroom-bg>` WebGL background and the
> Orbitron `@font-face`. Maintain the composition contract: root `#root` with
> `data-composition-id`, `data-width`, `data-height`, `data-start="0"`,
> `data-duration`; timed elements use `class="clip"` with
> `data-start`/`data-duration`/`data-track-index`; build a paused GSAP timeline
> and register it on `window.__timelines["claracle-intro"]` with absolute
> composition-time positions. Do NOT add external font/CDN links (lint rejects
> them — vendor assets locally). After editing run `npx hyperframes lint .` (0
> errors) then `./render.sh draft` and inspect frames.
