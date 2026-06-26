# Language fan-out job architecture (#439)

The multilanguage pipeline gathers **source assets once** and then processes
each configured language **independently**. A failure in one language is retried
on its own and never re-runs the shared source work or blocks the other
languages.

```
                         ┌──────────────────────────────┐
   gather_source()  ──▶  │  shared source (run ONCE)     │
   (once per job)        │  • episode brief (#433)       │
                         │  • browser recordings         │
                         └──────────────┬───────────────┘
                                        │  fan out by configured languages (#432)
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                                 ▼
   ┌─────────┐                     ┌─────────┐                       ┌─────────┐
   │  en     │                     │  es     │                       │  fr     │
   │ script  │                     │ script  │                       │ script  │
   │ TTS     │   independent       │ TTS     │   independent         │ TTS     │
   │ overlays│   retry             │ overlays│   retry               │ overlays│
   │ compose │                     │ compose │                       │ compose │
   │ publish │                     │ publish │                       │ publish │
   └─────────┘                     └─────────┘                       └─────────┘
 jobs/{id}/...                jobs/{id}/es/...                  jobs/{id}/fr/...
```

## Module: `podcaster/language_fanout.py`

Pure orchestration — callers inject the side-effecting stages, so the engine,
the job runner, and tests share one control flow.

| API | Purpose |
| --- | --- |
| `plan_language_branches(languages, default_language="en")` | Ordered, de-duplicated locales; default language first/primary. Locales are normalized to lowercase full tags (e.g. `fr-FR` → `fr-fr`); `pt-BR` and `pt-PT` remain distinct branches. |
| `shared_artifact_path(job_id, name)` | `jobs/{id}/{name}` — language-independent source assets. |
| `language_artifact_path(job_id, locale, name)` | `jobs/{id}/{locale}/{name}` where `locale` is the full normalized lowercase tag (e.g. `jobs/{id}/fr-fr/…`, `jobs/{id}/pt-br/…`); English stays flat (`jobs/{id}/{name}`) for backward compatibility, or pass `flat_default_language=None` to nest every language. |
| `run_language_fanout(languages, *, gather_source, process_language, retry, max_workers)` | Gather source once, then run each language with independent retry; returns `FanOutResult`. When `max_workers > 1`, `shared` is passed read-only to all threads — `process_language` must not mutate it. |
| `RetryPolicy(max_attempts=3)` | Per-branch retry. `NonRetryableError` short-circuits. |
| `FanOutResult` / `LanguageBranchResult` | Aggregate + per-language outcome (`status`, `attempts`, `payload`, `error`). |

### Failure semantics

- **Shared gather fails** → fatal: `run_language_fanout` raises, no branches run
  (nothing to fan out from).
- **One language fails** → isolated: recorded as a failed `LanguageBranchResult`;
  other languages still run; `gather_source` is **not** re-run.
- **Transient language error** → retried up to `RetryPolicy.max_attempts`.
- **Config/permanent error** → raise `NonRetryableError` to fail that branch
  immediately without burning retries.

## Adoption (job_runner / jobs / orchestration)

The current single-language pipeline is the `default_language="en"` case: with
one language and flat English paths, behavior is unchanged. To enable
multilanguage, the job runner supplies:

- `gather_source` = the existing source/brief + recording stage.
- `process_language(language, shared)` = script (#434) → TTS (#435) → overlays
  (#437) → compose → publish (#438), writing under `language_artifact_path(...)`.
- `languages` from the per-language config (#432).

Each language branch can be retried independently by the queue worker because
its artifacts live under an isolated `jobs/{id}/{locale}/` prefix and depend only
on the already-persisted shared source assets.
