# Backlog: TTS Bakeoff

## Objective

Select and evaluate TTS providers to synthesize Podcaster scripts into high-quality narration. Decision must balance **quality**, **cost**, **operational fit**, **rights**, and **reliability**.

Candidates include the provider families named in #4. No generated audio belongs in git; store listening samples outside the repository and attach only human notes, non-secret metadata, and redacted links to the issue.

---

## Provider Interface Expectations

Each candidate provider MUST:

### Input contract
- Accept plain-text UTF-8 scripts (section 1.2 of `docs/editorial-standards.md`)
- Support metadata: episode title, speaker/voice identity, requested tone (neutral, conversational, etc.)
- Parse supported markup or reject unsupported characters with clear error messages
- Return structured error responses (HTTP status code + error details) on validation failure

### Output contract
- Return audio in MP3 (primary); WAV optional for archival
- Specify codec, bitrate, sample rate (e.g., MP3, 192 kbps, 44.1 kHz)
- Provide timestamp mapping: byte-accurate link between script text and audio time (±100ms accuracy)
- Include metadata: voice/model used, synthesis parameters, duration, encoding details
- Supply license certificate: voice rights, usage restrictions, attribution requirements

### Failure modes & recovery
- Timeout on long scripts: return partial output, queue status, or clear retry guidance
- Unsupported characters: return error with character position and suggestions
- Rate limit: HTTP 429 with `Retry-After` header; caller backs off and retries
- Voice/model unavailable: return list of alternatives; caller selects and resynthesizes

---

## Evaluation Criteria

### Shared reviewed test script

Every candidate MUST synthesize the same reviewed script so the comparison is fair.

- Length: 5-10 minutes of spoken audio.
- Source: a published SquadScope-style article or a synthetic article approved for provider sharing; do not use unpublished drafts unless Hermes explicitly approves.
- Content coverage:
  - technical acronyms: API, CI/CD, OIDC, TTS, SAS
  - proper nouns: SquadScope, Podcaster, Azure, GitHub Actions, OpenAI
  - numbers and dates: ISO week, percentages, currency, durations
  - punctuation and pacing: short paragraph, long paragraph, quotation, comma-heavy sentence
  - two-speaker requirement: a narrator voice and a short quoted/alternate-speaker segment if the provider supports multiple voices
- Review before synthesis:
  - Farnsworth confirms the script is TTS-ready under `docs/editorial-standards.md`.
  - Hermes confirms the script contains no secrets, private drafts, personal data, or real-person voice-cloning request.
  - Leela confirms the script is representative enough for MVP provider selection.

Record the exact script hash in the issue notes. Do not commit provider-generated audio or transcript files.

### 1. Quality (40%)

**Narration quality:**
- Naturalness: no AI artifacts, robotic cadence, or unnatural word stress
- Pronunciation: correct handling of acronyms, proper nouns, technical terms
- Pacing: appropriate speed and breath points for podcast listening
- Consistency: same voice/model produces consistent tone across episodes

**Test procedure:**
- Synthesize 5–10 minute test script in each candidate voice
- Have at least two human listeners score naturalness on a 1–5 scale; three is preferred
- Spot-check pronunciation of 10 technical terms from the script
- Compare pacing against human-narrated podcasts in the same domain
- Resynthesise the same script on day 7 and compare for consistency

**Pass threshold:** Average naturalness ≥ 4.0; no major pronunciation errors; pacing within ±15% of human baseline.

### 2. Cost (25%)

**Per-episode cost:**
- Synthesis: $/minute of output audio
- Storage: $/GB for MP3 + WAV archival
- Bandwidth: $/GB for CDN delivery or SAS URL generation (if applicable)
- Support: tier pricing, SLA penalties (if any)

**Test procedure:**
- Calculate total cost for 50 episodes/year at 15 minutes average
- Include storage for 2-year archive
- Estimate bandwidth if distributing via Podcaster CDN

**Pass threshold:** Total annual cost ≤ budget [TBD by leadership]; cost predictable and documented.

### 3. Operational Fit (20%)

**Reliability:**
- SLA: uptime percentage and incident response time
- Latency: time from request to first audio byte (aim: <10s for up to 10-minute script)
- Concurrency: max simultaneous synthesis jobs (aim: ≥10)

**Integration:**
- SDK/API availability in Python 3.11 (or via REST)
- Authentication: API key, OAuth, or other; compatibility with Azure app settings
- Regional deployment: availability in target regions (aim: US East, US West, Europe)
- Monitoring: Azure App Insights integration or structured logs

