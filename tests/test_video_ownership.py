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
from podcaster.publication_state import PublicationIdentity
from podcaster.storage import LocalStorageBackend
from podcaster.video import distribution
from podcaster.video.distribution import VideoDistributionConfig, distribute_video
from podcaster.video.job_runner import _record_video_state, manifest_path
from podcaster.video.ownership import (
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
    repository.mark_notification_sent(
        document["outbox_id"],
        source_ownership=notification_token,
        authorize=lambda: guard.assert_permit(notification),
    )
    assert storage.get_bytes(outbox_path(document["outbox_id"])) is not None


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
