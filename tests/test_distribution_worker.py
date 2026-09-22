from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from podcaster.distribution_outbox import (
    DistributionOutboxRepository,
    commit_immutable_artifact,
    outbox_path,
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


def _setup(tmp_path, providers, *, approved=True, context=None):
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
        provider_approvals={
            provider: (
                {
                    "approved": True,
                    "approved_by": "operator",
                    "approved_at": "2026-09-22T17:00:00Z",
                    "source": "manifest_human_review",
                }
                if approved
                else {"approved": False}
            )
            for provider in providers
        },
        provider_context=context or {},
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
        "podcaster.distribution_worker._youtube_reconcile_create",
        lambda *args, **kwargs: ("absent", None),
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
    monkeypatch.setattr(
        "podcaster.distribution_worker._youtube_reconcile_create",
        lambda *args, **kwargs: ("absent", None),
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
    monkeypatch.setattr(
        "podcaster.distribution_worker._youtube_reconcile_create",
        lambda *args, **kwargs: ("absent", None),
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


def test_spotify_failed_reconcile_fails_closed_without_public_success(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"spotify": "public"})
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_video_to_episode",
        lambda *args, **kwargs: SimpleNamespace(
            status="failed",
            anchor_episode_id=None,
        ),
    )
    queue = Queue()
    final = process_message(
        message,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(),
    )
    assert final["aggregate"]["result"] == "publication_unknown"
    assert final["providers"]["spotify"]["receipts"][0]["ambiguous"] is True
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

    repair_pages = []

    class Repository:
        def __init__(self, storage):
            pass

        def repair_notifications_page(self, notify, **kwargs):
            repair_pages.append(kwargs)
            return 0, "repair-cursor"

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

    state = {}

    class Storage:
        def get_bytes(self, path):
            return state.get(path)

        def put_bytes(self, path, content, content_type):
            state[path] = content

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
    assert repair_pages == [{"limit": 100, "after_path": None}]
    assert json.loads(state[distribution_scheduler.SCHEDULER_STATE_PATH]) == {
        "outbox_cursor": "distribution-outbox/cursor.json",
        "repair_cursor": "repair-cursor",
    }


def test_scheduler_repairs_legacy_notifications_and_reconciles_without_replay(
    tmp_path, monkeypatch
):
    from podcaster import distribution_scheduler

    storage = LocalStorageBackend(tmp_path / "storage", "http://localhost/artifacts")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"scheduler-repair")
    artifact = commit_immutable_artifact(
        storage,
        source,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    current = [datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)]
    repository = DistributionOutboxRepository(storage, now=lambda: current[0])

    def enqueue(index):
        document, _created = repository.enqueue(
            PublicationIdentity(
                accepted_job_id=f"podcast-2026-W39-scheduler-{index}",
                week="2026-W39",
                publish_run_id=str(index),
                article_sha256=f"{index:064x}",
                manifest_sha256=f"{index + 100:064x}",
            ),
            artifact,
            provider_objectives={"youtube": "public"},
            enqueue_source="test",
            enqueue_version="v1",
        )
        return document

    fresh = enqueue(1)
    legacy = enqueue(2)
    accepted = enqueue(3)

    def make_legacy(raw):
        document = json.loads(raw.decode("utf-8"))
        for leg in document["providers"].values():
            leg["next_reconcile_at"] = None
            leg["active_schedule_token"] = None
            leg["active_schedule_source"] = None
        document["enqueue"]["notification_intent"] = {
            "intent_id": "legacy-intent",
            "source_ownership": {"owner": "legacy"},
            "reserved_at": "2026-09-22T19:59:00Z",
            "consumed_at": "2026-09-22T20:00:00Z",
        }
        document["enqueue"]["notification_sent_at"] = "2026-09-22T20:00:00Z"
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

    storage.update_bytes(
        outbox_path(legacy["outbox_id"]),
        "application/json; charset=utf-8",
        make_legacy,
    )
    accepted_owner = {"owner": "accepted"}
    repository.reserve_notification(
        accepted["outbox_id"],
        source_ownership=accepted_owner,
        authorize=lambda: None,
    )
    assert repository.authorize_notification_send(
        accepted["outbox_id"],
        source_ownership=accepted_owner,
        authorize=lambda: None,
    )
    assert repository.accept_notification_send(
        accepted["outbox_id"],
        source_ownership=accepted_owner,
        authorize=lambda: None,
    )

    monkeypatch.setattr(
        distribution_scheduler,
        "DistributionOutboxRepository",
        lambda backend: DistributionOutboxRepository(backend, now=lambda: current[0]),
    )
    monkeypatch.setattr(distribution_scheduler, "create_storage_backend", lambda: storage)
    enqueued = []

    def record_enqueue(outbox_id, *, authorize_send=None, mark_accepted=None):
        if authorize_send is not None:
            if not authorize_send():
                return True
            enqueued.append(("initial", outbox_id))
            assert mark_accepted is not None
            assert mark_accepted()
            return True
        enqueued.append(("reconciliation", outbox_id))
        return True

    monkeypatch.setattr(distribution_scheduler, "enqueue_distribution_job", record_enqueue)

    assert distribution_scheduler.run_once() == 0
    assert enqueued == [("initial", fresh["outbox_id"])]
    repaired_legacy = repository.read(legacy["outbox_id"])
    assert repaired_legacy["enqueue"]["notification_intent"]["state"] == "ambiguous"
    assert repaired_legacy["providers"]["youtube"]["active_schedule_token"]
    assert repaired_legacy["providers"]["youtube"]["next_reconcile_at"]
    assert (
        repository.read(accepted["outbox_id"])["enqueue"]["notification_intent"]["state"]
        == "accepted"
    )

    current[0] += timedelta(seconds=301)
    enqueued.clear()
    assert distribution_scheduler.run_once() == 0
    assert set(enqueued) == {
        ("reconciliation", fresh["outbox_id"]),
        ("reconciliation", legacy["outbox_id"]),
        ("reconciliation", accepted["outbox_id"]),
    }


