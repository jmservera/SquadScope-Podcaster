from __future__ import annotations

import json

from podcaster.distribution_outbox import (
    DistributionOutboxRepository,
    commit_immutable_artifact,
)
from podcaster.distribution_worker import process_message
from podcaster.publication_state import PublicationIdentity
from podcaster.publish import PublishResult as SpotifyPublishResult
from podcaster.publish import VideoPromoteResult
from podcaster.queue import QueueMessage, encode_distribution_message
from podcaster.storage import LocalStorageBackend
from podcaster.video.distribution import VideoDistributionConfig, YouTubeDeliveryError
from podcaster.video.youtube_playlist import PlaylistAddResult
from podcaster.video.youtube_publish import PublishResult


class Queue:
    def __init__(self):
        self.deleted = []

    def delete_message(self, message):
        self.deleted.append(message)


def _setup(tmp_path, providers, *, approved=True):
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
    review = (
        {
            "status": "approved",
            "approved_by": "operator-123",
            "approved_at": "2026-09-22T16:00:00Z",
            "audit_trail": [
                {
                    "actor": "operator-123",
                    "at": "2026-09-22T16:00:00Z",
                    "decision": "approved",
                }
            ],
        }
        if approved
        else {"status": "pending", "audit_trail": []}
    )
    storage.put_bytes(
        f"jobs/{identity.accepted_job_id}/manifest.json",
        json.dumps(
            {
                "job_id": identity.accepted_job_id,
                "request": {
                    "publish_run_id": identity.publish_run_id,
                    "manifest_sha256": identity.manifest_sha256,
                    "article_title": "Episode",
                    "article_summary": "Summary",
                    "language": "en",
                },
                "review": review,
            }
        ).encode(),
        "application/json",
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
    snippets = iter(
        [
            {
                "uploadStatus": "processed",
                "processingStatus": "succeeded",
                "privacyStatus": "unlisted",
            },
            {
                "uploadStatus": "processed",
                "processingStatus": "succeeded",
                "privacyStatus": "public",
            },
        ]
    )
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
        lambda *args, **kwargs: next(snippets),
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


def test_spotify_draft_mode_preserves_manual_handoff(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"spotify": "public"})
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_video_to_episode",
        lambda *args, **kwargs: SpotifyPublishResult(anchor_episode_id=123, status="draft"),
    )
    queue = Queue()
    final = process_message(
        message,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(),
    )
    assert final["aggregate"]["result"] == "manual_handoff_required"
    assert final["providers"]["spotify"]["receipts"][0]["native_state"] == "draft"
    assert queue.deleted == [message]


def test_youtube_public_promotion_requires_human_approval(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"youtube": "public"}, approved=False)
    calls = []
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_to_youtube",
        lambda *args, **kwargs: ("video-1", "url"),
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
        lambda *args, **kwargs: calls.append("promote"),
    )

    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )

    assert calls == []
    assert final["providers"]["youtube"]["result"] == "manual_handoff_required"
    assert final["providers"]["youtube"]["intent"]["operation"] == "draft_upload"
    assert "operator_approval" not in final["providers"]["youtube"]


def test_youtube_automatic_approval_identity_cannot_promote(tmp_path, monkeypatch):
    storage, document, message = _setup(tmp_path, {"youtube": "public"})
    manifest_path = f"jobs/{document['publication_identity']['accepted_job_id']}/manifest.json"
    manifest = json.loads(storage.get_bytes(manifest_path))
    manifest["review"]["approved_by"] = "system:auto-publish"
    manifest["review"]["audit_trail"][0]["actor"] = "system:auto-publish"
    storage.put_bytes(
        manifest_path,
        json.dumps(manifest).encode(),
        "application/json",
    )
    calls = []
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_to_youtube",
        lambda *args, **kwargs: ("video-1", "url"),
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
        lambda *args, **kwargs: calls.append("promote"),
    )

    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )

    assert calls == []
    assert final["providers"]["youtube"]["result"] == "manual_handoff_required"


def test_spotify_upload_and_live_promotion_are_fenced_and_verified(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"spotify": "public"})
    calls = []
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_video_to_episode",
        lambda *args, **kwargs: (
            calls.append(("upload", kwargs["content_type"]))
            or SpotifyPublishResult(anchor_episode_id=456, status="draft")
        ),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.promote_spotify_video_draft",
        lambda *args, **kwargs: (
            calls.append(("promote", args[0], kwargs["audio_anchor_id"]))
            or VideoPromoteResult(
                anchor_episode_id=456,
                terminal_state="published",
                is_published=True,
                authorized=True,
            )
        ),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.read_spotify_video_publication_state",
        lambda anchor_id: anchor_id == 456,
    )

    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(
            spotify_upload_enabled=True,
            spotify_video_publish_mode="live",
        ),
    )

    assert calls == [("upload", "video/mp4"), ("promote", 456, None)]
    assert final["providers"]["spotify"]["intent"]["operation"] == "live_promotion"
    assert final["providers"]["spotify"]["intent_history"][0]["operation"] == ("create_video_draft")
    assert final["aggregate"]["externally_verified_public"] is True


