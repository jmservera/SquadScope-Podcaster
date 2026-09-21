"""Timer entrypoint that repairs due distribution reconciliation notifications."""

from __future__ import annotations

import logging

from podcaster.distribution_outbox import DistributionOutboxRepository
from podcaster.queue import enqueue_distribution_job
from podcaster.storage import create_storage_backend

logger = logging.getLogger(__name__)


def run_once() -> int:
    repository = DistributionOutboxRepository(create_storage_backend())
    due = repository.due_reconciliations(limit=100)
    outbox_ids = sorted({outbox_id for outbox_id, _provider, _token in due})
    sent = sum(1 for outbox_id in outbox_ids if enqueue_distribution_job(outbox_id))
    logger.info("distribution reconciliation scheduler due=%s sent=%s", len(outbox_ids), sent)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
