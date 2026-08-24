# Claracle Intro / Outro Video Compositions

Max Headroom–style **intro** (18s) and **outro** (20s) bumpers — plus a
reusable **intermission** (10s) section-break asset — for the Claracle
podcast, built with [HyperFrames](https://www.npmjs.com/package/hyperframes) and
a WebGL [`webgl-max-headroom`](https://www.npmjs.com/package/webgl-max-headroom)
background, animated with [GSAP](https://gsap.com/).

Implements epic #314 (issues #315 environment, #316 intro, #317 outro).

> **No fades in these compositions.** Fade-in / fade-out and cross-fades are
> applied later by the `video_compose` pipeline. The intro and outro each carry
> ~6 seconds of just-background animation after their text beats finish so the
> pipeline has room to cross-fade. The intermission is background-only.

| Composition | File | Duration | Resolution | FPS |
| ----------- | ---- | -------- | ---------- | --- |
| Intro | `compositions/intro.html` | 18s | 1920×1080 | 30 |
| Outro | `compositions/outro.html` | 20s | 1920×1080 | 30 |
| Intermission | `compositions/intermission.html` | 10s | 1920×1080 | 30 |

## Layout

```
scripts/intro-outro/
├── index.html                 # project root composition (required by hyperframes)
├── compositions/
│   ├── intro.html             # 18s intro
│   ├── outro.html             # 20s outro
│   └── intermission.html      # 10s background-only section break
├── assets/
│   ├── max-headroom-bg.esm.js # vendored WebGL background component
│   └── fonts/Orbitron.woff2   # vendored cyberpunk font (offline-safe)
├── render.sh                  # render all three to output/*.mp4
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
./render.sh           # all three, high quality → output/{intro,outro,intermission}.mp4
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
  `fisheye-strength="0.2"`; outro `speed="0.3"` (calmer); intermission
  `speed="0.5"`, `fisheye-strength="0.2"` (matches intro).
- **No fades in-composition:** the GSAP timelines no longer fade `#root` in/out.
  Cross-fades are the `video_compose` pipeline's responsibility. Intro/outro hold
  ~6s of background-only animation after their text beats; the intermission is
  background-only for its full 10s.
- **Palette:** cyan `#00ffff`, magenta `#ff00ff`, yellow `#ffe14d` on dark.
- **Type:** Orbitron (variable, weights 400–900) via local `@font-face`.
- **Glitch/glow:** RGB-split via CSS `::before`/`::after` + `clip-path`
  keyframes; `neon-pulse` text-shadow. CSS animations run on hyperframes'
  virtual clock under deterministic capture.
- **Timing:** GSAP timelines are `gsap.timeline({ paused: true })` registered on
  `window.__timelines[<composition-id>]`; position params are absolute
  composition-time seconds. FPS is a CLI flag, not a meta attribute.

### Intro beats
- t=2s — "Claracle" title, neon-glow glitch entrance
- t=4s — "WEEKLY REPORT" subtitle fade-in
- t=6s — "with Clarabel & Joracle"
- t=8s — "AI-generated voice narration • Data is real" (small, lower area)

### Outro beats
Credits appear section-by-section (not scrolling): CREDITS → Director jmservera →
Hosts Clarabel & Joracle (AI-generated synthetic voices) → Music: Claracle theme
by jmservera (original composition) → Platform Claracle www.claracle.com →
"Follow us on Spotify". Credits fade, then a finale "Claracle" neon-glow pulse
with "This episode uses AI-generated voice narration".
The background then keeps animating for the remaining seconds (no fade-out).

### Intermission
Background-only section-break asset — no text, titles, or credits. 10 seconds of
the Max Headroom animation at the same speed/fisheye/colors as the intro, for the
video pipeline to drop between sections.

## Upload to Azure (review copies)

```bash
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

## Regenerating with an AI assistant

These compositions are plain HTML/CSS/JS and are well suited to AI-assisted
iteration. Example prompt:

> Edit `scripts/intro-outro/compositions/intro.html` (a HyperFrames composition,
> 18s, 1920×1080, 30fps). Keep the `<max-headroom-bg>` WebGL background and the
> Orbitron `@font-face`. Maintain the composition contract: root `#root` with
> `data-composition-id`, `data-width`, `data-height`, `data-start="0"`,
> `data-duration`; timed elements use `class="clip"` with
> `data-start`/`data-duration`/`data-track-index`; build a paused GSAP timeline
> and register it on `window.__timelines["claracle-intro"]` with absolute
> composition-time positions. Do NOT add external font/CDN links (lint rejects
> them — vendor assets locally). After editing run `npx hyperframes lint .` (0
> errors) then `./render.sh draft` and inspect frames.
