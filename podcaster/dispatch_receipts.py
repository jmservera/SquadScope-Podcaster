"""Durable sanitized correlation for upstream dispatch and first Azure arrival."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from podcaster.storage import StorageBackend

DISPATCH_SCHEMA_VERSION = "squadscope-podcaster-dispatch-receipt-v1"
DISPATCH_PREFIX = "dispatch-receipts"
_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_WEEK = re.compile(r"^\d{4}-W\d{2}$")
_DISPATCH_RESULTS = frozenset({"pending", "accepted", "blocked", "failed"})


class DispatchReceiptError(RuntimeError):
    """Raised when dispatch correlation is malformed or conflicts."""


@dataclass(frozen=True)
class DispatchSignal:
    name: str
    value: float
    severity: str
    state: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _token(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not _TOKEN.fullmatch(normalized):
        raise DispatchReceiptError(f"{name} is malformed")
    return normalized


def dispatch_receipt_path(correlation_id: str) -> str:
    return f"{DISPATCH_PREFIX}/{_token('dispatch_correlation_id', correlation_id)}.json"


class DispatchReceiptRepository:
    def __init__(
        self,
        storage: StorageBackend,
        *,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.storage = storage
        self.now = now

    def read(self, correlation_id: str) -> dict[str, Any] | None:
        raw = self.storage.get_bytes(dispatch_receipt_path(correlation_id))
        if raw is None:
            return None
        document = json.loads(raw.decode("utf-8"))
        if document.get("schema_version") != DISPATCH_SCHEMA_VERSION:
            raise DispatchReceiptError("dispatch receipt schema is unsupported")
        return document

    def require_arrival_eligible(self, *, correlation_id: str, week: str) -> dict[str, Any]:
        document = self.read(correlation_id)
        if document is None:
            raise DispatchReceiptError("dispatch intent is not registered")
        if document.get("week") != week:
            raise DispatchReceiptError("dispatch arrival week conflicts")
        if document.get("dispatch_result") in {"blocked", "failed"}:
            raise DispatchReceiptError("dispatch intent is terminal before Azure arrival")
        return document

    def register_intent(
        self,
        *,
        correlation_id: str,
        week: str,
        dispatch_result: str,
        source: str,
    ) -> tuple[dict[str, Any], bool]:
        correlation_id = _token("dispatch_correlation_id", correlation_id)
        if not _WEEK.fullmatch(week):
            raise DispatchReceiptError("week is malformed")
        if dispatch_result not in _DISPATCH_RESULTS:
            raise DispatchReceiptError("dispatch_result is unsupported")
        source = _token("dispatch_source", source)
        created = False
        captured: dict[str, Any] = {}

        def _register(raw: bytes | None) -> bytes:
            nonlocal created
            if raw is not None:
                current = json.loads(raw.decode("utf-8"))
                if current.get("week") != week or current.get("source") != source:
                    raise DispatchReceiptError("dispatch correlation conflicts")
                if current.get("dispatch_result") == "pending" and dispatch_result != "pending":
                    current["dispatch_result"] = dispatch_result
                    current["dispatch_recorded_at"] = _iso(self.now())
                    current["updated_at"] = _iso(self.now())
                captured.update(current)
                return json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
            timestamp = _iso(self.now())
            document = {
                "schema_version": DISPATCH_SCHEMA_VERSION,
                "dispatch_correlation_id": correlation_id,
                "week": week,
                "source": source,
                "intent_received_at": timestamp,
                "dispatch_result": dispatch_result,
                "dispatch_recorded_at": timestamp,
                "azure_api_accepted_at": None,
                "first_durable_arrival_at": None,
                "accepted_job_id": None,
                "arrival_state": "awaiting_arrival",
                "updated_at": timestamp,
            }
            captured.update(document)
            created = True
            return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()

        self.storage.update_bytes(
            dispatch_receipt_path(correlation_id),
            "application/json; charset=utf-8",
            _register,
        )
        return captured, created

    def record_arrival(
        self,
        *,
        correlation_id: str,
        week: str,
        accepted_job_id: str,
    ) -> dict[str, Any]:
        correlation_id = _token("dispatch_correlation_id", correlation_id)
        accepted_job_id = _token("accepted_job_id", accepted_job_id)
        captured: dict[str, Any] = {}

        def _arrive(raw: bytes | None) -> bytes:
            if raw is None:
                raise DispatchReceiptError("dispatch intent is not registered")
            current = json.loads(raw.decode("utf-8"))
            if current.get("week") != week:
                raise DispatchReceiptError("dispatch arrival week conflicts")
            existing = current.get("accepted_job_id")
            if existing not in (None, accepted_job_id):
                raise DispatchReceiptError("dispatch arrival job conflicts")
            timestamp = _iso(self.now())
            current["azure_api_accepted_at"] = current.get("azure_api_accepted_at") or timestamp
            current["first_durable_arrival_at"] = (
                current.get("first_durable_arrival_at") or timestamp
            )
            current["accepted_job_id"] = accepted_job_id
            current["arrival_state"] = "arrived"
            current["updated_at"] = timestamp
            captured.update(current)
            return json.dumps(current, sort_keys=True, separators=(",", ":")).encode()

        self.storage.update_bytes(
            dispatch_receipt_path(correlation_id),
            "application/json; charset=utf-8",
            _arrive,
        )
        return captured

    def missing_arrival_signals(
        self,
        *,
        warning_after: timedelta = timedelta(minutes=10),
        critical_after: timedelta = timedelta(minutes=30),
        limit: int = 500,
    ) -> list[DispatchSignal]:
        current = self.now()
        signals: list[DispatchSignal] = []
        for path in self.storage.list_blobs(f"{DISPATCH_PREFIX}/", limit=limit):
            raw = self.storage.get_bytes(path)
            if raw is None:
                continue
            document = json.loads(raw.decode("utf-8"))
            if document.get("first_durable_arrival_at"):
                continue
            accepted = _parse(str(document.get("intent_received_at") or ""))
            if accepted is None:
                signals.append(
                    DispatchSignal("dispatch_telemetry_missing", 1, "warning", "invalid_intent")
                )
                continue
            age = current - accepted
            if age >= critical_after:
                severity = "critical"
            elif age >= warning_after:
                severity = "warning"
            else:
                severity = "info"
            signals.append(
                DispatchSignal(
                    "dispatch_missing_azure_arrival_seconds",
                    max(0.0, age.total_seconds()),
                    severity,
                    str(document.get("dispatch_result") or "pending")[:32],
                )
            )
        return signals


def validate_dispatch_fields(payload: Mapping[str, Any]) -> list[str]:
    correlation_id = payload.get("dispatch_correlation_id")
    if correlation_id is None:
        return []
    try:
        _token("dispatch_correlation_id", str(correlation_id))
    except DispatchReceiptError as exc:
        return [str(exc)]
    return []
