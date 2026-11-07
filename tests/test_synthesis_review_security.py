from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Condition, Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from review_support import approval_evidence_for

from podcaster.distribution_outbox import (
    DistributionOutboxRepository,
    commit_immutable_artifact,
    provider_approval_is_valid,
)
from podcaster.job_runner import STATUS_COMPLETED, run_synthesis
from podcaster.orchestration import load_manifest, process_review_decision
from podcaster.publication_state import PublicationIdentity
from podcaster.review import ReviewBindingError, apply_review_decision
from podcaster.storage import LocalStorageBackend
from podcaster.tts import load_tts_config
from podcaster.video.job_runner import _distribution_human_approval
from tests.test_job_runner import _patch_audio
from tests.test_orchestration import _stage, _synthesized_manifest


def _approve(storage, result, **overrides):
    evidence = {**approval_evidence_for(result.manifest), **overrides}
    return process_review_decision(
        result.response["job_id"],
        storage=storage,
        reviewer="test-reviewer",
        reviewed_at="2026-10-29T11:05:00Z",
        decision="approved",
        **evidence,
    ).manifest


@pytest.mark.parametrize("boundary", ["video_queue", "video_distribution_approval"])
def test_canonical_post_audio_review_never_authorizes_unseen_video(
    reviewed_stage, monkeypatch, boundary
):
    storage, result, _generator, _producer, _script = reviewed_stage
    _approve(storage, result)
    job_id = result.response["job_id"]
    video = Mock()
    publish = Mock(side_effect=AssertionError("unexpected provider publication"))
    monkeypatch.setattr("podcaster.job_runner._enqueue_video", video)
    monkeypatch.setattr("podcaster.orchestration.publish_staged_job", publish)
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "true")
    _patch_audio(monkeypatch)
    synthesized = run_synthesis(
        job_id,
        storage,
        load_tts_config(),
        token_provider=lambda scope: "offline-token",
        transport=lambda request: b"offline-segment",
    )
    assert synthesized.status == STATUS_COMPLETED
    manifest = load_manifest(storage, job_id)
    assert manifest["generation"]["audio_mode"] == "synthesized"
    assert "video_runner" not in manifest["generation"]
    assert manifest["review"]["snapshot"]
    # This queue now requests private preparation only, never provider dispatch.
    video.assert_called_once_with(job_id, None)

    outcome = process_review_decision(
        job_id,
        storage=storage,
        reviewer="test-audio-reviewer",
        reviewed_at="2026-10-29T11:10:00Z",
        decision="approved",
        publish_on_approval=False,
        publish_video_on_approval=True,
    )

    persisted = load_manifest(storage, job_id)
    assert persisted == outcome.manifest
    assert persisted["generation"]["audio_mode"] == "synthesized"
    assert "video_runner" not in persisted["generation"]
    publish.assert_not_called()
    if boundary == "video_queue":
        video.assert_called_once_with(job_id, None)
    else:
        assert _distribution_human_approval(persisted) == {"approved": False}


FROZEN_NOW = datetime(2026, 10, 29, 11, 30, tzinfo=timezone.utc)


@pytest.fixture
def prepared_exact_video(reviewed_stage, monkeypatch, tmp_path, request):
    """Run real approval/synthesis/preparation against private local contracts."""
    from podcaster.generation import manifest_bytes
    from podcaster.review import content_snapshot_sha256
    from podcaster.video.distribution import VideoDistributionConfig
    from podcaster.video.job_runner import run_video_generation

    storage, result, *_ = reviewed_stage
    # The reviewed manifest expires at a fixed instant, while the outbox's
    # 24h verification horizon derives from its clock; freeze that clock
    # (including the default bound at class definition) off the wall clock.
    from podcaster import distribution_outbox

    frozen = FROZEN_NOW
    monkeypatch.setattr(distribution_outbox, "utc_now", lambda: frozen)
    monkeypatch.setattr(
        DistributionOutboxRepository.__init__,
        "__kwdefaults__",
        {"now": lambda: frozen},
    )
    parameters = getattr(request, "param", "youtube")
    target = parameters["target"] if isinstance(parameters, dict) else parameters
    video_bytes = b"V" * (2 * 1024 * 1024 if target == "youtube_chunked" else 2048)
    job_id = result.response["job_id"]
    manifest = result.manifest
    manifest["request"].update(
        publication_identity_mode="canonical",
        publish_run_id="40269058395",
        article_sha256="a" * 64,
        manifest_sha256="b" * 64,
        article_title="Editorial episode title",
        article_summary="A reviewed technical summary.",
        description_template="Music: Licensed offline theme — https://credits.example/theme",
    )
    if isinstance(parameters, dict):
        manifest["request"].update(parameters.get("metadata", {}))
    manifest["review"]["snapshot"]["manifest_sha256"] = content_snapshot_sha256(manifest)
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json")
    _approve(storage, result)
    _patch_audio(monkeypatch)
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "true")
    private_queue = Mock(return_value=True)
    synth = run_synthesis(
        job_id,
        storage,
        load_tts_config(),
        token_provider=lambda scope: "offline-token",
        transport=lambda request: b"offline-segment",
        enqueue_video=private_queue,
    )
    assert synth.status == STATUS_COMPLETED
    private_queue.assert_called_once_with(job_id)
    monkeypatch.setattr("podcaster.video.job_runner._probe_audio_duration", lambda _: 60.0)
    monkeypatch.setattr("podcaster.video.job_runner._resolve_dog_logo", lambda *args: None)
    monkeypatch.setattr("podcaster.video.sync_plan.check_repo_removed", lambda *args, **kw: False)
    monkeypatch.setattr(
        "podcaster.video.video_gen.record_episode", Mock(return_value=Mock(recorded=[]))
    )

    def compose(*args, output_path, **kwargs):
        proof = load_manifest(storage, job_id)["generation"]["synthesis_runner"]["audio"]
        assert kwargs["audio_path"].read_bytes() == storage.get_bytes(proof["path"])
        output_path.write_bytes(video_bytes)
        return Mock(duration_seconds=60.0, segment_count=1)

    composer = Mock(side_effect=compose)
    monkeypatch.setattr("podcaster.video.video_compose.compose_video", composer)
    provider = Mock(side_effect=AssertionError("private preparation cannot distribute"))
    notification = Mock(
        side_effect=AssertionError("private preparation cannot notify distribution")
    )
    monkeypatch.setattr("podcaster.video.job_runner.distribute_video", provider)
    monkeypatch.setattr("podcaster.video.job_runner.enqueue_distribution_job", notification)
    monkeypatch.delenv("DISTRIBUTION_OUTBOX_ENABLED", raising=False)
    if target in {"spotify_live", "both_live"}:
        monkeypatch.setenv("SPOTIFY_VIDEO_ALLOW_LIVE_PUBLISH", "true")
    if target == "spotify_override":
        monkeypatch.setenv("PODCASTER_SPOTIFY_ALLOW_UNRECONCILED_CREATE", "true")
    config = VideoDistributionConfig(
        youtube_enabled=target.startswith("youtube") or target.startswith("both"),
        spotify_upload_enabled=target.startswith("spotify") or target.startswith("both"),
        spotify_video_publish_mode="live" if target in {"spotify_live", "both_live"} else "draft",
        dry_run=False,
    )
    outcome = run_video_generation(job_id, storage, config=config, fanout=False)
    assert outcome.status == STATUS_COMPLETED
    assert outcome.distribution.public_delivery_status == "awaiting_review"
    repository = DistributionOutboxRepository(storage)
    document = repository.read(outcome.distribution.outbox_id)
    assert document["media_review"]["status"] == "pending"
    assert not provider_approval_is_valid(document, "youtube")
    provider.assert_not_called()
    notification.assert_not_called()
    assert storage.get_bytes(document["artifact"]["path"]) == video_bytes
    assert _distribution_human_approval(load_manifest(storage, job_id)) == {"approved": False}
    return storage, job_id, repository, document, config, composer


def _video_decision(storage, job_id, document, **overrides):
    return process_review_decision(
        job_id,
        storage=storage,
        reviewer="actual-offline-media-reviewer",
        reviewed_at=(
            datetime.fromisoformat(document["created_at"].replace("Z", "+00:00"))
            + timedelta(seconds=1)
        ).isoformat(),
        decision="approved",
        review_checks_confirmed=True,
        media_review={
            "outbox_id": document["outbox_id"],
            "publication_digest": document["publication_digest"],
            "artifact_sha256": document["artifact"]["sha256"],
            **overrides,
        },
    )


def _private_storage_snapshot(storage):
    return {
        str(path.relative_to(storage.root)): path.read_bytes()
        for path in storage.root.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def recovery_original(reviewed_stage, monkeypatch, request):
    """A completed exact-reviewed original with an expired first video attempt."""
    from podcaster.generation import manifest_bytes
    from podcaster.review import content_snapshot_sha256, validate_private_media_inputs
    from podcaster.video import recovery
    from podcaster.video.budget import VideoStageBudget

    storage, result, *_ = reviewed_stage
    job_id = result.response["job_id"]
    manifest = result.manifest
    original_id = getattr(request, "param", None)
    if original_id:
        old_job_id = job_id
        job_id = original_id
        for path in list(storage.root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(storage.root).as_posix()
                storage.put_bytes(
                    relative.replace(old_job_id, job_id),
                    path.read_bytes(),
                    "application/octet-stream",
                )
        rebound = json.loads(json.dumps(manifest).replace(old_job_id, job_id))
        manifest.clear()
        manifest.update(rebound)
        result.response["job_id"] = job_id
        identity = recovery.scoped_identity(job_id)
        manifest["request"]["week"] = identity.week
    manifest["request"].update(
        publication_identity_mode="canonical",
        publish_run_id=identity.publish_run_id if original_id else "40269058395",
        article_sha256=identity.article_sha256 if original_id else "a" * 64,
        manifest_sha256=identity.manifest_sha256 if original_id else "b" * 64,
    )
    manifest["review"]["snapshot"]["manifest_sha256"] = content_snapshot_sha256(manifest)
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json")
    _approve(storage, result)
    _patch_audio(monkeypatch)
    if original_id:

        def render(segments, wav_out, out, **kwargs):
            wav_out.write_bytes(b"W" * 512)
            out.write_bytes(b"M" * recovery.PRIVATE_ORIGINALS[job_id])
            return wav_out, out

        monkeypatch.setattr("podcaster.episode.render_distribution_audio", render)
    assert (
        run_synthesis(
            job_id,
            storage,
            load_tts_config(),
            token_provider=lambda scope: "offline-token",
            transport=lambda request: b"offline-segment",
            enqueue_video=Mock(return_value=True),
        ).status
        == STATUS_COMPLETED
    )
    if not original_id:
        monkeypatch.setattr(recovery, "private_original", lambda job, size=None: job == job_id)
    at = datetime.now(timezone.utc)
    previous = VideoStageBudget.start(
        now_utc=at - timedelta(seconds=5400),
        utcnow=lambda: at - timedelta(seconds=5400),
    )
    storage.put_bytes(
        f"jobs/{job_id}/video-budget.json",
        json.dumps(previous.to_dict()).encode(),
        "application/json",
    )
    current = load_manifest(storage, job_id)
    identity = PublicationIdentity(
        job_id,
        current["request"]["week"],
        current["request"]["publish_run_id"],
        current["request"]["article_sha256"],
        current["request"]["manifest_sha256"],
    )
    audio = validate_private_media_inputs(current, storage, job_id)
    return storage, job_id, identity, sha256(audio.content).hexdigest(), at


def test_recovery_denies_bad_identity_audio_and_unknown_claim_before_reservation(recovery_original):
    from podcaster.generation import manifest_bytes
    from podcaster.video.recovery import RecoveryDenied, reserve_epoch

    storage, job_id, identity, mp3_sha, at = recovery_original
    path = f"jobs/{job_id}/manifest.json"
    original = storage.get_bytes(path)
    for wrong_identity, wrong_audio in (
        (
            PublicationIdentity(
                job_id,
                "2026-W45",
                identity.publish_run_id,
                identity.article_sha256,
                identity.manifest_sha256,
            ),
            mp3_sha,
        ),
        (identity, "f" * 64),
    ):
        with pytest.raises(RecoveryDenied):
            reserve_epoch(
                storage,
                job_id,
                expected_identity=wrong_identity,
                expected_audio_sha256=wrong_audio,
                inventory_reference="inventory-2026-11-05",
                now=lambda: at,
            )
        assert storage.get_bytes(path) == original
    manifest = json.loads(original)
    manifest["generation"]["synthesis_claim"]["status"] = "unknown"
    storage.put_bytes(path, manifest_bytes(manifest), "application/json")
    with pytest.raises(RecoveryDenied):
        reserve_epoch(
            storage,
            job_id,
            expected_identity=identity,
            expected_audio_sha256=mp3_sha,
            inventory_reference="inventory-2026-11-05",
            now=lambda: at,
        )
    assert "video_recovery" not in load_manifest(storage, job_id)["generation"]


@pytest.mark.parametrize("authentication", ["api_key", "bearer"])
def test_scoped_original_route_retries_same_epoch_without_vault(
    recovery_original, monkeypatch, authentication
):
    from podcaster import monitoring
    from podcaster.auth import create_token
    from podcaster.video import recovery

    storage, job_id, identity, _, _ = recovery_original
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, identity.manifest_sha256),
    )
    monkeypatch.setenv("PODCASTER_API_KEY", "existing-api-key")
    if authentication == "bearer":
        secret = "offline-only-jwt-secret-at-least-32-bytes"
        monkeypatch.setenv("UI_AUTH_USERNAME", "offline-user")
        monkeypatch.setenv("UI_AUTH_PASSWORD", "offline-password")
        monkeypatch.setenv("UI_AUTH_SECRET", secret)
    monkeypatch.delenv("PODCASTER_VIDEO_RECOVERY_OPERATOR_SECRET_URI", raising=False)
    monkeypatch.setattr(monitoring, "get_storage", lambda: storage)
    sent = []
    monkeypatch.setattr(
        "podcaster.queue.create_video_queue_backend", lambda: Mock(send_message=sent.append)
    )
    path = f"/api/jobs/{job_id}/video/generate"
    with TestClient(monitoring.app) as client:
        assert client.post(path).status_code == 401
        assert client.post(path, headers={"X-Podcaster-Api-Key": "wrong"}).status_code == 401
        headers = (
            {"X-Podcaster-Api-Key": "existing-api-key"}
            if authentication == "api_key"
            else {"Authorization": f"Bearer {create_token('offline-user', secret)}"}
        )
        first = client.post(path, headers=headers, json={"inventory_reference": "fake-owner"})
        assert first.status_code == 200
        record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
        assert record["authority"] == recovery.SCOPED_PRIVATE_DIRECTIVE
        assert record["inventory_reference"] == recovery.SCOPED_PRIVATE_DIRECTIVE
        second = client.post(path, headers=headers)
        assert second.status_code == 200
        assert second.json()["epoch_id"] == first.json()["epoch_id"]
    assert len(sent) == 1
    assert len(load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"]) == 1


def test_scoped_original_route_rejects_changed_identity_without_reservation(
    recovery_original, monkeypatch
):
    from podcaster import monitoring
    from podcaster.video import recovery

    storage, job_id, identity, _, _ = recovery_original
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, "a" * 64),
    )
    monkeypatch.setenv("PODCASTER_API_KEY", "existing-api-key")
    monkeypatch.setattr(monitoring, "get_storage", lambda: storage)
    backend = Mock()
    monkeypatch.setattr("podcaster.queue.create_video_queue_backend", lambda: backend)
    path = f"jobs/{job_id}/manifest.json"
    before = storage.get_bytes(path)
    with TestClient(monitoring.app) as client:
        response = client.post(
            f"/api/jobs/{job_id}/video/generate",
            headers={"X-Podcaster-Api-Key": "existing-api-key"},
        )
    assert response.status_code == 409
    assert storage.get_bytes(path) == before
    backend.send_message.assert_not_called()


@pytest.mark.parametrize("failure", ["send", "factory"])
def test_scoped_queue_failure_retries_reserved_epoch(recovery_original, monkeypatch, failure):
    from podcaster import monitoring
    from podcaster.video import recovery

    storage, job_id, identity, _, _ = recovery_original
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, identity.manifest_sha256),
    )
    monkeypatch.setenv("PODCASTER_API_KEY", "existing-api-key")
    monkeypatch.setattr(monitoring, "get_storage", lambda: storage)
    sent = []

    def fail(_body):
        raise RuntimeError("queue unavailable")

    backend = Mock(send_message=fail)

    def factory():
        if failure == "factory":
            raise RuntimeError("queue constructor failed")
        return backend

    monkeypatch.setattr("podcaster.queue.create_video_queue_backend", factory)
    with TestClient(monitoring.app) as client:
        path = f"/api/jobs/{job_id}/video/generate"
        headers = {"X-Podcaster-Api-Key": "existing-api-key"}
        first = client.post(path, headers=headers)
        assert first.status_code == (503 if failure == "factory" else 502)
        record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
        assert record["dispatch"]["status"] == "pending"
        backend.send_message = sent.append
        monkeypatch.setattr("podcaster.queue.create_video_queue_backend", lambda: backend)
        retry = client.post(path, headers=headers)
        assert retry.status_code == 200
        assert retry.json()["epoch_id"] == record["epoch_id"]
    assert len(sent) == 1
    assert len(load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"]) == 1


@pytest.mark.parametrize(
    "recovery_original",
    ["podcast-2026-W44-00bc06dfb6e2", "podcast-2026-W45-9798e93edc06"],
    indirect=True,
)
def test_scoped_original_full_identity_and_actual_mp3_binding(recovery_original):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, identity, audio_sha, at = recovery_original
    path = f"jobs/{job_id}/manifest.json"
    original = storage.get_bytes(path)
    document = json.loads(original)
    audio = document["generation"]["synthesis_runner"]["audio"]
    content = storage.get_bytes(audio["path"])
    assert len(content) == recovery.PRIVATE_ORIGINALS[job_id]
    assert sha256(content).hexdigest() == audio_sha
    assert identity == recovery.scoped_identity(job_id)
    for field in ("week", "publish_run_id", "article_sha256", "manifest_sha256"):
        changed = json.loads(original)
        changed["request"][field] = "2026-W43" if field == "week" else "0" * 64
        storage.put_bytes(path, manifest_bytes(changed), "application/json")
        with pytest.raises(recovery.RecoveryDenied):
            recovery.scoped_epoch(storage, job_id, now=lambda: at)
        assert "video_recovery" not in load_manifest(storage, job_id)["generation"]
    storage.put_bytes(path, original, "application/json")
    for changed in (content[:-1], b"X" + content[1:]):
        storage.put_bytes(audio["path"], changed, "audio/mpeg")
        with pytest.raises(recovery.RecoveryDenied):
            recovery.scoped_epoch(storage, job_id, now=lambda: at)
        assert storage.get_bytes(path) == original
    storage.put_bytes(audio["path"], content, "audio/mpeg")
    epoch = recovery.scoped_epoch(storage, job_id, now=lambda: at)
    assert recovery.scoped_epoch(storage, job_id, now=lambda: at + timedelta(seconds=1)) == epoch
    record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record["deadline_at_utc"] == (at + timedelta(seconds=5100)).isoformat().replace(
        "+00:00", "Z"
    )


@pytest.mark.parametrize(
    ("job_id", "expected"),
    [
        (
            "podcast-2026-W44-00bc06dfb6e2",
            (
                "2026-W44",
                "40269058395",
                "2a34575cd7be0d0a6ec36b4a808a1909ae7f793d00a8511da464ec063cf2bfa0",
                "5c1fc333deb75ea2bfff990c1222f97a12acb2c3234e19df26717a27e8ff6f56",
            ),
        ),
        (
            "podcast-2026-W45-9798e93edc06",
            (
                "2026-W45",
                "41395629030",
                "fc4023679d8b2ba90481f783ebb9c57e61e059db3790c9c9d3c4a996b0242e34",
                "a64c9c664a510b94207a566c92c3cc0465af241a5304109b845d1317284f5d3b",
            ),
        ),
    ],
)
def test_owner_release_scope_uses_exact_w44_and_w45_publication_identities(job_id, expected):
    from podcaster.video.recovery import scoped_identity

    identity = scoped_identity(job_id)
    assert (
        identity.week,
        identity.publish_run_id,
        identity.article_sha256,
        identity.manifest_sha256,
    ) == expected


def test_owner_release_scope_rejects_near_miss_job_id():
    from podcaster.video.recovery import RecoveryDenied, scoped_identity

    with pytest.raises(RecoveryDenied):
        scoped_identity("podcast-2026-W45-9798e93edc06-near-miss")


def test_unrelated_job_remains_outside_owner_release_scope():
    from podcaster.video.recovery import private_original

    assert not private_original("podcast-2026-W46-unrelated")


def test_scoped_concurrent_reservations_commit_one_fixed_epoch(recovery_original, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local

    from podcaster.video import recovery

    storage, job_id, identity, _, at = recovery_original
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, identity.manifest_sha256),
    )
    read = storage.get_bytes
    barrier = Barrier(2)
    thread = local()
    path = f"jobs/{job_id}/manifest.json"
    historical = storage.get_bytes(f"jobs/{job_id}/video-budget.json")

    def simultaneous_read(key):
        raw = read(key)
        if key == path and not getattr(thread, "entered", False):
            thread.entered = True
            barrier.wait(timeout=5)
        return raw

    monkeypatch.setattr(storage, "get_bytes", simultaneous_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(recovery.scoped_epoch, storage, job_id, now=lambda: at) for _ in range(2)
        ]
        epochs = [future.result(timeout=10) for future in futures]
    monkeypatch.setattr(storage, "get_bytes", read)
    assert epochs[0] == epochs[1]
    state = load_manifest(storage, job_id)["generation"]["video_recovery"]
    assert len(state["epochs"]) == 1
    assert storage.get_bytes(f"jobs/{job_id}/video-budget.json") == historical


@pytest.mark.parametrize(
    "recovery_original",
    ["podcast-2026-W44-00bc06dfb6e2", "podcast-2026-W45-9798e93edc06"],
    indirect=True,
)
def test_scoped_original_rejects_second_epoch_after_terminal(recovery_original, monkeypatch):
    from podcaster.video import recovery

    storage, job_id, identity, _, at = recovery_original
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, identity.manifest_sha256),
    )
    epoch = recovery.scoped_epoch(storage, job_id, now=lambda: at)
    with pytest.raises(recovery.RecoveryDenied):
        recovery.reconcile_dispatch(
            storage, job_id, epoch, lambda _body: None, now=lambda: at + timedelta(seconds=5101)
        )
    with pytest.raises(recovery.RecoveryDenied):
        recovery.scoped_epoch(storage, job_id, now=lambda: at + timedelta(seconds=5102))
    state = load_manifest(storage, job_id)["generation"]["video_recovery"]
    assert len(state["epochs"]) == 1
    assert state["epochs"][0]["status"] == "terminal"


