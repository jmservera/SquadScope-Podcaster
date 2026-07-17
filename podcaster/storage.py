from __future__ import annotations

import logging
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

# Method labels recorded in manifests so reviewers can tell signed,
# time-limited download URLs apart from non-signed development locators.
DOWNLOAD_METHOD_USER_DELEGATION_SAS = "azure_ad_user_delegation_sas"
DOWNLOAD_METHOD_LOCAL_LOCATOR = "local_filesystem_locator"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredArtifact:
    path: str
    url: str
    size_bytes: int
    content_type: str


@dataclass(frozen=True)
class SignedDownloadUrl:
    """A time-limited download URL for one stored artifact.

    ``signed`` is true only when ``url`` carries a real, time-limited
    credential (an Azure AD *user-delegation* SAS — never an account key). For
    the local development backend the URL is an unsigned filesystem locator and
    ``signed`` is false. ``url`` is secret when ``signed`` is true and must
    never be logged or committed.
    """

    path: str
    url: str
    expires_at: str
    method: str
    signed: bool
    https_only: bool
    account_key_used: bool = False


class StorageBackend(Protocol):
    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact: ...

    def get_bytes(self, path: str) -> bytes | None: ...

    def update_bytes(
        self,
        path: str,
        content_type: str,
        update: Callable[[bytes | None], bytes],
    ) -> StoredArtifact: ...

    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]: ...

    def generate_download_url(self, path: str, *, expiry: datetime) -> SignedDownloadUrl: ...

    def blob_exists(self, path: str) -> bool: ...

    def blob_size(self, path: str) -> int | None: ...

    def upload_file(self, path: str, source: Path, content_type: str) -> StoredArtifact: ...

    def download_file(self, path: str, dest: Path) -> bool: ...

    def delete_blob(self, path: str) -> bool: ...

    def delete_prefix(self, prefix: str) -> int: ...


