from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from urllib.parse import urlencode

import pytest

from podcaster.api import GenerateHandler
from podcaster.publication_state import (
    append_evidence,
    evidence_path,
    publication_identity,
)
from podcaster.publication_status import (
    PublicationStatusError,
    publication_status,
    read_youtube_status,
)
from podcaster.video.youtube_reconcile import youtube_identity_tag
from tests.test_api import FakeHandler

NOW = datetime(2026, 10, 12, 20, 25, tzinfo=timezone.utc)
JOB = "podcast-2026-W42-84c67c525182"


class ReadOnlyStorage:
    def __init__(self):
        self.data: dict[str, bytes] = {}

    def get_bytes(self, path):
        return self.data.get(path)

    def update_bytes(self, path, content_type, update):
        self.data[path] = update(self.data.get(path))


@pytest.fixture
def accepted():
    storage = ReadOnlyStorage()
    manifest = {
        "job_id": JOB,
        "request": {
            "week": "2026-W42",
            "publish_run_id": "38270494467",
            "article_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "publication_identity_mode": "canonical",
            "language": "en",
        },
        "lifecycle": {"transitions": [{"to": "accepted", "at": NOW.isoformat()}]},
        "generation": {"synthesis_queue": {"status": "enqueued"}},
        "observability": {"correlation_id": JOB},
    }
    storage.data[f"jobs/{JOB}/manifest.json"] = json.dumps(manifest).encode()
    query = {
        key: manifest["request"][key]
        for key in ("week", "publish_run_id", "article_sha256", "manifest_sha256")
    }
    query.update(job_id=JOB, correlation_id=JOB)
    return storage, manifest, query


def receipts(storage, manifest, *, at=NOW):
    identity = publication_identity(manifest, JOB, "38270494467")
    for provider, provider_id, outcome, native, verification in (
        ("youtube", "_ZD9E_zTjtM", "draft_created", "unlisted", "none"),
        ("spotify", "127359743", "published", "published", "provider_readback"),
    ):
        append_evidence(
            storage,
            identity,
            platform=provider,
            media_kind="video",
            operation="distribution",
            outcome=outcome,
            provider_artifact_id=provider_id,
            native_state=native,
            verification=verification,
            evidence_source=f"{provider}_episode_readback",
            at=at,
        )
    return identity


def ready(identity, video_id):
    assert video_id == "_ZD9E_zTjtM"
    return {
        "uploadStatus": "processed",
        "processingStatus": "succeeded",
        "privacyStatus": "unlisted",
        "tags": [youtube_identity_tag(identity)],
    }


def save(storage, manifest):
    storage.data[f"jobs/{JOB}/manifest.json"] = json.dumps(manifest).encode()


def test_known_w42_provider_ids_unlisted_and_spotify_video_are_terminal(accepted):
    storage, manifest, query = accepted
    manifest["generation"].update(
        synthesis_runner={"status": "completed"}, video_runner={"status": "completed"}
    )
    save(storage, manifest)
    receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    response = publication_status(storage, query, now=NOW, youtube_readback=ready)
    assert response["schema_version"] == "podcast_publication_status_v1"
    assert response["identity"] == {
        field: query[field]
        for field in ("week", "publish_run_id", "article_sha256", "manifest_sha256")
    }
    assert response["job_id"] == response["correlation_id"] == JOB
    assert response["synthesis"]["state"] == response["video"]["state"] == "succeeded"
    assert response["provider"]["state"] == "published"
    assert response["provider"]["external_verified"] is True
    assert storage.data == before  # no receipt, manifest, queue or provider mutation