@pytest.mark.parametrize("status", ["reserved", "dispatched", "running"])
def test_spent_epoch_terminalization_needs_no_audio_io_and_preserves_history(
    recovery_original, monkeypatch, status
):
    from copy import deepcopy

    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, identity, audio_sha, at = recovery_original
    epoch_id = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=audio_sha,
        inventory_reference="existing-proof",
        now=lambda: at,
    )
    document = load_manifest(storage, job_id)
    document["generation"]["video_recovery"]["epochs"][0]["status"] = status
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(document), "application/json")
    before = deepcopy(document)
    monkeypatch.setattr(recovery, "_same_proof", Mock(side_effect=AssertionError("no media GET")))
    assert recovery.terminalize_expired_epoch(
        storage, job_id, epoch_id, now=lambda: at + timedelta(seconds=5101)
    )
    after = load_manifest(storage, job_id)
    result = after["generation"]["video_recovery"]["epochs"][0]
    assert result.pop("terminal_reason") == "deadline_elapsed"
    result["status"] = status
    assert after == before


@pytest.mark.parametrize("race", ["owner", "selected", "deadline"])
def test_spent_epoch_cas_never_overwrites_changed_owner_selection_or_budget(
    recovery_original, monkeypatch, race
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, identity, audio_sha, at = recovery_original
    epoch_id = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=audio_sha,
        inventory_reference="existing-proof",
        now=lambda: at,
    )
    real_update = storage.update_bytes
    path = f"jobs/{job_id}/manifest.json"
    raced = []

    def update(key, content_type, callback):
        document = load_manifest(storage, job_id)
        state = document["generation"]["video_recovery"]
        if race == "selected":
            state["selected_private_output"] = {"preserve": "committed"}
            state["epochs"][0]["status"] = "selected"
        elif race == "owner":
            state["epochs"][0]["owner"] = "successor"
        else:
            state["epochs"][0]["deadline_at_utc"] = (at + timedelta(seconds=5200)).isoformat()
        payload = manifest_bytes(document)
        storage.put_bytes(path, payload, content_type)
        raced.append(payload)
        return real_update(key, content_type, callback)

    monkeypatch.setattr(storage, "update_bytes", update)
    assert not recovery.terminalize_expired_epoch(
        storage, job_id, epoch_id, now=lambda: at + timedelta(seconds=5101)
    )
    assert storage.get_bytes(path) == raced[0]


