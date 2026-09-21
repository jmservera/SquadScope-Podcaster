"""Timer entrypoint that repairs due distribution reconciliation notifications."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from podcaster.dispatch_receipts import DispatchReceiptRepository
from podcaster.distribution_outbox import DistributionOutboxRepository
from podcaster.distribution_telemetry import signal_rows
from podcaster.queue import enqueue_distribution_job
from podcaster.storage import create_storage_backend

logger = logging.getLogger(__name__)
SCHEDULER_STATE_PATH = "distribution-scheduler/state.json"


def run_once() -> int:
    storage = create_storage_backend()
    repository = DistributionOutboxRepository(storage)
    raw_state = storage.get_bytes(SCHEDULER_STATE_PATH)
    state = json.loads(raw_state.decode("utf-8")) if raw_state else {}
    due, cursor = repository.due_reconciliations_page(
        limit=100,
        scan_limit=5000,
        after_path=state.get("outbox_cursor"),
    )
    by_outbox: dict[str, list[tuple[str, str]]] = {}
    for outbox_id, provider, token in due:
        by_outbox.setdefault(outbox_id, []).append((provider, token))
    sent = 0
    for outbox_id, notifications in by_outbox.items():
        if not enqueue_distribution_job(outbox_id):
            continue
        sent += 1
        for provider, token in notifications:
            repository.mark_reconciliation_notified(
                outbox_id,
                provider=provider,
                token=token,
            )
    cleaned = repository.cleanup_orphan_artifacts(limit=100)
    storage.put_bytes(
        SCHEDULER_STATE_PATH,
        json.dumps({"outbox_cursor": cursor}, sort_keys=True).encode("utf-8"),
        "application/json; charset=utf-8",
    )
    dispatch_signals = DispatchReceiptRepository(storage).missing_arrival_signals()
    for signal in dispatch_signals:
        logger.info(
            "dispatch_signal %s",
            json.dumps(
                {
                    "event": "dispatch_arrival_state",
                    "metric": signal.name,
                    "value": signal.value,
                    "severity": signal.severity,
                    "state": signal.state,
                },
                sort_keys=True,
            ),
        )
    active_depth = 0
    missing_heartbeats = 0
    documents = (
        repository.operational_documents() if hasattr(repository, "operational_documents") else ()
    )
    for document in documents:
        if document.get("weekly_aggregation", {}).get("state") not in (
            "published_verified",
            "published_verified_recovered",
        ):
            active_depth += 1
        claim = document.get("claim")
        if isinstance(claim, dict) and claim.get("heartbeat_at"):
            heartbeat = datetime.fromisoformat(str(claim["heartbeat_at"]).replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - heartbeat).total_seconds() > 900:
                missing_heartbeats += 1
        for row in signal_rows([document]):
            logger.info("distribution_signal %s", json.dumps(row, sort_keys=True))
    for metric, value, severity, state in (
        ("distribution_scheduler_heartbeat", 1, "info", "healthy"),
        (
            "distribution_active_outbox_depth",
            active_depth,
            "warning" if active_depth else "info",
            "active" if active_depth else "empty",
        ),
        (
            "distribution_claim_heartbeat_missing",
            missing_heartbeats,
            "critical" if missing_heartbeats else "info",
            "missing" if missing_heartbeats else "healthy",
        ),
    ):
        logger.info(
            "distribution_signal %s",
            json.dumps(
                {
                    "event": "distribution_scheduler_state",
                    "metric": metric,
                    "value": value,
                    "severity": severity,
                    "state": state,
                },
                sort_keys=True,
            ),
        )
    logger.info(
        "distribution reconciliation scheduler due=%s sent=%s orphan_cleanup=%s",
        len(by_outbox),
        sent,
        cleaned,
    )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
