from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from podcaster.distribution_outbox import (
    DistributionOutboxRepository,
    commit_immutable_artifact,
    outbox_path,
    provider_approval_is_valid,
)
from podcaster.job_logs import logs_path
from podcaster.publication_state import PublicationIdentity, evidence_path
from podcaster.queue import enqueue_distribution_job
from podcaster.storage import LocalStorageBackend
from podcaster.video import distribution
from podcaster.video.distribution import VideoDistributionConfig, distribute_video
from podcaster.video.job_runner import (
    _record_video_publication,
    _record_video_state,
    manifest_path,
)
from podcaster.video.ownership import (
    OwnershipClaim,
    OwnershipError,
    VideoOwnershipGuard,
    ownership_path,
)
from podcaster.video.video_compose import _finalize_output


def _storage(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(tmp_path / "storage", "https://blob.example")


def _claim(
    storage: LocalStorageBackend,
    *,
    job_id: str = "job",
    execution_id: str = "execution-1",
    now: datetime | None = None,
) -> VideoOwnershipGuard:
    current = now or datetime.now(timezone.utc)
    return VideoOwnershipGuard.acquire(
        storage,
        job_id,
        owner="video-runner",
        execution_id=execution_id,
        visibility_expires_at=current + timedelta(minutes=10),
        lease_expires_at=current + timedelta(minutes=5),
        now=lambda: current,
    )


def _force_takeover(storage: LocalStorageBackend, job_id: str) -> None:
    path = ownership_path(job_id)

    def replace(raw: bytes | None) -> bytes:
        assert raw is not None
        document = json.loads(raw.decode("utf-8"))
        fence = int(document["fencing_token"]) + 1
        document["fencing_token"] = fence
        document["claim"] = {
            **document["claim"],
            "owner": "successor",
            "claim_id": "successor-claim",
            "execution_id": "successor-execution",
            "fencing_token": fence,
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

    storage.update_bytes(path, "application/json", replace)


def _successor_guard(
    storage: LocalStorageBackend,
    previous: VideoOwnershipGuard,
) -> VideoOwnershipGuard:
    raw = storage.get_bytes(ownership_path(previous.claim.job_id))
    assert raw is not None
    current = json.loads(raw.decode("utf-8"))["claim"]
    return VideoOwnershipGuard(
        storage,
        OwnershipClaim(
            job_id=previous.claim.job_id,
            owner=str(current["owner"]),
            claim_id=str(current["claim_id"]),
            execution_id=str(current["execution_id"]),
            fencing_token=int(current["fencing_token"]),
        ),
        now=previous.now,
    )


def _notification_outbox(
    tmp_path: Path,
) -> tuple[
    LocalStorageBackend,
    VideoOwnershipGuard,
    DistributionOutboxRepository,
    str,
]:
    storage = _storage(tmp_path)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x" * 2048)
    guard = _claim(storage)
    artifact = commit_immutable_artifact(
        storage,
        media,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
    )
    repository = DistributionOutboxRepository(storage)
    document, _created = repository.enqueue(
        PublicationIdentity("job", "2026-W39", "1", "a" * 64, "b" * 64),
        artifact,
        provider_objectives={"youtube": "public"},
        enqueue_source="test",
        enqueue_version="v1",
    )
    return storage, guard, repository, document["outbox_id"]


def test_guard_fails_closed_for_transfer_expiry_corruption_and_unavailable_readback(tmp_path):
    storage = _storage(tmp_path)
    current = datetime.now(timezone.utc)
    guard = _claim(storage, now=current)

    _force_takeover(storage, "job")
    with pytest.raises(OwnershipError, match="stale"):
        guard.assert_current()

    expired = _claim(storage, job_id="expired", now=current)
    expired.now = lambda: current + timedelta(minutes=6)
    with pytest.raises(OwnershipError, match="expired"):
        expired.assert_current()

    storage.put_bytes(ownership_path("corrupt"), b"{", "application/json")
    corrupt_claim = _claim(storage, job_id="fresh", now=current).claim
    corrupt_guard = VideoOwnershipGuard(storage, corrupt_claim, now=lambda: current)
    corrupt_guard.claim = type(corrupt_claim)(
        job_id="corrupt",
        owner=corrupt_claim.owner,
        claim_id=corrupt_claim.claim_id,
        execution_id=corrupt_claim.execution_id,
        fencing_token=corrupt_claim.fencing_token,
    )
    with pytest.raises(OwnershipError, match="corrupt"):
        corrupt_guard.assert_current()

    storage.delete_blob(ownership_path("fresh"))
    with pytest.raises(OwnershipError, match="unavailable"):
        VideoOwnershipGuard(storage, corrupt_claim, now=lambda: current).assert_current()


def test_transfer_between_guard_and_target_write_rejects_stale_permit(tmp_path):
    storage = _storage(tmp_path)
    guard = _claim(storage)
    permit = guard.begin("immutable_archive", allow_idempotent_takeover=True)
    _force_takeover(storage, "job")

    with pytest.raises(OwnershipError, match="stale"):
        guard.assert_permit(permit)
    with pytest.raises(OwnershipError, match="stale"):
        guard.complete(permit, target="archive")


@pytest.mark.parametrize(
    "boundary",
    [
        "final_candidate_promotion",
        "immutable_archive",
        "distribution_outbox",
        "distribution_notification",
        "direct_provider_intent",
        "terminal_success",
    ],
)
def test_expiry_before_each_boundary_denies_mutation(tmp_path, boundary):
    storage = _storage(tmp_path)
    current = datetime.now(timezone.utc)
    guard = _claim(storage, job_id=boundary, now=current)
    guard.now = lambda: current + timedelta(minutes=6)

    with pytest.raises(OwnershipError, match="expired"):
        guard.begin(boundary, allow_idempotent_takeover=True)


def test_concurrent_workers_have_one_active_owner(tmp_path):
    storage = _storage(tmp_path)
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def acquire(execution_id: str) -> None:
        barrier.wait()
        try:
            _claim(storage, execution_id=execution_id)
        except OwnershipError:
            outcome = "denied"
        else:
            outcome = "winner"
        with lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=acquire, args=("execution-a",)),
        threading.Thread(target=acquire, args=("execution-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["denied", "winner"]


def test_takeover_reconciles_idempotent_boundary_but_not_provider_mutation(tmp_path):
    storage = _storage(tmp_path)
    first = _claim(storage)
    archive = first.begin("immutable_archive", allow_idempotent_takeover=True)
    first.complete(archive, target="artifact")
    provider = first.begin("direct_provider_intent", allow_idempotent_takeover=False)
    first.complete(provider, target="provider")

    current = datetime.now(timezone.utc)

    def expire(raw: bytes | None) -> bytes:
        document = json.loads(raw.decode("utf-8"))
        document["claim"]["visibility_expires_at"] = (current - timedelta(minutes=1)).isoformat()
        document["claim"]["lease_expires_at"] = (current - timedelta(minutes=1)).isoformat()
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

    storage.update_bytes(ownership_path("job"), "application/json", expire)
    successor = _claim(storage, execution_id="execution-2", now=current)
    adopted = successor.begin("immutable_archive", allow_idempotent_takeover=True)
    assert adopted.reconcile_only is False
    successor.assert_permit(adopted)

    read_only = successor.begin("direct_provider_intent", allow_idempotent_takeover=False)
    assert read_only.reconcile_only is True
    with pytest.raises(OwnershipError, match="reconciliation-only"):
        successor.assert_permit(read_only)


def test_archive_outbox_and_notification_writes_consume_source_permit(tmp_path):
    storage = _storage(tmp_path)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x" * 2048)
    guard = _claim(storage)

    archive = guard.begin("immutable_archive", allow_idempotent_takeover=True)
    token = guard.source_token(archive)
    artifact = commit_immutable_artifact(
        storage,
        media,
        media_kind="video",
        content_type="video/mp4",
        suffix=".mp4",
        source_ownership=token,
        authorize=lambda: guard.assert_permit(archive),
    )
    guard.complete(archive, target=artifact.path)

    outbox = guard.begin("distribution_outbox", allow_idempotent_takeover=True)
    outbox_token = guard.source_token(outbox)
    repository = DistributionOutboxRepository(storage)
    document, created = repository.enqueue(
        PublicationIdentity("job", "2026-W39", "1", "a" * 64, "b" * 64),
        artifact,
        provider_objectives={"youtube": "public"},
        enqueue_source="test",
        enqueue_version="v1",
        source_ownership=outbox_token,
        authorize=lambda: guard.assert_permit(outbox),
        provider_approvals={
            "youtube": {
                "approved": True,
                "approved_by": "operator",
                "approved_at": "2026-09-22T17:00:00Z",
                "source": "manifest_human_review",
            }
        },
        provider_context={"youtube": {"playlist_id": "playlist-1"}},
    )
    assert created is True
    assert document["source_ownership"] == outbox_token
    assert provider_approval_is_valid(document, "youtube") is True
    assert document["providers"]["youtube"]["context"]["playlist_id"] == "playlist-1"
    guard.complete(outbox, target=document["outbox_id"])

    notification = guard.begin("notification", allow_idempotent_takeover=True)
    notification_token = guard.source_token(notification)
    repository.reserve_notification(
        document["outbox_id"],
        source_ownership=notification_token,
        authorize=lambda: guard.assert_permit(notification),
    )
    assert (
        repository.consume_notification_intent(
            document["outbox_id"],
            source_ownership=notification_token,
            authorize=lambda: guard.assert_permit(notification),
        )
        is True
    )
    assert (
        repository.consume_notification_intent(
            document["outbox_id"],
            source_ownership=notification_token,
            authorize=lambda: guard.assert_permit(notification),
        )
        is False
    )
    assert storage.get_bytes(outbox_path(document["outbox_id"])) is not None


def test_takeover_at_distribution_send_boundary_selects_one_successor(tmp_path):
    storage, guard, repository, outbox_id = _notification_outbox(tmp_path)
    permit = guard.begin("distribution_notification", allow_idempotent_takeover=True)
    token = guard.source_token(permit)
    repository.reserve_notification(
        outbox_id,
        source_ownership=token,
        authorize=lambda: guard.assert_permit(permit),
    )

    class Producer:
        def __init__(self):
            self.messages: list[str] = []

        def send_message(self, body: str) -> None:
            self.messages.append(body)

    stale_producer = Producer()

    def lose_at_send() -> bool:
        _force_takeover(storage, "job")
        return repository.consume_notification_intent(
            outbox_id,
            source_ownership=token,
            authorize=lambda: guard.assert_permit(permit),
        )

    with pytest.raises(OwnershipError, match="stale"):
        enqueue_distribution_job(
            outbox_id,
            producer=stale_producer,
            authorize_send=lose_at_send,
        )
    assert stale_producer.messages == []

    successor = _successor_guard(storage, guard)
    successor_permit = successor.begin(
        "distribution_notification",
        allow_idempotent_takeover=True,
    )
    successor_token = successor.source_token(successor_permit)
    repository.reserve_notification(
        outbox_id,
        source_ownership=successor_token,
        authorize=lambda: successor.assert_permit(successor_permit),
    )
    winner = Producer()
    assert (
        enqueue_distribution_job(
            outbox_id,
            producer=winner,
            authorize_send=lambda: repository.consume_notification_intent(
                outbox_id,
                source_ownership=successor_token,
                authorize=lambda: successor.assert_permit(successor_permit),
            ),
        )
        is True
    )
    assert len(winner.messages) == 1


def test_crash_after_notification_intent_consumption_cannot_replay_send(tmp_path):
    storage, guard, repository, outbox_id = _notification_outbox(tmp_path)
    permit = guard.begin("distribution_notification", allow_idempotent_takeover=True)
    token = guard.source_token(permit)
    repository.reserve_notification(
        outbox_id,
        source_ownership=token,
        authorize=lambda: guard.assert_permit(permit),
    )

    class CrashingProducer:
        def send_message(self, body: str) -> None:
            raise RuntimeError("crash before queue accepted message")

    with pytest.raises(RuntimeError, match="crash before queue"):
        enqueue_distribution_job(
            outbox_id,
            producer=CrashingProducer(),
            authorize_send=lambda: repository.consume_notification_intent(
                outbox_id,
                source_ownership=token,
                authorize=lambda: guard.assert_permit(permit),
            ),
        )

    consumed = repository.read(outbox_id)["enqueue"]["notification_intent"]
    assert consumed["consumed_at"] is not None

    _force_takeover(storage, "job")
    successor = _successor_guard(storage, guard)
    successor_permit = successor.begin(
        "distribution_notification",
        allow_idempotent_takeover=True,
    )
    successor_token = successor.source_token(successor_permit)
    repository.reserve_notification(
        outbox_id,
        source_ownership=successor_token,
        authorize=lambda: successor.assert_permit(successor_permit),
    )

    replayed: list[str] = []

    class ReplayProducer:
        def send_message(self, body: str) -> None:
            replayed.append(body)

    assert (
        enqueue_distribution_job(
            outbox_id,
            producer=ReplayProducer(),
            authorize_send=lambda: repository.consume_notification_intent(
                outbox_id,
                source_ownership=successor_token,
                authorize=lambda: successor.assert_permit(successor_permit),
            ),
        )
        is True
    )
    assert replayed == []
    assert repository.read(outbox_id)["enqueue"]["notification_intent"] == consumed


def test_provider_mutation_consumes_durable_permit_and_takeover_is_read_only(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x" * 2048)
    guard = _claim(storage)
    permit = guard.begin("direct_provider_intent", allow_idempotent_takeover=False)
    upload = MagicMock(return_value=("video-id", "https://youtube.example/video-id"))
    monkeypatch.setattr(distribution, "upload_to_youtube", upload)

    result = distribute_video(
        media,
        "job",
        "title",
        "description",
        60,
        VideoDistributionConfig(
            youtube_enabled=True,
            blob_archive_enabled=False,
            dry_run=False,
        ),
        before_mutation=lambda _provider, _operation: guard.assert_permit(permit),
    )
    assert result.youtube_id == "video-id"
    upload.assert_called_once()

    _force_takeover(storage, "job")
    stale_result = distribute_video(
        media,
        "job",
        "title",
        "description",
        60,
        VideoDistributionConfig(
            youtube_enabled=True,
            blob_archive_enabled=False,
            dry_run=False,
        ),
        before_mutation=lambda _provider, _operation: guard.assert_permit(permit),
    )
    assert stale_result.status == "failed"
    assert stale_result.youtube_id is None
    upload.assert_called_once()


@pytest.mark.parametrize(
    ("takeover_boundary", "manifest_written", "evidence_written"),
    [
        (1, False, False),
        (2, True, False),
        (3, True, True),
    ],
)
def test_provider_return_takeover_fences_every_publication_write(
    tmp_path,
    monkeypatch,
    takeover_boundary,
    manifest_written,
    evidence_written,
):
    job_id = f"provider-boundary-{takeover_boundary}"
    storage = _storage(tmp_path)
    storage.put_bytes(manifest_path(job_id), b'{"generation":{}}', "application/json")
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x" * 2048)
    guard = _claim(storage, job_id=job_id)
    permit = guard.begin("direct_provider_intent", allow_idempotent_takeover=False)
    upload = MagicMock(return_value=("video-id", "https://youtube.example/video-id"))
    monkeypatch.setattr(distribution, "upload_to_youtube", upload)
    authorization_count = 0

    def authorize_persistence() -> None:
        nonlocal authorization_count
        authorization_count += 1
        if authorization_count == takeover_boundary:
            _force_takeover(storage, job_id)
        guard.assert_permit(permit)

    identity = PublicationIdentity(job_id, "2026-W39", "1", "a" * 64, "b" * 64)

    with pytest.raises(OwnershipError, match="stale"):
        distribute_video(
            media,
            job_id,
            "title",
            "description",
            60,
            VideoDistributionConfig(
                youtube_enabled=True,
                blob_archive_enabled=False,
                dry_run=False,
            ),
            before_mutation=lambda _provider, _operation: guard.assert_permit(permit),
            on_published=lambda platform, record: _record_video_publication(
                storage,
                job_id,
                identity,
                platform,
                record,
                authorize=authorize_persistence,
            ),
        )

    upload.assert_called_once()
    manifest = json.loads(storage.get_bytes(manifest_path(job_id)).decode("utf-8"))
    assert ("video_publish" in manifest["generation"]) is manifest_written
    assert (storage.get_bytes(evidence_path(job_id)) is not None) is evidence_written
    assert storage.get_bytes(logs_path(job_id)) is None


def test_takeover_fences_unknown_snapshot_after_evidence_failure(tmp_path, monkeypatch):
    from podcaster.video import job_runner

    job_id = "provider-fallback-boundary"
    storage = _storage(tmp_path)
    storage.put_bytes(manifest_path(job_id), b'{"generation":{}}', "application/json")
    guard = _claim(storage, job_id=job_id)
    permit = guard.begin("direct_provider_intent", allow_idempotent_takeover=False)
    authorization_count = 0

    def authorize_persistence() -> None:
        nonlocal authorization_count
        authorization_count += 1
        if authorization_count == 2:
            _force_takeover(storage, job_id)
        guard.assert_permit(permit)

    monkeypatch.setattr(
        job_runner,
        "append_evidence",
        MagicMock(side_effect=RuntimeError("evidence unavailable")),
    )
    with pytest.raises(OwnershipError, match="stale"):
        _record_video_publication(
            storage,
            job_id,
            PublicationIdentity(job_id, "2026-W39", "1", "a" * 64, "b" * 64),
            "youtube",
            {
                "status": "published",
                "outcome": "draft_created",
                "video_id": "video-id",
            },
            authorize=authorize_persistence,
        )

    manifest = json.loads(storage.get_bytes(manifest_path(job_id)).decode("utf-8"))
    assert manifest["generation"]["video_publish"]["youtube"]["outcome"] == "draft_created"
    assert storage.get_bytes(logs_path(job_id)) is None


def test_promotion_and_terminal_success_fail_closed_after_transfer(tmp_path):
    storage = _storage(tmp_path)
    guard = _claim(storage)
    promotion = guard.begin("final_candidate_promotion", allow_idempotent_takeover=True)
    video_only = tmp_path / "video-only.mp4"
    output = tmp_path / "final.mp4"
    video_only.write_bytes(b"source")

    def run(command):
        if command[0] == "ffprobe":
            return MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [{"codec_type": "video"}],
                        "format": {"duration": "1"},
                    }
                ),
                stderr="",
            )
        Path(command[-1]).write_bytes(b"candidate")
        return MagicMock(returncode=0, stdout="", stderr="")

    _force_takeover(storage, "job")
    with pytest.raises(OwnershipError, match="stale"):
        _finalize_output(
            video_only_path=video_only,
            video_duration=1,
            audio_path=None,
            output_path=output,
            segment_count=1,
            run=run,
            decode=lambda _path: None,
            before_final_promotion=lambda: guard.assert_permit(promotion),
        )
    assert not output.exists()

    storage.put_bytes(manifest_path("job"), b'{"generation":{}}', "application/json")
    with pytest.raises(OwnershipError, match="stale"):
        _record_video_state(
            storage,
            "job",
            {"status": "completed"},
            authorize=lambda: guard.assert_permit(promotion),
            fail_closed=True,
        )
    manifest = json.loads(storage.get_bytes(manifest_path("job")).decode("utf-8"))
    assert "video_runner" not in manifest["generation"]
