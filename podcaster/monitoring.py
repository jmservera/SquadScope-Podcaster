"""Job monitoring API for the SquadScope Podcaster dashboard (#249, #250).

Exposes read-only endpoints to list, inspect, and retrieve logs for
pipeline jobs stored in the storage backend. Also provides a streaming
proxy for media files so the UI can play audio/video without direct
storage credentials.

Runs as a separate FastAPI application (not the main /api/generate server).

Endpoints:
  GET /api/jobs              — list recent jobs (paginated)
  GET /api/jobs/{id}         — job detail (manifest + derived status)
  GET /api/jobs/{id}/logs    — job logs (runner state transitions)
  GET /api/stream/{path}     — stream blob content (audio/video/images)
  GET /api/episodes          — list generated episodes with metadata
  GET /api/articles/{path}   — serve markdown articles for preview
"""

from __future__ import annotations

import json
import mimetypes
import os
import hmac
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from podcaster.storage import StorageBackend, create_storage_backend

app = FastAPI(title="Podcaster Job Monitor", version="0.1.0")

# Allow the UI dev server (Vite) and production origins.
_CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "MONITORING_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
    ).split(",")
    if origin.strip()
]

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


def _verify_api_key(x_podcaster_api_key: str = Header(default="")) -> None:
    configured = os.environ.get("MONITORING_API_KEY") or os.environ.get("PODCASTER_API_KEY", "")
    if not configured:
        return
    if not x_podcaster_api_key or not hmac.compare_digest(x_podcaster_api_key, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


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


@app.get("/api/jobs", response_model=JobListResponse, dependencies=[Depends(_verify_api_key)])
def list_jobs(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    """List recent pipeline jobs."""
    storage = get_storage()
    # List all manifest blobs under jobs/ prefix.
    blobs = storage.list_blobs("jobs/", limit=10000)
    manifest_blobs = [b for b in blobs if b.endswith("/manifest.json")]

    summaries: list[JobSummary] = []
    for blob_path in manifest_blobs:
        raw = storage.get_bytes(blob_path)
        if raw is None:
            continue
        manifest = _parse_manifest(raw)
        if manifest is None:
            continue
        summaries.append(_extract_summary(manifest))

    # Sort by created_at descending so pagination is consistent.
    summaries.sort(key=lambda s: s.created_at or "", reverse=True)

    return JobListResponse(jobs=summaries[offset : offset + limit], total=len(summaries))


@app.get("/api/jobs/{job_id}", response_model=JobDetailResponse, dependencies=[Depends(_verify_api_key)])
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


@app.get("/api/jobs/{job_id}/logs", response_model=JobLogsResponse, dependencies=[Depends(_verify_api_key)])
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


# ---------------------------------------------------------------------------
# Blob streaming proxy (#250)
# ---------------------------------------------------------------------------

_STREAMABLE_PREFIXES = ("audio/", "video/", "image/")

_EXTENSION_CONTENT_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


def _content_type_for_path(path: str) -> str:
    """Determine content type from file extension."""
    for ext, ct in _EXTENSION_CONTENT_TYPES.items():
        if path.lower().endswith(ext):
            return ct
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


@app.get("/api/stream/{blob_path:path}", dependencies=[Depends(_verify_api_key)])
def stream_blob(blob_path: str):
    """Stream a blob from storage to the client.

    The UI calls this endpoint to play audio/video or display images
    without needing direct Azure Blob Storage credentials.
    """
    if not blob_path or blob_path.strip("/") == "":
        raise HTTPException(status_code=400, detail="blob_path must not be empty")

    content_type = _content_type_for_path(blob_path)

    if not any(content_type.startswith(prefix) for prefix in _STREAMABLE_PREFIXES):
        raise HTTPException(
            status_code=403,
            detail=f"Content type {content_type!r} is not streamable",
        )

    storage = get_storage()
    data = storage.get_bytes(blob_path)
    if data is None:
        raise HTTPException(status_code=404, detail="Blob not found")

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Length": str(len(data)),
            "Cache-Control": "private, max-age=3600",
        },
    )