@pytest.mark.parametrize("field", ["week", "publish_run_id", "article_sha256", "manifest_sha256"])
def test_exact_identity_conflicts_fail_closed(accepted, field):
    storage, _, query = accepted
    query[field] = {"week": "2026-W43", "publish_run_id": "1"}.get(field, "c" * 64)
    with pytest.raises(PublicationStatusError) as error:
        publication_status(storage, query, now=NOW)
    assert error.value.status == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_id", "../../secret"),
        ("correlation_id", "../secret"),
        ("language", "es"),
        ("correlation_id", "different"),
    ],
)
def test_path_language_and_correlation_guards(accepted, field, value):
    storage, _, query = accepted
    query[field] = value
    with pytest.raises(PublicationStatusError):
        publication_status(storage, query, now=NOW)


@pytest.mark.parametrize(
    "synthesis,video,expected",
    [
        (None, None, ("pending", "pending")),
        ({"status": "running"}, {"status": "running"}, ("started", "pending")),
        ({"status": "failed"}, None, ("failed", "pending")),
        ({"status": "completed"}, {"status": "failed"}, ("succeeded", "failed")),
        ({"status": "nonsense"}, None, ("unknown", "pending")),
    ],
)
def test_stage_contract_states(accepted, synthesis, video, expected):
    storage, manifest, query = accepted
    manifest["generation"].update(synthesis_runner=synthesis, video_runner=video)
    save(storage, manifest)
    response = publication_status(storage, query, now=NOW)
    assert (response["synthesis"]["state"], response["video"]["state"]) == expected
    assert response["provider"]["state"] == "pending"
    assert response["provider"]["external_verified"] is False


def test_actual_progress_marks_synthesis_arrival(accepted):
    from podcaster.progress import emit_progress

    storage, _, query = accepted
    emit_progress(storage, JOB, stage="synthesis", phase="tts", at=NOW)
    response = publication_status(storage, query, now=NOW)
    assert response["synthesis"]["state"] == "started"


@pytest.mark.parametrize(
    "fault",
    [
        "spotify_missing",
        "audio_only",
        "spotify_draft",
        "spotify_no_readback",
        "stale",
        "future",
        "corrupt",
        "identity",
        "missing_id",
        "youtube_unknown",
    ],
)
def test_incomplete_stale_mismatched_receipts_never_green(accepted, fault):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    path = evidence_path(JOB)
    document = json.loads(storage.data[path])
    if fault == "spotify_missing":
        document["records"].pop()
    elif fault == "audio_only":
        document["records"][-1]["media_kind"] = "audio"
    elif fault == "spotify_draft":
        document["records"][-1].update(outcome="draft_created", native_state="draft")
    elif fault == "spotify_no_readback":
        document["records"][-1]["verification"] = "none"
    elif fault in {"stale", "future"}:
        delta = -timedelta(days=8) if fault == "stale" else timedelta(days=1)
        for record in document["records"]:
            record["checked_at"] = (NOW + delta).isoformat()
    elif fault == "identity":
        document["records"][-1]["article_sha256"] = "c" * 64
    elif fault == "missing_id":
        document["records"][-1].update(provider_id=None, provider_artifact_id=None)
    elif fault == "youtube_unknown":
        document["records"][0]["outcome"] = "publication_unknown"
    storage.data[path] = b"bad JSON" if fault == "corrupt" else json.dumps(document).encode()
    if fault in {"corrupt", "identity"}:
        with pytest.raises(PublicationStatusError):
            publication_status(storage, query, now=NOW, youtube_readback=ready)
    else:
        response = publication_status(storage, query, now=NOW, youtube_readback=ready)
        assert response["provider"]["external_verified"] is False
        assert response["provider"]["state"] != "published"


@pytest.mark.parametrize(
    "readback",
    [
        None,
        {"uploadStatus": "uploaded", "processingStatus": "processing", "privacyStatus": "unlisted"},
        {"uploadStatus": "processed", "processingStatus": "succeeded", "privacyStatus": "private"},
        {"uploadStatus": "failed"},
        {"uploadStatus": "uploaded", "processingStatus": "failed"},
    ],
)
def test_youtube_processing_and_visibility_truth(accepted, readback):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    response = publication_status(storage, query, now=NOW, youtube_readback=lambda *_: readback)
    assert response["provider"]["external_verified"] is False