def test_scheduler_fails_when_repair_queue_is_unavailable(monkeypatch):
    from podcaster import distribution_scheduler

    class Repository:
        def __init__(self, storage):
            pass

        def repair_notifications_page(self, notify, **kwargs):
            notify("a" * 64, lambda: True, lambda: True)
            raise AssertionError("repair callback must fail closed")

    class Storage:
        def get_bytes(self, path):
            return None

        def put_bytes(self, path, content, content_type):
            raise AssertionError("failed repair must not advance scheduler state")

    monkeypatch.setattr(distribution_scheduler, "DistributionOutboxRepository", Repository)
    monkeypatch.setattr(distribution_scheduler, "create_storage_backend", Storage)
    monkeypatch.setattr(
        distribution_scheduler,
        "enqueue_distribution_job",
        lambda *args, **kwargs: False,
    )

    with pytest.raises(RuntimeError, match="repair queue is unavailable"):
        distribution_scheduler.run_once()


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


def test_unapproved_youtube_never_inserts_playlist_or_promotes(tmp_path, monkeypatch):
    storage, _document, message = _setup(
        tmp_path,
        {"youtube": "public"},
        approved=False,
        context={"youtube": {"locale": "en", "playlist_id": "PLshow"}},
    )
    calls = []
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._youtube_reconcile_create",
        lambda *args, **kwargs: ("found", "video-1"),
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
        "podcaster.distribution_worker.playlist_contains_video",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.add_video_to_playlist",
        lambda *args, **kwargs: calls.append("playlist"),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.publish_video",
        lambda *args, **kwargs: calls.append("promote"),
    )

    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(
            youtube_enabled=True,
            youtube_playlist_id="PLshow",
        ),
    )

    assert calls == []
    assert final["providers"]["youtube"]["result"] == "manual_handoff_required"
    assert final["providers"]["youtube"]["intent"] is None


def test_youtube_first_create_reconciles_before_consuming_intent(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"youtube": "public"})
    calls = []
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._youtube_reconcile_create",
        lambda *args, **kwargs: (calls.append("reconcile") or "found", "video-1"),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_to_youtube",
        lambda *args, **kwargs: calls.append("upload"),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.get_video_snippet",
        lambda *args, **kwargs: {
            "uploadStatus": "processed",
            "processingStatus": "succeeded",
            "privacyStatus": "public",
        },
    )

    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )

    assert calls == ["reconcile"]
    assert final["aggregate"]["externally_verified_public"] is True
    assert final["providers"]["youtube"]["intent"] is None


