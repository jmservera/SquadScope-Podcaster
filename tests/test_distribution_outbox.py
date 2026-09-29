from __future__ import annotations

import copy
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
    outbox_path,
    outbox_routing_enabled,
    provider_approval_is_valid,
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


def test_human_approval_is_durable_and_bound_to_publication_identity(
    tmp_path,
    identity,
):
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
    repository = DistributionOutboxRepository(storage)
    document, _created = repository.enqueue(
        identity,
        artifact,
        provider_objectives={"youtube": "public"},
        enqueue_source="test",
        enqueue_version="v1",
        provider_approvals={
            "youtube": {
                "approved": True,
                "approved_by": "operator",
                "approved_at": "2026-09-22T17:00:00Z",
                "source": "manifest_human_review",
            }
        },
    )

    assert provider_approval_is_valid(document, "youtube") is True
    approval = document["providers"]["youtube"]["approval"]
    assert approval["publication_digest"] == document["publication_digest"]
    assert approval["manifest_sha256"] == identity.manifest_sha256


def test_system_auto_approval_is_rejected(tmp_path, identity):
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
    document, _created = DistributionOutboxRepository(storage).enqueue(
        identity,
        artifact,
        provider_objectives={"youtube": "public"},
        enqueue_source="test",
        enqueue_version="v1",
        provider_approvals={
            "youtube": {
                "approved": True,
                "approved_by": "system:auto-publish",
                "approved_at": "2026-09-22T17:00:00Z",
                "source": "manifest_human_review",
            }
        },
    )

    assert provider_approval_is_valid(document, "youtube") is False
    assert document["providers"]["youtube"]["approval"]["approved"] is False


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


def test_initial_provider_legs_have_durable_reconciliation_schedule(setup):
    _storage, _repository, clock, document, _created = setup
    expected_due = clock() + timedelta(minutes=5)
    for leg in document["providers"].values():
        assert leg["next_reconcile_at"] == expected_due.isoformat().replace("+00:00", "Z")
        assert isinstance(leg["active_schedule_token"], str)
        assert len(leg["active_schedule_token"]) == 64


def test_ambiguous_notification_is_not_resent_and_becomes_due(setup):
    _storage, repository, clock, document, _created = setup
    ownership = {"owner": "initial"}
    repository.reserve_notification(
        document["outbox_id"],
        source_ownership=ownership,
        authorize=lambda: None,
    )
    assert repository.authorize_notification_send(
        document["outbox_id"],
        source_ownership=ownership,
        authorize=lambda: None,
    )
    sent = []
    assert repository.repair_notifications(sent.append) == 0
    assert sent == []
    clock.advance(301)
    due = repository.due_reconciliations()
    assert {(provider, token) for _outbox, provider, token in due} == {
        (provider, leg["active_schedule_token"]) for provider, leg in document["providers"].items()
    }


def test_legacy_consumed_notification_is_migrated_to_reconciliation(setup):
    storage, repository, clock, document, _created = setup
    path = outbox_path(document["outbox_id"])

    def make_legacy(raw):
        current = json.loads(raw.decode())
        for leg in current["providers"].values():
            leg["next_reconcile_at"] = None
            leg["active_schedule_token"] = None
            leg["active_schedule_source"] = None
        current["enqueue"]["notification_intent"] = {
            "intent_id": "legacy-intent",
            "source_ownership": {"owner": "legacy"},
            "reserved_at": "2026-09-21T20:59:00Z",
            "consumed_at": "2026-09-21T21:00:00Z",
        }
        current["enqueue"]["notification_sent_at"] = "2026-09-21T21:00:00Z"
        return json.dumps(current, sort_keys=True, separators=(",", ":")).encode()

    storage.update_bytes(path, "application/json", make_legacy)
    sent = []
    assert repository.repair_notifications(sent.append) == 0
    assert sent == []
    repaired = repository.read(document["outbox_id"])
    assert repaired["enqueue"]["notification_intent"]["state"] == "ambiguous"
    assert repaired["enqueue"]["notification_sent_at"] is None
    expected_due = clock() + timedelta(minutes=5)
    assert all(
        leg["next_reconcile_at"] == expected_due.isoformat().replace("+00:00", "Z")
        and leg["active_schedule_token"]
        for leg in repaired["providers"].values()
    )


