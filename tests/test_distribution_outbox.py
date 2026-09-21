from __future__ import annotations

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

    repository.authorize_recovery(
        document["outbox_id"],
        predecessor_attempt_id=failed_attempt["attempt_id"],
        source="operator",
        reason="no_mutation_proven",
        no_mutation_proven=True,
    )
    recovered_claim = repository.claim(
        document["outbox_id"], owner="recovery", execution_id="exec-recovery", lease_seconds=300
    )
    for provider in ("youtube", "spotify"):
        repository.record_verification(
            recovered_claim,
            provider=provider,
            result="externally_verified_public",
            source=f"{provider}_readback",
            provider_item_id=f"{provider}-recovered",
            native_state="public" if provider == "youtube" else "published",
        )
    recovered = repository.release(recovered_claim)
    assert recovered["attempts"][0] == failed_attempt
    assert recovered["weekly_aggregation"]["state"] == "published_verified_recovered"
    assert recovered["weekly_aggregation"]["failed_attempt_references"] == [
        failed_attempt["attempt_id"]
    ]
    assert recovered["weekly_aggregation"]["winning_attempt_id"] == recovered_claim.attempt_id
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
            no_mutation_proven=True,
        )


def test_weekly_w38_recovery_candidate_and_w39_missed_fixture():
    attempts = [
        {"attempt_id": "failed", "terminal_outcome": "failed_terminal"},
        {"attempt_id": "recovered", "terminal_outcome": "published_verified"},
    ]
    assert weekly_state_from_attempts(attempts) == "published_verified_recovered"
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


def test_four_cycle_acceptance_requires_exactly_four_green_weekly_decisions():
    green = [
        {"weekly_aggregation": {"state": "published_verified"}},
        {"weekly_aggregation": {"state": "published_verified_recovered"}},
        {"weekly_aggregation": {"state": "published_verified"}},
        {"weekly_aggregation": {"state": "published_verified"}},
    ]
    assert four_cycle_acceptance(green) is True
    green[2] = {"weekly_aggregation": {"state": "provider_unknown"}}
    assert four_cycle_acceptance(green) is False
    assert four_cycle_acceptance(green[:3]) is False


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
