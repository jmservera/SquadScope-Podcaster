"""Shared timing and admission contracts for the video pipeline."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any

from podcaster.sanitization import neutralize

PREFLIGHT_DEADLINE_SECONDS = 300
FANIN_DEADLINE_SECONDS = 1200
FALLBACK_DEADLINE_SECONDS = 1500
RENDER_DEADLINE_SECONDS = 3300
ARCHIVE_DEADLINE_SECONDS = 3600
PROVIDER_ADMISSION_DEADLINE_SECONDS = 4500
EVIDENCE_DEADLINE_SECONDS = 4920
JOB_DEADLINE_SECONDS = 5100

CLIP_LIFETIME_SECONDS = 720
PROVIDER_RESERVE_SECONDS = 1800
BUDGET_SCHEMA_VERSION = 1
CLIP_ADMISSION_SCHEMA_VERSION = 1

UtcNow = Callable[[], datetime]
Monotonic = Callable[[], float]


class VideoStage(str, Enum):
    PREFLIGHT = "preflight"
    FANIN = "fanin"
    FALLBACK = "fallback"
    RENDER = "render"
    ARCHIVE = "archive"
    PROVIDER_ADMISSION = "provider_admission"
    EVIDENCE = "evidence"
    SHUTDOWN = "shutdown"


STAGE_DEADLINES: Mapping[VideoStage, int] = MappingProxyType(
    {
        VideoStage.PREFLIGHT: PREFLIGHT_DEADLINE_SECONDS,
        VideoStage.FANIN: FANIN_DEADLINE_SECONDS,
        VideoStage.FALLBACK: FALLBACK_DEADLINE_SECONDS,
        VideoStage.RENDER: RENDER_DEADLINE_SECONDS,
        VideoStage.ARCHIVE: ARCHIVE_DEADLINE_SECONDS,
        VideoStage.PROVIDER_ADMISSION: PROVIDER_ADMISSION_DEADLINE_SECONDS,
        VideoStage.EVIDENCE: EVIDENCE_DEADLINE_SECONDS,
        VideoStage.SHUTDOWN: JOB_DEADLINE_SECONDS,
    }
)


class AdmissionReason(str, Enum):
    ADMITTED = "admitted"
    STAGE_DEADLINE_REACHED = "stage_deadline_reached"
    JOB_DEADLINE_REACHED = "job_deadline_reached"
    PROVIDER_DEADLINE_REACHED = "provider_deadline_reached"
    PROVIDER_RESERVE_INSUFFICIENT = "provider_reserve_insufficient"
    INVALID_DURABLE_PROJECTION = "invalid_durable_projection"


class TimeoutReason(str, Enum):
    OPERATION_DEADLINE = "operation_deadline"
    STAGE_DEADLINE = "stage_deadline"
    JOB_DEADLINE = "job_deadline"
    CLIP_DEADLINE = "clip_deadline"
    CANCELLED = "cancelled"


class ProviderMutationAdmissionError(TimeoutError):
    """Raised when a new provider mutation cannot start within the shared budget."""

    def __init__(
        self,
        decision: AdmissionDecision,
        *,
        provider: str | None = None,
        mutation_started: bool = False,
    ) -> None:
        super().__init__(
            f"provider mutation denied: {decision.reason.value} "
            f"(remaining={decision.remaining_seconds:.3f}s)"
        )
        self.decision = decision
        self.provider = provider
        self.mutation_started = mutation_started

    def __reduce__(self) -> tuple[object, tuple[AdmissionDecision], dict[str, object]]:
        return (
            type(self),
            (self.decision,),
            {
                "provider": self.provider,
                "mutation_started": self.mutation_started,
            },
        )


class TimingEvidenceKind(str, Enum):
    STAGE = "stage"
    ATTEMPT = "attempt"
    HEARTBEAT = "heartbeat"
    ARTIFACT = "artifact"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    return _require_utc(parsed, field_name)


def _format_utc(value: datetime) -> str:
    return _require_utc(value, "timestamp").isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class BudgetProjection:
    """Durable UTC projection shared across process and delivery boundaries."""

    started_at_utc: datetime
    deadline_at_utc: datetime
    schema_version: int = BUDGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        started = _require_utc(self.started_at_utc, "started_at_utc")
        deadline = _require_utc(self.deadline_at_utc, "deadline_at_utc")
        if self.schema_version != BUDGET_SCHEMA_VERSION:
            raise ValueError(f"unsupported budget schema version {self.schema_version}")
        expected = started + timedelta(seconds=JOB_DEADLINE_SECONDS)
        if deadline != expected:
            raise ValueError("durable job deadline does not match the shared lifetime")
        object.__setattr__(self, "started_at_utc", started)
        object.__setattr__(self, "deadline_at_utc", deadline)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "started_at_utc": _format_utc(self.started_at_utc),
            "deadline_at_utc": _format_utc(self.deadline_at_utc),
        }

    def stage_deadline_at_utc(self, stage: VideoStage) -> datetime:
        return self.started_at_utc + timedelta(seconds=STAGE_DEADLINES[stage])

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> BudgetProjection:
        return cls(
            schema_version=int(data.get("schema_version", 0)),
            started_at_utc=_parse_utc(data.get("started_at_utc"), "started_at_utc"),
            deadline_at_utc=_parse_utc(data.get("deadline_at_utc"), "deadline_at_utc"),
        )


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: AdmissionReason
    remaining_seconds: float


@dataclass(frozen=True)
class ClipAdmission:
    """Durable first admission for a clip; redelivery must reuse this value."""

    clip_id: str
    first_admitted_at_utc: datetime
    schema_version: int = CLIP_ADMISSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.clip_id.strip():
            raise ValueError("clip_id must not be empty")
        if self.schema_version != CLIP_ADMISSION_SCHEMA_VERSION:
            raise ValueError(f"unsupported clip admission schema version {self.schema_version}")
        object.__setattr__(
            self,
            "first_admitted_at_utc",
            _require_utc(self.first_admitted_at_utc, "first_admitted_at_utc"),
        )

    @property
    def deadline_at_utc(self) -> datetime:
        return self.first_admitted_at_utc + timedelta(seconds=CLIP_LIFETIME_SECONDS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "clip_id": self.clip_id,
            "first_admitted_at_utc": _format_utc(self.first_admitted_at_utc),
            "deadline_at_utc": _format_utc(self.deadline_at_utc),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ClipAdmission:
        admission = cls(
            schema_version=int(data.get("schema_version", 0)),
            clip_id=str(data.get("clip_id", "")),
            first_admitted_at_utc=_parse_utc(
                data.get("first_admitted_at_utc"), "first_admitted_at_utc"
            ),
        )
        persisted_deadline = _parse_utc(data.get("deadline_at_utc"), "deadline_at_utc")
        if persisted_deadline != admission.deadline_at_utc:
            raise ValueError("durable clip deadline does not match first admission")
        return admission

    @classmethod
    def first_or_existing(
        cls,
        clip_id: str,
        *,
        existing: Mapping[str, object] | None = None,
        now_utc: datetime | None = None,
    ) -> ClipAdmission:
        if existing is not None:
            admission = cls.from_dict(existing)
            if admission.clip_id != clip_id:
                raise ValueError("existing clip admission belongs to another clip")
            return admission
        return cls(clip_id=clip_id, first_admitted_at_utc=now_utc or _utc_now())


@dataclass(frozen=True)
class RecorderTimingEnvelope:
    """Serializable parent and first-clip timing facts sent to recorder processes."""

    budget: BudgetProjection
    clip: ClipAdmission
    browser_deadline_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.browser_deadline_at_utc is not None:
            object.__setattr__(
                self,
                "browser_deadline_at_utc",
                _require_utc(self.browser_deadline_at_utc, "browser_deadline_at_utc"),
            )

    @property
    def effective_deadline_at_utc(self) -> datetime:
        candidates = [
            self.clip.deadline_at_utc,
            self.budget.stage_deadline_at_utc(VideoStage.FANIN),
        ]
        if self.browser_deadline_at_utc is not None:
            candidates.append(self.browser_deadline_at_utc)
        return min(candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget.to_dict(),
            "clip": self.clip.to_dict(),
            "browser_deadline_at_utc": (
                _format_utc(self.browser_deadline_at_utc)
                if self.browser_deadline_at_utc is not None
                else None
            ),
            "effective_deadline_at_utc": _format_utc(self.effective_deadline_at_utc),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> RecorderTimingEnvelope:
        budget_data = data.get("budget")
        clip_data = data.get("clip")
        if not isinstance(budget_data, Mapping) or not isinstance(clip_data, Mapping):
            raise ValueError("recorder timing envelope is missing budget or clip timing")
        raw_browser_deadline = data.get("browser_deadline_at_utc")
        envelope = cls(
            budget=BudgetProjection.from_dict(budget_data),
            clip=ClipAdmission.from_dict(clip_data),
            browser_deadline_at_utc=(
                _parse_utc(raw_browser_deadline, "browser_deadline_at_utc")
                if raw_browser_deadline is not None
                else None
            ),
        )
        persisted_effective = _parse_utc(
            data.get("effective_deadline_at_utc"), "effective_deadline_at_utc"
        )
        if persisted_effective != envelope.effective_deadline_at_utc:
            raise ValueError("recorder effective deadline does not match timing facts")
        return envelope


@dataclass(frozen=True)
class VideoStageBudget:
    """Immutable run budget using the conservative local/durable remaining time."""

    projection: BudgetProjection
    _local_started_monotonic: float
    _local_elapsed_at_start: float
    _monotonic: Monotonic = field(repr=False, compare=False)
    _utcnow: UtcNow = field(repr=False, compare=False)

    @classmethod
    def start(
        cls,
        *,
        now_utc: datetime | None = None,
        monotonic: Monotonic = time.monotonic,
        utcnow: UtcNow = _utc_now,
    ) -> VideoStageBudget:
        started = _require_utc(now_utc or utcnow(), "now_utc")
        projection = BudgetProjection(
            started_at_utc=started,
            deadline_at_utc=started + timedelta(seconds=JOB_DEADLINE_SECONDS),
        )
        return cls._load(projection, now_utc=started, monotonic=monotonic, utcnow=utcnow)

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, object],
        *,
        now_utc: datetime | None = None,
        monotonic: Monotonic = time.monotonic,
        utcnow: UtcNow = _utc_now,
    ) -> VideoStageBudget:
        projection = BudgetProjection.from_dict(data)
        return cls._load(projection, now_utc=now_utc, monotonic=monotonic, utcnow=utcnow)

    @classmethod
    def _load(
        cls,
        projection: BudgetProjection,
        *,
        now_utc: datetime | None,
        monotonic: Monotonic,
        utcnow: UtcNow,
    ) -> VideoStageBudget:
        current_utc = _require_utc(now_utc or utcnow(), "now_utc")
        durable_elapsed = (current_utc - projection.started_at_utc).total_seconds()
        local_elapsed = (
            JOB_DEADLINE_SECONDS
            if durable_elapsed < 0
            else min(JOB_DEADLINE_SECONDS, durable_elapsed)
        )
        return cls(
            projection=projection,
            _local_started_monotonic=monotonic(),
            _local_elapsed_at_start=local_elapsed,
            _monotonic=monotonic,
            _utcnow=utcnow,
        )

    def to_dict(self) -> dict[str, Any]:
        return self.projection.to_dict()

    def local_elapsed_seconds(self) -> float:
        elapsed = self._local_elapsed_at_start + (self._monotonic() - self._local_started_monotonic)
        return max(0.0, elapsed)

    def durable_elapsed_seconds(self) -> float:
        current = _require_utc(self._utcnow(), "utcnow")
        return max(0.0, (current - self.projection.started_at_utc).total_seconds())

    def now_utc(self) -> datetime:
        """Return the budget clock's current UTC value."""
        return _require_utc(self._utcnow(), "utcnow")

    def elapsed_seconds(self) -> float:
        return max(self.local_elapsed_seconds(), self.durable_elapsed_seconds())

    def remaining_seconds(self, stage: VideoStage = VideoStage.SHUTDOWN) -> float:
        cutoff = STAGE_DEADLINES[stage]
        local_remaining = cutoff - self.local_elapsed_seconds()
        durable_remaining = cutoff - self.durable_elapsed_seconds()
        return max(0.0, min(local_remaining, durable_remaining))

    def operation_timeout(
        self,
        stage: VideoStage,
        requested_seconds: float | None = None,
    ) -> float:
        remaining = self.remaining_seconds(stage)
        if requested_seconds is None:
            return remaining
        return max(0.0, min(float(requested_seconds), remaining))

    def admit(self, stage: VideoStage) -> AdmissionDecision:
        remaining = self.remaining_seconds(stage)
        if remaining <= 0:
            reason = (
                AdmissionReason.JOB_DEADLINE_REACHED
                if stage is VideoStage.SHUTDOWN
                else AdmissionReason.STAGE_DEADLINE_REACHED
            )
            return AdmissionDecision(False, reason, remaining)
        return AdmissionDecision(True, AdmissionReason.ADMITTED, remaining)

    def admit_provider_mutation(self) -> AdmissionDecision:
        admission_remaining = self.remaining_seconds(VideoStage.PROVIDER_ADMISSION)
        if admission_remaining <= 0:
            return AdmissionDecision(
                False,
                AdmissionReason.PROVIDER_DEADLINE_REACHED,
                self.remaining_seconds(VideoStage.SHUTDOWN),
            )
        parent_remaining = self.remaining_seconds(VideoStage.SHUTDOWN)
        if parent_remaining < PROVIDER_RESERVE_SECONDS:
            return AdmissionDecision(
                False,
                AdmissionReason.PROVIDER_RESERVE_INSUFFICIENT,
                parent_remaining,
            )
        return AdmissionDecision(True, AdmissionReason.ADMITTED, parent_remaining)

    def require_provider_mutation(self) -> AdmissionDecision:
        """Admit one mutation boundary or fail closed with typed semantics."""
        decision = self.admit_provider_mutation()
        if not decision.allowed:
            raise ProviderMutationAdmissionError(decision)
        return decision


