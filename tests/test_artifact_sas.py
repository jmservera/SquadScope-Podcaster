from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcaster.artifact_access import (
    operator_download_access_metadata,
    sas_download_record,
)
from podcaster.storage import (
    DOWNLOAD_METHOD_LOCAL_LOCATOR,
    DOWNLOAD_METHOD_USER_DELEGATION_SAS,
    AzureBlobStorageBackend,
    LocalStorageBackend,
    SignedDownloadUrl,
    StoredArtifact,
    create_storage_backend,
)
from scripts.produce_episode import stage_review_upload


EXPIRY = datetime(2026, 6, 17, 22, 36, tzinfo=timezone.utc)


def test_azure_generate_download_url_builds_user_delegation_command() -> None:
    captured: dict[str, list[str]] = {}

    def fake_runner(command: list[str]) -> str:
        captured["command"] = command
        return (
            "https://acct.blob.core.windows.net/podcaster-artifacts/review/x.mp3"
            "?se=2026-06-17T22%3A36%3A00Z&sp=r&spr=https&sv=2026-04-06&sr=b&sig=abc\n"
        )

    backend = AzureBlobStorageBackend(
        "https://squadscopepo3f9a07d60de7.blob.core.windows.net",
        "podcaster-artifacts",
        sas_command_runner=fake_runner,
    )

    signed = backend.generate_download_url("review/x.mp3", expiry=EXPIRY)

    assert signed.signed is True
    assert signed.account_key_used is False
    assert signed.https_only is True
    assert signed.method == DOWNLOAD_METHOD_USER_DELEGATION_SAS
    assert signed.expires_at == "2026-06-17T22:36:00Z"
    assert signed.url.startswith("https://")

    command = captured["command"]
    assert command[0:4] == ["az", "storage", "blob", "generate-sas"]
    # Managed-identity / user-delegation flags, never an account key.
    assert "--auth-mode" in command and "login" in command
    assert "--as-user" in command
    assert "--https-only" in command
    assert "--full-uri" in command
    assert command[command.index("--account-name") + 1] == "squadscopepo3f9a07d60de7"
    assert command[command.index("-c") + 1] == "podcaster-artifacts"
    assert command[command.index("-n") + 1] == "review/x.mp3"
    assert command[command.index("--permissions") + 1] == "r"
    assert command[command.index("--expiry") + 1] == "2026-06-17T22:36:00Z"
    assert "--account-key" not in command


def test_azure_generate_download_url_rejects_non_https_output() -> None:
    backend = AzureBlobStorageBackend(
        "https://acct.blob.core.windows.net",
        "podcaster-artifacts",
        sas_command_runner=lambda command: "not-a-url",
    )
    with pytest.raises(RuntimeError, match="no https URL"):
        backend.generate_download_url("review/x.mp3", expiry=EXPIRY)


def test_azure_generate_download_url_normalizes_unsafe_path() -> None:
    seen: dict[str, str] = {}

    def fake_runner(command: list[str]) -> str:
        seen["blob"] = command[command.index("-n") + 1]
        return "https://acct.blob.core.windows.net/c/review/x.mp3?sig=abc"

    backend = AzureBlobStorageBackend(
        "https://acct.blob.core.windows.net",
        "podcaster-artifacts",
        sas_command_runner=fake_runner,
    )
    backend.generate_download_url("../review/./x.mp3", expiry=EXPIRY)
    assert seen["blob"] == "review/x.mp3"


def test_local_generate_download_url_is_unsigned_locator(tmp_path: Path) -> None:
    backend = LocalStorageBackend(tmp_path, "https://example.invalid/artifacts")
    signed = backend.generate_download_url("review/x.mp3", expiry=EXPIRY)

    assert signed.signed is False
    assert signed.account_key_used is False
    assert signed.method == DOWNLOAD_METHOD_LOCAL_LOCATOR
    assert signed.url == "https://example.invalid/artifacts/review/x.mp3"
    assert "?" not in signed.url


def test_create_storage_backend_returns_sas_capable_azure(monkeypatch) -> None:
    monkeypatch.setenv("PODCASTER_STORAGE_ACCOUNT_URL", "https://acct.blob.core.windows.net")
    monkeypatch.setenv("PODCASTER_STORAGE_CONTAINER", "podcaster-artifacts")
    backend = create_storage_backend()
    assert isinstance(backend, AzureBlobStorageBackend)
    assert hasattr(backend, "generate_download_url")