def test_approved_playlist_is_inserted_and_read_back_before_public_success(
    tmp_path,
    monkeypatch,
):
    storage, _document, message = _setup(
        tmp_path,
        {"youtube": "public"},
        context={"youtube": {"locale": "en", "playlist_id": "PLshow"}},
    )
    membership = iter([False, True])
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._youtube_reconcile_create",
        lambda *args, **kwargs: ("found", "video-1"),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.get_video_snippet",
        lambda *args, **kwargs: {
            "uploadStatus": "processed",
            "processingStatus": "succeeded",
            "privacyStatus": "public",
        },
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.playlist_contains_video",
        lambda *args, **kwargs: next(membership),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.add_video_to_playlist",
        lambda *args, **kwargs: SimpleNamespace(succeeded=True),
    )

    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(
            youtube_enabled=True,
            youtube_playlist_id="PLshow",
        ),
    )

    assert final["providers"]["youtube"]["intent"]["operation"] == "playlist_insert"
    assert final["aggregate"]["externally_verified_public"] is True


def test_spotify_upload_and_promotion_are_fenced_and_externally_verified(
    tmp_path,
    monkeypatch,
):
    storage, _document, message = _setup(
        tmp_path,
        {"spotify": "public"},
        context={"spotify": {"audio_anchor_id": 42}},
    )
    states = iter([False, True])
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_video_to_episode",
        lambda *args, **kwargs: SimpleNamespace(status="draft", anchor_episode_id=777),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.read_spotify_video_publication_state",
        lambda *args, **kwargs: next(states),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.promote_spotify_video_draft",
        lambda *args, **kwargs: SimpleNamespace(terminal_state="published"),
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

    assert final["aggregate"]["externally_verified_public"] is True
    assert final["providers"]["spotify"]["intent"]["operation"] == "public_promotion"
    assert final["providers"]["spotify"]["intent_history"][0]["operation"] == (
        "create_episode_intent"
    )


def test_spotify_rss_requires_immutable_media_and_external_feed_readback(
    tmp_path,
    monkeypatch,
):
    storage, document, message = _setup(
        tmp_path,
        {"spotify_rss": "public"},
        context={"spotify_rss": {"feed_path": "feeds/video.xml"}},
    )
    media_url = (
        f"https://cdn.example/distribution-artifacts/video/{document['artifact']['sha256']}.mp4"
    )
    feeds = iter(
        [
            None,
            f"""<rss><channel><item>
<guid isPermaLink="false">{document["outbox_id"]}</guid>
<enclosure url="{media_url}" />
</item></channel></rss>""",
        ]
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._verify_public_media",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._read_public_feed",
        lambda *args, **kwargs: next(feeds),
    )

    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(
            spotify_rss_enabled=True,
            spotify_rss_feed_path="feeds/video.xml",
            spotify_rss_public_media_origin="https://cdn.example",
            spotify_rss_public_feed_url="https://feeds.example/video.xml",
        ),
    )

    assert final["aggregate"]["externally_verified_public"] is True
    assert final["providers"]["spotify_rss"]["intent"]["operation"] == "rss_feed_publish"


def test_spotify_rss_redelivery_after_accepted_update_is_readback_only(
    tmp_path,
    monkeypatch,
):
    storage, _document, message = _setup(
        tmp_path,
        {"spotify_rss": "public"},
        context={"spotify_rss": {"feed_path": "feeds/video.xml"}},
    )
    updates = 0
    original_update = storage.update_bytes

    def counted_update(path, content_type, updater):
        nonlocal updates
        if path == "feeds/video.xml":
            updates += 1
        return original_update(path, content_type, updater)

    storage.update_bytes = counted_update
    monkeypatch.setattr(
        "podcaster.distribution_worker._verify_public_media",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._read_public_feed",
        lambda *args, **kwargs: None,
    )
    config = VideoDistributionConfig(
        spotify_rss_enabled=True,
        spotify_rss_feed_path="feeds/video.xml",
        spotify_rss_public_media_origin="https://cdn.example",
        spotify_rss_public_feed_url="https://feeds.example/video.xml",
    )

    first = process_message(message, queue=Queue(), storage=storage, config=config)
    second = process_message(message, queue=Queue(), storage=storage, config=config)

    assert first["providers"]["spotify_rss"]["result"] == "pending_provider"
    assert second["providers"]["spotify_rss"]["result"] == "pending_provider"
    assert updates == 1


