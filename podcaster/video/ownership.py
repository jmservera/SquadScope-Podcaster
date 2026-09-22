"""Durable fenced ownership for post-compose video mutations."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from podcaster.storage import StorageBackend

SCHEMA_VERSION = "squadscope-video-downstream-ownership-v2"
_CONTENT_TYPE = "application/json; charset=utf-8"
_EXPIRY_RESERVE = timedelta(seconds=5)


class OwnershipError(RuntimeError):
    """Raised when downstream mutation authority cannot be proven."""


@dataclass(frozen=True)
class OwnershipClaim:
    job_id: str
    owner: str
    claim_id: str
    execution_id: str
    fencing_token: int


@dataclass(frozen=True)
class BoundaryPermit:
    name: str
    permit_id: str
    fencing_token: int
    reconcile_only: bool


def ownership_path(job_id: str) -> str:
    return f"jobs/{job_id}/video/downstream-ownership.json"


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OwnershipError("ownership expiry must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _load(raw: bytes | None) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OwnershipError("ownership record is corrupt") from exc
    if type(document) is not dict:
        raise OwnershipError("ownership record is malformed")
    if document and document.get("schema_version") != SCHEMA_VERSION:
        raise OwnershipError("ownership record schema is unsupported")
    return document


def _dump(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


class VideoOwnershipGuard:
    """Persisted job claim plus CAS-fenced permits for downstream boundaries."""

    def __init__(
        self,
        storage: StorageBackend,
        claim: OwnershipClaim,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.storage = storage
        self.claim = claim
        self.now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def acquire(
        cls,
        storage: StorageBackend,
        job_id: str,
        *,
        owner: str,
        execution_id: str,
        visibility_expires_at: datetime,
        lease_expires_at: datetime | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> "VideoOwnershipGuard":
        clock = now or (lambda: datetime.now(timezone.utc))
        claimed_at = clock()
        visibility = _iso(visibility_expires_at)
        effective_lease = min(lease_expires_at or visibility_expires_at, visibility_expires_at)
        lease = _iso(effective_lease)
        if (
            _parse_time(visibility) <= claimed_at + _EXPIRY_RESERVE
            or _parse_time(lease) <= claimed_at + _EXPIRY_RESERVE
        ):
            raise OwnershipError("ownership expiry has already elapsed")
        captured: dict[str, Any] = {}

        def _claim(raw: bytes | None) -> bytes:
            document = _load(raw)
            current = document.get("claim")
            if isinstance(current, Mapping):
                expiry_values = [
                    value
                    for value in (
                        _parse_time(current.get("visibility_expires_at")),
                        _parse_time(current.get("lease_expires_at")),
                    )
                    if value is not None
                ]
                current_expiry = min(expiry_values) if len(expiry_values) == 2 else None
                if current_expiry is not None and current_expiry > claimed_at + _EXPIRY_RESERVE:
                    if current.get("owner") != owner or current.get("execution_id") != execution_id:
                        raise OwnershipError("video job already has an active downstream owner")
                    captured.update(current)
                    return raw if raw is not None else b""
            current_fence = document.get("fencing_token", 0)
            if type(current_fence) is not int or current_fence < 0:
                raise OwnershipError("ownership fencing token is malformed")
            fencing_token = current_fence + 1
            claim = {
                "owner": owner,
                "claim_id": uuid.uuid4().hex,
                "execution_id": execution_id,
                "fencing_token": fencing_token,
                "visibility_expires_at": visibility,
                "lease_expires_at": lease,
                "claimed_at": _iso(claimed_at),
            }
            document.update(
                {
                    "schema_version": SCHEMA_VERSION,
                    "job_id": job_id,
                    "fencing_token": fencing_token,
                    "claim": claim,
                    "boundaries": document.get("boundaries", {}),
                    "updated_at": _iso(claimed_at),
                }
            )
            captured.update(claim)
            return _dump(document)

        storage.update_bytes(ownership_path(job_id), _CONTENT_TYPE, _claim)
        guard = cls(
            storage,
            OwnershipClaim(
                job_id=job_id,
                owner=str(captured["owner"]),
                claim_id=str(captured["claim_id"]),
                execution_id=str(captured["execution_id"]),
                fencing_token=int(captured["fencing_token"]),
            ),
            now=clock,
        )
        guard.assert_current()
        return guard

    def _require_current(self, document: Mapping[str, Any]) -> None:
        current = document.get("claim")
        if not isinstance(current, Mapping):
            raise OwnershipError("ownership claim is missing")
        if (
            type(document.get("fencing_token")) is not int
            or type(current.get("fencing_token")) is not int
            or document.get("job_id") != self.claim.job_id
            or document.get("fencing_token") != self.claim.fencing_token
            or current.get("owner") != self.claim.owner
            or current.get("claim_id") != self.claim.claim_id
            or current.get("execution_id") != self.claim.execution_id
            or current.get("fencing_token") != self.claim.fencing_token
        ):
            raise OwnershipError("ownership claim is stale")
        visibility = _parse_time(current.get("visibility_expires_at"))
        lease = _parse_time(current.get("lease_expires_at"))
        safe_now = self.now() + _EXPIRY_RESERVE
        if visibility is None or lease is None or visibility <= safe_now or lease <= safe_now:
            raise OwnershipError("ownership claim has expired")

    def assert_current(self) -> None:
        raw = self.storage.get_bytes(ownership_path(self.claim.job_id))
        if raw is None:
            raise OwnershipError("ownership readback is unavailable")
        self._require_current(_load(raw))

    def refresh_lease(self, lease_expires_at: datetime) -> None:
        def _refresh(raw: bytes | None) -> bytes:
            document = _load(raw)
            self._require_current(document)
            visibility = _parse_time(document["claim"].get("visibility_expires_at"))
            if visibility is None:
                raise OwnershipError("visibility expiry is invalid")
            refreshed = _iso(min(lease_expires_at, visibility))
            lease = _parse_time(refreshed)
            if lease is None or lease <= self.now() + _EXPIRY_RESERVE:
                raise OwnershipError("refreshed lease expiry is outside visibility authority")
            document["claim"]["lease_expires_at"] = refreshed
            document["updated_at"] = _iso(self.now())
            return _dump(document)

        self.storage.update_bytes(ownership_path(self.claim.job_id), _CONTENT_TYPE, _refresh)
        self.assert_current()

    def begin(self, name: str, *, allow_idempotent_takeover: bool) -> BoundaryPermit:
        captured: dict[str, Any] = {}

        def _begin(raw: bytes | None) -> bytes:
            document = _load(raw)
            self._require_current(document)
            boundaries = document.setdefault("boundaries", {})
            if not isinstance(boundaries, dict):
                raise OwnershipError("ownership boundaries are malformed")
            previous = boundaries.get(name)
            if isinstance(previous, Mapping):
                same_claim = (
                    previous.get("claim_id") == self.claim.claim_id
                    and previous.get("fencing_token") == self.claim.fencing_token
                )
                if same_claim:
                    captured.update(previous)
                    captured["reconcile_only"] = previous.get("state") == "completed"
                    return raw if raw is not None else b""
                if not allow_idempotent_takeover:
                    captured.update(previous)
                    captured["reconcile_only"] = True
                    return raw if raw is not None else b""
            permit = {
                "name": name,
                "permit_id": uuid.uuid4().hex,
                "claim_id": self.claim.claim_id,
                "execution_id": self.claim.execution_id,
                "fencing_token": self.claim.fencing_token,
                "state": "active",
                "created_at": _iso(self.now()),
            }
            if isinstance(previous, Mapping):
                permit["reconciles_permit_id"] = previous.get("permit_id")
            boundaries[name] = permit
            document["updated_at"] = permit["created_at"]
            captured.update(permit)
            captured["reconcile_only"] = False
            return _dump(document)

        self.storage.update_bytes(ownership_path(self.claim.job_id), _CONTENT_TYPE, _begin)
        permit = BoundaryPermit(
            name=name,
            permit_id=str(captured["permit_id"]),
            fencing_token=int(captured["fencing_token"]),
            reconcile_only=bool(captured.get("reconcile_only")),
        )
        self.assert_permit(permit, mutation=not permit.reconcile_only)
        return permit

    def assert_permit(self, permit: BoundaryPermit, *, mutation: bool = True) -> None:
        raw = self.storage.get_bytes(ownership_path(self.claim.job_id))
        if raw is None:
            raise OwnershipError("ownership readback is unavailable")
        document = _load(raw)
        self._require_current(document)
        boundary = document.get("boundaries", {}).get(permit.name)
        if not isinstance(boundary, Mapping):
            raise OwnershipError("boundary permit is missing")
        if (
            boundary.get("permit_id") != permit.permit_id
            or boundary.get("fencing_token") != permit.fencing_token
        ):
            raise OwnershipError("boundary permit is stale")
        if mutation and (
            permit.reconcile_only
            or boundary.get("claim_id") != self.claim.claim_id
            or boundary.get("state") != "active"
        ):
            raise OwnershipError("boundary permit is reconciliation-only")

    def complete(self, permit: BoundaryPermit, *, target: str) -> None:
        def _complete(raw: bytes | None) -> bytes:
            document = _load(raw)
            self._require_current(document)
            boundary = document.get("boundaries", {}).get(permit.name)
            if not isinstance(boundary, dict):
                raise OwnershipError("boundary permit is missing")
            if (
                boundary.get("permit_id") != permit.permit_id
                or boundary.get("claim_id") != self.claim.claim_id
                or boundary.get("fencing_token") != self.claim.fencing_token
            ):
                raise OwnershipError("boundary permit is stale")
            boundary.update(
                {
                    "state": "completed",
                    "target": target,
                    "completed_at": _iso(self.now()),
                }
            )
            document["updated_at"] = boundary["completed_at"]
            return _dump(document)

        self.storage.update_bytes(ownership_path(self.claim.job_id), _CONTENT_TYPE, _complete)
        self.assert_permit(permit, mutation=False)

    def source_token(self, permit: BoundaryPermit) -> dict[str, Any]:
        self.assert_permit(permit, mutation=not permit.reconcile_only)
        return {
            "job_id": self.claim.job_id,
            "owner": self.claim.owner,
            "claim_id": self.claim.claim_id,
            "execution_id": self.claim.execution_id,
            "fencing_token": self.claim.fencing_token,
            "permit_name": permit.name,
            "permit_id": permit.permit_id,
        }
