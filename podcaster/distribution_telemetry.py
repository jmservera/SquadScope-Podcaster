"""Low-cardinality telemetry derived from durable distribution outbox truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

PROVIDER_STATE_EVENT = "distribution_provider_state"
SCHEDULER_STATE_EVENT = "distribution_scheduler_state"
WEEKLY_ALERT_METRICS = frozenset(
    {"distribution_identity_conflict", "distribution_weekly_non_green"}
)


@dataclass(frozen=True)
class DistributionSignal:
    name: str
    value: float
    severity: str
    provider: str
    media_kind: str
    state: str


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _age_seconds(now: datetime, value: str | None) -> float:
    timestamp = _parse(value)
    if timestamp is None:
        return 0.0
    return max(0.0, (now - timestamp).total_seconds())


def signals_for_outbox(
    document: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> list[DistributionSignal]:
    """Build alertable signals without provider item IDs, job IDs, or PII."""

    current = now or datetime.now(timezone.utc)
    artifact = document.get("artifact")
    media_kind = (
        str(artifact.get("media_kind") or "unknown") if isinstance(artifact, Mapping) else "unknown"
    )
    enqueue = document.get("enqueue")
    enqueue_at = str(enqueue.get("at") or "") if isinstance(enqueue, Mapping) else ""
    providers = document.get("providers")
    if not isinstance(providers, Mapping):
        return []
    signals: list[DistributionSignal] = []
    weekly = document.get("weekly_aggregation")
    if isinstance(weekly, Mapping):
        weekly_state = str(weekly.get("state") or "pending")
        if weekly_state == "identity_conflict":
            signals.append(
                DistributionSignal(
                    "distribution_identity_conflict",
                    1,
                    "critical",
                    "all",
                    media_kind,
                    weekly_state,
                )
            )
        if weekly_state not in ("published_verified", "published_verified_recovered"):
            signals.append(
                DistributionSignal(
                    "distribution_weekly_non_green",
                    1,
                    "critical",
                    "all",
                    media_kind,
                    weekly_state,
                )
            )
    claim = document.get("claim")
    if isinstance(claim, Mapping):
        claimed_at = str(claim.get("claimed_at") or "")
        claim_latency = max(
            0.0,
            ((_parse(claimed_at) or current) - (_parse(enqueue_at) or current)).total_seconds(),
        )
        signals.append(
            DistributionSignal(
                "distribution_claim_latency_seconds",
                claim_latency,
                "warning" if claim_latency > 300 else "info",
                "all",
                media_kind,
                "claimed",
            )
        )
        lease_expires = _parse(str(claim.get("lease_expires_at") or ""))
        if lease_expires is not None and lease_expires <= current:
            signals.append(
                DistributionSignal(
                    "distribution_lease_loss",
                    1,
                    "critical",
                    "all",
                    media_kind,
                    "lease_expired",
                )
            )
    for provider, raw_leg in providers.items():
        if not isinstance(raw_leg, Mapping):
            continue
        provider_name = str(provider)[:32]
        state = str(raw_leg.get("result") or "pending_provider")[:64]
        age = _age_seconds(current, enqueue_at)
        if state != "externally_verified_public":
            signals.append(
                DistributionSignal(
                    "distribution_pending_age_seconds",
                    age,
                    "critical" if age > 3600 else "warning" if age > 900 else "info",
                    provider_name,
                    media_kind,
                    state,
                )
            )
        if state == "publication_unknown":
            signals.append(
                DistributionSignal(
                    "distribution_provider_unknown",
                    1,
                    "critical",
                    provider_name,
                    media_kind,
                    state,
                )
            )
        if state == "manual_handoff_required":
            signals.append(
                DistributionSignal(
                    "distribution_manual_handoff",
                    1,
                    "critical" if age > 86400 else "warning",
                    provider_name,
                    media_kind,
                    state,
                )
            )
        if state == "poisoned":
            signals.append(
                DistributionSignal(
                    "distribution_poisoned",
                    1,
                    "critical",
                    provider_name,
                    media_kind,
                    state,
                )
            )
        verification = raw_leg.get("verification")
        native_state = (
            str(verification.get("native_state") or "").lower()
            if isinstance(verification, Mapping)
            else ""
        )
        if provider_name == "youtube" and native_state in ("private", "unlisted"):
            signals.append(
                DistributionSignal(
                    "distribution_youtube_non_public",
                    1,
                    "critical" if age > 3600 else "warning",
                    provider_name,
                    media_kind,
                    native_state,
                )
            )
        if provider_name == "spotify" and native_state == "draft":
            signals.append(
                DistributionSignal(
                    "distribution_spotify_draft",
                    1,
                    "critical" if age > 86400 else "warning",
                    provider_name,
                    media_kind,
                    native_state,
                )
            )
        intent = raw_leg.get("intent")
        consumed_at = str(intent.get("consumed_at") or "") if isinstance(intent, Mapping) else ""
        if consumed_at and state != "externally_verified_public":
            lag = _age_seconds(current, consumed_at)
            signals.append(
                DistributionSignal(
                    "distribution_public_verification_lag_seconds",
                    lag,
                    "critical" if lag > 3600 else "warning" if lag > 900 else "info",
                    provider_name,
                    media_kind,
                    state,
                )
            )
    return signals


def signal_rows(documents: Iterable[Mapping[str, Any]], *, now: datetime | None = None):
    for document in documents:
        for signal in signals_for_outbox(document, now=now):
            yield {
                "event": PROVIDER_STATE_EVENT,
                "metric": signal.name,
                "value": signal.value,
                "severity": signal.severity,
                "provider": signal.provider,
                "media_kind": signal.media_kind,
                "state": signal.state,
            }
