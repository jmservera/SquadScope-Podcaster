from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from podcaster.distribution_outbox import (
    ACTIONABLE_RESULTS,
    ARTIFACT_METADATA_PREFIX,
    OUTBOX_SCHEMA_VERSION,
    DistributionOutboxError,
    DistributionOutboxRepository,
    LeaseBudgetError,
    OutboxConflictError,
    StaleClaimError,
    UnsafeOutboxValueError,
    aggregate_exit_code,
    commit_immutable_artifact,
    exact_recovery_authorization_evidence,
    exact_verification_proof,
    four_cycle_acceptance,
    outbox_routing_enabled,
    reconciliation_message,
    weekly_state_from_attempts,
)
from podcaster.publication_state import PublicationIdentity
from podcaster.storage import LocalStorageBackend


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def identity():
    return PublicationIdentity(
        accepted_job_id="podcast-2026-W38-comparative",
        week="2026-W38",
        publish_run_id="123",
        article_sha256="a" * 64,
        manifest_sha256="b" * 64,
    )


@pytest.fixture
def setup(tmp_path, identity):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"safe-video")
    artifact = commit_immutable_artifact(
        storage,
        source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    clock = Clock()
    repository = DistributionOutboxRepository(storage, now=clock)
    document, created = repository.enqueue(
        identity,
        artifact,
        provider_objectives={"youtube": "public", "spotify": "public"},
        enqueue_source="video_runner",
        enqueue_version="v1",
    )
    return storage, repository, clock, document, created


def test_schema_round_trip_is_versioned_correlated_and_sanitized(setup):
    _storage, repository, _clock, document, created = setup
    assert created is True
    assert document["schema_version"] == OUTBOX_SCHEMA_VERSION
    assert document["publication_identity"]["accepted_job_id"] == "podcast-2026-W38-comparative"
    assert document["artifact"]["sha256"]
    assert document["providers"]["youtube"]["objective"] == "public"
    assert repository.read(document["outbox_id"]) == document
    serialized = json.dumps(document)
    for secret_key in ("authorization", "cookie", "signed_url", "body"):
        assert secret_key not in serialized.lower()


def test_enqueue_is_idempotent_but_conflicting_artifact_is_rejected(setup, identity, tmp_path):
    storage, repository, _clock, document, _created = setup
    same, created = repository.enqueue(
        identity,
        repository.read(document["outbox_id"]) and _artifact(document),
        provider_objectives={"youtube": "public", "spotify": "public"},
        enqueue_source="video_runner",
        enqueue_version="v1",
    )
    assert created is False
    assert same["outbox_id"] == document["outbox_id"]

    other_source = tmp_path / "other.mp4"
    other_source.write_bytes(b"other")
    other = commit_immutable_artifact(
        storage,
        other_source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    with pytest.raises(OutboxConflictError):
        repository.enqueue(
            identity,
            other,
            provider_objectives={"youtube": "public", "spotify": "public"},
            enqueue_source="video_runner",
            enqueue_version="v1",
        )


def test_artifact_claim_revalidation_detects_tamper(setup):
    storage, repository, _clock, document, _created = setup
    path = document["artifact"]["path"]
    storage.put_bytes(path, b"tampered", "video/mp4")
    with pytest.raises(DistributionOutboxError, match="verification"):
        repository.enqueue(
            _identity(document),
            _artifact(document),
            provider_objectives=document["provider_objectives"],
            enqueue_source="video_runner",
            enqueue_version="v1",
        )


def test_concurrent_claim_has_one_owner_and_takeover_increments_fence(setup):
    _storage, repository, clock, document, _created = setup
    claims = []
    errors = []

    def take(owner):
        try:
            claims.append(
                repository.claim(
                    document["outbox_id"],
                    owner=owner,
                    execution_id=f"exec-{owner}",
                    lease_seconds=30,
                )
            )
        except StaleClaimError as exc:
            errors.append(exc)

    first = threading.Thread(target=take, args=("one",))
    second = threading.Thread(target=take, args=("two",))
    first.start()
    second.start()
    first.join()
    second.join()
    assert len(claims) == 1
    assert len(errors) == 1
    clock.advance(31)
    takeover = repository.claim(
        document["outbox_id"], owner="takeover", execution_id="exec-2", lease_seconds=30
    )
    assert takeover.fencing_token == claims[0].fencing_token + 1
    with pytest.raises(StaleClaimError):
        repository.heartbeat(claims[0], lease_seconds=30)


def test_consumed_intent_makes_takeover_read_only_and_rejects_second_mutation(setup):
    _storage, repository, clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=120
    )
    repository.persist_intent(claim, provider="youtube", operation="upload")
    repository.consume_intent(
        claim,
        provider="youtube",
        provider_timeout_seconds=30,
        receipt_margin_seconds=10,
    )
    clock.advance(121)
    takeover = repository.claim(
        document["outbox_id"], owner="takeover", execution_id="exec-2", lease_seconds=120
    )
    assert takeover.read_only is True
    with pytest.raises(StaleClaimError, match="reconciliation-only"):
        repository.persist_intent(takeover, provider="spotify", operation="create")


def test_lease_margin_is_required_before_intent_consumption(setup):
    _storage, repository, _clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=40
    )
    repository.persist_intent(claim, provider="youtube", operation="upload")
    with pytest.raises(LeaseBudgetError):
        repository.consume_intent(
            claim,
            provider="youtube",
            provider_timeout_seconds=30,
            receipt_margin_seconds=10,
        )
    assert (
        repository.read(document["outbox_id"])["providers"]["youtube"]["intent"]["consumed_at"]
        is None
    )


def test_crash_after_mutation_before_receipt_preserves_consumed_intent(setup):
    _storage, repository, clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=120
    )
    repository.persist_intent(claim, provider="youtube", operation="upload")
    repository.consume_intent(
        claim,
        provider="youtube",
        provider_timeout_seconds=30,
        receipt_margin_seconds=10,
    )
    clock.advance(121)
    takeover = repository.claim(
        document["outbox_id"], owner="reconciler", execution_id="exec-2", lease_seconds=120
    )
    repository.record_verification(
        takeover,
        provider="youtube",
        result="publication_unknown",
        source="identity_readback",
    )
    state = repository.read(document["outbox_id"])
    assert state["providers"]["youtube"]["intent"]["consumed_at"]
    assert state["providers"]["youtube"]["receipts"] == []
    assert state["aggregate"]["result"] == "publication_unknown"


