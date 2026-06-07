from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
WEEK_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

RESPONSE_KEYS = (
    "job_id",
    "status",
    "manifest_url",
    "mp3_url",
    "wav_url",
    "transcript_url",
    "show_notes_url",
    "publishing_packet_url",
    "expires_at",
    "warnings",
    "errors",
)


def expected_api_key() -> str | None:
    return os.environ.get("PODCASTER_API_KEY")


def is_authorized(headers: dict[str, str]) -> bool:
    configured = expected_api_key()
    if not configured:
        return True
    supplied = ""
    for key, value in headers.items():
        if key.lower() == "x-podcaster-api-key":
            supplied = value
            break
    return bool(supplied) and supplied == configured


def validate_payload(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["request body must be a JSON object"]

    week = payload.get("week")
    if not isinstance(week, str) or not week.strip():
        errors.append("week is required")
    elif not WEEK_RE.match(week):
        errors.append("week contains unsupported characters")

    article_url = payload.get("article_url")
    if not isinstance(article_url, str) or not article_url.strip():
        errors.append("article_url is required")
    elif urlparse(article_url).scheme not in {"http", "https"}:
        errors.append("article_url must be an http or https URL")

    article_sha256 = payload.get("article_sha256")
    if article_sha256 is not None:
        if not isinstance(article_sha256, str) or not SHA256_RE.match(article_sha256):
            errors.append("article_sha256 must be a lowercase hex SHA-256 digest")

    source_artifacts = payload.get("source_artifacts")
    if source_artifacts is not None:
        if not isinstance(source_artifacts, list) or not all(isinstance(item, str) for item in source_artifacts):
            errors.append("source_artifacts must be an array of strings")

    for field in ("dry_run", "force"):
        if field in payload and not isinstance(payload[field], bool):
            errors.append(f"{field} must be a boolean")

    callback = payload.get("callback")
    if callback is not None and not isinstance(callback, dict):
        errors.append("callback must be an object")
    elif isinstance(callback, dict):
        callback_url = callback.get("url")
        if callback_url is not None and (not isinstance(callback_url, str) or urlparse(callback_url).scheme not in {"http", "https"}):
            errors.append("callback.url must be an http or https URL")
        secret_name = callback.get("secret_name")
        if secret_name is not None and not isinstance(secret_name, str):
            errors.append("callback.secret_name must be a string")

    return errors


def empty_error_response(errors: list[str], warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "job_id": None,
        "status": "failed",
        "manifest_url": None,
        "mp3_url": None,
        "wav_url": None,
        "transcript_url": None,
        "show_notes_url": None,
        "publishing_packet_url": None,
        "expires_at": None,
        "warnings": warnings or [],
        "errors": errors,
    }


def build_stub_response(payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    week = str(payload["week"]).strip()
    article_url = str(payload["article_url"]).strip()
    digest = hashlib.sha256(f"{week}|{article_url}".encode("utf-8")).hexdigest()[:12]
    safe_week = re.sub(r"[^A-Za-z0-9_.-]", "-", week)
    job_id = f"podcast-{safe_week}-{digest}"
    base_url = os.environ.get("PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/podcaster-stub")
    base_url = base_url.rstrip("/")
    status = "dry_run" if payload.get("dry_run") else "accepted"
    expires_at = (current + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    warnings = ["stub response: generation, review, storage, and TTS are not implemented yet"]
    if payload.get("callback"):
        warnings.append("callback accepted by contract but not invoked by scaffold")

    return {
        "job_id": job_id,
        "status": status,
        "manifest_url": f"{base_url}/manifests/{job_id}.json",
        "mp3_url": f"{base_url}/audio/{job_id}.mp3",
        "wav_url": None,
        "transcript_url": f"{base_url}/transcripts/{job_id}.txt",
        "show_notes_url": f"{base_url}/show-notes/{job_id}.md",
        "publishing_packet_url": f"{base_url}/packets/{job_id}.zip",
        "expires_at": expires_at,
        "warnings": warnings,
        "errors": [],
    }
