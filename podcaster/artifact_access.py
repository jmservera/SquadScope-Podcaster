from __future__ import annotations

from typing import Any

from podcaster.storage import SignedDownloadUrl

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
            "azure_identity": (
                "ACA managed identity writes artifacts; operators read with explicitly granted "
                "storage permissions."
            ),
            "local_development": (
                "PODCASTER_ARTIFACT_BASE_URL is a development locator only, not a public "
                "distribution URL."
            ),
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
            "trail": (
                "Use the job manifest, review audit trail, Application Insights correlation_id, "
                "and Azure Storage diagnostics for access review."
            ),
        },
        "publication": {
            "eligible": False,
            "blocked_by": ["human_review", "synthesis_not_completed"],
        },
    }


def sas_download_record(signed: SignedDownloadUrl, *, include_url: bool = True) -> dict[str, Any]:
    """Describe one time-limited download URL for the operator review manifest.

    The record always documents *how* the URL is signed and when it expires.
    The ``url`` field is included only when ``include_url`` is true and the URL
    is actually signed — it is a short-lived secret and is therefore omitted
    from any artifact that is itself uploaded to shared storage or committed.
    """

    record: dict[str, Any] = {
        "path": signed.path,
        "method": signed.method,
        "signed": signed.signed,
        "https_only": signed.https_only,
        "account_key_used": signed.account_key_used,
        "expires_at": signed.expires_at,
        "is_secret": signed.signed,
    }
    if include_url and signed.signed:
        record["url"] = signed.url
    return record


def operator_download_access_metadata(generated_at: str, expires_at: str) -> dict[str, Any]:
    """Artifact-access metadata for the operator REVIEW download path.

    Unlike :func:`artifact_access_metadata` (the private-operator-path model
    used by the public job pipeline), the operator review flow deliberately
    hands the reviewer a signed, time-limited *user-delegation* SAS so they can
    click-and-listen. It still uses no account keys and keeps publication
    human-gated.
    """

    return {
        "schema_version": ACCESS_SCHEMA_VERSION,
        "model": "operator_review_signed_download",
        "created_at": generated_at,
        "expires_at": expires_at,
        "response_urls": {
            "publicly_accessible": False,
            "requires_operator_credentials": False,
            "signed_urls": True,
            "signing_method": "azure_ad_user_delegation_sas",
            "account_key_used": False,
            "https_only": True,
        },
        "operator_access": {
            "method": "Time-limited Azure AD user-delegation SAS (read-only).",
            "azure_identity": (
                "Managed identity uploads artifacts and mints user-delegation SAS; no account "
                "keys are used."
            ),
            "secret_handling": (
                "SAS URLs are short-lived secrets; deliver them to the operator out-of-band "
                "and never commit them."
            ),
        },
        "retention": {
            "temporary": True,
            "expires_at": expires_at,
            "cleanup_after": expires_at,
            "cleanup_owner": "operator_or_storage_lifecycle_policy",
        },
        "publication": {
            "eligible": False,
            "blocked_by": ["human_review"],
        },
    }
