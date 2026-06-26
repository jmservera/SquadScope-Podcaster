# Real-time pipeline progress reporting

> Transport foundation for the Phase 5 Observability epic
> ([SquadScope-Coordinator#30](https://github.com/jmservera/SquadScope-Coordinator/issues/30)).
> Implemented in [#469](https://github.com/jmservera/SquadScope-Podcaster/issues/469).

The pipeline pushes granular progress **events** to a durable per-job store so the
monitoring UI can show live stage/segment progress instead of polling manifest
snapshots. This document is the canonical schema reference for downstream epic
tasks: the stage-progress API ([#470](https://github.com/jmservera/SquadScope-Podcaster/issues/470)),
the log viewer ([#472](https://github.com/jmservera/SquadScope-Podcaster/issues/472)),
and the stage-visualization component ([#474](https://github.com/jmservera/SquadScope-Podcaster/issues/474)).

## Why a durable blob store (not in-memory / WebSocket state)

Azure Container Apps runs the pipeline in **stateless, serverless** workers that can
scale to zero and restart between executions. The progress channel must therefore:

- survive a worker restart, and
- be readable by a *different* process (the monitoring API) than the one that
  produced it.

So progress is appended to `jobs/{job_id}/progress.json` via the storage backend's
atomic `update_bytes` (the same compare-and-write primitive used for manifests),
which is safe under concurrent writers. The API reads that document on demand —
no in-memory assumptions, correct across restarts.

## Event schema (`squadscope-podcaster-progress-v1`)

The progress document is a JSON object:

```json
{
  "schema_version": "squadscope-podcaster-progress-v1",
  "job_id": "podcast-2026-W24-abc123",
  "updated_at": "2026-06-26T12:00:00Z",
  "current": { "stage": "synthesis", "phase": "recording", "segment_index": 12,
               "segment_total": 18, "percent": 66.7, "message": "recording 12/18",
               "at": "2026-06-26T12:00:00Z" },
  "events": [ /* ProgressEvent[], ascending seq */ ]
}
```

Each `ProgressEvent`:

| Field           | Type        | Notes |
|-----------------|-------------|-------|
| `seq`           | int         | Monotonic, 1-based. Cursor for incremental polling / SSE resume. |
| `at`            | string      | ISO-8601 UTC (`...Z`). |
| `stage`         | string      | One of the stage values below. |
| `phase`         | string?     | Optional free-form sub-phase (e.g. `recording`). |
| `segment_index` | int?        | Optional 1-based segment counter (the `N` in `N/M`). |
| `segment_total` | int?        | Optional total segments (the `M`). |
| `percent`       | float?      | Optional `0..100`. Auto-derived from `N/M` when not given. |
| `message`       | string?     | Optional human-readable detail. |

Optional fields are omitted when unset. `current` is the latest event without its
`seq`. The `events` array is capped (newest retained) to bound document growth.

### Stage values (`PipelineStage`)

`queued`, `brief`, `script`, `synthesis`, `compose`, `mux`, `publish`, `completed`,
`failed`. The terminal stages are `completed` and `failed` — no further events are
expected after one is emitted.

## Producing events

`podcaster.progress.emit_progress(storage, job_id, stage=..., ...)` appends an event.
It is **best-effort**: any storage failure is logged and swallowed so progress
reporting can never break the pipeline. The synthesis runner currently emits at
synthesis start, completion, and failure; finer per-segment instrumentation is
tracked by #470.

## Consuming events

### Poll — `GET /api/jobs/{job_id}/progress?since=<seq>`

Returns events with `seq > since`, plus the latest `current` snapshot, the
`last_seq` cursor to pass on the next poll, and a `terminal` flag. The job must
exist (have a manifest); a job with no progress yet returns an empty list. This is
the simple fallback transport.

```json
{ "job_id": "...", "current": { ... }, "events": [ ... ], "last_seq": 12, "terminal": false }
```

### Stream (preferred) — `GET /api/jobs/{job_id}/progress/stream?since=<seq>`

A long-lived [Server-Sent Events](https://developer.mozilla.org/docs/Web/API/Server-sent_events)
response. The UI subscribes with `EventSource`; each progress event arrives as one
`data:` line with an `id:` equal to its `seq`. The server polls the durable store,
sends `: keep-alive` heartbeats when idle, and closes once the job reaches a
terminal stage (or after a safety timeout). On reconnect, resume from the last
seen `id` via the `since` query parameter.

```js
const es = new EventSource(`/api/jobs/${jobId}/progress/stream?since=${lastSeq}`);
es.onmessage = (e) => applyProgress(JSON.parse(e.data));
```

SSE is preferred over WebSocket/SignalR here because it is a single plain HTTP
response — the simplest correct option on ACA — and degrades gracefully to the
polling endpoint above.

### Tuning

| Env var | Default | Purpose |
|---------|---------|---------|
| `MONITORING_SSE_POLL_SECONDS`      | `1.0`    | Durable-store poll interval. |
| `MONITORING_SSE_HEARTBEAT_SECONDS` | `15.0`   | Idle heartbeat cadence. |
| `MONITORING_SSE_MAX_SECONDS`       | `1800.0` | Hard cap on a single stream's lifetime. |
