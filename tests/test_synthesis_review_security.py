from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from unittest.mock import Mock

import pytest
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


@pytest.fixture
def prepared_exact_video(reviewed_stage, monkeypatch, tmp_path):
    """Run real approval/synthesis/preparation against private local contracts."""
    from podcaster.generation import manifest_bytes
    from podcaster.review import content_snapshot_sha256
    from podcaster.video.distribution import VideoDistributionConfig
    from podcaster.video.job_runner import run_video_generation

    storage, result, *_ = reviewed_stage
    job_id = result.response["job_id"]
    manifest = result.manifest
    manifest["request"].update(
        publication_identity_mode="canonical",
        publish_run_id="40269058395",
        article_sha256="a" * 64,
        manifest_sha256="b" * 64,
    )
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
        output_path.write_bytes(b"V" * 2048)
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
    config = VideoDistributionConfig(youtube_enabled=True, dry_run=False)
    outcome = run_video_generation(job_id, storage, config=config, fanout=False)
    assert outcome.status == STATUS_COMPLETED
    assert outcome.distribution.public_delivery_status == "awaiting_review"
    repository = DistributionOutboxRepository(storage)
    document = repository.read(outcome.distribution.outbox_id)
    assert document["media_review"]["status"] == "pending"
    assert not provider_approval_is_valid(document, "youtube")
    provider.assert_not_called()
    notification.assert_not_called()
    assert storage.get_bytes(document["artifact"]["path"]) == b"V" * 2048
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
    reviewed_at = datetime.now(timezone.utc).isoformat()
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
    admission = datetime.now(timezone.utc) + timedelta(days=2)
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
                else datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                if invalid == "naive_time"
                else datetime.now(timezone.utc).isoformat()
            ),
            decision="approved",
            checks_confirmed=invalid != "missing_checks",
        )
    assert _private_storage_snapshot(storage) == before


@pytest.mark.parametrize("drift", ["script", "audio", "request", "claim", "missing_proof"])
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
    else:
        manifest["generation"]["synthesis_runner"].pop("review_input_sha256")
    storage.put_bytes(f"jobs/{job_id}/manifest.json", manifest_bytes(manifest), "application/json")
    before = _private_storage_snapshot(storage)
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
