"""Queue worker for durable provider-distribution outbox records."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Callable, Literal

from podcaster.distribution_outbox import (
    Claim,
    DistributionOutboxError,
    DistributionOutboxRepository,
    StaleClaimError,
)
from podcaster.queue import (
    QueueBackend,
    QueueMessage,
    create_distribution_queue_backend,
    parse_distribution_outbox_id,
)
from podcaster.storage import ManagedIdentityTokenCredential, StorageBackend, create_storage_backend

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 600
DEFAULT_VISIBILITY_SECONDS = 600
DEFAULT_MAX_DEQUEUE_COUNT = 5
PROVIDER_MUTATION_ENV = "DISTRIBUTION_WORKER_PROVIDER_MUTATION_ENABLED"
PRESERVED_PROVIDER_RESULTS = frozenset(
    {
        "externally_verified_public",
        "publication_unknown",
        "identity_conflict",
        "manual_handoff_required",
        "failed_terminal",
    }
)

WorkerStatus = Literal[
    "handled",
    "transient",
    "poisoned",
    "malformed",
    "failed",
]
ProviderDispatcher = Callable[[DistributionOutboxRepository, Claim, dict[str, object]], str]


@dataclass(frozen=True)
class DistributionWorkerOutcome:
    outbox_id: str
    status: WorkerStatus
    reason: str


def provider_mutation_enabled(env: dict[str, str] | None = None) -> bool:
    """Return whether this worker may invoke mutating provider dispatch."""

    source = os.environ if env is None else env
    return str(source.get(PROVIDER_MUTATION_ENV, "")).strip().lower() == "true"


def _default_dispatcher(
    _repository: DistributionOutboxRepository,
    _claim: Claim,
    _document: dict[str, object],
) -> str:
    raise DistributionOutboxError("provider mutation dispatcher is not implemented")


def _execution_id_for_message(message: QueueMessage) -> str:
    source = f"{message.message_id}:{message.pop_receipt}".encode("utf-8")
    digest = hashlib.sha256(source).hexdigest()
    return f"queue-{digest}"


def _record_poisoned(repository: DistributionOutboxRepository, claim: Claim) -> None:
    document = repository.read(claim.outbox_id)
    providers = document.get("providers", {})
    if not isinstance(providers, dict):
        raise DistributionOutboxError("outbox providers are malformed")
    for provider, leg in providers.items():
        if not isinstance(provider, str) or not isinstance(leg, dict):
            continue
        result = str(leg.get("result") or "")
        if result in PRESERVED_PROVIDER_RESULTS:
            continue
        repository.record_verification(
            claim,
            provider=provider,
            result="poisoned",
            source="distribution_worker_poison",
            exhaustion_reason="queue_dequeue_exhausted",
        )


def _run_dispatch_with_lease_renewal(
    dispatcher: ProviderDispatcher,
    repository: DistributionOutboxRepository,
    claim: Claim,
    document: dict[str, object],
    *,
    lease_seconds: int,
) -> str:
    heartbeat_interval = max(0.01, min(60.0, lease_seconds / 3))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(dispatcher, repository, claim, document)
        while True:
            try:
                return future.result(timeout=heartbeat_interval)
            except concurrent.futures.TimeoutError:
                repository.heartbeat(claim, lease_seconds=lease_seconds)


def process_message(
    message: QueueMessage,
    *,
    storage: StorageBackend,
    queue: QueueBackend,
    repository: DistributionOutboxRepository | None = None,
    owner: str | None = None,
    execution_id: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    max_dequeue_count: int = DEFAULT_MAX_DEQUEUE_COUNT,
    mutation_enabled: bool | None = None,
    dispatcher: ProviderDispatcher | None = None,
) -> DistributionWorkerOutcome:
    """Process one distribution queue hint against the authoritative outbox."""

    try:
        outbox_id = parse_distribution_outbox_id(message.body)
    except ValueError:
        logger.error(
            "discarding malformed distribution message message_id=%s dequeue_count=%s",
            message.message_id,
            message.dequeue_count,
        )
        queue.delete_message(message)
        return DistributionWorkerOutcome("", "malformed", "malformed_message")

    repository = repository or DistributionOutboxRepository(storage)
    owner_value = owner or f"distribution-worker-{uuid.uuid4().hex}"
    execution_value = execution_id or _execution_id_for_message(message)
    is_poison = message.dequeue_count >= max_dequeue_count
    try:
        claim = repository.claim(
            outbox_id,
            owner=owner_value,
            execution_id=execution_value,
            lease_seconds=lease_seconds,
        )
    except StaleClaimError:
        logger.info(
            "distribution outbox already claimed outbox_id=%s message_id=%s dequeue_count=%s",
            outbox_id,
            message.message_id,
            message.dequeue_count,
        )
        return DistributionWorkerOutcome(outbox_id, "transient", "active_claim")
    except DistributionOutboxError as exc:
        if str(exc) != "outbox item does not exist":
            raise
        if is_poison:
            logger.error(
                "discarding exhausted orphan distribution hint outbox_id=%s dequeue_count=%s",
                outbox_id,
                message.dequeue_count,
            )
            queue.delete_message(message)
            return DistributionWorkerOutcome(outbox_id, "poisoned", "orphan_hint_exhausted")
        logger.info(
            "distribution outbox is not yet available outbox_id=%s message_id=%s dequeue_count=%s",
            outbox_id,
            message.message_id,
            message.dequeue_count,
        )
        return DistributionWorkerOutcome(outbox_id, "transient", "outbox_missing")

    try:
        repository.heartbeat(claim, lease_seconds=lease_seconds)
        if is_poison:
            _record_poisoned(repository, claim)
            repository.release(claim)
            queue.delete_message(message)
            logger.error(
                "distribution message poisoned outbox_id=%s dequeue_count=%s",
                outbox_id,
                message.dequeue_count,
            )
            return DistributionWorkerOutcome(outbox_id, "poisoned", "dequeue_exhausted")

        enabled = provider_mutation_enabled() if mutation_enabled is None else mutation_enabled
        if enabled:
            dispatch = dispatcher or _default_dispatcher
            reason = _run_dispatch_with_lease_renewal(
                dispatch,
                repository,
                claim,
                repository.read(outbox_id),
                lease_seconds=lease_seconds,
            )
        else:
            reason = "provider_mutation_disabled"
        repository.release(claim)
        queue.delete_message(message)
        logger.info(
            "distribution message handled outbox_id=%s reason=%s mutation_enabled=%s",
            outbox_id,
            reason,
            enabled,
        )
        return DistributionWorkerOutcome(outbox_id, "handled", reason)
    except Exception:
        logger.exception(
            "distribution worker failed outbox_id=%s message_id=%s dequeue_count=%s",
            outbox_id,
            message.message_id,
            message.dequeue_count,
        )
        raise


def drain(
    queue: QueueBackend,
    storage: StorageBackend,
    *,
    max_messages: int = 1,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    visibility_timeout: int = DEFAULT_VISIBILITY_SECONDS,
    max_dequeue_count: int = DEFAULT_MAX_DEQUEUE_COUNT,
) -> list[DistributionWorkerOutcome]:
    """Process distribution queue messages until empty or capped."""

    outcomes: list[DistributionWorkerOutcome] = []
    for _ in range(max_messages):
        messages = queue.receive_messages(max_messages=1, visibility_timeout=visibility_timeout)
        if not messages:
            break
        for message in messages:
            outcomes.append(
                process_message(
                    message,
                    storage=storage,
                    queue=queue,
                    lease_seconds=lease_seconds,
                    max_dequeue_count=max_dequeue_count,
                )
            )
    return outcomes


def main(argv: list[str] | None = None) -> int:
    """Entry point for the provider-distribution ACA container job."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-messages", type=int, default=1)
    parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    parser.add_argument("--visibility-timeout", type=int, default=DEFAULT_VISIBILITY_SECONDS)
    parser.add_argument("--max-dequeue-count", type=int, default=DEFAULT_MAX_DEQUEUE_COUNT)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    queue = create_distribution_queue_backend()
    if queue is None:
        logger.error(
            "distribution queue not configured; set PODCASTER_STORAGE_QUEUE_URL or "
            "AZURE_STORAGE_CONNECTION_STRING to consume distribution queue"
        )
        return 2
    storage = create_storage_backend()
    if os.environ.get("PODCASTER_STORAGE_ACCOUNT_URL"):
        try:
            ManagedIdentityTokenCredential().get_token("https://storage.azure.com/.default")
        except Exception:
            logger.exception("managed identity token startup health check failed")
            return 3
    outcomes = drain(
        queue,
        storage,
        max_messages=args.max_messages,
        lease_seconds=args.lease_seconds,
        visibility_timeout=args.visibility_timeout,
        max_dequeue_count=args.max_dequeue_count,
    )
    failed = sum(1 for outcome in outcomes if outcome.status == "failed")
    transient = sum(1 for outcome in outcomes if outcome.status == "transient")
    logger.info(
        "distribution worker finished processed=%s transient=%s failed=%s",
        len(outcomes),
        transient,
        failed,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