def test_receipt_and_external_readback_are_required_for_public_success(setup):
    _storage, repository, _clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=300
    )
    for provider, operation in (("youtube", "upload"), ("spotify", "create")):
        repository.persist_intent(claim, provider=provider, operation=operation)
        repository.consume_intent(
            claim,
            provider=provider,
            provider_timeout_seconds=30,
            receipt_margin_seconds=10,
        )
        repository.record_receipt(
            claim,
            provider=provider,
            transport_class="accepted",
            provider_item_id=f"{provider}-id",
            native_state="draft",
        )
        repository.record_verification(
            claim,
            provider=provider,
            result="externally_verified_public",
            source="provider_readback",
            provider_item_id=f"{provider}-id",
            native_state="public",
            proof=exact_verification_proof(document, provider_item_id=f"{provider}-id"),
        )
    state = repository.release(claim)
    assert state["state"] == "completed_public"
    assert state["aggregate"]["externally_verified_public"] is True
    assert aggregate_exit_code([state]) == 0


@pytest.mark.parametrize("result", sorted(ACTIONABLE_RESULTS))
def test_every_non_public_result_is_nonzero(setup, result):
    _storage, repository, _clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=300
    )
    repository.record_verification(
        claim,
        provider="youtube",
        result=result,
        source="provider_readback",
    )
    state = repository.read(document["outbox_id"])
    assert aggregate_exit_code([state]) == 1
    assert aggregate_exit_code([]) == 1


def test_reconciliation_token_is_deduplicated_due_and_consumed_once(setup):
    _storage, repository, clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=300
    )
    due = clock() + timedelta(seconds=30)
    token = repository.schedule_reconciliation(claim, provider="youtube", due_at=due)
    assert repository.schedule_reconciliation(claim, provider="youtube", due_at=due) == token
    assert repository.due_reconciliations() == []
    clock.advance(31)
    assert repository.due_reconciliations() == [(document["outbox_id"], "youtube", token)]
    repository.consume_schedule_token(claim, provider="youtube", token=token)
    with pytest.raises(StaleClaimError):
        repository.consume_schedule_token(claim, provider="youtube", token=token)


def test_lost_notification_is_repaired_idempotently(setup):
    _storage, repository, _clock, document, _created = setup
    sent = []
    assert repository.repair_notifications(sent.append) == 1
    assert sent == [document["outbox_id"]]
    assert repository.repair_notifications(sent.append) == 0


def test_reconciliation_scan_is_fair_beyond_one_hundred_records(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"fair-scan")
    artifact = commit_immutable_artifact(
        storage,
        source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    clock = Clock()
    repository = DistributionOutboxRepository(storage, now=clock)
    for index in range(130):
        identity = PublicationIdentity(
            accepted_job_id=f"podcast-2026-W39-{index}",
            week="2026-W39",
            publish_run_id=str(index + 1),
            article_sha256=f"{index:064x}",
            manifest_sha256=f"{index + 1000:064x}",
        )
        document, _ = repository.enqueue(
            identity,
            artifact,
            provider_objectives={"youtube": "public"},
            enqueue_source="test",
            enqueue_version="v1",
        )
        claim = repository.claim(
            document["outbox_id"],
            owner="test",
            execution_id=f"exec-{index}",
            lease_seconds=300,
        )
        repository.schedule_reconciliation(
            claim,
            provider="youtube",
            due_at=clock() - timedelta(seconds=1),
        )
        repository.release(claim)

    first, cursor = repository.due_reconciliations_page(limit=100, scan_limit=5000)
    second, _ = repository.due_reconciliations_page(
        limit=100,
        scan_limit=5000,
        after_path=cursor,
    )
    assert len(first) == 100
    assert {item[0] for item in first} != {item[0] for item in second}
    assert len({item[0] for item in first + second}) == 130


def test_reconciliation_scan_reaches_due_record_beyond_five_thousand():
    now = datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc)
    paths = [f"distribution-outbox/{index:064x}.json" for index in range(5001)]
    documents = {}
    for index, path in enumerate(paths):
        due = index == 5000
        documents[path] = json.dumps(
            {
                "outbox_id": f"{index:064x}",
                "providers": {
                    "youtube": {
                        "next_reconcile_at": ("2026-09-21T20:59:00Z" if due else None),
                        "active_schedule_token": "a" * 64 if due else None,
                        "schedule_notification_token": None,
                        "schedule_notification_sent_at": None,
                    }
                },
            }
        ).encode()

    class PagedStorage:
        def get_bytes(self, path):
            return documents.get(path)

        def list_blobs_page(self, prefix, *, limit, continuation=None):
            start = int(continuation or 0)
            page = paths[start : start + limit]
            next_cursor = str(start + len(page)) if start + len(page) < len(paths) else None
            return page, next_cursor

    repository = DistributionOutboxRepository(PagedStorage(), now=lambda: now)
    first, cursor = repository.due_reconciliations_page(limit=100, scan_limit=5000)
    assert first == []
    assert cursor == "5000"
    second, cursor = repository.due_reconciliations_page(
        limit=100,
        scan_limit=5000,
        after_path=cursor,
    )
    assert second == [(f"{5000:064x}", "youtube", "a" * 64)]
    assert cursor is None


def test_reconciliation_notification_is_deduplicated_until_stale(setup):
    _storage, repository, clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=300
    )
    token = repository.schedule_reconciliation(
        claim,
        provider="youtube",
        due_at=clock() - timedelta(seconds=1),
    )
    repository.release(claim)
    assert repository.due_reconciliations()
    repository.mark_reconciliation_notified(
        document["outbox_id"],
        provider="youtube",
        token=token,
    )
    assert repository.due_reconciliations() == []
    clock.advance(901)
    assert repository.due_reconciliations()


