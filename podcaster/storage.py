from __future__ import annotations

import os
import time
from dataclasses import dataclass
from email.utils import formatdate
from pathlib import Path
from typing import Callable, Protocol
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
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

    def get_bytes(self, path: str) -> bytes | None:
        ...

    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> StoredArtifact:
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

    def get_bytes(self, path: str) -> bytes | None:
        safe_path = _safe_blob_path(path)
        target = self.root / safe_path
        if not target.exists():
            return None
        return target.read_bytes()

    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> StoredArtifact:
        import fcntl

        safe_path = _safe_blob_path(path)
        target = self.root / safe_path
        lock_path = self.root / ".locks" / f"{safe_path.replace('/', '__')}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            current = target.read_bytes() if target.exists() else None
            updated = update(current)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(updated)
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        return StoredArtifact(path=safe_path, url=f"{self.base_url}/{safe_path}", size_bytes=len(updated), content_type=content_type)


class AzureBlobStorageBackend:
    def __init__(self, account_url: str, container_name: str) -> None:
        self._credential = ManagedIdentityTokenCredential()
        self._container_name = container_name
        self._account_url = normalize_artifact_base_url(account_url)

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        safe_path = _safe_blob_path(path)
        self._put_blob(safe_path, content, content_type)
        return StoredArtifact(
            path=safe_path,
            url=f"{self._account_url}/{self._container_name}/{safe_path}",
            size_bytes=len(content),
            content_type=content_type,
        )

    def _put_blob(
        self,
        path: str,
        content: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> None:
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        token = self._credential.get_token("https://storage.azure.com/.default")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Length": str(len(content)),
            "Content-Type": content_type,
            "x-ms-blob-type": "BlockBlob",
            "x-ms-date": formatdate(timeval=None, localtime=False, usegmt=True),
            "x-ms-version": "2023-11-03",
        }
        if if_match is not None:
            headers["If-Match"] = if_match
        if if_none_match is not None:
            headers["If-None-Match"] = if_none_match
        request = Request(
            f"{self._account_url}/{self._container_name}/{encoded_path}",
            data=content,
            method="PUT",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=30):
                return
        except HTTPError as exc:
            if exc.code == 412:
                raise
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"blob upload failed for {path}: HTTP {exc.code} {detail}") from exc

    def get_bytes(self, path: str) -> bytes | None:
        safe_path = _safe_blob_path(path)
        content, _etag = self._get_blob_state(safe_path)
        return content

    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> StoredArtifact:
        safe_path = _safe_blob_path(path)
        for _attempt in range(5):
            content, etag = self._get_blob_state(safe_path)
            updated = update(content)
            try:
                self._put_blob(
                    safe_path,
                    updated,
                    content_type,
                    if_match=etag,
                    if_none_match="*" if etag is None else None,
                )
                return StoredArtifact(
                    path=safe_path,
                    url=f"{self._account_url}/{self._container_name}/{safe_path}",
                    size_bytes=len(updated),
                    content_type=content_type,
                )
            except HTTPError as exc:
                if exc.code == 412:
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"conditional blob update failed for {safe_path}: HTTP {exc.code} {detail}") from exc
        raise RuntimeError(f"conditional blob update failed for {safe_path}: concurrent updates did not settle")

    def _get_blob_state(self, safe_path: str) -> tuple[bytes | None, str | None]:
        encoded_path = "/".join(quote(part, safe="") for part in safe_path.split("/"))
        token = self._credential.get_token("https://storage.azure.com/.default")
        request = Request(
            f"{self._account_url}/{self._container_name}/{encoded_path}",
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "x-ms-date": formatdate(timeval=None, localtime=False, usegmt=True),
                "x-ms-version": "2023-11-03",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                return response.read(), response.headers.get("ETag")
        except HTTPError as exc:
            if exc.code == 404:
                return None, None
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"blob read failed for {safe_path}: HTTP {exc.code} {detail}") from exc


def create_storage_backend() -> StorageBackend:
    account_url = os.environ.get("PODCASTER_STORAGE_ACCOUNT_URL")
    container = os.environ.get("PODCASTER_STORAGE_CONTAINER", "podcaster-artifacts")
    if account_url:
        return AzureBlobStorageBackend(account_url=account_url, container_name=container)

    root = Path(os.environ.get("PODCASTER_LOCAL_STORAGE_PATH", ".podcaster-artifacts"))
    base_url = os.environ.get("PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/podcaster-stub")
    return LocalStorageBackend(root=root, base_url=base_url)


class ManagedIdentityTokenCredential:
    def get_token(self, *scopes: str) -> str:
        resource = _managed_identity_resource(scopes[0] if scopes else "https://storage.azure.com/.default")
        token_payload = _request_managed_identity_token(resource)
        token = token_payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("managed identity token response did not include an access token")
        _token_expires_on(token_payload)
        return token


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


def normalize_artifact_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("artifact base URL must be an http or https URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("artifact base URL must not contain credentials, query strings, or fragments")
    return base_url.rstrip("/")
