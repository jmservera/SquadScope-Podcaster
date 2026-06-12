# Podcast Export Contract and Artifact Schemas

This document describes the input payload contract from SquadScope and the
output artifact schemas produced by the Podcaster generation pipeline.

For the HTTP API contract (endpoint, auth, request/response format), see
[integration-contract.md](./integration-contract.md).

## Input Contract (from SquadScope)

### Handoff Payload Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `week` | string | ✅ | ISO week or issue identifier (e.g., `2026-W24`) |
| `article_url` | string | ✅ | Published article URL |
| `article_sha256` | string | ❌ | SHA-256 digest of article content |
| `article_title` | string | ❌ | Article title for generation context |
| `article_content` | string | ❌ | Full article text (enables LLM generation) |
| `source_artifacts` | array | ❌ | Supporting artifact references (strings or objects) |
| `podcast_config` | object | ❌ | Override podcast identity/style |
| `podcast_config.style_guide` | string | ❌ | Editorial style guide text |
| `dry_run` | boolean | ❌ | Generate stubs only (no synthesis) |
| `force` | boolean | ❌ | Regenerate even if prior artifacts exist |
| `cost_override` | object | ❌ | Budget override (requires `force=true`) |
| `callback` | object | ❌ | Future callback configuration |

### Generation Modes

1. **Placeholder mode** (no `article_content`): Produces deterministic stub
   scripts and claim ledgers. Suitable for contract validation and dry runs.

2. **LLM mode** (`article_content` provided + chat endpoint configured):
   Generates dynamic two-voice conversational scripts and extracts real
   factual claims from the article. Falls back to placeholder on failure.

## Output Artifacts

All artifacts are staged under `jobs/<job_id>/` in blob storage.

### Artifact Tree

```
jobs/<job_id>/
├── manifest.json          # Job metadata, lifecycle, publishing gates
├── script.txt             # Two-voice podcast script
├── claim-ledger.json      # Factual claims with source mapping
├── cost-ledger.json       # Episode cost tracking
├── transcript.txt         # Timestamped transcript
├── show-notes.md          # Episode show notes
├── review-checklist.md    # Human review requirements
├── audio/<job_id>.mp3     # Audio output (placeholder until TTS synthesis)
└── packets/<job_id>.zip   # Publishing packet (all artifacts bundled)
```

---

## Artifact Schemas

### manifest.json

Top-level job metadata. Evolves through lifecycle transitions.

```json
{
  "schema_version": "squadscope-podcaster-job-v1",
  "job_id": "podcast-2026-W24-abc123def456",
  "status": "review_pending",
  "created_at": "2026-06-12T10:00:00Z",
  "expires_at": "2026-06-19T10:00:00Z",
  "request": {
    "week": "2026-W24",
    "article_url": "https://example.com/article",
    "article_sha256": "...",
    "article_title": "Title",
    "article_content_provided": true,
    "source_artifacts": [],
    "dry_run": false,
    "force": false,
    "cost_override": { "recorded": false, "actor": null, "recorded_at": null },
    "callback": { "requested": false, "url_host": null, "secret_name_provided": false }
  },
  "lifecycle": {
    "status": "review_pending",
    "revision": 1,
    "force": false,
    "transitions": [
      { "at": "2026-06-12T10:00:00Z", "to": "accepted", "reason": "request_validated" },
      { "at": "2026-06-12T10:00:00Z", "to": "review_pending", "reason": "artifacts_staged" }
    ]
  },
  "review": {
    "required": true,
    "required_for_tts": true,
    "status": "pending",
    "mechanism": "github_environment",
    "environment": "podcast-review",
    "workflow": ".github/workflows/podcast-review-gate.yml"
  },
  "generation": {
    "engine": "llm-script-gen",
    "deterministic": false,
    "audio_mode": "placeholder",
    "tts_provider": null,
    "tts_synthesis": {
      "status": "blocked",
      "allowed": false,
      "blocked_by": ["human_review", "provider_not_selected"]
    }
  },
  "publishing": {
    "mode": "manual",
    "eligible": false,
    "blocked_by": ["human_review", "real_tts_not_implemented", "audio_validation_not_passed"]
  },
  "artifact_access": {
    "model": "private_operator_path",
    "response_urls": { "publicly_accessible": false }
  },
  "artifacts": { "...": "per-artifact metadata with sha256, size, content_type" }
}
```