def _w44_successor_fixture(recovery_original, monkeypatch):
    from dataclasses import asdict

    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, identity, audio_sha, at = recovery_original
    assert job_id == recovery.W44_JOB
    with monkeypatch.context() as fixed:
        fixed.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex=recovery.W44_PREDECESSOR))
        epoch = recovery.scoped_epoch(storage, job_id, now=lambda: at)
    claim = recovery.claim_epoch(
        storage,
        job_id,
        epoch,
        "old-worker",
        now=lambda: at,
        visibility_expires_at=at + timedelta(seconds=900),
    )
    after = at + timedelta(seconds=5102)
    with pytest.raises(recovery.RecoveryDenied):
        recovery.reconcile_dispatch(storage, job_id, epoch, Mock(), now=lambda: after)
    prior = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    evidence = {
        "schema_version": "w44-successor-quiescence-v1",
        "job_id": job_id,
        "predecessor_epoch_id": epoch,
        "coordinator_disposition": recovery.W44_SUCCESSOR_DISPOSITION,
        "observed_at": after.isoformat(),
        "predecessor_sha256": sha256(manifest_bytes(prior)).hexdigest(),
        "proof_sha256": prior["proof_sha256"],
        "historical_budget_sha256": sha256(
            storage.get_bytes(f"jobs/{job_id}/video-budget.json")
        ).hexdigest(),
        "execution_inventory_complete": True,
        "native_redeliveries_accounted": True,
        "competing_writer_absent": True,
        "executions": [
            {
                "name": f"podcaster-yqabcnkm2junu-video-{name}",
                "status": "Failed",
                "replica_inventory_complete": True,
                "replicas": [
                    {"name": replica, "exit_code": 1, "finished_at": after.isoformat()}
                    for replica in replicas
                ],
            }
            for name, replicas in (("98kpp", ["9rmfj", "9c562"]), ("zb8dm", ["retry"]))
        ],
    }
    assert prior["identity"] == asdict(identity)
    monkeypatch.setattr(recovery, "W44_AUDIO_SHA256", audio_sha)
    return storage, job_id, after, prior, evidence, claim


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
def test_exact_w44_successor_preserves_history_and_fixed_deadline(recovery_original, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, prior, evidence, old_claim = _w44_successor_fixture(
        recovery_original, monkeypatch
    )
    forbidden = Mock(side_effect=AssertionError("TTS/provider operation during private admission"))
    monkeypatch.setattr("podcaster.job_runner.run_synthesis", forbidden)
    monkeypatch.setattr("podcaster.video.job_runner.distribute_video", forbidden)
    monkeypatch.setattr("podcaster.video.job_runner.enqueue_distribution_job", forbidden)
    budget = storage.get_bytes(f"jobs/{job_id}/video-budget.json")
    storage.put_bytes(
        recovery.SUCCESSOR_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json"
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(recovery.scoped_epoch, storage, job_id, now=lambda: at) for _ in range(2)
        ]
        epochs = [future.result(timeout=30) for future in futures]
    assert epochs[0] == epochs[1] != prior["epoch_id"]
    state = load_manifest(storage, job_id)["generation"]["video_recovery"]
    assert state["epochs"][0] == prior
    successor = state["epochs"][1]
    assert successor["predecessor_epoch_id"] == recovery.W44_PREDECESSOR
    assert successor["quiescence_evidence"] == evidence
    assert successor["deadline_at_utc"] == recovery._iso(at + timedelta(seconds=5100))
    assert (
        recovery.scoped_epoch(storage, job_id, now=lambda: at + timedelta(seconds=1)) == epochs[0]
    )
    sent = []
    recovery.reconcile_pending(storage, sent.append, scoped_only=True, now=lambda: at)
    assert [recovery.message_epoch(body) for body in sent] == [epochs[0]]
    with pytest.raises(recovery.RecoveryDenied):
        recovery.assert_current(storage, old_claim, now=lambda: at)
    original = load_manifest(storage, job_id)
    with pytest.raises(recovery.RecoveryDenied):
        recovery.guard_manifest(original, claim=old_claim, now=lambda: at)
    with pytest.raises(recovery.RecoveryDenied):
        recovery.reconcile_dispatch(
            storage, job_id, epochs[0], Mock(), now=lambda: at + timedelta(seconds=5101)
        )
    with pytest.raises(recovery.RecoveryDenied):
        recovery.scoped_epoch(storage, job_id, now=lambda: at + timedelta(seconds=5102))
    assert storage.get_bytes(f"jobs/{job_id}/video-budget.json") == budget
    assert len(load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"]) == 2
    forbidden.assert_not_called()


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "stale",
        "future",
        "proof",
        "budget",
        "inventory",
        "native",
        "active",
        "replica",
        "known",
        "selected",
        "provider",
        "original_audio",
    ],
)
def test_successor_fails_closed_without_trusted_complete_fresh_termination(
    recovery_original, monkeypatch, bad
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, prior, evidence, _ = _w44_successor_fixture(recovery_original, monkeypatch)
    if bad == "stale":
        evidence["observed_at"] = recovery._iso(at - timedelta(seconds=121))
    elif bad == "future":
        evidence["observed_at"] = recovery._iso(at + timedelta(seconds=1))
    elif bad in {"proof", "budget"}:
        evidence[f"{'proof' if bad == 'proof' else 'historical_budget'}_sha256"] = "0" * 64
    elif bad == "inventory":
        evidence["execution_inventory_complete"] = False
    elif bad == "native":
        evidence["native_redeliveries_accounted"] = False
    elif bad == "active":
        evidence["executions"][0]["status"] = "Running"
    elif bad == "replica":
        del evidence["executions"][0]["replicas"][0]["finished_at"]
    elif bad == "known":
        evidence["executions"].pop()
    elif bad in {"selected", "provider"}:
        document = load_manifest(storage, job_id)
        if bad == "selected":
            document["generation"]["video_recovery"]["selected_private_output"] = {"unknown": True}
        else:
            document["generation"]["video_publish"] = {"youtube": {"id": "existing-intent"}}
        storage.put_bytes(
            f"jobs/{job_id}/manifest.json", manifest_bytes(document), "application/json"
        )
    elif bad == "original_audio":
        monkeypatch.setattr(recovery, "W44_AUDIO_SHA256", "0" * 64)
    if bad != "missing":
        storage.put_bytes(
            recovery.SUCCESSOR_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json"
        )
    with pytest.raises(recovery.RecoveryDenied):
        recovery.scoped_epoch(storage, job_id, now=lambda: at)
    assert load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"] == [prior]


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
@pytest.mark.parametrize("boundary", ["callback", "proof", "attestation", "retry"])
@pytest.mark.parametrize("elapsed", [-1, 120, 121])
def test_successor_uses_live_clock_after_reads_and_on_every_cas_retry(
    recovery_original, monkeypatch, boundary, elapsed
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, prior, evidence, _ = _w44_successor_fixture(recovery_original, monkeypatch)
    storage.put_bytes(
        recovery.SUCCESSOR_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json"
    )
    path = f"jobs/{job_id}/manifest.json"
    before = storage.get_bytes(path)
    budget = storage.get_bytes(f"jobs/{job_id}/video-budget.json")
    clock = [at]
    original_update = storage.update_bytes
    original_proof = recovery._proof
    original_get = storage.get_bytes
    proof_calls = 0
    callbacks = []

    def proof(*args):
        nonlocal proof_calls
        result = original_proof(*args)
        proof_calls += 1
        if boundary == "proof" and proof_calls == 2:
            clock[0] = at + timedelta(seconds=elapsed)
        return result

    def get_bytes(name):
        result = original_get(name)
        if boundary == "attestation" and name == recovery.SUCCESSOR_QUIESCENCE_PATH:
            clock[0] = at + timedelta(seconds=elapsed)
        return result

    def update_bytes(name, content_type, update):
        def callback(raw):
            callbacks.append(clock[0])
            return update(raw)

        if name == path:
            if boundary == "retry":
                # Discard the first proposal as a conditional-write conflict would.
                callback(original_get(path))
            if boundary in {"callback", "retry"}:
                clock[0] = at + timedelta(seconds=elapsed)
        return original_update(name, content_type, callback)

    monkeypatch.setattr(recovery, "_proof", proof)
    monkeypatch.setattr(storage, "get_bytes", get_bytes)
    monkeypatch.setattr(storage, "update_bytes", update_bytes)
    if elapsed != 120:
        with pytest.raises(recovery.RecoveryDenied):
            recovery.scoped_epoch(storage, job_id, now=lambda: clock[0])
        assert original_get(path) == before
    else:
        epoch_id = recovery.scoped_epoch(storage, job_id, now=lambda: clock[0])
        state = load_manifest(storage, job_id)["generation"]["video_recovery"]
        assert state["epochs"][0] == prior
        successor = state["epochs"][1]
        assert successor["epoch_id"] == epoch_id
        assert successor["started_at_utc"] == recovery._iso(clock[0])
        assert successor["deadline_at_utc"] == recovery._iso(clock[0] + timedelta(seconds=5100))
        assert successor["quiescence_evidence"] == evidence
        persisted = original_get(path)
        clock[0] += timedelta(seconds=1)
        assert recovery.scoped_epoch(storage, job_id, now=lambda: clock[0]) == epoch_id
        assert original_get(path) == persisted
    assert len(callbacks) == (2 if boundary == "retry" else 1)
    assert original_get(f"jobs/{job_id}/video-budget.json") == budget


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
def test_authenticated_request_cannot_supply_successor_quiescence(recovery_original, monkeypatch):
    from podcaster import monitoring
    from podcaster.video import recovery

    storage, job_id, at, prior, evidence, _ = _w44_successor_fixture(recovery_original, monkeypatch)
    monkeypatch.setenv("PODCASTER_API_KEY", "existing-api-key")
    monkeypatch.setattr(monitoring, "get_storage", lambda: storage)
    monkeypatch.setattr(recovery, "_now", lambda: at)
    queue = Mock()
    monkeypatch.setattr("podcaster.queue.create_video_queue_backend", lambda: queue)
    with TestClient(monitoring.app) as client:
        response = client.post(
            f"/api/jobs/{job_id}/video/generate",
            headers={"X-Podcaster-Api-Key": "existing-api-key"},
            json={
                "quiescence_evidence": evidence,
                "inventory_reference": recovery.W44_SUCCESSOR_DISPOSITION,
                "owner_approved": True,
            },
        )
    assert response.status_code in {409, 422}
    queue.send_message.assert_not_called()
    assert storage.get_bytes(recovery.SUCCESSOR_QUIESCENCE_PATH) is None
    assert load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"] == [prior]


def _w44_final_fixture(recovery_original, monkeypatch):
    from copy import deepcopy

    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, _, evidence, old_claim = _w44_successor_fixture(
        recovery_original, monkeypatch
    )
    storage.put_bytes(
        recovery.SUCCESSOR_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json"
    )
    with monkeypatch.context() as fixed:
        fixed.setattr(recovery.uuid, "uuid4", lambda: SimpleNamespace(hex=recovery.W44_SUCCESSOR))
        second = recovery.scoped_epoch(storage, job_id, now=lambda: at)
    second_claim = recovery.claim_epoch(
        storage,
        job_id,
        second,
        "spent-successor",
        now=lambda: at,
        visibility_expires_at=at + timedelta(seconds=900),
    )
    at += timedelta(seconds=5102)
    assert recovery.terminalize_expired_epoch(storage, job_id, second, now=lambda: at)
    state = load_manifest(storage, job_id)["generation"]["video_recovery"]
    monkeypatch.setattr(recovery, "W44_PROOF_SHA256", state["epochs"][0]["proof_sha256"])
    budget = storage.get_bytes(f"jobs/{job_id}/video-budget.json")
    monkeypatch.setattr(recovery, "W44_BUDGET_SHA256", sha256(budget).hexdigest())
    evidence = {
        **deepcopy(evidence),
        "schema_version": "w44-final-quiescence-v1",
        "predecessor_epoch_id": second,
        "coordinator_disposition": recovery.W44_FINAL_DISPOSITION,
        "observed_at": at.isoformat(),
        "predecessor_sha256": recovery._digest(state["epochs"][-1]),
        "predecessor_epochs_sha256": recovery._digest(state["epochs"]),
        "ledger_sha256": recovery._digest(state),
        "editor_authority_absent": True,
        "shared_capacity_conflict_absent": True,
    }
    inventories = {}
    for epoch_id, videos, recorder in (
        (recovery.W44_PREDECESSOR, ("98kpp", "zb8dm"), "old-recorder"),
        (recovery.W44_SUCCESSOR, ("f6rll", "mjn7b"), "successor-recorder"),
    ):
        inventories[epoch_id] = {
            "native_redeliveries_accounted": True,
            "recorder_inventory_complete": True,
            "video": [f"podcaster-yqabcnkm2junu-video-{name}" for name in videos],
            "recorder": [f"podcaster-yqabcnkm2junu-recorder-{recorder}"],
        }
    evidence["epoch_execution_inventories"] = inventories
    for name in (
        "podcaster-yqabcnkm2junu-video-f6rll",
        "podcaster-yqabcnkm2junu-video-mjn7b",
        "podcaster-yqabcnkm2junu-recorder-old-recorder",
        "podcaster-yqabcnkm2junu-recorder-successor-recorder",
    ):
        replicas = {
            "podcaster-yqabcnkm2junu-video-f6rll": ("ncgdn", "k7fw6"),
            "podcaster-yqabcnkm2junu-video-mjn7b": ("7mflp", "zsxkn"),
        }.get(name, ("main", "native"))
        evidence["executions"].append(
            {
                "name": name,
                "status": "Succeeded" if "-recorder-" in name else "Failed",
                "replica_inventory_complete": True,
                "replicas": [
                    {"name": name + "-" + replica, "exit_code": 1, "finished_at": at.isoformat()}
                    for replica in replicas
                ],
            }
        )
    return storage, job_id, at, deepcopy(state), evidence, (old_claim, second_claim)


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
@pytest.mark.parametrize("entry", ["direct", "scoped"])
@pytest.mark.parametrize("kind", ["video", "recorder"])
@pytest.mark.parametrize("replica_index", [0, 1])
def test_final_w44_receipts_cannot_predate_their_actual_epoch(
    recovery_original, monkeypatch, entry, kind, replica_index
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, before, evidence, _ = _w44_final_fixture(recovery_original, monkeypatch)
    names = evidence["epoch_execution_inventories"][recovery.W44_SUCCESSOR][kind]
    for execution in evidence["executions"]:
        if execution["name"] in names:
            execution["replicas"][replica_index]["finished_at"] = before["epochs"][0][
                "started_at_utc"
            ]
    storage.put_bytes(recovery.FINAL_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json")
    original = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    with pytest.raises(recovery.RecoveryDenied):
        if entry == "scoped":
            recovery.scoped_epoch(storage, job_id, now=lambda: at)
        else:
            recovery.reserve_epoch(
                storage,
                job_id,
                expected_identity=recovery.scoped_identity(job_id),
                expected_audio_sha256=recovery.W44_AUDIO_SHA256,
                inventory_reference=recovery.SCOPED_PRIVATE_DIRECTIVE,
                predecessor=recovery.W44_SUCCESSOR,
                scoped_private=True,
                now=lambda: at,
            )
    assert storage.get_bytes(f"jobs/{job_id}/manifest.json") == original


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
@pytest.mark.parametrize(
    "receipt",
    ["at_start", "before_start", "future", "submicro_future", "naive", "noncanonical"],
)
@pytest.mark.parametrize("epoch_index", [0, 1])
def test_final_w44_receipt_chronology_uses_each_persisted_epoch(
    recovery_original, monkeypatch, receipt, epoch_index
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, before, evidence, _ = _w44_final_fixture(recovery_original, monkeypatch)
    epoch = before["epochs"][epoch_index]
    start = recovery._parse(epoch["started_at_utc"])
    finished = {
        "at_start": epoch["started_at_utc"],
        "before_start": (start - timedelta(microseconds=1)).isoformat(),
        "future": (at + timedelta(microseconds=1)).isoformat(),
        "submicro_future": at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "1Z",
        "naive": start.replace(tzinfo=None).isoformat(),
        "noncanonical": start.isoformat().replace("T", " "),
    }[receipt]
    names = {
        name
        for kind in ("video", "recorder")
        for name in evidence["epoch_execution_inventories"][epoch["epoch_id"]][kind]
    }
    for execution in evidence["executions"]:
        if execution["name"] in names:
            for replica in execution["replicas"]:
                replica["finished_at"] = finished
    storage.put_bytes(recovery.FINAL_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json")
    if receipt == "at_start":
        recovery.scoped_epoch(storage, job_id, now=lambda: at)
        state = load_manifest(storage, job_id)["generation"]["video_recovery"]
        assert state["epochs"][:2] == before["epochs"]
        assert len(state["epochs"]) == 3
    else:
        with pytest.raises(recovery.RecoveryDenied):
            recovery.scoped_epoch(storage, job_id, now=lambda: at)
        assert load_manifest(storage, job_id)["generation"]["video_recovery"] == before


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
def test_final_w44_known_workers_cannot_be_reassigned_to_earlier_inventory(
    recovery_original, monkeypatch
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, before, evidence, _ = _w44_final_fixture(recovery_original, monkeypatch)
    inventories = evidence["epoch_execution_inventories"]
    successor_name = inventories[recovery.W44_SUCCESSOR]["video"].pop()
    inventories[recovery.W44_PREDECESSOR]["video"].append(successor_name)
    for execution in evidence["executions"]:
        if execution["name"] == successor_name:
            for replica in execution["replicas"]:
                replica["finished_at"] = before["epochs"][0]["started_at_utc"]
    storage.put_bytes(recovery.FINAL_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json")
    with pytest.raises(recovery.RecoveryDenied):
        recovery.scoped_epoch(storage, job_id, now=lambda: at)
    assert load_manifest(storage, job_id)["generation"]["video_recovery"] == before


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
@pytest.mark.parametrize("suffix", ["ncgdn", "k7fw6", "7mflp", "zsxkn"])
def test_final_w44_requires_every_known_successor_native_receipt(
    recovery_original, monkeypatch, suffix
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, before, evidence, _ = _w44_final_fixture(recovery_original, monkeypatch)
    for execution in evidence["executions"]:
        execution["replicas"] = [
            replica
            for replica in execution["replicas"]
            if not replica["name"].endswith("-" + suffix)
        ]
    storage.put_bytes(recovery.FINAL_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json")
    with pytest.raises(recovery.RecoveryDenied):
        recovery.scoped_epoch(storage, job_id, now=lambda: at)
    assert load_manifest(storage, job_id)["generation"]["video_recovery"] == before


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
@pytest.mark.parametrize("stage", ["first_successor", "final"])
def test_w44_observation_cannot_be_fractionally_future_after_storage(
    recovery_original, monkeypatch, stage
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    if stage == "final":
        storage, job_id, at, _, evidence, _ = _w44_final_fixture(recovery_original, monkeypatch)
        path = recovery.FINAL_QUIESCENCE_PATH
    else:
        storage, job_id, at, _, evidence, _ = _w44_successor_fixture(recovery_original, monkeypatch)
        path = recovery.SUCCESSOR_QUIESCENCE_PATH
    evidence["observed_at"] = at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "1Z"
    storage.put_bytes(path, manifest_bytes(evidence), "application/json")
    original = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    with pytest.raises(recovery.RecoveryDenied):
        recovery.scoped_epoch(storage, job_id, now=lambda: at)
    assert storage.get_bytes(f"jobs/{job_id}/manifest.json") == original


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
def test_final_w44_cas_retry_uses_latest_persisted_worker_start(recovery_original, monkeypatch):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, before, evidence, _ = _w44_final_fixture(recovery_original, monkeypatch)
    epoch = before["epochs"][1]
    for execution in evidence["executions"]:
        if (
            execution["name"]
            in evidence["epoch_execution_inventories"][recovery.W44_SUCCESSOR]["video"]
        ):
            for replica in execution["replicas"]:
                replica["finished_at"] = epoch["started_at_utc"]
    storage.put_bytes(recovery.FINAL_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json")
    path = f"jobs/{job_id}/manifest.json"
    update = storage.update_bytes

    def retry(name, content_type, callback):
        if name == path:
            callback(storage.get_bytes(path))
            document = load_manifest(storage, job_id)
            current = document["generation"]["video_recovery"]
            latest = current["epochs"][1]
            for field in ("started_at_utc", "deadline_at_utc"):
                latest[field] = recovery._iso(recovery._parse(latest[field]) + timedelta(seconds=1))
            evidence["ledger_sha256"] = recovery._digest(current)
            evidence["predecessor_epochs_sha256"] = recovery._digest(current["epochs"])
            evidence["predecessor_sha256"] = recovery._digest(latest)
            storage.put_bytes(path, manifest_bytes(document), "application/json")
            storage.put_bytes(
                recovery.FINAL_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json"
            )
        return update(name, content_type, callback)

    monkeypatch.setattr(storage, "update_bytes", retry)
    with pytest.raises(recovery.RecoveryDenied):
        recovery.scoped_epoch(storage, job_id, now=lambda: at)
    assert len(load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"]) == 2


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
def test_final_w44_admission_is_once_preserves_history_and_reconciles_pending_send(
    recovery_original, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, before, evidence, claims = _w44_final_fixture(
        recovery_original, monkeypatch
    )
    forbidden = Mock(side_effect=AssertionError("TTS/provider operation during private admission"))
    monkeypatch.setattr("podcaster.job_runner.run_synthesis", forbidden)
    monkeypatch.setattr("podcaster.video.job_runner.distribute_video", forbidden)
    monkeypatch.setattr("podcaster.video.job_runner.enqueue_distribution_job", forbidden)
    budget = storage.get_bytes(f"jobs/{job_id}/video-budget.json")
    storage.put_bytes(recovery.FINAL_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json")
    with ThreadPoolExecutor(max_workers=2) as pool:
        epochs = list(
            pool.map(lambda _: recovery.scoped_epoch(storage, job_id, now=lambda: at), range(2))
        )
    assert epochs[0] == epochs[1]
    state = load_manifest(storage, job_id)["generation"]["video_recovery"]
    assert state["epochs"][:2] == before["epochs"]
    final = state["epochs"][-1]
    assert len(state["epochs"]) == 3
    assert final["authority"] == recovery.W44_FINAL_DISPOSITION
    assert final["predecessor_epoch_id"] == recovery.W44_SUCCESSOR
    assert final["quiescence_evidence"] == evidence
    assert final["started_at_utc"] == recovery._iso(at)
    assert final["deadline_at_utc"] == recovery._iso(at + timedelta(seconds=5100))
    with pytest.raises(OSError):
        recovery.reconcile_dispatch(
            storage, job_id, epochs[0], Mock(side_effect=OSError("send failed")), now=lambda: at
        )
    sent = []
    recovery.reconcile_pending(storage, sent.append, scoped_only=True, now=lambda: at)
    assert [recovery.message_epoch(body) for body in sent] == [epochs[0]]
    for claim in claims:
        with pytest.raises(recovery.RecoveryDenied):
            recovery.assert_current(storage, claim, now=lambda: at)
    fixed = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    assert (
        recovery.scoped_epoch(storage, job_id, now=lambda: at + timedelta(seconds=121)) == epochs[0]
    )
    assert storage.get_bytes(f"jobs/{job_id}/manifest.json") == fixed
    claim = recovery.claim_epoch(
        storage,
        job_id,
        epochs[0],
        "final-worker",
        now=lambda: at,
        visibility_expires_at=at + timedelta(seconds=900),
    )
    assert claim.deadline_at_utc == final["deadline_at_utc"]
    later = at + timedelta(seconds=5101)
    recovery.reconcile_pending(storage, sent.append, scoped_only=True, now=lambda: later)
    with pytest.raises(recovery.RecoveryDenied):
        recovery.scoped_epoch(storage, job_id, now=lambda: later)
    with pytest.raises(recovery.RecoveryDenied):
        recovery.reserve_epoch(
            storage,
            job_id,
            expected_identity=recovery.scoped_identity(job_id),
            expected_audio_sha256=recovery.W44_AUDIO_SHA256,
            inventory_reference=recovery.SCOPED_PRIVATE_DIRECTIVE,
            predecessor=epochs[0],
            scoped_private=True,
            now=lambda: later,
        )
    assert len(load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"]) == 3
    assert storage.get_bytes(f"jobs/{job_id}/video-budget.json") == budget
    forbidden.assert_not_called()


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "old_attestation",
        "ledger",
        "prior_hash",
        "predecessors_hash",
        "latest",
        "disposition",
        "budget",
        "proof",
        "audio",
        "reorder",
        "missing_epoch",
        "changed_id",
        "changed_authority",
        "changed_terminal",
        "changed_deadline",
        "broken_chain",
        "selected",
        "provider",
        "live_lease",
        "editor",
        "capacity",
        "missing_inventory",
        "recorder",
        "native",
        "missing_receipt",
        "running",
        "missing_exit",
        "duplicate",
    ],
)
def test_final_w44_rejects_changed_chain_or_unproven_quiescence(
    recovery_original, monkeypatch, bad
):
    from copy import deepcopy

    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, state, evidence, _ = _w44_final_fixture(recovery_original, monkeypatch)
    document = load_manifest(storage, job_id)
    current = document["generation"]["video_recovery"]
    if bad == "old_attestation":
        evidence["schema_version"] = "w44-successor-quiescence-v1"
    elif bad in {"ledger", "prior_hash", "predecessors_hash", "latest", "disposition", "budget"}:
        key = {
            "ledger": "ledger_sha256",
            "prior_hash": "predecessor_sha256",
            "predecessors_hash": "predecessor_epochs_sha256",
            "latest": "predecessor_epoch_id",
            "disposition": "coordinator_disposition",
            "budget": "historical_budget_sha256",
        }[bad]
        evidence[key] = "0" * 64
    elif bad in {"proof", "audio"}:
        monkeypatch.setattr(recovery, f"W44_{bad.upper()}_SHA256", "0" * 64)
    elif bad == "reorder":
        current["epochs"].reverse()
    elif bad == "missing_epoch":
        current["epochs"].pop(0)
    elif bad.startswith("changed_"):
        key, value = {
            "changed_id": ("epoch_id", "f" * 32),
            "changed_authority": ("authority", recovery.W44_FINAL_DISPOSITION),
            "changed_terminal": ("terminal_reason", "operator_stopped"),
            "changed_deadline": ("deadline_at_utc", (at + timedelta(seconds=1)).isoformat()),
        }[bad]
        current["epochs"][0][key] = value
        evidence["ledger_sha256"] = recovery._digest(current)
        evidence["predecessor_epochs_sha256"] = recovery._digest(current["epochs"])
    elif bad == "broken_chain":
        current["epochs"][1]["quiescence_evidence"]["predecessor_sha256"] = "0" * 64
    elif bad == "selected":
        current["selected_private_output"] = {"unknown": True}
    elif bad == "provider":
        document["generation"]["video_publish"] = {"youtube": {"id": "existing-intent"}}
    elif bad == "live_lease":
        current["epochs"][0]["lease_expires_at_utc"] = (at + timedelta(seconds=30)).isoformat()
        evidence["ledger_sha256"] = recovery._digest(current)
        evidence["predecessor_epochs_sha256"] = recovery._digest(current["epochs"])
        current["epochs"][1]["quiescence_evidence"]["predecessor_sha256"] = recovery._digest(
            current["epochs"][0]
        )
    elif bad in {"editor", "capacity"}:
        evidence[
            "editor_authority_absent" if bad == "editor" else "shared_capacity_conflict_absent"
        ] = False
    elif bad == "missing_inventory":
        del evidence["epoch_execution_inventories"][recovery.W44_PREDECESSOR]
    elif bad in {"recorder", "native"}:
        evidence["epoch_execution_inventories"][recovery.W44_SUCCESSOR][
            "recorder_inventory_complete" if bad == "recorder" else "native_redeliveries_accounted"
        ] = False
    elif bad == "missing_receipt":
        evidence["executions"].pop()
    elif bad == "running":
        evidence["executions"][-1]["status"] = "Running"
    elif bad == "missing_exit":
        del evidence["executions"][-1]["replicas"][-1]["exit_code"]
    elif bad == "duplicate":
        evidence["executions"].append(deepcopy(evidence["executions"][-1]))
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(document), "application/json")
    original = storage.get_bytes(f"jobs/{job_id}/manifest.json")
    if bad != "missing":
        storage.put_bytes(
            recovery.FINAL_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json"
        )
    with pytest.raises(recovery.RecoveryDenied):
        recovery.scoped_epoch(storage, job_id, now=lambda: at)
    assert storage.get_bytes(f"jobs/{job_id}/manifest.json") == original
    assert len(state["epochs"]) == 2


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
@pytest.mark.parametrize("boundary", ["proof", "attestation", "retry", "ledger_race"])
@pytest.mark.parametrize("elapsed", [-1, 120, 121])
def test_final_w44_live_clock_and_current_ledger_rechecked_inside_cas(
    recovery_original, monkeypatch, boundary, elapsed
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, at, _, evidence, _ = _w44_final_fixture(recovery_original, monkeypatch)
    storage.put_bytes(recovery.FINAL_QUIESCENCE_PATH, manifest_bytes(evidence), "application/json")
    path = f"jobs/{job_id}/manifest.json"
    original = storage.get_bytes(path)
    original_get = storage.get_bytes
    original_update = storage.update_bytes
    original_proof = recovery._proof
    clock = [at]
    calls = [0]

    def proof(*args):
        result = original_proof(*args)
        calls[0] += 1
        if boundary == "proof" and calls[0] == 2:
            clock[0] = at + timedelta(seconds=elapsed)
        return result

    def get_bytes(name):
        result = original_get(name)
        if boundary == "attestation" and name == recovery.FINAL_QUIESCENCE_PATH:
            clock[0] = at + timedelta(seconds=elapsed)
        return result

    def update_bytes(name, content_type, update):
        if name == path and boundary in {"retry", "ledger_race"}:
            update(original_get(name))
            if boundary == "ledger_race":
                changed = json.loads(original_get(name))
                changed["generation"]["video_recovery"]["pending_selection_attempt"] = {
                    "unknown": True
                }
                storage.put_bytes(name, manifest_bytes(changed), "application/json")
            clock[0] = at + timedelta(seconds=elapsed)
        return original_update(name, content_type, update)

    monkeypatch.setattr(recovery, "_proof", proof)
    monkeypatch.setattr(storage, "get_bytes", get_bytes)
    monkeypatch.setattr(storage, "update_bytes", update_bytes)
    if elapsed != 120 or boundary == "ledger_race":
        with pytest.raises(recovery.RecoveryDenied):
            recovery.scoped_epoch(storage, job_id, now=lambda: clock[0])
        assert len(load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"]) == 2
        if boundary != "ledger_race":
            assert original_get(path) == original
    else:
        recovery.scoped_epoch(storage, job_id, now=lambda: clock[0])
        final = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][-1]
        assert final["started_at_utc"] == recovery._iso(clock[0])
        assert final["deadline_at_utc"] == recovery._iso(clock[0] + timedelta(seconds=5100))


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
def test_authenticated_request_cannot_supply_final_quiescence(recovery_original, monkeypatch):
    from podcaster import monitoring
    from podcaster.video import recovery

    storage, job_id, at, before, evidence, _ = _w44_final_fixture(recovery_original, monkeypatch)
    monkeypatch.setenv("PODCASTER_API_KEY", "existing-api-key")
    monkeypatch.setattr(monitoring, "get_storage", lambda: storage)
    monkeypatch.setattr(recovery, "_now", lambda: at)
    queue = Mock()
    monkeypatch.setattr("podcaster.queue.create_video_queue_backend", lambda: queue)
    with TestClient(monitoring.app) as client:
        response = client.post(
            f"/api/jobs/{job_id}/video/generate",
            headers={"X-Podcaster-Api-Key": "existing-api-key"},
            json={
                "quiescence_evidence": evidence,
                "coordinator_disposition": recovery.W44_FINAL_DISPOSITION,
                "owner_approved": True,
            },
        )
    assert response.status_code in {409, 422}
    queue.send_message.assert_not_called()
    assert storage.get_bytes(recovery.FINAL_QUIESCENCE_PATH) is None
    assert load_manifest(storage, job_id)["generation"]["video_recovery"] == before


def test_stale_ordinary_terminal_callback_cannot_modify_reserved_epoch(
    recovery_original, monkeypatch
):
    from podcaster.video import recovery
    from podcaster.video.budget import VideoStageBudget
    from podcaster.video.job_runner import TransientVideoError, _terminalize_spent_attempt

    storage, job_id, identity, _, at = recovery_original
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, identity.manifest_sha256),
    )
    budget = VideoStageBudget.from_dict(
        json.loads(storage.get_bytes(f"jobs/{job_id}/video-budget.json")),
        now_utc=at,
        utcnow=lambda: at,
    )
    recovery.scoped_epoch(storage, job_id, now=lambda: at)
    path = f"jobs/{job_id}/manifest.json"
    before = storage.get_bytes(path)
    with pytest.raises(TransientVideoError):
        _terminalize_spent_attempt(storage, job_id, budget)
    assert storage.get_bytes(path) == before


def test_scoped_pending_send_denies_new_provider_evidence(recovery_original, monkeypatch):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, identity, _, at = recovery_original
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, identity.manifest_sha256),
    )
    epoch = recovery.scoped_epoch(storage, job_id, now=lambda: at)
    manifest = load_manifest(storage, job_id)
    manifest["generation"]["video_publish"] = {"youtube": {"status": "unknown"}}
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json")
    sent = Mock()
    with pytest.raises(recovery.RecoveryDenied):
        recovery.reconcile_dispatch(storage, job_id, epoch, sent, now=lambda: at)
    recovery.reconcile_pending(storage, sent, now=lambda: at, scoped_only=True)
    sent.assert_not_called()


@pytest.mark.parametrize("fault", ["unscoped", "caller_reference", "identity", "extra_epoch"])
def test_scoped_startup_never_dispatches_unapproved_epoch(recovery_original, monkeypatch, fault):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, identity, _, at = recovery_original
    monkeypatch.setitem(recovery.PRIVATE_ORIGINALS, job_id, 0)
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, identity.manifest_sha256),
    )
    recovery.scoped_epoch(storage, job_id, now=lambda: at)
    manifest = load_manifest(storage, job_id)
    epochs = manifest["generation"]["video_recovery"]["epochs"]
    epoch = epochs[0]
    if fault == "unscoped":
        epoch.pop("authority")
    elif fault == "caller_reference":
        epoch["inventory_reference"] = "caller-claimed-owner"
    elif fault == "identity":
        epoch["identity"]["publish_run_id"] = "999"
    else:
        epochs.append({**epoch, "epoch_id": "f" * 32})
    path = f"jobs/{job_id}/manifest.json"
    storage.put_bytes(path, manifest_bytes(manifest), "application/json")
    before = storage.get_bytes(path)
    sent = Mock()
    recovery.reconcile_pending(storage, sent, now=lambda: at, scoped_only=True)
    sent.assert_not_called()
    assert storage.get_bytes(path) == before


def test_recovery_dispatch_reclaim_and_fixed_deadline(recovery_original):
    from podcaster.video.recovery import (
        RecoveryDenied,
        claim_epoch,
        message_epoch,
        reconcile_dispatch,
        reserve_epoch,
    )

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch = reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="owner-inventory-readback",
        now=lambda: at,
    )
    with pytest.raises(RecoveryDenied):
        reserve_epoch(
            storage,
            job_id,
            expected_identity=identity,
            expected_audio_sha256=mp3_sha,
            inventory_reference="owner-inventory-readback",
            now=lambda: at,
        )
    sent = []

    def send_then_crash(body):
        sent.append(body)
        raise RuntimeError("crashed after send")

    with pytest.raises(RuntimeError):
        reconcile_dispatch(storage, job_id, epoch, send_then_crash, now=lambda: at)
    assert message_epoch(sent[0]) == epoch
    reconcile_dispatch(storage, job_id, epoch, sent.append, now=lambda: at)
    assert sent == [sent[0]] * 2
    reconcile_dispatch(storage, job_id, epoch, sent.append, now=lambda: at)
    assert len(sent) == 2
    first = claim_epoch(
        storage,
        job_id,
        epoch,
        "first",
        now=lambda: at,
        visibility_expires_at=at + timedelta(seconds=5400),
    )
    with pytest.raises(RecoveryDenied):
        claim_epoch(
            storage,
            job_id,
            epoch,
            "second",
            now=lambda: at + timedelta(seconds=10),
            visibility_expires_at=at + timedelta(seconds=5400),
        )
    second = claim_epoch(
        storage,
        job_id,
        epoch,
        "second",
        now=lambda: at + timedelta(seconds=1801),
        visibility_expires_at=at + timedelta(seconds=5400),
    )
    assert first.deadline_at_utc == second.deadline_at_utc
    with pytest.raises(RecoveryDenied):
        claim_epoch(
            storage,
            job_id,
            epoch,
            "third",
            now=lambda: at + timedelta(seconds=5400),
            visibility_expires_at=at + timedelta(seconds=10800),
        )
    record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record["status"] == "terminal"
    assert record["deadline_at_utc"] == first.deadline_at_utc


def test_recovery_actual_queue_crash_redelivers_before_fixed_deadline(
    recovery_original, monkeypatch
):
    from dataclasses import replace

    from podcaster.queue import QueueMessage
    from podcaster.video import job_runner, recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="queue-crash-replay",
        now=lambda: at,
    )

    class TimelineQueue:
        def __init__(self):
            self.clock = at
            self.body = None
            self.visible = at
            self.receipt = 0
            self.receives = []
            self.deleted = []

        def send_message(self, body):
            self.body = body

        def receive_messages(self, max_messages=1, *, visibility_timeout=600):
            self.receives.append(visibility_timeout)
            if self.body is None or self.clock < self.visible:
                return []
            self.receipt += 1
            self.visible = self.clock + timedelta(seconds=visibility_timeout)
            return [
                QueueMessage("queue-id", str(self.receipt), self.body, self.receipt, self.visible)
            ]

        def update_message(self, message, *, visibility_timeout):
            assert message.pop_receipt == str(self.receipt)
            self.receipt += 1
            self.visible = self.clock + timedelta(seconds=visibility_timeout)
            return replace(message, pop_receipt=str(self.receipt), next_visible_on=self.visible)

        def delete_message(self, message):
            assert message.pop_receipt == str(self.receipt)
            self.deleted.append(message)
            self.body = None

    queue = TimelineQueue()
    recovery.reconcile_dispatch(storage, job_id, epoch, queue.send_message, now=lambda: at)
    original_process = job_runner.process_message
    monkeypatch.setattr(
        job_runner,
        "process_message",
        lambda msg, **kwargs: original_process(msg, now=queue.clock, **kwargs),
    )
    active_leases = []
    monkeypatch.setattr(
        job_runner._RecoveryQueueLease,
        "start",
        lambda lease: active_leases.append(lease),
    )
    monkeypatch.setattr(job_runner._RecoveryQueueLease, "stop", lambda lease: None)

    def crash_during_recording(*_args, **_kwargs):
        for seconds in (300, 600, 900, 1200, 1500):
            queue.clock = at + timedelta(seconds=seconds)
            active_leases[-1].beat()
        queue.clock = at + timedelta(seconds=1800)
        raise RuntimeError("crashed while recording")

    run = Mock(side_effect=crash_during_recording)
    monkeypatch.setattr(job_runner, "run_video_generation", run)
    with pytest.raises(RuntimeError, match="crashed"):
        job_runner.drain(queue, storage, max_messages=1)
    assert queue.receives == [900]
    original = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert original["owner"] == "queue-id:1"
    assert queue.visible == at + timedelta(seconds=2400)
    queue.clock = at + timedelta(seconds=2399)
    assert job_runner.drain(queue, storage, max_messages=1) == []
    queue.clock = at + timedelta(seconds=2400)
    run.side_effect = None
    run.return_value = job_runner.VideoOutcome(job_id, job_runner.STATUS_FAILED, reason="offline")
    outcome = job_runner.drain(queue, storage, max_messages=1)
    assert outcome[0].reason == "offline"
    assert queue.deleted[0].pop_receipt == "7"
    reclaimed = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert reclaimed["owner"] == "queue-id:7"
    assert reclaimed["deadline_at_utc"] == original["deadline_at_utc"]
    assert queue.clock < datetime.fromisoformat(reclaimed["deadline_at_utc"].replace("Z", "+00:00"))


def test_recovery_queue_renewal_rotates_receipt_and_fences_old_owner(recovery_original):
    from dataclasses import replace

    from podcaster.queue import QueueMessage
    from podcaster.video import job_runner, recovery
    from podcaster.video.ownership import VideoOwnershipGuard

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="renew-receipt",
        now=lambda: at,
    )
    current = at
    message = QueueMessage(
        "m", "r0", recovery.message(job_id, epoch), 1, at + timedelta(seconds=900)
    )
    claim = recovery.claim_epoch(
        storage,
        job_id,
        epoch,
        "m:r0",
        now=lambda: current,
        visibility_expires_at=message.next_visible_on,
    )

    class RotatingQueue:
        def __init__(self):
            self.receipt = "r0"
            self.visible = message.next_visible_on
            self.updates = []

        def update_message(self, msg, *, visibility_timeout):
            assert msg.pop_receipt == self.receipt
            assert visibility_timeout == 900
            self.receipt = f"r{len(self.updates) + 1}"
            self.visible = current + timedelta(seconds=visibility_timeout)
            self.updates.append(self.visible)
            return replace(msg, pop_receipt=self.receipt, next_visible_on=self.visible)

        def delete_message(self, msg):
            assert msg.pop_receipt == self.receipt

    queue = RotatingQueue()
    lease = job_runner._RecoveryQueueLease(queue, storage, message, claim, clock=lambda: current)
    guard = VideoOwnershipGuard.acquire(
        storage,
        f"{job_id}-epoch-{epoch}",
        owner="video-runner",
        execution_id="m:r0",
        visibility_expires_at=message.next_visible_on,
        now=lambda: current,
    )
    lease.attach_guard(guard)
    for seconds in (300, 600, 900, 1200, 1500):
        current = at + timedelta(seconds=seconds)
        lease.beat()
        assert lease.visible_until() == queue.visible
        assert lease.message.pop_receipt == queue.receipt
        with pytest.raises(recovery.RecoveryDenied):
            recovery.claim_epoch(
                storage,
                job_id,
                epoch,
                "late-old-owner",
                now=lambda: current,
                visibility_expires_at=current + timedelta(seconds=900),
            )
    current = at + timedelta(seconds=1800)
    assert queue.visible > current
    guard.assert_current()
    with pytest.raises(AssertionError):
        queue.delete_message(message)
    queue.delete_message(lease.message)
    record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record["deadline_at_utc"] == (at + timedelta(seconds=5100)).isoformat().replace(
        "+00:00", "Z"
    )


class _OfflineRotatingQueueClient:
    """In-memory transport for the actual connection-string queue adapter."""

    def __init__(self, at):
        self.clock = at
        self.visible = at
        self.body = None
        self.receipt = 0
        self.updates = []
        self.deleted = []
        self.receives = []
        self.changed = Condition()
        self.on_update = None

    def send_message(self, body):
        self.body = body

    def receive_messages(self, *, messages_per_page, visibility_timeout):
        assert messages_per_page == 1
        self.receives.append(visibility_timeout)
        if self.body is None or self.clock < self.visible:
            return []
        self.receipt += 1
        self.visible = self.clock + timedelta(seconds=visibility_timeout)
        return [
            SimpleNamespace(
                id="offline-id",
                pop_receipt=f"receipt-{self.receipt}",
                content=self.body,
                dequeue_count=self.receipt,
                next_visible_on=self.visible,
            )
        ]

    def update_message(self, message_id, receipt, *, visibility_timeout):
        assert message_id == "offline-id"
        assert receipt == f"receipt-{self.receipt}"
        assert visibility_timeout == 900
        with self.changed:
            self.clock += timedelta(seconds=300)
            self.receipt += 1
            self.visible = self.clock + timedelta(seconds=visibility_timeout)
            self.updates.append(self.visible)
            self.changed.notify_all()
        if self.on_update is not None:
            self.on_update()
        return SimpleNamespace(pop_receipt=f"receipt-{self.receipt}", next_visible_on=self.visible)

    def delete_message(self, message_id, receipt):
        assert message_id == "offline-id"
        assert receipt == f"receipt-{self.receipt}"
        self.deleted.append(receipt)
        self.body = None

    def wait_for_updates(self, count):
        with self.changed:
            assert self.changed.wait_for(lambda: len(self.updates) >= count, timeout=10)


def _offline_recovery_queue(storage, job_id, identity, mp3_sha, at):
    from podcaster.queue import ConnectionStringQueueBackend
    from podcaster.video import recovery

    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="threaded-adapter-readback",
        now=lambda: at,
    )
    client = _OfflineRotatingQueueClient(at)
    backend = ConnectionStringQueueBackend.__new__(ConnectionStringQueueBackend)
    backend._client = client
    recovery.reconcile_dispatch(storage, job_id, epoch, backend.send_message, now=lambda: at)
    return epoch, backend, client


def test_threaded_sdk_heartbeat_survives_recording_crash_and_reclaims(
    recovery_original, monkeypatch
):
    from podcaster.video import job_runner, recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch, queue, client = _offline_recovery_queue(storage, job_id, identity, mp3_sha, at)
    monkeypatch.setattr(job_runner, "RECOVERY_VIDEO_RENEW_INTERVAL", 0.01)
    original_process = job_runner.process_message
    monkeypatch.setattr(
        job_runner,
        "process_message",
        lambda msg, **kwargs: original_process(msg, now=client.clock, **kwargs),
    )
    original_init = job_runner._RecoveryQueueLease.__init__

    def init(lease, *args, **kwargs):
        original_init(lease, *args, **kwargs)
        lease.clock = lambda: client.clock

    monkeypatch.setattr(job_runner._RecoveryQueueLease, "__init__", init)

    def blocked_recording(*_args, **_kwargs):
        client.wait_for_updates(5)
        raise SystemExit("offline process crashed during recording")

    monkeypatch.setattr("podcaster.video.video_gen.record_episode", blocked_recording)
    monkeypatch.setattr(job_runner, "_probe_audio_duration", lambda _: 60.0)
    with pytest.raises(SystemExit, match="process crashed"):
        job_runner.drain(queue, storage, max_messages=1)
    assert client.receives == [900]
    original = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert original["owner"] == "offline-id:receipt-1"
    assert (
        datetime.fromisoformat(original["lease_expires_at_utc"].replace("Z", "+00:00"))
        <= client.visible
    )
    assert len(client.updates) >= 5
    with pytest.raises(recovery.RecoveryDenied):
        recovery.claim_epoch(
            storage,
            job_id,
            epoch,
            "stale-owner",
            now=lambda: client.clock,
            visibility_expires_at=client.visible,
        )
    client.clock = client.visible - timedelta(seconds=1)
    assert job_runner.drain(queue, storage, max_messages=1) == []
    client.clock = client.visible
    monkeypatch.setattr(
        job_runner,
        "run_video_generation",
        Mock(
            return_value=job_runner.VideoOutcome(job_id, job_runner.STATUS_FAILED, reason="offline")
        ),
    )
    outcomes = job_runner.drain(queue, storage, max_messages=1)
    assert outcomes[0].reason == "offline"
    assert client.deleted == [f"receipt-{client.receipt}"]
    reclaimed = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert reclaimed["owner"] == f"offline-id:receipt-{client.receipt}"
    assert reclaimed["deadline_at_utc"] == original["deadline_at_utc"]
    assert client.clock < datetime.fromisoformat(
        reclaimed["deadline_at_utc"].replace("Z", "+00:00")
    )


def test_threaded_sdk_heartbeat_selection_overlap_replays_selected_output(
    recovery_original, monkeypatch
):
    from podcaster.video import job_runner, recovery
    from podcaster.video.distribution import VideoDistributionConfig

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch, queue, client = _offline_recovery_queue(storage, job_id, identity, mp3_sha, at)
    monkeypatch.setattr(job_runner, "RECOVERY_VIDEO_RENEW_INTERVAL", 0.01)
    original_init = job_runner._RecoveryQueueLease.__init__

    def init(lease, *args, **kwargs):
        original_init(lease, *args, **kwargs)
        lease.clock = lambda: client.clock

    monkeypatch.setattr(job_runner._RecoveryQueueLease, "__init__", init)
    monkeypatch.setattr(job_runner, "_probe_audio_duration", lambda _: 60.0)
    monkeypatch.setattr(job_runner, "_resolve_dog_logo", lambda *args: None)
    monkeypatch.setattr("podcaster.video.sync_plan.check_repo_removed", lambda *args, **kw: False)
    provider = Mock(side_effect=AssertionError("provider called during private recovery"))
    monkeypatch.setattr(job_runner, "distribute_video", provider)
    monkeypatch.setattr(job_runner, "enqueue_distribution_job", provider)

    def record(*_args, **_kwargs):
        client.wait_for_updates(2)
        assert (
            load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]["owner"]
            == "offline-id:receipt-1"
        )
        return Mock(recorded=[])

    def compose(*_args, output_path, **_kwargs):
        client.wait_for_updates(4)
        client.on_update = overlap
        output_path.write_bytes(b"R" * 2048)
        return Mock(duration_seconds=60.0, segment_count=1)

    monkeypatch.setattr("podcaster.video.video_gen.record_episode", record)
    monkeypatch.setattr("podcaster.video.video_compose.compose_video", compose)
    selected = Event()
    renewed = Event()
    original_select = recovery.select_private_output

    def select(*args, **kwargs):
        result = original_select(*args, **kwargs)
        selected.set()
        assert renewed.wait(10)
        return result

    monkeypatch.setattr(recovery, "select_private_output", select)

    def overlap():
        assert selected.wait(10)
        renewed.set()

    outcome = job_runner.drain(
        queue, storage, config=VideoDistributionConfig(youtube_enabled=True), max_messages=1
    )[0]
    assert outcome.status == "completed"
    assert outcome.distribution.public_delivery_status == "awaiting_review"
    assert client.receives == [900]
    assert len(client.updates) >= 4
    assert client.deleted == [f"receipt-{client.receipt}"]
    assert recovery.read_selected(storage, job_id)["epoch_id"] == epoch
    record_state = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record_state["status"] == "selected"
    assert (
        datetime.fromisoformat(record_state["lease_expires_at_utc"].replace("Z", "+00:00"))
        <= client.visible
    )
    from podcaster.video.ownership import ownership_path

    guard = json.loads(storage.get_bytes(ownership_path(f"{job_id}-epoch-{epoch}")))
    assert guard["claim"]["execution_id"] == "offline-id:receipt-1"
    assert (
        datetime.fromisoformat(guard["claim"]["visibility_expires_at"].replace("Z", "+00:00"))
        >= client.updates[1]
    )
    provider.assert_not_called()
    # A duplicate delivery after the selection CAS must replay without rendering.
    replay = recovery.message(job_id, epoch)
    client.body = replay
    client.visible = client.clock
    monkeypatch.setattr(
        job_runner, "run_video_generation", Mock(side_effect=AssertionError("rerender"))
    )
    assert job_runner.drain(queue, storage, max_messages=1)[0].status == "completed"
    assert client.deleted[-1] == f"receipt-{client.receipt}"


def test_dispatch_send_before_ack_reconciles_from_running_claim(recovery_original):
    from podcaster.video.recovery import claim_epoch, reconcile_dispatch, reserve_epoch

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch = reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )
    sent = []

    def sent_but_not_acknowledged(body):
        sent.append(body)
        raise RuntimeError("transport response lost after send")

    with pytest.raises(RuntimeError):
        reconcile_dispatch(storage, job_id, epoch, sent_but_not_acknowledged, now=lambda: at)
    owner = claim_epoch(
        storage,
        job_id,
        epoch,
        "accepted-worker",
        now=lambda: at,
        visibility_expires_at=at + timedelta(seconds=5400),
    )
    reconcile_dispatch(storage, job_id, epoch, sent.append, now=lambda: at)
    assert len(sent) == 1
    record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record["dispatch"]["status"] == "accepted"
    assert record["status"] == "running"
    assert record["deadline_at_utc"] == owner.deadline_at_utc


@pytest.mark.parametrize(
    "elapsed,expected_status",
    [
        (0, "dispatched"),
        (5101, "terminal"),
    ],
)
def test_restart_reconciler_sends_pending_or_terminalizes_expired_intent(
    recovery_original,
    monkeypatch,
    elapsed,
    expected_status,
):
    from podcaster.video import recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    monkeypatch.setitem(recovery.PRIVATE_ORIGINALS, job_id, 0)
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )
    sent = []
    recovery.reconcile_pending(
        storage,
        sent.append,
        now=lambda: at + timedelta(seconds=elapsed),
    )
    record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record["epoch_id"] == epoch
    assert record["status"] == expected_status
    assert len(sent) == (1 if elapsed == 0 else 0)


@pytest.mark.parametrize("w44_fault", ["corrupt", "unavailable", "invalid_ledger"])
def test_reconciler_isolates_corrupt_original_and_bad_epoch(
    recovery_original, monkeypatch, caplog, w44_fault
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    w44 = "podcast-2026-W44-00bc06dfb6e2"
    monkeypatch.setattr(recovery, "PRIVATE_ORIGINALS", {w44: 0, job_id: 0})
    w44_path = f"jobs/{w44}/manifest.json"
    if w44_fault == "corrupt":
        storage.put_bytes(w44_path, b"{bad", "application/json")
    elif w44_fault == "invalid_ledger":
        invalid_state = {
            "job_id": w44,
            "generation": {
                "video_recovery": {
                    "schema_version": recovery.SCHEMA,
                    "epochs": [{"epoch_id": "duplicate"}, {"epoch_id": "duplicate"}],
                },
            },
        }
        storage.put_bytes(w44_path, manifest_bytes(invalid_state), "application/json")
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )
    manifest = load_manifest(storage, job_id)
    invalid = dict(manifest["generation"]["video_recovery"]["epochs"][0])
    invalid.update(epoch_id="malformed", deadline_at_utc="invalid")
    manifest["generation"]["video_recovery"]["epochs"].insert(0, invalid)
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json")
    if w44_fault == "unavailable":
        read = storage.get_bytes

        def unavailable(path):
            if path == w44_path:
                raise OSError("storage read failed")
            return read(path)

        monkeypatch.setattr(storage, "get_bytes", unavailable)
    sent = []
    recovery.reconcile_pending(storage, sent.append, now=lambda: at)
    assert len(sent) == 1
    assert recovery.message_epoch(sent[0]) == epoch
    assert (
        load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]["status"]
        == "reserved"
    )
    assert w44 in caplog.text
    assert "malformed" in caplog.text


