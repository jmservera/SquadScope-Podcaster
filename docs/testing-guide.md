# Testing guide

This guide documents every test layer in SquadScope-Podcaster, how to run each
one, and what a healthy result looks like. It also covers the local
integration-testing stack (`docker-compose.test.yml`) that brings up an Azurite
storage emulator.

## Test layers at a glance

| Layer | Location | Marker / selector | Needs | What it covers |
| --- | --- | --- | --- | --- |
| Unit | `tests/test_*.py` | default | Python only | Pure logic for every module in `podcaster/` |
| Integration (offline) | `tests/integration/` | `integration` | Python (+ optional `ffmpeg`, built UI) | Video planning→`.mp4`, UI serve/auth, publish/distribution |
| Storage emulator | `docker-compose.test.yml` | n/a | Docker + Azurite | Local Blob/Queue/Table endpoints for storage-aware checks |
| Container image | `Containerfile` / CI `synthesis-image` | n/a | Docker | ffmpeg/ffprobe baked in; image builds + runs |

Test configuration lives in `pyproject.toml`:

- `testpaths = ["tests"]`, `pythonpath = ["."]`
- Markers: `slow` (network + Playwright browsers), `integration`
- `addopts = "-m 'not slow'"` — slow tests are **deselected by default**.

## 1. Unit tests (full suite)

The whole suite runs offline with no Azure dependencies (managed-identity and
network calls are mocked).

```bash
python -m pytest tests/ -v --tb=short | tail -50
```

Expected (current `main`):

```
1880 passed, 1 skipped, 2 deselected in ~48s
```

- The 2 *deselected* tests are `slow`-marked (Playwright/network).
- The 1 *skipped* test is environment-gated.

Quick run: `python -m pytest -q` (uses `pyproject.toml` defaults).

## 2. Integration tests (offline)

These exercise three end-to-end paths with composition/upload mocked, so they
do not require real ffmpeg or Azure:

- video planning + composition ending in an `.mp4`
- serving the built UI bundle on a local port with SPA fallback
- publish/distribution flows for audio-only and video artifacts

```bash
python -m pytest tests/integration/ -v
```

Expected: `9 passed`.

For the UI serve test against a real bundle, build the frontend first:

```bash
cd ui && npm ci --ignore-scripts && npm run build
```

## 3. New-feature test groups

The four feature areas added recently each have focused suites. Run them
individually for fast feedback:

### Multilanguage (episode brief, target-language script gen, TTS provider abstraction)

```bash
python -m pytest \
  tests/test_episode_brief.py tests/test_language_config.py \
  tests/test_language_fanout.py tests/test_localization_qa.py \
  tests/test_localized_overlays.py tests/test_tts_providers.py \
  tests/test_script_gen.py -q
```

Expected: `128 passed`.

### YouTube (upload, metadata, OAuth, quota, publish)

```bash
python -m pytest \
  tests/test_youtube_upload.py tests/test_youtube_metadata.py \
  tests/test_youtube_oauth.py tests/test_youtube_credentials.py \
  tests/test_youtube_playlist.py tests/test_youtube_publish.py \
  tests/test_youtube_quota.py tests/test_youtube_distribute_integration.py -q
```

Expected: `141 passed`. OAuth is mocked; no real Google credentials are used.

### AV sync (script-plan metadata, audio metadata, clip manifests, EDL planner + renderer)

```bash
python -m pytest \
  tests/test_script_plan.py tests/test_audio_metadata.py \
  tests/test_clip_manifest.py tests/test_edl.py tests/test_edl_render.py \
  tests/test_video_sync_plan.py tests/test_video_audio_align.py -q
```

Expected: `270 passed, 1 skipped`.

### Parallel pipeline (DAG scheduler, async TTS pool)

```bash
python -m pytest \
  tests/test_scheduler.py tests/test_tts.py \
  tests/test_tts_bakeoff.py tests/test_enqueue_synthesis.py -q
```

Expected: `80 passed`. Covers the `podcaster/scheduler.py` DAG (topological
ordering, cycle/duplicate/unknown-dependency detection, checkpoint resume) and
the concurrent TTS synthesis path.

## 4. Local integration stack with Azurite

`docker-compose.test.yml` brings up the Azurite emulator (Blob 10000, Queue
10001, Table 10002) plus a `podcaster` test-runner service built from
`Containerfile` (ffmpeg/ffprobe baked in).

### Start the emulator

```bash
docker compose -f docker-compose.test.yml up -d azurite
docker compose -f docker-compose.test.yml ps          # azurite -> healthy
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:10000/devstoreaccount1
# 400 = listening (bare GET is rejected; the port is up)
```

### Azurite connection string

The well-known Azurite dev-account connection string (used by tooling and any
fixture that talks to Azurite directly via `azure-storage-blob`
`from_connection_string`, the Azure CLI, etc.):

```
DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;
```

Inside the compose network, replace `127.0.0.1` with the service host
`azurite` (already wired in `docker-compose.test.yml`).

> **Auth note:** In production the app is identity-only (managed identity /
> IMDS) for Blob + Queue — see `AzureBlobStorageBackend` in
> `podcaster/storage.py`. Azurite does **not** speak Azure AD, so the
> connection string above is for direct Azurite tooling/fixtures only. For the
> offline integration suite the app uses the `LocalStorageBackend`
> (`PODCASTER_LOCAL_STORAGE_PATH`); the managed-identity blob path is
> intentionally not pointed at Azurite.

### Run the suite inside the container

```bash
# builds podcaster-synthesis:test the first time (heavy: Playwright + whisper)
docker compose -f docker-compose.test.yml run --rm podcaster
```

The `podcaster` service overrides the synthesis entrypoint to run
`python -m pytest tests/ -v --tb=short` against the mounted working tree.

### Tear down

```bash
docker compose -f docker-compose.test.yml down -v
```

## 5. Continuous integration

`.github/workflows/ci.yml` calls `.github/workflows/reusable-ci.yml`, which
runs four jobs on every PR/push:

- **infrastructure** — `az bicep build` + Checkov scan of `infra/`
- **test** — `pip install -r requirements.txt && pip install -e ".[dev]"`,
  then `pytest`, then `python -m compileall podcaster`
- **ui** — `npm ci`, `npm test`, `npm run build`
- **synthesis-image** — `docker build -f Containerfile` and verify
  ffmpeg/ffprobe are present

CI must be **correct, not just green** — never weaken/skip a test or gate to
make a check pass.

## Troubleshooting

- **Azurite port already in use:** stop the old container with
  `docker compose -f docker-compose.test.yml down`, or free ports 10000–10002.
- **`StarletteDeprecationWarning` / `httpx2`:** harmless; install `httpx2`
  (already in the `dev` extra) to silence the TestClient warning.
- **Slow tests not running:** they are deselected by default; run
  `python -m pytest -m slow` (needs network + Playwright browsers).
