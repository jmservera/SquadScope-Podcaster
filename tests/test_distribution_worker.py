from __future__ import annotations

from podcaster.distribution_outbox import (
    DistributionOutboxRepository,
    commit_immutable_artifact,
)
from podcaster.distribution_worker import process_message
from podcaster.publication_state import PublicationIdentity
from podcaster.queue import QueueMessage, encode_distribution_message
from podcaster.storage import LocalStorageBackend
from podcaster.video.distribution import VideoDistributionConfig, YouTubeDeliveryError
from podcaster.video.youtube_publish import PublishResult


class Queue:
    def __init__(self):
        self.deleted = []

    def delete_message(self, message):
        self.deleted.append(message)


def _setup(tmp_path, providers):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"x" * 2048)
    artifact = commit_immutable_artifact(
        storage,
        source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    identity = PublicationIdentity(
        "podcast-2026-W38-comparative-worker",
        "2026-W38",
        "99",
        "a" * 64,
        "b" * 64,
    )
    document, _ = DistributionOutboxRepository(storage).enqueue(
        identity,
        artifact,
        provider_objectives=providers,
        enqueue_source="test",
        enqueue_version="v1",
    )
    message = QueueMessage(
        "m1",
        "r1",
        encode_distribution_message(document["outbox_id"]),
        1,
    )
    return storage, document, message


def test_youtube_draft_processing_promotion_and_public_readback(tmp_path, monkeypatch):
    storage, document, message = _setup(tmp_path, {"youtube": "public"})
    calls = []
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_to_youtube",
        lambda *args, **kwargs: (calls.append("upload") or "video-1", "url"),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.get_video_snippet",
        lambda *args, **kwargs: {
            "uploadStatus": "processed",
            "processingStatus": "succeeded",
            "privacyStatus": "unlisted",
        },
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.publish_video",
        lambda *args, **kwargs: (
            calls.append("promote")
            or PublishResult(
                video_id="video-1",
                succeeded=True,
                privacy_status="public",
            )
        ),
    )
    queue = Queue()
    final = process_message(
        message,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )
    assert calls == ["upload", "promote"]
    assert final["aggregate"]["externally_verified_public"] is True
    assert final["providers"]["youtube"]["intent"]["operation"] == "public_promotion"
    assert final["providers"]["youtube"]["intent_history"][0]["operation"] == "draft_upload"
    assert queue.deleted == [message]


def test_ambiguous_youtube_create_is_durable_unknown_without_retry(tmp_path, monkeypatch):
    storage, document, message = _setup(tmp_path, {"youtube": "public"})
    calls = []
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )

    def ambiguous(*args, **kwargs):
        calls.append("upload")
        raise YouTubeDeliveryError(
            "ambiguous",
            code="youtube_upload_init_ambiguous",
            stage="upload_init",
            retryable=False,
            mutation_ambiguous=True,
        )

    monkeypatch.setattr("podcaster.distribution_worker.upload_to_youtube", ambiguous)
    queue = Queue()
    final = process_message(
        message,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )
    assert calls == ["upload"]
    assert final["aggregate"]["result"] == "publication_unknown"
    assert final["providers"]["youtube"]["receipts"][0]["ambiguous"] is True
    assert queue.deleted == [message]


def test_ambiguous_chunk_upload_redelivery_is_reconcile_only(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"youtube": "public"})
    calls = []
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )

    def ambiguous(*args, **kwargs):
        calls.append("upload")
        raise YouTubeDeliveryError(
            "ambiguous",
            code="youtube_chunked_ambiguous_http_503",
            stage="upload_chunked",
            retryable=False,
            http_status=503,
            mutation_ambiguous=True,
        )

    monkeypatch.setattr("podcaster.distribution_worker.upload_to_youtube", ambiguous)
    config = VideoDistributionConfig(youtube_enabled=True)
    first = process_message(message, queue=Queue(), storage=storage, config=config)
    second = process_message(message, queue=Queue(), storage=storage, config=config)

    assert calls == ["upload"]
    assert first["aggregate"]["result"] == "publication_unknown"
    assert second["aggregate"]["result"] == "publication_unknown"
    assert second["providers"]["youtube"]["verification"]["source"] == (
        "youtube_identity_unprovable"
    )


def test_spotify_unsupported_public_mutation_preserves_manual_handoff(tmp_path):
    storage, _document, message = _setup(tmp_path, {"spotify": "public"})
    queue = Queue()
    final = process_message(
        message,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(),
    )
    assert final["aggregate"]["result"] == "manual_handoff_required"
    assert final["providers"]["spotify"]["receipts"] == []
    assert queue.deleted == [message]