@pytest.mark.parametrize("authorized", [False, True])
def test_authenticated_api_contract(accepted, monkeypatch, authorized):
    storage, _, query = accepted
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    factory = MagicMock(return_value=storage)
    monkeypatch.setattr("podcaster.api.create_storage_backend", factory)
    handler = FakeHandler(
        "GET",
        "/api/publication-status?" + urlencode(query),
        headers={"x-podcaster-api-key": "test-status-key" if authorized else "wrong"},
    )
    GenerateHandler.do_GET(handler)
    assert handler.response_code == (200 if authorized else 401)
    if authorized:
        assert handler.get_response_json()["schema_version"] == "podcast_publication_status_v1"
        assert handler.response_headers["Cache-Control"] == "no-store"
    else:
        factory.assert_not_called()


def test_api_duplicate_and_unknown_query_parameters_rejected(accepted, monkeypatch):
    storage, _, query = accepted
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    monkeypatch.setattr("podcaster.api.create_storage_backend", lambda: storage)
    for suffix in ("&job_id=second", "&path=secret"):
        handler = FakeHandler(
            "GET",
            "/api/publication-status?" + urlencode(query) + suffix,
            headers={"x-podcaster-api-key": "test-status-key"},
        )
        GenerateHandler.do_GET(handler)
        assert handler.response_code == 400


def test_api_unconfigured_auth_and_storage_errors_are_explicit(accepted, monkeypatch):
    from azure.core.exceptions import ServiceRequestError

    _, _, query = accepted
    factory = MagicMock(side_effect=ServiceRequestError("internal storage detail"))
    monkeypatch.setattr("podcaster.api.create_storage_backend", factory)
    headers = {"x-podcaster-api-key": "test-status-key"}
    handler = FakeHandler("GET", "/api/publication-status?" + urlencode(query), headers=headers)
    GenerateHandler.do_GET(handler)
    assert handler.response_code == 401
    factory.assert_not_called()
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    handler = FakeHandler("GET", "/api/publication-status?" + urlencode(query), headers=headers)
    GenerateHandler.do_GET(handler)
    assert handler.response_code == 503
    assert "internal" not in handler.get_response_json()["error"]


def test_status_oauth_failure_does_not_send_notifications(accepted, monkeypatch):
    storage, manifest, _ = accepted
    identity = receipts(storage, manifest)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "test-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("podcaster.video.distribution._load_youtube_refresh_token", lambda: "test")
    transport = MagicMock()
    transport.request.return_value = (400, b'{"error":"invalid_grant"}')
    notify = MagicMock(side_effect=AssertionError("status must not notify or write"))
    monkeypatch.setattr("podcaster.video.distribution._notify_youtube_reauthorization", notify)
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda **_: transport)
    assert read_youtube_status(identity, "_ZD9E_zTjtM") is None
    notify.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b'{"access_token":42}',
        b'{"access_token":"bad\\r\\nheader"}',
    ],
)
def test_malformed_oauth_readback_fails_closed(accepted, monkeypatch, body):
    storage, manifest, _ = accepted
    identity = receipts(storage, manifest)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "test-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("podcaster.video.distribution._load_youtube_refresh_token", lambda: "test")
    transport = MagicMock()
    transport.request.return_value = (200, body)
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda **_: transport)
    assert read_youtube_status(identity, "_ZD9E_zTjtM") is None
    assert transport.request.call_count == 1


@pytest.mark.parametrize(
    "item",
    [
        {"id": "other_video", "snippet": {}, "status": {}, "processingDetails": {}},
        {"id": "_ZD9E_zTjtM", "snippet": {"tags": ["unrelated"]}},
        {"id": "_ZD9E_zTjtM", "snippet": None},
    ],
)
def test_real_readback_rejects_wrong_id_tag_and_malformed_video(accepted, monkeypatch, item):
    storage, manifest, _ = accepted
    identity = receipts(storage, manifest)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "test-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("podcaster.video.distribution._load_youtube_refresh_token", lambda: "test")
    transport = MagicMock()
    transport.request.side_effect = [
        (200, b'{"access_token":"test-access"}'),
        (200, json.dumps({"items": [item]}).encode()),
    ]
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda **_: transport)
    assert read_youtube_status(identity, "_ZD9E_zTjtM") is None