def test_notification_repair_pages_reach_legacy_records_beyond_first_hundred(setup):
    storage, repository, _clock, document, _created = setup
    for index in range(100):
        item_id = f"{index + 1:064x}"
        copy = json.loads(json.dumps(document))
        copy["outbox_id"] = item_id
        storage.put_bytes(
            outbox_path(item_id),
            json.dumps(copy, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    legacy_id = "f" * 64
    legacy = json.loads(json.dumps(document))
    legacy["outbox_id"] = legacy_id
    for leg in legacy["providers"].values():
        leg["next_reconcile_at"] = None
        leg["active_schedule_token"] = None
        leg["active_schedule_source"] = None
    legacy["enqueue"]["notification_intent"] = {
        "intent_id": "legacy-intent",
        "source_ownership": {"owner": "legacy"},
        "reserved_at": "2026-09-21T20:59:00Z",
        "consumed_at": "2026-09-21T21:00:00Z",
    }
    legacy["enqueue"]["notification_sent_at"] = "2026-09-21T21:00:00Z"
    storage.put_bytes(
        outbox_path(legacy_id),
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        "application/json; charset=utf-8",
    )

    sent = []

    def notify(item_id, authorize_send, mark_accepted):
        if not authorize_send():
            return False
        sent.append(item_id)
        assert mark_accepted()
        return True

    _repaired, cursor = repository.repair_notifications_page(notify, limit=100)
    assert cursor is not None
    assert repository.read(legacy_id)["providers"]["youtube"]["active_schedule_token"] is None

    _repaired, cursor = repository.repair_notifications_page(
        notify,
        limit=100,
        after_path=cursor,
    )
    assert cursor is None
    repaired = repository.read(legacy_id)
    assert repaired["enqueue"]["notification_intent"]["state"] == "ambiguous"
    assert repaired["providers"]["youtube"]["active_schedule_token"]
    assert legacy_id not in sent


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


def _authorize_latest_unknown_recovery(setup):
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
    evidence = _authorize_recovery(
        repository,
        document["outbox_id"],
        unknown,
        suffix="resolved",
    )
    return repository, document, unknown, evidence


def test_latest_unknown_wrong_operation_cannot_create_recovery_evidence(setup):
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

    def _replace_operation(state):
        state["attempts"][-1]["provider_evidence"]["youtube"]["intent"]["operation"] = (
            "unrelated_read_only_probe"
        )

    repository._update(document["outbox_id"], _replace_operation)
    reconciliation = repository.claim(
        document["outbox_id"],
        owner="wrong-operation-readback",
        execution_id="wrong-operation-readback",
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

    with pytest.raises(DistributionOutboxError, match="operation is invalid"):
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
    ("case", "mutate"),
    [
        (
            "intent-id",
            lambda state: state["attempts"][1]["provider_evidence"]["youtube"][
                "intent"
            ].__setitem__("intent_id", "mutated-intent"),
        ),
        (
            "receipt-id",
            lambda state: state["attempts"][1]["provider_evidence"]["youtube"]["receipts"][
                0
            ].__setitem__("receipt_id", "mutated-receipt"),
        ),
        (
            "consumed-owner",
            lambda state: state["attempts"][1]["provider_evidence"]["youtube"][
                "intent"
            ].__setitem__("consumed_owner", "other-owner"),
        ),
        (
            "consumed-fence",
            lambda state: state["attempts"][1]["provider_evidence"]["youtube"][
                "intent"
            ].__setitem__("consumed_fence", 999),
        ),
        (
            "consumed-time",
            lambda state: state["attempts"][1]["provider_evidence"]["youtube"][
                "intent"
            ].__setitem__("consumed_at", "2026-09-21T20:00:00Z"),
        ),
        (
            "provider-item",
            lambda state: state["attempts"][1]["provider_evidence"]["youtube"]["receipts"][
                0
            ].__setitem__("provider_item_id", "youtube-other"),
        ),
        (
            "readback-state",
            lambda state: state["attempts"][1]["post_terminal_readbacks"][0][
                "verification"
            ].__setitem__("native_state", "pending"),
        ),
        (
            "authorization-binding",
            lambda state: state["recovery_authz"][-1]["evidence"]["providers"]["youtube"][
                "predecessor"
            ]["terminal_readback"].__setitem__("native_state", "tampered"),
        ),
        (
            "legacy-schema",
            lambda state: state["recovery_authz"][-1]["evidence"].__setitem__(
                "schema_version", "distribution-recovery-authorization-v1"
            ),
        ),
        (
            "wrong-successor",
            lambda state: state["attempts"][-1].__setitem__("attempt_id", "other-successor"),
        ),
    ],
)
def test_authorized_recovery_recomputes_exact_durable_binding(setup, case, mutate):
    repository, document, _unknown, _evidence = _authorize_latest_unknown_recovery(setup)
    repository._update(document["outbox_id"], mutate)

    claim = repository.claim(
        document["outbox_id"],
        owner=f"{case}-claim",
        execution_id=f"{case}-claim",
        lease_seconds=300,
    )
    assert claim.read_only is True


def test_authorized_recovery_rejects_receipt_swapped_between_providers(setup):
    repository, document, _unknown, _evidence = _authorize_latest_unknown_recovery(setup)

    def _swap_receipts(state):
        evidence = state["attempts"][1]["provider_evidence"]
        evidence["youtube"]["receipts"], evidence["spotify"]["receipts"] = (
            evidence["spotify"]["receipts"],
            evidence["youtube"]["receipts"],
        )

    repository._update(document["outbox_id"], _swap_receipts)
    claim = repository.claim(
        document["outbox_id"],
        owner="swapped-receipt",
        execution_id="swapped-receipt",
        lease_seconds=300,
    )
    assert claim.read_only is True


def test_recovery_authorization_retains_auditable_exact_structured_binding(setup):
    repository, document, unknown, evidence = _authorize_latest_unknown_recovery(setup)
    state = repository.read(document["outbox_id"])
    authorization = state["recovery_authz"][-1]
    successor = state["attempts"][-1]
    youtube = authorization["evidence"]["providers"]["youtube"]
    history = authorization["evidence"]["attempt_history"]

    assert evidence["providers"]["youtube"]["predecessor"]["attempt_id"] == unknown["attempt_id"]
    assert history["schema_version"] == "distribution-attempt-history-evidence-v1"
    assert history["boundary"]["predecessor_attempt_id"] == unknown["attempt_id"]
    assert history["boundary"]["bound_attempt_count"] == 2
    assert len(history["records"]) == 2
    assert len(history["digest"]) == 64
    assert youtube["predecessor"]["intent"]["operation"] == "recovery_mutation"
    assert youtube["predecessor"]["intent"]["operation_type"] == "recovery_fixture"
    assert youtube["predecessor"]["intent"]["intent_id"]
    assert youtube["predecessor"]["receipt"]["receipt_id"]
    assert youtube["predecessor"]["receipt"]["provider"] == "youtube"
    assert youtube["predecessor"]["terminal_readback"]["native_state"] == "failed"
    assert authorization["evidence"]["succeeding_attempt"]["attempt_id"] == successor["attempt_id"]
    assert (
        authorization["evidence"]["succeeding_attempt"]["providers"]["youtube"]
        == youtube["succeeding_provider"]
    )
    claim = repository.claim(
        document["outbox_id"],
        owner="exact-successor",
        execution_id="exact-successor",
        lease_seconds=300,
    )
    assert claim.read_only is False
    _clock = setup[2]
    _clock.advance(301)
    second_claim = repository.claim(
        document["outbox_id"],
        owner="exact-successor-replay",
        execution_id="exact-successor-replay",
        lease_seconds=300,
    )
    assert second_claim.read_only is True


_AUTHORIZATION_ENVELOPE_FIELDS = (
    "schema_version",
    "authz_id",
    "authz_version",
    "source",
    "reason",
    "authorized_at",
    "actor",
    "owner",
    "status",
    "superseded_by_authz_id",
    "predecessor_attempt_id",
    "successor_attempt_id",
    "evidence_schema_version",
    "evidence_digest",
    "evidence",
    "extensions",
)


def _claim_after_authorization_mutation(setup, mutate, *, third_attempt=False):
    if third_attempt:
        repository, document, _evidence = _authorize_third_attempt(setup)
    else:
        repository, document, _unknown, _evidence = _authorize_latest_unknown_recovery(setup)
    repository._update(document["outbox_id"], mutate)
    return repository.claim(
        document["outbox_id"],
        owner="authorization-envelope-probe",
        execution_id="authorization-envelope-probe",
        lease_seconds=300,
    )


@pytest.mark.parametrize("field", _AUTHORIZATION_ENVELOPE_FIELDS)
def test_recovery_authorization_rejects_every_removed_envelope_field(setup, field):
    claim = _claim_after_authorization_mutation(
        setup,
        lambda state: state["recovery_authz"][-1].pop(field),
    )
    assert claim.read_only is True


@pytest.mark.parametrize("field", _AUTHORIZATION_ENVELOPE_FIELDS)
def test_recovery_authorization_rejects_every_envelope_field_type_or_null_mutation(setup, field):
    def _mutate(state):
        authorization = state["recovery_authz"][-1]
        current = authorization[field]
        authorization[field] = "wrong-type" if current is None else None

    claim = _claim_after_authorization_mutation(setup, _mutate)
    assert claim.read_only is True


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        (
            "unknown-version",
            lambda authz: authz.__setitem__("schema_version", "distribution-recovery-authz-v999"),
        ),
        (
            "legacy-version",
            lambda authz: authz.__setitem__(
                "schema_version", "distribution-recovery-authorization-v3"
            ),
        ),
        ("source", lambda authz: authz.__setitem__("source", "bounded_reconciliation")),
        ("reason", lambda authz: authz.__setitem__("reason", "changed-reason")),
        (
            "authorized-at",
            lambda authz: authz.__setitem__("authorized_at", "1999-01-01T00:00:00Z"),
        ),
        ("actor", lambda authz: authz.__setitem__("actor", "unexpected-actor")),
        ("owner", lambda authz: authz.__setitem__("owner", "unexpected-owner")),
        ("status", lambda authz: authz.__setitem__("status", "revoked")),
        (
            "predecessor",
            lambda authz: authz.__setitem__("predecessor_attempt_id", "wrong-predecessor"),
        ),
        (
            "successor",
            lambda authz: authz.__setitem__("successor_attempt_id", "wrong-successor"),
        ),
        (
            "evidence-version",
            lambda authz: authz.__setitem__(
                "evidence_schema_version", "distribution-recovery-authorization-v3"
            ),
        ),
        ("evidence-digest", lambda authz: authz.__setitem__("evidence_digest", "0" * 64)),
        (
            "evidence-content",
            lambda authz: authz["evidence"].__setitem__("unexpected_evidence", "changed"),
        ),
        (
            "extensions-content",
            lambda authz: authz["extensions"].__setitem__("audit", "changed"),
        ),
        (
            "unknown-field",
            lambda authz: authz.__setitem__("unexpected_audit_field", "changed"),
        ),
    ],
)
def test_recovery_authorization_rejects_complete_envelope_mutation(setup, case, mutate):
    def _mutate(state):
        mutate(state["recovery_authz"][-1])

    claim = _claim_after_authorization_mutation(setup, _mutate)
    assert claim.read_only is True


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        (
            "duplicate-matching",
            lambda state: state["recovery_authz"].append(
                copy.deepcopy(state["recovery_authz"][-1])
            ),
        ),
        (
            "unrelated-extra",
            lambda state: state["recovery_authz"].append(
                {
                    **copy.deepcopy(state["recovery_authz"][-1]),
                    "authz_id": "unrelated-authz",
                    "successor_attempt_id": "unrelated-successor",
                }
            ),
        ),
        (
            "conflicting-extra",
            lambda state: state["recovery_authz"].append(
                {
                    **copy.deepcopy(state["recovery_authz"][-1]),
                    "authz_id": "conflicting-authz",
                }
            ),
        ),
    ],
)
def test_recovery_authorization_rejects_duplicate_unrelated_or_conflicting_extra(
    setup, case, mutate
):
    claim = _claim_after_authorization_mutation(setup, mutate)
    assert claim.read_only is True


