# Clip Manifests (repo video generation — Layer 2/3 input)

**Status:** Implemented — `podcaster/video/clip_manifest.py`
**Schema version:** `1.0` (`CLIP_MANIFEST_SCHEMA_VERSION`)
**Epic:** [jmservera/SquadScope-Coordinator#32](https://github.com/jmservera/SquadScope-Coordinator/issues/32) — Phase 4: Audio–Video Synchronization Architecture
**Issue:** jmservera/SquadScope-Podcaster#487

Repo clips are the raw video material the Layer 3 Edit Decision List (#488 / #490)
trims and sequences against the realized audio timeline. Each generated clip is
accompanied by a **clip manifest** so clips can be generated **in parallel**,
ahead of and independent from the audio, and edited **deterministically**.

> **Design principle:** generate long, trim to fit.

## Clip-length policy

`required_clip_seconds(discussion_seconds)`:

```
max(REQUIRED_CLIP_MIN_SECONDS=60s, discussion_seconds * DISCUSSION_MARGIN_FACTOR=1.5)
```

Every repo clip is recorded long enough to cover its discussion time with margin,
so the EDL can always **trim to fit** rather than stretch. Negative inputs clamp
to the 60s floor.

## Manifest contents (`ClipManifest`)

| Field | Type | Description |
|-------|------|-------------|
| `clip_id` | string | Stable clip identifier (e.g. `clip-000`) |
| `repo_url` | string\|null | Repo shown, or `null` for article/intermission/fallback |
| `duration_ms` | int | Realized recorded length |
| `chapters` | `ClipChapter[]` | Labeled regions (`readme`, `file-tree`, `issues`, …) with `start_ms`/`end_ms` |
| `trim_ranges` | `TrimRange[]` | Interior regions safe to **cut** when shortening |
| `loop_sections` | `LoopSection[]` | Stable regions safe to **repeat** when extending |
| `is_fallback` | bool | True for a static fallback card (freely loop/trimmable) |

Derived helpers: `trimmable_ms` (total cuttable), `min_trimmed_duration_ms`
(shortest achievable length), `covers(discussion_seconds)`.

### Deriving trim ranges & loop sections

`build_clip_manifest(...)` derives both from the chapters: each chapter's
**interior** — its span minus `edge_margin_ms` (default 500ms) on each side, when
at least `min_safe_range_ms` (default 1000ms) remains — is treated as both a safe
place to cut and a region that can be looped. Cutting away from chapter edges
keeps trims off page-load and scroll transitions. A *fallback* clip (a uniform
static card, no chapters) is fully trimmable and loopable across its whole span.

## Validation

`build_clip_manifest` raises `ClipManifestError` when:

- `duration_ms` is not positive;
- a chapter has non-positive duration;
- a chapter falls outside `[0, duration_ms]`;
- chapters overlap or are out of order.

## Serialized schema

`ClipManifest.to_dict()` / `ClipManifest.from_dict()` round-trip a stable,
versioned dict so the Layer 3 planner consumes manifests without touching the
video file. Bump `CLIP_MANIFEST_SCHEMA_VERSION` minor for additions, major for
breaking changes.