def test_real_youtube_readback_uses_exact_video_and_identity_without_upload(accepted, monkeypatch):
    storage, manifest, _ = accepted
    identity = receipts(storage, manifest)
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_ID", "test-client")
    monkeypatch.setenv("VIDEO_YOUTUBE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("podcaster.video.distribution._load_youtube_refresh_token", lambda: "test")
    transport = MagicMock()
    transport.request.side_effect = [
        (200, b'{"access_token":"test-access"}'),
        (
            200,
            json.dumps(
                {
                    "items": [
                        {
                            "id": "_ZD9E_zTjtM",
                            "snippet": {"tags": [youtube_identity_tag(identity)]},
                            "status": {"uploadStatus": "processed", "privacyStatus": "unlisted"},
                            "processingDetails": {"processingStatus": "succeeded"},
                        }
                    ]
                }
            ).encode(),
        ),
    ]
    monkeypatch.setattr("podcaster.video.distribution._DefaultTransport", lambda **_: transport)
    assert read_youtube_status(identity, "_ZD9E_zTjtM")["uploadStatus"] == "processed"
    assert len(transport.request.call_args_list) == 2
    assert transport.request.call_args_list[0].kwargs["method"] == "POST"  # OAuth only
    assert transport.request.call_args_list[1].kwargs["method"] == "GET"
    assert "/youtube/v3/videos?" in transport.request.call_args_list[1].args[0]


def test_required_youtube_retry_preserves_spotify_and_rearms_only_no_create_claim(
    accepted, tmp_path, monkeypatch
):
    from podcaster.publication_state import claim_evidence, latest_outcomes, read_evidence
    from podcaster.video.distribution import (
        VideoDistributionConfig,
        YouTubeDeliveryError,
        distribute_video,
    )
    from podcaster.video.job_runner import _record_video_publication
    from tests.test_video_distribution import FakeStorage

    storage, manifest, _ = accepted
    identity = publication_identity(manifest, JOB, "38270494467")
    append_evidence(
        storage,
        identity,
        platform="spotify",
        media_kind="video",
        operation="distribution",
        outcome="published",
        provider_artifact_id="127359743",
        native_state="published",
        verification="provider_readback",
        evidence_source="spotify_episode_readback",
        at=NOW,
    )
    spotify_receipt = latest_outcomes(read_evidence(storage, JOB))["spotify:video"].copy()
    claim_evidence(
        storage, identity, platform="youtube", media_kind="video", operation="upload_intent"
    )
    published = {
        "spotify_upload": {
            "status": "published",
            "outcome": "published",
            "episode_id": 127359743,
            "verification": "provider_readback",
            "native_state": "published",
            "retry_blocked": True,
        }
    }
    upload = MagicMock(
        side_effect=[
            YouTubeDeliveryError(
                "transient OAuth failure",
                code="youtube_oauth_http_503",
                stage="oauth_token",
                retryable=True,
            ),
            ("_ZD9E_zTjtM", "https://youtube.com/watch?v=_ZD9E_zTjtM"),
        ]
    )
    spotify = MagicMock(side_effect=AssertionError("must never duplicate Spotify"))
    monkeypatch.setattr("podcaster.video.distribution.upload_to_youtube", upload)
    monkeypatch.setattr("podcaster.video.distribution.upload_to_spotify_episode", spotify)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"x" * 2048)
    config = VideoDistributionConfig(
        youtube_enabled=True, youtube_required=True, spotify_upload_enabled=True
    )

    def record(provider, receipt):
        published[provider] = receipt
        assert _record_video_publication(storage, JOB, identity, provider, receipt)

    first = distribute_video(
        video,
        JOB,
        "title | W42",
        "desc",
        120,
        config,
        storage=FakeStorage(),
        published=published,
        publish_run_id=identity.publish_run_id,
        on_published=record,
    )
    assert first.youtube_required_failed and first.youtube_failure_retryable
    assert first.spotify_upload_updated and first.blob_path
    failed = latest_outcomes(read_evidence(storage, JOB))["youtube:video"]
    assert failed["retry_blocked"] is False
    assert failed["mutation_attempted"] is False
    assert (
        claim_evidence(
            storage, identity, platform="youtube", media_kind="video", operation="upload_intent"
        )
        is not None
    )
    assert (
        claim_evidence(
            storage, identity, platform="youtube", media_kind="video", operation="upload_intent"
        )
        is None
    )  # concurrent retry cannot acquire twice
    second = distribute_video(
        video,
        JOB,
        "title | W42",
        "desc",
        120,
        config,
        storage=FakeStorage(),
        published=published,
        publish_run_id=identity.publish_run_id,
        on_published=record,
    )
    assert second.youtube_id == "_ZD9E_zTjtM"
    assert not second.youtube_required_failed
    assert second.spotify_upload_updated
    assert upload.call_count == 2
    spotify.assert_not_called()
    assert latest_outcomes(read_evidence(storage, JOB))["spotify:video"] == spotify_receipt