class LocalStorageBackend:
    def __init__(self, root: Path, base_url: str) -> None:
        self.root = root
        self.base_url = normalize_artifact_base_url(base_url)

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        safe_path = _safe_blob_path(path)
        target = self.root / safe_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return StoredArtifact(
            path=safe_path,
            url=f"{self.base_url}/{safe_path}",
            size_bytes=len(content),
            content_type=content_type,
        )

    def get_bytes(self, path: str) -> bytes | None:
        safe_path = _safe_blob_path(path)
        target = self.root / safe_path
        if not target.exists():
            return None
        return target.read_bytes()

    def update_bytes(
        self,
        path: str,
        content_type: str,
        update: Callable[[bytes | None], bytes],
    ) -> StoredArtifact:
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
        return StoredArtifact(
            path=safe_path,
            url=f"{self.base_url}/{safe_path}",
            size_bytes=len(updated),
            content_type=content_type,
        )

    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        safe_prefix = _safe_blob_prefix(prefix)
        if limit <= 0 or not self.root.exists():
            return []
        matches: list[str] = []
        for target in sorted(path for path in self.root.rglob("*") if path.is_file()):
            relative = target.relative_to(self.root).as_posix()
            if relative.startswith(safe_prefix):
                matches.append(relative)
                if len(matches) >= limit:
                    break
        return matches

    def generate_download_url(self, path: str, *, expiry: datetime) -> SignedDownloadUrl:
        # Local development has no SAS service; the locator is unsigned and
        # only meaningful to an operator with filesystem access.
        safe_path = _safe_blob_path(path)
        return SignedDownloadUrl(
            path=safe_path,
            url=f"{self.base_url}/{safe_path}",
            expires_at=_format_sas_expiry(expiry),
            method=DOWNLOAD_METHOD_LOCAL_LOCATOR,
            signed=False,
            https_only=self.base_url.lower().startswith("https://"),
            account_key_used=False,
        )

    def blob_exists(self, path: str) -> bool:
        return (self.root / _safe_blob_path(path)).exists()

    def blob_size(self, path: str) -> int | None:
        target = self.root / _safe_blob_path(path)
        if not target.exists():
            return None
        return target.stat().st_size

    def upload_file(self, path: str, source: Path, content_type: str) -> StoredArtifact:
        import os
        import shutil

        safe_path = _safe_blob_path(path)
        target = self.root / safe_path
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling .tmp file then atomically promote it into place so a
        # crash mid-copy never leaves a partial blob that resume would mistake
        # for a complete checkpoint (issue #410 upload safety).
        tmp_target = target.with_name(target.name + ".tmp")
        shutil.copyfile(source, tmp_target)
        os.replace(tmp_target, target)
        return StoredArtifact(
            path=safe_path,
            url=f"{self.base_url}/{safe_path}",
            size_bytes=target.stat().st_size,
            content_type=content_type,
        )

    def download_file(self, path: str, dest: Path) -> bool:
        import shutil

        target = self.root / _safe_blob_path(path)
        if not target.exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, dest)
        return True

    def delete_blob(self, path: str) -> bool:
        target = self.root / _safe_blob_path(path)
        if not target.exists():
            return False
        target.unlink()
        return True

    def delete_prefix(self, prefix: str) -> int:
        safe_prefix = _safe_blob_prefix(prefix)
        if not self.root.exists():
            return 0
        deleted = 0
        for target in list(self.root.rglob("*")):
            if not target.is_file():
                continue
            relative = target.relative_to(self.root).as_posix()
            if relative == safe_prefix or relative.startswith(safe_prefix.rstrip("/") + "/"):
                target.unlink()
                deleted += 1
        directories = sorted(
            (path for path in self.root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            relative = directory.relative_to(self.root).as_posix()
            if relative != safe_prefix and not relative.startswith(safe_prefix.rstrip("/") + "/"):
                continue
            try:
                directory.rmdir()
            except OSError:
                pass
        return deleted


class AzureBlobStorageBackend:
    def __init__(
        self,
        account_url: str,
        container_name: str,
        *,
        sas_command_runner: Callable[[list[str]], str] | None = None,
    ) -> None:
        self._credential = ManagedIdentityTokenCredential()
        self._sdk_credential: "_SdkBlobCredential | None" = None
        self._container_name = container_name
        self._account_url = normalize_artifact_base_url(account_url)
        self._account_name = _account_name_from_url(self._account_url)
        self._sas_command_runner = sas_command_runner or _az_sas_command_runner

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

    def update_bytes(
        self,
        path: str,
        content_type: str,
        update: Callable[[bytes | None], bytes],
    ) -> StoredArtifact:
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
                raise RuntimeError(
                    f"conditional blob update failed for {safe_path}: HTTP {exc.code} {detail}"
                ) from exc
        raise RuntimeError(
            f"conditional blob update failed for {safe_path}: concurrent updates did not settle"
        )

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
            raise RuntimeError(
                f"blob read failed for {safe_path}: HTTP {exc.code} {detail}"
            ) from exc

    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        safe_prefix = _safe_blob_prefix(prefix)
        if limit <= 0:
            return []
        token = self._credential.get_token("https://storage.azure.com/.default")
        query = urlencode(
            {
                "restype": "container",
                "comp": "list",
                "prefix": safe_prefix,
                "maxresults": str(limit),
            }
        )
        request = Request(
            f"{self._account_url}/{self._container_name}?{query}",
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "x-ms-date": formatdate(timeval=None, localtime=False, usegmt=True),
                "x-ms-version": "2023-11-03",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                root = ET.fromstring(response.read())
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"blob list failed for {safe_prefix}: HTTP {exc.code} {detail}"
            ) from exc

        names: list[str] = []
        for blob in root.iter():
            if not blob.tag.endswith("Blob"):
                continue
            for child in blob:
                if child.tag.endswith("Name") and child.text:
                    names.append(child.text)
                    break
        return names[:limit]

    def generate_download_url(self, path: str, *, expiry: datetime) -> SignedDownloadUrl:
        """Mint a read-only, time-limited *user-delegation* SAS download URL.

        Uses the Azure CLI in managed-identity mode (``--auth-mode login
        --as-user``) so the SAS is signed by an Azure AD user-delegation key —
        **never** a storage account key. The returned ``url`` is a short-lived
        secret: do not log or persist it in committed files.
        """

        safe_path = _safe_blob_path(path)
        expiry_str = _format_sas_expiry(expiry)
        command = [
            "az",
            "storage",
            "blob",
            "generate-sas",
            "--account-name",
            self._account_name,
            "--auth-mode",
            "login",
            "--as-user",
            "-c",
            self._container_name,
            "-n",
            safe_path,
            "--permissions",
            "r",
            "--expiry",
            expiry_str,
            "--https-only",
            "--full-uri",
            "-o",
            "tsv",
        ]
        url = self._sas_command_runner(command).strip()
        if not url or not url.lower().startswith("https://"):
            raise RuntimeError(
                f"user-delegation SAS generation returned no https URL for {safe_path}"
            )
        return SignedDownloadUrl(
            path=safe_path,
            url=url,
            expires_at=expiry_str,
            method=DOWNLOAD_METHOD_USER_DELEGATION_SAS,
            signed=True,
            https_only=True,
            account_key_used=False,
        )

    def blob_exists(self, path: str) -> bool:
        safe_path = _safe_blob_path(path)
        encoded_path = "/".join(quote(part, safe="") for part in safe_path.split("/"))
        token = self._credential.get_token("https://storage.azure.com/.default")
        request = Request(
            f"{self._account_url}/{self._container_name}/{encoded_path}",
            method="HEAD",
            headers={
                "Authorization": f"Bearer {token}",
                "x-ms-date": formatdate(timeval=None, localtime=False, usegmt=True),
                "x-ms-version": "2023-11-03",
            },
        )
        try:
            with urlopen(request, timeout=30):
                return True
        except HTTPError as exc:
            if exc.code == 404:
                return False
            detail = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
            raise RuntimeError(
                f"blob existence check failed for {safe_path}: HTTP {exc.code} {detail}"
            ) from exc

    def blob_size(self, path: str) -> int | None:
        """Return the blob's Content-Length, or None when it does not exist.

        Used to verify a streamed upload landed intact (blob size == local file
        size) before the local copy is deleted (issue #410 upload safety).
        """
        safe_path = _safe_blob_path(path)
        encoded_path = "/".join(quote(part, safe="") for part in safe_path.split("/"))
        token = self._credential.get_token("https://storage.azure.com/.default")
        request = Request(
            f"{self._account_url}/{self._container_name}/{encoded_path}",
            method="HEAD",
            headers={
                "Authorization": f"Bearer {token}",
                "x-ms-date": formatdate(timeval=None, localtime=False, usegmt=True),
                "x-ms-version": "2023-11-03",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                length = response.headers.get("Content-Length")
                return int(length) if length is not None else None
        except HTTPError as exc:
            if exc.code == 404:
                return None
            detail = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
            raise RuntimeError(
                f"blob size probe failed for {safe_path}: HTTP {exc.code} {detail}"
            ) from exc

    def _sdk_blob_client(self, safe_path: str):
        """Build a streaming azure-storage-blob ``BlobClient`` for ``safe_path``.

        The SDK uploads/downloads in chunks (true streaming) instead of buffering
        the whole multi-GB intermediate in memory like the urllib data=handle path
        did.  Auth reuses the identity-only managed-identity token flow via
        :class:`_SdkBlobCredential` — never an account key.
        """
        from azure.storage.blob import BlobClient

        if self._sdk_credential is None:
            self._sdk_credential = _SdkBlobCredential()
        # Cap block/single-put size so large blobs are chunked (streamed) and a
        # bounded amount of memory is used per concurrent block.
        chunk = 8 * 1024 * 1024
        return BlobClient(
            account_url=self._account_url,
            container_name=self._container_name,
            blob_name=safe_path,
            credential=self._sdk_credential,
            max_block_size=chunk,
            max_single_put_size=chunk,
        )

    def upload_file(self, path: str, source: Path, content_type: str) -> StoredArtifact:
        # Stream the file straight off disk through the azure-storage-blob SDK so
        # the whole blob is never held in memory — intermediates can be multi-GB
        # videos.  ``max_concurrency=2`` uploads two blocks in parallel for
        # throughput while keeping the in-flight memory bounded (issue #410).
        from azure.storage.blob import ContentSettings

        safe_path = _safe_blob_path(path)
        size = source.stat().st_size
        client = self._sdk_blob_client(safe_path)
        try:
            with source.open("rb") as handle:
                client.upload_blob(
                    handle,
                    overwrite=True,
                    length=size,
                    max_concurrency=2,
                    content_settings=ContentSettings(content_type=content_type),
                )
        except Exception as exc:  # noqa: BLE001 — surface a clear upload failure
            raise RuntimeError(f"blob file upload failed for {safe_path}: {exc}") from exc
        return StoredArtifact(
            path=safe_path,
            url=f"{self._account_url}/{self._container_name}/{safe_path}",
            size_bytes=size,
            content_type=content_type,
        )

    def download_file(self, path: str, dest: Path) -> bool:
        # Stream the blob to disk via the SDK's StorageStreamDownloader
        # (``readinto``) so the file is written in chunks and never fully
        # materialised in memory (issue #410).  Stream into a sibling .part file
        # and atomically promote it only after the transfer completes, so a
        # mid-download failure never leaves a partial file that resume would
        # mistake for a complete checkpoint.
        import os

        from azure.core.exceptions import ResourceNotFoundError

        safe_path = _safe_blob_path(path)
        client = self._sdk_blob_client(safe_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_dest = dest.with_name(dest.name + ".part")
        try:
            downloader = client.download_blob(max_concurrency=2)
            with tmp_dest.open("wb") as handle:
                downloader.readinto(handle)
        except ResourceNotFoundError:
            tmp_dest.unlink(missing_ok=True)
            return False
        except Exception as exc:  # noqa: BLE001 — surface a clear download failure
            tmp_dest.unlink(missing_ok=True)
            raise RuntimeError(f"blob file download failed for {safe_path}: {exc}") from exc
        os.replace(tmp_dest, dest)
        return True

    def delete_blob(self, path: str) -> bool:
        safe_path = _safe_blob_path(path)
        encoded_path = "/".join(quote(part, safe="") for part in safe_path.split("/"))
        token = self._credential.get_token("https://storage.azure.com/.default")
        request = Request(
            f"{self._account_url}/{self._container_name}/{encoded_path}",
            method="DELETE",
            headers={
                "Authorization": f"Bearer {token}",
                "x-ms-date": formatdate(timeval=None, localtime=False, usegmt=True),
                "x-ms-version": "2023-11-03",
            },
        )
        try:
            with urlopen(request, timeout=30):
                return True
        except HTTPError as exc:
            if exc.code in (404, 202):
                return exc.code == 202
            detail = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
            raise RuntimeError(
                f"blob delete failed for {safe_path}: HTTP {exc.code} {detail}"
            ) from exc

    def delete_prefix(self, prefix: str) -> int:
        safe_prefix = _safe_blob_prefix(prefix)
        deleted = 0
        # list_blobs caps at maxresults; page until the prefix is exhausted so
        # cleanup removes every intermediate, not just the first page.
        while True:
            names = self.list_blobs(safe_prefix, limit=5000)
            if not names:
                break
            for name in names:
                if self.delete_blob(name):
                    deleted += 1
            if len(names) < 5000:
                break
        return deleted


class ConnectionStringStorageBackend:
    """Connection-string Blob backend for local/test only (Azurite); NOT prod.

    Production is identity-only (managed identity / IMDS); Azurite does not speak
    Azure AD, so this thin path is gated on ``AZURE_STORAGE_CONNECTION_STRING``
    exactly as ``docker-compose.test.yml`` documents. It covers the full
    :class:`StorageBackend` protocol surface using ``azure-storage-blob``.
    """

    def __init__(self, connection_string: str, container_name: str) -> None:
        from azure.storage.blob import ContainerClient

        self._container_name = container_name
        self._container = ContainerClient.from_connection_string(connection_string, container_name)
        self._ensure_container()

    def _ensure_container(self) -> None:
        from azure.core.exceptions import ResourceExistsError

        try:
            self._container.create_container()
        except ResourceExistsError:
            pass

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        from azure.storage.blob import ContentSettings

        safe_path = _safe_blob_path(path)
        self._container.upload_blob(
            safe_path,
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        return StoredArtifact(
            path=safe_path,
            url=f"{self._container.url}/{safe_path}",
            size_bytes=len(content),
            content_type=content_type,
        )

    def get_bytes(self, path: str) -> bytes | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._container.download_blob(_safe_blob_path(path)).readall()
        except ResourceNotFoundError:
            return None

    def update_bytes(
        self,
        path: str,
        content_type: str,
        update: Callable[[bytes | None], bytes],
    ) -> StoredArtifact:
        from azure.core import MatchConditions
        from azure.core.exceptions import (
            ResourceExistsError,
            ResourceModifiedError,
            ResourceNotFoundError,
        )
        from azure.storage.blob import ContentSettings

        safe_path = _safe_blob_path(path)
        blob = self._container.get_blob_client(safe_path)
        settings = ContentSettings(content_type=content_type)
        for _attempt in range(5):
            try:
                downloader = blob.download_blob()
                current: bytes | None = downloader.readall()
                etag = downloader.properties.etag
            except ResourceNotFoundError:
                current, etag = None, None
            updated = update(current)
            try:
                if etag is None:
                    # Create-if-absent: overwrite=False enforces If-None-Match=*.
                    blob.upload_blob(
                        updated,
                        overwrite=False,
                        content_settings=settings,
                    )
                else:
                    blob.upload_blob(
                        updated,
                        overwrite=True,
                        content_settings=settings,
                        etag=etag,
                        match_condition=MatchConditions.IfNotModified,
                    )
                return StoredArtifact(
                    path=safe_path,
                    url=f"{self._container.url}/{safe_path}",
                    size_bytes=len(updated),
                    content_type=content_type,
                )
            except (ResourceModifiedError, ResourceExistsError):
                continue
        raise RuntimeError(
            f"conditional blob update failed for {safe_path}: concurrent updates did not settle"
        )

    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        safe_prefix = _safe_blob_prefix(prefix)
        if limit <= 0:
            return []
        names: list[str] = []
        for name in self._container.list_blob_names(name_starts_with=safe_prefix):
            names.append(name)
            if len(names) >= limit:
                break
        return names

    def generate_download_url(self, path: str, *, expiry: datetime) -> SignedDownloadUrl:
        safe_path = _safe_blob_path(path)
        return SignedDownloadUrl(
            path=safe_path,
            url=f"{self._container.url}/{safe_path}",
            expires_at=_format_sas_expiry(expiry),
            method=DOWNLOAD_METHOD_LOCAL_LOCATOR,
            signed=False,
            https_only=self._container.url.lower().startswith("https://"),
            account_key_used=False,
        )

    def blob_exists(self, path: str) -> bool:
        return self._container.get_blob_client(_safe_blob_path(path)).exists()

    def blob_size(self, path: str) -> int | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._container.get_blob_client(_safe_blob_path(path)).get_blob_properties().size
        except ResourceNotFoundError:
            return None

    def upload_file(self, path: str, source: Path, content_type: str) -> StoredArtifact:
        from azure.storage.blob import ContentSettings

        safe_path = _safe_blob_path(path)
        size = source.stat().st_size
        with source.open("rb") as handle:
            self._container.upload_blob(
                safe_path,
                handle,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
        return StoredArtifact(
            path=safe_path,
            url=f"{self._container.url}/{safe_path}",
            size_bytes=size,
            content_type=content_type,
        )

    def download_file(self, path: str, dest: Path) -> bool:
        import os as _os

        from azure.core.exceptions import ResourceNotFoundError

        safe_path = _safe_blob_path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_dest = dest.with_name(dest.name + ".part")
        try:
            with tmp_dest.open("wb") as handle:
                self._container.download_blob(safe_path).readinto(handle)
        except ResourceNotFoundError:
            tmp_dest.unlink(missing_ok=True)
            return False
        _os.replace(tmp_dest, dest)
        return True

    def delete_blob(self, path: str) -> bool:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._container.delete_blob(_safe_blob_path(path))
            return True
        except ResourceNotFoundError:
            return False

    def delete_prefix(self, prefix: str) -> int:
        safe_prefix = _safe_blob_prefix(prefix)
        deleted = 0
        while True:
            names = self.list_blobs(safe_prefix, limit=5000)
            if not names:
                break
            for name in names:
                if self.delete_blob(name):
                    deleted += 1
            if len(names) < 5000:
                break
        return deleted


def _connection_string() -> str | None:
    value = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    return value or None


def create_storage_backend() -> StorageBackend:
    container = os.environ.get("PODCASTER_STORAGE_CONTAINER", "podcaster-artifacts")
    conn = _connection_string()
    if conn:
        return ConnectionStringStorageBackend(conn, container)

    account_url = os.environ.get("PODCASTER_STORAGE_ACCOUNT_URL")
    if account_url:
        return AzureBlobStorageBackend(account_url=account_url, container_name=container)

    root = Path(os.environ.get("PODCASTER_LOCAL_STORAGE_PATH", ".podcaster-artifacts"))
    base_url = os.environ.get(
        "PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/podcaster-stub"
    )
    return LocalStorageBackend(root=root, base_url=base_url)


def create_scratch_storage_backend() -> StorageBackend | None:
    """Build a storage backend for the video *scratch* container (issue #410).

    Intermediate video artifacts (segment recordings, normalized clips, composed
    video) are checkpointed here under ``video-jobs/{job-id}/intermediates/`` so
    the pipeline can resume after a crash and local disk only ever holds the file
    currently being processed.

    Returns ``None`` when no scratch container is configured (e.g. local dev or
    tests), in which case callers fall back to the legacy all-local-disk path.
    """
    account_url = os.environ.get("PODCASTER_STORAGE_ACCOUNT_URL")
    container = os.environ.get("PODCASTER_VIDEO_SCRATCH_CONTAINER", "").strip()
    if not container:
        return None
    conn = _connection_string()
    if conn:
        return ConnectionStringStorageBackend(conn, container)
    if account_url:
        return AzureBlobStorageBackend(account_url=account_url, container_name=container)

    root = Path(os.environ.get("PODCASTER_LOCAL_SCRATCH_PATH", ".podcaster-scratch"))
    base_url = os.environ.get(
        "PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/podcaster-scratch"
    )
    return LocalStorageBackend(root=root, base_url=base_url)


class ManagedIdentityTokenCredential:
    def get_token(self, *scopes: str) -> str:
        resource = _managed_identity_resource(
            scopes[0] if scopes else "https://storage.azure.com/.default"
        )
        token_payload = _request_managed_identity_token(resource)
        token = token_payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("managed identity token response did not include an access token")
        _token_expires_on(token_payload)
        return token


class _SdkBlobCredential:
    """Adapts the urllib managed-identity token flow to the azure-core
    ``TokenCredential`` protocol so ``azure-storage-blob`` can stream uploads and
    downloads using the same identity-only auth path (never an account key).

    The SDK calls ``get_token(*scopes, **kwargs)`` and expects an
    ``azure.core.credentials.AccessToken`` (token + epoch expiry), whereas the
    project's :class:`ManagedIdentityTokenCredential` returns a bare string, so
    this thin adapter bridges the two.
    """

    def get_token(self, *scopes: str, **kwargs):  # noqa: ANN003 - SDK passes extras
        from azure.core.credentials import AccessToken

        scope = scopes[0] if scopes else "https://storage.azure.com/.default"
        resource = _managed_identity_resource(scope)
        payload = _request_managed_identity_token(resource)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("managed identity token response did not include an access token")
        return AccessToken(token, _token_expires_on(payload))


def _managed_identity_resource(scope: str) -> str:
    return scope.removesuffix("/.default")


def _request_managed_identity_token(resource: str) -> dict[str, object]:
    app_service_endpoint = os.environ.get("IDENTITY_ENDPOINT")
    app_service_header = os.environ.get("IDENTITY_HEADER")
    client_id = os.environ.get("AZURE_CLIENT_ID")
    if app_service_endpoint and app_service_header:
        params: dict[str, str] = {"api-version": "2019-08-01", "resource": resource}
        if client_id:
            params["client_id"] = client_id
        query = urlencode(params)
        separator = "&" if "?" in app_service_endpoint else "?"
        request = Request(
            f"{app_service_endpoint}{separator}{query}",
            headers={"X-IDENTITY-HEADER": app_service_header},
        )
    else:
        params = {"api-version": "2018-02-01", "resource": resource}
        if client_id:
            params["client_id"] = client_id
        query = urlencode(params)
        request = Request(
            f"http://169.254.169.254/metadata/identity/oauth2/token?{query}",
            headers={"Metadata": "true"},
        )

    import json

    retryable_status_codes = {400, 429, 500, 502, 503, 504}
    backoff_delays = (1.0, 2.0, 4.0)
    max_attempts = 4

    for attempt in range(1, max_attempts + 1):
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in retryable_status_codes and attempt < max_attempts:
                delay = backoff_delays[attempt - 1]
                logger.warning(
                    "managed identity token request retrying after HTTP %s on attempt "
                    "%s/%s; sleeping %.0fs",
                    exc.code,
                    attempt,
                    max_attempts,
                    delay,
                )
                if exc.fp:
                    exc.close()
                time.sleep(delay)
                continue
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
            finally:
                if exc.fp:
                    exc.close()
            raise RuntimeError(
                f"managed identity token request failed: HTTP {exc.code} {exc.reason}; {detail}"
            ) from exc
        except URLError as exc:
            if attempt < max_attempts:
                delay = backoff_delays[attempt - 1]
                logger.warning(
                    "managed identity token request retrying after network error on attempt "
                    "%s/%s; sleeping %.0fs: %s",
                    attempt,
                    max_attempts,
                    delay,
                    exc.reason,
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"managed identity token request failed: {exc.reason}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("managed identity token response was not a JSON object")
        return payload

    raise RuntimeError("managed identity token request failed after retries")


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


def _safe_blob_prefix(prefix: str) -> str:
    parts = [part for part in prefix.replace("\\", "/").split("/") if part not in {"", ".", ".."}]
    if not parts:
        raise ValueError("artifact prefix must not be empty")
    return "/".join(parts)


def normalize_artifact_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("artifact base URL must be an http or https URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "artifact base URL must not contain credentials, query strings, or fragments"
        )
    return base_url.rstrip("/")


def _account_name_from_url(account_url: str) -> str:
    host = urlparse(account_url).netloc
    label = host.split(":", 1)[0].split(".", 1)[0]
    if not label:
        raise ValueError("could not derive storage account name from account URL")
    return label


def _format_sas_expiry(expiry: datetime) -> str:
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _az_sas_command_runner(command: list[str]) -> str:
    # The SAS value is returned on stdout and is a short-lived secret; it is
    # never logged here. stderr is surfaced only on failure for diagnostics.
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:500]
        raise RuntimeError(f"az SAS generation failed (exit {result.returncode}): {detail}")
    return result.stdout