def test_recovery_authorization_rejects_reordered_collection(setup):
    claim = _claim_after_authorization_mutation(
        setup,
        lambda state: state["recovery_authz"].reverse(),
        third_attempt=True,
    )
    assert claim.read_only is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "distribution-recovery-authz-set-v999"),
        ("authz_count", 99),
        ("ordered_authz_ids", []),
        ("active_authz_id", "wrong-active-authz"),
        ("digest", "0" * 64),
    ],
)
def test_recovery_authorization_rejects_set_identity_mutation(setup, field, value):
    claim = _claim_after_authorization_mutation(
        setup,
        lambda state: state["recovery_authz_set"].__setitem__(field, value),
    )
    assert claim.read_only is True


@pytest.mark.parametrize("value", [True, 1.0])
def test_recovery_authorization_rejects_authz_count_type_bypass(setup, value):
    claim = _claim_after_authorization_mutation(
        setup,
        lambda state: state["recovery_authz_set"].__setitem__("authz_count", value),
    )
    assert claim.read_only is True


def _scalar_paths(value, prefix=()):
    if type(value) is dict:
        for key, item in value.items():
            yield from _scalar_paths(item, (*prefix, key))
    elif type(value) is list:
        for index, item in enumerate(value):
            yield from _scalar_paths(item, (*prefix, index))
    elif value is None or type(value) in (bool, int, float, str):
        yield prefix


