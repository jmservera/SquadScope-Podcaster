# Phase Details: scaleout-fanout-flake

## P01 — Harness isolation fix

### Context

Baseline local runs failed 20/20 with `clip 0 missing`. Docker logs showed recorders running stale/different code from a shared `podcaster-synthesis:test` image and exiting 0 after recoverable setup failures. The current worktree source does not contain the logged stale exception path, so the selected fix is deterministic harness image isolation/building.

### P01-T01 — Update current-worktree image build/use

* Modify `docker-compose.fanout.yml` to allow a caller-supplied image tag instead of a fixed shared tag.
* Modify the test compose helper to set a unique tag for this worktree/process and run `docker compose build recorder` (or equivalent deterministic build) before scaling recorder containers.
* Keep Azurite service behavior unchanged.

### P01-T02 — Preserve strict test assertions

* Do not add sleeps/retries around missing clip assertions.
* Continue asserting every expected `.webm` and `.manifest.json`, plus exact counts.

## P02 — Validation and PR delivery

### P02-T01 — Targeted validation

* Run the target integration test once after the fix.
* Run the target integration test 20 consecutive times and record pass/fail count.

### P02-T02 — Required repository validation

* Run `python3 -m pytest -q`.
* Run `ruff check podcaster tests`.
* Run `ruff format --check podcaster tests`.

### P02-T03 — Delivery

* Commit with required trailer.
* Push `squad/723-scaleout-flake`.
* Open PR against `main` with `Closes jmservera/SquadScope-Podcaster#723` and mention that #682 touches nearby video code but root cause is harness image reuse.
* Wait about 3 minutes after CI starts/push, inspect CI and Copilot review threads, address valid unresolved threads up to two rounds.
