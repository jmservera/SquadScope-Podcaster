"""Durable fenced distribution outbox and reconciliation state machine."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from podcaster.publication_state import PublicationIdentity
from podcaster.storage import StorageBackend

OUTBOX_SCHEMA_VERSION = "squadscope-podcaster-distribution-outbox-v1"
OUTBOX_PREFIX = "distribution-outbox"
ARTIFACT_PREFIX = "distribution-artifacts"
ARTIFACT_METADATA_PREFIX = "distribution-artifact-metadata"
RECONCILIATION_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-distribution-reconcile-v1"

PROVIDER_RESULTS = frozenset(
    {
        "pending_provider",
        "externally_verified_public",
        "publication_unknown",
        "manual_handoff_required",
        "failed_retryable_pre_mutation",
        "failed_terminal",
        "poisoned",
    }
)
ACTIONABLE_RESULTS = frozenset(
    {
        "pending_provider",
        "publication_unknown",
        "manual_handoff_required",
        "failed_retryable_pre_mutation",
        "failed_terminal",
        "poisoned",
    }
)
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE_KEY = re.compile(
    r"(authorization|bearer|cookie|credential|secret|token_value|signed.?url|body|content)",
    re.IGNORECASE,
)


class DistributionOutboxError(RuntimeError):
    """Base error for durable distribution state violations."""


class OutboxConflictError(DistributionOutboxError):
    """Raised when an existing logical item conflicts with a new enqueue."""


class StaleClaimError(DistributionOutboxError):
    """Raised when a stale or non-owning worker attempts a fenced write."""


class LeaseBudgetError(DistributionOutboxError):
    """Raised when insufficient lease remains for mutation and receipt persistence."""


class UnsafeOutboxValueError(DistributionOutboxError):
    """Raised when a durable record would contain unsafe data."""


@dataclass(frozen=True)
class ArtifactReference:
    path: str
    sha256: str
    size_bytes: int
    media_kind: str


@dataclass(frozen=True)
class Claim:
    outbox_id: str
    owner: str
    claim_id: str
    execution_id: str
    fencing_token: int
    lease_expires_at: str
    read_only: bool


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _require_token(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_TOKEN.fullmatch(normalized):
        raise UnsafeOutboxValueError(f"{name} is malformed")
    return normalized


def _safe_value(value: Any, *, key: str = "") -> Any:
    if key and _UNSAFE_KEY.search(key):
        raise UnsafeOutboxValueError(f"unsafe durable field: {key}")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if "://" in value or len(value) > 256:
            raise UnsafeOutboxValueError(f"unsafe durable value for {key or 'field'}")
        return value
    if isinstance(value, Mapping):
        return {str(k)[:64]: _safe_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, key=key) for item in value]
    raise UnsafeOutboxValueError(f"unsupported durable value for {key or 'field'}")


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def outbox_id_for(
    identity: PublicationIdentity,
    *,
    media_kind: str,
    provider_objectives: Mapping[str, str],
) -> str:
    objective = ",".join(
        f"{_require_token('provider', provider)}={_require_token('objective', state)}"
        for provider, state in sorted(provider_objectives.items())
    )
    source = "|".join(
        (
            identity.accepted_job_id,
            identity.week,
            identity.publish_run_id,
            identity.article_sha256,
            identity.manifest_sha256,
            _require_token("media_kind", media_kind),
            objective,
        )
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def outbox_path(outbox_id: str) -> str:
    if not _SHA256.fullmatch(outbox_id):
        raise UnsafeOutboxValueError("outbox_id is malformed")
    return f"{OUTBOX_PREFIX}/{outbox_id}.json"


def artifact_path(sha256: str, media_kind: str, suffix: str = "") -> str:
    if not _SHA256.fullmatch(sha256):
        raise UnsafeOutboxValueError("artifact sha256 is malformed")
    safe_kind = _require_token("media_kind", media_kind)
    safe_suffix = ""
    if suffix:
        normalized = suffix if suffix.startswith(".") else f".{suffix}"
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", normalized):
            raise UnsafeOutboxValueError("artifact suffix is malformed")
        safe_suffix = normalized.lower()
    return f"{ARTIFACT_PREFIX}/{safe_kind}/{sha256}{safe_suffix}"


def commit_immutable_artifact(
    storage: StorageBackend,
    source: Path,
    *,
    media_kind: str,
    content_type: str,
    suffix: str = "",
) -> ArtifactReference:
    digest, size = _sha256_file(source)
    path = artifact_path(digest, media_kind, suffix)
    existing_size = storage.blob_size(path)
    if existing_size is None:
        storage.upload_file(path, source, content_type)
    elif existing_size != size:
        raise OutboxConflictError("content-addressed artifact size conflicts with existing blob")
    verify_artifact(storage, ArtifactReference(path, digest, size, media_kind))
    metadata_path = f"{ARTIFACT_METADATA_PREFIX}/{digest}.json"

    def _metadata(raw: bytes | None) -> bytes:
        if raw is not None:
            return raw
        document = {
            "artifact_path": path,
            "sha256": digest,
            "size_bytes": size,
            "media_kind": _require_token("media_kind", media_kind),
            "created_at": _iso(utc_now()),
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

    storage.update_bytes(
        metadata_path,
        "application/json; charset=utf-8",
        _metadata,
    )
    return ArtifactReference(path, digest, size, media_kind)


def verify_artifact(storage: StorageBackend, artifact: ArtifactReference) -> None:
    if storage.blob_size(artifact.path) != artifact.size_bytes:
        raise DistributionOutboxError("artifact size verification failed")
    fd, name = tempfile.mkstemp(prefix="distribution-artifact-", suffix=".verify")
    os.close(fd)
    target = Path(name)
    try:
        if not storage.download_file(artifact.path, target):
            raise DistributionOutboxError("artifact readback failed")
        digest, size = _sha256_file(target)
        if digest != artifact.sha256 or size != artifact.size_bytes:
            raise DistributionOutboxError("artifact integrity verification failed")
    finally:
        target.unlink(missing_ok=True)


def _identity_dict(identity: PublicationIdentity) -> dict[str, str]:
    return {
        "accepted_job_id": identity.accepted_job_id,
        "week": identity.week,
        "publish_run_id": identity.publish_run_id,
        "article_sha256": identity.article_sha256,
        "manifest_sha256": identity.manifest_sha256,
    }


def _artifact_dict(artifact: ArtifactReference) -> dict[str, Any]:
    return {
        "path": artifact.path,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
        "media_kind": artifact.media_kind,
    }


def _artifact_from(document: Mapping[str, Any]) -> ArtifactReference:
    artifact = document.get("artifact")
    if not isinstance(artifact, Mapping):
        raise DistributionOutboxError("outbox artifact is missing")
    return ArtifactReference(
        path=str(artifact.get("path") or ""),
        sha256=str(artifact.get("sha256") or ""),
        size_bytes=int(artifact.get("size_bytes") or 0),
        media_kind=str(artifact.get("media_kind") or ""),
    )


def _validate_document(document: Mapping[str, Any], expected_id: str) -> None:
    if document.get("schema_version") != OUTBOX_SCHEMA_VERSION:
        raise DistributionOutboxError("outbox schema is unsupported")
    if document.get("outbox_id") != expected_id:
        raise DistributionOutboxError("outbox identity is invalid")
    identity = document.get("publication_identity")
    artifact = document.get("artifact")
    providers = document.get("providers")
    if not isinstance(identity, Mapping) or not isinstance(artifact, Mapping):
        raise DistributionOutboxError("outbox correlation is incomplete")
    if not isinstance(providers, Mapping) or not providers:
        raise DistributionOutboxError("outbox provider objectives are missing")
    if not _SHA256.fullmatch(str(artifact.get("sha256") or "")):
        raise DistributionOutboxError("outbox artifact digest is invalid")
    _safe_value(document)


class DistributionOutboxRepository:
    def __init__(
        self,
        storage: StorageBackend,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.storage = storage
        self.now = now

    def read(self, outbox_id: str) -> dict[str, Any] | None:
        raw = self.storage.get_bytes(outbox_path(outbox_id))
        if raw is None:
            return None
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise DistributionOutboxError("outbox record is corrupt") from exc
        if not isinstance(document, dict):
            raise DistributionOutboxError("outbox record is malformed")
        _validate_document(document, outbox_id)
        return document

    def enqueue(
        self,
        identity: PublicationIdentity,
        artifact: ArtifactReference,
        *,
        provider_objectives: Mapping[str, str],
        enqueue_source: str,
        enqueue_version: str,
    ) -> tuple[dict[str, Any], bool]:
        verify_artifact(self.storage, artifact)
        item_id = outbox_id_for(
            identity,
            media_kind=artifact.media_kind,
            provider_objectives=provider_objectives,
        )
        created = False
        captured: dict[str, Any] = {}
        expected = {
            "publication_identity": _identity_dict(identity),
            "artifact": _artifact_dict(artifact),
            "provider_objectives": dict(sorted(provider_objectives.items())),
        }

        def _create(raw: bytes | None) -> bytes:
            nonlocal created
            if raw is not None:
                existing = json.loads(raw.decode("utf-8"))
                if (
                    existing.get("publication_identity") != expected["publication_identity"]
                    or existing.get("artifact") != expected["artifact"]
                    or existing.get("provider_objectives") != expected["provider_objectives"]
                ):
                    raise OutboxConflictError("logical outbox item conflicts with existing record")
                captured.update(existing)
                return raw
            timestamp = _iso(self.now())
            providers = {
                _require_token("provider", provider): {
                    "objective": _require_token("objective", objective),
                    "result": "pending_provider",
                    "intent": None,
                    "receipts": [],
                    "verification": None,
                    "next_reconcile_at": None,
                    "verification_attempt": 0,
                    "verification_budget": 8,
                    "verification_horizon_at": _iso(self.now() + timedelta(hours=24)),
                    "active_schedule_token": None,
                    "schedule_notification_token": None,
                    "schedule_notification_sent_at": None,
                }
                for provider, objective in sorted(provider_objectives.items())
            }
            document = {
                "schema_version": OUTBOX_SCHEMA_VERSION,
                "outbox_id": item_id,
                "publication_identity": expected["publication_identity"],
                "artifact": expected["artifact"],
                "provider_objectives": expected["provider_objectives"],
                "enqueue": {
                    "at": timestamp,
                    "source": _require_token("enqueue_source", enqueue_source),
                    "version": _require_token("enqueue_version", enqueue_version),
                    "notification_sent_at": None,
                },
                "state": "pending",
                "claim": None,
                "fencing_token": 0,
                "attempt_count": 0,
                "providers": providers,
                "aggregate": {
                    "result": "pending_provider",
                    "externally_verified_public": False,
                    "terminal_reason": None,
                },
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            _validate_document(document, item_id)
            captured.update(document)
            created = True
            return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

        self.storage.update_bytes(
            outbox_path(item_id),
            "application/json; charset=utf-8",
            _create,
        )
        return captured, created

    def mark_notification_sent(self, outbox_id: str) -> dict[str, Any]:
        return self._update(
            outbox_id,
            lambda document: document["enqueue"].__setitem__(
                "notification_sent_at",
                document["enqueue"].get("notification_sent_at") or _iso(self.now()),
            ),
        )

    def claim(
        self,
        outbox_id: str,
        *,
        owner: str,
        execution_id: str,
        lease_seconds: int,
    ) -> Claim:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        result: dict[str, Any] = {}

        def _claim(document: dict[str, Any]) -> None:
            now = self.now()
            current = document.get("claim")
            if isinstance(current, Mapping):
                expiry = _parse_time(str(current.get("lease_expires_at") or ""))
                if expiry is not None and expiry > now:
                    raise StaleClaimError("outbox item already has an active claim")
            token = int(document.get("fencing_token") or 0) + 1
            consumed = False
            for provider in document["providers"].values():
                if not isinstance(provider, Mapping):
                    continue
                intent = provider.get("intent")
                if not isinstance(intent, Mapping) or not intent.get("consumed_at"):
                    continue
                receipts = provider.get("receipts")
                has_receipt = isinstance(receipts, list) and any(
                    isinstance(receipt, Mapping)
                    and receipt.get("intent_id") == intent.get("intent_id")
                    and receipt.get("ambiguous") is not True
                    and receipt.get("transport_class") == "accepted"
                    for receipt in receipts
                )
                if not has_receipt:
                    consumed = True
                    break
            claim = {
                "owner": _require_token("claim_owner", owner),
                "claim_id": uuid.uuid4().hex,
                "execution_id": _require_token("execution_id", execution_id),
                "fencing_token": token,
                "claimed_at": _iso(now),
                "heartbeat_at": _iso(now),
                "lease_expires_at": _iso(now + timedelta(seconds=lease_seconds)),
                "read_only": consumed,
            }
            document["claim"] = claim
            document["fencing_token"] = token
            document["attempt_count"] = int(document.get("attempt_count") or 0) + 1
            document["state"] = "reconciling" if consumed else "claimed"
            result.update(claim)

        self._update(outbox_id, _claim)
        return Claim(
            outbox_id=outbox_id,
            owner=result["owner"],
            claim_id=result["claim_id"],
            execution_id=result["execution_id"],
            fencing_token=result["fencing_token"],
            lease_expires_at=result["lease_expires_at"],
            read_only=result["read_only"],
        )

    def heartbeat(self, claim: Claim, *, lease_seconds: int) -> dict[str, Any]:
        def _heartbeat(document: dict[str, Any]) -> None:
            self._require_claim(document, claim)
            now = self.now()
            document["claim"]["heartbeat_at"] = _iso(now)
            document["claim"]["lease_expires_at"] = _iso(now + timedelta(seconds=lease_seconds))

        return self._update(claim.outbox_id, _heartbeat)

    def persist_intent(
        self,
        claim: Claim,
        *,
        provider: str,
        operation: str,
        expected_provider_item_id: str | None = None,
        precondition_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def _persist(document: dict[str, Any]) -> None:
            self._require_claim(document, claim, mutation=True)
            leg = self._provider(document, provider)
            existing = leg.get("intent")
            if isinstance(existing, Mapping):
                if existing.get("operation") == operation:
                    captured.update(existing)
                    return
                receipts = leg.get("receipts")
                completed = (
                    existing.get("consumed_at")
                    and isinstance(receipts, list)
                    and any(
                        isinstance(receipt, Mapping)
                        and receipt.get("intent_id") == existing.get("intent_id")
                        and receipt.get("ambiguous") is not True
                        and receipt.get("transport_class") == "accepted"
                        for receipt in receipts
                    )
                )
                if not completed:
                    raise OutboxConflictError("provider has an unresolved mutation intent")
                leg.setdefault("intent_history", []).append(existing)
            intent = {
                "intent_id": uuid.uuid4().hex,
                "provider": _require_token("provider", provider),
                "operation": _require_token("operation", operation),
                "created_at": _iso(self.now()),
                "created_fence": claim.fencing_token,
                "consumed_at": None,
                "consumed_fence": None,
                "expected_provider_item_id": (
                    _require_token("provider_item_id", expected_provider_item_id)
                    if expected_provider_item_id
                    else None
                ),
                "precondition_fingerprint": (
                    _require_token("precondition_fingerprint", precondition_fingerprint)
                    if precondition_fingerprint
                    else None
                ),
            }
            leg["intent"] = intent
            document["state"] = "intent_persisted"
            captured.update(intent)

        self._update(claim.outbox_id, _persist)
        return captured

    def consume_intent(
        self,
        claim: Claim,
        *,
        provider: str,
        provider_timeout_seconds: int,
        receipt_margin_seconds: int,
    ) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def _consume(document: dict[str, Any]) -> None:
            self._require_claim(document, claim, mutation=True)
            leg = self._provider(document, provider)
            intent = leg.get("intent")
            if not isinstance(intent, dict):
                raise DistributionOutboxError("provider mutation intent is missing")
            if intent.get("consumed_at"):
                raise DistributionOutboxError("provider mutation intent is already consumed")
            expiry = _parse_time(document["claim"]["lease_expires_at"])
            remaining = (expiry - self.now()).total_seconds() if expiry else 0
            required = provider_timeout_seconds + receipt_margin_seconds
            if remaining <= required:
                raise LeaseBudgetError(
                    f"remaining lease {remaining:.3f}s does not exceed required {required}s"
                )
            intent["consumed_at"] = _iso(self.now())
            intent["consumed_fence"] = claim.fencing_token
            document["state"] = "mutating"
            captured.update(intent)

        self._update(claim.outbox_id, _consume)
        return captured

    def record_receipt(
        self,
        claim: Claim,
        *,
        provider: str,
        transport_class: str,
        provider_item_id: str | None = None,
        native_state: str | None = None,
        ambiguous: bool = False,
        code: str | None = None,
    ) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def _record(document: dict[str, Any]) -> None:
            self._require_claim(document, claim)
            leg = self._provider(document, provider)
            intent = leg.get("intent")
            if not isinstance(intent, Mapping) or not intent.get("consumed_at"):
                raise DistributionOutboxError("receipt requires a consumed mutation intent")
            receipt = {
                "receipt_id": uuid.uuid4().hex,
                "intent_id": intent["intent_id"],
                "at": _iso(self.now()),
                "fencing_token": claim.fencing_token,
                "transport_class": _require_token("transport_class", transport_class),
                "provider_item_id": (
                    _require_token("provider_item_id", provider_item_id)
                    if provider_item_id
                    else None
                ),
                "native_state": (
                    _require_token("native_state", native_state) if native_state else None
                ),
                "ambiguous": bool(ambiguous),
                "code": _require_token("code", code) if code else None,
            }
            leg["receipts"].append(receipt)
            document["state"] = "receipt_persisted"
            captured.update(receipt)

        self._update(claim.outbox_id, _record)
        return captured

    def record_verification(
        self,
        claim: Claim,
        *,
        provider: str,
        result: str,
        source: str,
        provider_item_id: str | None = None,
        native_state: str | None = None,
        next_reconcile_at: datetime | None = None,
        exhaustion_reason: str | None = None,
    ) -> dict[str, Any]:
        if result not in PROVIDER_RESULTS:
            raise ValueError(f"unsupported provider result: {result}")
        captured: dict[str, Any] = {}

        def _verify(document: dict[str, Any]) -> None:
            self._require_claim(document, claim)
            leg = self._provider(document, provider)
            verification = {
                "at": _iso(self.now()),
                "source": _require_token("verification_source", source),
                "provider_item_id": (
                    _require_token("provider_item_id", provider_item_id)
                    if provider_item_id
                    else None
                ),
                "native_state": (
                    _require_token("native_state", native_state) if native_state else None
                ),
                "result": result,
                "fencing_token": claim.fencing_token,
                "exhaustion_reason": (
                    _require_token("exhaustion_reason", exhaustion_reason)
                    if exhaustion_reason
                    else None
                ),
            }
            leg["verification"] = verification
            leg["result"] = result
            leg["verification_attempt"] = int(leg.get("verification_attempt") or 0) + 1
            leg["next_reconcile_at"] = _iso(next_reconcile_at) if next_reconcile_at else None
            if result != "pending_provider":
                leg["active_schedule_token"] = None
                leg["schedule_notification_token"] = None
                leg["schedule_notification_sent_at"] = None
            self._refresh_aggregate(document)
            captured.update(verification)

        self._update(claim.outbox_id, _verify)
        return captured

    def schedule_reconciliation(
        self,
        claim: Claim,
        *,
        provider: str,
        due_at: datetime,
    ) -> str:
        captured: dict[str, str] = {}

        def _schedule(document: dict[str, Any]) -> None:
            self._require_claim(document, claim)
            leg = self._provider(document, provider)
            existing = leg.get("active_schedule_token")
            if existing:
                captured["token"] = str(existing)
                return
            if int(leg.get("verification_attempt") or 0) >= int(
                leg.get("verification_budget") or 0
            ):
                leg["result"] = "manual_handoff_required"
                leg["next_reconcile_at"] = None
                self._refresh_aggregate(document)
                raise DistributionOutboxError("provider verification budget is exhausted")
            horizon = _parse_time(str(leg.get("verification_horizon_at") or ""))
            if horizon is not None and due_at > horizon:
                leg["result"] = "manual_handoff_required"
                leg["next_reconcile_at"] = None
                self._refresh_aggregate(document)
                raise DistributionOutboxError("provider verification horizon is exhausted")
            token = hashlib.sha256(
                f"{claim.outbox_id}|{provider}|{_iso(due_at)}".encode("utf-8")
            ).hexdigest()
            leg["next_reconcile_at"] = _iso(due_at)
            leg["active_schedule_token"] = token
            leg["schedule_notification_token"] = None
            leg["schedule_notification_sent_at"] = None
            leg["result"] = "pending_provider"
            self._refresh_aggregate(document)
            captured["token"] = token

        self._update(claim.outbox_id, _schedule)
        return captured["token"]

    def consume_schedule_token(
        self,
        claim: Claim,
        *,
        provider: str,
        token: str,
    ) -> None:
        def _consume(document: dict[str, Any]) -> None:
            self._require_claim(document, claim)
            leg = self._provider(document, provider)
            if leg.get("active_schedule_token") != token:
                raise StaleClaimError("reconciliation token is stale")
            due = _parse_time(str(leg.get("next_reconcile_at") or ""))
            if due is None or due > self.now():
                raise DistributionOutboxError("reconciliation token is not due")
            leg["active_schedule_token"] = None
            leg["schedule_notification_token"] = None
            leg["schedule_notification_sent_at"] = None
            leg["last_reconciled_at"] = _iso(self.now())

        self._update(claim.outbox_id, _consume)

    def release(self, claim: Claim) -> dict[str, Any]:
        def _release(document: dict[str, Any]) -> None:
            self._require_claim(document, claim)
            document["claim"] = None
            if document["aggregate"]["externally_verified_public"]:
                document["state"] = "completed_public"
            elif document["aggregate"]["result"] in (
                "publication_unknown",
                "manual_handoff_required",
                "failed_terminal",
                "poisoned",
            ):
                document["state"] = document["aggregate"]["result"]
            else:
                document["state"] = "pending"

        return self._update(claim.outbox_id, _release)

    def due_reconciliations(
        self,
        *,
        limit: int = 100,
        scan_limit: int = 5000,
        after_path: str | None = None,
        notification_stale_after: timedelta = timedelta(minutes=15),
    ) -> list[tuple[str, str, str]]:
        due, _cursor = self.due_reconciliations_page(
            limit=limit,
            scan_limit=scan_limit,
            after_path=after_path,
            notification_stale_after=notification_stale_after,
        )
        return due

    def due_reconciliations_page(
        self,
        *,
        limit: int = 100,
        scan_limit: int = 5000,
        after_path: str | None = None,
        notification_stale_after: timedelta = timedelta(minutes=15),
    ) -> tuple[list[tuple[str, str, str]], str | None]:
        now = self.now()
        due: list[tuple[str, str, str]] = []
        paths = sorted(self.storage.list_blobs(f"{OUTBOX_PREFIX}/", limit=scan_limit))
        if after_path and after_path in paths:
            split = paths.index(after_path) + 1
            paths = paths[split:] + paths[:split]
        last_scanned: str | None = None
        for path in paths:
            last_scanned = path
            raw = self.storage.get_bytes(path)
            if raw is None:
                continue
            document = json.loads(raw.decode("utf-8"))
            for provider, leg in document.get("providers", {}).items():
                due_at = _parse_time(str(leg.get("next_reconcile_at") or ""))
                token = leg.get("active_schedule_token")
                notified_token = leg.get("schedule_notification_token")
                notified_at = _parse_time(str(leg.get("schedule_notification_sent_at") or ""))
                notification_is_fresh = (
                    notified_token == token
                    and notified_at is not None
                    and now - notified_at < notification_stale_after
                )
                if due_at is not None and due_at <= now and isinstance(token, str):
                    if notification_is_fresh:
                        continue
                    due.append((str(document["outbox_id"]), str(provider), token))
                    if len(due) >= limit:
                        return due, last_scanned
        return due, last_scanned

    def mark_reconciliation_notified(
        self,
        outbox_id: str,
        *,
        provider: str,
        token: str,
    ) -> dict[str, Any]:
        def _mark(document: dict[str, Any]) -> None:
            leg = self._provider(document, provider)
            if leg.get("active_schedule_token") != token:
                raise StaleClaimError("reconciliation token is stale")
            leg["schedule_notification_token"] = token
            leg["schedule_notification_sent_at"] = _iso(self.now())

        return self._update(outbox_id, _mark)

    def cleanup_orphan_artifacts(
        self,
        *,
        retention: timedelta = timedelta(hours=24),
        limit: int = 100,
        outbox_scan_limit: int = 5000,
    ) -> int:
        referenced: set[str] = set()
        outbox_paths = self.storage.list_blobs(
            f"{OUTBOX_PREFIX}/",
            limit=outbox_scan_limit,
        )
        if len(outbox_paths) >= outbox_scan_limit:
            return 0
        for path in outbox_paths:
            raw = self.storage.get_bytes(path)
            if raw is None:
                continue
            document = json.loads(raw.decode("utf-8"))
            artifact = document.get("artifact")
            if isinstance(artifact, Mapping) and artifact.get("path"):
                referenced.add(str(artifact["path"]))
        removed = 0
        for metadata_path in self.storage.list_blobs(
            f"{ARTIFACT_METADATA_PREFIX}/",
            limit=limit,
        ):
            raw = self.storage.get_bytes(metadata_path)
            if raw is None:
                continue
            metadata = json.loads(raw.decode("utf-8"))
            artifact_path_value = str(metadata.get("artifact_path") or "")
            created_at = _parse_time(str(metadata.get("created_at") or ""))
            if (
                not artifact_path_value
                or artifact_path_value in referenced
                or created_at is None
                or self.now() - created_at < retention
            ):
                continue
            self.storage.delete_blob(artifact_path_value)
            self.storage.delete_blob(metadata_path)
            removed += 1
        return removed

    def repair_notifications(self, notify: Callable[[str], None], *, limit: int = 100) -> int:
        repaired = 0
        for path in self.storage.list_blobs(f"{OUTBOX_PREFIX}/", limit=limit):
            raw = self.storage.get_bytes(path)
            if raw is None:
                continue
            document = json.loads(raw.decode("utf-8"))
            if document.get("enqueue", {}).get("notification_sent_at"):
                continue
            item_id = str(document["outbox_id"])
            notify(item_id)
            self.mark_notification_sent(item_id)
            repaired += 1
        return repaired

    def backfill_from_publication_evidence(
        self,
        identity: PublicationIdentity,
        artifact: ArtifactReference,
        *,
        provider_objectives: Mapping[str, str],
        evidence_records: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Create reconciliation-only outbox state from historical #680 evidence."""

        document, _created = self.enqueue(
            identity,
            artifact,
            provider_objectives=provider_objectives,
            enqueue_source="publication_evidence_backfill",
            enqueue_version="v1",
        )
        latest: dict[str, Mapping[str, Any]] = {}
        for record in evidence_records:
            provider = str(record.get("platform") or "")
            if provider in provider_objectives:
                latest[provider] = record

        def _backfill(current: dict[str, Any]) -> None:
            for provider, leg in current["providers"].items():
                record = latest.get(provider)
                if record is None:
                    continue
                at = str(record.get("at") or current["created_at"])
                outcome = str(record.get("outcome") or "")
                status = str(record.get("status") or "")
                verification = str(record.get("verification") or "none")
                result = (
                    "externally_verified_public"
                    if status == "public"
                    and outcome == "published"
                    and verification == "external_verified"
                    else "publication_unknown"
                    if outcome == "publication_unknown" or status == "unknown"
                    else "manual_handoff_required"
                )
                leg["intent"] = {
                    "intent_id": hashlib.sha256(
                        f"backfill|{current['outbox_id']}|{provider}".encode("utf-8")
                    ).hexdigest(),
                    "provider": provider,
                    "operation": "historical_reconciliation",
                    "created_at": at,
                    "created_fence": 0,
                    "consumed_at": at,
                    "consumed_fence": 0,
                    "expected_provider_item_id": record.get("provider_artifact_id"),
                    "precondition_fingerprint": None,
                }
                leg["result"] = result
                leg["verification"] = {
                    "at": str(record.get("checked_at") or at),
                    "source": str(record.get("evidence_source") or "publication_evidence"),
                    "provider_item_id": record.get("provider_artifact_id"),
                    "native_state": record.get("native_state"),
                    "result": result,
                    "fencing_token": 0,
                    "exhaustion_reason": None,
                }
            self._refresh_aggregate(current)

        return self._update(document["outbox_id"], _backfill)

    def _update(
        self,
        outbox_id: str,
        mutation: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def _apply(raw: bytes | None) -> bytes:
            if raw is None:
                raise DistributionOutboxError("outbox item does not exist")
            document = json.loads(raw.decode("utf-8"))
            _validate_document(document, outbox_id)
            mutation(document)
            document["updated_at"] = _iso(self.now())
            _validate_document(document, outbox_id)
            captured.update(document)
            return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

        self.storage.update_bytes(
            outbox_path(outbox_id),
            "application/json; charset=utf-8",
            _apply,
        )
        return captured

    def _require_claim(
        self,
        document: Mapping[str, Any],
        claim: Claim,
        *,
        mutation: bool = False,
    ) -> None:
        current = document.get("claim")
        if not isinstance(current, Mapping):
            raise StaleClaimError("outbox item is not claimed")
        if (
            current.get("owner") != claim.owner
            or current.get("claim_id") != claim.claim_id
            or current.get("execution_id") != claim.execution_id
            or current.get("fencing_token") != claim.fencing_token
            or document.get("fencing_token") != claim.fencing_token
        ):
            raise StaleClaimError("outbox claim is stale")
        expiry = _parse_time(str(current.get("lease_expires_at") or ""))
        if expiry is None or expiry <= self.now():
            raise StaleClaimError("outbox claim lease has expired")
        if mutation and (claim.read_only or current.get("read_only")):
            raise StaleClaimError("takeover claim is reconciliation-only")

    @staticmethod
    def _provider(document: Mapping[str, Any], provider: str) -> dict[str, Any]:
        leg = document.get("providers", {}).get(provider)
        if not isinstance(leg, dict):
            raise DistributionOutboxError(f"provider is not requested: {provider}")
        return leg

    @staticmethod
    def _refresh_aggregate(document: dict[str, Any]) -> None:
        results = [str(leg.get("result")) for leg in document["providers"].values()]
        all_public = bool(results) and all(
            result == "externally_verified_public" for result in results
        )
        if all_public:
            aggregate = "completed_public"
            reason = None
        else:
            priority = (
                "poisoned",
                "publication_unknown",
                "manual_handoff_required",
                "failed_terminal",
                "failed_retryable_pre_mutation",
                "pending_provider",
            )
            aggregate = next(
                (result for result in priority if result in results),
                "pending_provider",
            )
            reason = aggregate
        document["aggregate"] = {
            "result": aggregate,
            "externally_verified_public": all_public,
            "terminal_reason": reason,
        }
        document["state"] = "completed_public" if all_public else "verifying"


def reconciliation_message(outbox_id: str, provider: str, token: str) -> str:
    payload = {
        "schema_version": RECONCILIATION_QUEUE_SCHEMA_VERSION,
        "outbox_id": outbox_id,
        "provider": _require_token("provider", provider),
        "schedule_token": _require_token("schedule_token", token),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def aggregate_exit_code(documents: Iterable[Mapping[str, Any]]) -> int:
    items = list(documents)
    if not items:
        return 1
    return (
        0
        if all(
            item.get("aggregate", {}).get("externally_verified_public") is True for item in items
        )
        else 1
    )


def outbox_routing_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get("DISTRIBUTION_OUTBOX_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
