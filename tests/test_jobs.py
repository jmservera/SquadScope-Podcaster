from __future__ import annotations

import hashlib
import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from zipfile import ZipFile

import pytest

from podcaster.costs import monthly_ledger_path
from podcaster.generation import generate_artifacts, manifest_bytes
from podcaster.jobs import build_job_id, run_generation_job
from podcaster.storage import (
    AzureBlobStorageBackend,
    LocalStorageBackend,
    _managed_identity_resource,
    _token_expires_on,
    create_storage_backend,
    normalize_artifact_base_url,
)
from podcaster.validation import RESPONSE_KEYS


def test_generation_job_stages_manifest_review_gate_and_packet() -> None:
    artifact_root = Path(".test-artifacts")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {"week": "2026-W23", "article_url": "https://example.com/article"},
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert tuple(result.response.keys()) == RESPONSE_KEYS
    assert result.response["status"] == "accepted"
    assert result.manifest["status"] == "review_pending"
    assert result.manifest["review"]["status"] == "pending"
    assert result.manifest["review"]["required"] is True
    assert result.manifest["review"]["mechanism"] == "github_environment"
    assert result.manifest["review"]["environment"] == "podcast-review"
    assert result.manifest["review"]["workflow"] == ".github/workflows/podcast-review-gate.yml"
    assert result.manifest["review"]["gate"]["status"] == "blocked"
    assert result.manifest["review"]["gate"]["approval_required_before"] == "non_dry_run_tts_synthesis"
    assert result.manifest["cost_ledger"]["budget"]["status"] == "within_budget"
    assert result.manifest["cost_ledger"]["readiness"]["complete"] is True
    assert result.manifest["generation"]["tts_synthesis"] == {
        "status": "blocked",
        "allowed": False,
        "blocked_by": ["human_review", "provider_not_selected"],
        "dry_run_bypass_allowed": False,
    }
    assert result.manifest["generation"]["audio_validation"]["status"] == "blocked"
    assert result.manifest["generation"]["audio_validation"]["ready"] is False
    assert result.manifest["generation"]["audio_validation"]["metadata"]["content_type"] == "audio/mpeg"
    assert result.manifest["artifact_access"]["model"] == "private_operator_path"
    assert result.manifest["artifact_access"]["response_urls"] == {
        "publicly_accessible": False,
        "requires_operator_credentials": True,
        "signed_urls": False,
        "query_strings_allowed": False,
        "credential_material_allowed": False,
    }
    assert result.manifest["artifact_access"]["retention"]["cleanup_after"] == result.response["expires_at"]
    assert result.manifest["artifact_access"]["audit"]["correlation_id"] == result.response["job_id"]
    assert result.response["publishing_packet_url"].endswith(".zip")
    assert "human review is required before publishing" in result.response["warnings"]
    assert "artifact URLs are private operator paths, not public publishing links" in result.response["warnings"]

    job_dir = artifact_root / "jobs" / result.response["job_id"]
    manifest_file = job_dir / "manifest.json"
    packet_file = job_dir / "packets" / f"{result.response['job_id']}.zip"
    assert (job_dir / "script.txt").exists()
    assert (job_dir / "claim-ledger.json").exists()
    assert (job_dir / "cost-ledger.json").exists()
    assert (job_dir / "review-checklist.md").exists()
    assert packet_file.exists()
    
    # Verify packet manifest uses flat structure per editorial standards section 7.2
    with ZipFile(packet_file) as packet:
        packet_manifest = json.loads(packet.read("MANIFEST.json"))
        assert packet_manifest["review_status"] == "pending"
        assert packet_manifest["review"]["environment"] == "podcast-review"
        assert packet_manifest["review"]["gate"]["status"] == "blocked"
        assert packet_manifest["generation"]["audio_validation"]["status"] == "blocked"
        assert packet_manifest["cost_ledger"]["budget"]["status"] == "within_budget"
        assert packet_manifest["publishing"]["packet_ready"] is False
        assert packet_manifest["publishing"]["readiness_checks"]["cost_ledger_complete"] is True
        assert packet_manifest["artifact_access"]["model"] == "private_operator_path"
        assert packet_manifest["artifact_access"]["publication"]["eligible"] is False
    
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_dry_run_preserves_response_shape_and_review_metadata() -> None:
    artifact_root = Path(".test-artifacts-dry-run")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {"week": "2026-W23", "article_url": "https://example.com/article", "dry_run": True, "callback": {"url": "https://example.com/cb", "secret_name": "CALLBACK_SECRET"}},
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert tuple(result.response.keys()) == RESPONSE_KEYS
    assert result.response["status"] == "dry_run"
    assert result.manifest["request"]["dry_run"] is True
    assert result.manifest["status"] == "dry_run"
    assert result.manifest["request"]["callback"] == {
        "requested": True,
        "url_host": "example.com",
        "secret_name_provided": True,
    }
    assert result.manifest["review"]["required"] is True
    assert result.manifest["review"]["required_for_tts"] is False
    assert result.manifest["review"]["status"] == "pending"
    assert result.manifest["review"]["gate"]["status"] == "dry_run_bypass"
    assert result.manifest["generation"]["tts_synthesis"]["dry_run_bypass_allowed"] is True
    assert "callback accepted by contract but not invoked yet" in result.response["warnings"]
    assert "CALLBACK_SECRET" not in json.dumps(result.manifest)
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_publishing_packet_extracts_with_required_files_and_checksums() -> None:
    artifact_root = Path(".test-artifacts-packet")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {"week": "2026-W23", "article_url": "https://example.com/article"},
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    packet_file = artifact_root / "jobs" / result.response["job_id"] / "packets" / f"{result.response['job_id']}.zip"
    with ZipFile(packet_file) as packet:
        names = set(packet.namelist())
        required = {
            "README.txt",
            "MANIFEST.json",
            "REVIEW-CHECKLIST.md",
            "PUBLISHING-GUIDE.txt",
            "script.txt",
            "claim-ledger.json",
            "COST-LEDGER.json",
            "transcript.txt",
            "show-notes.md",
            "audio/episode-2026-W23.mp3",
            "RIGHTS-AND-ATTRIBUTION.txt",
            "CHECKSUMS.txt",
        }
        assert required <= names
        manifest = json.loads(packet.read("MANIFEST.json"))
        assert manifest["job_id"] == result.response["job_id"]
        # Verify flat structure per editorial standards section 7.2
        assert manifest["review_status"] == "pending"
        assert manifest["publishing"]["eligible"] is False
        assert manifest["publishing"]["packet_ready"] is False
        assert manifest["generation"]["audio_placeholder"] is True
        assert manifest["generation"]["tts_synthesis"]["allowed"] is False
        cost_ledger = json.loads(packet.read("COST-LEDGER.json"))
        assert cost_ledger == manifest["cost_ledger"]
        readme = packet.read("README.txt").decode("utf-8")
        publishing_guide = packet.read("PUBLISHING-GUIDE.txt").decode("utf-8")
        show_notes = packet.read("show-notes.md").decode("utf-8")
        assert "Publication is blocked" in show_notes
        assert "Publication blocker checklist" in readme
        assert "PUBLICATION BLOCKED - PLACEHOLDER PACKET" in publishing_guide
        assert "Do not upload the MP3" in publishing_guide
        assert "PUBLISHING TO SPOTIFY" not in publishing_guide
        assert "Click 'Upload'" not in publishing_guide
        assert "<enclosure" not in publishing_guide
        assert "Submit feed URL" not in publishing_guide
        checksums = _parse_checksums(packet.read("CHECKSUMS.txt").decode("utf-8"))
        assert set(checksums) == names - {"CHECKSUMS.txt"}
        for name, expected in checksums.items():
            assert hashlib.sha256(packet.read(name)).hexdigest() == expected

    shutil.rmtree(artifact_root, ignore_errors=True)


