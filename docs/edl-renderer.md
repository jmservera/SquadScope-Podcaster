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
- **intermission fills** — a `color` source of the segment length;
- **title cards** — a `drawtext` overlay enabled for the card's leading window;
- **normalisation** — every segment is `scale`/`pad`/`fps`/`format`/`setsar`-ed to
  identical parameters so segments can be concatenated or cross-faded;
- **joining** — hard-cut `concat` (default, timing-exact) or `xfade` transitions
  (`RenderConfig.enable_crossfades`) using the EDL's declared crossfade.

## Determinism & timing

Identical inputs produce an identical `filter_complex` and identical ffmpeg
argv. Distinct clip files are assigned ffmpeg input indices in first-use order.

- **Hard-cut concat:** rendered duration equals the EDL total exactly.
- **xfade:** rendered duration equals the EDL total minus the cross-fade overlaps
  (standard `xfade` behaviour). Both are reported as
  `FfmpegRenderPlan.expected_duration_ms`.

## API

```python
plan = build_render_plan(edl, clip_paths, output_path, config=RenderConfig())
# plan.argv, plan.filter_complex, plan.expected_duration_ms  (pure, no side effects)

render_edl(edl, clip_paths, output_path, config=..., runner=...)  # shells out to ffmpeg
```

`clip_paths` maps each clip segment's `clip_id` to its source video path.
`render_edl` raises `EdlRenderError` on a missing clip, an empty EDL, a non-zero
ffmpeg exit, or a missing output. The ffmpeg call is injectable (`runner`) so the
graph builder is unit-tested without rendering; real-ffmpeg integration tests
verify rendered durations and skip when ffmpeg is unavailable.

## `RenderConfig`

| Field | Default | Notes |
|-------|---------|-------|
| `width` / `height` / `fps` | 1920 / 1080 / 30 | Canonical output format |
| `pixel_format` | `yuv420p` | |
| `crf` / `preset` | 18 / `medium` | libx264 quality |
| `intermission_color` | `black` | Fill colour |
| `font_file` | DejaVuSans-Bold | Title-card font (`drawtext` needs libfreetype) |
| `title_font_size` / `title_font_color` | 64 / `white` | |
| `enable_crossfades` | `False` | Hard-cut concat by default (timing-exact) |
