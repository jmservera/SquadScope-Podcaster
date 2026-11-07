from __future__ import annotations

import json
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from podcaster.auth import create_token
from podcaster.monitoring import app
from podcaster.video.owner_release import (
    W45_MP4_SHA256,
    owner_release_active,
    owner_release_dispatch,
    owner_release_evidence,
    owner_release_record_is_valid,
)

W44_JOB = "podcast-2026-W44-00bc06dfb6e2"
W45_JOB = "podcast-2026-W45-9798e93edc06"

IDENTITIES = {
    W44_JOB: {
        "accepted_job_id": W44_JOB,
        "week": "2026-W44",
        "publish_run_id": "40269058395",
        "article_sha256": "2a34575cd7be0d0a6ec36b4a808a1909ae7f793d00a8511da464ec063cf2bfa0",
        "manifest_sha256": "5c1fc333deb75ea2bfff990c1222f97a12acb2c3234e19df26717a27e8ff6f56",
    },
    W45_JOB: {
        "accepted_job_id": W45_JOB,
        "week": "2026-W45",
        "publish_run_id": "41395629030",
        "article_sha256": "fc4023679d8b2ba90481f783ebb9c57e61e059db3790c9c9d3c4a996b0242e34",
        "manifest_sha256": "a64c9c664a510b94207a566c92c3cc0465af241a5304109b845d1317284f5d3b",
    },
}


def _evidence_inputs(identity: dict[str, str], digest: str, size: int = 1024):
    selected = {
        "job_id": identity["accepted_job_id"],
        "identity": identity,
        "attempt_id": "selectedattempt",
        "epoch_id": None,
        "proof_sha256": "d" * 64,
        "mp4_sha256": digest,
        "size_bytes": size,
    }
    artifact = {"media_kind": "video", "sha256": digest, "size_bytes": size}
    canonical = {
        "artifact_id": "canonical-artifact",
        "selection_version": 1,
        "selected": True,
        "sha256": digest,
    }
    return selected, artifact, canonical


def _release_document(identity: dict[str, str], digest: str, size: int = 1024):
    selected, artifact, canonical = _evidence_inputs(identity, digest, size)
    metadata_digest = "e" * 64
    evidence = owner_release_evidence(
        identity,
        selected,
        artifact,
        canonical,
        publication_metadata_sha256=metadata_digest,
    )
    return selected, {
        "publication_identity": identity,
        "artifact": artifact,
        "canonical_artifact": canonical,
        "providers": {"youtube": {"context": {"publication_metadata_sha256": metadata_digest}}},
        "media_review": {"status": "pending", "audit_trail": []},
        "owner_release_authorization": evidence,
    }


def test_w44_authorization_binds_to_dynamic_selected_media_digest():
    digests = []
    for media in (b"synthetic W44 media one" * 50, b"synthetic W44 media two" * 50):
        digest = sha256(media).hexdigest()
        selected, artifact, canonical = _evidence_inputs(IDENTITIES[W44_JOB], digest, len(media))

        evidence = owner_release_evidence(
            IDENTITIES[W44_JOB],
            selected,
            artifact,
            canonical,
        )

        assert evidence is not None
        assert evidence["selected_artifact"]["mp4_sha256"] == digest
        assert evidence["canonical_artifact"]["sha256"] == digest
        digests.append(digest)
    assert digests[0] != digests[1]


def test_w45_authorization_requires_the_exact_selected_mp4_digest():
    identity = IDENTITIES[W45_JOB]
    selected, artifact, canonical = _evidence_inputs(identity, W45_MP4_SHA256)

    evidence = owner_release_evidence(identity, selected, artifact, canonical)

    assert evidence is not None
    assert evidence["selected_artifact"]["mp4_sha256"] == (
        "bb3eefa8a09b2a0d5175dcc658f7e6e5085982e374c8e1ff4f348678d00a67ec"
    )
    wrong_digest = "0" * 64 if W45_MP4_SHA256 != "0" * 64 else "1" * 64
    selected, artifact, canonical = _evidence_inputs(identity, wrong_digest)
    assert owner_release_evidence(identity, selected, artifact, canonical) is None