def test_generation_outputs_are_deterministic_and_documented() -> None:
    payload = {"week": "2026-W23", "article_url": "https://example.com/article", "article_sha256": "a" * 64}
    created_at = datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc)
    job_id = build_job_id(payload)

    first = generate_artifacts(job_id, payload, created_at)
    second = generate_artifacts(job_id, payload, created_at)

    assert [(artifact.path, artifact.content, artifact.content_type) for artifact in first] == [
        (artifact.path, artifact.content, artifact.content_type) for artifact in second
    ]
    assert [artifact.path for artifact in first] == [
        f"jobs/{job_id}/script.txt",
        f"jobs/{job_id}/claim-ledger.json",
        f"jobs/{job_id}/cost-ledger.json",
        f"jobs/{job_id}/transcript.txt",
        f"jobs/{job_id}/show-notes.md",
        f"jobs/{job_id}/review-checklist.md",
        f"jobs/{job_id}/audio/{job_id}.mp3",
        f"jobs/{job_id}/packets/{job_id}.zip",
    ]
    script = first[0].content.decode("utf-8")
    cost_ledger = json.loads(first[2].content.decode("utf-8"))
    transcript = first[3].content.decode("utf-8")
    show_notes = first[4].content.decode("utf-8")
    assert "deterministic production-path placeholder" in script
    assert cost_ledger["budget"]["status"] == "within_budget"
    assert cost_ledger["costs"]["staging_storage"]["estimated_usd"] == "0.00"
    assert "Title: Claracle Podcast" in transcript
    assert "Original article](https://example.com/article)" in show_notes
    assert first[6].content.startswith(f"Audio placeholder for {job_id}".encode("utf-8"))
    assert first[7].content_type == "application/zip"