**Scalability:**
- Can handle peak load (10+ concurrent 15-minute scripts)
- Rate limiting policy is clear and non-punitive
- No hard caps on monthly synthesis minutes

**Test procedure:**
- Deploy SDK/client library and confirm successful auth
- Synthesize 10 concurrent 1-minute scripts; measure latency and throughput
- Confirm monitoring integration with Azure App Insights
- Validate regional availability for deployment target

**Pass threshold:** ≥99.5% SLA; latency <10s for typical script; Python integration available; clear monitoring support.

### 4. Rights & Compliance (10%)

**Voice/model rights:**
- Voice ownership: who owns the synthesized audio? (goal: Podcaster/SquadScope)
- Commercial use: permitted for distribution to Spotify, podcast hosts, archives
- Attribution: required attribution in show notes or manifest?
- Exclusivity: can multiple podcasts use the same voice? (goal: yes, non-exclusive)
- Modification/derivative rights: can audio be edited, remixed, or re-licensed? (goal: yes)

**Data privacy:**
- No scripts stored by provider (or retention policy ≤30 days)
- No audio stored after synthesis/delivery (except by Podcaster as archive)
- Compliance with GDPR (if applicable to SquadScope user data)

**Test procedure:**
- Review provider terms of service for all above points
- Request written confirmation of voice/commercial rights for distribution
- Confirm data retention and privacy policy with legal team

**Pass threshold:** Voice rights permit non-exclusive commercial distribution; no long-term data retention by provider; GDPR compliance (if needed) confirmed.

### 5. Resilience & Fallback (5%)

**Contingency:**
- If provider is unavailable, can we switch to an alternative? (goal: multiple providers in rotation)
- Can we re-synthesize an episode on-demand if audio corrupts?
- Cold-start: how quickly can a new provider be integrated if current provider fails?

**Test procedure:**
- Verify that script format from one provider is compatible with another
- Estimate effort to integrate a second provider (aim: <1 week)
- Confirm that audio files are provider-agnostic (MP3 standard format)

**Pass threshold:** Audio format is standard (MP3); re-synthesis achievable within 24 hours; integration effort is low.

---

## Candidates & Status

`Quality` stays `TBD (human listening)` for every candidate — naturalness/pronunciation/pacing
require the human listening pass on #4 and cannot be inferred from documentation. The other
columns are filled from sourced provider documentation (see the "Non-Audio Comparison (sourced
research)" section below for citations and as-of dates).

| Provider | Quality | Cost | Ops Fit | Rights | Resilience | Status | Notes |
|----------|---------|------|---------|--------|-----------|--------|-------|
| Azure AI Speech Standard/Neural voices via Speech SDK | TBD (human listening) | $15 / 1M chars (Neural); NeuralHD tier higher, exact price unverified | High — real-time <300 ms, 30+ regions, 200 TPS (to 1,000), Python SDK + REST | Commercial podcast OK, customer-owned output, no attribution; Microsoft DPA, per-region residency | Standard MP3/WAV; full SSML multi-voice (up to 50 `<voice>`); easy re-synthesis | Researched — audio pending | Preferred Azure-first candidate for two or more voices and per-segment SSML control |
| Azure AI Speech batch synthesis | TBD (human listening) | Same per-char as real-time; not on F0 free tier | High for long-form async — 10–120 s job latency, no per-request 10-min cap, 25+ regions, no concurrent-job limit | Same as Azure Speech (Microsoft DPA, customer-owned); results retained 7 days default (max 31 via `timeToLiveInHours`) | Long-form MP3/WAV/OPUS/AAC/FLAC; full SSML multi-voice; queue/poll model | Researched — audio pending | Evaluate for long scripts and queue-style processing; not enough alone if MVP needs live multi-voice control |
| Azure AI Speech HD / Azure OpenAI / Foundry voices | TBD (human listening) | NeuralHD ≈ $22 / 1M chars (secondary-source corroborated 2026-06-10; reported reduced from $30) | HD: real-time <300 ms; OpenAI-voice: >500 ms. Limited regions (HD ~9; OpenAI-voice ~2–5) | Same Azure terms (DPA, customer-owned, no training); HD custom endpoint needs consent | Partial SSML only — `<phoneme>` unsupported on HD and OpenAI voices; `<prosody>`/`<emphasis>` limited | Conditional — researched | Evaluate only if available in target region and terms/retention reviewed; DragonHDOmni multi-talker is preview |
| OpenAI `tts-1` / `tts-1-hd` / `gpt-4o-mini-tts` (direct) | TBD (human listening) | tts-1 ≈ $15, tts-1-hd ≈ $30 / 1M chars (secondary-source corroborated 2026-06-10; official page 403); gpt-4o-mini-tts token-based ≈ $0.015/min | Streaming, Python SDK + REST; **no Azure region routing** (OpenAI infra); SLA/rate limits unverified | Commercial OK **but mandatory AI-generated disclosure to listeners**; API data not trained on (since 2023-03-01); abuse logs ≤30 days, ZDR eligible | **No SSML, no multi-voice per call** — must stitch per-speaker calls client-side; `gpt-4o-mini-tts` `instructions` is partial style substitute | Conditional — researched | Evaluate only if legal/privacy terms, listener-disclosure, and retention controls fit the MVP |

