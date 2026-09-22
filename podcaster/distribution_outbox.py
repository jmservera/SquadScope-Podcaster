"""Durable fenced distribution outbox and reconciliation state machine."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
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
ARTIFACT_REFERENCE_PREFIX = "distribution-artifact-references"
ORPHAN_CLEANUP_STATE_PATH = "distribution-scheduler/orphan-cleanup-state.json"
RECONCILIATION_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-distribution-reconcile-v1"
INITIAL_RECONCILIATION_DELAY = timedelta(minutes=5)
VERIFICATION_PROOF_FIELDS = (
    "week_match",
    "publication_identity_match",
    "manifest_match",
    "publication_digest_match",
    "artifact_sha256_match",
    "canonical_artifact_selected",
    "provider_identity_match",
    "terminal_authoritative_readback",
    "duplicate_ambiguity_resolved",
)
RECOVERY_AUTHORIZATION_SCHEMA_VERSION = "distribution-recovery-authz-v4"
RECOVERY_AUTHORIZATION_SET_SCHEMA_VERSION = "distribution-recovery-authz-set-v1"
ATTEMPT_HISTORY_EVIDENCE_SCHEMA_VERSION = "distribution-attempt-history-evidence-v1"
ATTEMPT_RECORD_VERSION = "distribution-attempt-record-v1"

PROVIDER_RESULTS = frozenset(
    {
        "pending_provider",
        "externally_verified_public",
        "publication_unknown",
        "manual_handoff_required",
        "failed_retryable_pre_mutation",
        "failed_terminal",
        "partial",
        "poisoned",
        "identity_conflict",
    }
)
ACTIONABLE_RESULTS = frozenset(
    {
        "pending_provider",
        "publication_unknown",
        "manual_handoff_required",
        "failed_retryable_pre_mutation",
        "failed_terminal",
        "partial",
        "poisoned",
        "identity_conflict",
    }
)
RECOVERY_MUTATION_OPERATION_TYPES = {
    "youtube": {
        "upload": "create",
        "draft_upload": "create",
        "playlist_insert": "playlist_insert",
        "public_promotion": "publish",
        "recovery_mutation": "recovery_fixture",
        "recovery_readback": "readback",
        "fixture_readback": "readback",
        "historical_reconciliation": "historical",
    },
    "spotify": {
        "create": "create",
        "create_episode_intent": "create",
        "public_promotion": "publish",
        "recovery_mutation": "recovery_fixture",
        "recovery_readback": "readback",
        "fixture_readback": "readback",
        "historical_reconciliation": "historical",
    },
    "spotify_rss": {
        "rss_feed_publish": "publish",
        "historical_reconciliation": "historical",
    },
}
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
    attempt_id: str


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


def _receipt_follows_consumed_intent(receipt_at: Any, consumed_at: Any) -> bool:
    if not isinstance(receipt_at, str) or not isinstance(consumed_at, str):
        return False
    try:
        receipt_time = _parse_time(receipt_at)
        consumed_time = _parse_time(consumed_at)
    except ValueError:
        return False
    return receipt_time is not None and consumed_time is not None and receipt_time >= consumed_time


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
        if not all(type(item_key) is str for item_key in value):
            raise UnsafeOutboxValueError(f"non-string durable field in {key or 'value'}")
        return {
            item_key[:64]: _safe_value(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, key=key) for item in value]
    raise UnsafeOutboxValueError(f"unsupported durable value for {key or 'field'}")


def _load_outbox_document(raw: bytes) -> dict[str, Any]:
    def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DistributionOutboxError("outbox record contains duplicate fields")
            result[key] = value
        return result

    def _reject_non_finite_number(value: str) -> None:
        raise DistributionOutboxError(f"outbox record contains non-finite number: {value}")

    def _parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise DistributionOutboxError(f"outbox record contains non-finite number: {value}")
        return parsed

    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_non_finite_number,
            parse_float=_parse_finite_float,
        )
    except DistributionOutboxError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise DistributionOutboxError("outbox record is corrupt") from exc
    if type(document) is not dict:
        raise DistributionOutboxError("outbox record is malformed")
    return document


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
    source_ownership: Mapping[str, Any] | None = None,
    authorize: Callable[[], None] | None = None,
) -> ArtifactReference:
    digest, size = _sha256_file(source)
    path = artifact_path(digest, media_kind, suffix)
    existing_size = storage.blob_size(path)
    if existing_size is None:
        if authorize is not None:
            authorize()
        storage.upload_file(path, source, content_type)
    elif existing_size != size:
        raise OutboxConflictError("content-addressed artifact size conflicts with existing blob")
    verify_artifact(storage, ArtifactReference(path, digest, size, media_kind))
    metadata_path = f"{ARTIFACT_METADATA_PREFIX}/{digest}.json"

    def _metadata(raw: bytes | None) -> bytes:
        if authorize is not None:
            authorize()
        if raw is not None:
            return raw
        document = {
            "artifact_path": path,
            "sha256": digest,
            "size_bytes": size,
            "media_kind": _require_token("media_kind", media_kind),
            "created_at": _iso(utc_now()),
        }
        if source_ownership is not None:
            document["source_ownership"] = _safe_value(source_ownership)
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

    storage.update_bytes(
        metadata_path,
        "application/json; charset=utf-8",
        _metadata,
    )
    reference_path = f"{ARTIFACT_REFERENCE_PREFIX}/{digest}.json"

    def _restore_reference(raw: bytes | None) -> bytes:
        if authorize is not None:
            authorize()
        reference = json.loads(raw.decode("utf-8")) if raw else {}
        if reference.get("deleting") is True:
            raise OutboxConflictError("artifact cleanup is in progress")
        reference.update(
            {
                "artifact_sha256": digest,
                "outbox_ids": reference.get("outbox_ids", []),
                "deleting": False,
                "deleted": False,
                "cleanup_claim_id": None,
                "updated_at": _iso(utc_now()),
            }
        )
        return json.dumps(reference, sort_keys=True, separators=(",", ":")).encode("utf-8")

    storage.update_bytes(
        reference_path,
        "application/json; charset=utf-8",
        _restore_reference,
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


def exact_verification_proof(
    document: Mapping[str, Any],
    *,
    provider_item_id: str,
) -> dict[str, Any]:
    identity = document["publication_identity"]
    artifact = document["artifact"]
    canonical = document["canonical_artifact"]
    return {
        "week": identity["week"],
        "outbox_id": document["outbox_id"],
        "accepted_job_id": identity["accepted_job_id"],
        "publish_run_id": identity["publish_run_id"],
        "article_sha256": identity["article_sha256"],
        "manifest_sha256": identity["manifest_sha256"],
        "publication_digest": document["publication_digest"],
        "artifact_sha256": artifact["sha256"],
        "canonical_artifact_id": canonical["artifact_id"],
        "canonical_selection_version": canonical["selection_version"],
        "canonical_artifact_selected": canonical["selected"] is True,
        "provider_item_id": provider_item_id,
        "provider_identity_match": True,
        "terminal_authoritative_readback": True,
        "duplicate_ambiguity_resolved": True,
    }


def exact_recovery_authorization_evidence(
    document: Mapping[str, Any],
    *,
    predecessor_attempt_id: str,
    expected_provider_item_ids: Mapping[str, str],
) -> dict[str, Any]:
    identity = document["publication_identity"]
    artifact = document["artifact"]
    canonical = document["canonical_artifact"]
    attempts = document.get("attempts", [])
    predecessor = next(
        (
            attempt
            for attempt in attempts
            if isinstance(attempt, Mapping) and attempt.get("attempt_id") == predecessor_attempt_id
        ),
        None,
    )
    if not isinstance(predecessor, Mapping):
        raise DistributionOutboxError("recovery predecessor is missing")
    predecessor_index = next(
        index
        for index, attempt in enumerate(attempts)
        if isinstance(attempt, Mapping) and attempt.get("attempt_id") == predecessor_attempt_id
    )
    provider_evidence = _recovery_predecessor_provider_evidence(document, predecessor)
    objectives = document.get("provider_objectives")
    if not isinstance(objectives, Mapping):
        raise DistributionOutboxError("recovery provider objectives are missing")
    providers: dict[str, Any] = {}
    for provider in sorted(objectives):
        leg = provider_evidence.get(provider)
        if not isinstance(leg, Mapping):
            raise DistributionOutboxError("recovery predecessor provider evidence is missing")
        expected_succeeding_item = expected_provider_item_ids.get(provider)
        if not expected_succeeding_item:
            raise DistributionOutboxError("recovery succeeding provider identity is missing")
        providers[provider] = {
            "provider": provider,
            "objective": objectives[provider],
            "predecessor": _recovery_provider_binding(
                predecessor_attempt_id,
                provider,
                leg,
            ),
            "succeeding_provider": {
                "provider": provider,
                "objective": objectives[provider],
                "expected_provider_item_id": str(expected_succeeding_item),
            },
        }
    return {
        "schema_version": RECOVERY_AUTHORIZATION_SCHEMA_VERSION,
        "outbox_id": document["outbox_id"],
        "publication_identity": dict(identity),
        "publication_digest": document["publication_digest"],
        "artifact": dict(artifact),
        "canonical_artifact": dict(canonical),
        "attempt_history": _canonical_attempt_history_evidence(
            document,
            predecessor_index=predecessor_index,
        ),
        "predecessor_attempt_id": predecessor_attempt_id,
        "providers": providers,
    }


def _canonical_typed_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null", "value": None}
    if type(value) is bool:
        return {"type": "boolean", "value": value}
    if type(value) is int:
        return {"type": "integer", "value": value}
    if type(value) is float:
        raise DistributionOutboxError("canonical recovery data contains an unsupported number")
    if type(value) is str:
        return {"type": "string", "value": value}
    if type(value) is dict:
        if not all(type(key) is str for key in value):
            raise DistributionOutboxError("canonical recovery data contains a non-string field")
        entries = []
        for key in sorted(value):
            entries.append({"key": key, "value": _canonical_typed_value(value[key])})
        return {"type": "object", "entries": entries}
    if type(value) is list:
        return {
            "type": "array",
            "items": [_canonical_typed_value(item) for item in value],
        }
    raise DistributionOutboxError("canonical recovery data contains an unsupported value")


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DistributionOutboxError("canonical recovery data is not valid JSON") from exc


def _exact_canonical_equal(actual: Any, expected: Any) -> bool:
    return _canonical_json_bytes(_canonical_typed_value(actual)) == _canonical_json_bytes(
        _canonical_typed_value(expected)
    )


def _require_versioned_canonical_contract(
    actual: Any,
    expected: dict[str, Any],
    *,
    schema_version: str,
    label: str,
) -> None:
    if (
        type(actual) is not dict
        or set(actual) != set(expected)
        or type(actual.get("schema_version")) is not str
        or actual.get("schema_version") != schema_version
        or not _exact_canonical_equal(actual, expected)
    ):
        raise DistributionOutboxError(f"{label} is invalid")


def _validated_attempt_history_records(
    attempts: Any,
    *,
    predecessor_index: int,
) -> list[Mapping[str, Any]]:
    if not isinstance(attempts, list) or predecessor_index < 0:
        raise DistributionOutboxError("recovery attempt history is missing")
    if predecessor_index >= len(attempts):
        raise DistributionOutboxError("recovery attempt history boundary is invalid")
    records = attempts[: predecessor_index + 1]
    attempt_ids: set[str] = set()
    previous_authorized_at: datetime | None = None
    for attempt_order, attempt in enumerate(records):
        if type(attempt) is not dict:
            raise DistributionOutboxError("recovery attempt history is malformed")
        if (
            type(attempt.get("record_version")) is not str
            or attempt.get("record_version") != ATTEMPT_RECORD_VERSION
        ):
            raise DistributionOutboxError("recovery attempt record version is unsupported")
        attempt_id = attempt.get("attempt_id")
        if type(attempt_id) is not str or not attempt_id or attempt_id in attempt_ids:
            raise DistributionOutboxError("recovery attempt identity is duplicate or missing")
        attempt_ids.add(attempt_id)
        if type(attempt.get("terminal_outcome")) is not str or not attempt["terminal_outcome"]:
            raise DistributionOutboxError("recovery attempt history contains a non-terminal record")
        authorized_at = attempt.get("authorized_at")
        if type(authorized_at) is not str:
            raise DistributionOutboxError("recovery attempt authorization time is missing")
        try:
            parsed_authorized_at = _parse_time(authorized_at)
        except ValueError as exc:
            raise DistributionOutboxError("recovery attempt authorization time is invalid") from exc
        if parsed_authorized_at is None or (
            previous_authorized_at is not None and parsed_authorized_at < previous_authorized_at
        ):
            raise DistributionOutboxError("recovery attempt authorization order is ambiguous")
        previous_authorized_at = parsed_authorized_at
        predecessor_attempt_id = attempt.get("predecessor_attempt_id")
        if attempt_order == 0:
            if predecessor_attempt_id is not None:
                raise DistributionOutboxError("initial attempt predecessor is invalid")
        elif type(predecessor_attempt_id) is not str or predecessor_attempt_id != records[
            attempt_order - 1
        ].get("attempt_id"):
            raise DistributionOutboxError("recovery attempt linkage is invalid")
        events = attempt.get("events")
        if type(events) is not list or not events:
            raise DistributionOutboxError("recovery attempt events are missing")
        previous_event_at: datetime | None = None
        for event_order, event in enumerate(events, start=1):
            if type(event) is not dict:
                raise DistributionOutboxError("recovery attempt event is malformed")
            sequence = event.get("sequence")
            if type(sequence) is not int or sequence != event_order:
                raise DistributionOutboxError("recovery attempt event sequence is invalid")
            if (
                type(event.get("event_type")) is not str
                or type(event.get("state")) is not str
                or event.get("event_type") != event.get("state")
                or not event.get("state")
            ):
                raise DistributionOutboxError("recovery attempt event type is invalid")
            event_at = event.get("at")
            if type(event_at) is not str:
                raise DistributionOutboxError("recovery attempt event time is missing")
            try:
                parsed_event_at = _parse_time(event_at)
            except ValueError as exc:
                raise DistributionOutboxError("recovery attempt event time is invalid") from exc
            if parsed_event_at is None or (
                previous_event_at is not None and parsed_event_at < previous_event_at
            ):
                raise DistributionOutboxError("recovery attempt event order is ambiguous")
            previous_event_at = parsed_event_at
    return records


def _canonical_attempt_history_evidence(
    document: Mapping[str, Any],
    *,
    predecessor_index: int,
) -> dict[str, Any]:
    records = _validated_attempt_history_records(
        document.get("attempts"),
        predecessor_index=predecessor_index,
    )
    identity = document.get("publication_identity")
    if not isinstance(identity, Mapping):
        raise DistributionOutboxError("recovery publication identity is missing")
    envelope = {
        "schema_version": ATTEMPT_HISTORY_EVIDENCE_SCHEMA_VERSION,
        "context": _canonical_typed_value(
            {
                "outbox_id": document.get("outbox_id"),
                "publication_identity": dict(identity),
                "publication_digest": document.get("publication_digest"),
                "artifact": dict(document.get("artifact", {})),
                "canonical_artifact": dict(document.get("canonical_artifact", {})),
                "provider_objectives": dict(document.get("provider_objectives", {})),
            }
        ),
        "boundary": {
            "predecessor_attempt_id": records[-1]["attempt_id"],
            "predecessor_attempt_index": predecessor_index,
            "bound_attempt_count": len(records),
            "bound_event_count": sum(len(attempt["events"]) for attempt in records),
        },
        "records": [_canonical_typed_value(dict(attempt)) for attempt in records],
    }
    envelope["digest"] = hashlib.sha256(_canonical_json_bytes(envelope)).hexdigest()
    return envelope


def _successor_authorization_expectation(attempt: Mapping[str, Any]) -> dict[str, Any]:
    semantic_record = {
        key: value for key, value in attempt.items() if key != "recovery_proof_digest"
    }
    return {
        "initial_record": _canonical_typed_value(semantic_record),
        "immutable_identity": _canonical_typed_value(
            {
                key: attempt.get(key)
                for key in (
                    "record_version",
                    "attempt_id",
                    "authz_id",
                    "authz_source",
                    "authz_reason",
                    "authorized_at",
                    "predecessor_attempt_id",
                )
            }
        ),
        "separately_bound_fields": ["recovery_proof_digest"],
    }


def _recovery_operation_type(provider: str, operation: Any) -> str:
    operation_name = str(operation or "")
    operation_type = RECOVERY_MUTATION_OPERATION_TYPES.get(provider, {}).get(operation_name)
    if not operation_type:
        raise DistributionOutboxError("recovery predecessor operation is invalid")
    return operation_type


def _recovery_provider_binding(
    predecessor_attempt_id: str,
    provider: str,
    leg: Mapping[str, Any],
) -> dict[str, Any]:
    verification = leg.get("verification")
    if not isinstance(verification, Mapping):
        raise DistributionOutboxError("recovery predecessor readback is missing")
    intent = leg.get("intent")
    receipts = leg.get("receipts")
    intent_binding = None
    receipt_binding = None
    if isinstance(intent, Mapping):
        operation = str(intent.get("operation") or "")
        intent_binding = {
            "intent_id": intent.get("intent_id"),
            "provider": intent.get("provider"),
            "operation": operation,
            "operation_type": _recovery_operation_type(provider, operation),
            "expected_provider_item_id": intent.get("expected_provider_item_id"),
            "created_at": intent.get("created_at"),
            "created_fence": intent.get("created_fence"),
            "consumed_at": intent.get("consumed_at"),
            "consumed_fence": intent.get("consumed_fence"),
            "consumed_owner": intent.get("consumed_owner"),
            "consumed_attempt_id": intent.get("consumed_attempt_id"),
        }
        if not isinstance(receipts, list) or len(receipts) != 1:
            raise DistributionOutboxError("recovery predecessor receipt is invalid")
        receipt = receipts[0]
        if not isinstance(receipt, Mapping):
            raise DistributionOutboxError("recovery predecessor receipt is invalid")
        receipt_binding = {
            "receipt_id": receipt.get("receipt_id"),
            "intent_id": receipt.get("intent_id"),
            "provider": receipt.get("provider"),
            "operation": receipt.get("operation"),
            "operation_type": receipt.get("operation_type"),
            "attempt_id": receipt.get("attempt_id"),
            "consumed_owner": receipt.get("consumed_owner"),
            "at": receipt.get("at"),
            "fencing_token": receipt.get("fencing_token"),
            "transport_class": receipt.get("transport_class"),
            "provider_item_id": receipt.get("provider_item_id"),
            "native_state": receipt.get("native_state"),
            "ambiguous": receipt.get("ambiguous"),
            "code": receipt.get("code"),
        }
    elif receipts not in (None, []):
        raise DistributionOutboxError("recovery predecessor receipt is invalid")
    return {
        "attempt_id": predecessor_attempt_id,
        "provider": provider,
        "result": leg.get("result"),
        "intent": intent_binding,
        "receipt": receipt_binding,
        "terminal_readback": {
            "at": verification.get("at"),
            "source": verification.get("source"),
            "provider_item_id": verification.get("provider_item_id"),
            "native_state": verification.get("native_state"),
            "result": verification.get("result"),
            "fencing_token": verification.get("fencing_token"),
        },
        "mutation_safe": leg.get("result") == "failed_terminal",
        "duplicate_ambiguity_resolved": True,
    }


def _recovery_predecessor_provider_evidence(
    document: Mapping[str, Any],
    predecessor: Mapping[str, Any],
) -> Mapping[str, Any]:
    if predecessor.get("terminal_outcome") == "failed_terminal":
        provider_evidence = predecessor.get("provider_evidence")
        if isinstance(provider_evidence, Mapping):
            return provider_evidence
        raise DistributionOutboxError("recovery predecessor evidence is missing")

    readbacks = predecessor.get("post_terminal_readbacks")
    objectives = document.get("provider_objectives")
    unresolved_message = (
        "unknown provider mutation cannot authorize retry"
        if predecessor.get("terminal_outcome") == "provider_unknown"
        else "recovery predecessor remains unresolved"
    )
    if not isinstance(readbacks, list) or not isinstance(objectives, Mapping):
        raise DistributionOutboxError(unresolved_message)
    provider_evidence = predecessor.get("provider_evidence")
    if not isinstance(provider_evidence, Mapping) or set(provider_evidence) != set(objectives):
        raise DistributionOutboxError(unresolved_message)
    expected_provider_items: dict[str, str] = {}
    exact_provider_evidence: dict[str, dict[str, Any]] = {}
    for provider in objectives:
        leg = provider_evidence.get(provider)
        intent = leg.get("intent") if isinstance(leg, Mapping) else None
        receipts = leg.get("receipts") if isinstance(leg, Mapping) else None
        verification = leg.get("verification") if isinstance(leg, Mapping) else None
        if (
            not isinstance(intent, Mapping)
            or intent.get("provider") != provider
            or not intent.get("operation")
            or not intent.get("consumed_at")
            or intent.get("consumed_fence") is None
            or not intent.get("consumed_owner")
            or intent.get("consumed_attempt_id") != predecessor.get("attempt_id")
            or not intent.get("intent_id")
            or not intent.get("expected_provider_item_id")
            or not isinstance(receipts, list)
            or len(receipts) != 1
        ):
            raise DistributionOutboxError(unresolved_message)
        operation = str(intent["operation"])
        operation_type = _recovery_operation_type(provider, operation)
        if operation_type == "readback":
            raise DistributionOutboxError(unresolved_message)
        expected_item = str(intent["expected_provider_item_id"])
        receipt = receipts[0]
        if (
            not isinstance(receipt, Mapping)
            or not receipt.get("receipt_id")
            or receipt.get("intent_id") != intent.get("intent_id")
            or receipt.get("provider") != provider
            or receipt.get("operation") != operation
            or receipt.get("operation_type") != operation_type
            or receipt.get("attempt_id") != predecessor.get("attempt_id")
            or receipt.get("consumed_owner") != intent.get("consumed_owner")
            or receipt.get("transport_class") != "accepted"
            or receipt.get("ambiguous") is not False
            or receipt.get("provider_item_id") != expected_item
            or not receipt.get("native_state")
            or not receipt.get("at")
            or not _receipt_follows_consumed_intent(receipt.get("at"), intent.get("consumed_at"))
            or receipt.get("fencing_token") != intent.get("consumed_fence")
        ):
            raise DistributionOutboxError(unresolved_message)
        candidates = {expected_item, str(receipt["provider_item_id"])}
        if isinstance(verification, Mapping) and verification.get("provider_item_id"):
            candidates.add(str(verification["provider_item_id"]))
        if candidates != {expected_item}:
            raise DistributionOutboxError(unresolved_message)
        expected_provider_items[provider] = expected_item
        exact_provider_evidence[provider] = {
            "intent": intent,
            "receipts": [receipt],
            "verification": verification,
            "result": leg.get("result"),
        }
    latest: dict[str, Mapping[str, Any]] = {}
    for readback in readbacks:
        if not isinstance(readback, Mapping):
            raise DistributionOutboxError(unresolved_message)
        provider = str(readback.get("provider") or "")
        if provider not in objectives or provider in latest:
            raise DistributionOutboxError(unresolved_message)
        latest[provider] = readback
    if set(latest) != set(objectives):
        raise DistributionOutboxError(unresolved_message)
    resolved: dict[str, Any] = {}
    for provider in objectives:
        verification = latest[provider].get("verification")
        if (
            not isinstance(verification, Mapping)
            or verification.get("result") != "failed_terminal"
            or "readback" not in str(verification.get("source") or "")
            or verification.get("provider_item_id") != expected_provider_items[provider]
            or not verification.get("native_state")
        ):
            raise DistributionOutboxError(unresolved_message)
        resolved[provider] = {
            "intent": exact_provider_evidence[provider]["intent"],
            "receipts": exact_provider_evidence[provider]["receipts"],
            "verification": verification,
            "result": "failed_terminal",
        }
    return resolved


def _validated_recovery_authorization_evidence(
    document: Mapping[str, Any],
    predecessor: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    succeeding_attempt_id: str | None = None,
    successor_may_progress: bool = False,
    allow_following_attempts: bool = False,
) -> dict[str, Any]:
    _canonical_typed_value(evidence)
    value = _safe_value(evidence, key="recovery_evidence")
    attempts = document.get("attempts", [])
    predecessor_index = next(
        (
            index
            for index, attempt in enumerate(attempts)
            if isinstance(attempt, Mapping)
            and attempt.get("attempt_id") == predecessor.get("attempt_id")
        ),
        -1,
    )
    later_attempts = attempts[predecessor_index + 1 :]
    if succeeding_attempt_id is None:
        if later_attempts:
            raise DistributionOutboxError("recovery predecessor is not the latest attempt")
    elif (
        (not allow_following_attempts and len(later_attempts) != 1)
        or not later_attempts
        or not isinstance(later_attempts[0], Mapping)
        or later_attempts[0].get("attempt_id") != succeeding_attempt_id
        or later_attempts[0].get("predecessor_attempt_id") != predecessor.get("attempt_id")
    ):
        raise DistributionOutboxError("recovery authorization attempt history is invalid")
    providers = value.get("providers")
    objectives = document.get("provider_objectives")
    if not isinstance(providers, Mapping) or not isinstance(objectives, Mapping):
        raise DistributionOutboxError("recovery authorization provider evidence is incomplete")
    if set(providers) != set(objectives):
        raise DistributionOutboxError("recovery authorization provider evidence is incomplete")
    expected_succeeding_items: dict[str, str] = {}
    for provider in objectives:
        authorization_leg = providers.get(provider)
        succeeding_provider = (
            authorization_leg.get("succeeding_provider")
            if isinstance(authorization_leg, Mapping)
            else None
        )
        expected_item = (
            succeeding_provider.get("expected_provider_item_id")
            if isinstance(succeeding_provider, Mapping)
            else None
        )
        if not expected_item:
            raise DistributionOutboxError("recovery authorization provider evidence is incomplete")
        expected_succeeding_items[provider] = str(expected_item)
    expected_value = exact_recovery_authorization_evidence(
        document,
        predecessor_attempt_id=str(predecessor.get("attempt_id") or ""),
        expected_provider_item_ids=expected_succeeding_items,
    )
    if succeeding_attempt_id is not None:
        current_expectation = _successor_authorization_expectation(later_attempts[0])
        stored_succeeding_attempt = value.get("succeeding_attempt")
        stored_expectation = (
            stored_succeeding_attempt.get("expectation")
            if isinstance(stored_succeeding_attempt, Mapping)
            else None
        )
        if successor_may_progress:
            if (
                type(stored_expectation) is not dict
                or not _exact_canonical_equal(
                    stored_expectation.get("immutable_identity"),
                    current_expectation["immutable_identity"],
                )
                or not _exact_canonical_equal(
                    stored_expectation.get("separately_bound_fields"),
                    ["recovery_proof_digest"],
                )
            ):
                raise DistributionOutboxError("recovery authorization successor is invalid")
            succeeding_expectation = dict(stored_expectation)
        else:
            succeeding_expectation = current_expectation
        expected_value["succeeding_attempt"] = {
            "attempt_id": succeeding_attempt_id,
            "predecessor_attempt_id": predecessor.get("attempt_id"),
            "providers": {
                provider: expected_value["providers"][provider]["succeeding_provider"]
                for provider in sorted(objectives)
            },
            "expectation": succeeding_expectation,
        }
    _require_versioned_canonical_contract(
        value,
        expected_value,
        schema_version=RECOVERY_AUTHORIZATION_SCHEMA_VERSION,
        label="recovery authorization identity evidence",
    )
    return value


_RECOVERY_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema_version",
        "authz_id",
        "authz_version",
        "source",
        "reason",
        "authorized_at",
        "actor",
        "owner",
        "status",
        "superseded_by_authz_id",
        "predecessor_attempt_id",
        "successor_attempt_id",
        "evidence_schema_version",
        "evidence_digest",
        "evidence",
        "extensions",
    }
)


def _exact_type(value: Any, expected: type) -> bool:
    return type(value) is expected


def _authorization_set_document(authorizations: list[Mapping[str, Any]]) -> dict[str, Any]:
    ordered_ids = [authorization["authz_id"] for authorization in authorizations]
    envelope = {
        "schema_version": RECOVERY_AUTHORIZATION_SET_SCHEMA_VERSION,
        "authz_count": len(authorizations),
        "ordered_authz_ids": ordered_ids,
        "active_authz_id": ordered_ids[-1] if ordered_ids else None,
    }
    digest_input = {
        "set": envelope,
        "authorizations": [dict(authorization) for authorization in authorizations],
    }
    envelope["digest"] = hashlib.sha256(
        _canonical_json_bytes(_canonical_typed_value(digest_input))
    ).hexdigest()
    return envelope


def _validate_recovery_authorization_set(
    actual: Any,
    expected: dict[str, Any],
) -> None:
    if (
        type(actual) is not dict
        or set(actual) != set(expected)
        or type(actual.get("schema_version")) is not str
        or type(actual.get("authz_count")) is not int
        or type(actual.get("ordered_authz_ids")) is not list
        or not all(type(item) is str for item in actual.get("ordered_authz_ids", []))
        or (
            actual.get("active_authz_id") is not None
            and type(actual.get("active_authz_id")) is not str
        )
        or type(actual.get("digest")) is not str
    ):
        raise DistributionOutboxError("recovery authorization set schema is invalid")
    _require_versioned_canonical_contract(
        actual,
        expected,
        schema_version=RECOVERY_AUTHORIZATION_SET_SCHEMA_VERSION,
        label="recovery authorization set binding",
    )


def _refresh_recovery_authorization_set(document: dict[str, Any]) -> None:
    authorizations = document.get("recovery_authz")
    if not isinstance(authorizations, list) or not all(
        isinstance(item, Mapping) for item in authorizations
    ):
        raise DistributionOutboxError("recovery authorization collection is malformed")
    document["recovery_authz_set"] = _authorization_set_document(authorizations)


def _expected_recovery_authorization(
    document: Mapping[str, Any],
    authorization: Mapping[str, Any],
    predecessor: Mapping[str, Any],
    successor: Mapping[str, Any],
    *,
    status: str,
    superseded_by_authz_id: str | None,
    successor_may_progress: bool,
) -> dict[str, Any]:
    evidence = authorization.get("evidence")
    if not isinstance(evidence, Mapping):
        raise DistributionOutboxError("recovery authorization evidence is missing")
    validated = _validated_recovery_authorization_evidence(
        document,
        predecessor,
        evidence,
        succeeding_attempt_id=str(successor.get("attempt_id") or ""),
        successor_may_progress=successor_may_progress,
        allow_following_attempts=True,
    )
    digest = hashlib.sha256(_canonical_json_bytes(validated)).hexdigest()
    return {
        "schema_version": RECOVERY_AUTHORIZATION_SCHEMA_VERSION,
        "authz_id": successor.get("authz_id"),
        "authz_version": 4,
        "source": successor.get("authz_source"),
        "reason": successor.get("authz_reason"),
        "authorized_at": successor.get("authorized_at"),
        "actor": None,
        "owner": None,
        "status": status,
        "superseded_by_authz_id": superseded_by_authz_id,
        "predecessor_attempt_id": predecessor.get("attempt_id"),
        "successor_attempt_id": successor.get("attempt_id"),
        "evidence_schema_version": validated.get("schema_version"),
        "evidence_digest": digest,
        "evidence": validated,
        "extensions": {},
    }


def _validated_recovery_authorization_collection(
    document: Mapping[str, Any],
    authorizations: Iterable[Mapping[str, Any]],
    *,
    active_successor_may_progress: bool,
) -> list[Mapping[str, Any]]:
    if not isinstance(authorizations, list):
        raise DistributionOutboxError("recovery authorization collection is malformed")
    attempts = document.get("attempts")
    if not isinstance(attempts, list):
        raise DistributionOutboxError("recovery authorization attempts are missing")
    recovery_attempts = attempts[1:]
    if len(authorizations) != len(recovery_attempts):
        raise DistributionOutboxError("recovery authorization cardinality is invalid")
    validated: list[Mapping[str, Any]] = []
    for index, authorization in enumerate(authorizations):
        if type(authorization) is not dict or set(authorization) != (
            _RECOVERY_AUTHORIZATION_FIELDS
        ):
            raise DistributionOutboxError("recovery authorization envelope is invalid")
        if (
            not _exact_type(authorization.get("schema_version"), str)
            or not _exact_type(authorization.get("authz_id"), str)
            or not _exact_type(authorization.get("authz_version"), int)
            or not _exact_type(authorization.get("source"), str)
            or not _exact_type(authorization.get("reason"), str)
            or not _exact_type(authorization.get("authorized_at"), str)
            or authorization.get("actor") is not None
            or authorization.get("owner") is not None
            or not _exact_type(authorization.get("status"), str)
            or (
                authorization.get("superseded_by_authz_id") is not None
                and not _exact_type(authorization.get("superseded_by_authz_id"), str)
            )
            or not _exact_type(authorization.get("predecessor_attempt_id"), str)
            or not _exact_type(authorization.get("successor_attempt_id"), str)
            or not _exact_type(authorization.get("evidence_schema_version"), str)
            or not _exact_type(authorization.get("evidence_digest"), str)
            or type(authorization.get("evidence")) is not dict
            or not _exact_type(authorization.get("extensions"), dict)
        ):
            raise DistributionOutboxError("recovery authorization envelope type is invalid")
        successor = recovery_attempts[index]
        predecessor = attempts[index]
        if not isinstance(successor, Mapping) or not isinstance(predecessor, Mapping):
            raise DistributionOutboxError("recovery authorization attempt boundary is invalid")
        next_authorization_id = (
            recovery_attempts[index + 1].get("authz_id")
            if index + 1 < len(recovery_attempts)
            and isinstance(recovery_attempts[index + 1], Mapping)
            else None
        )
        expected = _expected_recovery_authorization(
            document,
            authorization,
            predecessor,
            successor,
            status="superseded" if next_authorization_id else "active",
            superseded_by_authz_id=(str(next_authorization_id) if next_authorization_id else None),
            successor_may_progress=(
                True if next_authorization_id else active_successor_may_progress
            ),
        )
        if not _exact_canonical_equal(authorization, expected):
            raise DistributionOutboxError("recovery authorization envelope binding is invalid")
        validated.append(authorization)
    set_document = document.get("recovery_authz_set")
    expected_set = _authorization_set_document(validated)
    _validate_recovery_authorization_set(set_document, expected_set)
    return validated


def _recovery_authorization_binding_is_valid(
    document: Mapping[str, Any],
    winner: Mapping[str, Any],
    authorizations: Iterable[Mapping[str, Any]],
    *,
    successor_may_progress: bool = False,
) -> bool:
    try:
        validated = _validated_recovery_authorization_collection(
            document,
            authorizations,
            active_successor_may_progress=successor_may_progress,
        )
    except (DistributionOutboxError, UnsafeOutboxValueError):
        return False
    matching = [
        authorization
        for authorization in validated
        if authorization.get("authz_id") == winner.get("authz_id")
        and authorization.get("successor_attempt_id") == winner.get("attempt_id")
        and authorization.get("predecessor_attempt_id") == winner.get("predecessor_attempt_id")
        and authorization.get("status") == "active"
    ]
    if len(matching) != 1:
        return False
    authorization = matching[0]
    return (
        authorization.get("evidence_digest") == winner.get("recovery_proof_digest")
        and authorization.get("evidence_schema_version") == RECOVERY_AUTHORIZATION_SCHEMA_VERSION
    )


def _recovery_authorization_is_valid(
    document: Mapping[str, Any],
    winner: Mapping[str, Any],
    authorizations: Iterable[Mapping[str, Any]],
) -> bool:
    if not _recovery_authorization_binding_is_valid(
        document,
        winner,
        authorizations,
        successor_may_progress=True,
    ):
        return False
    authorization = next(
        item
        for item in authorizations
        if isinstance(item, Mapping) and item.get("authz_id") == winner.get("authz_id")
    )
    validated = authorization["evidence"]
    providers = document.get("providers")
    if not isinstance(providers, Mapping):
        return False
    for provider, authorization_leg in validated["providers"].items():
        leg = providers.get(provider)
        verification = leg.get("verification") if isinstance(leg, Mapping) else None
        if (
            not isinstance(verification, Mapping)
            or verification.get("provider_item_id")
            != authorization_leg.get("succeeding_provider", {}).get("expected_provider_item_id")
            or str(verification.get("native_state") or "").lower() not in ("public", "published")
            or (
                "readback" not in str(verification.get("source") or "")
                and verification.get("source") not in ("youtube_videos_list", "provider_readback")
            )
        ):
            return False
    return True


def _publication_digest(identity: Mapping[str, Any], artifact: Mapping[str, Any]) -> str:
    values = (
        str(identity.get("accepted_job_id") or ""),
        str(identity.get("week") or ""),
        str(identity.get("publish_run_id") or ""),
        str(identity.get("article_sha256") or ""),
        str(identity.get("manifest_sha256") or ""),
        str(artifact.get("sha256") or ""),
        str(artifact.get("media_kind") or ""),
    )
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def _provider_approval(
    approval: Mapping[str, Any] | None,
    *,
    publication_digest: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    denied = {
        "approved": False,
        "approved_by": None,
        "approved_at": None,
        "source": None,
        "publication_digest": publication_digest,
        "manifest_sha256": manifest_sha256,
    }
    if not isinstance(approval, Mapping) or approval.get("approved") is not True:
        return denied
    approved_by = str(approval.get("approved_by") or "").strip()
    approved_at = str(approval.get("approved_at") or "").strip()
    source = str(approval.get("source") or "").strip()
    if (
        not approved_by
        or approved_by.lower().startswith("system:")
        or not approved_at
        or source != "manifest_human_review"
    ):
        return denied
    _require_token("approved_by", approved_by)
    try:
        _parse_time(approved_at)
    except ValueError:
        return denied
    return {
        "approved": True,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "source": source,
        "publication_digest": publication_digest,
        "manifest_sha256": manifest_sha256,
    }


def provider_approval_is_valid(
    document: Mapping[str, Any],
    provider: str,
) -> bool:
    providers = document.get("providers")
    identity = document.get("publication_identity")
    if not isinstance(providers, Mapping) or not isinstance(identity, Mapping):
        return False
    leg = providers.get(provider)
    approval = leg.get("approval") if isinstance(leg, Mapping) else None
    approved_at = approval.get("approved_at") if isinstance(approval, Mapping) else None
    try:
        parsed_approval_time = _parse_time(approved_at) if isinstance(approved_at, str) else None
    except ValueError:
        parsed_approval_time = None
    return bool(
        isinstance(approval, Mapping)
        and approval.get("approved") is True
        and isinstance(approval.get("approved_by"), str)
        and approval["approved_by"]
        and not approval["approved_by"].lower().startswith("system:")
        and parsed_approval_time is not None
        and approval.get("source") == "manifest_human_review"
        and approval.get("publication_digest") == document.get("publication_digest")
        and approval.get("manifest_sha256") == identity.get("manifest_sha256")
    )


def _attempt_event(attempt: dict[str, Any], state: str, at: str, **details: Any) -> None:
    event = {
        "sequence": len(attempt["events"]) + 1,
        "event_type": state,
        "at": at,
        "state": state,
    }
    event.update({key: value for key, value in details.items() if value is not None})
    attempt["events"].append(event)
    attempt["state"] = state


def _ensure_truth_fields(document: dict[str, Any]) -> None:
    identity = document.get("publication_identity")
    artifact = document.get("artifact")
    if not isinstance(identity, Mapping) or not isinstance(artifact, Mapping):
        return
    digest = _publication_digest(identity, artifact)
    document.setdefault("publication_digest", digest)
    document.setdefault(
        "canonical_artifact",
        {
            "artifact_id": str(artifact.get("sha256") or ""),
            "path": str(artifact.get("path") or ""),
            "sha256": str(artifact.get("sha256") or ""),
            "media_kind": str(artifact.get("media_kind") or ""),
            "selected": True,
            "selection_reason": "content_addressed_render",
            "selection_version": "v1",
        },
    )
    attempts = document.setdefault("attempts", [])
    if not attempts:
        timestamp = str(document.get("created_at") or _iso(utc_now()))
        attempt_id = uuid.uuid4().hex
        attempts.append(
            {
                "record_version": ATTEMPT_RECORD_VERSION,
                "attempt_id": attempt_id,
                "authz_id": hashlib.sha256(
                    f"initial|{document.get('outbox_id')}|{attempt_id}".encode()
                ).hexdigest(),
                "authz_source": "initial_enqueue",
                "authz_reason": "accepted_publication",
                "authorized_at": timestamp,
                "predecessor_attempt_id": None,
                "state": "pending",
                "terminal_outcome": None,
                "events": [
                    {
                        "sequence": 1,
                        "event_type": "accepted",
                        "at": timestamp,
                        "state": "accepted",
                    }
                ],
                "proof_references": [],
            }
        )
    document.setdefault("recovery_authz", [])
    if not document["recovery_authz"] and "recovery_authz_set" not in document:
        _refresh_recovery_authorization_set(document)
    document.setdefault(
        "weekly_aggregation",
        {
            "decision_id": hashlib.sha256(
                f"weekly|{document.get('outbox_id')}|v1".encode()
            ).hexdigest(),
            "rule_version": "weekly-publication-truth-v1",
            "evaluated_attempt_ids": [attempt["attempt_id"] for attempt in attempts],
            "state": "pending",
            "winning_attempt_id": None,
            "proof_references": [],
            "unresolved_conditions": [],
            "decided_at": str(document.get("updated_at") or document.get("created_at")),
            "worker_exit_class": "nonzero",
        },
    )
    enqueue = document.setdefault("enqueue", {})
    intent = enqueue.get("notification_intent")
    if isinstance(intent, dict):
        if intent.get("accepted_at"):
            intent["state"] = "accepted"
        elif intent.get("consumed_at"):
            intent["state"] = "ambiguous"
            enqueue["notification_sent_at"] = None
        else:
            intent.setdefault("state", "reserved")


def _ensure_provider_reconciliation(
    document: dict[str, Any],
    *,
    due_at: datetime,
) -> bool:
    changed = False
    outbox_id = str(document.get("outbox_id") or "")
    for provider, leg in document.get("providers", {}).items():
        if (
            not isinstance(leg, dict)
            or leg.get("result") != "pending_provider"
            or leg.get("active_schedule_token")
        ):
            continue
        token = hashlib.sha256(f"{outbox_id}|{provider}|{_iso(due_at)}".encode("utf-8")).hexdigest()
        leg["next_reconcile_at"] = _iso(due_at)
        leg["active_schedule_token"] = token
        leg["active_schedule_source"] = "initial_notification"
        leg["schedule_notification_token"] = None
        leg["schedule_notification_sent_at"] = None
        leg["notification_reservation"] = None
        changed = True
    return changed


def _active_attempt(document: Mapping[str, Any]) -> dict[str, Any]:
    attempts = document.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise DistributionOutboxError("attempt ledger is missing")
    attempt = attempts[-1]
    if not isinstance(attempt, dict):
        raise DistributionOutboxError("attempt ledger is malformed")
    return attempt


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
        document = _load_outbox_document(raw)
        _ensure_truth_fields(document)
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
        source_ownership: Mapping[str, Any] | None = None,
        authorize: Callable[[], None] | None = None,
        provider_approvals: Mapping[str, Mapping[str, Any]] | None = None,
        provider_context: Mapping[str, Mapping[str, Any]] | None = None,
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
        publication_digest = _publication_digest(
            expected["publication_identity"],
            expected["artifact"],
        )
        approvals = provider_approvals or {}
        contexts = provider_context or {}

        def _create(raw: bytes | None) -> bytes:
            nonlocal created
            if authorize is not None:
                authorize()
            if raw is not None:
                existing = _load_outbox_document(raw)
                if (
                    existing.get("publication_identity") != expected["publication_identity"]
                    or existing.get("artifact") != expected["artifact"]
                    or existing.get("provider_objectives") != expected["provider_objectives"]
                ):
                    raise OutboxConflictError("logical outbox item conflicts with existing record")
                if source_ownership is not None:
                    existing["source_ownership"] = _safe_value(source_ownership)
                for provider in provider_objectives:
                    leg = existing.get("providers", {}).get(provider)
                    if not isinstance(leg, dict):
                        raise OutboxConflictError("existing provider leg is malformed")
                    incoming_approval = _provider_approval(
                        approvals.get(provider),
                        publication_digest=publication_digest,
                        manifest_sha256=identity.manifest_sha256,
                    )
                    if incoming_approval["approved"] is True:
                        current_approval = leg.get("approval")
                        if (
                            isinstance(current_approval, Mapping)
                            and current_approval.get("approved") is True
                            and current_approval != incoming_approval
                        ):
                            raise OutboxConflictError(
                                "provider approval conflicts with existing record"
                            )
                        leg["approval"] = incoming_approval
                    incoming_context = _safe_value(dict(contexts.get(provider, {})))
                    if incoming_context:
                        current_context = leg.get("context")
                        if current_context not in (None, {}, incoming_context):
                            raise OutboxConflictError(
                                "provider context conflicts with existing record"
                            )
                        leg["context"] = incoming_context
                _ensure_truth_fields(existing)
                _ensure_provider_reconciliation(
                    existing,
                    due_at=self.now() + INITIAL_RECONCILIATION_DELAY,
                )
                _validate_document(existing, item_id)
                captured.update(existing)
                return json.dumps(existing, sort_keys=True, separators=(",", ":")).encode("utf-8")
            timestamp = _iso(self.now())
            providers = {
                _require_token("provider", provider): {
                    "objective": _require_token("objective", objective),
                    "approval": _provider_approval(
                        approvals.get(provider),
                        publication_digest=publication_digest,
                        manifest_sha256=identity.manifest_sha256,
                    ),
                    "context": _safe_value(dict(contexts.get(provider, {}))),
                    "result": "pending_provider",
                    "intent": None,
                    "receipts": [],
                    "verification": None,
                    "next_reconcile_at": None,
                    "verification_attempt": 0,
                    "verification_budget": 8,
                    "verification_horizon_at": _iso(self.now() + timedelta(hours=24)),
                    "active_schedule_token": None,
                    "active_schedule_source": None,
                    "schedule_notification_token": None,
                    "schedule_notification_sent_at": None,
                    "notification_reservation": None,
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
                    "notification_intent": None,
                    "notification_sent_at": None,
                },
                "source_ownership": (
                    _safe_value(source_ownership) if source_ownership is not None else None
                ),
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
            _ensure_truth_fields(document)
            _ensure_provider_reconciliation(
                document,
                due_at=self.now() + INITIAL_RECONCILIATION_DELAY,
            )
            _validate_document(document, item_id)
            captured.update(document)
            created = True
            return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

        self._register_artifact_reference(artifact.sha256, item_id)
        self.storage.update_bytes(
            outbox_path(item_id),
            "application/json; charset=utf-8",
            _create,
        )
        return captured, created

    def _register_artifact_reference(self, artifact_sha256: str, outbox_id: str) -> None:
        path = f"{ARTIFACT_REFERENCE_PREFIX}/{artifact_sha256}.json"

        def _register(raw: bytes | None) -> bytes:
            document = json.loads(raw.decode("utf-8")) if raw else {}
            if document.get("deleting") is True or document.get("deleted") is True:
                raise OutboxConflictError("artifact is fenced for cleanup")
            references = {
                str(reference)
                for reference in document.get("outbox_ids", [])
                if isinstance(reference, str)
            }
            references.add(outbox_id)
            document.update(
                {
                    "artifact_sha256": artifact_sha256,
                    "outbox_ids": sorted(references),
                    "deleting": False,
                    "deleted": False,
                    "cleanup_claim_id": None,
                    "updated_at": _iso(self.now()),
                }
            )
            return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

        self.storage.update_bytes(path, "application/json; charset=utf-8", _register)

    def mark_notification_sent(
        self,
        outbox_id: str,
        *,
        source_ownership: Mapping[str, Any] | None = None,
        authorize: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        def _mark(document: dict[str, Any]) -> None:
            if authorize is not None:
                authorize()
            if source_ownership is not None and document.get(
                "notification_source_ownership"
            ) != dict(source_ownership):
                raise StaleClaimError("outbox notification ownership is stale")
            intent = document["enqueue"].get("notification_intent")
            if isinstance(intent, Mapping) and source_ownership is not None:
                intent_source = intent.get("source_ownership")
                if intent_source is not None and intent_source != dict(source_ownership):
                    raise StaleClaimError("outbox notification intent ownership is stale")
            document["enqueue"]["notification_sent_at"] = document["enqueue"].get(
                "notification_sent_at"
            ) or _iso(self.now())

        return self._update(outbox_id, _mark)

    def reserve_notification(
        self,
        outbox_id: str,
        *,
        source_ownership: Mapping[str, Any],
        authorize: Callable[[], None],
    ) -> dict[str, Any]:
        def _reserve(document: dict[str, Any]) -> None:
            authorize()
            enqueue = document["enqueue"]
            current = enqueue.get("notification_intent")
            if current is not None and not isinstance(current, Mapping):
                raise OutboxConflictError("outbox notification intent is malformed")
            if enqueue.get("notification_sent_at") or (
                isinstance(current, Mapping)
                and current.get("state") in ("sending", "ambiguous", "accepted")
            ):
                return
            safe_ownership = _safe_value(source_ownership)
            if isinstance(current, Mapping) and current.get("source_ownership") == safe_ownership:
                return
            reserved_at = _iso(self.now())
            enqueue["notification_intent"] = {
                "intent_id": uuid.uuid4().hex,
                "source_ownership": safe_ownership,
                "reserved_at": reserved_at,
                "state": "reserved",
                "send_started_at": None,
                "accepted_at": None,
            }
            document["notification_source_ownership"] = safe_ownership
            enqueue["notification_reserved_at"] = reserved_at

        return self._update(outbox_id, _reserve)

    def authorize_notification_send(
        self,
        outbox_id: str,
        *,
        source_ownership: Mapping[str, Any],
        authorize: Callable[[], None],
    ) -> bool:
        """Atomically consume the sole initial queue-send authority."""

        authorized = False
        safe_ownership = _safe_value(source_ownership)

        def _authorize(document: dict[str, Any]) -> None:
            nonlocal authorized
            authorize()
            enqueue = document["enqueue"]
            current = enqueue.get("notification_intent")
            if enqueue.get("notification_sent_at") or (
                isinstance(current, Mapping)
                and current.get("state") in ("sending", "ambiguous", "accepted")
            ):
                return
            if not isinstance(current, dict):
                raise StaleClaimError("outbox notification intent is missing")
            if current.get("source_ownership") != safe_ownership:
                raise StaleClaimError("outbox notification intent ownership is stale")
            current["state"] = "sending"
            current["send_started_at"] = _iso(self.now())
            authorized = True

        self._update(outbox_id, _authorize)
        return authorized

    def accept_notification_send(
        self,
        outbox_id: str,
        *,
        source_ownership: Mapping[str, Any],
        authorize: Callable[[], None],
    ) -> bool:
        """Mark a reserved notification accepted only after the queue send returns."""

        accepted = False
        safe_ownership = _safe_value(source_ownership)

        def _accept(document: dict[str, Any]) -> None:
            nonlocal accepted
            authorize()
            enqueue = document["enqueue"]
            current = enqueue.get("notification_intent")
            if enqueue.get("notification_sent_at") or (
                isinstance(current, Mapping) and current.get("state") == "accepted"
            ):
                return
            if not isinstance(current, dict):
                raise StaleClaimError("outbox notification intent is missing")
            if current.get("source_ownership") != safe_ownership:
                raise StaleClaimError("outbox notification intent ownership is stale")
            if current.get("state") != "sending":
                raise StaleClaimError("outbox notification send was not started")
            accepted_at = _iso(self.now())
            current["state"] = "accepted"
            current["accepted_at"] = accepted_at
            enqueue["notification_sent_at"] = accepted_at
            accepted = True

        self._update(outbox_id, _accept)
        return accepted

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
            _ensure_truth_fields(document)
            current = document.get("claim")
            if isinstance(current, Mapping):
                expiry = _parse_time(str(current.get("lease_expires_at") or ""))
                if expiry is not None and expiry > now:
                    raise StaleClaimError("outbox item already has an active claim")
            token = int(document.get("fencing_token") or 0) + 1
            active_attempt = _active_attempt(document)
            consumed = bool(active_attempt.get("terminal_outcome"))
            if (
                active_attempt.get("authz_source") != "initial_enqueue"
                and not consumed
                and not _recovery_authorization_binding_is_valid(
                    document,
                    active_attempt,
                    document.get("recovery_authz", []),
                )
            ):
                consumed = True
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
                "attempt_id": _active_attempt(document)["attempt_id"],
            }
            document["claim"] = claim
            document["fencing_token"] = token
            document["attempt_count"] = int(document.get("attempt_count") or 0) + 1
            document["state"] = "reconciling" if consumed else "claimed"
            _attempt_event(
                _active_attempt(document),
                "reconciling" if consumed else "claimed",
                _iso(now),
                owner=claim["owner"],
                claim_id=claim["claim_id"],
                execution_id=claim["execution_id"],
                fencing_token=token,
                lease_expires_at=claim["lease_expires_at"],
            )
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
            attempt_id=result["attempt_id"],
        )

    def authorize_recovery(
        self,
        outbox_id: str,
        *,
        predecessor_attempt_id: str,
        source: str,
        reason: str,
        evidence: Mapping[str, Any],
    ) -> str:
        """Create a new attempt without rewriting its predecessor."""

        captured: dict[str, str] = {}

        def _authorize(document: dict[str, Any]) -> None:
            _ensure_truth_fields(document)
            attempts = document["attempts"]
            predecessor = next(
                (
                    attempt
                    for attempt in attempts
                    if attempt.get("attempt_id") == predecessor_attempt_id
                ),
                None,
            )
            if not isinstance(predecessor, Mapping) or not predecessor.get("terminal_outcome"):
                raise DistributionOutboxError("recovery predecessor is not terminal")
            provider_evidence = _recovery_predecessor_provider_evidence(document, predecessor)
            if source not in ("operator", "bounded_reconciliation"):
                raise DistributionOutboxError("recovery requires an explicit trusted authorizer")
            for leg in provider_evidence.values():
                if not isinstance(leg, Mapping):
                    raise DistributionOutboxError("recovery predecessor evidence is malformed")
                if leg.get("result") == "publication_unknown":
                    raise DistributionOutboxError(
                        "unknown provider mutation cannot authorize retry"
                    )
                for receipt in leg.get("receipts", []):
                    if isinstance(receipt, Mapping) and receipt.get("ambiguous") is True:
                        raise DistributionOutboxError(
                            "ambiguous provider mutation cannot authorize retry"
                        )
            validated_evidence = _validated_recovery_authorization_evidence(
                document,
                predecessor,
                evidence,
            )
            at = _iso(self.now())
            authorization_id = uuid.uuid4().hex
            attempt_id = uuid.uuid4().hex
            succeeding_attempt = {
                "record_version": ATTEMPT_RECORD_VERSION,
                "attempt_id": attempt_id,
                "authz_id": authorization_id,
                "authz_source": _require_token("authorization_source", source),
                "authz_reason": _require_token("authorization_reason", reason),
                "authorized_at": at,
                "predecessor_attempt_id": predecessor_attempt_id,
                "state": "pending",
                "terminal_outcome": None,
                "events": [
                    {
                        "sequence": 1,
                        "event_type": "accepted",
                        "at": at,
                        "state": "accepted",
                    }
                ],
                "proof_references": [],
            }
            validated_evidence["succeeding_attempt"] = {
                "attempt_id": attempt_id,
                "predecessor_attempt_id": predecessor_attempt_id,
                "providers": {
                    provider: validated_evidence["providers"][provider]["succeeding_provider"]
                    for provider in sorted(validated_evidence["providers"])
                },
                "expectation": _successor_authorization_expectation(succeeding_attempt),
            }
            evidence_digest = hashlib.sha256(_canonical_json_bytes(validated_evidence)).hexdigest()
            previous_authorizations = document["recovery_authz"]
            if previous_authorizations:
                _validated_recovery_authorization_collection(
                    document,
                    previous_authorizations,
                    active_successor_may_progress=True,
                )
                previous_authorizations[-1]["status"] = "superseded"
                previous_authorizations[-1]["superseded_by_authz_id"] = authorization_id
            authorization = {
                "schema_version": RECOVERY_AUTHORIZATION_SCHEMA_VERSION,
                "authz_id": authorization_id,
                "authz_version": 4,
                "source": _require_token("authorization_source", source),
                "reason": _require_token("authorization_reason", reason),
                "authorized_at": at,
                "actor": None,
                "owner": None,
                "status": "active",
                "superseded_by_authz_id": None,
                "predecessor_attempt_id": predecessor_attempt_id,
                "successor_attempt_id": attempt_id,
                "evidence_schema_version": validated_evidence["schema_version"],
                "evidence_digest": evidence_digest,
                "evidence": validated_evidence,
                "extensions": {},
            }
            document["recovery_authz"].append(authorization)
            succeeding_attempt["recovery_proof_digest"] = evidence_digest
            attempts.append(succeeding_attempt)
            _refresh_recovery_authorization_set(document)
            for leg in document["providers"].values():
                leg["result"] = "pending_provider"
                leg["verification"] = None
                leg["intent"] = None
                leg["receipts"] = []
            document["state"] = "pending"
            self._refresh_aggregate(document)
            captured["authorization_id"] = authorization_id

        self._update(outbox_id, _authorize)
        return captured["authorization_id"]

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
            attempt = _active_attempt(document)
            if attempt.get("authz_source") != "initial_enqueue":
                authorization = next(
                    (
                        item
                        for item in document.get("recovery_authz", [])
                        if isinstance(item, Mapping)
                        and item.get("authz_id") == attempt.get("authz_id")
                    ),
                    None,
                )
                expected = (
                    authorization.get("evidence", {})
                    .get("providers", {})
                    .get(provider, {})
                    .get("succeeding_provider", {})
                    .get("expected_provider_item_id")
                    if isinstance(authorization, Mapping)
                    else None
                )
                if not expected or expected_provider_item_id != expected:
                    raise DistributionOutboxError(
                        "recovery intent does not match its authorization"
                    )
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
                "operation_type": _recovery_operation_type(provider, operation),
                "created_at": _iso(self.now()),
                "created_fence": claim.fencing_token,
                "consumed_at": None,
                "consumed_fence": None,
                "consumed_owner": None,
                "consumed_attempt_id": None,
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
            _attempt_event(
                _active_attempt(document),
                "intent_persisted",
                _iso(self.now()),
                provider=provider,
                intent_id=intent["intent_id"],
            )
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
            intent["consumed_owner"] = claim.owner
            intent["consumed_attempt_id"] = claim.attempt_id
            document["state"] = "mutating"
            _attempt_event(
                _active_attempt(document),
                "mutating",
                intent["consumed_at"],
                provider=provider,
                intent_id=intent["intent_id"],
            )
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
                "provider": provider,
                "operation": intent["operation"],
                "operation_type": intent["operation_type"],
                "attempt_id": claim.attempt_id,
                "consumed_owner": intent["consumed_owner"],
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
            _attempt_event(
                _active_attempt(document),
                "receipt_persisted",
                receipt["at"],
                provider=provider,
                receipt_id=receipt["receipt_id"],
            )
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
        proof: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if result not in PROVIDER_RESULTS:
            raise ValueError(f"unsupported provider result: {result}")
        captured: dict[str, Any] = {}

        def _verify(document: dict[str, Any]) -> None:
            self._require_claim(document, claim)
            leg = self._provider(document, provider)
            proof_value = self._verification_proof(
                document,
                leg,
                provider_item_id=provider_item_id,
                native_state=native_state,
                source=source,
                proof=proof,
            )
            effective_result = result
            if result == "externally_verified_public" and not proof_value["green"]:
                effective_result = "identity_conflict"
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
                "result": effective_result,
                "fencing_token": claim.fencing_token,
                "exhaustion_reason": (
                    _require_token("exhaustion_reason", exhaustion_reason)
                    if exhaustion_reason
                    else None
                ),
                "proof": proof_value,
            }
            leg["verification"] = verification
            leg["result"] = effective_result
            attempt = _active_attempt(document)
            if attempt.get("terminal_outcome"):
                attempt.setdefault("post_terminal_readbacks", []).append(
                    {
                        "provider": provider,
                        "verification": dict(verification),
                    }
                )
            leg["verification_attempt"] = int(leg.get("verification_attempt") or 0) + 1
            leg["next_reconcile_at"] = _iso(next_reconcile_at) if next_reconcile_at else None
            if effective_result != "pending_provider":
                leg["active_schedule_token"] = None
                leg["active_schedule_source"] = None
                leg["schedule_notification_token"] = None
                leg["schedule_notification_sent_at"] = None
                leg["notification_reservation"] = None
            self._refresh_aggregate(document)
            _attempt_event(
                _active_attempt(document),
                "verifying",
                verification["at"],
                provider=provider,
                result=effective_result,
            )
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
            if existing and leg.get("active_schedule_source") != "initial_notification":
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
            leg["active_schedule_source"] = "worker"
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
            leg["active_schedule_source"] = None
            leg["schedule_notification_token"] = None
            leg["schedule_notification_sent_at"] = None
            leg["last_reconciled_at"] = _iso(self.now())

        self._update(claim.outbox_id, _consume)

    def release(self, claim: Claim) -> dict[str, Any]:
        def _release(document: dict[str, Any]) -> None:
            self._require_claim(document, claim)
            attempt = _active_attempt(document)
            weekly_state = str(document["weekly_aggregation"]["state"])
            terminal_map = {
                "published_verified": "published_verified",
                "published_verified_recovered": "published_verified",
                "identity_conflict": "identity_conflict",
                "provider_unknown": "provider_unknown",
                "manual_action_required": "manual_action_required",
                "partial": "partial",
                "failed_terminal": "failed_terminal",
            }
            outcome = terminal_map.get(weekly_state)
            if outcome and not attempt.get("terminal_outcome"):
                attempt["terminal_outcome"] = outcome
                attempt["provider_evidence"] = {
                    provider: {
                        "intent": leg.get("intent"),
                        "receipts": leg.get("receipts", []),
                        "verification": leg.get("verification"),
                        "result": leg.get("result"),
                    }
                    for provider, leg in document["providers"].items()
                }
                attempt["proof_references"] = [
                    str(leg.get("verification", {}).get("provider_item_id"))
                    for leg in document["providers"].values()
                    if isinstance(leg.get("verification"), Mapping)
                    and leg["verification"].get("provider_item_id")
                ]
                _attempt_event(attempt, "completed", _iso(self.now()), outcome=outcome)
            document["claim"] = None
            if weekly_state in ("published_verified", "published_verified_recovered"):
                document["state"] = "completed_public"
            elif document["aggregate"]["result"] in (
                "publication_unknown",
                "manual_handoff_required",
                "failed_terminal",
                "partial",
                "identity_conflict",
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
        continuation = after_path
        last_scanned: str | None = None
        scanned = 0
        while scanned < scan_limit:
            page_size = min(limit, scan_limit - scanned)
            paths, next_continuation = self._list_page(
                f"{OUTBOX_PREFIX}/",
                limit=page_size,
                continuation=continuation,
            )
            if not paths:
                return due, None
            for path in sorted(paths):
                last_scanned = path
                scanned += 1
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
                    reservation = leg.get("notification_reservation")
                    reservation_expires = (
                        _parse_time(str(reservation.get("lease_expires_at") or ""))
                        if isinstance(reservation, Mapping)
                        else None
                    )
                    reservation_is_fresh = (
                        isinstance(reservation, Mapping)
                        and reservation.get("token") == token
                        and reservation.get("stage") in ("reserved", "enqueue_started")
                        and reservation_expires is not None
                        and reservation_expires > now
                    )
                    if due_at is not None and due_at <= now and isinstance(token, str):
                        if notification_is_fresh or reservation_is_fresh:
                            continue
                        due.append((str(document["outbox_id"]), str(provider), token))
                if len(due) >= limit:
                    return due[:limit], next_continuation
            continuation = next_continuation
            if not continuation:
                return due, None
        return due, continuation or last_scanned

    def reserve_reconciliation_notification(
        self,
        outbox_id: str,
        *,
        provider: str,
        token: str,
        owner: str,
        lease_seconds: int = 60,
    ) -> str:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        captured: dict[str, str] = {}

        def _reserve(document: dict[str, Any]) -> None:
            leg = self._provider(document, provider)
            if leg.get("active_schedule_token") != token:
                raise StaleClaimError("reconciliation token is stale")
            if leg.get("schedule_notification_token") == token:
                raise StaleClaimError("reconciliation notification is already complete")
            current = leg.get("notification_reservation")
            now = self.now()
            if isinstance(current, Mapping):
                expiry = _parse_time(str(current.get("lease_expires_at") or ""))
                if current.get("token") == token and expiry is not None and expiry > now:
                    raise StaleClaimError("reconciliation notification is already reserved")
            fence = int(leg.get("notification_fence") or 0) + 1
            reservation_id = uuid.uuid4().hex
            leg["notification_fence"] = fence
            leg["notification_reservation"] = {
                "reservation_id": reservation_id,
                "owner": _require_token("notification_owner", owner),
                "token": token,
                "fencing_token": fence,
                "stage": "reserved",
                "reserved_at": _iso(now),
                "lease_expires_at": _iso(now + timedelta(seconds=lease_seconds)),
            }
            captured["reservation_id"] = reservation_id

        self._update(outbox_id, _reserve)
        return captured["reservation_id"]

    def begin_reconciliation_enqueue(
        self,
        outbox_id: str,
        *,
        provider: str,
        token: str,
        reservation_id: str,
    ) -> None:
        def _begin(document: dict[str, Any]) -> None:
            leg = self._provider(document, provider)
            reservation = leg.get("notification_reservation")
            if (
                not isinstance(reservation, dict)
                or reservation.get("reservation_id") != reservation_id
                or reservation.get("token") != token
                or reservation.get("fencing_token") != leg.get("notification_fence")
                or reservation.get("stage") != "reserved"
            ):
                raise StaleClaimError("reconciliation notification reservation is stale")
            expiry = _parse_time(str(reservation.get("lease_expires_at") or ""))
            if expiry is None or expiry <= self.now():
                raise StaleClaimError("reconciliation notification reservation expired")
            reservation["stage"] = "enqueue_started"
            reservation["enqueue_started_at"] = _iso(self.now())

        self._update(outbox_id, _begin)

    def abort_reconciliation_enqueue(
        self,
        outbox_id: str,
        *,
        provider: str,
        token: str,
        reservation_id: str,
    ) -> None:
        def _abort(document: dict[str, Any]) -> None:
            leg = self._provider(document, provider)
            reservation = leg.get("notification_reservation")
            if (
                not isinstance(reservation, Mapping)
                or reservation.get("reservation_id") != reservation_id
                or reservation.get("token") != token
                or reservation.get("fencing_token") != leg.get("notification_fence")
                or reservation.get("stage") != "enqueue_started"
            ):
                raise StaleClaimError("reconciliation notification reservation is stale")
            leg["notification_reservation"] = None

        self._update(outbox_id, _abort)

    def complete_reconciliation_notification(
        self,
        outbox_id: str,
        *,
        provider: str,
        token: str,
        reservation_id: str,
    ) -> dict[str, Any]:
        def _complete(document: dict[str, Any]) -> None:
            leg = self._provider(document, provider)
            reservation = leg.get("notification_reservation")
            if (
                not isinstance(reservation, Mapping)
                or reservation.get("reservation_id") != reservation_id
                or reservation.get("token") != token
                or reservation.get("fencing_token") != leg.get("notification_fence")
                or reservation.get("stage") != "enqueue_started"
            ):
                raise StaleClaimError("reconciliation notification reservation is stale")
            expiry = _parse_time(str(reservation.get("lease_expires_at") or ""))
            if expiry is None or expiry <= self.now():
                raise StaleClaimError("reconciliation notification reservation expired")
            leg["schedule_notification_token"] = token
            leg["schedule_notification_sent_at"] = _iso(self.now())
            leg["notification_reservation"] = None

        return self._update(outbox_id, _complete)

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
        page_limit: int = 4,
        work_limit: int = 400,
        time_limit_seconds: float = 5.0,
    ) -> int:
        if min(limit, outbox_scan_limit, page_limit, work_limit) <= 0:
            return 0
        started = time.monotonic()
        removed = 0
        work = 0
        pages = 0
        raw_state = self.storage.get_bytes(ORPHAN_CLEANUP_STATE_PATH)
        cleanup_state = json.loads(raw_state.decode("utf-8")) if raw_state else {}
        if cleanup_state.get("reference_index_complete") is not True:
            migration_cursor = cleanup_state.get("outbox_reference_cursor")
            paths, next_migration_cursor = self._list_page(
                f"{OUTBOX_PREFIX}/",
                limit=outbox_scan_limit,
                continuation=migration_cursor,
            )
            for path in paths:
                raw = self.storage.get_bytes(path)
                if raw is None:
                    continue
                document = json.loads(raw.decode("utf-8"))
                artifact = document.get("artifact")
                digest = artifact.get("sha256") if isinstance(artifact, Mapping) else None
                outbox_id = document.get("outbox_id")
                if _SHA256.fullmatch(str(digest or "")) and isinstance(outbox_id, str):
                    self._register_artifact_reference(str(digest), outbox_id)
            cleanup_state["outbox_reference_cursor"] = next_migration_cursor
            cleanup_state["reference_index_complete"] = next_migration_cursor is None
            cleanup_state["last_reference_migration"] = {
                "scanned": len(paths),
                "completed_at": _iso(self.now()),
            }
            self.storage.put_bytes(
                ORPHAN_CLEANUP_STATE_PATH,
                json.dumps(cleanup_state, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            if next_migration_cursor is not None:
                return 0
        cursor = cleanup_state.get("metadata_cursor")
        while (
            pages < page_limit
            and work < work_limit
            and removed < limit
            and time.monotonic() - started < time_limit_seconds
        ):
            metadata_paths, next_cursor = self._list_page(
                f"{ARTIFACT_METADATA_PREFIX}/",
                limit=min(limit - removed, work_limit - work),
                continuation=cursor,
            )
            pages += 1
            if not metadata_paths and cursor:
                cursor = None
                continue
            for metadata_path in metadata_paths:
                if (
                    work >= work_limit
                    or removed >= limit
                    or time.monotonic() - started >= time_limit_seconds
                ):
                    break
                work += 1
                raw = self.storage.get_bytes(metadata_path)
                if raw is None:
                    continue
                metadata = json.loads(raw.decode("utf-8"))
                artifact_path_value = str(metadata.get("artifact_path") or "")
                digest = str(metadata.get("sha256") or "")
                created_at = _parse_time(str(metadata.get("created_at") or ""))
                if (
                    not artifact_path_value
                    or not _SHA256.fullmatch(digest)
                    or created_at is None
                    or self.now() - created_at < retention
                ):
                    continue
                reference_path = f"{ARTIFACT_REFERENCE_PREFIX}/{digest}.json"
                claim_id = uuid.uuid4().hex
                claimed = {"value": False}

                def _claim_delete(reference_raw: bytes | None) -> bytes:
                    reference = json.loads(reference_raw.decode("utf-8")) if reference_raw else {}
                    if reference.get("outbox_ids") or reference.get("deleting") is True:
                        return reference_raw or b"{}"
                    reference.update(
                        {
                            "artifact_sha256": digest,
                            "outbox_ids": [],
                            "deleting": True,
                            "cleanup_claim_id": claim_id,
                            "updated_at": _iso(self.now()),
                        }
                    )
                    claimed["value"] = True
                    return json.dumps(reference, sort_keys=True, separators=(",", ":")).encode(
                        "utf-8"
                    )

                self.storage.update_bytes(
                    reference_path,
                    "application/json; charset=utf-8",
                    _claim_delete,
                )
                if not claimed["value"]:
                    continue
                self.storage.delete_blob(artifact_path_value)
                self.storage.delete_blob(metadata_path)
                finalized = {"value": False}

                def _finalize_delete(reference_raw: bytes | None) -> bytes:
                    reference = json.loads(reference_raw.decode("utf-8")) if reference_raw else {}
                    if (
                        reference.get("deleting") is not True
                        or reference.get("cleanup_claim_id") != claim_id
                        or reference.get("outbox_ids")
                    ):
                        return reference_raw or b"{}"
                    reference.update(
                        {
                            "deleting": False,
                            "deleted": True,
                            "cleanup_claim_id": None,
                            "updated_at": _iso(self.now()),
                        }
                    )
                    finalized["value"] = True
                    return json.dumps(reference, sort_keys=True, separators=(",", ":")).encode(
                        "utf-8"
                    )

                self.storage.update_bytes(
                    reference_path,
                    "application/json; charset=utf-8",
                    _finalize_delete,
                )
                if not finalized["value"]:
                    raise DistributionOutboxError("artifact cleanup fence was lost")
                removed += 1
            cursor = next_cursor
            if not cursor:
                break
        self.storage.put_bytes(
            ORPHAN_CLEANUP_STATE_PATH,
            json.dumps(
                {
                    "metadata_cursor": cursor,
                    "outbox_reference_cursor": cleanup_state.get("outbox_reference_cursor"),
                    "reference_index_complete": cleanup_state.get(
                        "reference_index_complete", False
                    ),
                    "last_reference_migration": cleanup_state.get("last_reference_migration"),
                    "last_run": {
                        "pages": pages,
                        "work_items": work,
                        "removed": removed,
                        "completed_at": _iso(self.now()),
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            "application/json; charset=utf-8",
        )
        return removed

    def _list_page(
        self,
        prefix: str,
        *,
        limit: int,
        continuation: str | None,
    ) -> tuple[list[str], str | None]:
        pager = getattr(self.storage, "list_blobs_page", None)
        if callable(pager):
            return pager(prefix, limit=limit, continuation=continuation)
        paths = sorted(self.storage.list_blobs(prefix, limit=limit))
        if continuation:
            paths = [path for path in paths if path > continuation]
        return paths[:limit], paths[-1] if len(paths) >= limit else None

    def repair_notifications(self, notify: Callable[[str], None], *, limit: int = 100) -> int:
        repaired = 0
        for path in self.storage.list_blobs(f"{OUTBOX_PREFIX}/", limit=limit):
            raw = self.storage.get_bytes(path)
            if raw is None:
                continue
            document = json.loads(raw.decode("utf-8"))
            item_id = str(document["outbox_id"])
            document = self._update(
                item_id,
                lambda current: _ensure_provider_reconciliation(
                    current,
                    due_at=self.now() + INITIAL_RECONCILIATION_DELAY,
                ),
            )
            enqueue = document.get("enqueue", {})
            intent = enqueue.get("notification_intent")
            if enqueue.get("notification_sent_at") or (
                isinstance(intent, Mapping)
                and intent.get("state") in ("sending", "ambiguous", "accepted")
            ):
                continue
            repair_ownership = {"repair_id": uuid.uuid4().hex}
            try:
                self.reserve_notification(
                    item_id,
                    source_ownership=repair_ownership,
                    authorize=lambda: None,
                )
                won_intent = self.authorize_notification_send(
                    item_id,
                    source_ownership=repair_ownership,
                    authorize=lambda: None,
                )
            except StaleClaimError:
                continue
            if not won_intent:
                continue
            notify(item_id)
            self.accept_notification_send(
                item_id,
                source_ownership=repair_ownership,
                authorize=lambda: None,
            )
            repaired += 1
        return repaired

    def operational_documents(self, *, page_size: int = 1000) -> Iterable[dict[str, Any]]:
        continuation: str | None = None
        while True:
            paths, continuation = self._list_page(
                f"{OUTBOX_PREFIX}/",
                limit=page_size,
                continuation=continuation,
            )
            for path in paths:
                raw = self.storage.get_bytes(path)
                if raw is None:
                    continue
                document = json.loads(raw.decode("utf-8"))
                _ensure_truth_fields(document)
                yield document
            if not continuation:
                break

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
            document = _load_outbox_document(raw)
            _ensure_truth_fields(document)
            _validate_document(document, outbox_id)
            mutation(document)
            document["updated_at"] = _iso(self.now())
            _validate_document(document, outbox_id)
            captured.update(document)
            try:
                return json.dumps(
                    document,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise DistributionOutboxError("outbox record is not valid JSON") from exc

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
        if mutation:
            attempt = _active_attempt(document)
            if attempt.get("authz_source") != "initial_enqueue" and not (
                _recovery_authorization_binding_is_valid(
                    document,
                    attempt,
                    document.get("recovery_authz", []),
                    successor_may_progress=True,
                )
            ):
                raise StaleClaimError("recovery authorization is no longer valid")

    @staticmethod
    def _provider(document: Mapping[str, Any], provider: str) -> dict[str, Any]:
        leg = document.get("providers", {}).get(provider)
        if not isinstance(leg, dict):
            raise DistributionOutboxError(f"provider is not requested: {provider}")
        return leg

    @staticmethod
    def _verification_proof(
        document: Mapping[str, Any],
        leg: Mapping[str, Any],
        *,
        provider_item_id: str | None,
        native_state: str | None,
        source: str,
        proof: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        supplied = dict(proof or {})
        identity = document["publication_identity"]
        artifact = document["artifact"]
        expected_provider_ids = {
            str(receipt.get("provider_item_id"))
            for receipt in leg.get("receipts", [])
            if isinstance(receipt, Mapping) and receipt.get("provider_item_id")
        }
        intent = leg.get("intent")
        if isinstance(intent, Mapping) and intent.get("expected_provider_item_id"):
            expected_provider_ids.add(str(intent["expected_provider_item_id"]))
        existing_verification = leg.get("verification")
        if isinstance(existing_verification, Mapping) and existing_verification.get(
            "provider_item_id"
        ):
            expected_provider_ids.add(str(existing_verification["provider_item_id"]))
        observed_provider_id = str(provider_item_id or "")
        provider_identity_match = bool(observed_provider_id) and expected_provider_ids == {
            observed_provider_id
        }
        canonical = document.get("canonical_artifact")
        values = {
            "week_match": supplied.get("week") == identity["week"],
            "publication_identity_match": supplied.get("outbox_id") == document["outbox_id"]
            and supplied.get("accepted_job_id") == identity["accepted_job_id"]
            and supplied.get("publish_run_id") == identity["publish_run_id"]
            and supplied.get("article_sha256") == identity["article_sha256"],
            "manifest_match": supplied.get("manifest_sha256") == identity["manifest_sha256"],
            "publication_digest_match": supplied.get("publication_digest")
            == document["publication_digest"],
            "artifact_sha256_match": supplied.get("artifact_sha256") == artifact["sha256"],
            "canonical_artifact_selected": isinstance(canonical, Mapping)
            and supplied.get("canonical_artifact_id") == canonical.get("artifact_id")
            and supplied.get("canonical_selection_version") == canonical.get("selection_version")
            and supplied.get("canonical_artifact_selected") is True
            and canonical.get("selected") is True,
            "provider_identity_match": supplied.get("provider_item_id") == observed_provider_id
            and supplied.get("provider_identity_match") is True
            and provider_identity_match,
            "terminal_authoritative_readback": supplied.get("terminal_authoritative_readback")
            is True
            and bool(provider_item_id)
            and str(native_state or "").lower() in ("public", "published")
            and ("readback" in source or source in ("youtube_videos_list", "provider_readback")),
            "duplicate_ambiguity_resolved": supplied.get("duplicate_ambiguity_resolved") is True
            and len(expected_provider_ids) == 1,
        }
        values["green"] = all(values.values())
        values["evidence"] = _safe_value(supplied, key="verification_evidence")
        return values

    @staticmethod
    def _refresh_aggregate(document: dict[str, Any]) -> None:
        _ensure_truth_fields(document)
        results = [str(leg.get("result")) for leg in document["providers"].values()]
        proofs = [
            leg.get("verification", {}).get("proof", {})
            for leg in document["providers"].values()
            if isinstance(leg.get("verification"), Mapping)
        ]
        all_public = (
            bool(results)
            and all(result == "externally_verified_public" for result in results)
            and len(proofs) == len(results)
            and all(proof.get("green") is True for proof in proofs)
        )
        if all_public:
            aggregate = "completed_public"
            reason = None
        else:
            priority = (
                "identity_conflict",
                "poisoned",
                "publication_unknown",
                "manual_handoff_required",
                "partial",
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
        attempts = document["attempts"]
        current_attempt = _active_attempt(document)
        previous_non_green = [
            attempt
            for attempt in attempts[:-1]
            if attempt.get("terminal_outcome") not in (None, "published_verified")
        ]
        unresolved: list[str] = []
        precedence = (
            "identity_conflict",
            "provider_unknown",
            "manual_action_required",
            "partial",
            "missed_not_dispatched",
            "failed_terminal",
            "pending",
        )
        normalized = {
            "identity_conflict": "identity_conflict",
            "publication_unknown": "provider_unknown",
            "manual_handoff_required": "manual_action_required",
            "partial": "partial",
            "poisoned": "failed_terminal",
            "failed_terminal": "failed_terminal",
            "failed_retryable_pre_mutation": "pending",
            "pending_provider": "pending",
        }
        if all_public:
            prior_outcomes = {
                str(attempt.get("terminal_outcome")) for attempt in previous_non_green
            }
            if "identity_conflict" in prior_outcomes:
                weekly_state = "identity_conflict"
                winning_attempt_id = None
                unresolved = ["identity_conflict"]
            elif "provider_unknown" in prior_outcomes:
                weekly_state = "provider_unknown"
                winning_attempt_id = None
                unresolved = ["provider_unknown"]
            else:
                if previous_non_green:
                    authorized = _recovery_authorization_is_valid(
                        document,
                        current_attempt,
                        document.get("recovery_authz", []),
                    )
                    weekly_state = (
                        "published_verified_recovered" if authorized else "identity_conflict"
                    )
                    winning_attempt_id = current_attempt["attempt_id"] if authorized else None
                    if not authorized:
                        unresolved = ["recovery_authorization_invalid"]
                else:
                    weekly_state = "published_verified"
                    winning_attempt_id = current_attempt["attempt_id"]
        else:
            candidates = [normalized.get(result, result) for result in results]
            if "identity_conflict" in candidates:
                weekly_state = "identity_conflict"
            elif "provider_unknown" in candidates:
                weekly_state = "provider_unknown"
            elif "manual_action_required" in candidates:
                weekly_state = "manual_action_required"
            elif "partial" in candidates or (
                "externally_verified_public" in candidates
                and any(item != "externally_verified_public" for item in candidates)
            ):
                weekly_state = "partial"
            elif candidates and all(item == "failed_terminal" for item in candidates):
                weekly_state = "failed_terminal"
            elif "pending" in candidates:
                weekly_state = "pending"
            else:
                weekly_state = next(
                    (candidate for candidate in precedence if candidate in candidates),
                    "partial",
                )
            winning_attempt_id = None
            unresolved = sorted(set(candidates))
        decision = document["weekly_aggregation"]
        decision.update(
            {
                "evaluated_attempt_ids": [str(attempt["attempt_id"]) for attempt in attempts],
                "state": weekly_state,
                "winning_attempt_id": winning_attempt_id,
                "proof_references": [
                    str(leg.get("verification", {}).get("provider_item_id"))
                    for leg in document["providers"].values()
                    if isinstance(leg.get("verification"), Mapping)
                    and leg["verification"].get("provider_item_id")
                ],
                "failed_attempt_references": [
                    str(attempt["attempt_id"]) for attempt in previous_non_green
                ],
                "unresolved_conditions": unresolved,
                "decided_at": _iso(utc_now()),
                "worker_exit_class": (
                    "zero"
                    if weekly_state in ("published_verified", "published_verified_recovered")
                    else "nonzero"
                ),
            }
        )
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
    return 0 if all(_authoritative_green_document(item) for item in items) else 1


def four_cycle_acceptance(documents: Iterable[Mapping[str, Any]]) -> bool:
    items = list(documents)
    if len(items) != 4 or not all(_authoritative_green_document(item) for item in items):
        return False
    weeks = [str(item["publication_identity"]["week"]) for item in items]
    try:
        parsed = [datetime.strptime(week + "-1", "%G-W%V-%u").date() for week in weeks]
    except ValueError:
        return False
    return all((later - earlier).days == 7 for earlier, later in zip(parsed, parsed[1:]))


def _authoritative_green_document(document: Mapping[str, Any]) -> bool:
    aggregation = document.get("weekly_aggregation")
    attempts = document.get("attempts")
    providers = document.get("providers")
    identity = document.get("publication_identity")
    artifact = document.get("artifact")
    canonical = document.get("canonical_artifact")
    if not all(
        isinstance(value, Mapping)
        for value in (aggregation, providers, identity, artifact, canonical)
    ) or not isinstance(attempts, list):
        return False
    state = aggregation.get("state")
    if state not in ("published_verified", "published_verified_recovered"):
        return False
    if (
        aggregation.get("rule_version") != "weekly-publication-truth-v1"
        or not aggregation.get("decision_id")
        or not all(identity.get(field) for field in ("week", "accepted_job_id", "publish_run_id"))
        or not _SHA256.fullmatch(str(identity.get("article_sha256") or ""))
        or not _SHA256.fullmatch(str(identity.get("manifest_sha256") or ""))
        or not _SHA256.fullmatch(str(document.get("publication_digest") or ""))
        or not _SHA256.fullmatch(str(artifact.get("sha256") or ""))
        or canonical.get("selected") is not True
        or canonical.get("artifact_id") != artifact.get("sha256")
        or not canonical.get("selection_version")
        or document.get("publication_digest") != _publication_digest(identity, artifact)
    ):
        return False
    evaluated = aggregation.get("evaluated_attempt_ids")
    attempt_ids = [
        attempt.get("attempt_id") for attempt in attempts if isinstance(attempt, Mapping)
    ]
    if evaluated != attempt_ids or aggregation.get("winning_attempt_id") not in attempt_ids:
        return False
    if aggregation.get("unresolved_conditions") or not aggregation.get("proof_references"):
        return False
    if state == "published_verified_recovered":
        winner = next(
            (
                attempt
                for attempt in attempts
                if isinstance(attempt, Mapping)
                and attempt.get("attempt_id") == aggregation.get("winning_attempt_id")
            ),
            None,
        )
        authz = document.get("recovery_authz")
        if (
            not isinstance(winner, Mapping)
            or not winner.get("predecessor_attempt_id")
            or not isinstance(authz, list)
            or not _recovery_authorization_is_valid(document, winner, authz)
        ):
            return False
    verified_provider_ids: list[str] = []
    for leg in providers.values():
        if not isinstance(leg, Mapping) or leg.get("result") != "externally_verified_public":
            return False
        verification = leg.get("verification")
        if not isinstance(verification, Mapping):
            return False
        proof = verification.get("proof")
        evidence = proof.get("evidence") if isinstance(proof, Mapping) else None
        provider_item_id = verification.get("provider_item_id")
        rebound = (
            DistributionOutboxRepository._verification_proof(
                document,
                leg,
                provider_item_id=str(provider_item_id or ""),
                native_state=str(verification.get("native_state") or ""),
                source=str(verification.get("source") or ""),
                proof=evidence,
            )
            if isinstance(evidence, Mapping)
            else {}
        )
        if (
            not isinstance(proof, Mapping)
            or proof.get("green") is not True
            or not all(proof.get(field) is True for field in VERIFICATION_PROOF_FIELDS)
            or rebound.get("green") is not True
            or any(rebound.get(field) != proof.get(field) for field in VERIFICATION_PROOF_FIELDS)
            or provider_item_id not in aggregation.get("proof_references", [])
            or str(verification.get("native_state") or "").lower() not in ("public", "published")
            or (
                "readback" not in str(verification.get("source") or "")
                and verification.get("source") not in ("youtube_videos_list", "provider_readback")
            )
        ):
            return False
        verified_provider_ids.append(str(provider_item_id))
    if len(set(verified_provider_ids)) != len(verified_provider_ids) or sorted(
        verified_provider_ids
    ) != sorted(aggregation.get("proof_references", [])):
        return False
    return (
        aggregation.get("worker_exit_class") == "zero"
        and document.get("aggregate", {}).get("externally_verified_public") is True
    )


def weekly_state_from_attempts(
    attempts: Iterable[Mapping[str, Any]],
    *,
    missed_not_dispatched: bool = False,
    weekly_record: Mapping[str, Any] | None = None,
) -> str:
    """Deterministically aggregate immutable attempt outcomes for one week."""

    items = list(attempts)
    outcomes = [str(item.get("terminal_outcome") or item.get("outcome") or "") for item in items]
    if "identity_conflict" in outcomes:
        return "identity_conflict"
    if "provider_unknown" in outcomes:
        return "provider_unknown"
    if missed_not_dispatched and not items:
        return "missed_not_dispatched"
    verified = [
        item
        for item in items
        if str(item.get("terminal_outcome")) == "published_verified"
        and (
            item.get("proof_complete") is True
            or (
                weekly_record is not None
                and weekly_record.get("weekly_aggregation", {}).get("winning_attempt_id")
                == item.get("attempt_id")
            )
        )
    ]
    if verified:
        earlier_non_green = any(
            item is not verified[-1]
            and str(item.get("terminal_outcome") or "") not in ("", "published_verified")
            for item in items
        )
        if earlier_non_green:
            return (
                "published_verified_recovered"
                if weekly_record is not None
                and _authoritative_green_document(weekly_record)
                and weekly_record.get("weekly_aggregation", {}).get("state")
                == "published_verified_recovered"
                else "identity_conflict"
            )
        return (
            "published_verified"
            if weekly_record is not None
            and _authoritative_green_document(weekly_record)
            and weekly_record.get("weekly_aggregation", {}).get("state") == "published_verified"
            else "identity_conflict"
        )
    precedence = (
        "manual_action_required",
        "partial",
        "failed_terminal",
        "pending",
    )
    return next((state for state in precedence if state in outcomes), "pending")


def outbox_routing_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get("DISTRIBUTION_OUTBOX_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