@pytest.mark.parametrize(
    ("field", "near_miss"),
    [
        ("accepted_job_id", "podcast-2026-W45-9798e93edc06-near-miss"),
        ("week", "2026-W43"),
        ("publish_run_id", "0"),
        ("article_sha256", "0" * 64),
        ("manifest_sha256", "1" * 64),
    ],
)
def test_owner_release_rejects_near_miss_publication_identity(field, near_miss):
    identity = dict(IDENTITIES[W44_JOB])
    identity[field] = near_miss
    selected, artifact, canonical = _evidence_inputs(identity, "a" * 64)

    assert owner_release_evidence(identity, selected, artifact, canonical) is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda selected, artifact, canonical: artifact.update(sha256="b" * 64),
        lambda selected, artifact, canonical: canonical.update(sha256="b" * 64),
        lambda selected, artifact, canonical: canonical.update(selected=False),
        lambda selected, artifact, canonical: selected.update(mp4_sha256="b" * 64),
    ],
)
def test_owner_release_requires_one_selected_artifact_digest(mutation):
    identity = IDENTITIES[W44_JOB]
    selected, artifact, canonical = _evidence_inputs(identity, "a" * 64)
    mutation(selected, artifact, canonical)

    assert owner_release_evidence(identity, selected, artifact, canonical) is None


def test_persisted_owner_release_record_is_bound_to_identity_and_artifact():
    identity = IDENTITIES[W45_JOB]
    selected, artifact, canonical = _evidence_inputs(identity, W45_MP4_SHA256)
    evidence = owner_release_evidence(identity, selected, artifact, canonical)
    document = {
        "publication_identity": identity,
        "artifact": artifact,
        "canonical_artifact": canonical,
        "owner_release_authorization": evidence,
    }

    assert owner_release_record_is_valid(document)
    assert "approved" not in evidence
    assert "approved_by" not in evidence
    assert "media_review" not in evidence

    document["owner_release_authorization"] = {**evidence, "owner": "not-the-owner"}
    assert not owner_release_record_is_valid(document)


def test_unrelated_job_cannot_receive_owner_release_exception():
    identity = {
        "accepted_job_id": "podcast-2026-W46-unrelated",
        "week": "2026-W46",
        "publish_run_id": "41395629031",
        "article_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
    }
    selected, artifact, canonical = _evidence_inputs(identity, "c" * 64)

    assert owner_release_evidence(identity, selected, artifact, canonical) is None


@pytest.mark.parametrize("job_id", ["../outside", "podcast-2026-W46-unrelated"])
def test_owner_release_rejects_unscoped_job_before_storage_access(job_id):
    from podcaster.orchestration import prepare_owner_authorized_video_release
    from podcaster.review import ReviewBindingError
    from podcaster.video.owner_release import OWNER_RELEASE_OWNER

    with pytest.raises(ReviewBindingError):
        prepare_owner_authorized_video_release(
            job_id,
            owner=OWNER_RELEASE_OWNER,
            storage=object(),
        )


@pytest.mark.parametrize(
    ("job_id", "digest"),
    [
        (W44_JOB, "a" * 64),
        (W45_JOB, W45_MP4_SHA256),
    ],
)
def test_exact_w44_and_w45_are_admitted_at_owner_release_boundary(job_id, digest, monkeypatch):
    from podcaster.distribution_outbox import owner_release_allows_distribution
    from podcaster.video import recovery

    identity = IDENTITIES[job_id]
    selected, document = _release_document(identity, digest)
    monkeypatch.setattr(recovery, "read_selected", lambda _storage, _job_id: selected)

    assert owner_release_allows_distribution(document, object())


