"""Job monitoring API for the SquadScope Podcaster dashboard (#249).

Exposes read-only endpoints to list, inspect, and retrieve logs for
pipeline jobs stored in the storage backend. Runs as a separate FastAPI
application (not the main /api/generate server).

Endpoints:
  GET /api/jobs          — list recent jobs (paginated)
  GET /api/jobs/{id}     — job detail (manifest + derived status)
  GET /api/jobs/{id}/logs — job logs (runner state transitions)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from podcaster.storage import StorageBackend, create_storage_backend

logger = logging.getLogger("podcaster.monitoring")

app = FastAPI(title="Podcaster Job Monitor", version="0.1.0")

# Allow the UI dev server (Vite) and production origins.
_CORS_ORIGINS = os.environ.get(
    "MONITORING_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class JobSummary(BaseModel):
    job_id: str
    status: str
    created_at: str | None = None
    week: str | None = None
    article_title: str | None = None


class JobListResponse(BaseModel):
    jobs: list[JobSummary]
    total: int


class JobDetailResponse(BaseModel):
    job_id: str
    status: str
    created_at: str | None = None
    expires_at: str | None = None
    week: str | None = None
    article_url: str | None = None
    article_title: str | None = None
    generation: dict[str, Any] | None = None
    publishing: dict[str, Any] | None = None
    lifecycle: dict[str, Any] | None = None
    quality_score: float | None = None
    warnings: list[str] | None = None


class LogEntry(BaseModel):
    timestamp: str | None = None
    event: str
    detail: str | None = None


class JobLogsResponse(BaseModel):
    job_id: str
    logs: list[LogEntry]


# ---------------------------------------------------------------------------
# Storage dependency
# ---------------------------------------------------------------------------

_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        _storage = create_storage_backend()
    return _storage


def set_storage(storage: StorageBackend | None) -> None:
    """Inject a storage backend (used in tests)."""
    global _storage
    _storage = storage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_manifest(raw: bytes) -> dict[str, Any] | None:
    try:
        doc = json.loads(raw.decode("utf-8"))
        return doc if isinstance(doc, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _extract_summary(manifest: dict[str, Any]) -> JobSummary:
    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    return JobSummary(
        job_id=manifest.get("job_id", ""),
        status=manifest.get("status", "unknown"),
        created_at=manifest.get("created_at"),
        week=str(request.get("week", "")) if request.get("week") else None,
        article_title=request.get("article_title") if isinstance(request.get("article_title"), str) else None,
    )


def _extract_detail(manifest: dict[str, Any]) -> JobDetailResponse:
    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else None

    # Derive a quality score from audio validation if available.
    quality_score: float | None = None
    if generation and isinstance(generation.get("audio_validation"), dict):
        av = generation["audio_validation"]
        if av.get("status") == "passed":
            quality_score = 1.0
        elif av.get("status") == "placeholder":
            quality_score = 0.0

    return JobDetailResponse(
        job_id=manifest.get("job_id", ""),
        status=manifest.get("status", "unknown"),
        created_at=manifest.get("created_at"),
        expires_at=manifest.get("expires_at"),
        week=str(request.get("week", "")) if request.get("week") else None,
        article_url=request.get("article_url") if isinstance(request.get("article_url"), str) else None,
        article_title=request.get("article_title") if isinstance(request.get("article_title"), str) else None,
        generation=generation,
        publishing=manifest.get("publishing") if isinstance(manifest.get("publishing"), dict) else None,
        lifecycle=manifest.get("lifecycle") if isinstance(manifest.get("lifecycle"), dict) else None,
        quality_score=quality_score,
        warnings=manifest.get("warnings") if isinstance(manifest.get("warnings"), list) else None,
    )


def _extract_logs(manifest: dict[str, Any]) -> list[LogEntry]:
    """Extract log-like entries from lifecycle transitions and runner state."""
    logs: list[LogEntry] = []

    # Lifecycle transitions
    lifecycle = manifest.get("lifecycle")
    if isinstance(lifecycle, dict):
        transitions = lifecycle.get("transitions")
        if isinstance(transitions, list):
            for t in transitions:
                if isinstance(t, dict):
                    logs.append(
                        LogEntry(
                            timestamp=t.get("at"),
                            event=f"transition:{t.get('to', 'unknown')}",
                            detail=t.get("reason"),
                        )
                    )

    # Synthesis runner state
    generation = manifest.get("generation")
    if isinstance(generation, dict):
        runner = generation.get("synthesis_runner")
        if isinstance(runner, dict):
            logs.append(
                LogEntry(
                    timestamp=runner.get("completed_at") or runner.get("at"),
                    event=f"synthesis:{runner.get('status', 'unknown')}",
                    detail=runner.get("reason"),
                )
            )

        # Synthesis queue state
        queue = generation.get("synthesis_queue")
        if isinstance(queue, dict):
            logs.append(
                LogEntry(
                    timestamp=queue.get("enqueued_at"),
                    event=f"queue:{queue.get('status', 'unknown')}",
                    detail=queue.get("detail"),
                )
            )

    return sorted(logs, key=lambda e: e.timestamp or "")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/jobs", response_model=JobListResponse)
def list_jobs(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    """List recent pipeline jobs."""
    storage = get_storage()
    # List all manifest blobs under jobs/ prefix.
    blobs = storage.list_blobs("jobs/", limit=500)
    manifest_blobs = [b for b in blobs if b.endswith("/manifest.json")]

    # Sort by path (which includes the job_id with week info) — reverse for most recent first.
    manifest_blobs.sort(reverse=True)

    total = len(manifest_blobs)
    page = manifest_blobs[offset : offset + limit]

    summaries: list[JobSummary] = []
    for blob_path in page:
        raw = storage.get_bytes(blob_path)
        if raw is None:
            continue
        manifest = _parse_manifest(raw)
        if manifest is None:
            continue
        summaries.append(_extract_summary(manifest))

    # Sort by created_at descending (most recent first).
    summaries.sort(key=lambda s: s.created_at or "", reverse=True)

    return JobListResponse(jobs=summaries, total=total)


@app.get("/api/jobs/{job_id}", response_model=JobDetailResponse)
def get_job(job_id: str):
    """Get detailed information for a specific job."""
    storage = get_storage()
    manifest_path = f"jobs/{job_id}/manifest.json"
    raw = storage.get_bytes(manifest_path)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    manifest = _parse_manifest(raw)
    if manifest is None:
        raise HTTPException(status_code=500, detail="Manifest is corrupt")
    return _extract_detail(manifest)


@app.get("/api/jobs/{job_id}/logs", response_model=JobLogsResponse)
def get_job_logs(job_id: str):
    """Get log entries for a specific job (lifecycle transitions + runner state)."""
    storage = get_storage()
    manifest_path = f"jobs/{job_id}/manifest.json"
    raw = storage.get_bytes(manifest_path)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    manifest = _parse_manifest(raw)
    if manifest is None:
        raise HTTPException(status_code=500, detail="Manifest is corrupt")
    return JobLogsResponse(job_id=job_id, logs=_extract_logs(manifest))


@app.get("/healthz")
def healthz():
    return {"status": "healthy"}
