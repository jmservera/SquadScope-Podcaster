"""FastAPI API server for podcaster monitoring and generation endpoints.

Exposes read-only endpoints to list, inspect, and retrieve logs for
pipeline jobs stored in the storage backend. Also provides a streaming
proxy for media files so the UI can play audio/video without direct
storage credentials.

Endpoints:
  POST /api/generate         — validate payload and enqueue generation
  POST /api/review           — process review decisions
  GET /api/jobs              — list recent jobs (paginated)
  GET /api/jobs/{id}         — job detail (manifest + derived status)
  GET /api/jobs/{id}/logs    — job logs (runner state transitions)
  GET /api/stream/{path}     — stream blob content (audio/video/images)
  GET /api/episodes          — list generated episodes with metadata
  GET /api/articles/{path}   — serve markdown articles for preview
  GET /api/credentials       — auth configuration status for the UI
  GET/POST /api/config       — runtime config inspection placeholder
"""

from __future__ import annotations

import json
import logging
import os
import hmac
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from podcaster.auth import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    create_token,
    get_credentials,
    verify_auth,
    verify_token,
)
from podcaster.failure_reporting import report_failure
from podcaster.jobs import failed_response, run_generation_job
from podcaster.orchestration import process_review_decision
from podcaster.storage import StorageBackend, create_storage_backend
from podcaster.validation import validate_payload_details

app = FastAPI(title="Podcaster Job Monitor", version="0.1.0")

MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MiB

