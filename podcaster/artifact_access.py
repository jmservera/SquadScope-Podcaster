from __future__ import annotations

from typing import Any

ACCESS_MODEL = "private_operator_path"
ACCESS_SCHEMA_VERSION = "squadscope-podcaster-artifact-access-v1"


def artifact_access_metadata(job_id: str, created_at: str, expires_at: str) -> dict[str, Any]:
    return {
        "schema_version": ACCESS_SCHEMA_VERSION,
        "model": ACCESS_MODEL,
        "created_at": created_at,
        "expires_at": expires_at,
        "response_urls": {
            "publicly_accessible": False,
            "requires_operator_credentials": True,
            "signed_urls": False,
            "query_strings_allowed": False,
            "credential_material_allowed": False,
        },
        "operator_access": {
            "method": "Azure RBAC or local filesystem access",
            "azure_identity": "Function App managed identity writes artifacts; operators read with explicitly granted storage permissions.",
            "local_development": "PODCASTER_ARTIFACT_BASE_URL is a development locator only, not a public distribution URL.",
        },
        "retention": {
            "temporary": True,
            "expires_at": expires_at,
            "cleanup_after": expires_at,
            "cleanup_owner": "operator_or_storage_lifecycle_policy",
        },
        "audit": {
            "correlation_id": job_id,
            "safe_log_fields": ["job_id", "week", "status", "artifact_count", "dry_run"],
            "trail": "Use the job manifest, review audit trail, Application Insights correlation_id, and Azure Storage diagnostics for access review.",
        },
        "publication": {
            "eligible": False,
            "blocked_by": ["human_review", "real_tts_not_implemented"],
        },
    }