def test_local_storage_backend_stages_under_safe_project_relative_paths(monkeypatch) -> None:
    artifact_root = Path(".test-artifacts-storage")
    shutil.rmtree(artifact_root, ignore_errors=True)
    monkeypatch.delenv("PODCASTER_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.setenv("PODCASTER_LOCAL_STORAGE_PATH", str(artifact_root))
    monkeypatch.setenv("PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/base/")

    storage = create_storage_backend()
    stored = storage.put_bytes("../jobs/./podcast-safe/../manifest.json", b"{}", "application/json")

    assert isinstance(storage, LocalStorageBackend)
    assert stored.path == "jobs/podcast-safe/manifest.json"
    assert stored.url == "https://example.invalid/base/jobs/podcast-safe/manifest.json"
    assert stored.size_bytes == 2
    assert stored.content_type == "application/json"
    assert (artifact_root / "jobs" / "podcast-safe" / "manifest.json").read_bytes() == b"{}"
    assert not Path("manifest.json").exists()
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_managed_identity_scope_is_converted_to_resource() -> None:
    assert _managed_identity_resource("https://storage.azure.com/.default") == "https://storage.azure.com"
    assert _managed_identity_resource("https://storage.azure.com") == "https://storage.azure.com"


def test_managed_identity_expiry_accepts_epoch_or_expires_in(monkeypatch) -> None:
    assert _token_expires_on({"expires_on": "1800000000"}) == 1800000000
    monkeypatch.setattr("podcaster.storage.time.time", lambda: 1000)
    assert _token_expires_on({"expires_in": "60"}) == 1060


def test_artifact_urls_are_private_operator_paths_without_query_credentials() -> None:
    artifact_root = Path(".test-artifacts-private-urls")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://storage.example.invalid/private-artifacts")

    result = run_generation_job(
        {"week": "2026-W23", "article_url": "https://example.com/article"},
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    response_urls = [
        result.response[key]
        for key in ("manifest_url", "mp3_url", "transcript_url", "show_notes_url", "publishing_packet_url")
    ]
    manifest_urls = [details["url"] for details in result.manifest["artifacts"].values()]
    for url in response_urls + manifest_urls:
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.query == ""
        assert parsed.fragment == ""
        assert parsed.username is None
        assert parsed.password is None

    assert result.manifest["artifact_access"]["response_urls"]["signed_urls"] is False
    assert result.manifest["artifact_access"]["operator_access"]["method"] == "Azure RBAC or local filesystem access"
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_artifact_base_urls_reject_embedded_credentials_and_signed_queries() -> None:
    assert normalize_artifact_base_url("https://storage.example.invalid/base/") == "https://storage.example.invalid/base"
    for unsafe_url in (
        "https://storage.example.invalid/base?sig=secret",
        "https://user:pass@storage.example.invalid/base",
        "https://storage.example.invalid/base#token",
    ):
        try:
            normalize_artifact_base_url(unsafe_url)
        except ValueError as exc:
            assert "must not contain credentials" in str(exc)
        else:
            raise AssertionError(f"unsafe artifact base URL was accepted: {unsafe_url}")


def test_job_lifecycle_metadata_observability_and_manifest_serialization(caplog) -> None:
    artifact_root = Path(".test-artifacts-lifecycle")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")
    payload = {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_sha256": "b" * 64,
        "source_artifacts": ["https://example.com/source.json"],
        "force": True,
    }

    with caplog.at_level(logging.INFO):
        result = run_generation_job(payload, storage=storage, now=datetime(2026, 6, 7, 19, 7, 49, 816000, tzinfo=timezone.utc))

    manifest = result.manifest
    job_id = build_job_id(payload)
    assert result.response["job_id"] == job_id
    assert manifest["job_id"] == job_id
    assert manifest["status"] == "review_pending"
    assert manifest["created_at"] == "2026-06-07T19:07:49Z"
    assert manifest["expires_at"] == result.response["expires_at"] == "2026-06-14T19:07:49Z"
    assert manifest["request"] == {
        "week": "2026-W23",
        "article_url": "https://example.com/article",
        "article_sha256": "b" * 64,
        "article_title": None,
        "article_content_provided": False,
        "source_artifacts": ["https://example.com/source.json"],
        "dry_run": False,
        "force": True,
        "cost_override": {"recorded": False, "actor": None, "recorded_at": None},
        "callback": {"requested": False, "url_host": None, "secret_name_provided": False},
    }
    assert manifest["lifecycle"]["force"] is True
    assert manifest["lifecycle"]["transitions"][-1]["to"] == "review_pending"
    assert manifest["publishing"]["blocked_by"] == ["human_review", "real_tts_not_implemented", "audio_validation_not_passed"]
    assert manifest["review"]["artifacts_for_review"] == [
        "script.txt",
        "claim-ledger.json",
        "cost-ledger.json",
        "transcript.txt",
        "show-notes.md",
        "review-checklist.md",
        "manifest.json",
        "publishing-packet.zip",
    ]
    assert manifest["generation"]["tts_synthesis"]["allowed"] is False
    assert manifest["artifact_access"]["publication"]["blocked_by"] == ["human_review", "real_tts_not_implemented"]
    assert manifest["publishing"]["packet_ready"] is False
    assert manifest["publishing"]["readiness_checks"] == {
        "cost_ledger_complete": True,
        "budget_status": "within_budget",
        "editorial_review_complete": False,
        "real_audio_available": False,
    }
    assert manifest["cost_ledger"]["week"] == "2026-W23"
    assert manifest["cost_ledger"]["provider"] == "not_selected"
    assert manifest["cost_ledger"]["duration_seconds"] == 0
    assert manifest["cost_ledger"]["privacy"]["secrets_recorded"] is False
    assert manifest["observability"]["correlation_id"] == job_id
    assert all(details["url"].startswith("https://example.invalid/artifacts/jobs/") for details in manifest["artifacts"].values())
    assert all(
        details["access_model"] == "private_operator_path"
        and details["publicly_accessible"] is False
        and details["size_bytes"] > 0
        and details["content_type"]
        and len(details["sha256"]) == 64
        for details in manifest["artifacts"].values()
    )
    serialized = json.loads(manifest_bytes(manifest).decode("utf-8"))
    assert serialized == manifest
    assert f"podcaster job staged job_id={job_id} status=review_pending dry_run=False artifact_count=9" in caplog.text
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_non_dry_run_fails_closed_when_monthly_episode_limit_exceeded() -> None:
    artifact_root = Path(".test-artifacts-budget")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")
    storage.put_bytes(
        monthly_ledger_path("2026-06"),
        manifest_bytes(
            {
                "schema_version": "squadscope-podcaster-monthly-cost-ledger-v1",
                "month": "2026-06",
                "episodes": [
                    {"job_id": f"existing-{index}", "week": f"2026-W2{index}", "estimated_total_usd": "0.00"}
                    for index in range(5)
                ],
            }
        ),
        "application/json; charset=utf-8",
    )

    result = run_generation_job(
        {"week": "2026-W29", "article_url": "https://example.com/new-article"},
        storage=storage,
        now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "failed"
    assert result.response["errors"] == ["monthly podcast budget exceeded; explicit operator override required"]
    assert result.manifest["budget"]["status"] == "over_budget"
    assert not (artifact_root / "jobs" / str(result.manifest["job_id"])).exists()
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_monthly_budget_is_reserved_before_artifacts_are_staged() -> None:
    artifact_root = Path(".test-artifacts-budget-reservation")
    shutil.rmtree(artifact_root, ignore_errors=True)

    class TrackingStorage(LocalStorageBackend):
        def __init__(self, root: Path, base_url: str) -> None:
            super().__init__(root, base_url)
            self.monthly_updates: list[str] = []

        def update_bytes(self, path, content_type, update):
            self.monthly_updates.append(path)
            return super().update_bytes(path, content_type, update)

        def put_bytes(self, path, content, content_type):
            if path.startswith("jobs/") and not self.monthly_updates:
                raise AssertionError("job artifacts were staged before monthly budget reservation")
            return super().put_bytes(path, content, content_type)

    storage = TrackingStorage(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {"week": "2026-W29", "article_url": "https://example.com/new-article"},
        storage=storage,
        now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "accepted"
    assert storage.monthly_updates == [monthly_ledger_path("2026-06"), monthly_ledger_path("2026-06")]
    monthly = json.loads((artifact_root / monthly_ledger_path("2026-06")).read_text(encoding="utf-8"))
    assert monthly["episodes"][-1].get("state") != "reserved"
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_concurrent_jobs_share_atomic_monthly_budget_reservation() -> None:
    artifact_root = Path(".test-artifacts-concurrent-budget")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    payloads = [
        {"week": f"2026-W2{index}", "article_url": f"https://example.com/article-{index}"}
        for index in range(6)
    ]

    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(
                    lambda payload: run_generation_job(
                        payload,
                        storage=storage,
                        now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
                    ),
                    payloads,
                )
            )

        statuses = [result.response["status"] for result in results]
        assert statuses.count("accepted") == 5
        assert statuses.count("failed") == 1

        monthly = json.loads((artifact_root / monthly_ledger_path("2026-06")).read_text(encoding="utf-8"))
        assert len(monthly["episodes"]) == 5
        assert len({episode["job_id"] for episode in monthly["episodes"]}) == 5
        assert all(episode.get("state") != "reserved" for episode in monthly["episodes"])

        staged_job_ids = {path.name for path in (artifact_root / "jobs").iterdir() if path.is_dir()}
        accepted_job_ids = {
            build_job_id(payload)
            for payload, result in zip(payloads, results, strict=True)
            if result.response["status"] == "accepted"
        }
        failed_job_ids = {build_job_id(payload) for payload in payloads} - accepted_job_ids
        assert staged_job_ids == accepted_job_ids
        assert not any((artifact_root / "jobs" / job_id).exists() for job_id in failed_job_ids)
    finally:
        shutil.rmtree(artifact_root, ignore_errors=True)


def test_non_dry_run_allows_explicit_operator_cost_override() -> None:
    artifact_root = Path(".test-artifacts-budget-override")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")
    storage.put_bytes(
        monthly_ledger_path("2026-06"),
        manifest_bytes(
            {
                "schema_version": "squadscope-podcaster-monthly-cost-ledger-v1",
                "month": "2026-06",
                "episodes": [
                    {"job_id": f"existing-{index}", "week": f"2026-W2{index}", "estimated_total_usd": "0.00"}
                    for index in range(5)
                ],
            }
        ),
        "application/json; charset=utf-8",
    )

    result = run_generation_job(
        {
            "week": "2026-W29",
            "article_url": "https://example.com/new-article",
            "force": True,
            "cost_override": {
                "actor": "hermes",
                "reason": "approved launch exception",
                "recorded_at": "2026-06-09T11:00:00Z",
            },
        },
        storage=storage,
        now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "accepted"
    assert result.manifest["cost_ledger"]["budget"]["status"] == "override_recorded"
    assert result.manifest["cost_ledger"]["budget"]["override"] == {
        "actor": "hermes",
        "reason": "approved launch exception",
        "recorded_at": "2026-06-09T11:00:00Z",
    }
    monthly = json.loads((artifact_root / monthly_ledger_path("2026-06")).read_text(encoding="utf-8"))
    assert len(monthly["episodes"]) == 6
    assert monthly["episodes"][-1]["budget_status"] == "override_recorded"
    shutil.rmtree(artifact_root, ignore_errors=True)


def test_azure_conditional_update_retries_412_conflicts_then_succeeds(monkeypatch) -> None:
    backend = AzureBlobStorageBackend("https://storage.example.invalid", "podcaster-artifacts")
    states = [(b'{"attempt": 1}', '"etag-1"'), (b'{"attempt": 2}', '"etag-2"')]
    put_attempts: list[dict[str, str | None]] = []

    def get_blob_state(safe_path: str) -> tuple[bytes | None, str | None]:
        assert safe_path == monthly_ledger_path("2026-06")
        return states.pop(0)

    def put_blob(
        path: str,
        content: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        put_attempts.append(
            {
                "path": path,
                "content": content.decode("utf-8"),
                "if_match": if_match,
                "if_none_match": if_none_match,
            }
        )
        if len(put_attempts) == 1:
            raise HTTPError("https://storage.example.invalid/blob", 412, "Precondition Failed", hdrs=None, fp=None)

    monkeypatch.setattr(backend, "_get_blob_state", get_blob_state)
    monkeypatch.setattr(backend, "_put_blob", put_blob)

    stored = backend.update_bytes(
        monthly_ledger_path("2026-06"),
        "application/json; charset=utf-8",
        lambda content: content.replace(b"attempt", b"updated") if content else b"{}",
    )

    assert stored.path == monthly_ledger_path("2026-06")
    assert [attempt["if_match"] for attempt in put_attempts] == ['"etag-1"', '"etag-2"']
    assert [attempt["if_none_match"] for attempt in put_attempts] == [None, None]
    assert put_attempts[-1]["content"] == '{"updated": 2}'


def test_azure_conditional_update_fails_after_412_retry_exhaustion(monkeypatch) -> None:
    backend = AzureBlobStorageBackend("https://storage.example.invalid", "podcaster-artifacts")
    get_attempts: list[str] = []
    put_attempts: list[str] = []

    def get_blob_state(safe_path: str) -> tuple[bytes | None, str | None]:
        get_attempts.append(safe_path)
        return b'{"episodes": []}', '"stale-etag"'

    def put_blob(
        path: str,
        content: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        put_attempts.append(f"{path}:{if_match}:{if_none_match}")
        raise HTTPError("https://storage.example.invalid/blob", 412, "Precondition Failed", hdrs=None, fp=None)

    monkeypatch.setattr(backend, "_get_blob_state", get_blob_state)
    monkeypatch.setattr(backend, "_put_blob", put_blob)

    with pytest.raises(RuntimeError, match="concurrent updates did not settle"):
        backend.update_bytes(
            monthly_ledger_path("2026-06"),
            "application/json; charset=utf-8",
            lambda content: content or b"{}",
        )

    assert get_attempts == [monthly_ledger_path("2026-06")] * 5
    assert put_attempts == [f"{monthly_ledger_path('2026-06')}:\"stale-etag\":None"] * 5


def test_azure_conditional_update_retry_exhaustion_stops_before_artifacts() -> None:
    class AlwaysConflictingAzureStorage(AzureBlobStorageBackend):
        def __init__(self) -> None:
            self._account_url = "https://storage.example.invalid"
            self._container_name = "podcaster-artifacts"
            self.get_attempts = 0
            self.put_attempts = 0
            self.artifact_puts: list[str] = []

        def put_bytes(self, path, content, content_type):
            self.artifact_puts.append(path)
            return super().put_bytes(path, content, content_type)

        def _get_blob_state(self, safe_path: str):
            self.get_attempts += 1
            return None, None

        def _put_blob(self, path, content, content_type, *, if_match=None, if_none_match=None):
            self.put_attempts += 1
            raise HTTPError("https://storage.example.invalid/blob", 412, "Precondition Failed", hdrs=None, fp=None)

    storage = AlwaysConflictingAzureStorage()

    with pytest.raises(RuntimeError, match="concurrent updates did not settle"):
        run_generation_job(
            {"week": "2026-W29", "article_url": "https://example.com/new-article"},
            storage=storage,
            now=datetime(2026, 6, 30, 19, 7, 49, tzinfo=timezone.utc),
        )

    assert storage.get_attempts == 5
    assert storage.put_attempts == 5
    assert storage.artifact_puts == []


def test_artifacts_do_not_include_api_secret_marker() -> None:
    artifact_root = Path(".test-artifacts-secret-scan")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    result = run_generation_job(
        {"week": "2026-W23", "article_url": "https://example.com/article"},
        storage=storage,
        now=datetime(2026, 6, 7, 19, 7, 49, tzinfo=timezone.utc),
    )

    serialized_response = json.dumps(result.response)
    serialized_manifest = json.dumps(result.manifest)
    packet_bytes = (artifact_root / "jobs" / result.response["job_id"] / "packets" / f"{result.response['job_id']}.zip").read_bytes()
    assert "dont-leak-me" not in serialized_response
    assert "dont-leak-me" not in serialized_manifest
    assert b"dont-leak-me" not in packet_bytes
    shutil.rmtree(artifact_root, ignore_errors=True)


def _parse_checksums(content: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in content.splitlines():
        digest, name = line.split("  ", 1)
        parsed[name] = digest
    return parsed


def test_llm_script_generation_replaces_placeholder_when_article_content_provided(monkeypatch) -> None:
    """When article_content is present and chat endpoint is configured, the LLM script is used."""
    import json as _json
    from urllib.request import Request

    artifact_root = Path(".test-artifacts-llm-gen")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    # Configure the chat endpoint via environment
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")

    # Mock the managed identity token and HTTP transport
    fake_dialogue = "Theo: Welcome to Claracle! Let's dive into AI.\nVera: Both hosts on this show are AI-generated synthetic voices, not human presenters. Let's go."

    def fake_transport(request: Request) -> bytes:
        return _json.dumps({"choices": [{"message": {"content": fake_dialogue}}]}).encode()

    def fake_token_provider(scope: str) -> str:
        return "fake-token"

    # Patch the script_gen module to use our fakes
    from podcaster import script_gen
    monkeypatch.setattr(script_gen, "_default_transport", fake_transport)
    monkeypatch.setattr(script_gen.ManagedIdentityTokenCredential, "get_token", staticmethod(fake_token_provider))

    result = run_generation_job(
        {
            "week": "2026-W24",
            "article_url": "https://example.com/ai-article",
            "article_title": "AI Revolution",
            "article_content": "This article discusses the latest AI developments in June 2026.",
        },
        storage=storage,
        now=datetime(2026, 6, 12, 10, 0, 0, tzinfo=timezone.utc),
    )

    assert result.response["status"] == "accepted"
    assert result.manifest["generation"]["engine"] == "llm-script-gen"
    assert result.manifest["generation"]["deterministic"] is False

    # The script artifact should contain the LLM-generated dialogue
    job_id = result.response["job_id"]
    script_file = artifact_root / "jobs" / job_id / "script.txt"
    script_content = script_file.read_text()
    assert "Theo: Welcome to Claracle!" in script_content
    assert "AI-generated synthetic voices" in script_content
    assert "---" in script_content  # Header separator present

    shutil.rmtree(artifact_root, ignore_errors=True)


def test_llm_script_generation_falls_back_on_failure(monkeypatch) -> None:
    """When LLM call fails, falls back to deterministic placeholder."""
    from urllib.request import Request

    artifact_root = Path(".test-artifacts-llm-fallback")
    shutil.rmtree(artifact_root, ignore_errors=True)
    storage = LocalStorageBackend(artifact_root, "https://example.invalid/artifacts")

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o-mini")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "managed_identity")

    def failing_transport(request: Request) -> bytes:
        raise RuntimeError("LLM endpoint unavailable")

    def fake_token_provider(scope: str) -> str:
        return "fake-token"

    from podcaster import script_gen
    monkeypatch.setattr(script_gen, "_default_transport", failing_transport)
    monkeypatch.setattr(script_gen.ManagedIdentityTokenCredential, "get_token", staticmethod(fake_token_provider))

    result = run_generation_job(
        {
            "week": "2026-W24",
            "article_url": "https://example.com/ai-article",
            "article_title": "AI Revolution",
            "article_content": "Some article content here.",
        },
        storage=storage,
        now=datetime(2026, 6, 12, 10, 0, 0, tzinfo=timezone.utc),
    )

    # Falls back to placeholder
    assert result.response["status"] == "accepted"
    assert result.manifest["generation"]["engine"] == "local-deterministic-placeholder"
    assert result.manifest["generation"]["deterministic"] is True
    assert any("LLM script generation failed" in w for w in result.response["warnings"])

    shutil.rmtree(artifact_root, ignore_errors=True)