def _value_at_path(value, path):
    for part in path:
        value = value[part]
    return value


def _set_value_at_path(value, path, replacement):
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = replacement


_NO_SCALAR_REPLACEMENT = object()


def _typed_scalar_replacement(current, replacement_kind):
    replacements = {
        "bool": not current if type(current) is bool else True,
        "int": current + 97 if type(current) is int else 97,
        "float": 1.0,
        "string": f"{current}-changed" if type(current) is str else "typed-substitution",
        "null": None,
    }
    replacement = replacements[replacement_kind]
    if type(replacement) is type(current) and replacement == current:
        return _NO_SCALAR_REPLACEMENT
    return replacement


@pytest.mark.parametrize("replacement_kind", ["bool", "int", "float", "string", "null"])
def test_recovery_canonical_contract_rejects_scalar_substitutions_everywhere(
    setup, replacement_kind
):
    storage, repository, _clock, document, _created = setup
    repository, document, _evidence = _authorize_third_attempt(setup)
    path = outbox_path(document["outbox_id"])
    baseline = storage.get_bytes(path)
    assert baseline is not None
    baseline_state = repository.read(document["outbox_id"])
    surfaces = {
        "authorization-set": ("recovery_authz_set",),
        "authorization-envelope": ("recovery_authz", -1),
        "authorization-evidence": ("recovery_authz", -1, "evidence"),
        "attempt-history": ("recovery_authz", -1, "evidence", "attempt_history"),
        "attempt-record": ("attempts", 0),
        "attempt-events": ("attempts", 0, "events"),
        "successor": ("recovery_authz", -1, "evidence", "succeeding_attempt"),
    }

    exercised = 0
    for surface, root_path in surfaces.items():
        root = _value_at_path(baseline_state, root_path)
        for relative_path in _scalar_paths(root):
            current = _value_at_path(root, relative_path)
            replacement = _typed_scalar_replacement(current, replacement_kind)
            if replacement is _NO_SCALAR_REPLACEMENT:
                continue
            storage.update_bytes(
                path,
                "application/json; charset=utf-8",
                lambda _raw, snapshot=baseline: snapshot,
            )

            def _mutate(state, target=(*root_path, *relative_path), value=replacement):
                _set_value_at_path(state, target, value)

            denied = False
            try:
                repository._update(document["outbox_id"], _mutate)
                claim = repository.claim(
                    document["outbox_id"],
                    owner=f"typed-{replacement_kind}",
                    execution_id=f"typed-{replacement_kind}",
                    lease_seconds=300,
                )
                denied = claim.read_only
            except DistributionOutboxError:
                denied = True
            assert denied, (
                f"{surface} path {relative_path!r} accepted {replacement_kind} "
                f"for {type(current).__name__}"
            )
            exercised += 1

    assert exercised > 0