@dataclass(frozen=True)
class TimingEvidence:
    timestamp_utc: datetime
    kind: TimingEvidenceKind
    stage: VideoStage
    remaining_seconds: float
    reason: str | None = None
    details: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_utc", _require_utc(self.timestamp_utc, "timestamp_utc"))
        safe_reason = neutralize(self.reason, limit=128) if self.reason is not None else None
        safe_details = {
            neutralize(key, limit=64): neutralize(value, limit=256)
            for key, value in self.details.items()
        }
        object.__setattr__(self, "reason", safe_reason)
        object.__setattr__(self, "details", MappingProxyType(safe_details))

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": _format_utc(self.timestamp_utc),
            "kind": self.kind.value,
            "stage": self.stage.value,
            "remaining_seconds": max(0.0, round(self.remaining_seconds, 3)),
            "reason": self.reason,
            "details": dict(self.details),
        }


class BoundedTimingEvidence:
    """Append-only in-memory evidence buffer that stops accepting entries at its cap."""

    def __init__(self, max_events: int = 256) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._max_events = max_events
        self._events: list[TimingEvidence] = []
        self._dropped = 0

    def append(self, event: TimingEvidence) -> bool:
        if len(self._events) >= self._max_events:
            self._dropped += 1
            return False
        self._events.append(event)
        return True

    @property
    def dropped(self) -> int:
        return self._dropped

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_events": self._max_events,
            "events": [event.to_dict() for event in self._events],
            "dropped": self._dropped,
        }
