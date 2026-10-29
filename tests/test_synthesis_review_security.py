from __future__ import annotations

from datetime import datetime, timezone
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
    video.assert_not_called()

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
        video.assert_not_called()
    else:
        assert _distribution_human_approval(persisted) == {"approved": False}


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