def test_w44_runtime_release_rejects_a_changed_selected_manifest(monkeypatch):
    from podcaster.distribution_outbox import owner_release_allows_distribution
    from podcaster.video import recovery

    selected, document = _release_document(IDENTITIES[W44_JOB], "a" * 64)
    monkeypatch.setattr(recovery, "read_selected", lambda _storage, _job_id: selected)
    storage = object()

    assert owner_release_allows_distribution(document, storage)

    changed_selection = {**selected, "attempt_id": "different-selection"}
    monkeypatch.setattr(recovery, "read_selected", lambda _storage, _job_id: changed_selection)
    assert not owner_release_allows_distribution(document, storage)


@pytest.mark.parametrize(
    ("field", "near_miss"),
    [
        ("accepted_job_id", "podcast-2026-W45-9798e93edc06-near-miss"),
        ("week", "2026-W43"),
        ("publish_run_id", "0"),
        ("article_sha256", "0" * 64),
        ("manifest_sha256", "1" * 64),
    ],
)
def test_owner_release_boundary_rejects_near_miss_identity(field, near_miss, monkeypatch):
    from podcaster.distribution_outbox import owner_release_allows_distribution
    from podcaster.video import recovery

    identity = dict(IDENTITIES[W45_JOB])
    selected, document = _release_document(identity, W45_MP4_SHA256)
    identity[field] = near_miss
    document["publication_identity"] = identity
    monkeypatch.setattr(recovery, "read_selected", lambda _storage, _job_id: selected)

    assert not owner_release_allows_distribution(document, object())


@pytest.mark.parametrize("target", ["artifact", "selected"])
def test_owner_release_boundary_rejects_altered_media_digest(target, monkeypatch):
    from podcaster.distribution_outbox import owner_release_allows_distribution
    from podcaster.video import recovery

    identity = IDENTITIES[W45_JOB]
    selected, document = _release_document(identity, W45_MP4_SHA256)
    if target == "artifact":
        document["artifact"] = {**document["artifact"], "sha256": "0" * 64}
        monkeypatch.setattr(recovery, "read_selected", lambda _storage, _job_id: selected)
    else:
        altered = {**selected, "mp4_sha256": "0" * 64}
        monkeypatch.setattr(recovery, "read_selected", lambda _storage, _job_id: altered)

    assert not owner_release_allows_distribution(document, object())


def test_owner_release_keeps_unrelated_job_defaults_and_global_spotify_setting():
    from podcaster.distribution_outbox import media_review_allows_distribution
    from podcaster.video.distribution import VideoDistributionConfig

    unrelated = {"publication_identity": {"accepted_job_id": "podcast-2026-W46-other"}}
    assert media_review_allows_distribution(unrelated)
    assert VideoDistributionConfig(spotify_upload_enabled=True).spotify_upload_enabled


def test_owner_release_dispatch_binds_adapter_admission_to_selected_file(tmp_path, monkeypatch):
    from podcaster.video import recovery
    from podcaster.video.distribution import VideoDistributionConfig, upload_to_youtube
    from podcaster.video.recovery import RecoveryDenied

    identity = IDENTITIES[W44_JOB]
    selected_bytes = b"exact selected W44 bytes" * 100
    selected_digest = sha256(selected_bytes).hexdigest()
    selected, document = _release_document(identity, selected_digest, len(selected_bytes))
    selected_path = tmp_path / "selected.mp4"
    other_path = tmp_path / "other.mp4"
    selected_path.write_bytes(selected_bytes)
    other_path.write_bytes(b"other media" * 100)
    monkeypatch.setattr(recovery, "read_selected", lambda _storage, _job_id: selected)
    config = VideoDistributionConfig(youtube_enabled=True)

    with owner_release_dispatch(document, object(), selected_path):
        assert owner_release_active(W44_JOB, selected_path)
        assert not owner_release_active(W44_JOB, other_path)
        selected_path.write_bytes(b"changed media" * 100)
        assert not owner_release_active(W44_JOB, selected_path)
        with pytest.raises(RecoveryDenied):
            upload_to_youtube(
                selected_path,
                "title",
                "description",
                config,
                job_id=W44_JOB,
            )

    assert not owner_release_active(W44_JOB, selected_path)


