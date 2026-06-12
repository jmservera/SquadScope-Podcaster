# Prompt-Injection Audit — Article-to-Script Generation Path

Issue: [#46](https://github.com/jmservera/SquadScope-Podcaster/issues/46) ·
Owners: Hermes (security), Fry (tests) · Status: hardening landed, scanner
decision recorded.

## Threat model

Podcaster consumes externally controlled text (article URLs/metadata, source
artifacts produced by SquadScope, and — in future — fetched article content).
Any of these can carry **indirect prompt injection**: instructions embedded in
data that try to make a downstream consumer (human reviewer, LLM, or TTS
engine) obey them. Coordinator security rule: **treat all input text as
untrusted by default** — fence it, validate it, length-cap it, scan it.

Today generation is a deterministic placeholder with **no LLM call**, so the
live injection surface is *indirect*: untrusted text echoed into review
artifacts (`script.txt`, `show-notes.md`, `transcript.txt`,
`review-checklist.md`, the packet, and `MANIFEST.json`) that humans and future
automation read. The hardening below neutralizes that surface now so the
defense is already in place when real article fetching / LLM / TTS is added.

## Inventory of external text fields

| Field | Source | Enters | Pre-existing control | Added control |
|-------|--------|--------|----------------------|---------------|
| `week` | request | script/show-notes/metadata | `WEEK_RE` charset allowlist (validation) | — |
| `article_url` | request | script/show-notes/transcript/metadata | http/https scheme check (validation) | — |
| `article_sha256` | request | script/metadata | lowercase hex-64 check (validation) | — |
| `source_artifacts[]` string | request | script line | type check only | **fenced + control-stripped + length-capped + injection-flagged** |
| `source_artifacts[].role` | request | script line | string-type check | **fenced + capped (64) + flagged** |
| `source_artifacts[].{url,href,uri}` | request | script line | http/https check | **fenced + capped (512)** |
| `source_artifacts[].{path,name}` | request | script line | string-type check | **fenced + capped (512/256)** |
| `source_artifacts[].sha256` | request | script line | hex-64 check | neutralized + capped (64) |
| Unknown object fields | request | — | rejected by validation allowlist | not echoed (echo allowlist) |
| Future: fetched article body | crawl | future LLM/TTS prompt | none yet | **must route through `podcaster.sanitization` before any prompt** |

## Implemented hardening (`podcaster/sanitization.py`)

1. **Untrusted-content fencing** — every echoed source-artifact value is wrapped
   in `《UNTRUSTED》…《/UNTRUSTED》` delimiters. Literal delimiters in the input
   are escaped so untrusted text cannot break out of or forge a fence.
2. **Structure-breaking neutralization** — control chars, zero-width / BiDi
   override glyphs are removed and all whitespace (incl. newlines/tabs) is
   collapsed, so untrusted text cannot inject new structural lines (e.g. a fake
   `Host outro:` directive) into an artifact. NFKC normalization first.
3. **Length caps** — per-field caps (`FIELD_LIMITS`) bound echoed text.
4. **Field allowlist** — only `role, url, href, uri, path, name, sha256` are
   echoed; all other object fields are dropped (validation already rejects
   fields outside the schema allowlist).
5. **Injection-marker detection** — `flag_injection` reports neutral marker
   names (`ignore_instructions`, `role_injection`, `identity_override`,
   `encoded_blob`, …) including base64 / percent / `\u` encoded payloads.
   Detection is **flag-only**: matched text is neutralized and **never obeyed**.
   Flags surface in the `Source Artifact:` line as
   `[untrusted-content-flagged: …; not executed]` and in `MANIFEST.json`'s
   `safety.injection_markers_detected`.
6. **Output canary checks** — `assert_no_canary` and regression tests prove
   untrusted markers never escape the fenced region into structural lines.
7. **Review/non-publication gates preserved** — human review and the
   non-publication blockers (`real_tts_not_implemented`, `human_review`) are
   untouched; this work is defense-in-depth, not a replacement.

`MANIFEST.json` now carries a `safety` block (`squadscope-podcaster-safety-v1`)
summarizing the fenced fields, caps, allowlist, detected markers, and the
explicit `obeys_external_instructions: false` assertion.

## Content-scanner decision (LLM Guard vs. Azure Prompt Shields)

**Decision: defer integration until a real LLM/TTS generation path exists;
adopt Azure AI Content Safety *Prompt Shields* as the primary scanner at that
point, with LLM Guard as an optional local/dev fallback.**

Rationale:
- There is **no LLM call today**, so a runtime scanner would add cost/latency
  and a new dependency with no active prompt to protect. The deterministic
  placeholder is fully covered by the structural fencing above.
- **Azure Prompt Shields** is the primary choice when generation goes live: it
  is a managed Azure service (consistent with the ACA-only Azure-native
  posture and managed identity), covers user-prompt and document/indirect
  attacks, and needs no extra model hosting. Cost/quota and a dedicated
  resource are a coordinator/Hermes decision at integration time.
- **LLM Guard** (OSS) is retained as a documented fallback for local
  development and offline tests, scanning article/source text before prompt
  construction.

**Required-before gate:** any change that feeds untrusted text into an LLM or
TTS prompt MUST (a) route the text through `podcaster.sanitization` and (b)
add Prompt Shields (or the documented fallback) ahead of the model call. This
gate is encoded in `MANIFEST.json` →
`safety.content_scanner.required_before = "llm_or_tts_generation_from_untrusted_text"`.

## Tests

`tests/test_sanitization.py` covers marker detection (incl. encoded variants),
control/zero-width stripping, length caps, fence breakout resistance, source-
artifact allowlisting, and end-to-end proof that malicious source-artifact text
is neutralized/fenced in `script.txt`, leaks no canary into `show-notes.md`, and
is reported in the packet `safety` summary.

## Sign-off

- [ ] Hermes — security review.
- [ ] Nibbler — final sign-off before closure.
