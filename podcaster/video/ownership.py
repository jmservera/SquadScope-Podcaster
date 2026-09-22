"""Durable fenced ownership for post-compose video mutations."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from podcaster.storage import StorageBackend

SCHEMA_VERSION = "squadscope-video-downstream-ownership-v1"
_CONTENT_TYPE = "application/json; charset=utf-8"
_EXPIRY_SKEW_RESERVE = timedelta(seconds=5)


class OwnershipError(RuntimeError):
    """Raised when downstream mutation authority cannot be proven."""


@dataclass(frozen=True)
class OwnershipClaim:
    job_id: str
    owner: str
    claim_id: str
    execution_id: str
    fencing_token: int
    visibility_expires_at: str
    lease_expires_at: str


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


def _next_fencing_token(document: Mapping[str, Any]) -> int:
    current = document.get("fencing_token", 0)
    if type(current) is not int or current < 0:
        raise OwnershipError("ownership fencing token is malformed")
    return current + 1


class VideoOwnershipGuard:
    """Authoritative claim and boundary-permit repository for one video job."""

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
        current_time = clock()
        visibility = _iso(visibility_expires_at)
        lease = _iso(lease_expires_at or visibility_expires_at)
        safe_now = current_time + _EXPIRY_SKEW_RESERVE
        if _parse_time(visibility) <= safe_now or _parse_time(lease) <= safe_now:
            raise OwnershipError("ownership expiry has already elapsed")
        captured: dict[str, Any] = {}

        def _claim(raw: bytes | None) -> bytes:
            document = _load(raw)
            current = document.get("claim")
            if isinstance(current, Mapping):
                current_expiry = min(
                    filter(
                        None,
                        (
                            _parse_time(current.get("visibility_expires_at")),
                            _parse_time(current.get("lease_expires_at")),
                        ),
                    ),
                    default=None,
                )
                if current_expiry is not None and current_expiry > safe_now:
                    if current.get("owner") != owner or current.get("execution_id") != execution_id:
                        raise OwnershipError("video job already has an active downstream owner")
                    captured.update(current)
                    return raw if raw is not None else b""
            token = _next_fencing_token(document)
            claim = {
                "owner": owner,
                "claim_id": uuid.uuid4().hex,
                "execution_id": execution_id,
                "fencing_token": token,
                "visibility_expires_at": visibility,
                "lease_expires_at": lease,
                "claimed_at": _iso(current_time),
            }
            document.update(
                {
                    "schema_version": SCHEMA_VERSION,
                    "job_id": job_id,
                    "fencing_token": token,
                    "claim": claim,
                    "boundaries": document.get("boundaries", {}),
                    "updated_at": _iso(current_time),
                }
            )
            captured.update(claim)
            return _dump(document)

        storage.update_bytes(ownership_path(job_id), _CONTENT_TYPE, _claim)
        claim = OwnershipClaim(
            job_id=job_id,
            owner=str(captured["owner"]),
            claim_id=str(captured["claim_id"]),
            execution_id=str(captured["execution_id"]),
            fencing_token=int(captured["fencing_token"]),
            visibility_expires_at=str(captured["visibility_expires_at"]),
            lease_expires_at=str(captured["lease_expires_at"]),
        )
        guard = cls(storage, claim, now=clock)
        guard.assert_current()
        return guard

    def _require_current(self, document: Mapping[str, Any]) -> Mapping[str, Any]:
        current = document.get("claim")
        if not isinstance(current, Mapping):
            raise OwnershipError("ownership claim is missing")
        if (
            type(document.get("fencing_token")) is not int
            or type(current.get("fencing_token")) is not int
        ):
            raise OwnershipError("ownership fencing token is malformed")
        if (
            document.get("job_id") != self.claim.job_id
            or document.get("fencing_token") != self.claim.fencing_token
            or current.get("owner") != self.claim.owner
            or current.get("claim_id") != self.claim.claim_id
            or current.get("execution_id") != self.claim.execution_id
            or current.get("fencing_token") != self.claim.fencing_token
        ):
            raise OwnershipError("ownership claim is stale")
        now = self.now() + _EXPIRY_SKEW_RESERVE
        visibility = _parse_time(current.get("visibility_expires_at"))
        lease = _parse_time(current.get("lease_expires_at"))
        if visibility is None or lease is None or visibility <= now or lease <= now:
            raise OwnershipError("ownership claim has expired")
        return current

    def assert_current(self) -> None:
        raw = self.storage.get_bytes(ownership_path(self.claim.job_id))
        if raw is None:
            raise OwnershipError("ownership readback is unavailable")
        self._require_current(_load(raw))