def test_owner_release_api_requires_standard_auth_and_designated_owner_jwt(monkeypatch):
    secret = "test-owner-release-secret"
    monkeypatch.setenv("UI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("UI_AUTH_PASSWORD", "test-password")
    monkeypatch.setenv("UI_AUTH_SECRET", secret)
    monkeypatch.setenv("PODCASTER_API_KEY", "machine-api-key")

    queued = []

    def prepare(job_id, *, owner, storage):
        queued.append((job_id, owner, storage))
        return {"outbox_id": "local-outbox", "status": "queued_or_repairable"}

    storage = object()
    monkeypatch.setattr("podcaster.monitoring.prepare_owner_authorized_video_release", prepare)
    monkeypatch.setattr("podcaster.monitoring.get_storage", lambda: storage)

    with TestClient(app) as client:
        path = "/api/video/owner-release"
        payload = {
            "job_id": W44_JOB,
            "owner": "attacker",
            "article_sha256": "0" * 64,
            "review_checks_confirmed": True,
        }
        assert client.post(path, json=payload).status_code == 401
        assert (
            client.post(
                path,
                json=payload,
                headers={"X-Podcaster-Api-Key": "machine-api-key"},
            ).status_code
            == 403
        )
        non_owner = client.post(
            path,
            json=payload,
            headers={"Authorization": f"Bearer {create_token('admin', secret)}"},
        )
        assert non_owner.status_code == 403
        owner = client.post(
            path,
            json=payload,
            headers={"Authorization": f"Bearer {create_token('jmservera', secret)}"},
        )

    assert owner.status_code == 202
    assert owner.json() == {"outbox_id": "local-outbox", "status": "queued_or_repairable"}
    assert queued == [(W44_JOB, "jmservera", storage)]


def test_legacy_api_owner_release_requires_api_auth_and_owner_jwt(monkeypatch):
    from podcaster.api import GenerateHandler, create_token
    from tests.test_api import FakeHandler, make_handler

    secret = "test-owner-release-secret"
    monkeypatch.setenv("UI_AUTH_USERNAME", "admin")
    monkeypatch.setenv("UI_AUTH_PASSWORD", "test-password")
    monkeypatch.setenv("UI_AUTH_SECRET", secret)
    monkeypatch.setenv("PODCASTER_API_KEY", "machine-api-key")
    monkeypatch.setattr(
        FakeHandler,
        "_is_designated_owner_jwt",
        staticmethod(GenerateHandler._is_designated_owner_jwt),
        raising=False,
    )
    monkeypatch.setattr(
        FakeHandler,
        "_handle_owner_authorized_video_release",
        GenerateHandler._handle_owner_authorized_video_release,
        raising=False,
    )

    queued = []

    def prepare(job_id, *, owner, storage):
        queued.append((job_id, owner, storage))
        return {"outbox_id": "local-outbox", "status": "queued_or_repairable"}

    storage = object()
    monkeypatch.setattr("podcaster.api.prepare_owner_authorized_video_release", prepare)
    monkeypatch.setattr("podcaster.api.create_storage_backend", lambda: storage)

    def post(headers):
        body = json.dumps(
            {
                "job_id": W44_JOB,
                "owner": "attacker",
                "article_sha256": "0" * 64,
                "review_checks_confirmed": True,
            }
        ).encode()
        return make_handler(
            "POST",
            "/api/video/owner-release",
            body,
            {"Content-Length": str(len(body)), **headers},
        )

    assert post({}).response_code == 401
    assert post({"X-Podcaster-Api-Key": "machine-api-key"}).response_code == 403
    non_owner = post(
        {
            "Authorization": f"Bearer {create_token('admin', secret)}",
            "X-Podcaster-Api-Key": "machine-api-key",
        }
    )
    assert non_owner.response_code == 403
    owner = post(
        {
            "Authorization": f"Bearer {create_token('jmservera', secret)}",
            "X-Podcaster-Api-Key": "machine-api-key",
        }
    )

    assert owner.response_code == 202
    assert owner.get_response_json() == {
        "outbox_id": "local-outbox",
        "status": "queued_or_repairable",
    }
    assert queued == [(W44_JOB, "jmservera", storage)]