def test_spotify_post_handoff_expected_item_readback_can_clear(tmp_path, monkeypatch):
    storage, document, message = _setup(tmp_path, {"spotify": "public"})
    repository = DistributionOutboxRepository(storage)
    claim = repository.claim(
        document["outbox_id"],
        owner="operator",
        execution_id="manual-handoff",
        lease_seconds=300,
    )
    repository.record_verification(
        claim,
        provider="spotify",
        result="manual_handoff_required",
        source="unsupported_public_mutation",
        provider_item_id="123",
        native_state="draft",
    )
    repository.release(claim)
    monkeypatch.setattr(
        "podcaster.distribution_worker.read_spotify_video_publication_state",
        lambda anchor_id: anchor_id == 123,
    )
    queue = Queue()
    final = process_message(
        message,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(),
    )
    assert final["aggregate"]["externally_verified_public"] is True


def test_scheduler_enqueues_each_due_outbox_once(monkeypatch):
    from podcaster import distribution_scheduler

    class Repository:
        def __init__(self, storage):
            pass

        def due_reconciliations_page(self, **kwargs):
            return (
                [
                    ("a" * 64, "youtube", "t1"),
                    ("a" * 64, "spotify", "t2"),
                    ("b" * 64, "youtube", "t3"),
                ],
                "distribution-outbox/cursor.json",
            )

        def mark_reconciliation_notified(self, outbox_id, *, provider, token):
            return None

        def reserve_reconciliation_notification(
            self, outbox_id, *, provider, token, owner, lease_seconds=60
        ):
            return f"{provider}-reservation"

        def begin_reconciliation_enqueue(self, outbox_id, *, provider, token, reservation_id):
            return None

        def complete_reconciliation_notification(
            self, outbox_id, *, provider, token, reservation_id
        ):
            return None

        def abort_reconciliation_enqueue(self, outbox_id, *, provider, token, reservation_id):
            return None

        def cleanup_orphan_artifacts(self, limit):
            return 0

    class Storage:
        def get_bytes(self, path):
            return None

        def put_bytes(self, path, content, content_type):
            return None

        def list_blobs(self, prefix, *, limit):
            return []

    sent = []
    monkeypatch.setattr(distribution_scheduler, "DistributionOutboxRepository", Repository)
    monkeypatch.setattr(distribution_scheduler, "create_storage_backend", Storage)
    monkeypatch.setattr(
        distribution_scheduler,
        "enqueue_distribution_job",
        lambda outbox_id: sent.append(outbox_id) or True,
    )
    assert distribution_scheduler.run_once() == 0
    assert sent == ["a" * 64, "b" * 64]


def _prepare_lost_promotion(storage, document):
    repository = DistributionOutboxRepository(storage)
    claim = repository.claim(
        document["outbox_id"],
        owner="first-worker",
        execution_id="first-exec",
        lease_seconds=300,
    )
    repository.persist_intent(claim, provider="youtube", operation="draft_upload")
    repository.consume_intent(
        claim,
        provider="youtube",
        provider_timeout_seconds=30,
        receipt_margin_seconds=30,
    )
    repository.record_receipt(
        claim,
        provider="youtube",
        transport_class="accepted",
        provider_item_id="video-1",
        native_state="unlisted",
    )
    repository.persist_intent(
        claim,
        provider="youtube",
        operation="public_promotion",
        expected_provider_item_id="video-1",
    )
    repository.consume_intent(
        claim,
        provider="youtube",
        provider_timeout_seconds=30,
        receipt_margin_seconds=30,
    )

    def expire(current):
        current["claim"]["lease_expires_at"] = "2000-01-01T00:00:00Z"

    repository._update(document["outbox_id"], expire)


def test_lost_promotion_response_takeover_converges_by_public_readback(tmp_path, monkeypatch):
    storage, document, message = _setup(tmp_path, {"youtube": "public"})
    _prepare_lost_promotion(storage, document)
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.get_video_snippet",
        lambda *args: {
            "uploadStatus": "processed",
            "processingStatus": "succeeded",
            "privacyStatus": "public",
        },
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.publish_video",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate promotion")),
    )
    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )
    assert final["aggregate"]["externally_verified_public"] is True


def test_lost_promotion_response_nonpublic_takeover_never_mutates_again(tmp_path, monkeypatch):
    storage, document, message = _setup(tmp_path, {"youtube": "public"})
    _prepare_lost_promotion(storage, document)
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.get_video_snippet",
        lambda *args: {
            "uploadStatus": "processed",
            "processingStatus": "succeeded",
            "privacyStatus": "unlisted",
        },
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.publish_video",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate promotion")),
    )
    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )
    leg = final["providers"]["youtube"]
    assert leg["result"] == "publication_unknown"
    assert leg["verification"]["source"] == "youtube_promotion_identity_readback"