@pytest.mark.parametrize("replacement", [float("nan"), float("inf"), float("-inf"), -0.0])
def test_recovery_canonical_contract_rejects_unsupported_float_forms(setup, replacement):
    claim = None
    try:
        claim = _claim_after_authorization_mutation(
            setup,
            lambda state: state["recovery_authz_set"].__setitem__("authz_count", replacement),
        )
    except DistributionOutboxError:
        pass
    assert claim is None or claim.read_only is True


def test_recovery_canonical_contract_rejects_unsupported_container_value(setup):
    with pytest.raises(DistributionOutboxError):
        _claim_after_authorization_mutation(
            setup,
            lambda state: state["recovery_authz"][-1]["evidence"].__setitem__(
                "unsupported", {"not-json"}
            ),
        )


def test_outbox_json_rejects_duplicate_recovery_fields(setup):
    storage, repository, _clock, document, _created = setup
    repository, document, _evidence = _authorize_third_attempt(setup)
    path = outbox_path(document["outbox_id"])
    raw = storage.get_bytes(path)
    assert raw is not None
    duplicated = raw.replace(b'"authz_count":2', b'"authz_count":2,"authz_count":2', 1)
    assert duplicated != raw
    storage.update_bytes(
        path,
        "application/json; charset=utf-8",
        lambda _raw: duplicated,
    )
    with pytest.raises(DistributionOutboxError, match="duplicate fields"):
        repository.read(document["outbox_id"])


