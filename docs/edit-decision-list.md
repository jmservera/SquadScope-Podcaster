# Edit Decision List (Layer 3 — Timeline planner)

**Status:** Implemented — `podcaster/video/edl.py`
**Schema version:** `1.0` (`EDL_SCHEMA_VERSION`)
**Epic:** [jmservera/SquadScope-Coordinator#32](https://github.com/jmservera/SquadScope-Coordinator/issues/32) — Phase 4: Audio–Video Synchronization Architecture
**Issue:** jmservera/SquadScope-Podcaster#488

The EDL is Layer 3's **editorial decisioning** stage:

```
(1) Script Plan Metadata  →  (2) Realized Audio Metadata  →  (3) Edit Decision List
        #485                          #486                          #488 (this) / #490
```

`plan_edl(metadata, clips, ...)` consumes the **realized audio metadata** (Layer 2,
`podcaster/audio_metadata.py`) and the **clip manifests**
(`podcaster/video/clip_manifest.py`) and produces an ordered, gap-free
`EditDecisionList` matching video material to the audio timeline. The EDL is the
*plan*; the ffmpeg renderer (#490) executes it.

## Editorial rules

- **Minimum visual segment duration.** A non-intermission visual segment shorter
  than `min_visual_ms` (default 8s) is **merged into a neighbour** (the short
  topic extends the previous block, which keeps its clip; a short *leading* block
  is absorbed forward). Intermissions are exempt — they are deliberate breathers.
- **Trim to fit / loop to fill.** Each clip is recorded long
  (`required_clip_seconds`). `plan_source_ranges` trims a clip to the exact audio
  duration using the manifest's **safe trim ranges** (largest-first; falls back
  to a tail trim if insufficient), or **loops** a loop section when a clip is
  unexpectedly short — never stretching.
- **Graceful degradation.** A repo/article block whose clip is missing degrades
  to an **intermission fill** (`is_fallback=True`) rather than failing (ties into
  #489).
- **Crossfades & title cards.** A crossfade (`crossfade_in_ms`) is declared into
  every segment after the first; a `TitleCardOverlay` is declared on the first
  segment of each section (titles via the optional `section_titles` map).

## Guarantees (deterministic for identical inputs)

`validate_edl(edl)` asserts all of:

- segments tile `[0, total_duration_ms]` with **no gaps and no overlaps**;
- every non-intermission segment is at least `min_visual_ms` long;
- each clip segment's `source_ranges` durations sum to its timeline duration.

## `EdlSegment`

| Field | Description |
|-------|-------------|
| `kind` | `clip` or `intermission` |
| `timeline_start_ms` / `timeline_end_ms` | Position on the final video timeline |
| `visual_mode` | Layer 1 visual mode (`repo`/`article`/`intermission`) |
| `clip_id` / `repo_url` | Source clip (null for an intermission fill) |
| `section_id` | Enclosing section (grouping / title cards) |
| `source_ranges` | Clip sub-ranges concatenated to fill the segment |
| `looped` | Clip extended by repeating a loop section |
| `crossfade_in_ms` | Crossfade into this segment (0 for the first) |
| `title_card` | Optional title-card overlay at the segment start |
| `is_fallback` | Degraded to a fill because the clip was missing |

## Serialized schema

`EditDecisionList.to_dict()` / `from_dict()` round-trip a stable, versioned dict
the renderer (#490) consumes directly.

## Pipeline position

```
ScriptPlan (#485) ─┐
                   ├─► RealizedAudioMetadata (#486) ─┐
TTS durations  ────┘                                 ├─► plan_edl ─► EditDecisionList ─► ffmpeg renderer (#490)
ClipManifests (#487) ────────────────────────────────┘
```
