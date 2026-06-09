from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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
        self.base_url = base_url.rstrip("/")

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        safe_path = _safe_blob_path(path)
        target = self.root / safe_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return StoredArtifact(path=safe_path, url=f"{self.base_url}/{safe_path}", size_bytes=len(content), content_type=content_type)


class AzureBlobStorageBackend:
    def __init__(self, account_url: str, container_name: str) -> None:
        try:
            from azure.core.credentials import AccessToken
            from azure.storage.blob import BlobServiceClient, ContentSettings
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise RuntimeError("Azure storage dependencies are not installed") from exc

        self._content_settings_type = ContentSettings
        credential = ManagedIdentityTokenCredential(access_token_type=AccessToken)
        self._container = BlobServiceClient(account_url=account_url, credential=credential).get_container_client(container_name)
        self._container_name = container_name
        self._account_url = account_url.rstrip("/")

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


class ManagedIdentityTokenCredential:
    def __init__(self, access_token_type: type) -> None:
        self._access_token_type = access_token_type

    def get_token(self, *scopes: str, **_: object) -> object:
        resource = _managed_identity_resource(scopes[0] if scopes else "https://storage.azure.com/.default")
        token_payload = _request_managed_identity_token(resource)
        token = token_payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("managed identity token response did not include an access token")
        return self._access_token_type(token, _token_expires_on(token_payload))


def _managed_identity_resource(scope: str) -> str:
    return scope.removesuffix("/.default")


def _request_managed_identity_token(resource: str) -> dict[str, object]:
    app_service_endpoint = os.environ.get("IDENTITY_ENDPOINT")
    app_service_header = os.environ.get("IDENTITY_HEADER")
    if app_service_endpoint and app_service_header:
        query = urlencode({"api-version": "2019-08-01", "resource": resource})
        separator = "&" if "?" in app_service_endpoint else "?"
        request = Request(f"{app_service_endpoint}{separator}{query}", headers={"X-IDENTITY-HEADER": app_service_header})
    else:
        query = urlencode({"api-version": "2018-02-01", "resource": resource})
        request = Request(
            f"http://169.254.169.254/metadata/identity/oauth2/token?{query}",
            headers={"Metadata": "true"},
        )

    import json

    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("managed identity token response was not a JSON object")
    return payload


def _token_expires_on(payload: dict[str, object]) -> int:
    expires_on = payload.get("expires_on")
    if isinstance(expires_on, int):
        return expires_on
    if isinstance(expires_on, str) and expires_on.isdigit():
        return int(expires_on)
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, int):
        return int(time.time()) + expires_in
    if isinstance(expires_in, str) and expires_in.isdigit():
        return int(time.time()) + int(expires_in)
    raise RuntimeError("managed identity token response did not include an expiry")


def _safe_blob_path(path: str) -> str:
    parts = [part for part in path.replace("\\", "/").split("/") if part not in {"", ".", ".."}]
    if not parts:
        raise ValueError("artifact path must not be empty")
    return "/".join(parts)