def test_required_youtube_ambiguous_creation_blocks_remutation(accepted, tmp_path, monkeypatch):
    from podcaster.publication_state import claim_evidence, read_evidence, retry_is_blocked
    from podcaster.video.distribution import (
        VideoDistributionConfig,
        YouTubeDeliveryError,
        distribute_video,
    )
    from podcaster.video.job_runner import _record_video_publication
    from tests.test_video_distribution import FakeStorage

    storage, manifest, _ = accepted
    identity = publication_identity(manifest, JOB, "38270494467")
    claim_evidence(
        storage, identity, platform="youtube", media_kind="video", operation="upload_intent"
    )
    upload = MagicMock(
        side_effect=YouTubeDeliveryError(
            "response lost",
            code="youtube_resumable_init_ambiguous",
            stage="resumable_session_init",
            retryable=False,
            mutation_ambiguous=True,
        )
    )
    monkeypatch.setattr("podcaster.video.distribution.upload_to_youtube", upload)
    published = {
        "spotify_upload": {
            "status": "published",
            "outcome": "published",
            "episode_id": 127359743,
        }
    }
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"x" * 2048)
    config = VideoDistributionConfig(
        youtube_enabled=True, youtube_required=True, spotify_upload_enabled=True
    )

    def record(provider, receipt):
        published[provider] = receipt
        assert _record_video_publication(storage, JOB, identity, provider, receipt)

    for _ in range(2):
        result = distribute_video(
            video,
            JOB,
            "title | W42",
            "desc",
            120,
            config,
            storage=FakeStorage(),
            published=published,
            publish_run_id=identity.publish_run_id,
            on_published=record,
        )
        assert result.provider_outcomes["youtube"] == "publication_unknown"
    assert upload.call_count == 1
    assert retry_is_blocked(read_evidence(storage, JOB), platform="youtube", media_kind="video")
    assert (
        claim_evidence(
            storage, identity, platform="youtube", media_kind="video", operation="upload_intent"
        )
        is None
    )