def test_api_restart_reconciles_unsent_private_dispatch(recovery_original, monkeypatch):
    from podcaster import monitoring
    from podcaster.video import recovery

    storage, job_id, identity, mp3_sha, _ = recovery_original
    monkeypatch.setitem(recovery.PRIVATE_ORIGINALS, job_id, 0)
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, identity.manifest_sha256),
    )
    recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference=recovery.SCOPED_PRIVATE_DIRECTIVE,
        scoped_private=True,
        now=lambda: datetime.now(timezone.utc),
    )
    delivered = Event()
    sent = []

    def send(message):
        sent.append(message)
        delivered.set()

    monkeypatch.delenv("PODCASTER_VIDEO_RECOVERY_OPERATOR_SECRET_URI", raising=False)
    monkeypatch.setattr(monitoring, "get_storage", lambda: storage)
    monkeypatch.setattr(
        "podcaster.queue.create_video_queue_backend",
        lambda: Mock(send_message=send),
    )
    with TestClient(monitoring.app):
        assert delivered.wait(3)
    assert len(sent) == 1
    record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record["status"] == "dispatched"


def test_reconciler_terminalizes_unclaimed_accepted_epoch(recovery_original, monkeypatch):
    from podcaster.video import recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    monkeypatch.setitem(recovery.PRIVATE_ORIGINALS, job_id, 0)
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )
    sent = []
    recovery.reconcile_dispatch(storage, job_id, epoch, sent.append, now=lambda: at)
    recovery.reconcile_pending(
        storage,
        sent.append,
        now=lambda: at + timedelta(seconds=5101),
    )
    assert len(sent) == 1
    record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record["status"] == "terminal"
    assert record["terminal_reason"] == "deadline_elapsed"


def test_reconciler_terminalizes_unsent_epoch_when_queue_unavailable(
    recovery_original,
    monkeypatch,
):
    from podcaster.video import recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    monkeypatch.setitem(recovery.PRIVATE_ORIGINALS, job_id, 0)
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )

    def unavailable(_message):
        raise RuntimeError("queue unavailable")

    recovery.reconcile_pending(storage, unavailable, now=lambda: at)
    record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record["epoch_id"] == epoch
    assert record["status"] == "reserved"
    assert record["dispatch"]["status"] == "pending"
    recovery.reconcile_pending(storage, unavailable, now=lambda: at + timedelta(seconds=5101))
    record = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert record["status"] == "terminal"
    assert record["terminal_reason"] == "deadline_elapsed"


def test_private_candidate_selection_losers_and_readback_fail_closed(recovery_original, tmp_path):
    from podcaster.video.recovery import (
        RecoveryDenied,
        claim_epoch,
        read_selected,
        reserve_epoch,
        select_private_output,
    )

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch = reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="owner-inventory-readback",
        now=lambda: at,
    )
    claim = claim_epoch(
        storage,
        job_id,
        epoch,
        "owner",
        now=lambda: at,
        visibility_expires_at=at + timedelta(seconds=5400),
    )
    from podcaster.video.recovery import _proof

    proof = _proof(load_manifest(storage, job_id), storage, job_id)[1]
    source_a, source_b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    source_a.write_bytes(b"A" * 2048)
    source_b.write_bytes(b"B" * 2048)
    selected = select_private_output(
        storage,
        job_id,
        source_a,
        attempt_id="first",
        claim=claim,
        expected_proof_sha256=proof,
        now=lambda: at,
    )
    with pytest.raises(RecoveryDenied):
        select_private_output(
            storage,
            job_id,
            source_b,
            attempt_id="second",
            claim=claim,
            expected_proof_sha256=proof,
            now=lambda: at,
        )
    assert storage.blob_exists(selected["outbox_path"])
    assert read_selected(storage, job_id) == selected
    held = storage.get_bytes(selected["outbox_path"])
    storage.put_bytes(selected["outbox_path"], b"{}", "application/json")
    with pytest.raises(RecoveryDenied):
        read_selected(storage, job_id)
    storage.put_bytes(selected["outbox_path"], held, "application/json")
    storage.put_bytes(selected["archive_path"], b"C" * 2048, "video/mp4")
    with pytest.raises(RecoveryDenied, match="digest mismatch"):
        read_selected(storage, job_id)
    storage.put_bytes(selected["archive_path"], b"A" * 2048, "video/mp4")
    manifest = load_manifest(storage, job_id)
    state = manifest["generation"]["video_recovery"]
    state["epochs"].append(dict(state["epochs"][0]))
    from podcaster.generation import manifest_bytes

    storage.put_bytes(
        f"jobs/{job_id}/manifest.json",
        manifest_bytes(manifest),
        "application/json",
    )
    with pytest.raises(RecoveryDenied, match="ambiguous"):
        read_selected(storage, job_id)


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
def test_w44_selected_media_is_bound_to_canonical_manifest_and_verified_digest(recovery_original):
    from hashlib import sha256
    from pathlib import Path
    from uuid import uuid4

    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery
    from podcaster.video.owner_release import (
        owner_release_evidence,
        owner_release_matches_selected,
    )

    storage, job_id, _identity, _audio_sha, at = recovery_original
    media = b"synthetic W44 selected candidate" * 100
    source = Path("tests") / f".owner-release-w44-{uuid4().hex}.mp4"
    try:
        source.write_bytes(media)
        document = load_manifest(storage, job_id)
        review_before = document["review"]
        proof = recovery._proof(document, storage, job_id)[1]
        selected = recovery.select_private_output(
            storage,
            job_id,
            source,
            attempt_id="w44selectedcandidate",
            claim=None,
            expected_proof_sha256=proof,
            now=lambda: at,
        )
        digest = sha256(media).hexdigest()
        canonical = load_manifest(storage, job_id)["generation"]["video_recovery"][
            "selected_private_output"
        ]

        assert selected == canonical
        assert selected["mp4_sha256"] == digest
        assert selected["archive_path"].endswith(f"{digest}.mp4")
        assert recovery.read_selected(storage, job_id) == selected
        assert load_manifest(storage, job_id)["review"] == review_before

        artifact = {"media_kind": "video", "sha256": digest, "size_bytes": len(media)}
        canonical_artifact = {
            "artifact_id": "w44-canonical-selected-artifact",
            "selection_version": 1,
            "selected": True,
            "sha256": digest,
        }
        authorization = owner_release_evidence(
            selected["identity"],
            selected,
            artifact,
            canonical_artifact,
        )
        release_document = {
            "publication_identity": selected["identity"],
            "artifact": artifact,
            "canonical_artifact": canonical_artifact,
            "owner_release_authorization": authorization,
        }
        assert authorization is not None
        assert owner_release_matches_selected(release_document, storage)

        release_document["artifact"] = {**artifact, "sha256": sha256(b"arbitrary").hexdigest()}
        assert not owner_release_matches_selected(release_document, storage)

        canonical["mp4_sha256"] = sha256(b"unrelated media").hexdigest()
        changed = load_manifest(storage, job_id)
        changed["generation"]["video_recovery"]["selected_private_output"] = canonical
        storage.put_bytes(
            f"jobs/{job_id}/manifest.json",
            manifest_bytes(changed),
            "application/json",
        )
        with pytest.raises(recovery.RecoveryDenied):
            recovery.read_selected(storage, job_id)
    finally:
        source.unlink(missing_ok=True)


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
def test_expired_owner_release_reconciles_without_authorizing_mutation(
    recovery_original, monkeypatch
):
    from pathlib import Path
    from uuid import uuid4

    from podcaster import distribution_outbox
    from podcaster.generation import manifest_bytes
    from podcaster.orchestration import prepare_owner_authorized_video_release
    from podcaster.video import recovery
    from podcaster.video.distribution import VideoDistributionConfig
    from podcaster.video.owner_release import OWNER_RELEASE_AUTHORIZED_AT, OWNER_RELEASE_OWNER

    storage, job_id, _identity, _audio_sha, _at = recovery_original
    authorized_at = datetime.fromisoformat(OWNER_RELEASE_AUTHORIZED_AT.replace("Z", "+00:00"))
    enqueue_at = authorized_at - timedelta(days=2)
    persisted_late_at = authorized_at + timedelta(hours=23)
    expired_at = authorized_at + timedelta(hours=25)
    monkeypatch.setattr(
        distribution_outbox.DistributionOutboxRepository.__init__,
        "__kwdefaults__",
        {"now": lambda: enqueue_at},
    )
    record_authorization = (
        distribution_outbox.DistributionOutboxRepository.record_owner_release_authorization
    )

    def persist_delayed_authorization(repository, outbox_id, *, owner):
        repository.now = lambda: persisted_late_at
        return record_authorization(repository, outbox_id, owner=owner)

    monkeypatch.setattr(
        distribution_outbox.DistributionOutboxRepository,
        "record_owner_release_authorization",
        persist_delayed_authorization,
    )
    source = Path("tests") / f".expired-owner-release-{uuid4().hex}.mp4"
    try:
        source.write_bytes(b"synthetic W44 release candidate" * 100)
        manifest = load_manifest(storage, job_id)
        proof = recovery._proof(manifest, storage, job_id)[1]
        recovery.select_private_output(
            storage,
            job_id,
            source,
            attempt_id="expiredownerrelease",
            claim=None,
            expected_proof_sha256=proof,
        )
        manifest = load_manifest(storage, job_id)
        manifest["generation"]["video_runner"]["publication_metadata"] = {
            "title": "Existing reviewed title",
            "description": "Existing reviewed description",
        }
        storage.put_bytes(
            f"jobs/{job_id}/manifest.json",
            manifest_bytes(manifest),
            "application/json",
        )
        config = VideoDistributionConfig(
            youtube_enabled=True,
            youtube_client_id="test-client",
            youtube_client_secret="test-secret",
            youtube_refresh_token="test-token",
        )
        monkeypatch.setattr(VideoDistributionConfig, "from_env", lambda: config)
        monkeypatch.setattr(distribution_outbox, "outbox_routing_enabled", lambda: True)
        monkeypatch.setattr("podcaster.queue.enqueue_distribution_job", lambda *_, **__: None)
        result = prepare_owner_authorized_video_release(
            job_id,
            owner=OWNER_RELEASE_OWNER,
            storage=storage,
        )
        repository = distribution_outbox.DistributionOutboxRepository(
            storage, now=lambda: persisted_late_at
        )
        outbox = repository.read(result["outbox_id"])
        horizon = datetime.fromisoformat(
            outbox["providers"]["youtube"]["verification_horizon_at"].replace("Z", "+00:00")
        )
        assert outbox["enqueue"]["at"] == enqueue_at.isoformat().replace("+00:00", "Z")
        assert horizon == authorized_at + timedelta(hours=24)
        assert persisted_late_at < horizon

        claim = repository.claim(
            result["outbox_id"], owner="test-worker", execution_id="ambiguous", lease_seconds=300
        )
        repository.persist_intent(claim, provider="youtube", operation="draft_upload")
        repository.consume_intent(
            claim, provider="youtube", provider_timeout_seconds=120, receipt_margin_seconds=30
        )
        repository.release(claim)
        repository.now = lambda: expired_at
        expired_claim = repository.claim(
            result["outbox_id"], owner="test-worker", execution_id="expired", lease_seconds=300
        )
        assert repository.authorize_dispatch(expired_claim, provider="youtube")
        repository.release(expired_claim)
        monkeypatch.setattr(
            distribution_outbox.DistributionOutboxRepository.__init__,
            "__kwdefaults__",
            {"now": lambda: expired_at},
        )

        class TrackingTransport(_ReviewedYouTubeTransport):
            def __init__(self):
                super().__init__()
                self.reconciliation_reads = []

            def request(self, url, **kwargs):
                if "/channels?" in url or "/playlistItems?" in url:
                    self.reconciliation_reads.append(url)
                return super().request(url, **kwargs)

        http = TrackingTransport()
        monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda: http)
        monkeypatch.setattr(
            "podcaster.video.distribution._get_youtube_access_token", lambda *_: "offline"
        )
        assert _run_reviewed_worker(storage, outbox).reason == "reviewed_video_dispatched"
        assert any("/playlistItems?" in url for url in http.reconciliation_reads)
        assert http.mutations == []
        latest = repository.read(result["outbox_id"])
        leg = latest["providers"]["youtube"]
        assert leg["intent"]["operation"] == "draft_upload"
        assert leg["intent"]["consumed_at"]
        assert leg["receipts"] == []
        assert leg["result"] == "publication_unknown"
    finally:
        source.unlink(missing_ok=True)


