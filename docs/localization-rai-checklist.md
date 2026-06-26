# Localization RAI Checklist — Cultural Appropriateness (es / fr)

Part of the multilanguage epic (jmservera/SquadScope-Coordinator#27), issue #440.

This checklist is the **human gate** applied to every localized episode before
publish. The automated localization QA (`podcaster/localization_qa.py`) hard-fails
on untranslated English leakage and missing disclosure/CTA, and **flags**
overgeneralization patterns for review — but cultural appropriateness is a
judgment call. Reviewers (coordinated with Hermes for safety/RAI) apply the items
below to the Spanish (`es-419`) and French (`fr-FR`) output.

> Source language note: scripts are authored **directly** in the target language
> (#434), never machine-translated from English. The voices are native Azure
> multilingual neural pairs (#436). The checklist therefore focuses on *content*
> appropriateness, not translation fidelity.

## How to use

1. Run the pipeline; inspect the per-locale `localization_qa` result.
2. If `passed` is `false`, the episode is **blocked** — fix leakage / disclosure /
   CTA before re-running.
3. If `flags` is non-empty (or always, for a new market), apply the checklist
   below to the flagged passages and the script as a whole.
4. Record the outcome in the human review gate (`backlog/human-review-gate.md`).

## Hard requirements (automated — must pass)

- [ ] **No untranslated English leakage.** Dialogue is genuinely in the target
      language. Technical proper nouns (API, GitHub, CI/CD, Azure) staying in
      English is expected and correct.
- [ ] **AI-voice disclosure present** in the target language (es/fr), spoken in
      the opening per the show format.
- [ ] **Closing CTA present**, pointing listeners to the Claracle site in
      natural target-language phrasing.

## Cultural-appropriateness review (human — apply per locale)

### 1. No stereotyping or overgeneralization
- [ ] No sweeping "all/todos los/tous les <group>" generalizations about
      nationalities, regions, genders, or communities.
- [ ] Tech communities and maintainers are described by what they build, not by
      demographic assumptions.

### 2. Register and idiom
- [ ] Tone matches the Claracle two-host conversational format — natural, not a
      stiff or word-for-word machine register.
- [ ] Idioms read naturally for the locale. For `es-419`, prefer broadly
      Latin-American–intelligible phrasing (the `es-MX` voice is a proxy); avoid
      Iberian-only idioms. For `fr-FR`, use standard metropolitan French.
- [ ] Forms of address are consistent (e.g. French `vous`/`tu` choice is
      deliberate and consistent across both hosts).

### 3. Inclusive and respectful framing
- [ ] No content that mocks, demeans, or essentializes any group.
- [ ] Sensitive topics (security incidents, layoffs, controversies) are framed
      factually and without inflammatory localized slang.
- [ ] No locale-specific political, religious, or cultural editorializing beyond
      the source article's facts.

### 4. Brand and disclosure integrity
- [ ] The brand name ("Claracle") and the (English) website stay universal; only
      functional strings (disclosure, CTA) are localized.
- [ ] The AI-voice disclosure clearly states both voices are AI-generated, in the
      target language, without softening or omitting that fact.

### 5. Names and pronunciation
- [ ] Host persona names are consistent with the configured hosts for the locale.
- [ ] Proper nouns likely to be mispronounced have been spot-checked in the
      rendered audio sample.

## Sign-off

| Field | Value |
| ----- | ----- |
| Locale | `es-419` / `fr-FR` |
| Reviewer | |
| Date | |
| Automated QA `passed` | |
| Flags reviewed | |
| Cultural-appropriateness verdict | approve / changes-requested / reject |

Coordinate the RAI criteria with Hermes (safety). This document is intentionally
a living checklist: extend the term/pattern lists in
`podcaster/localization_qa.py` (`flag_terms`) and the items above as new markets
are added.