def test_spotify_rss_unconfigured_origin_fails_closed_without_feed_mutation(tmp_path):
    storage, _document, message = _setup(tmp_path, {"spotify_rss": "public"})
    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(
            spotify_rss_enabled=True,
            spotify_rss_feed_path="feeds/video.xml",
        ),
    )
    assert final["providers"]["spotify_rss"]["result"] == "manual_handoff_required"
    assert final["providers"]["spotify_rss"]["intent"] is None


def test_spotify_rss_signed_or_query_origin_fails_closed(tmp_path):
    storage, _document, message = _setup(tmp_path, {"spotify_rss": "public"})
    final = process_message(
        message,
        queue=Queue(),
        storage=storage,
        config=VideoDistributionConfig(
            spotify_rss_enabled=True,
            spotify_rss_feed_path="feeds/video.xml",
            spotify_rss_public_media_origin="https://cdn.example?sig=ephemeral",
            spotify_rss_public_feed_url="https://feeds.example/video.xml?sig=ephemeral",
        ),
    )
    assert final["providers"]["spotify_rss"]["result"] == "manual_handoff_required"
    assert final["providers"]["spotify_rss"]["intent"] is None


def test_malformed_distribution_message_is_safely_discarded(tmp_path):
    storage, _document, _message = _setup(tmp_path, {"youtube": "public"})
    malformed = QueueMessage("bad", "r1", "not-json", 1)
    queue = Queue()
    final = process_message(
        malformed,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(),
    )
    assert final["state"] == "malformed_discarded"
    assert queue.deleted == [malformed]


def test_distribution_poison_exhaustion_is_durable_and_deleted(tmp_path, monkeypatch):
    storage, _document, message = _setup(tmp_path, {"youtube": "public"})
    poison = QueueMessage(message.message_id, message.pop_receipt, message.body, 5)
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._youtube_reconcile_create",
        lambda *args, **kwargs: ("absent", None),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_to_youtube",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider failed")),
    )
    queue = Queue()

    final = process_message(
        poison,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )

    assert final["providers"]["youtube"]["result"] == "poisoned"
    assert final["providers"]["youtube"]["verification"]["exhaustion_reason"] == (
        "maximum_dequeue_count_exhausted"
    )
    assert queue.deleted == [poison]


def test_distribution_poison_exhaustion_reclaims_after_lease_loss(tmp_path, monkeypatch):
    storage, document, message = _setup(tmp_path, {"youtube": "public"})
    poison = QueueMessage(message.message_id, message.pop_receipt, message.body, 5)
    clock = [datetime(2026, 9, 22, 17, 0, tzinfo=timezone.utc)]
    repository_type = DistributionOutboxRepository
    monkeypatch.setattr(
        "podcaster.distribution_worker.DistributionOutboxRepository",
        lambda storage: repository_type(storage, now=lambda: clock[0]),
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._get_youtube_access_token",
        lambda config, transport: "token",
    )
    monkeypatch.setattr(
        "podcaster.distribution_worker._youtube_reconcile_create",
        lambda *args, **kwargs: ("absent", None),
    )

    def fail_after_lease_loss(*args, **kwargs):
        clock[0] += timedelta(seconds=901)
        raise RuntimeError("provider failed")

    monkeypatch.setattr(
        "podcaster.distribution_worker.upload_to_youtube",
        fail_after_lease_loss,
    )
    queue = Queue()

    final = process_message(
        poison,
        queue=queue,
        storage=storage,
        config=VideoDistributionConfig(youtube_enabled=True),
    )

    persisted = repository_type(storage).read(document["outbox_id"])
    assert persisted is not None
    assert final["providers"]["youtube"]["result"] == "poisoned"
    assert final["providers"]["youtube"]["verification"]["fencing_token"] == 2
    assert final["providers"]["youtube"]["verification"]["exhaustion_reason"] == (
        "maximum_dequeue_count_exhausted"
    )
    assert final["attempt_count"] == 2
    assert final["claim"] is None
    assert queue.deleted == [poison]