@pytest.mark.parametrize("recovery_original", ["podcast-2026-W44-00bc06dfb6e2"], indirect=True)
def test_owner_release_queues_youtube_only_without_forging_media_review(
    recovery_original, monkeypatch
):
    from pathlib import Path
    from uuid import uuid4

    from podcaster import distribution_outbox
    from podcaster.generation import manifest_bytes
    from podcaster.orchestration import prepare_owner_authorized_video_release
    from podcaster.video import recovery
    from podcaster.video.distribution import VideoDistributionConfig
    from podcaster.video.owner_release import OWNER_RELEASE_OWNER

    storage, job_id, _identity, _audio_sha, at = recovery_original
    source = Path("tests") / f".owner-release-dispatch-{uuid4().hex}.mp4"
    try:
        source.write_bytes(b"synthetic W44 release candidate" * 100)
        manifest = load_manifest(storage, job_id)
        proof = recovery._proof(manifest, storage, job_id)[1]
        recovery.select_private_output(
            storage,
            job_id,
            source,
            attempt_id="ownerreleasedispatch",
            claim=None,
            expected_proof_sha256=proof,
            now=lambda: at,
        )
        manifest = load_manifest(storage, job_id)
        manifest["generation"]["video_runner"]["publication_metadata"] = {
            "title": "Existing reviewed title",
            "description": "Existing reviewed description",
        }
        storage.put_bytes(
            f"jobs/{job_id}/manifest.json",
            manifest_bytes(manifest),
            "application/json",
        )

        config = VideoDistributionConfig(
            youtube_enabled=True,
            youtube_client_id="test-client",
            youtube_client_secret="test-secret",
            youtube_refresh_token="test-token",
            spotify_upload_enabled=True,
        )
        monkeypatch.setattr(VideoDistributionConfig, "from_env", lambda: config)
        monkeypatch.setattr(distribution_outbox, "outbox_routing_enabled", lambda: True)
        queued: list[str] = []

        def capture_queue(outbox_id, *, authorize_send, mark_accepted):
            authorize_send()
            mark_accepted()
            queued.append(outbox_id)

        monkeypatch.setattr("podcaster.queue.enqueue_distribution_job", capture_queue)
        result = prepare_owner_authorized_video_release(
            job_id,
            owner=OWNER_RELEASE_OWNER,
            storage=storage,
        )

        repository = distribution_outbox.DistributionOutboxRepository(storage)
        outbox = repository.read(result["outbox_id"])
        assert result["status"] == "queued_or_repairable"
        assert len(queued) == 1
        assert queued[0] == result["outbox_id"]
        assert set(outbox["providers"]) == {"youtube"}
        assert outbox["media_review"] == {"status": "pending", "audit_trail": []}
        assert outbox["owner_release_authorization"]["source"] == (
            "owner_authorized_release_exception"
        )
        assert datetime.fromisoformat(
            outbox["providers"]["youtube"]["verification_horizon_at"].replace("Z", "+00:00")
        ) == datetime.fromisoformat(
            outbox["owner_release_authorization"]["authorized_at"].replace("Z", "+00:00")
        ) + timedelta(hours=24)
        http = _ReviewedYouTubeTransport()
        monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda: http)
        monkeypatch.setattr(
            "podcaster.video.distribution._get_youtube_access_token", lambda *_: "offline"
        )
        assert _run_reviewed_worker(storage, outbox).reason == "reviewed_video_dispatched"
        assert http.bytes_uploaded == storage.get_bytes(outbox["artifact"]["path"])
        assert http.mutations == ["upload", "promote"]
        assert repository.read(result["outbox_id"])["media_review"] == {
            "status": "pending",
            "audit_trail": [],
        }
        assert _run_reviewed_worker(storage, outbox).reason == "reviewed_video_dispatched"
        assert http.mutations == ["upload", "promote"]
    finally:
        source.unlink(missing_ok=True)


@pytest.mark.parametrize("damage", ["lost_commit", "changed_pointer", "held_outbox", "archive"])
def test_first_private_selection_requires_durable_readback(
    recovery_original, tmp_path, monkeypatch, damage
):
    from podcaster.generation import manifest_bytes
    from podcaster.video.recovery import RecoveryDenied, _proof, select_private_output

    storage, job_id, _, _, at = recovery_original
    path = f"jobs/{job_id}/manifest.json"
    proof = _proof(load_manifest(storage, job_id), storage, job_id)[1]
    source = tmp_path / "candidate.mp4"
    source.write_bytes(b"V" * 2048)
    original_update = storage.update_bytes

    def ambiguous_update(blob, content_type, callback):
        if blob != path:
            return original_update(blob, content_type, callback)

        def intervene(raw):
            written = callback(raw)
            document = json.loads(written)
            candidate = (
                document["generation"].get("video_recovery", {}).get("selected_private_output")
            )
            if candidate is None:
                return written
            if damage == "lost_commit":
                return raw
            if damage == "changed_pointer":
                document["generation"]["video_recovery"]["selected_private_output"][
                    "attempt_id"
                ] = "different"
                return manifest_bytes(document)
            if damage == "held_outbox":
                storage.put_bytes(candidate["outbox_path"], b"{}", "application/json")
            if damage == "archive":
                storage.put_bytes(candidate["archive_path"], b"X" * 2048, "video/mp4")
            return written

        return original_update(blob, content_type, intervene)

    monkeypatch.setattr(storage, "update_bytes", ambiguous_update)
    with pytest.raises(RecoveryDenied):
        select_private_output(
            storage,
            job_id,
            source,
            attempt_id="first",
            claim=None,
            expected_proof_sha256=proof,
            now=lambda: at,
        )


def test_unverified_private_selection_keeps_queue_message(recovery_original, monkeypatch):
    from podcaster.queue import QueueMessage, encode_video_message
    from podcaster.video import job_runner, recovery

    storage, job_id, _, _, at = recovery_original
    message = QueueMessage(
        message_id="unverified",
        pop_receipt="receipt",
        body=encode_video_message(job_id),
        dequeue_count=job_runner.MAX_DEQUEUE_COUNT,
        next_visible_on=at + timedelta(seconds=900),
    )
    queue = Mock()
    monkeypatch.setattr(
        job_runner,
        "run_video_generation",
        Mock(side_effect=recovery.PrivateSelectionUnverified("readback failed")),
    )
    outcome = job_runner.process_message(message, storage=storage, queue=queue, now=at)
    assert outcome.status == "failed"
    queue.delete_message.assert_not_called()


@pytest.mark.parametrize("recovery_epoch", [False, True])
def test_tampered_selected_output_replay_does_not_ack(recovery_original, tmp_path, recovery_epoch):
    from podcaster.queue import QueueMessage, encode_video_message
    from podcaster.video import job_runner, recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    claim = None
    if recovery_epoch:
        epoch = recovery.reserve_epoch(
            storage,
            job_id,
            expected_identity=identity,
            expected_audio_sha256=mp3_sha,
            inventory_reference="readback",
            now=lambda: at,
        )
        claim = recovery.claim_epoch(
            storage,
            job_id,
            epoch,
            "owner",
            now=lambda: at,
            visibility_expires_at=at + timedelta(seconds=5400),
        )
    source = tmp_path / "candidate.mp4"
    source.write_bytes(b"V" * 2048)
    selected = recovery.select_private_output(
        storage,
        job_id,
        source,
        attempt_id="first",
        claim=claim,
        expected_proof_sha256=recovery._proof(load_manifest(storage, job_id), storage, job_id)[1],
        now=lambda: at,
    )
    storage.put_bytes(selected["outbox_path"], b"{}", "application/json")
    queue = Mock()
    message = QueueMessage(
        message_id="tampered",
        pop_receipt="receipt",
        body=(
            recovery.message(job_id, claim.epoch_id)
            if claim is not None
            else encode_video_message(job_id)
        ),
        dequeue_count=job_runner.MAX_DEQUEUE_COUNT,
        next_visible_on=at + timedelta(seconds=900),
    )
    outcome = job_runner.process_message(message, storage=storage, queue=queue, now=at)
    assert outcome.status == "failed"
    queue.delete_message.assert_not_called()


def test_selected_completion_survives_commit_then_raise_and_failed_or_skipped_updates(
    recovery_original, tmp_path, monkeypatch
):
    from podcaster.queue import QueueMessage
    from podcaster.video import job_runner, recovery
    from podcaster.video.recovery import _proof

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )
    claim = recovery.claim_epoch(
        storage,
        job_id,
        epoch,
        "owner",
        now=lambda: at,
        visibility_expires_at=at + timedelta(seconds=5400),
    )
    proof = _proof(load_manifest(storage, job_id), storage, job_id)[1]
    source = tmp_path / "selected.mp4"
    source.write_bytes(b"V" * 2048)
    update = storage.update_bytes
    once = [True]

    def commit_then_raise(path, content_type, callback):
        result = update(path, content_type, callback)
        if path == f"jobs/{job_id}/manifest.json" and once[0]:
            document = load_manifest(storage, job_id)
            if document["generation"]["video_recovery"].get("selected_private_output"):
                once[0] = False
                raise RuntimeError("selection committed, acknowledgement lost")
        return result

    monkeypatch.setattr(storage, "update_bytes", commit_then_raise)
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        recovery.select_private_output(
            storage,
            job_id,
            source,
            attempt_id="selected",
            claim=claim,
            expected_proof_sha256=proof,
            now=lambda: at,
        )
    selected = recovery.read_selected(storage, job_id)
    for status in ("failed", "skipped"):
        job_runner._record_video_state(
            storage,
            job_id,
            {"status": status, "reason": "post_selection"},
            recovery_claim=claim,
            attempt_id="selected",
            fail_closed=True,
        )
        runner = load_manifest(storage, job_id)["generation"]["video_runner"]
        assert runner["status"] == "completed"
        assert runner["distribution"]["blob_path"] == selected["archive_path"]
    job_runner._record_video_state(
        storage,
        job_id,
        {"status": "completed", "segment_count": 3},
        recovery_claim=claim,
        attempt_id="selected",
        fail_closed=True,
    )
    assert (
        load_manifest(storage, job_id)["generation"]["video_runner"]["distribution"]["blob_path"]
        == selected["archive_path"]
    )
    queue = Mock()
    replay = QueueMessage(
        message_id="replay",
        pop_receipt="new",
        body=recovery.message(job_id, epoch),
        dequeue_count=2,
        next_visible_on=at + timedelta(seconds=900),
    )
    outcome = job_runner.process_message(replay, storage=storage, queue=queue, now=at)
    assert outcome.status == "completed"
    assert outcome.video_blob_path == selected["archive_path"]
    assert recovery.read_selected(storage, job_id) == selected
    durable = load_manifest(storage, job_id)["generation"]["video_runner"]
    assert durable["status"] == "completed"
    assert durable["distribution"]["blob_path"] == selected["archive_path"]
    queue.delete_message.assert_called_once_with(replay)
    with pytest.raises(recovery.RecoveryDenied):
        job_runner._record_video_state(
            storage,
            job_id,
            {"status": "failed"},
            recovery_claim=claim,
            attempt_id="stale",
            fail_closed=True,
        )


@pytest.mark.parametrize("pause_at", ["archive", "selection", "cas_retry"])
def test_old_ordinary_private_writer_cannot_select_after_recovery_claim(
    recovery_original, monkeypatch, tmp_path, pause_at
):
    from podcaster.video import job_runner, recovery
    from podcaster.video.budget import VideoStageBudget
    from podcaster.video.distribution import VideoDistributionConfig

    storage, job_id, identity, mp3_sha, at = recovery_original
    initial = VideoStageBudget.start(now_utc=at, utcnow=lambda: at)
    storage.put_bytes(
        job_runner.video_budget_path(job_id),
        json.dumps(initial.to_dict()).encode(),
        "application/json",
    )
    monkeypatch.setattr(job_runner, "_probe_audio_duration", lambda _: 60.0)
    monkeypatch.setattr(job_runner, "_resolve_dog_logo", lambda *args: None)
    monkeypatch.setattr(
        "podcaster.video.video_gen.record_episode", Mock(return_value=Mock(recorded=[]))
    )

    def compose(*args, output_path, **kwargs):
        output_path.write_bytes(b"O" * 2048)
        return Mock(duration_seconds=60.0, segment_count=1)

    monkeypatch.setattr("podcaster.video.video_compose.compose_video", compose)
    provider = Mock(side_effect=AssertionError("old attempt reached provider"))
    monkeypatch.setattr(job_runner, "distribute_video", provider)
    monkeypatch.setattr(job_runner, "enqueue_distribution_job", provider)
    recovered = {}

    def reserve_after_expiry():
        expired = VideoStageBudget.start(
            now_utc=at - timedelta(seconds=5400),
            utcnow=lambda: at - timedelta(seconds=5400),
        )
        storage.put_bytes(
            job_runner.video_budget_path(job_id),
            json.dumps(expired.to_dict()).encode(),
            "application/json",
        )
        epoch = recovery.reserve_epoch(
            storage,
            job_id,
            expected_identity=identity,
            expected_audio_sha256=mp3_sha,
            inventory_reference="readback-before-takeover",
            now=lambda: at,
        )
        recovered["claim"] = recovery.claim_epoch(
            storage,
            job_id,
            epoch,
            "new-owner",
            now=lambda: at,
            visibility_expires_at=at + timedelta(seconds=5400),
        )

    if pause_at == "archive":
        original_upload = storage.upload_file

        def intercept_upload(path, source, content_type):
            if "private-candidates" in path and "claim" not in recovered:
                reserve_after_expiry()
            return original_upload(path, source, content_type)

        monkeypatch.setattr(storage, "upload_file", intercept_upload)
    else:
        original_update = storage.update_bytes

        def intercept_selection(path, content_type, update):
            if path == job_runner.manifest_path(job_id) and "claim" not in recovered:
                # Only pause at the candidate's final manifest selection callback.
                caller = getattr(update, "__name__", "")
                if caller == "choose":
                    if pause_at == "cas_retry":
                        update(storage.get_bytes(path))
                    reserve_after_expiry()
            return original_update(path, content_type, update)

        monkeypatch.setattr(storage, "update_bytes", intercept_selection)

    with pytest.raises(job_runner.TransientVideoError):
        job_runner.run_video_generation(
            job_id,
            storage,
            config=VideoDistributionConfig(youtube_enabled=True),
            fanout=False,
            now=at,
            ownership_execution_id="old-ordinary-worker",
        )
    provider.assert_not_called()
    claim = recovered["claim"]
    new_source = tmp_path / "recovery.mp4"
    new_source.write_bytes(b"R" * 2048)
    proof = recovery._proof(load_manifest(storage, job_id), storage, job_id)[1]
    chosen = recovery.select_private_output(
        storage,
        job_id,
        new_source,
        attempt_id="recovery",
        claim=claim,
        expected_proof_sha256=proof,
        now=lambda: at,
    )
    assert recovery.read_selected(storage, job_id) == chosen
    assert chosen["mp4_sha256"] == sha256(b"R" * 2048).hexdigest()
    assert storage.blob_size(chosen["archive_path"]) == 2048
    assert len(storage.list_blobs(f"jobs/{job_id}/video/private-candidates/", limit=20)) == 4
    provider.assert_not_called()


@pytest.mark.parametrize(
    "provider_state", ["prior_id", "unknown", "corrupt_evidence", "orphan_outbox"]
)
def test_identity_wide_prior_provider_state_blocks_recovery_reservation(
    recovery_original, provider_state
):
    from podcaster.distribution_outbox import outbox_id_for, outbox_path
    from podcaster.generation import manifest_bytes
    from podcaster.publication_state import evidence_path
    from podcaster.video.recovery import RecoveryDenied, reserve_epoch

    storage, job_id, identity, mp3_sha, at = recovery_original
    path = f"jobs/{job_id}/manifest.json"
    if provider_state == "corrupt_evidence":
        storage.put_bytes(evidence_path(job_id), b"{", "application/json")
    elif provider_state == "orphan_outbox":
        orphan = outbox_id_for(
            identity, media_kind="video", provider_objectives={"youtube": "public"}
        )
        storage.put_bytes(outbox_path(orphan), b"{}", "application/json")
    else:
        manifest = load_manifest(storage, job_id)
        manifest["generation"]["video_publish"] = {
            "youtube": {"provider_id": "id-1", "outcome": "draft_created"}
            if provider_state == "prior_id"
            else {"outcome": "publication_unknown", "retry_blocked": True}
        }
        storage.put_bytes(path, manifest_bytes(manifest), "application/json")
    with pytest.raises(RecoveryDenied):
        reserve_epoch(
            storage,
            job_id,
            expected_identity=identity,
            expected_audio_sha256=mp3_sha,
            inventory_reference="readback",
            now=lambda: at,
        )
    assert "video_recovery" not in load_manifest(storage, job_id)["generation"]


@pytest.mark.parametrize(
    "distribution",
    [
        {"provider_outcomes": {"spotify_upload": "publication_unknown"}},
        {"provider_outcomes": {"spotify": "pending_provider"}},
        {"provider_outcomes": {"unknown": "unknown"}},
        {"provider_outcomes": "invalid"},
        {"provider_records": {"spotify": {"status": "unknown"}}},
        {"provider_records": ["invalid"]},
        {"spotify_rss_updated": True},
        {"spotify_rss_updated": "unknown"},
        "invalid",
    ],
)
@pytest.mark.parametrize("phase", ["reservation", "selection"])
def test_runner_only_provider_state_blocks_private_recovery(
    recovery_original, tmp_path, distribution, phase
):
    from podcaster.generation import manifest_bytes
    from podcaster.video import recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    if phase == "selection":
        epoch = recovery.reserve_epoch(
            storage,
            job_id,
            expected_identity=identity,
            expected_audio_sha256=mp3_sha,
            inventory_reference="readback",
            now=lambda: at,
        )
        claim = recovery.claim_epoch(
            storage,
            job_id,
            epoch,
            "owner",
            now=lambda: at,
            visibility_expires_at=at + timedelta(seconds=5400),
        )
    manifest = load_manifest(storage, job_id)
    manifest["generation"]["video_runner"] = {"distribution": distribution}
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json")
    source = tmp_path / "candidate.mp4"
    source.write_bytes(b"V" * 2048)
    with pytest.raises(recovery.RecoveryDenied):
        if phase == "reservation":
            recovery.reserve_epoch(
                storage,
                job_id,
                expected_identity=identity,
                expected_audio_sha256=mp3_sha,
                inventory_reference="readback",
                now=lambda: at,
            )
        else:
            recovery.select_private_output(
                storage,
                job_id,
                source,
                attempt_id="first",
                claim=claim,
                expected_proof_sha256=recovery._proof(manifest, storage, job_id)[1],
                now=lambda: at,
            )
    state = load_manifest(storage, job_id)["generation"].get("video_recovery", {})
    assert state.get("selected_private_output") is None
    if phase == "reservation":
        assert not state.get("epochs")


def test_empty_runner_provider_state_remains_quiet_for_recovery(recovery_original):
    from podcaster.generation import manifest_bytes
    from podcaster.video.recovery import reserve_epoch

    storage, job_id, identity, mp3_sha, at = recovery_original
    manifest = load_manifest(storage, job_id)
    manifest["generation"]["video_runner"] = {
        "distribution": {
            "provider_outcomes": {},
            "provider_records": {},
            "spotify_rss_updated": False,
        }
    }
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json")
    assert reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )


@pytest.mark.parametrize(
    "platform,kind,allowed",
    [
        ("youtube", None, False),
        ("youtube", "unexpected", False),
        ("youtube", "video", False),
        ("youtube", ["audio"], False),
        ("spotify", None, False),
        (["spotify"], "audio", False),
        (None, "audio", False),
        ("unrecognized", "audio", False),
        ("spotify", "audio", True),
    ],
)
def test_recovery_requires_explicit_recognized_audio_evidence_scope(
    recovery_original, platform, kind, allowed
):
    from podcaster.publication_state import evidence_path
    from podcaster.video.recovery import RecoveryDenied, reserve_epoch

    storage, job_id, identity, mp3_sha, at = recovery_original
    record = {
        "job_id": job_id,
        "week": identity.week,
        "publish_run_id": identity.publish_run_id,
        "article_sha256": identity.article_sha256,
        "manifest_sha256": identity.manifest_sha256,
        "platform": platform,
        "operation": "upload",
        "outcome": "uploaded",
    }
    if kind is not None:
        record["media_kind"] = kind
    evidence = {
        "schema_version": "squadscope-podcaster-publication-evidence-v1",
        "job_id": job_id,
        "records": [record],
    }
    storage.put_bytes(evidence_path(job_id), json.dumps(evidence).encode(), "application/json")
    if allowed:
        assert reserve_epoch(
            storage,
            job_id,
            expected_identity=identity,
            expected_audio_sha256=mp3_sha,
            inventory_reference="readback",
            now=lambda: at,
        )
    else:
        with pytest.raises(RecoveryDenied):
            reserve_epoch(
                storage,
                job_id,
                expected_identity=identity,
                expected_audio_sha256=mp3_sha,
                inventory_reference="readback",
                now=lambda: at,
            )
        assert "video_recovery" not in load_manifest(storage, job_id)["generation"]


