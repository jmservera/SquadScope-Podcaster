"""FastAPI API server for podcaster monitoring and generation endpoints.

Exposes read-only endpoints to list, inspect, and retrieve logs for
pipeline jobs stored in the storage backend. Also provides a streaming
proxy for media files so the UI can play audio/video without direct
storage credentials.

Endpoints:
  POST /api/generate              — validate payload and enqueue generation
  POST /api/review                — process review decisions
  GET /api/jobs                   — list recent jobs (paginated)
  GET /api/jobs/{id}              — job detail (manifest + derived status)
  GET /api/jobs/{id}/logs         — job logs (structured + runner state), level/search filter
  GET /api/jobs/{id}/progress     — poll real-time progress events (issue #469)
  GET /api/jobs/{id}/progress/stream — SSE stream of progress events (issue #469)
  POST /api/jobs/{id}/video/generate — manually enqueue a video job
  GET /api/stream/{path}          — stream blob content (audio/video/images)
  GET /api/episodes               — list generated episodes with metadata
  GET /api/articles/{path}        — serve markdown articles for preview
  GET /api/credentials            — list credentials (summaries, no secrets)
  POST /api/credentials           — create a credential
  PUT /api/credentials/{id}       — update a credential
  DELETE /api/credentials/{id}    — delete a credential
  GET /api/podcast-config         — get podcast configuration
  POST /api/podcast-config        — save podcast configuration
  GET/POST /api/config            — runtime config inspection placeholder
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from podcaster.auth import (
    _STREAM_TOKEN_EXPIRY_SECONDS,
    LoginRequest,
    LoginResponse,
    MeResponse,
    create_scoped_token,
    create_token,
    get_credentials,
    verify_auth,
    verify_scoped_query_access,
    verify_token,
)
from podcaster.credentials import CredentialStore
from podcaster.failure_reporting import report_failure
from podcaster.job_logs import LogLevel, read_logs
from podcaster.jobs import ReplayCollisionError, failed_response, run_generation_job
from podcaster.orchestration import process_review_decision
from podcaster.podcast_config import PodcastConfigStore
from podcaster.progress import (
    filter_events_since,
    is_terminal,
    read_progress,
)
from podcaster.queue import enqueue_video_job
from podcaster.stage_progress import summarize as summarize_stage_progress
from podcaster.storage import StorageBackend, create_storage_backend
from podcaster.validation import validate_payload_details

logger = logging.getLogger(__name__)

app = FastAPI(title="Podcaster Job Monitor", version="0.1.0")

MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MiB

# CORS is deny-by-default. Set MONITORING_CORS_ORIGINS to a comma-separated
# allowlist of trusted UI origins (e.g. "https://ui.example.com") to enable
# cross-origin browser access. A literal "*" is rejected: wildcard CORS on
# authenticated credential/generation endpoints is a security risk (#607).
_CORS_ORIGINS_RAW = os.environ.get("MONITORING_CORS_ORIGINS", "")


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


_CORS_ORIGINS = [
    origin.strip()
    for origin in _CORS_ORIGINS_RAW.split(",")
    if origin.strip() and origin.strip() != "*" and not _has_control_chars(origin.strip())
]
if "*" in (o.strip() for o in _CORS_ORIGINS_RAW.split(",")):
    logging.getLogger("podcaster.monitoring").warning(
        "MONITORING_CORS_ORIGINS contains '*'; wildcard CORS is not permitted and "
        "will be ignored. Configure an explicit origin allowlist (#607)."
    )

if _CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "x-podcaster-api-key"],
    )


@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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
    level: str = LogLevel.INFO
    event: str
    message: str | None = None
    detail: str | None = None
    task_id: str | None = None
    stage: str | None = None
    seq: int | None = None
    source: str = "manifest"


class JobLogsResponse(BaseModel):
    job_id: str
    logs: list[LogEntry]
    total: int = 0
    level: str | None = None
    search: str | None = None


class ProgressResponse(BaseModel):
    """Polling snapshot of a job's real-time progress (issue #469).

    ``current`` is the latest progress event (without its ``seq``); ``events``
    are the events newer than the requested ``since`` cursor; ``last_seq`` is the
    cursor to pass on the next poll; ``terminal`` is true once the job reached a
    completed/failed stage and no further events are expected.
    """

    job_id: str
    current: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    last_seq: int = 0
    terminal: bool = False


class StageProgressResponse(BaseModel):
    """Stage-progress summary with ETA for an in-flight job (issue #470).

    Derived from the durable progress stream (#469): the current pipeline
    ``stage``, the segment counter (``segment_index``/``segment_total``, e.g.
    "recording 12/18"), the ``phase``, a derived completion ``percent``, and an
    ``eta`` extrapolated from observed segment timings.  ``phase`` is ``pending``
    when no progress has been reported yet.
    """

    job_id: str
    stage: str | None = None
    phase: str | None = "pending"
    segment_index: int | None = None
    segment_total: int | None = None
    percent: float | None = None
    message: str | None = None
    updated_at: str | None = None
    terminal: bool = False
    eta: str | None = None
    eta_seconds: float | None = None


class JobAsset(BaseModel):
    """A streamable media artifact produced for a job (issue #471).

    ``url`` points at the authenticated streaming proxy (``/api/stream/...``).
    Note this is **not** a SAS URL: the proxy is gated by the standard Podcaster
    bearer token (``verify_auth``) on every request and is not an
    artifact-scoped capability — any caller holding a valid token can stream any
    blob path. Bytes are proxied through the API rather than served directly
    from storage. If a short-lived, per-artifact access model is required, mint
    SAS URLs (or add a dedicated SAS issuance endpoint) instead.
    """

    name: str
    path: str
    url: str
    content_type: str | None = None
    kind: str  # "video" | "audio" | "image"


class JobAssetsResponse(BaseModel):
    """Per-job asset listing for the UI asset browser (issue #471)."""

    job_id: str
    assets: list[JobAsset] = []
    total: int = 0


# Server-sent-events stream tuning. The loop polls the durable store rather than
# holding in-memory state so it is correct on stateless/serverless ACA workers.
_SSE_POLL_SECONDS = float(os.environ.get("MONITORING_SSE_POLL_SECONDS", "1.0"))
_SSE_HEARTBEAT_SECONDS = float(os.environ.get("MONITORING_SSE_HEARTBEAT_SECONDS", "15.0"))
_SSE_MAX_SECONDS = float(os.environ.get("MONITORING_SSE_MAX_SECONDS", "1800.0"))


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
        article_title=(
            request.get("article_title") if isinstance(request.get("article_title"), str) else None
        ),
    )


def _extract_detail(manifest: dict[str, Any]) -> JobDetailResponse:
    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    generation = (
        manifest.get("generation") if isinstance(manifest.get("generation"), dict) else None
    )

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
        article_url=(
            request.get("article_url") if isinstance(request.get("article_url"), str) else None
        ),
        article_title=(
            request.get("article_title") if isinstance(request.get("article_title"), str) else None
        ),
        generation=generation,
        publishing=(
            manifest.get("publishing") if isinstance(manifest.get("publishing"), dict) else None
        ),
        lifecycle=(
            manifest.get("lifecycle") if isinstance(manifest.get("lifecycle"), dict) else None
        ),
        quality_score=quality_score,
        warnings=manifest.get("warnings") if isinstance(manifest.get("warnings"), list) else None,
    )


def _derive_level(event: str, detail: str | None) -> str:
    """Infer a severity level for a manifest-derived log entry."""
    haystack = f"{event} {detail or ''}".lower()
    if any(token in haystack for token in ("failed", "error", "empty_audio")):
        return LogLevel.ERROR
    if any(token in haystack for token in ("warning", "warn", "skipped", "drift")):
        return LogLevel.WARNING
    return LogLevel.INFO


def _extract_logs(manifest: dict[str, Any]) -> list[LogEntry]:
    """Extract log-like entries from lifecycle transitions and runner state."""
    logs: list[LogEntry] = []

    def _add(
        timestamp: str | None,
        event: str,
        detail: str | None,
        stage: str | None = None,
    ) -> None:
        logs.append(
            LogEntry(
                timestamp=timestamp,
                level=_derive_level(event, detail),
                event=event,
                message=detail,
                detail=detail,
                stage=stage,
                source="manifest",
            )
        )

    # Lifecycle transitions
    lifecycle = manifest.get("lifecycle")
    if isinstance(lifecycle, dict):
        transitions = lifecycle.get("transitions")
        if isinstance(transitions, list):
            for t in transitions:
                if isinstance(t, dict):
                    _add(t.get("at"), f"transition:{t.get('to', 'unknown')}", t.get("reason"))

    # Synthesis runner state
    generation = manifest.get("generation")
    if isinstance(generation, dict):
        runner = generation.get("synthesis_runner")
        if isinstance(runner, dict):
            _add(
                runner.get("completed_at") or runner.get("at"),
                f"synthesis:{runner.get('status', 'unknown')}",
                runner.get("reason"),
                stage="synthesis",
            )

        # Synthesis queue state
        queue = generation.get("synthesis_queue")
        if isinstance(queue, dict):
            _add(
                queue.get("enqueued_at"),
                f"queue:{queue.get('status', 'unknown')}",
                queue.get("detail"),
            )

    return logs


def _structured_logs(document: dict[str, Any] | None) -> list[LogEntry]:
    """Convert a durable structured-log document into ``LogEntry`` items."""
    if not document:
        return []
    records = document.get("records")
    if not isinstance(records, list):
        return []
    entries: list[LogEntry] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        message = record.get("message")
        stage = record.get("stage")
        entries.append(
            LogEntry(
                timestamp=record.get("at"),
                level=LogLevel.normalize(record.get("level")),
                event=str(stage or record.get("level") or "log"),
                message=str(message) if message is not None else None,
                detail=str(message) if message is not None else None,
                task_id=record.get("task_id"),
                stage=stage if isinstance(stage, str) else None,
                seq=record.get("seq") if isinstance(record.get("seq"), int) else None,
                source="structured",
            )
        )
    return entries


def _filter_log_entries(
    entries: list[LogEntry], *, level: str | None, search: str | None
) -> list[LogEntry]:
    """Apply minimum-severity and case-insensitive substring filtering."""
    min_rank = LogLevel.rank(level) if level else None
    needle = search.strip().lower() if isinstance(search, str) and search.strip() else None

    out: list[LogEntry] = []
    for entry in entries:
        if min_rank is not None and LogLevel.rank(entry.level) < min_rank:
            continue
        if needle is not None:
            haystack = " ".join(
                str(part)
                for part in (entry.event, entry.message, entry.detail, entry.task_id, entry.stage)
                if part
            ).lower()
            if needle not in haystack:
                continue
        out.append(entry)
    return out


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
    except ReplayCollisionError:
        response = failed_response(["replay output already exists"])
        return JSONResponse(status_code=409, content=response)
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
    reviewed_at = str(payload.get("reviewed_at") or "").strip() or datetime.now(
        timezone.utc
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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


@app.get(
    "/api/jobs/{job_id}",
    response_model=JobDetailResponse,
    dependencies=[Depends(verify_auth)],
)
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


@app.get(
    "/api/jobs/{job_id}/logs",
    response_model=JobLogsResponse,
    dependencies=[Depends(verify_auth)],
)
def get_job_logs(
    job_id: str,
    level: str | None = Query(
        default=None,
        description="Minimum severity: debug|info|warning|error",
    ),
    search: str | None = Query(default=None, description="Case-insensitive substring filter"),
):
    """Get log entries for a job, merging durable structured logs (#472) with
    manifest-derived lifecycle/runner state.

    Supports minimum-severity filtering via ``level`` (e.g. ``warning`` returns
    warnings and errors) and free-text ``search`` across event/message/task/stage.
    Entries are ordered by timestamp, then ``seq`` for stable ordering of
    structured records sharing a timestamp.
    """
    storage = get_storage()
    manifest_path = f"jobs/{job_id}/manifest.json"
    raw = storage.get_bytes(manifest_path)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    manifest = _parse_manifest(raw)
    if manifest is None:
        raise HTTPException(status_code=500, detail="Manifest is corrupt")

    # Treat blank/whitespace-only params as "not provided" so `?level=%20`
    # does not silently apply the `info` default and drop `debug` entries, and
    # the echoed-back filters reflect what was actually applied (#472 review).
    normalized_level = (
        LogLevel.normalize(level) if isinstance(level, str) and level.strip() else None
    )
    normalized_search = search.strip() if isinstance(search, str) and search.strip() else None

    entries = _extract_logs(manifest)
    entries.extend(_structured_logs(read_logs(storage, job_id)))
    entries.sort(key=lambda e: (e.timestamp or "", e.seq if e.seq is not None else 0))

    filtered = _filter_log_entries(entries, level=normalized_level, search=normalized_search)
    return JobLogsResponse(
        job_id=job_id,
        logs=filtered,
        total=len(filtered),
        level=normalized_level,
        search=normalized_search,
    )


def _job_exists(storage: StorageBackend, job_id: str) -> bool:
    return storage.blob_exists(f"jobs/{job_id}/manifest.json")


@app.get(
    "/api/jobs/{job_id}/progress",
    response_model=ProgressResponse,
    dependencies=[Depends(verify_auth)],
)
def get_job_progress(job_id: str, since: int = Query(default=0, ge=0)):
    """Poll real-time progress events for a job (issue #469).

    Returns events with ``seq > since`` plus the latest ``current`` snapshot.
    Acts as the polling fallback to the SSE stream and works on stateless ACA
    workers because it reads the durable per-job progress document. The job must
    exist (have a manifest); a job with no progress yet returns an empty list.
    """
    storage = get_storage()
    if not _job_exists(storage, job_id):
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    document = read_progress(storage, job_id)
    if document is None:
        return ProgressResponse(
            job_id=job_id,
            current=None,
            events=[],
            last_seq=since,
            terminal=False,
        )

    new_events = filter_events_since(document.get("events") or [], since)
    last_seq = new_events[-1]["seq"] if new_events else since
    return ProgressResponse(
        job_id=job_id,
        current=document.get("current") if isinstance(document.get("current"), dict) else None,
        events=new_events,
        last_seq=last_seq,
        terminal=is_terminal(document),
    )


@app.get("/api/progress-token", dependencies=[Depends(verify_auth)])
def mint_progress_token(job_id: str = Query(...)):
    """Mint a short-lived query token for the SSE progress stream."""
    storage = get_storage()
    if not _job_exists(storage, job_id):
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    creds = get_credentials()
    if creds is None:
        return {"token": "", "expires_in": 0}
    return {
        "token": create_scoped_token(creds[2], scope="progress", resource=job_id),
        "expires_in": _STREAM_TOKEN_EXPIRY_SECONDS,
    }


@app.get(
    "/api/jobs/{job_id}/progress/summary",
    response_model=StageProgressResponse,
    dependencies=[Depends(verify_auth)],
)
def get_job_progress_summary(job_id: str):
    """Stage-progress summary with segment N/M, phase and ETA (issue #470).

    A higher-level view over the #469 event stream: the current pipeline stage,
    the segment counter, the phase, a derived completion percent, and an ETA
    extrapolated from observed segment timings.  The job must exist (have a
    manifest); a job with no progress yet returns a ``pending`` summary.
    """
    storage = get_storage()
    if not _job_exists(storage, job_id):
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    document = read_progress(storage, job_id)
    summary = summarize_stage_progress(document)
    return StageProgressResponse(job_id=job_id, **summary)


_ASSET_KIND_ORDER = {"video": 0, "audio": 1, "image": 2}


def _asset_kind(content_type: str | None) -> str | None:
    """Map a streamable content type to a coarse asset kind, else None."""
    if not content_type:
        return None
    for kind in ("video", "audio", "image"):
        if content_type.startswith(f"{kind}/"):
            return kind
    return None


@app.get(
    "/api/jobs/{job_id}/assets",
    response_model=JobAssetsResponse,
    dependencies=[Depends(verify_auth)],
)
def list_job_assets(job_id: str):
    """List the streamable media assets (video/audio/thumbnails) for a job (#471).

    Discovers every media blob under ``jobs/{job_id}/`` and returns playable
    URLs via the authenticated streaming proxy (``/api/stream/...``). These are
    authenticated proxy URLs gated by the standard Podcaster bearer token on
    every request — **not** SAS URLs and not artifact-scoped capabilities. The
    job must exist (have a manifest); a job with no media yet returns an empty
    list.
    """
    storage = get_storage()
    if not _job_exists(storage, job_id):
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    prefix = f"jobs/{job_id}/"
    blobs = storage.list_blobs(prefix, limit=10000)

    assets: list[JobAsset] = []
    seen: set[str] = set()
    for path in blobs:
        if path in seen:
            continue
        content_type = _content_type_for_path(path)
        kind = _asset_kind(content_type)
        if kind is None:
            continue  # skip manifests, logs, progress docs and other non-media
        seen.add(path)
        name = path[len(prefix) :] if path.startswith(prefix) else path.rsplit("/", 1)[-1]
        assets.append(
            JobAsset(
                name=name,
                path=path,
                url=f"/api/stream/{path}",
                content_type=content_type,
                kind=kind,
            )
        )

    assets.sort(key=lambda a: (_ASSET_KIND_ORDER.get(a.kind, 9), a.name))
    return JobAssetsResponse(job_id=job_id, assets=assets, total=len(assets))


def _sse_pack(event_id: int | None, data: dict[str, Any]) -> str:
    chunk = ""
    if event_id is not None:
        chunk += f"id: {event_id}\n"
    chunk += f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    return chunk


async def _progress_event_stream(job_id: str, since: int) -> AsyncIterator[str]:
    """Yield server-sent events for a job's progress until it terminates.

    Polls the durable store every ``_SSE_POLL_SECONDS``; emits a heartbeat
    comment when idle so proxies keep the connection open; stops once the job
    reaches a terminal stage or ``_SSE_MAX_SECONDS`` elapses. Reading the durable
    document each tick keeps the stream correct across worker restarts.
    """
    storage = get_storage()
    cursor = since
    loop = asyncio.get_running_loop()
    started = loop.time()
    last_emit = started

    # Initial catch-up so a late subscriber immediately sees prior events.
    document = read_progress(storage, job_id)
    if document is not None:
        for event in filter_events_since(document.get("events") or [], cursor):
            cursor = event["seq"]
            yield _sse_pack(cursor, event)
            last_emit = loop.time()
        if is_terminal(document):
            yield ": end\n\n"
            return

    while True:
        if loop.time() - started > _SSE_MAX_SECONDS:
            yield ": timeout\n\n"
            return
        await asyncio.sleep(_SSE_POLL_SECONDS)

        document = read_progress(storage, job_id)
        new_events = (
            filter_events_since(document.get("events") or [], cursor)
            if document is not None
            else []
        )
        if new_events:
            for event in new_events:
                cursor = event["seq"]
                yield _sse_pack(cursor, event)
            last_emit = loop.time()
            if is_terminal(document):
                yield ": end\n\n"
                return
        elif loop.time() - last_emit >= _SSE_HEARTBEAT_SECONDS:
            yield ": keep-alive\n\n"
            last_emit = loop.time()


@app.get("/api/jobs/{job_id}/progress/stream")
async def stream_job_progress(
    request: Request,
    job_id: str,
    since: int = Query(default=0, ge=0),
):
    """Stream real-time progress for a job over Server-Sent Events (issue #469).

    Preferred transport for the observability UI: a single long-lived HTTP
    response over the FastAPI monitoring API (simplest to run on ACA). The UI
    subscribes with ``EventSource`` and receives one ``data:`` line per progress
    event; reconnects can resume from the last ``id`` via the ``since`` query
    parameter. Backed by the same durable store as the polling endpoint.
    """
    verify_scoped_query_access(request, scope="progress", resource=job_id)
    storage = get_storage()
    if not _job_exists(storage, job_id):
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    return StreamingResponse(
        _progress_event_stream(job_id, since),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/jobs/{job_id}/video/generate", dependencies=[Depends(verify_auth)])
def enqueue_video(job_id: str):
    """Manually enqueue a video-generation message for an existing job.

    Useful for retriggering video composition without a full re-synthesis.
    The job must already exist (have a manifest). Returns 503 when the video
    queue is not configured.
    """
    storage = get_storage()
    manifest_path = f"jobs/{job_id}/manifest.json"
    raw = storage.get_bytes(manifest_path)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    manifest = _parse_manifest(raw)
    if manifest is None:
        raise HTTPException(status_code=500, detail="Manifest is corrupt")

    try:
        enqueued = enqueue_video_job(job_id)
    except Exception:
        logger.warning("manual video enqueue failed job_id=%s", job_id, exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to enqueue video job")

    if not enqueued:
        raise HTTPException(
            status_code=503,
            detail="Video queue is not configured (PODCASTER_STORAGE_QUEUE_URL unset)",
        )

    logger.info("manual video enqueue succeeded job_id=%s", job_id)
    return {"job_id": job_id, "enqueued": True}


@app.get("/healthz")
def healthz():
    return {"status": "healthy"}


def _get_credential_store() -> CredentialStore:
    """Build a CredentialStore using UI_AUTH_SECRET for Fernet encryption."""
    secret = os.environ.get("UI_AUTH_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=501,
            detail="UI_AUTH_SECRET is required for credential encryption",
        )
    return CredentialStore(get_storage(), secret=secret)


@app.get("/api/credentials", dependencies=[Depends(verify_auth)])
def api_credentials_list():
    """List stored credentials (summaries only, no secret values)."""
    store = _get_credential_store()
    return store.list_credentials()


@app.post("/api/credentials", dependencies=[Depends(verify_auth)])
async def api_credentials_create(request: Request):
    """Create a new credential entry."""
    payload, error_response = await _read_json_object(request)
    if error_response is not None:
        return error_response
    assert payload is not None
    store = _get_credential_store()
    try:
        summary = store.create_credential(payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=summary)


@app.put("/api/credentials/{credential_id}", dependencies=[Depends(verify_auth)])
async def api_credentials_update(credential_id: str, request: Request):
    """Update an existing credential entry."""
    payload, error_response = await _read_json_object(request)
    if error_response is not None:
        return error_response
    assert payload is not None
    store = _get_credential_store()
    try:
        summary = store.update_credential(credential_id, payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if summary is None:
        raise HTTPException(status_code=404, detail="credential not found")
    return JSONResponse(status_code=200, content=summary)


@app.delete("/api/credentials/{credential_id}", dependencies=[Depends(verify_auth)])
def api_credentials_delete(credential_id: str):
    """Delete a credential entry."""
    store = _get_credential_store()
    deleted = store.delete_credential(credential_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="credential not found")
    return Response(status_code=204)


@app.get("/api/podcast-config", dependencies=[Depends(verify_auth)])
def api_podcast_config_get():
    """Return podcast configuration."""
    store = PodcastConfigStore(get_storage())
    return store.get()


@app.post("/api/podcast-config", dependencies=[Depends(verify_auth)])
async def api_podcast_config_save(request: Request):
    """Save podcast configuration."""
    payload, error_response = await _read_json_object(request)
    if error_response is not None:
        return error_response
    assert payload is not None
    store = PodcastConfigStore(get_storage())
    try:
        document = store.save(payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=document)


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


# Blob paths reaching the streaming proxy are caller-controlled. Validate them
# at the API boundary against a strict allowlist and reject path traversal
# before the value is used to address storage, so a request can never escape
# the storage namespace (defense-in-depth over the backend's own sanitizer).
_SAFE_BLOB_PATH_RE = re.compile(r"[A-Za-z0-9._/\-]+")


def _require_safe_blob_path(path: str) -> str:
    segments = path.split("/")
    if (
        not _SAFE_BLOB_PATH_RE.fullmatch(path)
        or path.startswith("/")
        or any(segment in ("", ".", "..") for segment in segments)
    ):
        raise HTTPException(status_code=400, detail="Invalid blob path")
    return path


@app.get("/api/stream-token", dependencies=[Depends(verify_auth)])
def mint_stream_token(path: str = Query(...)):
    """Mint a short-lived query token for a single streamable blob."""
    path = _require_safe_blob_path(path)
    content_type = _content_type_for_path(path)
    if content_type is None or not any(
        content_type.startswith(prefix) for prefix in _STREAMABLE_PREFIXES
    ):
        raise HTTPException(
            status_code=403,
            detail=f"Content type {content_type!r} is not streamable",
        )

    storage = get_storage()
    if storage.get_bytes(path) is None:
        raise HTTPException(status_code=404, detail="Blob not found")

    creds = get_credentials()
    if creds is None:
        return {"token": "", "expires_in": 0}
    return {
        "token": create_scoped_token(creds[2], scope="stream", resource=path),
        "expires_in": _STREAM_TOKEN_EXPIRY_SECONDS,
    }


@app.get("/api/stream/{blob_path:path}")
def stream_blob(request: Request, blob_path: str):
    """Stream a blob from storage to the client.

    The UI calls this endpoint to play audio/video or display images
    without needing direct Azure Blob Storage credentials.
    """
    verify_scoped_query_access(request, scope="stream", resource=blob_path)

    if not blob_path or blob_path.strip("/") == "":
        raise HTTPException(status_code=400, detail="blob_path must not be empty")

    blob_path = _require_safe_blob_path(blob_path)

    content_type = _content_type_for_path(blob_path)
    if content_type is None or not any(
        content_type.startswith(prefix) for prefix in _STREAMABLE_PREFIXES
    ):
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


class EpisodeArtifact(BaseModel):
    """A downloadable artifact file produced for an episode."""

    name: str
    path: str
    url: str
    content_type: str | None = None


class EpisodeSummary(BaseModel):
    job_id: str
    title: str | None = None
    created_at: str | None = None
    status: str = "unknown"
    audio_path: str | None = None
    audio_url: str | None = None
    video_path: str | None = None
    video_url: str | None = None
    quality_score: float | None = None
    publish_status: str | None = None
    artifacts: list[EpisodeArtifact] = []


class EpisodeListResponse(BaseModel):
    episodes: list[EpisodeSummary]
    total: int


def _normalize_blob_path(value: str) -> str:
    """Normalize a blob reference to a relative path suitable for /api/stream/.

    ``video_runner.distribution.blob_path`` may be recorded as a full URL
    (e.g. ``https://account.blob.core.windows.net/container/path/file.mp4``)
    rather than a relative blob path. Strip any scheme/host prefix and the
    leading container segment so only the container-relative blob path remains,
    matching the format used for audio and other artifacts.
    """
    stripped = value.strip()
    match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+/(.*)$", stripped)
    if match:
        stripped = match.group(1)
        container = os.environ.get("PODCASTER_STORAGE_CONTAINER", "podcaster-artifacts")
        prefix = f"{container}/"
        if container and stripped.startswith(prefix):
            stripped = stripped[len(prefix) :]
    return stripped.lstrip("/")


def _extract_video_path(generation: dict[str, Any]) -> str | None:
    """Resolve the generated video (.mp4) blob path from a manifest's generation block."""
    video_runner = generation.get("video_runner")
    if isinstance(video_runner, dict):
        distribution = video_runner.get("distribution")
        if isinstance(distribution, dict):
            blob_path = distribution.get("blob_path")
            if isinstance(blob_path, str) and blob_path:
                return _normalize_blob_path(blob_path)

    artifacts = generation.get("artifacts")
    if isinstance(artifacts, dict):
        video = artifacts.get("video")
        if isinstance(video, dict):
            path = video.get("path")
            if isinstance(path, str) and path:
                return path
        elif isinstance(video, str) and video:
            return video

    return None


def _collect_artifacts(generation: dict[str, Any], exclude: set[str]) -> list[EpisodeArtifact]:
    """Collect extra downloadable artifact files (e.g. wav, images), excluding
    the primary audio/video already surfaced via dedicated players."""
    items: list[EpisodeArtifact] = []
    seen = {p for p in exclude if p}

    def _add(path: Any, name: str | None = None) -> None:
        if not isinstance(path, str) or not path or path in seen:
            return
        seen.add(path)
        items.append(
            EpisodeArtifact(
                name=name or path.rsplit("/", 1)[-1],
                path=path,
                url=f"/api/stream/{path}",
                content_type=_content_type_for_path(path),
            )
        )

    def _add_mapping(mapping: Any) -> None:
        if not isinstance(mapping, dict):
            return
        for key, val in mapping.items():
            if isinstance(val, dict):
                _add(val.get("path"), key)
            elif isinstance(val, str):
                _add(val, key)

    _add_mapping(generation.get("artifacts"))

    synth = generation.get("synthesis_runner")
    if isinstance(synth, dict):
        audio_section = synth.get("audio")
        if isinstance(audio_section, dict):
            _add_mapping(audio_section.get("artifacts"))

    return items


def _extract_episode(manifest: dict[str, Any]) -> EpisodeSummary | None:
    """Extract episode summary from a manifest. Returns None if no audio."""
    generation = (
        manifest.get("generation") if isinstance(manifest.get("generation"), dict) else None
    )
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
        audio_path = (
            generation.get("audio_file") if isinstance(generation.get("audio_file"), str) else None
        )

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
    publishing = (
        manifest.get("publishing") if isinstance(manifest.get("publishing"), dict) else None
    )

    video_path = _extract_video_path(generation)
    artifacts = _collect_artifacts(generation, exclude={audio_path, video_path or ""})

    quality_score: float | None = None
    if isinstance(generation.get("audio_validation"), dict):
        av = generation["audio_validation"]
        if av.get("status") == "passed":
            quality_score = 1.0
        elif av.get("status") == "placeholder":
            quality_score = 0.0

    return EpisodeSummary(
        job_id=manifest.get("job_id", ""),
        title=(
            request.get("article_title") if isinstance(request.get("article_title"), str) else None
        ),
        created_at=manifest.get("created_at"),
        status=manifest.get("status", "unknown"),
        audio_path=audio_path,
        audio_url=f"/api/stream/{audio_path}" if audio_path else None,
        video_path=video_path,
        video_url=f"/api/stream/{video_path}" if video_path else None,
        quality_score=quality_score,
        publish_status=publishing.get("status") if publishing else None,
        artifacts=artifacts,
    )


@app.get(
    "/api/episodes",
    response_model=EpisodeListResponse,
    dependencies=[Depends(verify_auth)],
)
def list_episodes(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
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
