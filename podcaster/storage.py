from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


@dataclass(frozen=True)
class StoredArtifact:
    path: str
    url: str
    size_bytes: int
    content_type: str


class StorageBackend(Protocol):
    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        ...


class LocalStorageBackend:
    def __init__(self, root: Path, base_url: str) -> None:
        self.root = root
        self.base_url = normalize_artifact_base_url(base_url)

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        safe_path = _safe_blob_path(path)
        target = self.root / safe_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return StoredArtifact(path=safe_path, url=f"{self.base_url}/{safe_path}", size_bytes=len(content), content_type=content_type)


class AzureBlobStorageBackend:
    def __init__(self, account_url: str, container_name: str) -> None:
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient, ContentSettings
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise RuntimeError("Azure storage dependencies are not installed") from exc

        self._content_settings_type = ContentSettings
        credential = DefaultAzureCredential()
        self._container = BlobServiceClient(account_url=account_url, credential=credential).get_container_client(container_name)
        self._container_name = container_name
        self._account_url = normalize_artifact_base_url(account_url)

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        safe_path = _safe_blob_path(path)
        self._container.upload_blob(
            name=safe_path,
            data=content,
            overwrite=True,
            content_settings=self._content_settings_type(content_type=content_type),
        )
        return StoredArtifact(
            path=safe_path,
            url=f"{self._account_url}/{self._container_name}/{safe_path}",
            size_bytes=len(content),
            content_type=content_type,
        )


def create_storage_backend() -> StorageBackend:
    account_url = os.environ.get("PODCASTER_STORAGE_ACCOUNT_URL")
    container = os.environ.get("PODCASTER_STORAGE_CONTAINER", "podcaster-artifacts")
    if account_url:
        return AzureBlobStorageBackend(account_url=account_url, container_name=container)

    root = Path(os.environ.get("PODCASTER_LOCAL_STORAGE_PATH", ".podcaster-artifacts"))
    base_url = os.environ.get("PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/podcaster-stub")
    return LocalStorageBackend(root=root, base_url=base_url)


def _safe_blob_path(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part not in {"", ".", ".."}]
    if not parts:
        raise ValueError("artifact path must not be empty")
    return "/".join(parts)


def normalize_artifact_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("artifact base URL must be an http or https URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("artifact base URL must not contain credentials, query strings, or fragments")
    return base_url.rstrip("/")