def test_spotify_ambiguous_upload_replay_never_uploads_twice(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"spotify": "public"})
    calls = []
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_video_to_episode",
        lambda *args, **kwargs: calls.append("upload") or SpotifyPublishResult(status="failed"),
    )
    config = VideoDistributionConfig(spotify_upload_enabled=True)

    first = process_message(message, queue=Queue(), storage=storage, config=config)
    second = process_message(message, queue=Queue(), storage=storage, config=config)

    assert calls == ["upload"]
    assert first["providers"]["spotify"]["result"] == "publication_unknown"
    assert second["providers"]["spotify"]["result"] == "publication_unknown"


def test_spotify_rss_is_cas_idempotent_and_read_back(tmp_path):
    storage, _document, message = _setup(tmp_path, {"spotify_rss": "public"})
    storage.put_bytes(
        "feeds/show.xml",
        b"<rss><channel><title>Show</title></channel></rss>",
        "application/rss+xml",
    )
    config = VideoDistributionConfig(
        spotify_rss_enabled=True,
        spotify_rss_feed_path="feeds/show.xml",
    )

    first = process_message(message, queue=Queue(), storage=storage, config=config)
    second = process_message(message, queue=Queue(), storage=storage, config=config)
    feed = storage.get_bytes("feeds/show.xml").decode()

    assert first["aggregate"]["externally_verified_public"] is True
    assert second["aggregate"]["externally_verified_public"] is True
    assert feed.count('<guid isPermaLink="false">') == 1


def test_malformed_distribution_message_is_discarded(tmp_path):
    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    message = QueueMessage("bad", "receipt", "not-json", 1)
    queue = Queue()

    final = process_message(
        message,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(),
    )

    assert final["aggregate"]["result"] == "poisoned"
    assert queue.deleted == [message]


def test_retry_exhaustion_persists_poison_and_deletes(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"youtube": "public"})
    message = QueueMessage(
        message.message_id,
        message.pop_receipt,
        message.body,
        5,
    )
    monkeypatch.setattr(storage, "download_file", lambda *args: False)
    queue = Queue()

    final = process_message(
        message,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )

    assert final["providers"]["youtube"]["result"] == "poisoned"
    assert final["providers"]["youtube"]["verification"]["exhaustion_reason"] == (
        "queue_dequeue_limit_reached"
    )
    assert queue.deleted == [message]


def test_youtube_playlist_insert_is_verified_and_not_repeated(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"youtube": "public"})
    calls = []
    membership = iter([False, True])
    snippets = iter(
        [
            {
                "uploadStatus": "processed",
                "processingStatus": "succeeded",
                "privacyStatus": "unlisted",
            },
            {
                "uploadStatus": "processed",
                "processingStatus": "succeeded",
                "privacyStatus": "public",
            },
        ]
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_to_youtube",
        lambda *args, **kwargs: ("video-1", "url"),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.get_video_snippet",
        lambda *args: next(snippets),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.playlist_contains_video",
        lambda *args, **kwargs: next(membership),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.add_video_to_playlist",
        lambda *args, **kwargs: (
            calls.append("playlist")
            or PlaylistAddResult(
                video_id="video-1",
                playlist_id="PL-show",
                succeeded=True,
                playlist_item_id="item-1",
            )
        ),
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

    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(
            youtube_enabled=True,
            youtube_playlist_id="PL-show",
        ),
    )

    assert calls == ["playlist", "promote"]
    assert final["providers"]["youtube"]["intent_history"][-1]["operation"] == ("playlist_insert")
    assert final["aggregate"]["externally_verified_public"] is True


def test_ambiguous_playlist_insert_replay_reconciles_without_second_insert(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"youtube": "public"})
    calls = []
    membership = iter([False, True])
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_to_youtube",
        lambda *args, **kwargs: ("video-1", "url"),
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
        "podcaster.distribution_worker.playlist_contains_video",
        lambda *args, **kwargs: next(membership),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.add_video_to_playlist",
        lambda *args, **kwargs: (
            calls.append("playlist")
            or PlaylistAddResult(
                video_id="video-1",
                playlist_id="PL-show",
                succeeded=False,
                error="timeout",
            )
        ),
    )

    config = VideoDistributionConfig(
        youtube_enabled=True,
        youtube_playlist_id="PL-show",
    )
    first = process_message(message, queue=Queue(), storage=storage, config=config)
    second = process_message(message, queue=Queue(), storage=storage, config=config)

    assert calls == ["playlist"]
    assert first["providers"]["youtube"]["result"] == "publication_unknown"
    assert second["providers"]["youtube"]["verification"]["source"] == (
        "youtube_promotion_identity_readback"
    )


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
