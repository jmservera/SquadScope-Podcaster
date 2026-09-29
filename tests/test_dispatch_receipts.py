from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from podcaster.dispatch_receipts import (
    DispatchReceiptError,
    DispatchReceiptRepository,
    dispatch_receipt_path,
)
from podcaster.distribution_outbox import (
    DistributionOutboxRepository,
    commit_immutable_artifact,
    exact_verification_proof,
)
from podcaster.publication_state import PublicationIdentity
from podcaster.storage import LocalStorageBackend


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


def test_intent_arrival_correlation_fires_and_clears_without_sensitive_fields(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    clock = Clock()
    repository = DispatchReceiptRepository(storage, now=clock)
    document, created = repository.register_intent(
        correlation_id="weekly-2026-W39-run-7",
        week="2026-W39",
        dispatch_result="accepted",
        source="squadscope_weekly",
    )
    assert created is True
    assert document["arrival_state"] == "awaiting_arrival"
    clock.value += timedelta(minutes=11)
    [signal] = repository.missing_arrival_signals()
    assert signal.name == "dispatch_missing_azure_arrival_seconds"
    assert signal.severity == "warning"

    arrived = repository.record_arrival(
        correlation_id="weekly-2026-W39-run-7",
        week="2026-W39",
        accepted_job_id="podcast-2026-W39",
    )
    assert arrived["arrival_state"] == "arrived"
    assert repository.missing_arrival_signals() == []
    raw = storage.get_bytes(dispatch_receipt_path("weekly-2026-W39-run-7"))
    assert raw is not None
    serialized = raw.decode()
    for forbidden in ("article_url", "token", "cookie", "signed_url", "provider_item"):
        assert forbidden not in serialized


def test_duplicate_intent_is_idempotent_but_conflict_fails(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    repository = DispatchReceiptRepository(storage)
    _, created = repository.register_intent(
        correlation_id="weekly-2026-W39-run-8",
        week="2026-W39",
        dispatch_result="pending",
        source="squadscope_weekly",
    )
    _, duplicate_created = repository.register_intent(
        correlation_id="weekly-2026-W39-run-8",
        week="2026-W39",
        dispatch_result="blocked",
        source="squadscope_weekly",
    )
    assert created is True
    assert duplicate_created is False
    with pytest.raises(DispatchReceiptError, match="conflicts"):
        repository.register_intent(
            correlation_id="weekly-2026-W39-run-8",
            week="2026-W40",
            dispatch_result="pending",
            source="squadscope_weekly",
        )


def test_arrival_requires_registered_upstream_intent(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    repository = DispatchReceiptRepository(storage)
    with pytest.raises(DispatchReceiptError, match="not registered"):
        repository.record_arrival(
            correlation_id="manual-downstream-only",
            week="2026-W39",
            accepted_job_id="podcast-2026-W39",
        )


def test_w39_class_trace_localizes_dispatch_arrival_and_terminal_provider_fixture(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    dispatch = DispatchReceiptRepository(storage)
    dispatch.register_intent(
        correlation_id="weekly-2026-W39-e2e",
        week="2026-W39",
        dispatch_result="accepted",
        source="squadscope_weekly",
    )
    arrival = dispatch.record_arrival(
        correlation_id="weekly-2026-W39-e2e",
        week="2026-W39",
        accepted_job_id="podcast-2026-W39-e2e",
    )
    source = tmp_path / "video.mp4"
    source.write_bytes(b"terminal-provider-fixture")
    artifact = commit_immutable_artifact(
        storage,
        source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    outbox = DistributionOutboxRepository(storage)
    document, _ = outbox.enqueue(
        PublicationIdentity(
            accepted_job_id=arrival["accepted_job_id"],
            week="2026-W39",
            publish_run_id="39",
            article_sha256="a" * 64,
            manifest_sha256="b" * 64,
        ),
        artifact,
        provider_objectives={"youtube": "public"},
        enqueue_source="integration_fixture",
        enqueue_version="v1",
    )
    claim = outbox.claim(
        document["outbox_id"],
        owner="fixture",
        execution_id="fixture-exec",
        lease_seconds=300,
    )
    outbox.persist_intent(
        claim,
        provider="youtube",
        operation="fixture_readback",
        expected_provider_item_id="fixture-video",
    )
    outbox.record_verification(
        claim,
        provider="youtube",
        result="externally_verified_public",
        source="deterministic_external_readback",
        provider_item_id="fixture-video",
        native_state="public",
        proof=exact_verification_proof(document, provider_item_id="fixture-video"),
    )
    terminal = outbox.release(claim)
    assert terminal["publication_identity"]["accepted_job_id"] == arrival["accepted_job_id"]
    assert terminal["aggregate"]["externally_verified_public"] is True