def test_api_queue_construction_failure_reconciles_deadline_and_recovers(
    recovery_original, monkeypatch
):
    from podcaster import monitoring
    from podcaster.video import recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    monkeypatch.setitem(recovery.PRIVATE_ORIGINALS, job_id, 0)
    monkeypatch.setitem(
        recovery.SCOPED_IDENTITIES,
        job_id,
        (identity.week, identity.publish_run_id, identity.article_sha256, identity.manifest_sha256),
    )
    recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference=recovery.SCOPED_PRIVATE_DIRECTIVE,
        scoped_private=True,
        now=lambda: at,
    )
    original = recovery.reconcile_pending
    from threading import Event

    reconciled = Event()
    elapsed = [0]

    def reconcile(storage_arg, send, *, scoped_only=False):
        try:
            return original(
                storage_arg,
                send,
                now=lambda: at + timedelta(seconds=elapsed[0]),
                scoped_only=scoped_only,
            )
        finally:
            reconciled.set()

    sent = []

    def unavailable():
        raise RuntimeError("queue constructor failed")

    monkeypatch.delenv("PODCASTER_VIDEO_RECOVERY_OPERATOR_SECRET_URI", raising=False)
    monkeypatch.setattr(monitoring, "get_storage", lambda: storage)
    monkeypatch.setattr(recovery, "reconcile_pending", reconcile)
    monkeypatch.setattr("podcaster.queue.create_video_queue_backend", unavailable)
    with TestClient(monitoring.app):
        assert reconciled.wait(3)
    assert not sent
    epoch = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert epoch["status"] == "reserved"
    assert epoch["dispatch"]["status"] == "pending"

    elapsed[0] = 5101
    reconciled.clear()
    with TestClient(monitoring.app):
        assert reconciled.wait(3)
    assert not sent
    epoch = load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]
    assert epoch["status"] == "terminal"
    assert epoch["terminal_reason"] == "deadline_elapsed"

    reconciled.clear()
    with pytest.raises(recovery.RecoveryDenied):
        recovery.reserve_epoch(
            storage,
            job_id,
            expected_identity=identity,
            expected_audio_sha256=mp3_sha,
            inventory_reference="fresh-readback",
            now=lambda: at + timedelta(seconds=5101),
        )

    def healthy(storage_arg, send, *, scoped_only=False):
        try:
            return original(
                storage_arg,
                send,
                now=lambda: at + timedelta(seconds=5101),
                scoped_only=scoped_only,
            )
        finally:
            reconciled.set()

    monkeypatch.setattr(recovery, "reconcile_pending", healthy)
    monkeypatch.setattr(
        "podcaster.queue.create_video_queue_backend",
        lambda: Mock(send_message=sent.append),
    )
    with TestClient(monitoring.app):
        assert reconciled.wait(3)
    assert not sent
    assert [
        item["status"]
        for item in load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"]
    ] == ["terminal"]


@pytest.mark.parametrize(
    "job_id",
    ["podcast-2026-W44-00bc06dfb6e2", "podcast-2026-W45-9798e93edc06"],
)
def test_private_original_cannot_reach_distribution_or_final_adapters(tmp_path, job_id):
    from podcaster import publish
    from podcaster.distribution_dispatch import dispatch_reviewed_video
    from podcaster.video.distribution import (
        VideoDistributionConfig,
        distribute_video,
        upload_to_spotify_episode,
        upload_to_youtube,
    )
    from podcaster.video.recovery import PRIVATE_ORIGINALS, RecoveryDenied, scoped_identity

    assert job_id in PRIVATE_ORIGINALS
    path = tmp_path / "completed.mp4"
    path.write_bytes(b"V" * 2048)
    youtube = Mock(side_effect=AssertionError("YouTube network called"))
    spotify = Mock(side_effect=AssertionError("Spotify network called"))
    config = VideoDistributionConfig(
        youtube_enabled=True,
        spotify_upload_enabled=True,
        dry_run=False,
    )
    identity = scoped_identity(job_id)
    with pytest.raises(RecoveryDenied):
        distribute_video(path, job_id, "title", "description", 60, config)
    with pytest.raises(RecoveryDenied):
        upload_to_youtube(
            path,
            "title",
            "description",
            config,
            transport=youtube,
            job_id=job_id,
        )
    with pytest.raises(RecoveryDenied):
        upload_to_spotify_episode(
            path,
            None,
            config,
            job_id=job_id,
            publication_identity_context=identity,
        )
    with pytest.raises(RecoveryDenied):
        publish.upload_video_to_episode(
            path,
            title="title",
            publication_identity_context=identity,
        )
    with pytest.raises(RecoveryDenied):
        dispatch_reviewed_video(
            None,
            None,
            {"publication_identity": {"accepted_job_id": job_id}},
            transport=spotify,
        )
    youtube.assert_not_called()
    spotify.assert_not_called()


@pytest.mark.parametrize(
    "job_id,identity",
    [
        ("job-1", None),
        ("job-1", {"accepted_job_id": "job-1"}),
        ("job-1", PublicationIdentity("other", "2026-W44", "1", "a" * 64, "b" * 64)),
        ("job-1", PublicationIdentity("job-1", "bad-week", "1", "a" * 64, "b" * 64)),
        ("job-1", PublicationIdentity("job-1", "2026-W44", "", "a" * 64, "b" * 64)),
        (
            "podcast-2026-W44-00bc06dfb6e2",
            PublicationIdentity(
                "podcast-2026-W44-00bc06dfb6e2",
                "2026-W44",
                "1",
                "a" * 64,
                "b" * 64,
            ),
        ),
    ],
)
def test_spotify_video_boundaries_require_coherent_identity_before_dry_run_or_session(
    tmp_path, monkeypatch, job_id, identity
):
    from podcaster import publish
    from podcaster.video.distribution import VideoDistributionConfig, upload_to_spotify_episode
    from podcaster.video.recovery import RecoveryDenied

    video = tmp_path / "video.mp4"
    video.write_bytes(b"V" * 2048)
    session = Mock(side_effect=AssertionError("session reached"))
    monkeypatch.setattr(publish, "_build_session", session)
    monkeypatch.setenv("SPOTIFY_PUBLISH_DRY_RUN", "true")
    with pytest.raises(RecoveryDenied):
        upload_to_spotify_episode(
            video,
            None,
            VideoDistributionConfig(dry_run=True),
            job_id=job_id,
            publication_identity_context=identity,
        )
    with pytest.raises(RecoveryDenied):
        publish.upload_video_to_episode(
            video,
            title="Video",
            job_id=job_id,
            publication_identity_context=identity,
        )
    session.assert_not_called()


def test_recovery_worker_renders_only_private_selected_candidate(recovery_original, monkeypatch):
    import time

    from podcaster.video import job_runner, recovery
    from podcaster.video.distribution import VideoDistributionConfig
    from podcaster.video.ownership import VideoOwnershipGuard

    storage, job_id, identity, mp3_sha, at = recovery_original
    old_guard = VideoOwnershipGuard.acquire(
        storage,
        job_id,
        owner="video-runner",
        execution_id="old-ordinary-worker",
        visibility_expires_at=at + timedelta(seconds=5400),
        now=lambda: at,
    )
    old_permit = old_guard.begin("immutable_archive", allow_idempotent_takeover=True)
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )
    visibility = at + timedelta(seconds=5400)
    claim = recovery.claim_epoch(
        storage,
        job_id,
        epoch,
        "recovery-worker",
        now=lambda: at,
        visibility_expires_at=visibility,
    )
    monkeypatch.setattr(job_runner, "_probe_audio_duration", lambda _: 60.0)
    monkeypatch.setattr(job_runner, "_resolve_dog_logo", lambda *args: None)
    monkeypatch.setattr(
        "podcaster.video.video_gen.record_episode", Mock(return_value=Mock(recorded=[]))
    )

    def compose(*args, output_path, **kwargs):
        output_path.write_bytes(b"R" * 2048)
        return Mock(duration_seconds=60.0, segment_count=1)

    monkeypatch.setattr("podcaster.video.video_compose.compose_video", compose)
    provider = Mock(side_effect=AssertionError("recovery attempted provider mutation"))
    monkeypatch.setattr(job_runner, "distribute_video", provider)
    monkeypatch.setattr(job_runner, "enqueue_distribution_job", provider)
    outcome = job_runner.run_video_generation(
        job_id,
        storage,
        config=VideoDistributionConfig(youtube_enabled=True),
        fanout=False,
        now=at,
        ownership_execution_id="recovery-worker",
        ownership_visibility_expires_at=visibility,
        lifecycle_deadline_monotonic=time.monotonic() - 10,
        recovery_visibility=lambda: datetime.now(timezone.utc) + timedelta(seconds=900),
        recovery_claim=claim,
    )
    assert outcome.status == "completed"
    assert outcome.distribution.public_delivery_status == "awaiting_review"
    selected = recovery.read_selected(storage, job_id)
    assert selected["epoch_id"] == epoch
    assert storage.get_bytes(selected["outbox_path"])
    assert outcome.video_blob_path == selected["archive_path"]
    assert load_manifest(storage, job_id)["generation"]["video_runner"]["status"] == "completed"
    old_guard.assert_permit(old_permit)
    provider.assert_not_called()


def test_stale_and_late_recovery_messages_never_start_video(recovery_original, monkeypatch):
    from podcaster.queue import QueueMessage, encode_video_message
    from podcaster.video import job_runner, recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )
    forbidden = Mock(side_effect=AssertionError("stale message created video clients"))
    monkeypatch.setattr(job_runner, "create_intermediate_store", forbidden)
    queue = Mock()
    old = QueueMessage(
        message_id="old",
        pop_receipt="old",
        body=encode_video_message(job_id),
        dequeue_count=2,
        next_visible_on=at + timedelta(seconds=5400),
    )
    assert job_runner.process_message(old, storage=storage, queue=queue, now=at).reason == (
        job_runner.REASON_STALE_VIDEO_ATTEMPT
    )
    for next_visible, expected in (
        (None, "recovery_authority_denied"),
        (at + timedelta(seconds=10800), "recovery_authority_denied"),
    ):
        msg = QueueMessage(
            message_id="epoch",
            pop_receipt="claim",
            body=recovery.message(job_id, epoch),
            dequeue_count=2,
            next_visible_on=next_visible,
        )
        current = at + timedelta(seconds=5400) if next_visible is not None else at
        assert (
            job_runner.process_message(
                msg,
                storage=storage,
                queue=queue,
                now=current,
            ).reason
            == expected
        )
    forbidden.assert_not_called()
    assert queue.delete_message.call_count == 3
    assert (
        load_manifest(storage, job_id)["generation"]["video_recovery"]["epochs"][0]["status"]
        == "terminal"
    )


def test_precommit_orphan_is_not_automatically_reselected(recovery_original, monkeypatch, tmp_path):
    from podcaster.video import recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )
    claim = recovery.claim_epoch(
        storage,
        job_id,
        epoch,
        "owner",
        now=lambda: at,
        visibility_expires_at=at + timedelta(seconds=5400),
    )
    proof = recovery._proof(load_manifest(storage, job_id), storage, job_id)[1]
    source = tmp_path / "orphan.mp4"
    source.write_bytes(b"O" * 2048)
    original_update = storage.update_bytes

    def fail_before_selection(path, content_type, update):
        if path.endswith("/manifest.json") and update.__name__ == "choose":
            raise RuntimeError("crash before selected pointer CAS")
        return original_update(path, content_type, update)

    monkeypatch.setattr(storage, "update_bytes", fail_before_selection)
    with pytest.raises(RuntimeError, match="crash before selected"):
        recovery.select_private_output(
            storage,
            job_id,
            source,
            attempt_id="crashed",
            claim=claim,
            expected_proof_sha256=proof,
            now=lambda: at,
        )
    monkeypatch.setattr(storage, "update_bytes", original_update)
    assert (
        load_manifest(storage, job_id)["generation"]["video_recovery"]["pending_selection_attempt"][
            "attempt_id"
        ]
        == "crashed"
    )
    with pytest.raises(recovery.RecoveryDenied, match="orphaned"):
        recovery.select_private_output(
            storage,
            job_id,
            source,
            attempt_id="automaticretry",
            claim=claim,
            expected_proof_sha256=proof,
            now=lambda: at,
        )
    with pytest.raises(recovery.RecoveryDenied):
        recovery.read_selected(storage, job_id)


def test_postcommit_retry_resolves_same_verified_private_pointer(
    recovery_original, tmp_path, monkeypatch
):
    from podcaster.queue import QueueMessage
    from podcaster.video import job_runner, recovery

    storage, job_id, identity, mp3_sha, at = recovery_original
    epoch = recovery.reserve_epoch(
        storage,
        job_id,
        expected_identity=identity,
        expected_audio_sha256=mp3_sha,
        inventory_reference="readback",
        now=lambda: at,
    )
    claim = recovery.claim_epoch(
        storage,
        job_id,
        epoch,
        "owner",
        now=lambda: at,
        visibility_expires_at=at + timedelta(seconds=5400),
    )
    source = tmp_path / "selected.mp4"
    source.write_bytes(b"S" * 2048)
    proof = recovery._proof(load_manifest(storage, job_id), storage, job_id)[1]
    selected = recovery.select_private_output(
        storage,
        job_id,
        source,
        attempt_id="committed",
        claim=claim,
        expected_proof_sha256=proof,
        now=lambda: at,
    )
    precrash_state = load_manifest(storage, job_id)["generation"]["video_runner"]
    assert precrash_state["status"] == "completed"
    assert precrash_state["distribution"]["blob_path"] == selected["archive_path"]
    assert precrash_state["distribution"]["public_delivery_status"] == "awaiting_review"
    forbidden = Mock(side_effect=AssertionError("post-commit retry rerendered"))
    monkeypatch.setattr(job_runner, "create_intermediate_store", forbidden)
    queue = Mock()
    retry = QueueMessage(
        message_id="retry",
        pop_receipt="new-receipt",
        body=recovery.message(job_id, epoch),
        dequeue_count=2,
        next_visible_on=at + timedelta(seconds=10800),
    )
    outcome = job_runner.process_message(
        retry, storage=storage, queue=queue, now=at + timedelta(seconds=5400)
    )
    assert outcome.status == "completed"
    assert outcome.distribution.public_delivery_status == "awaiting_review"
    assert outcome.video_blob_path == selected["archive_path"]
    assert recovery.read_selected(storage, job_id) == selected
    assert load_manifest(storage, job_id)["generation"]["video_runner"] == precrash_state
    queue.delete_message.assert_called_once_with(retry)
    forbidden.assert_not_called()


def test_ordinary_postcommit_retry_reuses_same_private_pointer(
    recovery_original, tmp_path, monkeypatch
):
    from podcaster.queue import QueueMessage, encode_video_message
    from podcaster.video import job_runner, recovery

    storage, job_id, _identity, _mp3_sha, at = recovery_original
    source = tmp_path / "ordinary.mp4"
    source.write_bytes(b"Q" * 2048)
    proof = recovery._proof(load_manifest(storage, job_id), storage, job_id)[1]
    chosen = recovery.select_private_output(
        storage,
        job_id,
        source,
        attempt_id="ordinary",
        claim=None,
        expected_proof_sha256=proof,
        now=lambda: at,
    )
    forbidden = Mock(side_effect=AssertionError("ordinary retry regenerated private video"))
    monkeypatch.setattr(job_runner, "create_intermediate_store", forbidden)
    queue = Mock()
    retry = QueueMessage(
        message_id="ordinary",
        pop_receipt="new-receipt",
        body=encode_video_message(job_id),
        dequeue_count=2,
        next_visible_on=at + timedelta(seconds=5400),
    )
    outcome = job_runner.process_message(
        retry, storage=storage, queue=queue, now=at + timedelta(seconds=5400)
    )
    assert outcome.video_blob_path == chosen["archive_path"]
    assert recovery.read_selected(storage, job_id) == chosen
    assert outcome.distribution.public_delivery_status == "awaiting_review"
    queue.delete_message.assert_called_once_with(retry)
    forbidden.assert_not_called()


def test_exact_review_private_video_separate_media_decision_and_existing_outbox_progression(
    prepared_exact_video,
):
    from podcaster.distribution_outbox import MediaReviewRequiredError

    storage, job_id, repository, document, *_ = prepared_exact_video
    with pytest.raises(MediaReviewRequiredError):
        repository.claim(
            document["outbox_id"], owner="worker", execution_id="one", lease_seconds=600
        )
    # Pending review cannot be queued by either existing scheduler scan.
    notified = Mock()
    assert repository.repair_notifications_page(notified)[0] == 0
    assert repository.due_reconciliations_page()[0] == []
    notified.assert_not_called()
    _video_decision(storage, job_id, document)
    approved = repository.read(document["outbox_id"])
    assert provider_approval_is_valid(approved, "youtube")
    assert approved["media_review"]["binding"]["artifact_sha256"] == document["artifact"]["sha256"]

    # Queue notification uses the existing scheduler's consumed one-shot intent.
    def notify(_id, authorize_send, mark_accepted):
        assert authorize_send()
        assert mark_accepted()
        return True

    assert repository.repair_notifications_page(notify)[0] == 1
    assert repository.repair_notifications_page(notify)[0] == 0
    claim = repository.claim(
        document["outbox_id"], owner="worker", execution_id="one", lease_seconds=600
    )
    repository.persist_intent(claim, provider="youtube", operation="draft_upload")
    repository.consume_intent(
        claim, provider="youtube", provider_timeout_seconds=60, receipt_margin_seconds=15
    )
    # Ordinary audio review/publication remains closed even after video approval.
    assert _distribution_human_approval(load_manifest(storage, job_id)) == {"approved": False}


class _ReviewedYouTubeTransport:
    def __init__(self, *, processing_status="succeeded", upload_status="processed"):
        self.video = None
        self.mutations = []
        self.bytes_uploaded = None
        self.processing_status = processing_status
        self.upload_status = upload_status
        self.readback_parts = []

    def request_with_headers(self, url, *, method="GET", headers=None, data=None):
        assert method == "POST"
        self.mutations.append("upload")
        self.video = {"id": "reviewed-video", **json.loads(data)}
        self.video["snippet"]["channelId"] = "owned-channel"
        self.video["status"]["uploadStatus"] = self.upload_status
        if self.processing_status is not None:
            self.video["processingDetails"] = {"processingStatus": self.processing_status}
        return 200, {"location": "https://www.googleapis.com/upload-session"}, b""

    def request(self, url, *, method="GET", headers=None, data=None):
        if method == "PUT" and "upload-session" in url:
            self.bytes_uploaded = data
            return 200, b'{"id":"reviewed-video"}'
        if method == "PUT":
            self.mutations.append("promote")
            self.video["status"]["privacyStatus"] = json.loads(data)["status"]["privacyStatus"]
            return 200, b"{}"
        if "/channels?" in url:
            return 200, json.dumps(
                {
                    "items": [
                        {
                            "id": "owned-channel",
                            "contentDetails": {"relatedPlaylists": {"uploads": "owned-uploads"}},
                        }
                    ]
                }
            ).encode()
        if "/playlistItems?" in url:
            return 200, b'{"items":[]}'
        assert "/videos?" in url
        from urllib.parse import parse_qs, urlparse

        self.readback_parts.append(parse_qs(urlparse(url).query)["part"][0])
        return 200, json.dumps({"items": [self.video] if self.video else []}).encode()


def _run_reviewed_worker(storage, document):
    from podcaster.distribution_worker import process_message
    from podcaster.queue import encode_distribution_message
    from tests.test_distribution_worker import MemoryQueue, _message

    return process_message(
        _message(encode_distribution_message(document["outbox_id"])),
        storage=storage,
        queue=MemoryQueue(),
        mutation_enabled=True,
    )


def test_real_dispatcher_uploads_exact_reviewed_mp4_then_promotes_once(
    prepared_exact_video, monkeypatch
):
    from podcaster.video.distribution import VideoDistributionConfig

    storage, job_id, repository, document, config, _composer = prepared_exact_video
    http = _ReviewedYouTubeTransport()
    monkeypatch.setattr(VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda: http)
    monkeypatch.setattr(
        "podcaster.video.distribution._get_youtube_access_token", lambda *_: "offline"
    )
    # Even explicitly enabling the real worker cannot consume pending media.
    assert _run_reviewed_worker(storage, document).reason == "media_review_required"
    assert http.mutations == []
    _video_decision(storage, job_id, document)
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert http.bytes_uploaded == storage.get_bytes(document["artifact"]["path"])
    assert http.mutations == ["upload", "promote"]
    metadata = load_manifest(storage, job_id)["generation"]["video_runner"]["publication_metadata"]
    assert http.video["snippet"]["title"] == metadata["title"] == "Editorial episode title"
    assert http.video["snippet"]["description"].startswith(metadata["description"] + "\n\n")
    assert "A reviewed technical summary." in metadata["description"]
    assert "Credits:" in metadata["description"]
    assert "https://credits.example/theme" in metadata["description"]
    completed = repository.read(document["outbox_id"])
    assert completed["aggregate"]["externally_verified_public"] is True
    assert [r["operation"] for r in completed["providers"]["youtube"]["receipts"]] == [
        "draft_upload",
        "public_promotion",
    ]
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert http.mutations == ["upload", "promote"]


