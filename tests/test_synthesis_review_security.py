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
def prepared_exact_video(reviewed_stage, monkeypatch, tmp_path, request):
    """Run real approval/synthesis/preparation against private local contracts."""
    from podcaster.generation import manifest_bytes
    from podcaster.review import content_snapshot_sha256
    from podcaster.video.distribution import VideoDistributionConfig
    from podcaster.video.job_runner import run_video_generation

    storage, result, *_ = reviewed_stage
    target = getattr(request, "param", "youtube")
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
    http = _ReviewedYouTubeTransport(
        processing_status=processing_status, upload_status=upload_status
    )
    monkeypatch.setattr(distribution.VideoDistributionConfig, "from_env", lambda: config)
    monkeypatch.setattr(distribution, "_DefaultTransport", lambda: http)
    monkeypatch.setattr(distribution, "_get_youtube_access_token", lambda *_: "offline")
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    assert http.mutations == ["upload"]
    state = repository.read(document["outbox_id"])
    assert state["aggregate"]["externally_verified_public"] is False
    assert state["providers"]["youtube"]["result"] == "pending_provider"
    assert state["providers"]["youtube"]["verification"]["source"] == "youtube_processing_not_ready"
    assert all("processingDetails" in parts for parts in http.readback_parts)

    # Even public privacy on a later reconciliation cannot certify playability.
    http.video["status"]["privacyStatus"] = "public"
    assert _run_reviewed_worker(storage, document).reason == "reviewed_video_dispatched"
    state = repository.read(document["outbox_id"])
    assert state["aggregate"]["externally_verified_public"] is False
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
        assert outcome.reason == "media_review_required"
    except (DistributionOutboxError, ReviewBindingError):
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
def test_real_adapters_continue_pending_leg_without_replaying_finished_mutations(
    prepared_exact_video, monkeypatch
):
    from podcaster import publish
    from podcaster.video import distribution
    from tests.test_publish import _mock_graphql_listing_resp, _mock_json_resp

    storage, job_id, repository, document, config, _ = prepared_exact_video
    _video_decision(storage, job_id, document)
    clock = datetime.now(timezone.utc)
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
