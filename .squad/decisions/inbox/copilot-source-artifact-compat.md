# Decision: source_artifacts accepts string and object references

## Context

SquadScope emits `source_artifacts` as object references, while the original Podcaster validation accepted only `array[string]`.

## Decision

Keep the `/api/generate` v1 request backward compatible: `source_artifacts` accepts both legacy string references and SquadScope object references in the same array. Object references must include at least one stable reference field: `path`, `url`, `href`, `uri`, or `name`.

## Consequences

- Existing callers using `array[string]` continue to work.
- SquadScope object references pass validation without requiring a schema-version fork.
- Unknown object fields remain rejected so future contract drift is visible in tests.
- The top-level `/api/generate` response shape remains unchanged.