def test_reconciliation_notification_reservation_is_single_winner_and_fenced(setup):
    _storage, repository, clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=300
    )
    token = repository.schedule_reconciliation(
        claim,
        provider="youtube",
        due_at=clock() - timedelta(seconds=1),
    )
    repository.release(claim)
    winners = []
    errors = []
    barrier = threading.Barrier(2)

    def reserve(owner):
        barrier.wait()
        try:
            winners.append(
                repository.reserve_reconciliation_notification(
                    document["outbox_id"],
                    provider="youtube",
                    token=token,
                    owner=owner,
                    lease_seconds=30,
                )
            )
        except StaleClaimError as exc:
            errors.append(str(exc))

    workers = [threading.Thread(target=reserve, args=(f"scheduler-{index}",)) for index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(winners) == 1
    assert len(errors) == 1

    first_reservation = winners[0]
    clock.advance(31)
    replacement = repository.reserve_reconciliation_notification(
        document["outbox_id"],
        provider="youtube",
        token=token,
        owner="scheduler-recovery",
        lease_seconds=30,
    )
    with pytest.raises(StaleClaimError):
        repository.begin_reconciliation_enqueue(
            document["outbox_id"],
            provider="youtube",
            token=token,
            reservation_id=first_reservation,
        )
    repository.begin_reconciliation_enqueue(
        document["outbox_id"],
        provider="youtube",
        token=token,
        reservation_id=replacement,
    )
    assert repository.due_reconciliations() == []
    clock.advance(31)
    assert repository.due_reconciliations() == [(document["outbox_id"], "youtube", token)]
    recovered = repository.reserve_reconciliation_notification(
        document["outbox_id"],
        provider="youtube",
        token=token,
        owner="scheduler-final",
        lease_seconds=30,
    )
    with pytest.raises(StaleClaimError):
        repository.abort_reconciliation_enqueue(
            document["outbox_id"],
            provider="youtube",
            token=token,
            reservation_id=replacement,
        )
    with pytest.raises(StaleClaimError):
        repository.complete_reconciliation_notification(
            document["outbox_id"],
            provider="youtube",
            token=token,
            reservation_id=replacement,
        )
    repository.begin_reconciliation_enqueue(
        document["outbox_id"],
        provider="youtube",
        token=token,
        reservation_id=recovered,
    )
    repository.complete_reconciliation_notification(
        document["outbox_id"],
        provider="youtube",
        token=token,
        reservation_id=recovered,
    )
    assert repository.due_reconciliations() == []


def test_orphan_artifact_cleanup_is_retained_bounded_and_reference_safe(setup):
    storage, repository, clock, document, _created = setup
    referenced_digest = document["artifact"]["sha256"]
    source = storage.root.parent / "orphan.mp4"
    source.write_bytes(b"orphan")
    orphan = commit_immutable_artifact(
        storage,
        source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    for digest in (referenced_digest, orphan.sha256):
        metadata_path = f"{ARTIFACT_METADATA_PREFIX}/{digest}.json"

        def age(raw):
            value = json.loads(raw.decode())
            value["created_at"] = "2026-09-19T00:00:00Z"
            return json.dumps(value).encode()

        storage.update_bytes(metadata_path, "application/json", age)
    clock.advance(2 * 86400)
    assert repository.cleanup_orphan_artifacts(retention=timedelta(hours=24), limit=1) <= 1
    repository.cleanup_orphan_artifacts(retention=timedelta(hours=24), limit=100)
    assert storage.blob_exists(document["artifact"]["path"])
    assert not storage.blob_exists(orphan.path)


def test_message_and_durable_fields_reject_secrets_and_urls(setup):
    _storage, repository, _clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=300
    )
    repository.persist_intent(claim, provider="youtube", operation="upload")
    repository.consume_intent(
        claim,
        provider="youtube",
        provider_timeout_seconds=30,
        receipt_margin_seconds=10,
    )
    with pytest.raises(UnsafeOutboxValueError):
        repository.record_receipt(
            claim,
            provider="youtube",
            transport_class="https://secret.example",
        )
    message = reconciliation_message(document["outbox_id"], "youtube", "a" * 64)
    assert "youtube" in message
    assert "secret" not in message


def test_routing_flag_is_disabled_by_default():
    assert outbox_routing_enabled({}) is False
    assert outbox_routing_enabled({"DISTRIBUTION_OUTBOX_ENABLED": "true"}) is True


def test_historical_ambiguous_evidence_backfills_reconciliation_only(setup, identity):
    _storage, repository, clock, document, _created = setup
    state = repository.backfill_from_publication_evidence(
        identity,
        _artifact(document),
        provider_objectives=document["provider_objectives"],
        evidence_records=[
            {
                "platform": "youtube",
                "outcome": "publication_unknown",
                "status": "unknown",
                "at": "2026-09-20T00:00:00Z",
                "provider_artifact_id": "video-1",
            }
        ],
    )
    assert state["providers"]["youtube"]["result"] == "publication_unknown"
    assert state["providers"]["youtube"]["intent"]["consumed_at"]
    clock.advance(301)
    claim = repository.claim(
        document["outbox_id"], owner="takeover", execution_id="reconcile", lease_seconds=300
    )
    assert claim.read_only is True


def test_failed_attempt_remains_immutable_after_authorized_verified_recovery(setup):
    _storage, repository, _clock, document, _created = setup
    first = repository.claim(
        document["outbox_id"], owner="first", execution_id="exec-first", lease_seconds=300
    )
    repository.record_verification(
        first,
        provider="youtube",
        result="failed_terminal",
        source="youtube_processing_readback",
        provider_item_id="youtube-failed",
        native_state="failed",
    )
    repository.record_verification(
        first,
        provider="spotify",
        result="failed_terminal",
        source="spotify_episode_readback",
        provider_item_id="spotify-failed",
        native_state="failed",
    )
    failed = repository.release(first)
    failed_attempt = failed["attempts"][0]
    assert failed_attempt["terminal_outcome"] == "failed_terminal"

    recovery_evidence = exact_recovery_authorization_evidence(
        repository.read(document["outbox_id"]),
        predecessor_attempt_id=failed_attempt["attempt_id"],
        expected_provider_item_ids={
            "youtube": "youtube-recovered",
            "spotify": "spotify-recovered",
        },
    )
    repository.authorize_recovery(
        document["outbox_id"],
        predecessor_attempt_id=failed_attempt["attempt_id"],
        source="operator",
        reason="no_mutation_proven",
        evidence=recovery_evidence,
    )
    recovered_claim = repository.claim(
        document["outbox_id"], owner="recovery", execution_id="exec-recovery", lease_seconds=300
    )
    for provider in ("youtube", "spotify"):
        repository.persist_intent(
            recovered_claim,
            provider=provider,
            operation="recovery_readback",
            expected_provider_item_id=f"{provider}-recovered",
        )
        repository.record_verification(
            recovered_claim,
            provider=provider,
            result="externally_verified_public",
            source=f"{provider}_readback",
            provider_item_id=f"{provider}-recovered",
            native_state="public" if provider == "youtube" else "published",
            proof=exact_verification_proof(
                repository.read(document["outbox_id"]),
                provider_item_id=f"{provider}-recovered",
            ),
        )
    recovered = repository.release(recovered_claim)
    assert recovered["attempts"][0] == failed_attempt
    assert recovered["weekly_aggregation"]["state"] == "published_verified_recovered"
    assert recovered["weekly_aggregation"]["failed_attempt_references"] == [
        failed_attempt["attempt_id"]
    ]
    assert recovered["weekly_aggregation"]["winning_attempt_id"] == recovered_claim.attempt_id
    assert (
        weekly_state_from_attempts(recovered["attempts"], weekly_record=recovered)
        == "published_verified_recovered"
    )
    assert aggregate_exit_code([recovered]) == 0


@pytest.mark.parametrize(
    "proof,source,native_state",
    [
        ({"manifest_sha256": "c" * 64}, "youtube_readback", "public"),
        ({"artifact_sha256": "d" * 64}, "youtube_readback", "public"),
        ({"canonical_artifact_selected": False}, "youtube_readback", "public"),
        ({"duplicate_ambiguity_resolved": False}, "youtube_readback", "public"),
        ({}, "youtube_upload", "public"),
    ],
)
def test_missing_or_conflicting_green_proof_is_identity_conflict(
    setup, proof, source, native_state
):
    _storage, repository, _clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=300
    )
    verification = repository.record_verification(
        claim,
        provider="youtube",
        result="externally_verified_public",
        source=source,
        provider_item_id="youtube-id",
        native_state=native_state,
        proof=proof,
    )
    assert verification["result"] == "identity_conflict"
    state = repository.read(document["outbox_id"])
    assert state["weekly_aggregation"]["state"] == "identity_conflict"
    assert aggregate_exit_code([state]) == 1


