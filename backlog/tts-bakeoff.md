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

| Provider | Quality | Cost | Ops Fit | Rights | Resilience | Status | Notes |
|----------|---------|------|---------|--------|-----------|--------|-------|
| Azure AI Speech Standard voices via Speech SDK | TBD | TBD | High | TBD | TBD | Pending | Preferred Azure-first candidate for two or more voices and per-segment control |
| Azure AI Speech batch synthesis | TBD | TBD | High for long-form async | TBD | TBD | Pending | Evaluate for long scripts and queue-style processing; not enough alone if MVP needs live multi-voice control |
| Azure AI Speech OpenAI voices / Foundry voices | TBD | TBD | TBD | TBD | TBD | Conditional | Evaluate only if available in the target region and terms/retention are reviewed |
| OpenAI `tts-1` or `gpt-4o-mini-tts` | TBD | TBD | TBD | TBD | TBD | Conditional | Evaluate only if legal/privacy terms and retention controls fit the MVP |

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
