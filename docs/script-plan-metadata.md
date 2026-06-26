# Script Plan Metadata (Layer 1)

**Status:** Implemented — `podcaster/script_plan.py`
**Schema version:** `1.0` (`SCRIPT_PLAN_SCHEMA_VERSION`)
**Epic:** [jmservera/SquadScope-Coordinator#32](https://github.com/jmservera/SquadScope-Coordinator/issues/32) — Phase 4: Audio–Video Synchronization Architecture
**Issue:** jmservera/SquadScope-Podcaster#485

The **script plan** is Layer 1 of the three-layer audio–video synchronization
model:

```
(1) Script Plan Metadata  →  (2) Realized Audio Metadata  →  (3) Edit Decision List
        #485                          #486                          #488 / #490
```

Its purpose is to make the script **declare its visual intent explicitly** rather
than inferring it after the fact (regex repo-URL scraping, "no repo URL ⇒ generic
background", etc.). Downstream layers consume the plan deterministically.

> **Design principle:** explicit markers > NLP inference. An intermission is an
> *explicit* mode, never merely "the absence of a repo reference".

## Visual markers in the script

Visual intent is declared with **non-spoken** marker lines in the script
markdown, parallel to the `## Section:` headers from `podcaster/sections.py`:

```
## Section: AI Frameworks Showdown
## Visual: repo https://github.com/owner/repo-a
HOST_A: This week three projects caught our eye...
HOST_B: Right — the first one is wild.
## Visual: intermission
HOST_A: Let's take a breath before the next batch.
## Section: Tooling Roundup
## Visual: article
HOST_B: Back to the rundown we published...
```

A `## Visual:` marker applies to **every following host turn** until the next
marker. Host turns before the first marker default to `article`.

The marker grammar is tolerant (case-insensitive, 1–6 leading `#`, `:` or `-`
separator). `## Visual: repo <url>` requires a GitHub repo URL.

### Visual modes

| `visual_mode` | Meaning | `repo_url` |
|---------------|---------|------------|
| `repo` | Show a specific GitHub repository | **required** |
| `article` | Show the source article / weekly page | none |
| `intermission` | Show a deliberate intermission / breather card | none |

Because the TTS pipeline only synthesizes lines that start with a host label
(`podcaster.episode.parse_script_segments`), `## Visual:` lines are inherently
non-spoken. `strip_visual_markers()` scrubs them from arbitrary text when needed.

## Serialized schema (`ScriptPlan.to_dict()`)

```json
{
  "schema_version": "1.0",
  "segments": [
    {
      "index": 0,
      "speaker": "Theo",
      "text": "Welcome to the show everyone.",
      "visual_mode": "article",
      "repo_url": null,
      "section_id": null
    },
    {
      "index": 1,
      "speaker": "Vera",
      "text": "This first repo is wild.",
      "visual_mode": "repo",
      "repo_url": "https://github.com/owner/repo-a",
      "section_id": "section-1"
    }
  ],
  "sections": [
    {
      "id": "section-1",
      "title": "AI Frameworks Showdown",
      "summary": "...",
      "repo_slugs": ["owner/repo-a"],
      "title_card": {"text": "AI Frameworks Showdown", "duration_seconds": 0.75}
    }
  ]
}
```

`ScriptPlan.from_dict()` reverses the serialization, so Layer 2 (realized audio
metadata) and Layer 3 (the EDL) can consume the plan without re-parsing markdown.

### Segment fields

| Field | Type | Description |
|-------|------|-------------|
| `index` | int | Zero-based spoken-order position in the episode |
| `speaker` | string | Host label as written in the script |
| `text` | string | Spoken text for the turn |
| `visual_mode` | enum | `repo` \| `article` \| `intermission` |
| `repo_url` | string\|null | Repo URL when `visual_mode` is `repo`, else `null` |
| `section_id` | string\|null | Enclosing `ScriptSection` id, or `null` (cold open) |

## Validation (`validate_script_plan`)

Blocking (raises `ScriptPlanValidationError`):

- every segment declares a known `visual_mode`;
- `repo` segments carry a well-formed GitHub `repo_url`;
- non-`repo` segments carry no `repo_url`.

Soft (logged + returned as warnings):

- plan has no spoken segments;
- plan declares no `repo` visuals (likely an inference regression).

## Generation prompt

`build_visual_marker_guidance()` adds a **VISUAL INTENT** block to the
script-generation system prompt (`podcaster/script_gen.py`), instructing the
model to emit `## Visual:` markers per segment and declare intermissions
explicitly.

## Downstream

Layer 2 — **Realized Audio Metadata** ([#486](https://github.com/jmservera/SquadScope-Podcaster/issues/486),
`podcaster/audio_metadata.py`, see `docs/realized-audio-metadata.md`) consumes
this plan plus the realized per-segment TTS durations to produce millisecond
utterance / word / topic timings whose topic ranges align to the `## Visual:`
markers declared here.

## Schema versioning

The schema is versioned via `SCRIPT_PLAN_SCHEMA_VERSION`. Bump the **minor** for
backward-compatible additions and the **major** for breaking changes; downstream
layers should guard on `schema_version`.