---

## Non-Audio Comparison (sourced research)

**As-of date: 2026-06-10.** This section records the factual, documentation-sourced
inputs for Cost, Operational Fit, Rights/Privacy, and Resilience so reviewers can
make the provider decision once the **human listening** quality pass (the remaining
open acceptance criterion on #4) is complete. Audio quality is intentionally not
scored here.

### Cost
- **Azure Speech Neural (real-time and batch):** $15.00 per 1M billable characters.
  Source: `learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits`
  ("Multiply … by the unit price of $15 per million characters"); billable-character rules at
  `learn.microsoft.com/en-us/azure/ai-services/speech-service/text-to-speech` ("Billable characters").
  Free F0 tier: 0.5M chars/month.
- **Azure Speech NeuralHD / HD voices:** separate higher tier exists (pricing-page footnote 4,
  `azure.microsoft.com/en-us/pricing/details/speech/`); **≈ $22 / 1M chars** per secondary-source
  corroboration (2026-06-10; reported reduced from $30) — official JS-rendered pricing page still
  not machine-fetchable, confirm before the decision gate.
- **OpenAI direct:** `tts-1` ≈ $15/1M, `tts-1-hd` ≈ $30/1M, `gpt-4o-mini-tts` token-based
  (≈ $0.015/min audio) — **secondary-source corroborated 2026-06-10** (`openai.com/api/pricing`
  still HTTP 403). Confirm at `platform.openai.com/docs/pricing` before any decision.

### Data Retention / Training
- **Azure Speech (prebuilt voices):** input text and output audio are **not stored** in Microsoft
  logs and **not used to train** models; data processed only in the resource region.
  Sources: `learn.microsoft.com/en-us/azure/foundry/responsible-ai/speech-service/text-to-speech/data-privacy-security`,
  `learn.microsoft.com/en-us/azure/ai-services/speech-service/regions`.
- **Azure Speech batch:** same; job results retained 168 h default / 744 h max (`timeToLiveInHours`).
  Source: `learn.microsoft.com/en-us/azure/ai-services/speech-service/batch-synthesis`.
- **Azure OpenAI / Foundry voices:** prompts/completions not used to train; models stateless.
  Source: `learn.microsoft.com/en-us/azure/foundry/responsible-ai/openai/data-privacy`.
- **OpenAI direct:** API data not used for training since 2023-03-01; `/v1/audio/speech` keeps no
  application state, abuse-monitoring logs ≤30 days, **Zero Data Retention eligible**.
  Source: `developers.openai.com/api/docs/guides/your-data`.

### Rights / Compliance
- **All Azure routes:** commercial podcast distribution permitted, customer owns output, no
  attribution requirement found; covered by the Microsoft Products and Services DPA (GDPR/CCPA);
  per-region data residency. Real-person cloning (Custom Neural Voice) is limited-access and requires
  recorded voice-talent consent.
- **OpenAI direct:** commercial distribution permitted **but usage policy requires clear disclosure
  to listeners that the voice is AI-generated**
  (`developers.openai.com/api/docs/guides/text-to-speech`). Custom voices require a mandatory consent
  recording. Output-ownership terms unverified (policy pages 403). Data residency by sales approval
  (+10% uplift).

### Operational Fit
- **Azure Speech real-time:** latency <300 ms; max 10 min audio / 64 KB SSML per request; 200 TPS
  (adjustable to 1,000); HTTP 429 mostly autoscaling-transient (retry with backoff); 30+ regions;
  Python SDK (`azure-cognitiveservices-speech`) + REST. SLA commonly 99.9% (Azure AI Services Standard)
  — **not verified from a directly readable source today**.
- **Azure Speech batch:** async poll model; job latency ~10–120 s; 10,000 inputs / 2 MB JSON per job;
  no concurrent-job limit; 25+ regions; not on F0.
- **Azure HD / OpenAI-via-Azure voices:** HD <300 ms, OpenAI-voice >500 ms; HD available in ~9 regions,
  OpenAI-voice in ~2–5 — region availability is the key constraint.
- **OpenAI direct:** real-time streaming; Python SDK (`openai`) + REST; **no Azure-region routing**
  (runs on OpenAI infra); SLA and rate limits **unverified** today.

### Resilience / Multi-Voice & SSML
- **Azure Speech real-time + batch:** full W3C SSML 1.0 + `mstts:` extensions; up to 50 distinct
  `<voice>` tags per document (true multi-speaker in one request); `<break>`, `<phoneme>`, `<prosody>`
  all supported; standard MP3/WAV → provider-agnostic, easy re-synthesis and fallback.
- **Azure HD / OpenAI-via-Azure voices:** **partial SSML** — `<phoneme>` unsupported on HD and OpenAI
  voices; `<prosody>`/`<emphasis>` limited. DragonHDOmni offers a preview multi-talker voice.
- **OpenAI direct:** **no SSML and no multi-voice per call** — multi-speaker episodes require separate
  per-segment calls stitched client-side; `gpt-4o-mini-tts` `instructions` is a partial style substitute.

### Unverified items to confirm before the decision gate
- ~~Azure NeuralHD / HD exact per-character price (JS-rendered pricing page).~~ — see 2026-06-10 verification update below.
- ~~OpenAI TTS pricing (`openai.com` pages returned HTTP 403).~~ — see 2026-06-10 verification update below. OpenAI per-request character limit, rate limits, SLA, and explicit output-ownership terms **remain unverified** (official policy/limit pages still 403).
- ~~Azure AI Services exact SLA %.~~ — see 2026-06-10 verification update below (99.9% confirmed via secondary sources; confirm the signed SLA PDF before the decision gate).
- Full Azure HD-voice supported/unsupported SSML element list **remains unverified**.

### 2026-06-10 verification update (secondary-source corroboration)

The official OpenAI pricing/policy pages (HTTP 403) and the Azure Speech pricing page
(JavaScript-rendered) are still not machine-fetchable. The figures below are **corroborated by
independent secondary sources** and should be re-confirmed against the official pages by a human
before the decision gate; they are **not** a substitute for the primary-source verification the gate
requires.

- **OpenAI TTS pricing** (as-of 2026-06-10):
  - `tts-1` ≈ **$15 / 1M characters**; `tts-1-hd` ≈ **$30 / 1M characters**.
  - `gpt-4o-mini-tts` is **token-based** (~$0.60 / 1M text-input tokens + ~$12 / 1M audio-output
    tokens), ≈ **$0.015 / minute** of generated audio in typical use.
  - Sources: `costgoat.com/pricing/openai-tts`, `cloudprice.net/models/openai-gpt-4o-mini-tts`,
    `s-anand.net/blog/openai-tts-cost/`. Confirms the previously estimated tts-1 / tts-1-hd figures;
    official `platform.openai.com/docs/pricing` confirmation still required.
- **Azure Speech Neural HD price** (as-of 2026-06-10): ≈ **$22 / 1M characters** (reported reduced
  from $30 in March 2026); Standard Neural remains $15 / 1M; Custom Neural ≈ $24 / 1M; F0 free tier
  0.5M chars/month. Sources: `azurefeeds.com/2026/03/31/azure-speech-neural-hd-text-to-speech-recent-voice-updates/`,
  `speechactors.com/article/microsoft-azure-pricing-and-plans`. Official JS-rendered
  `azure.microsoft.com/en-us/pricing/details/speech/` confirmation still required.
- **Azure AI Services SLA** (as-of 2026-06-10): **99.9%** monthly uptime for paid tiers (free F0
  tier excluded), matching the previously assumed figure. Sources: `azurecharts.com/sla`,
  `opsiocloud.com/knowledge-base/what-is-azure-sla/`. Confirm against the signed Microsoft SLA PDF
  before the decision gate.

**Decision-input impact:** With these corroborated numbers, **Azure Speech Standard Neural ($15/1M,
99.9% SLA, full SSML multi-voice, customer-owned output, no listener-disclosure requirement)** remains
the strongest non-audio candidate for the MVP primary, with **Azure Speech batch synthesis** as the
long-form/fallback path. OpenAI direct stays a conditional candidate only (no SSML/multi-voice per
call, mandatory AI-voice listener disclosure, unverified limits). **No provider is selected here** —
the human listening naturalness/pronunciation/pacing pass and Hermes' compliance sign-off are still
required before recording the choice in `docs/SECURITY.md`.

> **No provider is selected by this research.** Selection still requires the human listening
> naturalness/pronunciation/pacing scores (≥4.0 threshold), Hermes' security/compliance sign-off, and
> recording the chosen primary + fallback in `docs/SECURITY.md` per the Decision Gate below.

---

## Sample Generation Tooling (issue #41)

The shared reviewed test script and a sample-generation tool are committed so the
bakeoff is reproducible. **No generated audio is committed** — only this input
script, redacted manifests, and human listening notes are shared.

- **Reviewed test script:** `docs/tts-bakeoff-test-script.txt`
  - SHA-256: `54443424505ded64c9c498021030661bf559be040a2cdd8b644f2fc9e1d97290`
  - Secret-free, two-speaker (`NARRATOR` / `GUEST`), covers acronyms, proper
    nouns, numbers/dates, punctuation, and pacing per the evaluation criteria.
- **Tool:** `scripts/tts_bakeoff_synthesize.py` (logic in `podcaster/tts_bakeoff.py`).

Plan the run without contacting any provider (safe anywhere, no keys needed):

```bash
python scripts/tts_bakeoff_synthesize.py --week 2026-W23 --manifest-out out/bakeoff-manifest.json
```

Generate and store private samples (operator step; requires an authorized Azure
Speech resource — see #30 for production infra, this stays bakeoff-only):

```bash
export AZURE_SPEECH_ENDPOINT="https://<region>.tts.speech.microsoft.com"
export AZURE_SPEECH_KEY="<from Key Vault / app setting; never commit>"
export PODCASTER_STORAGE_ACCOUNT_URL="https://<account>.blob.core.windows.net"
python scripts/tts_bakeoff_synthesize.py --execute --week 2026-W23 \
  --manifest-out out/bakeoff-manifest.json
```

Safety properties enforced by the tool and its tests:

- Execute mode refuses to run (exit 3) and prints the exact missing variable
  names if Azure Speech context is absent — no workarounds.
- Script text is XML-escaped before SSML embedding (SSML/XXE injection guard).
- The API key is read from the environment only and never printed; SAS query
  strings are redacted from manifest URLs.
- Audio is stored in the existing private storage account; samples are
  bakeoff-only and must not be published.
- Unreviewed providers (OpenAI/Foundry voices) ship disabled until Hermes
  approves region availability and retention terms.

Record each candidate's human listening notes against #4 using the template
below.

---

## Human Listening Notes Template

Attach notes to #4 using this structure for each candidate. Do not include secrets, provider keys, raw SAS URLs, or generated audio checked into git.

```markdown
### TTS bakeoff notes — {provider} / {voice}

- Test script hash: `{sha256}`
- Provider path: `{Speech SDK | batch synthesis | Foundry/OpenAI voice | OpenAI API}`
- Region/account boundary: `{region and resource type}`
- Voice/model: `{voice or model name}`
- Audio format returned: `{codec, bitrate, sample rate}`
- Synthesis duration / latency: `{wall-clock time}`
- Estimated episode cost: `{cost and assumptions}`
- Retention/training assumption: `{linked policy or reviewed setting}`
- Attribution/licensing requirement: `{required text or none}`

#### Listener scores
| Listener | Naturalness 1-5 | Pronunciation 1-5 | Pacing 1-5 | Technical-term errors | Notes |
|----------|------------------|-------------------|------------|-----------------------|-------|
| {name/role} | TBD | TBD | TBD | TBD | TBD |

#### Decision
- Passes quality threshold: `{yes/no}`
- Passes rights/privacy threshold: `{yes/no}`
- Recommended role: `{primary | fallback | reject}`
- Follow-up required before implementation: `{none or list}`
```

---

## Decision Gate

**Before production use, TTS provider MUST pass ALL of:**
1. Quality test with ≥4.0 naturalness score
2. Cost estimate within budget
3. Operational fit: SLA ≥99.5%, latency <10s, Python support
4. Rights: commercial use permitted, no long-term data retention
5. Resilience: fallback strategy defined or second provider ready
6. Human listening notes attached to #4 for the selected primary and fallback providers
7. Selected provider and fallback recorded in `docs/SECURITY.md` before any non-dry-run TTS implementation

**Final approval:** Editorial team + Leela (coordinator) sign-off required.

---

## Security & Compliance Gate

**MANDATORY: Before integrating ANY TTS provider, Hermes (Safety & Security) must review and approve the following:**

### Credential & Secret Handling

- [ ] **API key format:** Is authentication a bearer token (API key) or OAuth? Can it be stored as a GitHub secret?
- [ ] **Key rotation:** Can keys be rotated without downtime? Are old keys invalidated immediately?
- [ ] **Error handling:** If authentication fails, does the provider log or echo the received key in error messages?
- [ ] **Fallback behavior:** If credentials are missing, does Podcaster return a safe error (not "invalid key format")?

### Data Privacy & Compliance

- [ ] **Data retention:** Does the provider retain article text, audio, or voiceprints after synthesis? For how long?
- [ ] **Data deletion:** Can we request explicit deletion of data? Is deletion guaranteed within 24 hours?
- [ ] **Data location:** Where is data processed? Does it comply with SquadScope's data residency requirements?
- [ ] **Sub-processors:** Are there sub-contractors? Are they named in a DPA?
- [ ] **DPA available:** Can Podcaster obtain a Data Processing Agreement covering GDPR/CCPA requirements?
- [ ] **Audit rights:** Can Podcaster request audit logs of data access?

### SSML Safety & Injection Prevention

- [ ] **SSML support:** Which SSML tags does the provider support? (e.g., `<voice>`, `<break>`, `<phoneme>`)
- [ ] **Input sanitization:** What happens if article text contains `<voice>` or `<break>` tags? Are they escaped or processed?
- [ ] **Injection testing:** Run Podcaster test suite with payloads:
  - `<voice gender="male">`, `<break time="5s" />`, `<phoneme>` (all should be sanitized or rejected)
  - XML entity refs like `<!ENTITY xxe SYSTEM "file:///etc/passwd">` (should be rejected)
  - Script tags like `<script>alert("xss")</script>` (should be escaped as literal text)
- [ ] **Error handling:** Invalid SSML returns clear error, not provider stack trace.

### Integration Security

- [ ] **Azure managed identity support:** Can the Function App authenticate using its system-assigned identity, or only API keys?
- [ ] **Credential storage:** Provider credentials are stored in GitHub secrets, Azure app settings, or Key Vault references, never logged.
- [ ] **Logging:** Podcaster logs do not include API keys, provider credentials, or full audio output.
- [ ] **Error messages:** Errors returned to SquadScope do not leak provider details or credentials.
- [ ] **Timeout behavior:** If TTS takes >30 seconds, Function App times out gracefully (not with stack trace).

### Failure Modes & Rate Limiting

- [ ] **Rate limit response:** Provider returns HTTP 429 with `Retry-After` header; Podcaster respects it.
- [ ] **Quota enforcement:** If monthly/yearly quota is exceeded, does provider queue requests or return error?
- [ ] **Fallback:** If TTS is unavailable, can Podcaster return a safe stub response or queue for later?

### Audit & Compliance

- [ ] **Request logging:** Each TTS request is logged with job ID, timestamp, status, and duration (no keys/content).
- [ ] **Cost tracking:** Cost per synthesis is logged or queryable via Azure Cost Management.
- [ ] **Audit trail:** If audio is regenerated, prior synthesis details are retained for compliance.

### Sign-Off

**Hermes reviews the above checklist and approves before code review. Security concerns block integration.**

---

## Implementation Notes

- TTS synthesis is triggered during the `generate` endpoint processing (Bender handles infra)
- Script validation (section 1 of `editorial-standards.md`) occurs before TTS submission
- Audio output goes to Azure Blob Storage; manifest records provider, voice, timestamps
- Human review gate (section 6 of `editorial-standards.md`) is mandatory before non-dry-run synthesis
- Transcript generation from TTS output (via Azure Speech-to-Text or similar) is a separate milestone
- **No generated audio belongs in git.** `.gitignore` must exclude `*.mp3`, `*.wav`, and all audio formats.
