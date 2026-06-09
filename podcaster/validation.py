from __future__ import annotations

import hmac
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
WEEK_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
SOURCE_ARTIFACT_OBJECT_FIELDS = {
    "artifact_checksum",
    "crawled_at",
    "exists",
    "freshness",
    "generated_at",
    "href",
    "name",
    "path",
    "provenance",
    "role",
    "same_day_reuse",
    "sha256",
    "size_bytes",
    "source_artifact_provenance",
    "source_config_checksum",
    "source_reuse_summary",
    "source_status",
    "sources_failed",
    "sources_requested",
    "sources_succeeded",
    "schema_checksum",
    "uri",
    "url",
    "week",
}

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
        return False
    supplied = ""
    for key, value in headers.items():
        if key.lower() == "x-podcaster-api-key":
            supplied = value
            break
    return bool(supplied) and hmac.compare_digest(supplied, configured)


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
        if not isinstance(source_artifacts, list):
            errors.append("source_artifacts must be an array of strings or source artifact objects")
        else:
            errors.extend(_validate_source_artifacts(source_artifacts))

    for field in ("dry_run", "force"):
        if field in payload and not isinstance(payload[field], bool):
            errors.append(f"{field} must be a boolean")

    cost_override = payload.get("cost_override")
    if cost_override is not None:
        if not isinstance(cost_override, dict):
            errors.append("cost_override must be an object")
        else:
            for field in ("actor", "reason", "recorded_at"):
                value = cost_override.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"cost_override.{field} is required")
            if payload.get("force") is not True:
                errors.append("cost_override requires force=true")

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


def _validate_source_artifacts(source_artifacts: list[Any]) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(source_artifacts):
        label = f"source_artifacts[{index}]"
        if isinstance(item, str):
            continue
        if not isinstance(item, dict):
            errors.append(f"{label} must be a string or source artifact object")
            continue
        errors.extend(_validate_source_artifact_object(label, item))
    return errors


def _validate_source_artifact_object(label: str, artifact: dict[Any, Any]) -> list[str]:
    errors: list[str] = []
    if not artifact:
        return [f"{label} must include path, url, href, uri, or name"]

    unknown_fields = sorted(str(key) for key in artifact if isinstance(key, str) and key not in SOURCE_ARTIFACT_OBJECT_FIELDS)
    if unknown_fields:
        errors.append(f"{label} contains unsupported fields: {', '.join(unknown_fields)}")

    reference_fields = ("path", "url", "href", "uri", "name")
    if not any(_is_non_empty_string(artifact.get(field)) for field in reference_fields):
        errors.append(f"{label} must include path, url, href, uri, or name")
    for url_field in ("url", "href", "uri"):
        value = artifact.get(url_field)
        if value is not None and (not isinstance(value, str) or urlparse(value).scheme not in {"http", "https"}):
            errors.append(f"{label}.{url_field} must be an http or https URL")

    for string_field in ("role", "path", "name", "week", "generated_at", "crawled_at", "source_status"):
        value = artifact.get(string_field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{label}.{string_field} must be a string")

    for digest_field in ("sha256", "artifact_checksum", "schema_checksum", "source_config_checksum"):
        value = artifact.get(digest_field)
        if value is not None and (not isinstance(value, str) or not SHA256_RE.match(value)):
            errors.append(f"{label}.{digest_field} must be a lowercase hex SHA-256 digest")

    exists = artifact.get("exists")
    if exists is not None and not isinstance(exists, bool):
        errors.append(f"{label}.exists must be a boolean")

    size_bytes = artifact.get("size_bytes")
    if size_bytes is not None and (not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0):
        errors.append(f"{label}.size_bytes must be a non-negative integer")

    for object_field in (
        "freshness",
        "provenance",
        "same_day_reuse",
        "source_artifact_provenance",
        "source_reuse_summary",
    ):
        value = artifact.get(object_field)
        if value is not None and not isinstance(value, dict):
            errors.append(f"{label}.{object_field} must be an object")

    for array_field in ("sources_requested", "sources_succeeded", "sources_failed"):
        value = artifact.get(array_field)
        if value is not None and not isinstance(value, list):
            errors.append(f"{label}.{array_field} must be an array")

    return errors


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
    from pathlib import Path

    from podcaster.jobs import run_generation_job
    from podcaster.storage import LocalStorageBackend

    storage = LocalStorageBackend(
        root=Path(os.environ.get("PODCASTER_LOCAL_STORAGE_PATH", ".podcaster-artifacts")),
        base_url=os.environ.get("PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/podcaster-stub"),
    )
    return run_generation_job(payload, storage=storage, now=now).response
