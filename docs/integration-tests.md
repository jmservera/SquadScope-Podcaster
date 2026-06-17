# Integration tests

These tests cover three offline integration paths:

- video planning + composition ending in an `.mp4`
- serving the built UI bundle on a local port with SPA fallback
- publish/distribution flows for audio-only and video artifacts

## Run locally

```bash
pytest tests/integration/ -v
```

## Prerequisites

- Python 3.11+
- Node 22+ for the UI build
- `ffmpeg` only for non-mocked/full video runs (the integration suite mocks composition)

If you want the UI serve test to run locally, build the frontend first:

```bash
cd ui && npm ci --ignore-scripts && npm run build
```

## GitHub Actions

Trigger **Integration Tests** manually from the Actions tab with `workflow_dispatch`, or let it run on pull requests that touch `tests/integration/`, `podcaster/video/`, or `ui/`.
