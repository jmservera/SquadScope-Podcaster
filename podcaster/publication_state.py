"""Canonical provider-delivery outcomes and durable reconciliation evidence."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Mapping

from podcaster.job_logs import LogLevel, emit_log

if TYPE_CHECKING:
    from podcaster.storage import StorageBackend

UPLOADED = "uploaded"
DRAFT_CREATED = "draft_created"
MANUAL_HANDOFF_REQUIRED = "manual_handoff_required"
PUBLISHED = "published"
PUBLICATION_UNKNOWN = "publication_unknown"
CANONICAL_OUTCOMES = (
    UPLOADED,
    DRAFT_CREATED,
    MANUAL_HANDOFF_REQUIRED,
    PUBLISHED,
    PUBLICATION_UNKNOWN,
)

EVIDENCE_SCHEMA_VERSION = "squadscope-podcaster-publication-evidence-v1"
MIN_EVIDENCE_RETENTION_DAYS = 28
EVIDENCE_PREFIX = "publication-evidence"
PROVIDER_STATUSES = (
    "not_requested",
    "gated",
    "pending",
    "draft",
    "private",
    "unlisted",
    "public",
    "failed",
    "unknown",
)
VERIFICATION_STATES = ("none", "provider_readback", "external_verified")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WEEK_RE = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
_RUN_RE = re.compile(r"^[0-9]+$")
_UNSAFE_DETAIL_KEY = re.compile(
    r"(authorization|bearer|cookie|credential|secret|token|signed.?url|body|content)",
    re.IGNORECASE,
)


class PublicationStateError(ValueError):
    """Raised when publication identity or durable evidence is unsafe."""


@dataclass(frozen=True)
class PublicationIdentity:
    accepted_job_id: str
    week: str
    publish_run_id: str
    article_sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class PublicationEvidence:
    seq: int
    at: str
    week: str
    publish_run_id: str
    article_sha256: str
    manifest_sha256: str
    job_id: str
    platform: str
    media_kind: str
    operation: str
    outcome: str
    status: str
    transport_status: str
    native_state: str | None = None
    verification: str = "none"
    evidence_source: str | None = None
    provider_id: str | None = None
    provider_artifact_id: str | None = None
    mutation_attempted: bool = False
    confirmation_source: str | None = None
    confirmed_at: str | None = None
    retry_blocked: bool = False
    code: str | None = None
    checked_at: str | None = None
    last_error_code: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def validate_outcome(outcome: str) -> str:
    if outcome not in CANONICAL_OUTCOMES:
        raise PublicationStateError(f"unknown publication outcome: {outcome!r}")
    return outcome


def _provider_status(
    outcome: str,
    *,
    verification: str,
    native_state: str | None,
) -> str:
    if verification not in VERIFICATION_STATES:
        raise PublicationStateError(f"unknown verification state: {verification!r}")
    normalized_native = str(native_state or "").strip().lower()
    if outcome == PUBLISHED:
        return "public" if verification == "external_verified" else "pending"
    if outcome == DRAFT_CREATED:
        if normalized_native in ("private", "unlisted", "draft"):
            return normalized_native
        return "draft"
    if outcome == UPLOADED:
        return "pending"
    if outcome == MANUAL_HANDOFF_REQUIRED:
        return "gated"
    if outcome == PUBLICATION_UNKNOWN:
        return "unknown"
    raise PublicationStateError(f"cannot normalize publication outcome: {outcome!r}")


def outcome_from_publish_status(
    status: str | None,
    *,
    confirmed: bool = False,
) -> str | None:
    if status == "published":
        return PUBLISHED if confirmed else PUBLICATION_UNKNOWN
    if status in ("draft", "scheduled"):
        return DRAFT_CREATED
    if status == "failed":
        return MANUAL_HANDOFF_REQUIRED
    return None


def outcome_from_distribution_status(status: str | None) -> str | None:
    if status in ("completed", "partial", "failed", "pending"):
        return None
    return None


def outcome_from_spotify_terminal_state(terminal_state: str | None) -> str | None:
    if terminal_state in ("published", "already_published"):
        return PUBLISHED
    if terminal_state == "publication_state_unknown":
        return PUBLICATION_UNKNOWN
    if terminal_state == "draft_gate_denied":
        return DRAFT_CREATED
    if terminal_state in (
        "blocked_protected_historical_draft",
        "manual_handoff_required",
        "failed",
    ):
        return MANUAL_HANDOFF_REQUIRED
    return None


def new_publish_run_id() -> str:
    return str(uuid.uuid4().int)


def canonical_identity_requested(request: Mapping[str, Any]) -> bool:
    return (
        request.get("publication_identity_mode") == "canonical"
        or request.get("publish_run_id") is not None
        or request.get("manifest_sha256") is not None
    )


def publication_identity(
    manifest: Mapping[str, Any],
    accepted_job_id: str,
    publish_run_id: str,
) -> PublicationIdentity:
    manifest_job_id = manifest.get("job_id")
    if not accepted_job_id or manifest_job_id != accepted_job_id:
        raise PublicationStateError("accepted job_id does not match manifest")
    request = manifest.get("request")
    if not isinstance(request, Mapping) or request.get("dry_run") is True:
        raise PublicationStateError("publication evidence requires an accepted non-dry-run job")
    identity_mode = request.get("publication_identity_mode")
    if identity_mode == "legacy":
        raise PublicationStateError("legacy requests cannot create canonical publication evidence")
    if identity_mode not in (None, "canonical"):
        raise PublicationStateError("publication identity mode is malformed")
    lifecycle = manifest.get("lifecycle")
    transitions = lifecycle.get("transitions") if isinstance(lifecycle, Mapping) else None
    accepted = isinstance(transitions, list) and any(
        isinstance(item, Mapping) and item.get("to") == "accepted" for item in transitions
    )
    if not accepted or accepted_job_id.endswith("-dry-run"):
        raise PublicationStateError("manifest does not contain accepted job identity")
    week = str(request.get("week") or "").strip()
    manifest_publish_run_id = request.get("publish_run_id")
    if not isinstance(manifest_publish_run_id, str) or not manifest_publish_run_id:
        raise PublicationStateError("manifest publish_run_id is missing")
    if publish_run_id and publish_run_id != manifest_publish_run_id:
        raise PublicationStateError("publish_run_id conflicts with manifest identity")
    publish_run_id = manifest_publish_run_id
    article_sha256 = str(request.get("article_sha256") or "").strip()
    manifest_sha256 = str(request.get("manifest_sha256") or "").strip()
    if not _WEEK_RE.fullmatch(week):
        raise PublicationStateError("manifest week is malformed")
    if not _SHA256_RE.fullmatch(article_sha256):
        raise PublicationStateError("manifest article_sha256 is malformed")
    if not _SHA256_RE.fullmatch(manifest_sha256):
        raise PublicationStateError("manifest manifest_sha256 is malformed")
    if not isinstance(publish_run_id, str) or not _RUN_RE.fullmatch(publish_run_id):
        raise PublicationStateError("publish_run_id is malformed")
    return PublicationIdentity(
        accepted_job_id,
        week,
        publish_run_id,
        article_sha256,
        manifest_sha256,
    )


def evidence_path(job_id: str) -> str:
    return f"{EVIDENCE_PREFIX}/{job_id}.json"


def legacy_evidence_path(job_id: str) -> str:
    return f"jobs/{job_id}/publication-evidence.json"


def _iso(moment: datetime | None = None) -> str:
    value = moment or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_details(details: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not details:
        return None
    safe: dict[str, Any] = {}
    for key, value in details.items():
        name = str(key)[:64]
        if _UNSAFE_DETAIL_KEY.search(name):
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            safe[name] = value
        elif isinstance(value, str) and "://" not in value:
            safe[name] = value[:256]
    return safe or None


def _load_evidence(raw: bytes | None, job_id: str, *, strict: bool) -> dict[str, Any]:
    if raw is None:
        return {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "job_id": job_id,
            "minimum_retention_days": MIN_EVIDENCE_RETENTION_DAYS,
            "retention_policy": "append_only_no_count_eviction",
            "updated_at": None,
            "records": [],
        }
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        if strict:
            raise PublicationStateError("publication evidence is corrupt") from exc
        return {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "job_id": job_id,
            "minimum_retention_days": MIN_EVIDENCE_RETENTION_DAYS,
            "retention_policy": "append_only_no_count_eviction",
            "updated_at": None,
            "records": [],
            "corrupt": True,
        }
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or document.get("job_id") != job_id
        or not isinstance(document.get("records"), list)
    ):
        if strict:
            raise PublicationStateError("publication evidence identity/schema is invalid")
        return {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "job_id": job_id,
            "minimum_retention_days": MIN_EVIDENCE_RETENTION_DAYS,
            "retention_policy": "append_only_no_count_eviction",
            "updated_at": None,
            "records": [],
            "corrupt": True,
        }
    return document


def append_evidence(
    storage: StorageBackend,
    identity: PublicationIdentity,
    *,
    platform: str,
    media_kind: str,
    operation: str,
    outcome: str,
    provider_artifact_id: str | int | None = None,
    mutation_attempted: bool = False,
    confirmation_source: str | None = None,
    verification: str = "none",
    native_state: str | None = None,
    transport_status: str | None = None,
    evidence_source: str | None = None,
    retry_blocked: bool = False,
    code: str | None = None,
    details: Mapping[str, Any] | None = None,
    at: datetime | None = None,
    authorize: Callable[[], None] | None = None,
) -> PublicationEvidence | None:
    validate_outcome(outcome)
    effective_verification = (
        "provider_readback" if confirmation_source and verification == "none" else verification
    )
    status = _provider_status(
        outcome,
        verification=effective_verification,
        native_state=native_state,
    )
    normalized_transport_status = transport_status or (
        "mutation_attempted" if mutation_attempted else "not_attempted"
    )
    timestamp = _iso(at)
    artifact_id = str(provider_artifact_id) if provider_artifact_id is not None else None
    dedupe_key = (
        identity.accepted_job_id,
        identity.week,
        identity.article_sha256,
        identity.manifest_sha256,
        identity.publish_run_id,
        platform,
        media_kind,
        operation,
        outcome,
        artifact_id or "",
    )
    captured: dict[str, Any] = {}
    legacy_raw = storage.get_bytes(legacy_evidence_path(identity.accepted_job_id))

    def _apply(raw: bytes | None) -> bytes:
        if authorize is not None:
            authorize()
        document = _load_evidence(
            raw if raw is not None else legacy_raw,
            identity.accepted_job_id,
            strict=True,
        )
        records = document["records"]
        for existing in records:
            if not isinstance(existing, dict):
                raise PublicationStateError("publication evidence contains a malformed record")
            existing_key = (
                existing.get("job_id"),
                existing.get("week"),
                existing.get("article_sha256"),
                existing.get("manifest_sha256"),
                existing.get("publish_run_id"),
                existing.get("platform"),
                existing.get("media_kind"),
                existing.get("operation"),
                existing.get("outcome"),
                existing.get("provider_artifact_id") or "",
            )
            if existing_key == dedupe_key:
                return raw if raw is not None else legacy_raw or b""
        next_seq = (
            max(
                (
                    int(record.get("seq", 0))
                    for record in records
                    if isinstance(record, dict) and str(record.get("seq", "")).isdigit()
                ),
                default=0,
            )
            + 1
        )
        record = PublicationEvidence(
            seq=next_seq,
            at=timestamp,
            week=identity.week,
            publish_run_id=identity.publish_run_id,
            article_sha256=identity.article_sha256,
            manifest_sha256=identity.manifest_sha256,
            job_id=identity.accepted_job_id,
            platform=str(platform)[:64],
            media_kind=str(media_kind)[:32],
            operation=str(operation)[:64],
            outcome=outcome,
            status=status,
            transport_status=str(normalized_transport_status)[:64],
            native_state=str(native_state)[:64] if native_state else None,
            verification=effective_verification,
            evidence_source=(
                str(evidence_source or confirmation_source)[:64]
                if evidence_source or confirmation_source
                else None
            ),
            provider_id=artifact_id,
            provider_artifact_id=artifact_id,
            mutation_attempted=mutation_attempted,
            confirmation_source=(str(confirmation_source)[:64] if confirmation_source else None),
            confirmed_at=timestamp if confirmation_source else None,
            retry_blocked=retry_blocked,
            code=str(code)[:64] if code else None,
            checked_at=timestamp,
            last_error_code=str(code)[:64] if code else None,
            details=_safe_details(details),
        ).to_dict()
        records.append(record)
        document["minimum_retention_days"] = MIN_EVIDENCE_RETENTION_DAYS
        document["retention_policy"] = "append_only_no_count_eviction"
        document["records"] = records
        document["updated_at"] = timestamp
        captured.update(record)
        return json.dumps(document, ensure_ascii=False).encode("utf-8")

    storage.update_bytes(
        evidence_path(identity.accepted_job_id),
        "application/json; charset=utf-8",
        _apply,
    )
    return PublicationEvidence(**captured) if captured else None


def read_evidence(storage: StorageBackend, job_id: str) -> dict[str, Any] | None:
    raw = storage.get_bytes(evidence_path(job_id))
    if raw is None:
        raw = storage.get_bytes(legacy_evidence_path(job_id))
    return None if raw is None else _load_evidence(raw, job_id, strict=False)


def latest_outcomes(document: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    records = document.get("records") if isinstance(document, Mapping) else None
    if not isinstance(records, list):
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        platform = record.get("platform")
        media_kind = record.get("media_kind")
        if isinstance(platform, str) and isinstance(media_kind, str):
            latest[f"{platform}:{media_kind}"] = record
    return latest


def retry_is_blocked(
    document: Mapping[str, Any] | None,
    *,
    platform: str,
    media_kind: str,
) -> bool:
    record = latest_outcomes(document).get(f"{platform}:{media_kind}")
    return bool(
        record
        and record.get("outcome")
        in (UPLOADED, PUBLICATION_UNKNOWN, MANUAL_HANDOFF_REQUIRED, DRAFT_CREATED, PUBLISHED)
        and record.get("retry_blocked", True)
    )


def emit_publication_signal(
    storage: StorageBackend,
    identity: PublicationIdentity,
    *,
    platform: str,
    media_kind: str,
    outcome: str,
    provider_artifact_id: str | int | None = None,
    code: str | None = None,
    authorize: Callable[[], None] | None = None,
    fail_closed: bool = False,
) -> object | None:
    validate_outcome(outcome)
    artifact_id = str(provider_artifact_id) if provider_artifact_id is not None else ""
    level = (
        LogLevel.ERROR
        if outcome == PUBLICATION_UNKNOWN
        else LogLevel.WARNING
        if outcome == MANUAL_HANDOFF_REQUIRED
        else LogLevel.INFO
    )
    context = {
        "publication_outcome": outcome,
        "platform": platform,
        "media_kind": media_kind,
        "provider_artifact_id": artifact_id or None,
        "publish_run_id": identity.publish_run_id,
        "code": code,
        "action_required": outcome in (PUBLICATION_UNKNOWN, MANUAL_HANDOFF_REQUIRED),
    }
    return emit_log(
        storage,
        identity.accepted_job_id,
        message=f"provider delivery outcome: {platform} {media_kind} {outcome}",
        level=level,
        stage="publication",
        context={key: value for key, value in context.items() if value is not None},
        dedupe_key="|".join((identity.accepted_job_id, platform, media_kind, outcome, artifact_id)),
        authorize=authorize,
        fail_closed=fail_closed,
    )