# Allow all origins by default for backward compatibility with the API container.
_CORS_ORIGINS_RAW = os.environ.get("MONITORING_CORS_ORIGINS", "*")
_CORS_ORIGINS = (
    ["*"]
    if _CORS_ORIGINS_RAW.strip() == "*"
    else [origin.strip() for origin in _CORS_ORIGINS_RAW.split(",") if origin.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
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


async def _read_json_object(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    content_length = int(request.headers.get("content-length", "0") or "0")
    if content_length > MAX_REQUEST_BODY:
        return None, JSONResponse(status_code=413, content={"error": "request body too large"})

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        return None, JSONResponse(
            status_code=400,
            content=failed_response(["request body must be valid JSON"]),
        )

    if not isinstance(payload, dict):
        return None, JSONResponse(
            status_code=400,
            content=failed_response(["request body must be a JSON object"]),
        )

    return payload, None


# ---------------------------------------------------------------------------
# Auth endpoints (#273)
# ---------------------------------------------------------------------------


@app.post("/api/auth/login", response_model=LoginResponse)
def login(body: LoginRequest):
    """Validate username/password and return a JWT."""
    creds = get_credentials()
    if creds is None:
        raise HTTPException(
            status_code=501,
            detail="Simple auth is not configured (UI_AUTH_* env vars missing)",
        )
    expected_user, expected_pass, secret = creds
    if not hmac.compare_digest(body.username, expected_user) or not hmac.compare_digest(
        body.password, expected_pass
    ):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_token(body.username, secret)
    return LoginResponse(token=token, username=body.username)


@app.get("/api/auth/me", response_model=MeResponse)
def me(authorization: str = Header(default="")):
    """Return the current user from a valid Bearer token."""
    creds = get_credentials()
    if creds is None:
        raise HTTPException(status_code=501, detail="Simple auth is not configured")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    import jwt as _jwt

    try:
        payload = verify_token(authorization[7:], creds[2])
    except _jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return MeResponse(username=payload["sub"])


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


@app.post("/api/generate", dependencies=[Depends(verify_auth)])
async def api_generate(request: Request):
    """Validate payload and run the generation pipeline (#279)."""
    payload, error_response = await _read_json_object(request)
    if error_response is not None:
        return error_response

    assert payload is not None
    validation = validate_payload_details(payload)
    if validation.errors:
        response = failed_response(validation.errors, validation.warnings or None)
        return JSONResponse(status_code=400, content=response)

    try:
        result = run_generation_job(payload, validation_warnings=validation.warnings or None)
    except Exception:
        logger.exception("unhandled error in generation job")
        report_failure(
            container="podcaster-api",
            error_type="GenerateEndpointError",
            error_message="Unhandled exception in /api/generate",
        )
        response = failed_response(["internal server error"])
        return JSONResponse(status_code=500, content=response)

    status_code = 202
    if result.response.get("status") == "failed":
        status_code = 400
    elif result.response.get("status") == "dry_run":
        status_code = 200

    logger.info(
        "api_generate job_id=%s status=%s dry_run=%s",
        result.response.get("job_id"),
        result.response.get("status"),
        bool(payload.get("dry_run")),
    )
    return JSONResponse(status_code=status_code, content=result.response)


@app.post("/api/review", dependencies=[Depends(verify_auth)])
async def api_review(request: Request):
    """Process a review decision for a generated episode (#279)."""
    payload, error_response = await _read_json_object(request)
    if error_response is not None:
        return error_response

    assert payload is not None
    job_id = str(payload.get("job_id") or "").strip()
    reviewer = str(payload.get("reviewer") or "").strip()
    decision = str(payload.get("decision") or "").strip()
    notes = str(payload.get("notes") or "")
    run_url = str(payload.get("run_url") or "").strip() or None
    reviewed_at = str(payload.get("reviewed_at") or "").strip() or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    publish_on_approval = payload.get("publish_on_approval", True) is not False

    errors: list[str] = []
    if not job_id:
        errors.append("job_id is required")
    if not reviewer:
        errors.append("reviewer is required")
    if decision not in {"approved", "changes_requested", "rejected"}:
        errors.append("decision must be approved, changes_requested, or rejected")
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})

    try:
        outcome = process_review_decision(
            job_id,
            reviewer=reviewer,
            decision=decision,
            reviewed_at=reviewed_at,
            notes=notes,
            run_url=run_url,
            publish_on_approval=publish_on_approval,
        )
    except ValueError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except Exception:
        logger.exception("unhandled error in review orchestration")
        report_failure(
            container="podcaster-api",
            error_type="ReviewEndpointError",
            error_message="Unhandled exception in /api/review",
        )
        return JSONResponse(status_code=500, content={"error": "internal server error"})

    publish_result = outcome.publish_result
    logger.info(
        "api_review job_id=%s decision=%s publish_status=%s",
        job_id,
        decision,
        publish_result.status if publish_result else "not_requested",
    )
    return JSONResponse(
        status_code=200,
        content={
            "job_id": job_id,
            "status": outcome.manifest.get("status"),
            "review_status": outcome.manifest.get("review_status"),
            "publish_status": publish_result.status if publish_result else None,
            "publish_error": publish_result.error if publish_result else None,
            "manifest": outcome.manifest,
        },
    )


@app.get("/api/jobs", response_model=JobListResponse, dependencies=[Depends(verify_auth)])
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


@app.get("/api/jobs/{job_id}", response_model=JobDetailResponse, dependencies=[Depends(verify_auth)])
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


@app.get("/api/jobs/{job_id}/logs", response_model=JobLogsResponse, dependencies=[Depends(verify_auth)])
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


@app.get("/api/credentials", dependencies=[Depends(verify_auth)])
def api_credentials():
    """Return credential configuration status for the UI (#279)."""
    return {
        "api_key_configured": bool(
            os.environ.get("MONITORING_API_KEY") or os.environ.get("PODCASTER_API_KEY")
        ),
        "ui_auth_configured": get_credentials() is not None,
    }


@app.get("/api/config", dependencies=[Depends(verify_auth)])
def api_config_get():
    """Return current runtime configuration for the UI (#279)."""
    return {
        "storage_backend": os.environ.get("STORAGE_BACKEND", "azure"),
        "storage_container": os.environ.get("AZURE_STORAGE_CONTAINER", ""),
        "cors_origins": _CORS_ORIGINS,
    }


@app.post("/api/config", dependencies=[Depends(verify_auth)])
async def api_config_post(request: Request):
    """Update runtime configuration (placeholder) (#279)."""
    payload, error_response = await _read_json_object(request)
    if error_response is not None:
        return error_response

    assert payload is not None
    return {"status": "accepted", "note": "Runtime config updates are not yet implemented"}


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


def _content_type_for_path(path: str) -> str | None:
    """Determine content type from file extension using the allowlist only.

    Returns None if the extension is not in the allowlist.
    """
    for ext, ct in _EXTENSION_CONTENT_TYPES.items():
        if path.lower().endswith(ext):
            return ct
    return None


@app.get("/api/stream/{blob_path:path}", dependencies=[Depends(verify_auth)])
def stream_blob(blob_path: str):
    """Stream a blob from storage to the client.

    The UI calls this endpoint to play audio/video or display images
    without needing direct Azure Blob Storage credentials.
    """
    if not blob_path or blob_path.strip("/") == "":
        raise HTTPException(status_code=400, detail="blob_path must not be empty")

    content_type = _content_type_for_path(blob_path)
    if content_type is None or not any(content_type.startswith(prefix) for prefix in _STREAMABLE_PREFIXES):
        raise HTTPException(
            status_code=403,
            detail=f"Content type {content_type!r} is not streamable",
        )

    storage = get_storage()
    data = storage.get_bytes(blob_path)
    if data is None:
        raise HTTPException(status_code=404, detail="Blob not found")

    _CHUNK_SIZE = 64 * 1024

    def _iter_chunks():
        for offset in range(0, len(data), _CHUNK_SIZE):
            yield data[offset : offset + _CHUNK_SIZE]

    return StreamingResponse(
        _iter_chunks(),
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

    # Check synthesis_runner manifest shape (how audio is recorded in real runs)
    if not audio_path:
        synth = generation.get("synthesis_runner")
        if isinstance(synth, dict):
            audio_section = synth.get("audio")
            if isinstance(audio_section, dict):
                audio_path = audio_section.get("path")
                if not audio_path:
                    synth_artifacts = audio_section.get("artifacts")
                    if isinstance(synth_artifacts, dict):
                        mp3 = synth_artifacts.get("mp3")
                        if isinstance(mp3, dict):
                            audio_path = mp3.get("path")

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


@app.get("/api/episodes", response_model=EpisodeListResponse, dependencies=[Depends(verify_auth)])
def list_episodes(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    """List generated episodes that have audio artifacts."""
    storage = get_storage()
    _BLOB_LISTING_CAP = 10000
    blobs = storage.list_blobs("jobs/", limit=_BLOB_LISTING_CAP)
    if len(blobs) >= _BLOB_LISTING_CAP:
        logger.warning(
            "Episode listing hit the %d-blob cap; results may be incomplete. "
            "Consider implementing paged listing in StorageBackend.",
            _BLOB_LISTING_CAP,
        )
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


@app.get("/api/articles/{article_path:path}", dependencies=[Depends(verify_auth)])
def get_article(article_path: str):
    """Serve a markdown article from storage for UI preview."""
    if not article_path or article_path.strip("/") == "":
        raise HTTPException(status_code=400, detail="article_path must not be empty")

    # Restrict to articles/ prefix to prevent unintended data exposure
    normalized = article_path.lstrip("/")
    if not normalized.startswith("articles/"):
        raise HTTPException(
            status_code=403,
            detail="Only files under the articles/ prefix can be served via this endpoint",
        )
    # Prevent path traversal
    if ".." in normalized:
        raise HTTPException(status_code=403, detail="Path traversal is not allowed")

    _ARTICLE_EXTENSIONS = (".md", ".txt")
    if not any(article_path.lower().endswith(ext) for ext in _ARTICLE_EXTENSIONS):
        raise HTTPException(
            status_code=403,
            detail="Only markdown and text files can be served via this endpoint",
        )
    content_type = _content_type_for_path(article_path) or "text/plain"

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