def test_unknown_mutation_cannot_authorize_blind_retry(setup):
    _storage, repository, _clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="exec-1", lease_seconds=300
    )
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            claim,
            provider=provider,
            result="publication_unknown",
            source=f"{provider}_identity_unprovable",
        )
    state = repository.release(claim)
    attempt_id = state["attempts"][0]["attempt_id"]
    with pytest.raises(DistributionOutboxError, match="cannot authorize retry"):
        repository.authorize_recovery(
            document["outbox_id"],
            predecessor_attempt_id=attempt_id,
            source="automatic",
            reason="blind_retry",
            evidence={},
        )


def _failed_attempt(repository, outbox_id, *, owner, execution_id):
    claim = repository.claim(outbox_id, owner=owner, execution_id=execution_id, lease_seconds=300)
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            claim,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_readback",
            provider_item_id=f"{provider}-{execution_id}-failed",
            native_state="failed",
        )
    return repository.release(claim)["attempts"][-1]


def _authorize_recovery(repository, outbox_id, predecessor, *, suffix):
    evidence = exact_recovery_authorization_evidence(
        repository.read(outbox_id),
        predecessor_attempt_id=predecessor["attempt_id"],
        expected_provider_item_ids={
            "youtube": f"youtube-{suffix}",
            "spotify": f"spotify-{suffix}",
        },
    )
    repository.authorize_recovery(
        outbox_id,
        predecessor_attempt_id=predecessor["attempt_id"],
        source="operator",
        reason="no_mutation_proven",
        evidence=evidence,
    )
    return evidence


def _terminate_current_attempt_unknown(
    repository,
    outbox_id,
    *,
    suffix,
    receipt_providers=("youtube", "spotify"),
):
    claim = repository.claim(
        outbox_id,
        owner=f"unknown-{suffix}",
        execution_id=f"unknown-{suffix}",
        lease_seconds=300,
    )
    for provider in ("youtube", "spotify"):
        repository.persist_intent(
            claim,
            provider=provider,
            operation="recovery_mutation",
            expected_provider_item_id=f"{provider}-{suffix}",
        )
        repository.consume_intent(
            claim,
            provider=provider,
            provider_timeout_seconds=30,
            receipt_margin_seconds=30,
        )
        if provider in receipt_providers:
            repository.record_receipt(
                claim,
                provider=provider,
                transport_class="accepted",
                provider_item_id=f"{provider}-{suffix}",
                native_state="created",
            )
        repository.record_verification(
            claim,
            provider=provider,
            result="publication_unknown",
            source=f"{provider}_identity_unprovable",
        )
    return repository.release(claim)["attempts"][-1]


def test_later_provider_unknown_blocks_recovery_from_older_failed_predecessor(setup):
    _storage, repository, _clock, document, _created = setup
    failed = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="failed",
        execution_id="failed",
    )
    stale_evidence = _authorize_recovery(
        repository,
        document["outbox_id"],
        failed,
        suffix="unknown",
    )
    _terminate_current_attempt_unknown(
        repository,
        document["outbox_id"],
        suffix="unknown",
    )

    with pytest.raises(DistributionOutboxError, match="latest attempt"):
        repository.authorize_recovery(
            document["outbox_id"],
            predecessor_attempt_id=failed["attempt_id"],
            source="operator",
            reason="reuse_stale_predecessor",
            evidence=stale_evidence,
        )

    state = repository.read(document["outbox_id"])
    assert len(state["attempts"]) == 2
    reconciliation = repository.claim(
        document["outbox_id"],
        owner="reconciliation",
        execution_id="reconciliation",
        lease_seconds=300,
    )
    assert reconciliation.read_only is True
    repository.release(reconciliation)


def test_exact_terminal_readback_of_latest_unknown_allows_new_safe_recovery(setup):
    _storage, repository, _clock, document, _created = setup
    failed = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="failed",
        execution_id="failed",
    )
    _authorize_recovery(repository, document["outbox_id"], failed, suffix="unknown")
    unknown = _terminate_current_attempt_unknown(
        repository,
        document["outbox_id"],
        suffix="unknown",
    )

    reconciliation = repository.claim(
        document["outbox_id"],
        owner="authoritative-readback",
        execution_id="authoritative-readback",
        lease_seconds=300,
    )
    assert reconciliation.read_only is True
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            reconciliation,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_terminal_readback",
            provider_item_id=f"{provider}-unknown",
            native_state="failed",
        )
    repository.release(reconciliation)

    _authorize_recovery(
        repository,
        document["outbox_id"],
        unknown,
        suffix="resolved",
    )
    safe_continuation = repository.claim(
        document["outbox_id"],
        owner="safe-continuation",
        execution_id="safe-continuation",
        lease_seconds=300,
    )
    assert safe_continuation.read_only is False
    state = repository.read(document["outbox_id"])
    assert len(state["attempts"]) == 3
    assert state["attempts"][1]["terminal_outcome"] == "provider_unknown"
    assert state["attempts"][2]["predecessor_attempt_id"] == unknown["attempt_id"]