def test_outbox_json_rejects_exponent_overflow(setup):
    storage, repository, _clock, document, _created = setup
    path = outbox_path(document["outbox_id"])
    raw = storage.get_bytes(path)
    assert raw is not None
    overflowed = raw.replace(b'"fencing_token":0', b'"fencing_token":1e999', 1)
    assert overflowed != raw
    storage.update_bytes(
        path,
        "application/json; charset=utf-8",
        lambda _raw: overflowed,
    )
    with pytest.raises(DistributionOutboxError, match="non-finite number"):
        repository.read(document["outbox_id"])


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
        lambda history: history["records"].pop(),
        lambda history: history["records"].reverse(),
        lambda history: history["records"].append(copy.deepcopy(history["records"][0])),
    ],
    ids=["omit", "reorder", "duplicate"],
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
    mutate_history(evidence["attempt_history"])

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


def _authorize_third_attempt(setup):
    _storage, repository, _clock, document, _created = setup
    first = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="first-history",
        execution_id="first-history",
    )
    _authorize_recovery(repository, document["outbox_id"], first, suffix="second-history")
    second = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="second-history",
        execution_id="second-history",
    )
    evidence = _authorize_recovery(
        repository,
        document["outbox_id"],
        second,
        suffix="third-history",
    )
    return repository, document, evidence


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        (
            "attempt-record-version",
            lambda attempt: attempt.__setitem__(
                "record_version", "distribution-attempt-record-v999"
            ),
        ),
        ("attempt-id", lambda attempt: attempt.__setitem__("attempt_id", "forged-attempt")),
        ("authorization-id", lambda attempt: attempt.__setitem__("authz_id", "forged-authz")),
        (
            "authorization-source",
            lambda attempt: attempt.__setitem__("authz_source", "forged-source"),
        ),
        (
            "authorization-reason",
            lambda attempt: attempt.__setitem__("authz_reason", "forged-reason"),
        ),
        (
            "authorization-time",
            lambda attempt: attempt.__setitem__("authorized_at", "1999-01-01T00:00:00Z"),
        ),
        (
            "predecessor-linkage",
            lambda attempt: attempt.__setitem__("predecessor_attempt_id", "forged-predecessor"),
        ),
        ("attempt-state", lambda attempt: attempt.__setitem__("state", "forged-state")),
        (
            "terminal-classification",
            lambda attempt: attempt.__setitem__("terminal_outcome", "provider_unknown"),
        ),
        (
            "proof-reference",
            lambda attempt: attempt["proof_references"].append("forged-proof"),
        ),
        (
            "provider-result",
            lambda attempt: attempt["provider_evidence"]["youtube"].__setitem__(
                "result", "provider_unknown"
            ),
        ),
        (
            "provider-readback-source",
            lambda attempt: attempt["provider_evidence"]["youtube"]["verification"].__setitem__(
                "source", "forged-readback"
            ),
        ),
        (
            "provider-readback-item",
            lambda attempt: attempt["provider_evidence"]["youtube"]["verification"].__setitem__(
                "provider_item_id", "forged-item"
            ),
        ),
        (
            "provider-readback-state",
            lambda attempt: attempt["provider_evidence"]["youtube"]["verification"].__setitem__(
                "native_state", "pending"
            ),
        ),
        (
            "provider-readback-result",
            lambda attempt: attempt["provider_evidence"]["youtube"]["verification"].__setitem__(
                "result", "publication_unknown"
            ),
        ),
        (
            "provider-readback-fence",
            lambda attempt: attempt["provider_evidence"]["youtube"]["verification"].__setitem__(
                "fencing_token", 999999
            ),
        ),
        (
            "provider-readback-time",
            lambda attempt: attempt["provider_evidence"]["youtube"]["verification"].__setitem__(
                "at", "1999-01-01T00:00:00Z"
            ),
        ),
        (
            "event-type",
            lambda attempt: attempt["events"][1].__setitem__("event_type", "forged-event"),
        ),
        (
            "event-state",
            lambda attempt: attempt["events"][1].__setitem__("state", "forged-state"),
        ),
        (
            "event-sequence",
            lambda attempt: attempt["events"][1].__setitem__("sequence", 99),
        ),
        (
            "event-time",
            lambda attempt: attempt["events"][1].__setitem__("at", "1999-01-01T00:00:00Z"),
        ),
        (
            "event-owner",
            lambda attempt: attempt["events"][1].__setitem__("owner", "forged-owner"),
        ),
        (
            "event-claim-id",
            lambda attempt: attempt["events"][1].__setitem__("claim_id", "forged-claim"),
        ),
        (
            "event-execution-id",
            lambda attempt: attempt["events"][1].__setitem__("execution_id", "forged-execution"),
        ),
        (
            "event-fence",
            lambda attempt: attempt["events"][1].__setitem__("fencing_token", 999999),
        ),
        (
            "event-lease",
            lambda attempt: attempt["events"][1].__setitem__(
                "lease_expires_at", "2099-01-01T00:00:00Z"
            ),
        ),
    ],
)
def test_authorized_recovery_denies_every_semantic_prior_attempt_or_event_mutation(
    setup, case, mutate
):
    repository, document, _evidence = _authorize_third_attempt(setup)

    def _mutate(state):
        mutate(state["attempts"][0])

    repository._update(document["outbox_id"], _mutate)
    claim = repository.claim(
        document["outbox_id"],
        owner=f"{case}-owner",
        execution_id=f"{case}-execution",
        lease_seconds=300,
    )
    assert claim.read_only is True


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        (
            "reorder-attempts",
            lambda state: state["attempts"].__setitem__(
                slice(0, 2), reversed(state["attempts"][:2])
            ),
        ),
        (
            "duplicate-attempt",
            lambda state: state["attempts"].insert(1, copy.deepcopy(state["attempts"][0])),
        ),
        ("omit-attempt", lambda state: state["attempts"].pop(0)),
        (
            "insert-extra-attempt",
            lambda state: state["attempts"].insert(
                2,
                {
                    **copy.deepcopy(state["attempts"][1]),
                    "attempt_id": "inserted-extra-attempt",
                },
            ),
        ),
        (
            "duplicate-event",
            lambda state: state["attempts"][0]["events"].insert(
                2, copy.deepcopy(state["attempts"][0]["events"][1])
            ),
        ),
        ("omit-event", lambda state: state["attempts"][0]["events"].pop(1)),
        (
            "reorder-events",
            lambda state: state["attempts"][0]["events"].__setitem__(
                slice(1, 3), reversed(state["attempts"][0]["events"][1:3])
            ),
        ),
        (
            "insert-extra-event",
            lambda state: state["attempts"][0]["events"].append(
                {
                    "sequence": len(state["attempts"][0]["events"]) + 1,
                    "event_type": "forged",
                    "at": state["attempts"][0]["events"][-1]["at"],
                    "state": "forged",
                }
            ),
        ),
    ],
)
def test_authorized_recovery_denies_attempt_or_event_structure_mutation(setup, case, mutate):
    repository, document, _evidence = _authorize_third_attempt(setup)
    repository._update(document["outbox_id"], mutate)
    claim = repository.claim(
        document["outbox_id"],
        owner=f"{case}-owner",
        execution_id=f"{case}-execution",
        lease_seconds=300,
    )
    assert claim.read_only is True