def test_sas_download_record_omits_secret_url_when_requested() -> None:
    signed = SignedDownloadUrl(
        path="review/x.mp3",
        url="https://acct.blob.core.windows.net/c/review/x.mp3?sig=secret",
        expires_at="2026-06-17T22:36:00Z",
        method=DOWNLOAD_METHOD_USER_DELEGATION_SAS,
        signed=True,
        https_only=True,
    )

    with_url = sas_download_record(signed, include_url=True)
    without_url = sas_download_record(signed, include_url=False)

    assert with_url["url"] == signed.url
    assert with_url["is_secret"] is True
    assert with_url["account_key_used"] is False
    assert "url" not in without_url
    assert without_url["is_secret"] is True


def test_sas_download_record_never_includes_url_for_unsigned_locator() -> None:
    signed = SignedDownloadUrl(
        path="review/x.mp3",
        url="https://example.invalid/artifacts/review/x.mp3",
        expires_at="2026-06-17T22:36:00Z",
        method=DOWNLOAD_METHOD_LOCAL_LOCATOR,
        signed=False,
        https_only=True,
    )
    record = sas_download_record(signed, include_url=True)
    assert "url" not in record
    assert record["is_secret"] is False


def test_operator_download_access_metadata_keeps_publication_gated() -> None:
    meta = operator_download_access_metadata("2026-06-10T22:36:00Z", "2026-06-17T22:36:00Z")
    assert meta["response_urls"]["signed_urls"] is True
    assert meta["response_urls"]["account_key_used"] is False
    assert meta["response_urls"]["publicly_accessible"] is False
    assert meta["publication"]["eligible"] is False
    assert "human_review" in meta["publication"]["blocked_by"]


class _RecordingStorage:
    """In-memory storage backend that records uploads and mints fake SAS URLs."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        self.objects[path] = content
        return StoredArtifact(path=path, url=f"https://acct/c/{path}", size_bytes=len(content), content_type=content_type)

    def get_bytes(self, path: str) -> bytes | None:  # pragma: no cover - unused
        return self.objects.get(path)

    def update_bytes(self, path, content_type, update):  # pragma: no cover - unused
        raise NotImplementedError

    def generate_download_url(self, path: str, *, expiry) -> SignedDownloadUrl:
        return SignedDownloadUrl(
            path=path,
            url=f"https://acct.blob.core.windows.net/podcaster-artifacts/{path}?sig=SECRET-{path}",
            expires_at="2026-06-17T22:36:00Z",
            method=DOWNLOAD_METHOD_USER_DELEGATION_SAS,
            signed=True,
            https_only=True,
        )


def test_stage_review_upload_uploads_and_keeps_storage_manifest_sas_free() -> None:
    storage = _RecordingStorage()
    base_manifest = {
        "week": "2026-W24",
        "expires_at": "2026-06-17T22:36:00Z",
        "audio": {"sha256": "deadbeef"},
    }

    local_manifest, storage_manifest = stage_review_upload(
        storage,
        prefix="review",
        week="2026-W24",
        mp3_bytes=b"ID3-audio-bytes",
        script_text="Host A (fable): hello",
        base_manifest=base_manifest,
        generated_at="2026-06-10T22:36:00Z",
        expiry=EXPIRY,
    )

    # All three artifacts were uploaded.
    assert "review/claracle-2026-W24.mp3" in storage.objects
    assert "review/claracle-2026-W24-script.txt" in storage.objects
    assert "review/claracle-2026-W24-review-manifest.json" in storage.objects

    # The stored manifest must not contain any SAS secret.
    stored_manifest_bytes = storage.objects["review/claracle-2026-W24-review-manifest.json"]
    assert b"SECRET-" not in stored_manifest_bytes
    assert b"sig=" not in stored_manifest_bytes
    stored_doc = json.loads(stored_manifest_bytes)
    for record in stored_doc["download"]["urls"].values():
        assert "url" not in record
        assert record["account_key_used"] is False
    assert stored_doc["artifact_storage"]["account_key_used"] is False

    # The local manifest carries the operator's signed URLs.
    local_urls = local_manifest["download"]["urls"]
    assert local_urls["audio_mp3"]["url"].endswith("?sig=SECRET-review/claracle-2026-W24.mp3")
    assert local_urls["script_txt"]["url"].startswith("https://")
    assert local_urls["review_manifest"]["url"].startswith("https://")
    assert storage_manifest["download"]["method"] == DOWNLOAD_METHOD_USER_DELEGATION_SAS