def test_latest_unknown_readback_for_different_provider_item_fails_closed(setup):
    _storage, repository, _clock, document, _created = setup
    failed = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="failed",
        execution_id="failed",
    )
    _authorize_recovery(repository, document["outbox_id"], failed, suffix="unknown")
    unknown = _terminate_current_attempt_unknown(
        repository,
        document["outbox_id"],
        suffix="unknown",
    )

    reconciliation = repository.claim(
        document["outbox_id"],
        owner="different-item-readback",
        execution_id="different-item-readback",
        lease_seconds=300,
    )
    assert reconciliation.read_only is True
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            reconciliation,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_terminal_readback",
            provider_item_id=f"{provider}-DIFFERENT-ITEM",
            native_state="failed",
        )
    repository.release(reconciliation)

    with pytest.raises(DistributionOutboxError, match="cannot authorize retry"):
        exact_recovery_authorization_evidence(
            repository.read(document["outbox_id"]),
            predecessor_attempt_id=unknown["attempt_id"],
            expected_provider_item_ids={
                "youtube": "youtube-resolved",
                "spotify": "spotify-resolved",
            },
        )
    state = repository.read(document["outbox_id"])
    assert len(state["attempts"]) == 2
    takeover = repository.claim(
        document["outbox_id"],
        owner="still-read-only",
        execution_id="still-read-only",
        lease_seconds=300,
    )
    assert takeover.read_only is True


def test_latest_unknown_without_provider_receipts_fails_closed(setup):
    _storage, repository, _clock, document, _created = setup
    failed = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="failed",
        execution_id="failed",
    )
    _authorize_recovery(repository, document["outbox_id"], failed, suffix="unknown")
    unknown = _terminate_current_attempt_unknown(
        repository,
        document["outbox_id"],
        suffix="unknown",
        receipt_providers=(),
    )

    reconciliation = repository.claim(
        document["outbox_id"],
        owner="zero-receipt-readback",
        execution_id="zero-receipt-readback",
        lease_seconds=300,
    )
    assert reconciliation.read_only is True
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            reconciliation,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_terminal_readback",
            provider_item_id=f"{provider}-unknown",
            native_state="failed",
        )
    repository.release(reconciliation)

    with pytest.raises(DistributionOutboxError, match="cannot authorize retry"):
        exact_recovery_authorization_evidence(
            repository.read(document["outbox_id"]),
            predecessor_attempt_id=unknown["attempt_id"],
            expected_provider_item_ids={
                "youtube": "youtube-resolved",
                "spotify": "spotify-resolved",
            },
        )
    assert len(repository.read(document["outbox_id"])["attempts"]) == 2
    takeover = repository.claim(
        document["outbox_id"],
        owner="zero-receipt-still-read-only",
        execution_id="zero-receipt-still-read-only",
        lease_seconds=300,
    )
    assert takeover.read_only is True


def test_latest_unknown_with_duplicate_identical_receipt_fails_closed(setup):
    _storage, repository, _clock, document, _created = setup
    failed = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="failed",
        execution_id="failed",
    )
    _authorize_recovery(repository, document["outbox_id"], failed, suffix="unknown")
    unknown = _terminate_current_attempt_unknown(
        repository,
        document["outbox_id"],
        suffix="unknown",
    )

    def _duplicate_receipt(state):
        receipt = dict(state["attempts"][-1]["provider_evidence"]["youtube"]["receipts"][0])
        state["attempts"][-1]["provider_evidence"]["youtube"]["receipts"].append(receipt)

    repository._update(document["outbox_id"], _duplicate_receipt)
    reconciliation = repository.claim(
        document["outbox_id"],
        owner="duplicate-receipt-readback",
        execution_id="duplicate-receipt-readback",
        lease_seconds=300,
    )
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            reconciliation,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_terminal_readback",
            provider_item_id=f"{provider}-unknown",
            native_state="failed",
        )
    repository.release(reconciliation)

    with pytest.raises(DistributionOutboxError, match="cannot authorize retry"):
        exact_recovery_authorization_evidence(
            repository.read(document["outbox_id"]),
            predecessor_attempt_id=unknown["attempt_id"],
            expected_provider_item_ids={
                "youtube": "youtube-resolved",
                "spotify": "spotify-resolved",
            },
        )
    assert len(repository.read(document["outbox_id"])["attempts"]) == 2