def test_authorized_recovery_denies_unexpected_successor_event_append(setup):
    repository, document, _evidence = _authorize_third_attempt(setup)

    def _append(state):
        state["attempts"][-1]["events"].append(
            {
                "sequence": 2,
                "event_type": "unexpected",
                "at": state["attempts"][-1]["authorized_at"],
                "state": "unexpected",
            }
        )

    repository._update(document["outbox_id"], _append)
    claim = repository.claim(
        document["outbox_id"],
        owner="unexpected-successor-event",
        execution_id="unexpected-successor-event",
        lease_seconds=300,
    )
    assert claim.read_only is True


def test_authorized_recovery_denies_cross_week_publication_replay(setup):
    repository, document, _evidence = _authorize_third_attempt(setup)

    def _replay(state):
        state["publication_identity"]["week"] = "2026-W39"
        state["publication_identity"]["accepted_job_id"] = "podcast-2026-W39-replay"

    repository._update(document["outbox_id"], _replay)
    claim = repository.claim(
        document["outbox_id"],
        owner="cross-week-replay",
        execution_id="cross-week-replay",
        lease_seconds=300,
    )
    assert claim.read_only is True


def test_canonical_attempt_history_round_trip_is_stable(setup):
    _storage, repository, _clock, document, _created = setup
    first = _failed_attempt(
        repository,
        document["outbox_id"],
        owner="round-trip",
        execution_id="round-trip",
    )
    state = repository.read(document["outbox_id"])
    evidence = exact_recovery_authorization_evidence(
        state,
        predecessor_attempt_id=first["attempt_id"],
        expected_provider_item_ids={
            "youtube": "youtube-round-trip",
            "spotify": "spotify-round-trip",
        },
    )
    serialized = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    assert json.loads(serialized) == evidence
    recomputed = exact_recovery_authorization_evidence(
        repository.read(document["outbox_id"]),
        predecessor_attempt_id=first["attempt_id"],
        expected_provider_item_ids={
            "youtube": "youtube-round-trip",
            "spotify": "spotify-round-trip",
        },
    )
    assert recomputed == evidence
    history_without_digest = dict(evidence["attempt_history"])
    digest = history_without_digest.pop("digest")
    assert (
        hashlib.sha256(
            json.dumps(
                history_without_digest,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        == digest
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