# ---------------------------------------------------------------------------
# Episodes listing (#250)
# ---------------------------------------------------------------------------


class EpisodeSummary(BaseModel):
    job_id: str
    title: str | None = None
    created_at: str | None = None
    status: str = "unknown"
    audio_path: str | None = None
    audio_url: str | None = None
    quality_score: float | None = None
    publish_status: str | None = None


class EpisodeListResponse(BaseModel):
    episodes: list[EpisodeSummary]
    total: int


def _extract_episode(manifest: dict[str, Any]) -> EpisodeSummary | None:
    """Extract episode summary from a manifest. Returns None if no audio."""
    generation = manifest.get("generation") if isinstance(manifest.get("generation"), dict) else None
    if generation is None:
        return None

    audio_path: str | None = None
    artifacts = generation.get("artifacts")
    if isinstance(artifacts, dict):
        audio = artifacts.get("audio") or artifacts.get("final_audio")
        if isinstance(audio, dict):
            audio_path = audio.get("path")
        elif isinstance(audio, str):
            audio_path = audio

    if not audio_path:
        audio_path = generation.get("audio_file") if isinstance(generation.get("audio_file"), str) else None

    if not audio_path:
        return None

    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    publishing = manifest.get("publishing") if isinstance(manifest.get("publishing"), dict) else None

    quality_score: float | None = None
    if isinstance(generation.get("audio_validation"), dict):
        av = generation["audio_validation"]
        if av.get("status") == "passed":
            quality_score = 1.0
        elif av.get("status") == "placeholder":
            quality_score = 0.0

    return EpisodeSummary(
        job_id=manifest.get("job_id", ""),
        title=request.get("article_title") if isinstance(request.get("article_title"), str) else None,
        created_at=manifest.get("created_at"),
        status=manifest.get("status", "unknown"),
        audio_path=audio_path,
        audio_url=f"/api/stream/{audio_path}" if audio_path else None,
        quality_score=quality_score,
        publish_status=publishing.get("status") if publishing else None,
    )


@app.get("/api/episodes", response_model=EpisodeListResponse, dependencies=[Depends(_verify_api_key)])
def list_episodes(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    """List generated episodes that have audio artifacts."""
    storage = get_storage()
    blobs = storage.list_blobs("jobs/", limit=10000)
    manifest_blobs = [b for b in blobs if b.endswith("/manifest.json")]

    episodes: list[EpisodeSummary] = []
    for blob_path in manifest_blobs:
        raw = storage.get_bytes(blob_path)
        if raw is None:
            continue
        manifest = _parse_manifest(raw)
        if manifest is None:
            continue
        episode = _extract_episode(manifest)
        if episode is not None:
            episodes.append(episode)

    episodes.sort(key=lambda e: e.created_at or "", reverse=True)
    return EpisodeListResponse(episodes=episodes[offset : offset + limit], total=len(episodes))


# ---------------------------------------------------------------------------
# Markdown article preview (#250)
# ---------------------------------------------------------------------------


@app.get("/api/articles/{article_path:path}", dependencies=[Depends(_verify_api_key)])
def get_article(article_path: str):
    """Serve a markdown article from storage for UI preview."""
    if not article_path or article_path.strip("/") == "":
        raise HTTPException(status_code=400, detail="article_path must not be empty")

    content_type = _content_type_for_path(article_path)
    if content_type not in ("text/markdown", "text/plain"):
        raise HTTPException(
            status_code=403,
            detail="Only markdown and text files can be served via this endpoint",
        )

    storage = get_storage()
    data = storage.get_bytes(article_path)
    if data is None:
        raise HTTPException(status_code=404, detail="Article not found")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="Article is not valid UTF-8 text")

    return Response(
        content=text,
        media_type=content_type + "; charset=utf-8",
        headers={"Cache-Control": "private, max-age=300"},
    )