def test_latest_unknown_with_partial_provider_receipts_fails_closed(setup):
    _storage, repository, _clock, document, _created = setup
    failed = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="failed",
        execution_id="failed",
    )
    _authorize_recovery(repository, document["outbox_id"], failed, suffix="unknown")
    unknown = _terminate_current_attempt_unknown(
        repository,
        document["outbox_id"],
        suffix="unknown",
        receipt_providers=("youtube",),
    )

    reconciliation = repository.claim(
        document["outbox_id"],
        owner="partial-receipt-readback",
        execution_id="partial-receipt-readback",
        lease_seconds=300,
    )
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            reconciliation,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_terminal_readback",
            provider_item_id=f"{provider}-unknown",
            native_state="failed",
        )
    repository.release(reconciliation)

    with pytest.raises(DistributionOutboxError, match="cannot authorize retry"):
        exact_recovery_authorization_evidence(
            repository.read(document["outbox_id"]),
            predecessor_attempt_id=unknown["attempt_id"],
            expected_provider_item_ids={
                "youtube": "youtube-resolved",
                "spotify": "spotify-resolved",
            },
        )
    assert len(repository.read(document["outbox_id"])["attempts"]) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence["youtube"]["intent"].__setitem__(
            "expected_provider_item_id", None
        ),
        lambda evidence: evidence["youtube"]["intent"].__setitem__("provider", "spotify"),
        lambda evidence: evidence["youtube"]["receipts"].append(
            {
                "intent_id": "stale-intent",
                "provider_item_id": "youtube-unknown",
                "ambiguous": False,
            }
        ),
        lambda evidence: evidence["youtube"]["receipts"].append(
            {
                "intent_id": evidence["youtube"]["intent"]["intent_id"],
                "provider_item_id": "youtube-conflict",
                "ambiguous": False,
            }
        ),
        lambda evidence: evidence["youtube"]["receipts"][0].__setitem__(
            "transport_class", "failed"
        ),
        lambda evidence: evidence["youtube"]["receipts"][0].__setitem__("receipt_id", None),
        lambda evidence: evidence["youtube"]["receipts"][0].__setitem__("at", "not-a-timestamp"),
        lambda evidence: evidence["youtube"]["receipts"][0].__setitem__(
            "fencing_token",
            evidence["youtube"]["intent"]["consumed_fence"] + 1,
        ),
    ],
    ids=[
        "missing-item-identity",
        "provider-kind-mismatch",
        "stale-receipt",
        "conflicting-item-candidate",
        "wrong-transport-kind",
        "malformed-receipt",
        "malformed-receipt-timestamp",
        "wrong-operation-fence",
    ],
)
def test_latest_unknown_provider_identity_candidates_fail_closed(setup, mutation):
    _storage, repository, _clock, document, _created = setup
    failed = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="failed",
        execution_id="failed",
    )
    _authorize_recovery(repository, document["outbox_id"], failed, suffix="unknown")
    unknown = _terminate_current_attempt_unknown(
        repository,
        document["outbox_id"],
        suffix="unknown",
    )

    def _mutate(state):
        mutation(state["attempts"][-1]["provider_evidence"])

    repository._update(document["outbox_id"], _mutate)
    reconciliation = repository.claim(
        document["outbox_id"],
        owner="identity-readback",
        execution_id="identity-readback",
        lease_seconds=300,
    )
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            reconciliation,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_terminal_readback",
            provider_item_id=f"{provider}-unknown",
            native_state="failed",
        )
    repository.release(reconciliation)

    with pytest.raises(DistributionOutboxError, match="cannot authorize retry"):
        exact_recovery_authorization_evidence(
            repository.read(document["outbox_id"]),
            predecessor_attempt_id=unknown["attempt_id"],
            expected_provider_item_ids={
                "youtube": "youtube-resolved",
                "spotify": "spotify-resolved",
            },
        )
    assert len(repository.read(document["outbox_id"])["attempts"]) == 2


def test_duplicate_latest_unknown_provider_readback_fails_closed(setup):
    _storage, repository, _clock, document, _created = setup
    failed = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="failed",
        execution_id="failed",
    )
    _authorize_recovery(repository, document["outbox_id"], failed, suffix="unknown")
    unknown = _terminate_current_attempt_unknown(
        repository,
        document["outbox_id"],
        suffix="unknown",
    )

    reconciliation = repository.claim(
        document["outbox_id"],
        owner="duplicate-readback",
        execution_id="duplicate-readback",
        lease_seconds=300,
    )
    for provider in ("youtube", "youtube", "spotify"):
        repository.record_verification(
            reconciliation,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_terminal_readback",
            provider_item_id=f"{provider}-unknown",
            native_state="failed",
        )
    repository.release(reconciliation)

    with pytest.raises(DistributionOutboxError, match="cannot authorize retry"):
        exact_recovery_authorization_evidence(
            repository.read(document["outbox_id"]),
            predecessor_attempt_id=unknown["attempt_id"],
            expected_provider_item_ids={
                "youtube": "youtube-resolved",
                "spotify": "spotify-resolved",
            },
        )
    assert len(repository.read(document["outbox_id"])["attempts"]) == 2


def test_stale_authorization_referencing_non_latest_predecessor_is_rejected(setup):
    _storage, repository, _clock, document, _created = setup
    first = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="first",
        execution_id="first",
    )
    stale_evidence = _authorize_recovery(
        repository,
        document["outbox_id"],
        first,
        suffix="second",
    )
    _failed_attempt(
        repository,
        document["outbox_id"],
        owner="second",
        execution_id="second",
    )

    with pytest.raises(DistributionOutboxError, match="latest attempt"):
        repository.authorize_recovery(
            document["outbox_id"],
            predecessor_attempt_id=first["attempt_id"],
            source="operator",
            reason="stale_authorization",
            evidence=stale_evidence,
        )


@pytest.mark.parametrize(
    "mutate_history",
    [
        lambda attempt_ids: attempt_ids[:-1],
        lambda attempt_ids: list(reversed(attempt_ids)),
    ],
)
def test_recovery_authorization_rejects_omitted_or_reordered_attempt_history(setup, mutate_history):
    _storage, repository, _clock, document, _created = setup
    first = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="first",
        execution_id="first",
    )
    _authorize_recovery(repository, document["outbox_id"], first, suffix="second")
    second = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="second",
        execution_id="second",
    )
    evidence = exact_recovery_authorization_evidence(
        repository.read(document["outbox_id"]),
        predecessor_attempt_id=second["attempt_id"],
        expected_provider_item_ids={
            "youtube": "youtube-third",
            "spotify": "spotify-third",
        },
    )
    evidence["prior_attempt_ids"] = mutate_history(evidence["prior_attempt_ids"])

    with pytest.raises(
        DistributionOutboxError,
        match="identity evidence is invalid",
    ):
        repository.authorize_recovery(
            document["outbox_id"],
            predecessor_attempt_id=second["attempt_id"],
            source="operator",
            reason="tampered_history",
            evidence=evidence,
        )


def test_weekly_w38_recovery_candidate_and_w39_missed_fixture():
    attempts = [
        {"attempt_id": "failed", "terminal_outcome": "failed_terminal"},
        {
            "attempt_id": "recovered",
            "terminal_outcome": "published_verified",
            "proof_complete": True,
            "recovery_evidence_reference": "opaque-label-only-token",
        },
    ]
    assert weekly_state_from_attempts(attempts) == "identity_conflict"
    assert weekly_state_from_attempts([], missed_not_dispatched=True) == "missed_not_dispatched"


def test_weekly_attempt_precedence_is_deterministic():
    assert (
        weekly_state_from_attempts(
            [
                {"terminal_outcome": "manual_action_required"},
                {"terminal_outcome": "provider_unknown"},
                {"terminal_outcome": "identity_conflict"},
                {"terminal_outcome": "published_verified"},
            ]
        )
        == "identity_conflict"
    )
    assert (
        weekly_state_from_attempts(
            [
                {"terminal_outcome": "partial"},
                {"terminal_outcome": "manual_action_required"},
            ]
        )
        == "manual_action_required"
    )


