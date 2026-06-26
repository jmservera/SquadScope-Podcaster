# Realized Audio Metadata (Layer 2)

**Status:** Implemented — `podcaster/audio_metadata.py`
**Schema version:** `1.0` (`AUDIO_METADATA_SCHEMA_VERSION`)
**Epic:** [jmservera/SquadScope-Coordinator#32](https://github.com/jmservera/SquadScope-Coordinator/issues/32) — Phase 4: Audio–Video Synchronization Architecture
**Issue:** jmservera/SquadScope-Podcaster#486

Realized audio metadata is Layer 2 of the three-layer audio–video synchronization
model:

```
(1) Script Plan Metadata  →  (2) Realized Audio Metadata  →  (3) Edit Decision List
        #485                          #486                          #488 / #490
```

Layer 1 declares **intent** (what the video should show per spoken turn). Layer 2
captures **reality**: the millisecond-precise timing produced once a script has
been synthesized. Layer 3 (the EDL) consumes this to match audio segments to
video clips **without re-measuring audio or re-inferring structure from prose**.

> **Design principle (from Layer 1):** explicit, measured, deterministic > inferred.

## Inputs

`extract_realized_audio_metadata(plan, segment_durations, *, gap_seconds, speech_offset_seconds, host_labels)`:

| Input | Description |
|-------|-------------|
| `plan` | The Layer 1 `ScriptPlan` (`podcaster/script_plan.py`) |
| `segment_durations` | Realized duration (seconds) of each synthesized host turn — one per `plan.segments`, e.g. from `podcaster.audio.probe_segment_durations()` |
| `gap_seconds` | Inter-segment silence used when stitching (default `0.35`), so timings match the final mix |
| `speech_offset_seconds` | Lead-in before speech starts (e.g. intro-music full-volume period); added to every timestamp |
| `host_labels` | Optional `(host_a_label, host_b_label)` to pin the normalized `speaker_id` mapping |

The timeline is built with `podcaster.audio.compute_segment_timeline`, the same
primitive the production pipeline uses to stitch segments, so the timings agree
with the assembled audio.

## Outputs

Three granularities of timing, all in **milliseconds**:

### Utterance timings (`UtteranceTiming`)

One per spoken host turn, parallel to the script plan:

| Field | Type | Description |
|-------|------|-------------|
| `index` | int | Zero-based spoken-order position |
| `speaker` | string | Raw host label as written in the script |
| `speaker_id` | string | Normalized role: `host_a` / `host_b` (… `host_c`) |
| `text` | string | Spoken text |
| `start_ms` / `end_ms` | int | Span on the assembled speech timeline |
| `visual_mode` | enum | `repo` \| `article` \| `intermission` (from Layer 1) |
| `repo_url` | string\|null | Repo URL when `visual_mode` is `repo` |
| `section_id` | string\|null | Enclosing `ScriptSection` id |
| `words` | `WordTiming[]` | Per-word timings covering `[start_ms, end_ms]` |

`speaker_id` resolution: when `host_labels` is supplied, `host_labels[0]` →
`host_a` and `host_labels[1]` → `host_b` (case-insensitive). Otherwise speakers
are assigned by first appearance, so the lead host (who speaks first) becomes
`host_a`.

### Word timings (`WordTiming`)

The Azure OpenAI `/audio/speech` endpoint returns **no** word timestamps, so word
boundaries are **estimated** by distributing each utterance's measured duration
across its whitespace-delimited words in proportion to character length. The
distribution is:

- **contiguous** — each word starts where the previous ended;
- **non-overlapping & monotonic** — boundaries never go backwards;
- **exact** — first word starts at the utterance start, last ends at the
  utterance end, with no rounding drift;
- **deterministic** — identical inputs always yield identical output.

This is good enough for editorial caption / emphasis cues; it is explicitly an
estimate, not a forced alignment.

### Topic ranges (`TopicRange`)

Contiguous runs of utterances sharing the same Layer 1 visual context
`(visual_mode, repo_url)`. Because that context only changes at an explicit
`## Visual:` marker, **topic ranges align to the script's visual markers** and
map directly to the repo / article / intermission segments declared in Layer 1.

| Field | Type | Description |
|-------|------|-------------|
| `visual_mode` | enum | Shared visual mode for the run |
| `repo_url` | string\|null | Shared repo URL (for `repo` runs) |
| `section_id` | string\|null | Section id of the first utterance in the run |
| `start_ms` / `end_ms` | int | First utterance start … last utterance end |
| `utterance_indices` | int[] | Spoken-order indices in this range |

`RealizedAudioMetadata.repo_topics` returns just the `repo` topic ranges in
timeline order — the per-repository discussion spans.

## Serialized schema (`RealizedAudioMetadata.to_dict()`)

```json
{
  "schema_version": "1.0",
  "gap_ms": 350,
  "speech_offset_ms": 0,
  "total_duration_ms": 10900,
  "utterances": [
    {
      "index": 1,
      "speaker": "Vera",
      "speaker_id": "host_b",
      "text": "First repo here",
      "start_ms": 2350,
      "end_ms": 5350,
      "visual_mode": "repo",
      "repo_url": "https://github.com/owner/repo-a",
      "section_id": "section-1",
      "words": [{"text": "First", "start_ms": 2350, "end_ms": 3350}]
    }
  ],
  "topics": [
    {
      "visual_mode": "repo",
      "repo_url": "https://github.com/owner/repo-a",
      "section_id": "section-1",
      "start_ms": 2350,
      "end_ms": 6700,
      "utterance_indices": [1, 2]
    }
  ]
}
```

`RealizedAudioMetadata.from_dict()` reverses the serialization, so Layer 3 (the
EDL) can consume the metadata without touching audio or markdown.

## Validation

`extract_realized_audio_metadata` raises `RealizedAudioMetadataError` when:

- `segment_durations` is not parallel to `plan.segments`;
- any segment duration is negative;
- `speech_offset_seconds` is negative.

Soft signals are logged (warning) when the plan has no utterances or declares no
`repo` topics (a likely Layer 1 regression).

## Schema versioning

Versioned via `AUDIO_METADATA_SCHEMA_VERSION`. Bump the **minor** for
backward-compatible additions and the **major** for breaking changes; Layer 3
should guard on `schema_version`.