@pytest.mark.parametrize(
    ("processing_status", "upload_status"),
    [
        ("processing", "uploaded"),
        ("failed", "processed"),
        (None, "processed"),
        ("succeeded", "failed"),
    ],
)
def test_real_dispatcher_blocks_unready_youtube_before_public_promotion(
    prepared_exact_video, monkeypatch, processing_status, upload_status
):
    from podcaster.video import distribution

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    original_upload = distribution.upload_to_youtube
    upload_jobs = []

    def observe_upload(*args, **kwargs):
        upload_jobs.append(kwargs.get("job_id"))
        return original_upload(*args, **kwargs)

    monkeypatch.setattr(distribution, "upload_to_youtube", observe_upload)
    http = _ReviewedYouTubeTransport(
        processing_status=processing_status, upload_status=upload_status
    )
    monkeypatch.setattr(distribution.VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(distribution, "_DefaultTransport", lambda: http)
    monkeypatch.setattr(distribution, "_get_youtube_access_token", lambda *_: "offline")
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert http.mutations == ["upload"]
    assert upload_jobs == [job_id]
    state = repository.read(document["outbox_id"])
    assert state["aggregate"]["externally_verified_public"] is False
    assert state["providers"]["youtube"]["result"] == "pending_provider"
    assert state["providers"]["youtube"]["verification"]["source"] == "youtube_processing_not_ready"
    assert all("processingDetails" in parts for parts in http.readback_parts)

    # Even public privacy on a later reconciliation cannot certify playability.
    http.video["status"]["privacyStatus"] = "public"
    due = state["providers"]["youtube"]["next_reconcile_at"]
    repository.now = lambda: datetime.fromisoformat(due.replace("Z", "+00:00"))
    monkeypatch.setattr(
        "podcaster.distribution_worker.DistributionOutboxRepository", lambda _: repository
    )
    readbacks = len(http.readback_parts)
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    state = repository.read(document["outbox_id"])
    assert state["aggregate"]["externally_verified_public"] is False
    assert len(http.readback_parts) > readbacks
    assert http.mutations == ["upload"]


def test_real_dispatcher_rechecks_youtube_processing_after_promotion(
    prepared_exact_video, monkeypatch
):
    from podcaster.video import distribution

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)

    class FailedAfterPromotion(_ReviewedYouTubeTransport):
        def request(self, url, **kwargs):
            result = super().request(url, **kwargs)
            if "promote" in self.mutations:
                self.video["processingDetails"]["processingStatus"] = "failed"
            return result

    http = FailedAfterPromotion()
    monkeypatch.setattr(distribution.VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(distribution, "_DefaultTransport", lambda: http)
    monkeypatch.setattr(distribution, "_get_youtube_access_token", lambda *_: "offline")
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert http.mutations == ["upload", "promote"]
    state = repository.read(document["outbox_id"])
    assert state["aggregate"]["externally_verified_public"] is False
    assert state["providers"]["youtube"]["verification"]["source"] == "youtube_processing_not_ready"


@pytest.mark.parametrize("final_state", ["processing", "public", "private"])
def test_real_dispatcher_final_permitted_youtube_poll_is_durable(
    prepared_exact_video, monkeypatch, final_state
):
    from podcaster.video import distribution

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    http = _ReviewedYouTubeTransport(processing_status="processing")
    monkeypatch.setattr(distribution.VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(distribution, "_DefaultTransport", lambda: http)
    monkeypatch.setattr(distribution, "_get_youtube_access_token", lambda *_: "offline")
    monkeypatch.setattr(
        "podcaster.distribution_worker.DistributionOutboxRepository", lambda _: repository
    )
    for attempt in range(1, 9):
        if attempt == 8 and final_state != "processing":
            http.video["processingDetails"]["processingStatus"] = "succeeded"
            http.video["status"]["privacyStatus"] = final_state
        assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
        state = repository.read(document["outbox_id"])
        leg = state["providers"]["youtube"]
        assert leg["verification_attempt"] == attempt
        assert len(http.readback_parts) == attempt
        assert http.mutations == ["upload"]
        if attempt < 8:
            assert leg["result"] == "pending_provider"
            assert state["attempts"][-1]["terminal_outcome"] is None
            due = datetime.fromisoformat(leg["next_reconcile_at"].replace("Z", "+00:00"))
            repository.now = lambda: due
    assert state["claim"] is None
    assert leg["next_reconcile_at"] is None
    assert leg["active_schedule_token"] is None
    assert repository.due_reconciliations() == []
    if final_state == "public":
        assert leg["result"] == "externally_verified_public"
        assert state["attempts"][-1]["terminal_outcome"] == "published_verified"
    else:
        assert leg["result"] == leg["verification"]["result"] == "manual_handoff_required"
        assert leg["verification"]["exhaustion_reason"] == (
            "provider verification budget is exhausted"
        )
        assert state["attempts"][-1]["terminal_outcome"] == "manual_action_required"
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert len(http.readback_parts) == 8
    assert http.mutations == ["upload"]


def test_real_dispatcher_delayed_due_message_persists_expired_pending_handoff(
    prepared_exact_video, monkeypatch
):
    from podcaster.video import distribution

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    http = _ReviewedYouTubeTransport(processing_status="processing")
    monkeypatch.setattr(distribution.VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(distribution, "_DefaultTransport", lambda: http)
    monkeypatch.setattr(distribution, "_get_youtube_access_token", lambda *_: "offline")
    monkeypatch.setattr(
        "podcaster.distribution_worker.DistributionOutboxRepository", lambda _: repository
    )
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    leg = repository.read(document["outbox_id"])["providers"]["youtube"]
    late = datetime.fromisoformat(leg["verification_horizon_at"].replace("Z", "+00:00"))
    repository.now = lambda: late + timedelta(seconds=1)
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    state = repository.read(document["outbox_id"])
    leg = state["providers"]["youtube"]
    assert leg["result"] == leg["verification"]["result"] == "manual_handoff_required"
    assert leg["verification"]["exhaustion_reason"] == (
        "provider verification horizon is exhausted"
    )
    assert leg["verification_attempt"] == 1
    assert leg["next_reconcile_at"] is None
    assert leg["active_schedule_token"] is None
    assert state["claim"] is None
    assert state["attempts"][-1]["terminal_outcome"] == "manual_action_required"
    assert repository.due_reconciliations() == []
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert len(http.readback_parts) == 1
    assert http.mutations == ["upload"]


@pytest.mark.parametrize(
    "prepared_exact_video",
    [
        {"target": "youtube", "metadata": {"article_title": "T" * 101}},
        {"target": "youtube", "metadata": {"article_summary": "S" * 4920}},
        {"target": "youtube", "metadata": {"article_summary": "S" * 5100}},
    ],
    indirect=True,
)
def test_real_dispatcher_binds_adapter_bounded_metadata_and_retains_marker(
    prepared_exact_video, monkeypatch
):
    from podcaster.video import distribution

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    http = _ReviewedYouTubeTransport(processing_status="processing")
    monkeypatch.setattr(distribution.VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(distribution, "_DefaultTransport", lambda: http)
    monkeypatch.setattr(distribution, "_get_youtube_access_token", lambda *_: "offline")
    monkeypatch.setattr(
        "podcaster.distribution_worker.DistributionOutboxRepository", lambda _: repository
    )
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    metadata = load_manifest(storage, job_id)["generation"]["video_runner"]["publication_metadata"]
    snippet = http.video["snippet"]
    marker = f"Publication {document['publication_digest']}"
    assert snippet["title"] == metadata["title"][:100]
    assert snippet["description"] == metadata["description"][: 5000 - len(marker) - 2] + (
        "\n\n" + marker
    )
    assert len(snippet["title"]) <= 100 and len(snippet["description"]) <= 5000
    assert snippet["description"].endswith(marker)
    leg = repository.read(document["outbox_id"])["providers"]["youtube"]
    due = datetime.fromisoformat(leg["next_reconcile_at"].replace("Z", "+00:00"))
    repository.now = lambda: due
    http.video["processingDetails"]["processingStatus"] = "succeeded"
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert repository.read(document["outbox_id"])["aggregate"]["externally_verified_public"]
    assert http.mutations == ["upload", "promote"]


@pytest.mark.parametrize(
    "drift",
    [
        "mp4",
        "provider",
        "job",
        "digest",
        "identity",
        "context",
        "window",
        "audio_replaced",
        "metadata",
    ],
)
def test_real_dispatcher_rejects_changed_bindings_before_provider_io(
    prepared_exact_video, monkeypatch, drift
):
    from dataclasses import replace

    from podcaster.distribution_outbox import DistributionOutboxError, outbox_path
    from podcaster.generation import manifest_bytes
    from podcaster.video.distribution import VideoDistributionConfig

    storage, job_id, repository, document, config, _composer = prepared_exact_video
    _video_decision(storage, job_id, document)
    document = repository.read(document["outbox_id"])
    if drift == "mp4":
        storage.put_bytes(document["artifact"]["path"], b"wrong" * 410, "video/mp4")
    elif drift == "audio_replaced":
        manifest = load_manifest(storage, job_id)
        audio = manifest["generation"]["synthesis_runner"]["audio"]
        replacement = b"other-complete-audio"
        storage.put_bytes(audio["path"], replacement, "audio/mpeg")
        for proof in (audio, audio["artifacts"]["mp3"], manifest["artifacts"][audio["path"]]):
            proof["sha256"] = sha256(replacement).hexdigest()
            proof["size_bytes"] = len(replacement)
        storage.put_bytes(
            f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json"
        )
    elif drift == "provider":
        config = replace(config, youtube_client_id="different-account")
    elif drift == "job":
        manifest = load_manifest(storage, job_id)
        manifest["generation"]["video_runner"]["distribution"]["outbox_id"] = "c" * 64
        storage.put_bytes(
            f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json"
        )
    elif drift == "metadata":
        manifest = load_manifest(storage, job_id)
        manifest["generation"]["video_runner"]["publication_metadata"]["description"] = (
            "Changed copy"
        )
        storage.put_bytes(
            f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json"
        )
    elif drift == "identity":
        document["publication_identity"]["article_sha256"] = "c" * 64
    elif drift == "digest":
        document["publication_digest"] = "c" * 64
    elif drift == "context":
        document["providers"]["youtube"]["context"]["playlist_id"] = "wrong-playlist"
    elif drift == "window":
        document["providers"]["youtube"]["verification_horizon_at"] = "2026-01-01T00:00:00Z"
    if drift in {"identity", "digest", "context", "window"}:
        storage.put_bytes(
            outbox_path(document["outbox_id"]), manifest_bytes(document), "application/json"
        )
    external = Mock(side_effect=AssertionError("provider I/O before binding validation"))
    monkeypatch.setattr(VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr("podcaster.video.distribution._get_youtube_access_token", external)
    try:
        outcome = _run_reviewed_worker(storage, document)
        if drift == "window":
            assert outcome.reason == "reviewed_video_dispatched"
            state = repository.read(document["outbox_id"])
            assert state["claim"] is None
            assert state["providers"]["youtube"]["result"] == "manual_handoff_required"
            assert state["providers"]["youtube"]["verification_attempt"] == 0
            assert state["providers"]["youtube"]["next_reconcile_at"] is None
        else:
            assert outcome.reason == "media_review_required"
    except (DistributionOutboxError, ReviewBindingError):
        if drift == "window":
            raise
        pass
    external.assert_not_called()


@pytest.mark.parametrize("drift", ["local", "stored"])
def test_real_dispatcher_rechecks_actual_mp4_at_adapter_boundary(
    prepared_exact_video, monkeypatch, drift
):
    from podcaster.distribution_outbox import DistributionOutboxError
    from podcaster.video import distribution

    storage, job_id, _repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    http = _ReviewedYouTubeTransport()
    monkeypatch.setattr(distribution.VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(distribution, "_DefaultTransport", lambda: http)
    monkeypatch.setattr(distribution, "_get_youtube_access_token", lambda *_: "offline")
    upload = distribution.upload_to_youtube

    def replace_before_adapter(path, *args, **kwargs):
        if drift == "local":
            path.write_bytes(b"X" * document["artifact"]["size_bytes"])
        else:
            storage.put_bytes(
                document["artifact"]["path"],
                b"X" * document["artifact"]["size_bytes"],
                "video/mp4",
            )
        return upload(path, *args, **kwargs)

    monkeypatch.setattr(distribution, "upload_to_youtube", replace_before_adapter)
    with pytest.raises(DistributionOutboxError):
        _run_reviewed_worker(storage, document)
    assert http.mutations == []
    assert http.bytes_uploaded is None


def test_real_dispatcher_ambiguous_upload_is_never_repeated(prepared_exact_video, monkeypatch):
    from podcaster.video.distribution import VideoDistributionConfig

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    http = _ReviewedYouTubeTransport()
    monkeypatch.setattr(VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda: http)
    monkeypatch.setattr(
        "podcaster.video.distribution._get_youtube_access_token", lambda *_: "offline"
    )
    upload = Mock(side_effect=RuntimeError("ambiguous create"))
    monkeypatch.setattr("podcaster.video.distribution.upload_to_youtube", upload)
    with pytest.raises(RuntimeError, match="ambiguous"):
        _run_reviewed_worker(storage, document)
    # Release the crashed claim as an expired worker would, without rearming.
    state = repository.read(document["outbox_id"])
    from podcaster.distribution_outbox import Claim

    live = state["claim"]
    repository.release(
        Claim(
            **{key: live[key] for key in Claim.__dataclass_fields__ if key != "outbox_id"},
            outbox_id=document["outbox_id"],
        )
    )
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert upload.call_count == 1
    assert (
        repository.read(document["outbox_id"])["providers"]["youtube"]["result"]
        == "publication_unknown"
    )


@pytest.mark.parametrize("prepared_exact_video", ["spotify", "spotify_live"], indirect=True)
def test_real_spotify_dispatcher_preserves_live_gate_and_exact_video_identity(
    prepared_exact_video, monkeypatch
):
    from podcaster import publish
    from podcaster.video.distribution import VideoDistributionConfig

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    marker = f"Publication {document['publication_digest']}; MP4 {document['artifact']['sha256']}"
    metadata = load_manifest(storage, job_id)["generation"]["video_runner"]["publication_metadata"]
    title = f"{metadata['title']} [{document['publication_digest']}]"
    description = f"{metadata['description']}\n\n{marker}"
    monkeypatch.setattr(VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(
        publish, "_get_credentials", lambda _: ("offline-show", "offline", "offline")
    )
    monkeypatch.setattr(publish, "_build_session", lambda *_: Mock())
    monkeypatch.setattr(
        publish, "_resolve_legacy_ids", lambda _session, _show: ("offline-station", "offline-user")
    )

    published = False

    def upload(path, **kwargs):
        kwargs["before_mutation"]()
        assert kwargs["require_reconciliation"] is True
        assert path.read_bytes() == storage.get_bytes(document["artifact"]["path"])
        assert kwargs["title"] == title and kwargs["description"] == description
        return publish.PublishResult(status="draft", anchor_episode_id=123456)

    uploader = Mock(side_effect=upload)

    def promote_video(_id, **kwargs):
        nonlocal published
        assert config.spotify_video_publish_mode == "live"
        assert publish._spotify_video_allow_live_publish()
        kwargs["before_mutation"]()
        published = True
        return publish.VideoPromoteResult(terminal_state="published", is_published=True)

    promote = Mock(side_effect=promote_video)
    monkeypatch.setattr(publish, "upload_video_to_episode", uploader)
    monkeypatch.setattr(publish, "promote_spotify_video_draft", promote)

    def readback(_session, anchor_id, user_id):
        assert anchor_id == 123456
        assert user_id == "offline-user"
        return published, {"id": anchor_id, "title": title, "description": description}

    monkeypatch.setattr(publish, "_read_episode_overview", readback)
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert uploader.call_count == 1
    state = repository.read(document["outbox_id"])
    if config.spotify_video_publish_mode == "live":
        promote.assert_called_once()
        assert state["aggregate"]["externally_verified_public"] is True
        assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
        assert uploader.call_count == 1
        promote.assert_called_once()
    else:
        promote.assert_not_called()
        assert state["providers"]["spotify"]["result"] == "manual_handoff_required"
        assert state["aggregate"]["externally_verified_public"] is False


@pytest.mark.parametrize("prepared_exact_video", ["both", "both_live"], indirect=True)
@pytest.mark.parametrize("terminal_exhaustion", [None, "budget", "horizon"])
def test_real_adapters_continue_pending_leg_without_replaying_finished_mutations(
    prepared_exact_video, monkeypatch, terminal_exhaustion
):
    from podcaster import publish
    from podcaster.video import distribution
    from tests.test_publish import _mock_graphql_listing_resp, _mock_json_resp

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    clock = FROZEN_NOW
    repository.now = lambda: clock
    monkeypatch.setattr(
        "podcaster.distribution_worker.DistributionOutboxRepository", lambda _: repository
    )
    http = _ReviewedYouTubeTransport(processing_status="processing")
    monkeypatch.setattr(distribution.VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(distribution, "_DefaultTransport", lambda: http)
    monkeypatch.setattr(distribution, "_get_youtube_access_token", lambda *_: "offline")
    monkeypatch.setattr(
        publish, "_get_credentials", lambda _: ("offline-show", "offline", "offline")
    )
    monkeypatch.setattr(publish.time, "sleep", lambda _: None)

    class SpotifyNetwork:
        def __init__(self):
            self.episode = {"id": 123456, "isPublished": False, "isDraft": True}
            self.creates = self.uploads = 0

        def request(self, method, url, **kwargs):
            if url == publish._SPOTIFY_CREATORS_GRAPHQL_URL:
                return _mock_graphql_listing_resp([])
            if url.endswith("/legacyIds"):
                payload = {"stationId": "99", "userId": "7"}
            elif url.endswith("/stations/99/episodes"):
                self.creates += 1
                payload = {"id": self.episode["id"]}
            elif url.endswith("/update"):
                self.episode.update(kwargs["json"])
                self.episode["isDraft"] = not self.episode["isPublished"]
                for source, target in (
                    ("episodeType", "podcastEpisodeType"),
                    ("seasonNumber", "podcastSeasonNumber"),
                    ("episodeNumber", "podcastEpisodeNumber"),
                ):
                    self.episode[target] = kwargs["json"].get(source)
                payload = {}
            elif url.endswith("/signedUrl"):
                payload = {
                    "uploadId": "offline-upload",
                    "signedUrlParts": [{"partNumber": 1, "url": "https://storage.example/part"}],
                }
            elif url == "https://storage.example/part":
                assert kwargs["data"] == storage.get_bytes(document["artifact"]["path"])
                self.uploads += 1
                payload = {}
            elif url.endswith("/process_upload"):
                payload = {}
            elif "/upload/media/" in url:
                payload = {"request": {"state": "processed"}}
            elif "/overview" in url:
                payload = self.episode.copy()
            else:
                raise AssertionError(f"unexpected offline Spotify request: {method} {url}")
            response = _mock_json_resp(payload)
            response.status_code = 200
            response.headers = {"ETag": "offline-part"}
            return response

    session = SpotifyNetwork()
    monkeypatch.setattr(publish, "_build_session", lambda *_: session)
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    state = repository.read(document["outbox_id"])
    assert state["providers"]["youtube"]["result"] == "pending_provider"
    assert state["attempts"][-1]["terminal_outcome"] is None
    assert session.creates == session.uploads == 1
    assert http.mutations == ["upload"]
    if terminal_exhaustion:

        def exhaust_terminal(document):
            leg = document["providers"]["spotify"]
            if terminal_exhaustion == "budget":
                leg["verification_budget"] = leg["verification_attempt"]
            else:
                leg["verification_horizon_at"] = clock.isoformat()

        repository._update(document["outbox_id"], exhaust_terminal)
    token = state["providers"]["youtube"]["active_schedule_token"]
    due = state["providers"]["youtube"]["next_reconcile_at"]

    # An early duplicate preserves the token and does not poll the pending leg.
    readbacks = len(http.readback_parts)
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert len(http.readback_parts) == readbacks
    assert (
        repository.read(document["outbox_id"])["providers"]["youtube"]["active_schedule_token"]
        == token
    )

    clock = datetime.fromisoformat(due.replace("Z", "+00:00"))
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    state = repository.read(document["outbox_id"])
    leg = state["providers"]["youtube"]
    assert leg["active_schedule_token"] != token
    assert datetime.fromisoformat(leg["next_reconcile_at"].replace("Z", "+00:00")) > clock
    assert state["attempts"][-1]["terminal_outcome"] is None
    assert session.creates == session.uploads == 1

    clock = datetime.fromisoformat(leg["next_reconcile_at"].replace("Z", "+00:00"))
    http.video["processingDetails"]["processingStatus"] = "succeeded"
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    state = repository.read(document["outbox_id"])
    assert state["providers"]["youtube"]["result"] == "externally_verified_public"
    assert state["aggregate"]["externally_verified_public"] is (
        config.spotify_video_publish_mode == "live"
    )
    assert state["attempts"][-1]["terminal_outcome"] == (
        "published_verified"
        if config.spotify_video_publish_mode == "live"
        else "manual_action_required"
    )
    assert http.mutations == ["upload", "promote"]
    assert session.creates == session.uploads == 1
    metadata = load_manifest(storage, job_id)["generation"]["video_runner"]["publication_metadata"]
    assert session.episode["title"].startswith(metadata["title"])
    assert session.episode["description"].startswith(metadata["description"] + "\n\n")


@pytest.mark.parametrize("prepared_exact_video", ["youtube_chunked"], indirect=True)
@pytest.mark.parametrize("drift", [None, "local", "stored", "audio", "claim"])
def test_real_chunked_adapter_has_constant_media_reads_and_fenced_local_copy(
    prepared_exact_video, monkeypatch, drift
):
    from functools import partial

    from podcaster.distribution_outbox import DistributionOutboxError, outbox_path
    from podcaster.video import distribution, youtube

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    audio = load_manifest(storage, job_id)["generation"]["synthesis_runner"]["audio"]

    class ChunkedNetwork(_ReviewedYouTubeTransport):
        def __init__(self):
            super().__init__()
            self.chunks = []

        def request_with_headers(self, url, **kwargs):
            if kwargs.get("method") != "PUT":
                return super().request_with_headers(url, **kwargs)
            self.chunks.append(kwargs["data"])
            if len(self.chunks) == 1:
                if drift == "local":
                    local_path.write_bytes(b"X" * document["artifact"]["size_bytes"])
                elif drift == "stored":
                    storage.put_bytes(
                        document["artifact"]["path"],
                        b"X" * document["artifact"]["size_bytes"],
                        "video/mp4",
                    )
                elif drift == "audio":
                    storage.put_bytes(audio["path"], b"changed-audio", "audio/mpeg")
                elif drift == "claim":
                    current = repository.read(document["outbox_id"])
                    current["fencing_token"] += 1
                    current["claim"]["fencing_token"] += 1
                    storage.put_bytes(
                        outbox_path(document["outbox_id"]),
                        json.dumps(current).encode(),
                        "application/json",
                    )
            size = sum(map(len, self.chunks))
            if size < document["artifact"]["size_bytes"]:
                return 308, {"range": f"bytes=0-{size - 1}"}, b""
            self.bytes_uploaded = b"".join(self.chunks)
            return 200, {}, b'{"id":"reviewed-video"}'

    http = ChunkedNetwork()
    monkeypatch.setattr(distribution.VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(distribution, "_DefaultTransport", lambda: http)
    monkeypatch.setattr(distribution, "_get_youtube_access_token", lambda *_: "offline")
    monkeypatch.setattr(distribution, "_MAX_SINGLE_UPLOAD_BYTES", 1)
    monkeypatch.setattr(
        distribution,
        "_load_chunked_uploader",
        lambda: partial(youtube.upload_chunked, chunk_size=256 * 1024),
    )
    monkeypatch.setattr(youtube.time, "sleep", lambda _: None)
    get_bytes = storage.get_bytes
    reads = []

    def tracked_read(path):
        reads.append(path)
        return get_bytes(path)

    monkeypatch.setattr(storage, "get_bytes", tracked_read)
    download = storage.download_file

    def tracked_download(path, destination):
        reads.append(path)
        return download(path, destination)

    monkeypatch.setattr(storage, "download_file", tracked_download)
    upload = distribution.upload_to_youtube
    local_path = None
    adapter_reads = []

    def tracked_upload(path, *args, **kwargs):
        nonlocal local_path
        local_path = path
        reads.clear()
        try:
            return upload(path, *args, **kwargs)
        finally:
            adapter_reads.extend(reads)

    monkeypatch.setattr(distribution, "upload_to_youtube", tracked_upload)
    if drift is not None:
        expected_error = (
            ReviewBindingError
            if drift == "audio"
            else (DistributionOutboxError, distribution.YouTubeDeliveryError)
        )
        with pytest.raises(expected_error):
            _run_reviewed_worker(storage, document)
        assert http.mutations == ["upload"]
        assert len(http.chunks) == (1 if drift in {"local", "claim"} else 8)
        assert (
            repository.read(document["outbox_id"])["aggregate"]["externally_verified_public"]
            is False
        )
    else:
        assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
        assert len(http.chunks) == 8
        assert http.bytes_uploaded == b"V" * document["artifact"]["size_bytes"]
        assert http.mutations == ["upload", "promote"]
        assert adapter_reads.count(document["artifact"]["path"]) == 1
        assert 1 <= adapter_reads.count(audio["path"]) <= 2


@pytest.mark.parametrize("endpoint", ["api", "monitoring"])
def test_actual_media_review_http_retry_retains_original_binding_and_window(
    prepared_exact_video, monkeypatch, endpoint
):
    from fastapi.testclient import TestClient

    from podcaster.monitoring import app
    from tests.test_api import make_handler

    storage, job_id, repository, document, *_ = prepared_exact_video
    monkeypatch.setenv("PODCASTER_API_KEY", "offline-review-key")
    monkeypatch.setattr("podcaster.api.create_storage_backend", lambda: storage)
    monkeypatch.setattr("podcaster.monitoring.get_storage", lambda: storage)
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime.fromisoformat(
        document["created_at"].replace("Z", "+00:00")
    ) + timedelta(seconds=2)
    monkeypatch.setattr(f"podcaster.{endpoint}.datetime", clock)
    payload = {
        "job_id": job_id,
        "reviewer": "actual-offline-media-reviewer",
        "decision": "approved",
        "review_checks_confirmed": True,
        "media_review": {
            "outbox_id": document["outbox_id"],
            "publication_digest": document["publication_digest"],
            "artifact_sha256": document["artifact"]["sha256"],
        },
    }

    def submit():
        body = json.dumps(payload).encode()
        headers = {"x-podcaster-api-key": "offline-review-key", "Content-Length": str(len(body))}
        if endpoint == "api":
            return make_handler("POST", "/api/review", body, headers).response_code
        with TestClient(app) as client:
            return client.post("/api/review", json=payload, headers=headers).status_code

    assert submit() == 200
    original = repository.read(document["outbox_id"])
    clock.now.return_value += timedelta(minutes=1)
    assert submit() == 200
    repeated = repository.read(document["outbox_id"])
    assert repeated["media_review"] == original["media_review"]
    assert repeated["providers"] == original["providers"]
    assert len(repeated["media_review"]["audit_trail"]) == 1
    payload["decision"] = "rejected"
    assert submit() == 409
    assert repository.read(document["outbox_id"])["media_review"] == original["media_review"]


@pytest.mark.parametrize("prepared_exact_video", ["spotify_override"], indirect=True)
@pytest.mark.parametrize("listing_failure", ["http", "malformed", "incomplete"])
def test_real_spotify_adapter_cannot_blind_create_on_reviewed_dispatch(
    prepared_exact_video, monkeypatch, listing_failure
):
    from podcaster import publish
    from podcaster.distribution_outbox import DistributionOutboxError
    from podcaster.video.distribution import VideoDistributionConfig
    from tests.test_publish import _mock_error_resp, _mock_graphql_listing_resp, _mock_json_resp

    storage, job_id, repository, document, config, _ = prepared_exact_video
    # The override was already set during preparation, so the reviewed runtime
    # matches. Exercise the real adapter, pagination/parser and retry boundary.
    _video_decision(storage, job_id, document)
    monkeypatch.setattr(VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(
        publish, "_get_credentials", lambda _: ("offline-show", "offline", "offline")
    )
    session = Mock()
    if listing_failure == "http":
        session.request.return_value = _mock_error_resp(503, "listing unavailable")
    elif listing_failure == "malformed":
        session.request.return_value = _mock_json_resp({"episodes": []})
    else:
        session.request.return_value = _mock_graphql_listing_resp([], totalItems=1, totalPages=2)
    monkeypatch.setattr(publish, "_build_session", lambda *_: session)
    monkeypatch.setattr(publish, "_resolve_legacy_ids", lambda *_: ("99", "7"))
    monkeypatch.setattr(publish.time, "sleep", lambda _: None)
    create = Mock(side_effect=AssertionError("blind create reached real provider boundary"))
    monkeypatch.setattr(publish, "_create_episode", create)
    with pytest.raises(DistributionOutboxError, match="confirmed identity"):
        _run_reviewed_worker(storage, document)
    assert session.request.call_count >= 1
    assert session.request.call_args.kwargs["json"]["operationName"] == "WebGetIndexedEpisodeList"
    create.assert_not_called()
    assert storage.get_bytes(f"publication-evidence/{job_id}.json") is None
    state = repository.read(document["outbox_id"])
    assert state["aggregate"]["externally_verified_public"] is False
    assert state["providers"]["spotify"]["result"] == "publication_unknown"
    # A failed/unknown outbox attempt remains reconciliation-only on redelivery.
    from podcaster.distribution_outbox import Claim

    live = state["claim"]
    repository.release(
        Claim(
            **{key: live[key] for key in Claim.__dataclass_fields__ if key != "outbox_id"},
            outbox_id=document["outbox_id"],
        )
    )
    calls = session.request.call_count
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert session.request.call_count == calls
    create.assert_not_called()


@pytest.mark.parametrize("prepared_exact_video", ["spotify"], indirect=True)
def test_real_dispatcher_rejects_spotify_override_changed_after_media_review(
    prepared_exact_video, monkeypatch
):
    from podcaster import publish
    from podcaster.distribution_outbox import DistributionOutboxError
    from podcaster.video.distribution import VideoDistributionConfig

    storage, job_id, _repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    monkeypatch.setenv("PODCASTER_SPOTIFY_ALLOW_UNRECONCILED_CREATE", "true")
    monkeypatch.setattr(VideoDistributionConfig, "from_env", lambda: config)
    session = Mock(side_effect=AssertionError("provider I/O before runtime validation"))
    monkeypatch.setattr(publish, "_build_session", session)
    with pytest.raises(DistributionOutboxError, match="binding changed"):
        _run_reviewed_worker(storage, document)
    session.assert_not_called()


@pytest.mark.parametrize("field", ["outbox_id", "publication_digest", "artifact_sha256"])
def test_wrong_media_binding_has_no_side_effects(prepared_exact_video, field):
    storage, job_id, _repository, document, *_ = prepared_exact_video
    before = _private_storage_snapshot(storage)
    with pytest.raises(ReviewBindingError):
        _video_decision(storage, job_id, document, **{field: "c" * 64})
    assert _private_storage_snapshot(storage) == before


def test_media_review_and_video_preparation_are_idempotent(prepared_exact_video):
    from podcaster.video.job_runner import run_video_generation

    storage, job_id, repository, document, config, composer = prepared_exact_video
    assert run_video_generation(job_id, storage, config=config, fanout=False).status == "skipped"
    assert composer.call_count == 1
    _video_decision(storage, job_id, document)
    _video_decision(storage, job_id, document)
    assert len(repository.read(document["outbox_id"])["media_review"]["audit_trail"]) == 1
    with pytest.raises(ReviewBindingError):
        process_review_decision(
            job_id,
            storage=storage,
            reviewer="different-reviewer",
            reviewed_at="2026-10-29T11:16:00Z",
            decision="approved",
            review_checks_confirmed=True,
            media_review={
                "outbox_id": document["outbox_id"],
                "publication_digest": document["publication_digest"],
                "artifact_sha256": document["artifact"]["sha256"],
            },
        )


@pytest.mark.parametrize("decision", ["rejected", "changes_requested"])
def test_negative_media_decision_keeps_existing_outbox_closed(prepared_exact_video, decision):
    from podcaster.distribution_outbox import MediaReviewRequiredError

    _storage, job_id, repository, document, *_ = prepared_exact_video
    reviewed_at = FROZEN_NOW.isoformat()
    repository.record_media_review(
        document["outbox_id"],
        accepted_job_id=job_id,
        publication_digest=document["publication_digest"],
        artifact_sha256=document["artifact"]["sha256"],
        reviewer="actual-offline-media-reviewer",
        reviewed_at=reviewed_at,
        decision=decision,
        checks_confirmed=True,
    )
    with pytest.raises(MediaReviewRequiredError):
        repository.claim(
            document["outbox_id"], owner="worker", execution_id="one", lease_seconds=600
        )
    assert repository.repair_notifications_page(Mock())[0] == 0
    assert not provider_approval_is_valid(repository.read(document["outbox_id"]), "youtube")


def test_media_review_starts_verification_budget_once_after_private_wait(prepared_exact_video):
    from datetime import timedelta

    _storage, job_id, repository, document, *_ = prepared_exact_video
    admission = FROZEN_NOW + timedelta(days=2)
    repository.now = lambda: admission
    arguments = {
        "accepted_job_id": job_id,
        "publication_digest": document["publication_digest"],
        "artifact_sha256": document["artifact"]["sha256"],
        "reviewer": "actual-offline-media-reviewer",
        "reviewed_at": admission.isoformat(),
        "decision": "approved",
        "checks_confirmed": True,
    }
    approved = repository.record_media_review(document["outbox_id"], **arguments)
    horizon = approved["providers"]["youtube"]["verification_horizon_at"]
    assert datetime.fromisoformat(horizon.replace("Z", "+00:00")) == admission + timedelta(hours=24)
    repository.now = lambda: admission + timedelta(hours=1)
    repeated = repository.record_media_review(document["outbox_id"], **arguments)
    assert repeated["providers"]["youtube"]["verification_horizon_at"] == horizon
    assert len(repeated["media_review"]["audit_trail"]) == 1


@pytest.mark.parametrize("invalid", ["missing_checks", "old_time", "naive_time"])
def test_media_decision_requires_actual_fresh_explicit_review(prepared_exact_video, invalid):
    from podcaster.distribution_outbox import OutboxConflictError

    storage, job_id, repository, document, *_ = prepared_exact_video
    before = _private_storage_snapshot(storage)
    with pytest.raises(OutboxConflictError):
        repository.record_media_review(
            document["outbox_id"],
            accepted_job_id=job_id,
            publication_digest=document["publication_digest"],
            artifact_sha256=document["artifact"]["sha256"],
            reviewer="actual-offline-media-reviewer",
            reviewed_at=(
                "2026-01-01T00:00:00Z"
                if invalid == "old_time"
                else FROZEN_NOW.replace(tzinfo=None).isoformat()
                if invalid == "naive_time"
                else FROZEN_NOW.isoformat()
            ),
            decision="approved",
            checks_confirmed=invalid != "missing_checks",
        )
    assert _private_storage_snapshot(storage) == before


@pytest.mark.parametrize(
    "drift",
    [
        "script",
        "audio",
        "request",
        "claim",
        "missing_proof",
        "prepended_mp3",
        "empty_proof_missing_mp3",
        "missing_mp3_proof",
        "selected_path",
        "manifest_audio_digest",
    ],
)
def test_private_video_rejects_stale_synthesis_without_side_effects(
    prepared_exact_video, monkeypatch, drift
):
    from podcaster.generation import manifest_bytes
    from podcaster.video.job_runner import run_video_generation

    storage, job_id, _repository, document, config, composer = prepared_exact_video
    manifest = load_manifest(storage, job_id)
    if drift == "script":
        storage.put_bytes(f"jobs/{job_id}/script.txt", b"unreviewed", "text/plain")
    elif drift == "audio":
        path = manifest["generation"]["synthesis_runner"]["audio"]["path"]
        storage.put_bytes(path, b"wrong-audio", "audio/mpeg")
    elif drift == "request":
        manifest["request"]["language"] = "es"
    elif drift == "claim":
        manifest["generation"]["synthesis_claim"]["dispatch_id"] = "different-dispatch"
    elif drift == "prepended_mp3":
        path = f"jobs/{job_id}/unrelated.mp3"
        storage.put_bytes(path, b"unreviewed-audio", "audio/mpeg")
        manifest["artifacts"] = {
            path: {"sha256": sha256(b"unreviewed-audio").hexdigest(), "size_bytes": 16},
            **manifest["artifacts"],
        }
    elif drift == "empty_proof_missing_mp3":
        audio = manifest["generation"]["synthesis_runner"]["audio"]
        storage.delete_blob(audio["path"])
        audio["artifacts"] = {}
    elif drift == "missing_mp3_proof":
        manifest["generation"]["synthesis_runner"]["audio"]["artifacts"].pop("mp3")
    elif drift == "selected_path":
        manifest["generation"]["synthesis_runner"]["audio"]["path"] = f"jobs/{job_id}/other.mp3"
    elif drift == "manifest_audio_digest":
        path = manifest["generation"]["synthesis_runner"]["audio"]["path"]
        manifest["artifacts"][path]["sha256"] = "c" * 64
    else:
        manifest["generation"]["synthesis_runner"].pop("review_input_sha256")
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json")
    before = _private_storage_snapshot(storage)
    from podcaster.review import validate_private_media_inputs
    from podcaster.video.job_runner import _resolve_audio_path

    with pytest.raises(ReviewBindingError):
        validate_private_media_inputs(manifest, storage, job_id)
    with pytest.raises(ReviewBindingError):
        _resolve_audio_path(manifest, job_id, storage, storage.root)
    assert run_video_generation(job_id, storage, config=config).status == "skipped"
    assert composer.call_count == 1
    with pytest.raises(ReviewBindingError):
        _video_decision(storage, job_id, document)
    assert _private_storage_snapshot(storage) == before


def test_noncanonical_post_audio_review_keeps_legacy_approval_contract(tmp_path):
    storage = LocalStorageBackend(tmp_path / "legacy", "https://example.invalid/artifacts")
    manifest = _synthesized_manifest()
    _stage(storage, manifest)
    reviewed = process_review_decision(
        manifest["job_id"],
        storage=storage,
        reviewer="test-legacy-reviewer",
        reviewed_at="2026-10-29T11:10:00Z",
        decision="approved",
        publish_on_approval=False,
        publish_video_on_approval=False,
    ).manifest
    assert reviewed["review"]["status"] == "approved"
    assert reviewed["publishing"]["eligible"] is True
    assert _distribution_human_approval(reviewed) == {
        "approved": True,
        "approved_by": "test-legacy-reviewer",
        "approved_at": "2026-10-29T11:10:00Z",
        "source": "manifest_human_review",
    }


@pytest.mark.parametrize("snapshot_state", ["missing", "null", "string", "list"])
@pytest.mark.parametrize("canonical_marker", ["synthesis_approval", "provider_settings_sha256"])
def test_malformed_post_audio_snapshot_never_grants_publication_approval(
    tmp_path, monkeypatch, snapshot_state, canonical_marker
):
    storage = LocalStorageBackend(tmp_path / "canonical", "https://example.invalid/artifacts")
    manifest = _synthesized_manifest()
    if snapshot_state != "missing":
        manifest["review"]["snapshot"] = {
            "null": None,
            "string": "corrupt",
            "list": [],
        }[snapshot_state]
    if canonical_marker == "synthesis_approval":
        manifest["review"]["synthesis_approval"] = {"status": "approved"}
    else:
        manifest["generation"]["provider_settings_sha256"] = "a" * 64
    _stage(storage, manifest)
    publish = Mock(side_effect=AssertionError("unexpected provider publication"))
    monkeypatch.setattr("podcaster.orchestration.publish_episode", publish)

    outcome = process_review_decision(
        manifest["job_id"],
        storage=storage,
        reviewer="test-audio-reviewer",
        reviewed_at="2026-10-29T11:10:00Z",
        decision="approved",
        publish_on_approval=True,
        publish_video_on_approval=True,
    )

    persisted = load_manifest(storage, manifest["job_id"])
    assert persisted == outcome.manifest
    assert persisted["review"]["status"] == "pending"
    assert persisted["review_status"] == "pending"
    assert persisted["review"]["approved_by"] is None
    assert persisted["review"]["approved_at"] is None
    assert persisted["review"]["gate"]["status"] == "blocked"
    assert persisted["review"]["audio_approval"]["status"] == "approved"
    assert persisted["publishing"]["eligible"] is False
    assert "human_review" in persisted["publishing"]["blocked_by"]
    assert persisted["publishing"]["readiness_checks"]["editorial_review_complete"] is False
    assert _distribution_human_approval(persisted) == {"approved": False}
    assert outcome.publish_result is None
    publish.assert_not_called()


@pytest.mark.parametrize("snapshot_state", ["missing", "null", "string", "list"])
def test_malformed_pre_tts_snapshot_rejects_approval_atomically(
    reviewed_stage, monkeypatch, snapshot_state
):
    storage, result, generator, producer, _script = reviewed_stage
    manifest = json.loads(json.dumps(result.manifest))
    if snapshot_state == "missing":
        manifest["review"].pop("snapshot")
    else:
        manifest["review"]["snapshot"] = {
            "null": None,
            "string": "corrupt",
            "list": [],
        }[snapshot_state]
    path = f"jobs/{result.response['job_id']}/manifest.json"
    before = json.dumps(manifest).encode()
    storage.put_bytes(path, before, "application/json")
    publish = Mock(side_effect=AssertionError("unexpected provider publication"))
    monkeypatch.setattr("podcaster.orchestration.publish_episode", publish)

    with pytest.raises(ReviewBindingError):
        _approve(storage, result)

    assert storage.get_bytes(path) == before
    assert producer.sent == []
    publish.assert_not_called()
    generator.assert_called_once()


def test_snapshot_does_not_replace_provider_privacy_or_rai_signoff(reviewed_stage):
    storage, result, generator, producer, _script = reviewed_stage
    assert result.manifest["review"]["snapshot"]
    required = {
        "provider_not_selected",
        "provider_privacy_review_required",
        "rai_security_signoff_required",
    }
    assert required <= set(result.manifest["generation"]["tts_synthesis"]["blocked_by"])
    reviewed = apply_review_decision(
        result.manifest,
        reviewer="test-reviewer",
        reviewed_at="2026-10-29T11:05:00Z",
        decision="approved",
        review_checks_confirmed=True,
    )
    assert reviewed["review"]["synthesis_approval"]["status"] == "approved"
    assert reviewed["generation"]["tts_synthesis"]["allowed"] is False
    assert required <= set(reviewed["generation"]["tts_synthesis"]["blocked_by"])
    before = storage.get_bytes(f"jobs/{result.response['job_id']}/manifest.json")
    with pytest.raises(ReviewBindingError):
        _approve(storage, result, provider_authorization=None)
    assert storage.get_bytes(f"jobs/{result.response['job_id']}/manifest.json") == before
    assert producer.sent == []
    generator.assert_called_once()


@pytest.mark.parametrize(
    "invalid",
    [
        "selected_provider",
        "fallback_provider",
        "provider_selection_reference",
        "provider_privacy_review_reference",
        "rai_security_signoff_reference",
        "disclosure_reference",
        "region",
        "provider_settings_sha256",
        "wrong_config_binding",
        "wrong_selected_provider",
        "blank_privacy_reference",
        "non_string_rai_reference",
        "extra_unrecorded_field",
        "checks_missing",
        "checks_string_true",
    ],
)
def test_approval_without_complete_config_bound_checks_is_atomic_and_never_queued(
    reviewed_stage, invalid
):
    storage, result, generator, producer, _script = reviewed_stage
    evidence = approval_evidence_for(result.manifest)
    authorization = evidence["provider_authorization"]
    if invalid == "wrong_config_binding":
        authorization["provider_settings_sha256"] = "0" * 64
    elif invalid == "wrong_selected_provider":
        authorization["selected_provider"] = "unreviewed-provider"
    elif invalid == "blank_privacy_reference":
        authorization["provider_privacy_review_reference"] = " "
    elif invalid == "non_string_rai_reference":
        authorization["rai_security_signoff_reference"] = True
    elif invalid == "extra_unrecorded_field":
        authorization["implicit_approval"] = True
    elif invalid == "checks_missing":
        evidence["review_checks_confirmed"] = False
    elif invalid == "checks_string_true":
        evidence["review_checks_confirmed"] = "true"
    else:
        authorization.pop(invalid)
    path = f"jobs/{result.response['job_id']}/manifest.json"
    before = storage.get_bytes(path)
    with pytest.raises(ReviewBindingError):
        _approve(storage, result, **evidence)
    assert storage.get_bytes(path) == before
    assert producer.sent == []
    generator.assert_called_once()


def test_explicit_config_bound_checks_queue_exact_script_but_not_publication(
    reviewed_stage, tmp_path, monkeypatch
):
    storage, result, generator, producer, script = reviewed_stage
    publish = Mock(side_effect=AssertionError("script review attempted publication"))
    video = Mock(side_effect=AssertionError("script review queued publishing-capable video"))
    monkeypatch.setattr("podcaster.orchestration.publish_staged_job", publish)
    monkeypatch.setattr("podcaster.job_runner._enqueue_video", video)
    monkeypatch.setenv("VIDEO_GENERATION_ENABLED", "true")
    reviewed = _approve(storage, result)
    approval = reviewed["review"]["synthesis_approval"]
    authorization = approval["provider_authorization"]
    assert authorization == approval_evidence_for(result.manifest)["provider_authorization"]
    assert approval["binding"]["provider_authorization"] == authorization
    assert approval["binding"]["review_checks_confirmed"] is True
    assert approval["binding"] in approval["audit_trail"]
    assert reviewed["generation"]["tts_synthesis"]["allowed"] is True
    assert len(producer.sent) == 1
    assert storage.get_bytes(f"jobs/{result.response['job_id']}/script.txt") == script
    assert approval["binding"]["script_sha256"] == sha256(script).hexdigest()
    assert reviewed["review"]["status"] == "pending"
    assert not reviewed["review"].get("approved_by")
    assert not reviewed["review"].get("approved_at")
    assert reviewed.get("review_status") != "approved"
    assert reviewed["publishing"]["eligible"] is False
    assert "human_review" in reviewed["publishing"]["blocked_by"]
    assert _distribution_human_approval(reviewed) == {"approved": False}

    media = tmp_path / "offline-video.mp4"
    media.write_bytes(b"offline-test-video")
    artifact = commit_immutable_artifact(
        storage, media, media_kind="video", content_type="video/mp4", suffix=".mp4"
    )
    identity = PublicationIdentity(
        accepted_job_id=result.response["job_id"],
        week="2026-W44",
        publish_run_id="test-pre-tts",
        article_sha256="a" * 64,
        manifest_sha256=approval["binding"]["manifest_sha256"],
    )
    document, _created = DistributionOutboxRepository(
        storage, now=lambda: datetime(2026, 10, 29, 11, 5, tzinfo=timezone.utc)
    ).enqueue(
        identity,
        artifact,
        provider_objectives={"youtube": "public", "spotify": "public"},
        enqueue_source="test",
        enqueue_version="v1",
        provider_approvals={
            provider: _distribution_human_approval(reviewed) for provider in ("youtube", "spotify")
        },
    )
    for provider in ("youtube", "spotify"):
        assert provider_approval_is_valid(document, provider) is False
        assert document["providers"][provider]["approval"]["approved"] is False
    assert load_manifest(storage, result.response["job_id"])["review"]["status"] == "pending"
    generator.assert_called_once()
    publish.assert_not_called()
    video.assert_not_called()