def test_four_cycle_acceptance_requires_complete_authoritative_envelopes():
    green = [_authoritative_cycle(f"2026-W{week}") for week in range(35, 39)]
    assert four_cycle_acceptance(green) is True
    green[2]["providers"]["youtube"]["verification"]["proof"]["green"] = False
    assert four_cycle_acceptance(green) is False
    assert four_cycle_acceptance(green[:3]) is False
    assert (
        four_cycle_acceptance(
            [{"weekly_aggregation": {"state": "published_verified"}} for _ in range(4)]
        )
        is False
    )
    tampered = [_authoritative_cycle(f"2026-W{week}") for week in range(35, 39)]
    tampered[2]["publication_identity"]["accepted_job_id"] = "tampered-job"
    assert four_cycle_acceptance(tampered) is False
    recovered_without_authorization = [
        _authoritative_cycle(f"2026-W{week}") for week in range(35, 39)
    ]
    recovered_without_authorization[2]["weekly_aggregation"]["state"] = (
        "published_verified_recovered"
    )
    assert four_cycle_acceptance(recovered_without_authorization) is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cycle: cycle["publication_identity"].__setitem__("week", "2026-W01"),
        lambda cycle: cycle["publication_identity"].__setitem__("accepted_job_id", "tampered-job"),
        lambda cycle: cycle["publication_identity"].__setitem__("manifest_sha256", "f" * 64),
        lambda cycle: cycle["artifact"].__setitem__("sha256", "f" * 64),
        lambda cycle: cycle["providers"]["youtube"]["verification"].__setitem__(
            "provider_item_id", "other-video"
        ),
        lambda cycle: cycle["providers"]["youtube"]["verification"].__setitem__(
            "source", "operator_label"
        ),
        lambda cycle: cycle["providers"]["youtube"]["verification"]["proof"][
            "evidence"
        ].__setitem__("duplicate_ambiguity_resolved", False),
    ],
)
def test_four_cycle_acceptance_rejects_identity_tampering(mutate):
    cycles = [_authoritative_cycle(f"2026-W{week}") for week in range(35, 39)]
    mutate(cycles[1])
    assert four_cycle_acceptance(cycles) is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("week", "2026-W39"),
        ("accepted_job_id", "wrong-job"),
        ("manifest_sha256", "f" * 64),
        ("publication_digest", "e" * 64),
        ("artifact_sha256", "f" * 64),
        ("predecessor_attempt_id", "wrong-attempt"),
        ("safety_readback_source", "operator_label"),
        ("terminal_authoritative_readback", False),
    ],
)
def test_recovery_authorization_rejects_mismatched_structured_evidence(setup, field, value):
    _storage, repository, _clock, document, _created = setup
    first = repository.claim(
        document["outbox_id"], owner="first", execution_id="exec-first", lease_seconds=300
    )
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            first,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_readback",
            provider_item_id=f"{provider}-failed",
            native_state="failed",
        )
    failed = repository.release(first)
    predecessor_id = failed["attempts"][0]["attempt_id"]
    evidence = exact_recovery_authorization_evidence(
        failed,
        predecessor_attempt_id=predecessor_id,
        expected_provider_item_ids={
            "youtube": "youtube-recovered",
            "spotify": "spotify-recovered",
        },
    )
    if field in evidence:
        evidence[field] = value
    elif field == "terminal_authoritative_readback":
        evidence["providers"]["youtube"]["mutation_safe"] = value
    elif field == "safety_readback_source":
        evidence["providers"]["youtube"]["safety_readback_source"] = value
    else:
        evidence["publication_identity"][field] = value
    with pytest.raises(DistributionOutboxError):
        repository.authorize_recovery(
            document["outbox_id"],
            predecessor_attempt_id=predecessor_id,
            source="operator",
            reason="no_mutation_proven",
            evidence=evidence,
        )


def test_recovery_intent_rejects_provider_identity_outside_authorization(setup):
    _storage, repository, _clock, document, _created = setup
    first = repository.claim(
        document["outbox_id"], owner="first", execution_id="exec-first", lease_seconds=300
    )
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            first,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_readback",
            provider_item_id=f"{provider}-failed",
            native_state="failed",
        )
    failed = repository.release(first)
    predecessor_id = failed["attempts"][0]["attempt_id"]
    evidence = exact_recovery_authorization_evidence(
        failed,
        predecessor_attempt_id=predecessor_id,
        expected_provider_item_ids={
            "youtube": "youtube-recovered",
            "spotify": "spotify-recovered",
        },
    )
    repository.authorize_recovery(
        document["outbox_id"],
        predecessor_attempt_id=predecessor_id,
        source="operator",
        reason="no_mutation_proven",
        evidence=evidence,
    )
    claim = repository.claim(
        document["outbox_id"], owner="recovery", execution_id="exec-recovery", lease_seconds=300
    )
    with pytest.raises(
        DistributionOutboxError,
        match="recovery intent does not match its authorization",
    ):
        repository.persist_intent(
            claim,
            provider="youtube",
            operation="recovery_readback",
            expected_provider_item_id="wrong-provider-item",
        )


def test_recovery_authorization_rejects_opaque_evidence(setup):
    _storage, repository, _clock, document, _created = setup
    claim = repository.claim(
        document["outbox_id"], owner="first", execution_id="exec-first", lease_seconds=300
    )
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            claim,
            provider=provider,
            result="failed_terminal",
            source=f"{provider}_readback",
            provider_item_id=f"{provider}-failed",
            native_state="failed",
        )
    failed = repository.release(claim)
    with pytest.raises(DistributionOutboxError):
        repository.authorize_recovery(
            document["outbox_id"],
            predecessor_attempt_id=failed["attempts"][0]["attempt_id"],
            source="operator",
            reason="no_mutation_proven",
            evidence={"reference": "opaque-token"},
        )


def test_cleanup_pages_complete_references_before_deleting(setup):
    storage, repository, clock, document, _created = setup
    for index in range(5):
        source = storage.root.parent / f"artifact-{index}.mp4"
        source.write_bytes(f"artifact-{index}".encode())
        artifact = commit_immutable_artifact(
            storage,
            source,
            media_kind="video",
            content_type="video/mp4",
            suffix=".mp4",
        )
        metadata_path = f"{ARTIFACT_METADATA_PREFIX}/{artifact.sha256}.json"

        def age(raw):
            value = json.loads(raw.decode())
            value["created_at"] = "2026-09-19T00:00:00Z"
            return json.dumps(value).encode()

        storage.update_bytes(metadata_path, "application/json", age)
    clock.advance(2 * 86400)
    for _ in range(8):
        repository.cleanup_orphan_artifacts(
            retention=timedelta(hours=24),
            limit=2,
            outbox_scan_limit=1,
        )
    assert storage.blob_exists(document["artifact"]["path"])