> **Synthesis lifecycle:** The manifest above shows the *initial* staged state.
> After `/api/generate` returns HTTP 202, the ACA synthesis job picks up the
> queued message and produces real two-voice audio (Azure OpenAI `gpt-4o-mini-tts`,
> fable + alloy). On completion the manifest is updated in-place:
> `generation.audio_mode` → `"synthesized"`, `generation.tts_provider` →
> `"openai-tts"`, `publishing.blocked_by` removes `"real_tts_not_implemented"`,
> and the MP3 artifact is replaced with the synthesized episode. Publication
> remains blocked by `"human_review"` until editorial sign-off.

### claim-ledger.json

Array of factual claims extracted from the source article.

```json
[
  {
    "claim_id": "claim_001",
    "script_excerpt": "Python 3.14 was released in June 2026",
    "source_url": "https://example.com/article",
    "source_quote": "Python 3.14 was officially released on June 1, 2026.",
    "source_paragraph": 2,
    "verified": false,
    "editor_notes": "Verify release date against python.org"
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `claim_id` | string | Unique claim identifier |
| `script_excerpt` | string | The factual claim as stated in conversation |
| `source_url` | string | Article URL where claim originates |
| `source_quote` | string\|null | Exact quote from article (null if implicit) |
| `source_paragraph` | int\|null | Approximate paragraph number |
| `verified` | boolean | Always `false` until human review |
| `editor_notes` | string | Guidance for human reviewer |

### cost-ledger.json

Episode cost tracking and budget guardrails.

```json
{
  "week": "2026-W24",
  "month": "2026-06",
  "provider": "not_selected",
  "voice": "not_selected",
  "billable_characters": 2500,
  "duration_seconds": 0,
  "budget": {
    "status": "within_budget",
    "monthly_cap_usd": "50.00",
    "projected_episode_count": 1,
    "projected_monthly_spend_usd": "0.00"
  },
  "readiness": { "complete": true }
}
```

### script.txt

Two-voice podcast script with metadata header.

```
Title: Claracle Podcast – Week 2026-W24
Episode: 2026-W24
Podcast: Claracle (https://www.claracle.com)
Source URL: https://example.com/article
Source SHA256: abc123...
Voices: Theo = fable (OpenAI TTS, the enthusiast); Vera = alloy (OpenAI TTS, the veteran)
Safety: source article text is untrusted data, sanitized, and never executed as instructions.
Generator: squad-podcaster llm-script-gen v0.1
---

Theo: Welcome to Claracle 2026-W24 issue! ...
Vera: Both hosts on this show are AI-generated synthetic voices, not human presenters. ...
...

Host outro: Manual review is required before publishing.
```

### transcript.txt

Timestamped transcript with metadata header. Timestamps are placeholders
until real audio synthesis produces accurate timecodes.

### show-notes.md

Episode show notes in Markdown with source links, AI disclosure, and
correction reporting instructions.

### review-checklist.md

Human review requirements checklist. Reviewers must verify claims, check
links, confirm TTS readiness, and approve before synthesis.

### publishing packet (ZIP)

All artifacts bundled with a `MANIFEST.json` root. The packet is
publication-blocked until all gates pass.

---

## Validation Rules

See `podcaster/validation.py` for the authoritative validation logic.

Key constraints:
- `week`: alphanumeric + `_.:-` characters only
- `article_url`: must be http/https
- `article_sha256`: lowercase hex, exactly 64 characters
- `source_artifacts`: array of strings or valid objects (with at least one reference field)
- `article_content`: string, min 50 chars recommended for useful generation
- `podcast_config`: validated sub-fields for name, url, host_a, host_b, style_guide

## Fixtures

Sample request fixtures for integration testing:

- `tests/fixtures/podcaster_request_legacy_strings.json` — legacy string source artifacts
- `tests/fixtures/podcaster_request_squadscope_objects.json` — SquadScope object references
