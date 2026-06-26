# ffmpeg EDL Renderer (Layer 3 — execution)

**Status:** Implemented — `podcaster/video/edl_render.py`
**Epic:** [jmservera/SquadScope-Coordinator#32](https://github.com/jmservera/SquadScope-Coordinator/issues/32) — Phase 4: Audio–Video Synchronization Architecture
**Issue:** jmservera/SquadScope-Podcaster#490

The execution half of Layer 3: the planner (#488) emits an
`EditDecisionList`; this module translates it into a single ffmpeg
`filter_complex` pipeline and renders the final video.

```
EditDecisionList (#488) ─► build_render_plan ─► ffmpeg filter_complex ─► final video
```

## Filtergraph construction

Built segment-by-segment, then joined:

- **cuts / trims** — each clip segment `source_ranges` entry becomes `trim`+`setpts`;
  multiple ranges are `concat`-enated, so the clip plays exactly the kept material;
- **screenshot fills** — a still poster-frame image input held for the segment
  (`-loop 1 -t <dur>`), normalised like a clip (fallback chain, #489);
- **card fills** — a `color` source with a centred full-duration `drawtext` of the
  repo/article name (fallback chain, #489);
- **intermission fills** — a `color` source of the segment length;
- **title cards** — a `drawtext` overlay enabled for the card's leading window;
- **normalisation** — every segment is `scale`/`pad`/`fps`/`format`/`setsar`-ed to
  identical parameters so segments can be concatenated or cross-faded;
- **joining** — hard-cut `concat` (default, timing-exact) or `xfade` transitions
  (`RenderConfig.enable_crossfades`) using the EDL's declared crossfade.

## Graceful degradation (#489)

A failed or missing clip must never hard-fail the render or leave a gap. Before
building the graph, `render_edl` calls `degrade_for_render`, which rewrites any
clip segment whose `clip_id` is missing from `clip_paths` (or whose file does not
exist on disk) — and any screenshot segment whose image is missing — through the
fallback chain **`screenshot → card → intermission`**:

```python
degraded = degrade_for_render(
    edl, clip_paths,
    image_paths=...,        # fallback_image_id → still-image path
    screenshots=...,        # repo_url → fallback_image_id
    repo_labels=...,        # repo_url → friendly card text
    section_titles=...,
    check_files=True,       # also drop clips whose file is absent on disk
)
```

(Pass `check_files=False` to skip the on-disk existence check and only degrade
segments already marked unavailable.)

Timeline bounds, crossfades, title cards and section grouping are preserved, so
the result stays gap-free and the same length. Pass `degrade_missing=False` to
`render_edl` to opt out and have `build_render_plan` raise on a missing clip.

## Determinism & timing

Identical inputs produce an identical `filter_complex` and identical ffmpeg
argv. Distinct clip files are assigned ffmpeg input indices in first-use order.

- **Hard-cut concat:** rendered duration equals the EDL total exactly.
- **xfade:** rendered duration equals the EDL total minus the cross-fade overlaps
  (standard `xfade` behaviour). Both are reported as
  `FfmpegRenderPlan.expected_duration_ms`.

## API

```python
plan = build_render_plan(edl, clip_paths, output_path, image_paths=..., config=RenderConfig())
# plan.argv, plan.filter_complex, plan.expected_duration_ms  (pure, no side effects)

render_edl(edl, clip_paths, output_path, image_paths=..., screenshots=...,
           repo_labels=..., config=..., degrade_missing=True, runner=...)
```

`clip_paths` maps each clip segment's `clip_id` to its source video path;
`image_paths` maps each screenshot segment's `fallback_image_id` to a still-image
path. With `degrade_missing=True` (default) a missing clip is degraded to a
screenshot/card/intermission fill rather than raising. `render_edl` still raises
`EdlRenderError` on an empty EDL, a non-zero ffmpeg exit, or a missing output. The
ffmpeg call is injectable (`runner`) so the graph builder is unit-tested without
rendering; real-ffmpeg integration tests verify rendered durations and skip when
ffmpeg (or its `drawtext` filter) is unavailable.

## `RenderConfig`

| Field | Default | Notes |
|-------|---------|-------|
| `width` / `height` / `fps` | 1920 / 1080 / 30 | Canonical output format |
| `pixel_format` | `yuv420p` | |
| `crf` / `preset` | 18 / `medium` | libx264 quality |
| `intermission_color` | `black` | Fill colour |
| `font_file` | DejaVuSans-Bold | Title-card / card font (`drawtext` needs libfreetype) |
| `title_font_size` / `title_font_color` | 64 / `white` | |
| `card_color` | `0x1e1e2e` | Fallback card background (#489) |
| `card_font_size` / `card_font_color` | 72 / `white` | Fallback card text (#489) |
| `enable_crossfades` | `False` | Hard-cut concat by default (timing-exact) |