def test_cleanup_reference_created_before_delete_prevents_removal(setup, monkeypatch, tmp_path):
    storage, repository, clock, _document, _created = setup
    source = tmp_path / "concurrent-orphan.mp4"
    source.write_bytes(b"concurrent-orphan")
    artifact = commit_immutable_artifact(
        storage,
        source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    metadata_path = f"{ARTIFACT_METADATA_PREFIX}/{artifact.sha256}.json"

    def age(raw):
        value = json.loads(raw.decode())
        value["created_at"] = "2026-09-19T00:00:00Z"
        return json.dumps(value).encode()

    storage.update_bytes(metadata_path, "application/json", age)
    clock.advance(2 * 86400)
    original_update = storage.update_bytes
    injected = {"done": False}

    def update_with_reference(path, content_type, update):
        if (
            path.endswith(f"{artifact.sha256}.json")
            and "distribution-artifact-references/" in path
            and not injected["done"]
        ):
            target = storage.root / path
            value = json.loads(target.read_text())
            value["outbox_ids"] = ["f" * 64]
            target.write_text(json.dumps(value))
            injected["done"] = True
        return original_update(path, content_type, update)

    monkeypatch.setattr(storage, "update_bytes", update_with_reference)
    assert repository.cleanup_orphan_artifacts(retention=timedelta(hours=24), limit=100) == 0
    assert storage.blob_exists(artifact.path)


def test_cleanup_backfills_legacy_reference_before_deletion(setup):
    storage, repository, clock, document, _created = setup
    digest = document["artifact"]["sha256"]
    storage.delete_blob(f"distribution-artifact-references/{digest}.json")
    metadata_path = f"{ARTIFACT_METADATA_PREFIX}/{digest}.json"

    def age(raw):
        value = json.loads(raw.decode())
        value["created_at"] = "2026-09-19T00:00:00Z"
        return json.dumps(value).encode()

    storage.update_bytes(metadata_path, "application/json", age)
    clock.advance(2 * 86400)
    assert repository.cleanup_orphan_artifacts(retention=timedelta(hours=24)) == 0
    assert storage.blob_exists(document["artifact"]["path"])
    reference = json.loads(
        storage.get_bytes(f"distribution-artifact-references/{digest}.json").decode()
    )
    assert reference["outbox_ids"] == [document["outbox_id"]]


def test_cleanup_fails_closed_while_legacy_reference_scan_is_incomplete(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    source = tmp_path / "legacy.mp4"
    source.write_bytes(b"legacy")
    artifact = commit_immutable_artifact(
        storage,
        source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    metadata_path = f"{ARTIFACT_METADATA_PREFIX}/{artifact.sha256}.json"
    metadata = json.loads(storage.get_bytes(metadata_path).decode())
    metadata["created_at"] = "2026-09-19T00:00:00Z"
    storage.put_bytes(metadata_path, json.dumps(metadata).encode(), "application/json")
    for index in range(2):
        storage.put_bytes(
            f"distribution-outbox/{index:064x}.json",
            json.dumps(
                {
                    "outbox_id": f"{index:064x}",
                    "artifact": {
                        "sha256": artifact.sha256 if index == 1 else "f" * 64,
                    },
                }
            ).encode(),
            "application/json",
        )
    repository = DistributionOutboxRepository(
        storage,
        now=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    assert repository.cleanup_orphan_artifacts(outbox_scan_limit=1) == 0
    assert storage.blob_exists(artifact.path)
    assert repository.cleanup_orphan_artifacts(outbox_scan_limit=1) == 0
    assert storage.blob_exists(artifact.path)


def _authoritative_cycle(week):
    attempt_id = f"attempt-{week}"
    provider_ids = ["youtube-id", "spotify-id"]
    identity = {
        "week": week,
        "accepted_job_id": f"job-{week}",
        "publish_run_id": f"run-{week}",
        "article_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
    }
    artifact = {"sha256": "a" * 64, "media_kind": "video"}
    publication_digest = hashlib.sha256(
        "|".join(
            (
                identity["accepted_job_id"],
                identity["week"],
                identity["publish_run_id"],
                identity["article_sha256"],
                identity["manifest_sha256"],
                artifact["sha256"],
                artifact["media_kind"],
            )
        ).encode()
    ).hexdigest()
    document = {
        "outbox_id": f"outbox-{week}",
        "publication_identity": {
            **identity,
        },
        "publication_digest": publication_digest,
        "artifact": artifact,
        "canonical_artifact": {
            "artifact_id": "a" * 64,
            "selected": True,
            "selection_version": "v1",
        },
        "attempts": [{"attempt_id": attempt_id, "terminal_outcome": "published_verified"}],
        "recovery_authz": [],
        "providers": {
            provider: {
                "result": "externally_verified_public",
                "intent": {"expected_provider_item_id": provider_id},
                "verification": {
                    "provider_item_id": provider_id,
                    "source": f"{provider}_readback",
                    "native_state": "public" if provider == "youtube" else "published",
                },
            }
            for provider, provider_id in zip(("youtube", "spotify"), provider_ids, strict=True)
        },
        "weekly_aggregation": {
            "state": "published_verified",
            "decision_id": f"decision-{week}",
            "rule_version": "weekly-publication-truth-v1",
            "evaluated_attempt_ids": [attempt_id],
            "winning_attempt_id": attempt_id,
            "proof_references": provider_ids,
            "unresolved_conditions": [],
            "worker_exit_class": "zero",
        },
        "aggregate": {"externally_verified_public": True},
    }
    for provider, provider_id in zip(("youtube", "spotify"), provider_ids, strict=True):
        proof_evidence = exact_verification_proof(document, provider_item_id=provider_id)
        leg = document["providers"][provider]
        leg["verification"]["proof"] = DistributionOutboxRepository._verification_proof(
            document,
            leg,
            provider_item_id=provider_id,
            native_state=leg["verification"]["native_state"],
            source=leg["verification"]["source"],
            proof=proof_evidence,
        )
    return document


def _artifact(document):
    from podcaster.distribution_outbox import ArtifactReference

    value = document["artifact"]
    return ArtifactReference(
        path=value["path"],
        sha256=value["sha256"],
        size_bytes=value["size_bytes"],
        media_kind=value["media_kind"],
    )


def _identity(document):
    return PublicationIdentity(**document["publication_identity"])
