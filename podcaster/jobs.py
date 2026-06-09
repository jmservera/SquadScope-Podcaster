from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from podcaster.artifact_access import ACCESS_MODEL, artifact_access_metadata
from podcaster.generation import generate_artifacts, manifest_bytes, checksum
from podcaster.storage import StoredArtifact, StorageBackend, create_storage_backend
from podcaster.validation import RESPONSE_KEYS


@dataclass(frozen=True)
class JobResult:
    response: dict[str, Any]
    manifest: dict[str, Any]


def build_job_id(payload: dict[str, Any]) -> str:
    week = str(payload["week"]).strip()
    article_url = str(payload["article_url"]).strip()
    digest = hashlib.sha256(f"{week}|{article_url}".encode("utf-8")).hexdigest()[:12]
    safe_week = re.sub(r"[^A-Za-z0-9_.-]", "-", week)
    return f"podcast-{safe_week}-{digest}"


def run_generation_job(payload: dict[str, Any], storage: StorageBackend | None = None, now: datetime | None = None) -> JobResult:
    current = now or datetime.now(timezone.utc)
    expires_at = (current + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    job_id = build_job_id(payload)
    storage = storage or create_storage_backend()

    warnings = [
        "audio is a deterministic placeholder pending TTS implementation",
        "human review is required before publishing",
        "artifact URLs are private operator paths, not public publishing links",
    ]
    if payload.get("callback"):
        warnings.append("callback accepted by contract but not invoked yet")

    stored: dict[str, StoredArtifact] = {}
    checksums: dict[str, str] = {}
    for artifact in generate_artifacts(job_id, payload, current, expires_at):
        stored_artifact = storage.put_bytes(artifact.path, artifact.content, artifact.content_type)
        stored[artifact.path] = stored_artifact
        checksums[artifact.path] = checksum(artifact.content)

    created_at = current.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest_status = "dry_run" if payload.get("dry_run") else "review_pending"
    manifest = {
        "schema_version": "squadscope-podcaster-job-v1",
        "job_id": job_id,
        "status": manifest_status,
        "created_at": created_at,
        "expires_at": expires_at,
        "request": _request_metadata(payload),
        "lifecycle": _lifecycle_metadata(payload, created_at, manifest_status),
        "review": _review_metadata(payload),
        "generation": {
            "engine": "local-deterministic-placeholder",
            "deterministic": True,
            "audio_mode": "placeholder",
            "tts_provider": None,
            "tts_voice": None,
            "tts_synthesis": {
                "status": "blocked",
                "allowed": False,
                "blocked_by": ["human_review", "provider_not_selected"] if not payload.get("dry_run") else ["provider_not_selected"],
                "dry_run_bypass_allowed": bool(payload.get("dry_run")),
            },
        },
        "publishing": {
            "mode": "manual",
            "packet_ready": True,
            "eligible": False,
            "blocked_by": ["human_review", "real_tts_not_implemented"],
            "public_url": None,
        },
        "artifact_access": artifact_access_metadata(job_id, created_at, expires_at),
        "artifacts": {
            path: {
                "url": artifact.url,
                "access_model": ACCESS_MODEL,
                "publicly_accessible": False,
                "size_bytes": artifact.size_bytes,
                "content_type": artifact.content_type,
                "sha256": checksums[path],
            }
            for path, artifact in stored.items()
        },
        "observability": {
            "correlation_id": job_id,
            "log_schema_version": "2026-06-07",
            "safe_log_fields": ["job_id", "week", "status", "artifact_count", "dry_run"],
        },
        "warnings": warnings,
    }
    manifest_path = f"jobs/{job_id}/manifest.json"
    manifest_artifact = storage.put_bytes(manifest_path, manifest_bytes(manifest), "application/json; charset=utf-8")
    logging.info(
        "podcaster job staged job_id=%s status=%s dry_run=%s artifact_count=%s",
        job_id,
        manifest["status"],
        bool(payload.get("dry_run")),
        len(stored) + 1,
    )

    response = _response_from_artifacts(
        job_id=job_id,
        status="dry_run" if payload.get("dry_run") else "accepted",
        manifest_url=manifest_artifact.url,
        artifacts=stored,
        expires_at=expires_at,
        warnings=warnings,
    )
    return JobResult(response=response, manifest=manifest)


def failed_response(errors: list[str], warnings: list[str] | None = None) -> dict[str, Any]:
    return dict(
        zip(
            RESPONSE_KEYS,
            [None, "failed", None, None, None, None, None, None, None, warnings or [], errors],
            strict=True,
        )
    )


def _request_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    callback = payload.get("callback") if isinstance(payload.get("callback"), dict) else {}
    callback_url = callback.get("url") if isinstance(callback, dict) else None
    return {
        "week": payload.get("week"),
        "article_url": payload.get("article_url"),
        "article_sha256": payload.get("article_sha256"),
        "source_artifacts": payload.get("source_artifacts", []),
        "dry_run": bool(payload.get("dry_run")),
        "force": bool(payload.get("force")),
        "callback": {
            "requested": bool(payload.get("callback")),
            "url_host": urlparse(callback_url).netloc if isinstance(callback_url, str) else None,
            "secret_name_provided": bool(callback.get("secret_name")) if isinstance(callback, dict) else False,
        },
    }


def _lifecycle_metadata(payload: dict[str, Any], created_at: str, status: str) -> dict[str, Any]:
    transitions = [{"at": created_at, "to": "dry_run" if payload.get("dry_run") else "accepted", "reason": "request_validated"}]
    if not payload.get("dry_run"):
        transitions.append({"at": created_at, "to": "review_pending", "reason": "artifacts_staged"})
    return {"status": status, "revision": 1, "force": bool(payload.get("force")), "transitions": transitions}


def _review_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    dry_run = bool(payload.get("dry_run"))
    return {
        "required": True,
        "required_for_tts": not dry_run,
        "status": "pending",
        "mechanism": "github_environment",
        "environment": "podcast-review",
        "workflow": ".github/workflows/podcast-review-gate.yml",
        "approved_by": None,
        "approved_at": None,
        "audit_trail": [],
        "artifacts_for_review": [
            "script.txt",
            "claim-ledger.json",
            "transcript.txt",
            "show-notes.md",
            "review-checklist.md",
            "manifest.json",
            "publishing-packet.zip",
        ],
        "gate": {
            "status": "dry_run_bypass" if dry_run else "blocked",
            "approval_required_before": "non_dry_run_tts_synthesis",
            "checks": [
                "script_accuracy",
                "claim_verification",
                "citation_link_integrity",
                "transcript_readiness",
                "tts_readiness",
                "rights_attribution",
            ],
        },
    }


def _response_from_artifacts(
    *,
    job_id: str,
    status: str,
    manifest_url: str,
    artifacts: dict[str, StoredArtifact],
    expires_at: str,
    warnings: list[str],
) -> dict[str, Any]:
    def find(suffix: str) -> str | None:
        for path, artifact in artifacts.items():
            if path.endswith(suffix):
                return artifact.url
        return None

    return dict(
        zip(
            RESPONSE_KEYS,
            [
                job_id,
                status,
                manifest_url,
                find(".mp3"),
                None,
                find("transcript.txt"),
                find("show-notes.md"),
                find(".zip"),
                expires_at,
                warnings,
                [],
            ],
            strict=True,
        )
    )
