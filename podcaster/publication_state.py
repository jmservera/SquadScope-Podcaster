"""Canonical provider-delivery outcomes and durable reconciliation evidence."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

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
REARMABLE_CLAIM_OPERATIONS = ("upload_intent", "create_episode_intent")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WEEK_RE = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
_RUN_RE = re.compile(r"^[0-9]+$")
_UNSAFE_DETAIL_KEY = re.compile(
    r"(authorization|bearer|cookie|credential|secret|token|signed.?url|body|content)",
    re.IGNORECASE,
)
_CREATE_SAFETY_DETAIL_KEYS = frozenset(
    {
        "create_provenance",
        "mutation_possibility",
        "pre_create_episode_ids",
        "pre_create_snapshot_complete",
        "snapshot_completeness",
        "snapshot_evidence_source",
    }
)
_PROVIDER_SNAPSHOT_STATE_CAPABILITY = object()


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


class CreateIntentProvenance(str, Enum):
    RECONCILIATION_BACKED = "reconciliation_backed"
    BLIND_UNRECONCILED = "blind_unreconciled"
    UPLOAD_DISPATCH = "upload_dispatch"


class SnapshotCompleteness(str, Enum):
    ABSENT = "absent"
    TRUNCATED = "truncated"
    COMPLETE = "complete"


class MutationPossibility(str, Enum):
    NOT_POSSIBLE = "not_possible"
    POSSIBLE = "possible"
    CONFIRMED = "confirmed"


def _normalize_snapshot_evidence_source(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if not any(
        not character.isspace() and not unicodedata.category(character).startswith("C")
        for character in normalized
    ):
        return None
    return normalized


class ProviderSnapshot:
    """Immutable provider snapshot with state-specific tuple-backed values."""

    __slots__ = ()

    def __init_subclass__(
        cls,
        *,
        _provider_snapshot_state_capability: object | None = None,
        **kwargs: Any,
    ) -> None:
        if _provider_snapshot_state_capability is not _PROVIDER_SNAPSHOT_STATE_CAPABILITY:
            raise TypeError("ProviderSnapshot cannot be subclassed")
        super().__init_subclass__(**kwargs)

    def __new__(
        cls,
        episode_ids: tuple[int, ...],
        completeness: SnapshotCompleteness,
        evidence_source: str | None = None,
    ) -> "ProviderSnapshot":
        if cls is not ProviderSnapshot:
            return super().__new__(cls)
        if not isinstance(completeness, SnapshotCompleteness):
            raise PublicationStateError("snapshot completeness must be explicit")
        if completeness == SnapshotCompleteness.ABSENT:
            return _AbsentProviderSnapshot(episode_ids)
        if completeness == SnapshotCompleteness.TRUNCATED:
            return _TruncatedProviderSnapshot(episode_ids, evidence_source)
        return _CompleteProviderSnapshot(episode_ids, evidence_source)

    def __init__(
        self,
        episode_ids: tuple[int, ...],
        completeness: SnapshotCompleteness,
        evidence_source: str | None = None,
    ) -> None:
        pass

    @property
    def episode_ids(self) -> tuple[int, ...]:
        raise NotImplementedError

    @property
    def completeness(self) -> SnapshotCompleteness:
        raise NotImplementedError

    def require_evidence_source(self) -> str:
        raise PublicationStateError("absent snapshot has no evidence source")

    @classmethod
    def absent(cls) -> "ProviderSnapshot":
        return cls((), SnapshotCompleteness.ABSENT, None)

    @classmethod
    def truncated(
        cls,
        episode_ids: set[int] | list[int] | tuple[int, ...],
        *,
        evidence_source: str,
    ) -> "ProviderSnapshot":
        return cls(tuple(episode_ids), SnapshotCompleteness.TRUNCATED, evidence_source)

    @classmethod
    def complete(
        cls,
        episode_ids: set[int] | list[int] | tuple[int, ...],
        *,
        evidence_source: str,
    ) -> "ProviderSnapshot":
        return cls(tuple(episode_ids), SnapshotCompleteness.COMPLETE, evidence_source)

    def to_details(self) -> dict[str, Any]:
        return _provider_snapshot_to_details(self)


def _normalize_snapshot_ids(raw_ids: Any) -> tuple[int, ...]:
    try:
        values = tuple(raw_ids)
    except TypeError as exc:
        raise PublicationStateError("snapshot episode ids are unreadable") from exc
    normalized_ids: list[int] = []
    for raw_id in values:
        if isinstance(raw_id, bool):
            raise PublicationStateError("snapshot episode id cannot be a boolean")
        try:
            normalized_ids.append(int(raw_id))
        except (TypeError, ValueError) as exc:
            raise PublicationStateError("snapshot episode id is unreadable") from exc
    unique_sorted_ids = tuple(sorted(set(normalized_ids)))
    if len(unique_sorted_ids) != len(normalized_ids):
        raise PublicationStateError("snapshot episode ids must be unique")
    return unique_sorted_ids


class _AbsentProviderSnapshot(
    tuple,
    ProviderSnapshot,
    _provider_snapshot_state_capability=_PROVIDER_SNAPSHOT_STATE_CAPABILITY,
):
    __slots__ = ()

    def __new__(cls, episode_ids: Any = ()) -> "_AbsentProviderSnapshot":
        normalized_ids = _normalize_snapshot_ids(episode_ids)
        if normalized_ids:
            raise PublicationStateError("absent snapshot cannot carry episode ids")
        return tuple.__new__(cls)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @property
    def episode_ids(self) -> tuple[int, ...]:
        return ()

    @property
    def completeness(self) -> SnapshotCompleteness:
        return SnapshotCompleteness.ABSENT


class _ObservedProviderSnapshot(
    tuple,
    ProviderSnapshot,
    _provider_snapshot_state_capability=_PROVIDER_SNAPSHOT_STATE_CAPABILITY,
):
    __slots__ = ()

    def __new__(
        cls,
        episode_ids: Any,
        evidence_source: Any,
    ) -> "_ObservedProviderSnapshot":
        normalized_source = _normalize_snapshot_evidence_source(evidence_source)
        if normalized_source is None:
            raise PublicationStateError("observed snapshot requires an evidence source")
        return tuple.__new__(
            cls,
            (_normalize_snapshot_ids(episode_ids), normalized_source),
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @property
    def episode_ids(self) -> tuple[int, ...]:
        return self[0]

    @property
    def evidence_source(self) -> str:
        return self[1]

    def require_evidence_source(self) -> str:
        return self.evidence_source


class _TruncatedProviderSnapshot(
    _ObservedProviderSnapshot,
    _provider_snapshot_state_capability=_PROVIDER_SNAPSHOT_STATE_CAPABILITY,
):
    __slots__ = ()

    @property
    def completeness(self) -> SnapshotCompleteness:
        return SnapshotCompleteness.TRUNCATED


class _CompleteProviderSnapshot(
    _ObservedProviderSnapshot,
    _provider_snapshot_state_capability=_PROVIDER_SNAPSHOT_STATE_CAPABILITY,
):
    __slots__ = ()

    @property
    def completeness(self) -> SnapshotCompleteness:
        return SnapshotCompleteness.COMPLETE


def _trusted_provider_snapshot_values(
    snapshot: ProviderSnapshot,
) -> tuple[SnapshotCompleteness, tuple[int, ...], str | None]:
    snapshot_type = type(snapshot)
    if snapshot_type is _AbsentProviderSnapshot:
        if tuple.__len__(snapshot) != 0:
            raise PublicationStateError("absent snapshot has invalid stored state")
        return SnapshotCompleteness.ABSENT, (), None
    if snapshot_type is _TruncatedProviderSnapshot:
        completeness = SnapshotCompleteness.TRUNCATED
    elif snapshot_type is _CompleteProviderSnapshot:
        completeness = SnapshotCompleteness.COMPLETE
    else:
        raise PublicationStateError("snapshot must use a trusted concrete type")

    if tuple.__len__(snapshot) != 2:
        raise PublicationStateError("observed snapshot has invalid stored state")
    episode_ids = tuple.__getitem__(snapshot, 0)
    evidence_source = tuple.__getitem__(snapshot, 1)
    if (
        type(episode_ids) is not tuple
        or any(type(episode_id) is not int for episode_id in episode_ids)
        or tuple(sorted(set(episode_ids))) != episode_ids
    ):
        raise PublicationStateError("snapshot episode ids have invalid stored state")
    if (
        type(evidence_source) is not str
        or _normalize_snapshot_evidence_source(evidence_source) != evidence_source
    ):
        raise PublicationStateError("snapshot evidence source has invalid stored state")
    return completeness, episode_ids, evidence_source


def _provider_snapshot_to_details(snapshot: ProviderSnapshot) -> dict[str, Any]:
    completeness, episode_ids, evidence_source = _trusted_provider_snapshot_values(snapshot)
    details: dict[str, Any] = {"snapshot_completeness": completeness.value}
    if episode_ids or completeness != SnapshotCompleteness.ABSENT:
        details["pre_create_episode_ids"] = list(episode_ids)
    if evidence_source is not None:
        details["snapshot_evidence_source"] = evidence_source
    return details


@dataclass(frozen=True)
class CreateSafetyState:
    provenance: CreateIntentProvenance
    snapshot: ProviderSnapshot
    mutation_possibility: MutationPossibility

    def __post_init__(self) -> None:
        if type(self) is not CreateSafetyState:
            raise PublicationStateError("create safety state must use the trusted concrete type")
        if type(self.provenance) is not CreateIntentProvenance:
            raise PublicationStateError("create provenance must be explicit")
        _trusted_provider_snapshot_values(self.snapshot)
        if type(self.mutation_possibility) is not MutationPossibility:
            raise PublicationStateError("mutation possibility must be explicit")

    @classmethod
    def reconciliation_backed(cls, snapshot: ProviderSnapshot) -> "CreateSafetyState":
        return cls(
            CreateIntentProvenance.RECONCILIATION_BACKED,
            snapshot,
            MutationPossibility.NOT_POSSIBLE,
        )

    @classmethod
    def unreconciled_override(cls) -> "CreateSafetyState":
        return cls(
            CreateIntentProvenance.BLIND_UNRECONCILED,
            ProviderSnapshot.absent(),
            MutationPossibility.POSSIBLE,
        )

    @classmethod
    def upload_dispatch(cls) -> "CreateSafetyState":
        return cls(
            CreateIntentProvenance.UPLOAD_DISPATCH,
            ProviderSnapshot.absent(),
            MutationPossibility.POSSIBLE,
        )

    @classmethod
    def provider_confirmed(
        cls,
        provenance: CreateIntentProvenance,
        snapshot: ProviderSnapshot | None = None,
    ) -> "CreateSafetyState":
        return cls(
            provenance,
            snapshot or ProviderSnapshot.absent(),
            MutationPossibility.CONFIRMED,
        )

    def to_details(self) -> dict[str, Any]:
        return _create_safety_state_to_details(self)


def _create_safety_state_to_details(state: CreateSafetyState) -> dict[str, Any]:
    if type(state) is not CreateSafetyState:
        raise PublicationStateError("create safety state must use the trusted concrete type")
    provenance = object.__getattribute__(state, "provenance")
    snapshot = object.__getattribute__(state, "snapshot")
    mutation_possibility = object.__getattribute__(state, "mutation_possibility")
    if type(provenance) is not CreateIntentProvenance:
        raise PublicationStateError("create provenance must be explicit")
    if type(mutation_possibility) is not MutationPossibility:
        raise PublicationStateError("mutation possibility must be explicit")
    return {
        "create_provenance": provenance.value,
        "mutation_possibility": mutation_possibility.value,
        **_provider_snapshot_to_details(snapshot),
    }


def _parse_snapshot_ids(raw_ids: Any) -> tuple[int, ...]:
    if not isinstance(raw_ids, list):
        raise PublicationStateError("create safety evidence has no episode id snapshot")
    ids: list[int] = []
    for raw_id in raw_ids:
        if isinstance(raw_id, bool):
            raise PublicationStateError("create safety evidence contains an invalid boolean id")
        try:
            ids.append(int(raw_id))
        except (TypeError, ValueError) as exc:
            raise PublicationStateError("create safety evidence contains an unreadable id") from exc
    return tuple(ids)


def _legacy_snapshot_from_details(details: Mapping[str, Any]) -> ProviderSnapshot:
    raw_complete = details.get("pre_create_snapshot_complete")
    if not isinstance(raw_complete, bool):
        raise PublicationStateError(
            "legacy create safety evidence has no boolean snapshot completeness"
        )
    ids = _parse_snapshot_ids(details.get("pre_create_episode_ids"))
    if raw_complete:
        return ProviderSnapshot.complete(ids, evidence_source="legacy_pre_create_snapshot")
    return ProviderSnapshot.truncated(ids, evidence_source="legacy_pre_create_snapshot")


def create_safety_state_from_record(record: Mapping[str, Any]) -> CreateSafetyState | None:
    details = record.get("details")
    if not isinstance(details, Mapping):
        details = {}

    raw_provenance = details.get("create_provenance")
    if isinstance(raw_provenance, str):
        try:
            provenance = CreateIntentProvenance(raw_provenance)
        except ValueError as exc:
            raise PublicationStateError("create safety evidence has unknown provenance") from exc
    elif record.get("operation") == "upload_intent":
        provenance = CreateIntentProvenance.UPLOAD_DISPATCH
    elif record.get("operation") == "unreconciled_create_intent":
        provenance = CreateIntentProvenance.BLIND_UNRECONCILED
    elif record.get("operation") == "create_episode_intent":
        provenance = CreateIntentProvenance.RECONCILIATION_BACKED
    else:
        return None

    if "snapshot_completeness" in details:
        raw_completeness = details.get("snapshot_completeness")
        if not isinstance(raw_completeness, str):
            raise PublicationStateError("create safety evidence has unknown snapshot state")
        try:
            completeness = SnapshotCompleteness(raw_completeness)
        except ValueError as exc:
            raise PublicationStateError(
                "create safety evidence has unknown snapshot state"
            ) from exc
        if completeness == SnapshotCompleteness.ABSENT:
            snapshot = ProviderSnapshot.absent()
        else:
            evidence_source = _normalize_snapshot_evidence_source(
                details.get("snapshot_evidence_source")
            )
            if evidence_source is None:
                raise PublicationStateError(
                    "explicit observed snapshot has no valid persisted evidence source"
                )
            snapshot = ProviderSnapshot(
                _parse_snapshot_ids(details.get("pre_create_episode_ids")),
                completeness,
                evidence_source,
            )
    elif "pre_create_snapshot_complete" in details:
        snapshot = _legacy_snapshot_from_details(details)
    else:
        snapshot = ProviderSnapshot.absent()

    raw_mutation = details.get("mutation_possibility")
    if isinstance(raw_mutation, str):
        try:
            mutation_possibility = MutationPossibility(raw_mutation)
        except ValueError as exc:
            raise PublicationStateError(
                "create safety evidence has unknown mutation state"
            ) from exc
    elif record.get("provider_artifact_id") or record.get("provider_id"):
        mutation_possibility = MutationPossibility.CONFIRMED
    elif provenance in (
        CreateIntentProvenance.BLIND_UNRECONCILED,
        CreateIntentProvenance.UPLOAD_DISPATCH,
    ):
        mutation_possibility = MutationPossibility.POSSIBLE
    else:
        mutation_possibility = MutationPossibility.NOT_POSSIBLE

    return CreateSafetyState(provenance, snapshot, mutation_possibility)


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
        elif (
            name == "pre_create_episode_ids"
            and isinstance(value, list)
            and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        ):
            safe[name] = value
        elif isinstance(value, list) and all(
            isinstance(item, (bool, int, float)) or item is None for item in value
        ):
            safe[name] = value[:100]
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
    create_safety_state: CreateSafetyState | None = None,
    at: datetime | None = None,
    _rearmable_claim: bool = False,
) -> PublicationEvidence | None:
    validate_outcome(outcome)
    if create_safety_state is not None and type(create_safety_state) is not CreateSafetyState:
        raise PublicationStateError("create safety state must use the trusted concrete type")
    if details and _CREATE_SAFETY_DETAIL_KEYS.intersection(details):
        raise PublicationStateError(
            "create safety details must be persisted through create_safety_state"
        )
    safe_details = _safe_details(details) or {}
    if create_safety_state is not None:
        safe_details.update(_create_safety_state_to_details(create_safety_state))
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
        document = _load_evidence(
            raw if raw is not None else legacy_raw,
            identity.accepted_job_id,
            strict=True,
        )
        records = document["records"]
        duplicate_index: int | None = None
        for index, existing in enumerate(records):
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
                duplicate_index = index
        if duplicate_index is not None:
            if not _rearmable_claim:
                later_claim = any(
                    isinstance(candidate, Mapping)
                    and candidate.get("job_id") == identity.accepted_job_id
                    and candidate.get("week") == identity.week
                    and candidate.get("article_sha256") == identity.article_sha256
                    and candidate.get("manifest_sha256") == identity.manifest_sha256
                    and candidate.get("publish_run_id") == identity.publish_run_id
                    and candidate.get("platform") == platform
                    and candidate.get("media_kind") == media_kind
                    and candidate.get("operation") in REARMABLE_CLAIM_OPERATIONS
                    for candidate in records[duplicate_index + 1 :]
                )
                if retry_blocked or not later_claim:
                    return raw if raw is not None else legacy_raw or b""
            else:
                retry_authorization: Mapping[str, Any] | None = None
                for candidate in records[duplicate_index + 1 :]:
                    if not isinstance(candidate, Mapping):
                        raise PublicationStateError(
                            "publication evidence contains a malformed record"
                        )
                    if (
                        candidate.get("job_id") == identity.accepted_job_id
                        and candidate.get("week") == identity.week
                        and candidate.get("article_sha256") == identity.article_sha256
                        and candidate.get("manifest_sha256") == identity.manifest_sha256
                        and candidate.get("publish_run_id") == identity.publish_run_id
                        and candidate.get("platform") == platform
                        and candidate.get("media_kind") == media_kind
                    ):
                        if candidate.get("operation") in REARMABLE_CLAIM_OPERATIONS:
                            retry_authorization = None
                        else:
                            retry_authorization = candidate
                if (
                    retry_authorization is None
                    or retry_authorization.get("retry_blocked") is not False
                ):
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
            details=safe_details or None,
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


def claim_evidence(
    storage: StorageBackend,
    identity: PublicationIdentity,
    *,
    platform: str,
    media_kind: str,
    operation: str,
    details: Mapping[str, Any] | None = None,
    create_safety_state: CreateSafetyState | None = None,
    at: datetime | None = None,
) -> PublicationEvidence | None:
    """Atomically acquire or re-arm a provider mutation claim.

    A historical claim can only be acquired again after later evidence for the
    same publication identity and provider explicitly records
    ``retry_blocked=false``. The newly appended claim consumes that
    authorization, so concurrent contenders cannot both proceed.
    """
    if operation not in REARMABLE_CLAIM_OPERATIONS:
        raise PublicationStateError(f"operation is not a re-armable claim: {operation!r}")
    return append_evidence(
        storage,
        identity,
        platform=platform,
        media_kind=media_kind,
        operation=operation,
        outcome=PUBLICATION_UNKNOWN,
        mutation_attempted=False,
        retry_blocked=True,
        code="mutation_intent",
        details=details,
        create_safety_state=create_safety_state,
        at=at,
        _rearmable_claim=True,
    )


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


def spotify_video_retry_blocking_record(
    document: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    records = document.get("records") if isinstance(document, Mapping) else None
    if not isinstance(records, list):
        return None
    for record in reversed(records):
        if (
            not isinstance(record, Mapping)
            or record.get("platform") != "spotify"
            or record.get("media_kind") != "video"
        ):
            continue
        details = record.get("details")
        safe_reconciliation_intent = (
            record.get("operation") == "create_episode_intent"
            and record.get("outcome") == PUBLICATION_UNKNOWN
            and record.get("mutation_attempted") is False
            and not record.get("provider_id")
            and not record.get("provider_artifact_id")
            and record.get("code") == "mutation_intent"
            and isinstance(details, Mapping)
            and isinstance(details.get("pre_create_episode_ids"), list)
            and details.get("pre_create_snapshot_complete") is True
        )
        if safe_reconciliation_intent:
            return None
        if record.get("outcome") in (
            UPLOADED,
            PUBLICATION_UNKNOWN,
            MANUAL_HANDOFF_REQUIRED,
            DRAFT_CREATED,
            PUBLISHED,
        ) and record.get("retry_blocked", True):
            return record
        return None
    return None


def spotify_video_retry_is_blocked(document: Mapping[str, Any] | None) -> bool:
    return spotify_video_retry_blocking_record(document) is not None


def emit_publication_signal(
    storage: StorageBackend,
    identity: PublicationIdentity,
    *,
    platform: str,
    media_kind: str,
    outcome: str,
    provider_artifact_id: str | int | None = None,
    code: str | None = None,
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
    )
