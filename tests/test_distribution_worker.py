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
        "podcast-2026-W38-worker",
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

        def due_reconciliations(self, limit):
            return [
                ("a" * 64, "youtube", "t1"),
                ("a" * 64, "spotify", "t2"),
                ("b" * 64, "youtube", "t3"),
            ]

    sent = []
    monkeypatch.setattr(distribution_scheduler, "DistributionOutboxRepository", Repository)
    monkeypatch.setattr(distribution_scheduler, "create_storage_backend", lambda: object())
    monkeypatch.setattr(
        distribution_scheduler,
        "enqueue_distribution_job",
        lambda outbox_id: sent.append(outbox_id) or True,
    )
    assert distribution_scheduler.run_once() == 0
    assert sent == ["a" * 64, "b" * 64]