def test_in_flight_upload_intent_is_pending_not_terminal_unknown(accepted):
    from podcaster.publication_state import claim_evidence

    storage, manifest, query = accepted
    identity = publication_identity(manifest, JOB, query["publish_run_id"])
    claim_evidence(
        storage,
        identity,
        platform="youtube",
        media_kind="video",
        operation="upload_intent",
        at=NOW,
    )
    response = publication_status(storage, query, now=NOW)
    assert response["provider"]["state"] == "pending"
    assert response["provider"]["providers"]["youtube"]["code"] == "upload_in_progress"


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "invented"),
        ("outcome", "completed"),
        ("verification", "yes"),
        ("seq", True),
        ("at", "yesterday"),
    ],
)
def test_malformed_provider_receipts_are_explicit(accepted, field, value):
    storage, manifest, query = accepted
    receipts(storage, manifest)
    path = evidence_path(JOB)
    document = json.loads(storage.data[path])
    document["records"][-1][field] = value
    storage.data[path] = json.dumps(document).encode()
    with pytest.raises(PublicationStatusError) as error:
        publication_status(storage, query, now=NOW, youtube_readback=ready)
    assert error.value.code == "invalid_provider_evidence"


@pytest.mark.integration
def test_http_api_terminal_status_contract_is_read_only(accepted, monkeypatch):
    from functools import partial
    from http.server import HTTPServer
    from threading import Thread
    from urllib.request import Request, urlopen

    storage, manifest, query = accepted
    manifest["generation"].update(
        synthesis_runner={"status": "completed"}, video_runner={"status": "completed"}
    )
    save(storage, manifest)
    receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    monkeypatch.setattr("podcaster.api.create_storage_backend", lambda: storage)
    monkeypatch.setattr(
        "podcaster.api.publication_status",
        partial(publication_status, now=NOW, youtube_readback=ready),
    )
    generation = MagicMock(side_effect=AssertionError("GET must never generate"))
    monkeypatch.setattr("podcaster.api.run_generation_job", generation)
    server = HTTPServer(("127.0.0.1", 0), GenerateHandler)
    worker = Thread(target=server.serve_forever)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/publication-status?"
        request = Request(
            url + urlencode(query), headers={"x-podcaster-api-key": "test-status-key"}
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == 200
            payload = json.load(response)
        # The exact enums and fields accepted by SquadScope's v1 terminal consumer.
        assert payload["schema_version"] == "podcast_publication_status_v1"
        assert payload["identity"] == {
            field: query[field]
            for field in ("week", "publish_run_id", "article_sha256", "manifest_sha256")
        }
        assert payload["job_id"] == query["job_id"]
        assert payload["correlation_id"] == query["correlation_id"]
        assert payload["synthesis"]["state"] in {"started", "succeeded"}
        assert payload["video"]["state"] == "succeeded"
        assert payload["provider"]["state"] == "published"
        assert payload["provider"]["external_verified"] is True
        assert storage.data == before
        generation.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


@pytest.mark.integration
@pytest.mark.parametrize("authorized", [False, True])
def test_production_fastapi_entrypoint_contract_and_auth(accepted, monkeypatch, authorized):
    from functools import partial

    from fastapi.testclient import TestClient

    from podcaster.monitoring import app

    storage, manifest, query = accepted
    manifest["generation"].update(
        synthesis_runner={"status": "completed"}, video_runner={"status": "completed"}
    )
    save(storage, manifest)
    receipts(storage, manifest)
    before = copy.deepcopy(storage.data)
    monkeypatch.setenv("PODCASTER_API_KEY", "test-status-key")
    factory = MagicMock(return_value=storage)
    monkeypatch.setattr("podcaster.monitoring.get_storage", factory)
    monkeypatch.setattr(
        "podcaster.monitoring.publication_status",
        partial(publication_status, now=NOW, youtube_readback=ready),
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/publication-status",
            params=query,
            headers={"x-podcaster-api-key": "test-status-key" if authorized else "wrong"},
        )
    assert response.status_code == (200 if authorized else 401)
    if authorized:
        assert response.headers["Cache-Control"] == "no-store"
        payload = response.json()
        assert payload["schema_version"] == "podcast_publication_status_v1"
        assert payload["job_id"] == payload["correlation_id"] == JOB
        assert payload["provider"]["state"] == "published"
        assert payload["provider"]["external_verified"] is True
        assert payload["video"]["state"] == payload["synthesis"]["state"] == "succeeded"
        assert storage.data == before
    else:
        factory.assert_not_called()
