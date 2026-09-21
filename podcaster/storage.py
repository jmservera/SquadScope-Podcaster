from __future__ import annotations

import errno
import logging
import os
import stat
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

from werkzeug.utils import secure_filename

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


def bounded_delete_prefix(
    storage: StorageBackend,
    prefix: str,
    *,
    max_blobs: int = 1000,
) -> int:
    """Delete at most ``max_blobs`` under one already-scoped prefix."""

    if max_blobs <= 0:
        return 0
    names = storage.list_blobs(prefix, limit=max_blobs)
    return sum(1 for name in names if storage.delete_blob(name))


class LocalStorageBackend:
    def __init__(self, root: Path, base_url: str) -> None:
        self.root = root
        self.base_url = normalize_artifact_base_url(base_url)

    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        safe_path = _safe_blob_path(path)
        root_path = os.path.realpath(os.fspath(self.root))
        target_path = os.path.realpath(os.path.join(root_path, safe_path))
        root_prefix = root_path if root_path.endswith(os.sep) else f"{root_path}{os.sep}"
        if target_path.startswith(root_prefix):
            parent_fd, leaf_name = _open_local_parent_fd(self.root, safe_path, create=True)
            try:
                fd = _open_local_leaf(
                    parent_fd,
                    leaf_name,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                    0o644,
                )
                with os.fdopen(fd, "wb") as target_file:
                    target_file.write(content)
            finally:
                os.close(parent_fd)
        else:
            raise ValueError("artifact path escapes storage root")
        return StoredArtifact(
            path=safe_path,
            url=f"{self.base_url}/{safe_path}",
            size_bytes=len(content),
            content_type=content_type,
        )

    def get_bytes(self, path: str) -> bytes | None:
        safe_path = _safe_blob_path(path)
        root_path = os.path.realpath(os.fspath(self.root))
        target_path = os.path.realpath(os.path.join(root_path, safe_path))
        root_prefix = root_path if root_path.endswith(os.sep) else f"{root_path}{os.sep}"
        if target_path.startswith(root_prefix):
            parent_fd: int | None = None
            try:
                parent_fd, leaf_name = _open_local_parent_fd(self.root, safe_path, create=False)
                fd = _open_local_leaf(parent_fd, leaf_name, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as target_file:
                    return target_file.read()
            except FileNotFoundError:
                return None
            finally:
                if parent_fd is not None:
                    os.close(parent_fd)
        raise ValueError("artifact path escapes storage root")

    def update_bytes(
        self,
        path: str,
        content_type: str,
        update: Callable[[bytes | None], bytes],
    ) -> StoredArtifact:
        import fcntl

        safe_path = _safe_blob_path(path)
        root_path = os.path.realpath(os.fspath(self.root))
        root_prefix = root_path if root_path.endswith(os.sep) else f"{root_path}{os.sep}"
        target_path = os.path.realpath(os.path.join(root_path, safe_path))
        if not target_path.startswith(root_prefix):
            raise ValueError("artifact path escapes storage root")
        lock_blob_path = f"locks/{safe_path.replace('/', '__')}.lock"
        lock_path_text = os.path.realpath(os.path.join(root_path, lock_blob_path))
        if not lock_path_text.startswith(root_prefix):
            raise ValueError("artifact path escapes storage root")
        if target_path.startswith(root_prefix):
            if not lock_path_text.startswith(root_prefix):
                raise ValueError("artifact path escapes storage root")
            target_parent_fd: int | None = None
            lock_parent_fd: int | None = None
            try:
                lock_parent_fd, lock_name = _open_local_parent_fd(
                    self.root, lock_blob_path, create=True
                )
                lock_fd = _open_local_leaf(
                    lock_parent_fd,
                    lock_name,
                    os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
                    0o644,
                )
                with os.fdopen(lock_fd, "w", encoding="utf-8") as lock_file:
                    fcntl.flock(lock_file, fcntl.LOCK_EX)
                    target_parent_fd, target_name = _open_local_parent_fd(
                        self.root, safe_path, create=True
                    )
                    try:
                        current_fd = _open_local_leaf(
                            target_parent_fd, target_name, os.O_RDONLY | os.O_NOFOLLOW
                        )
                    except FileNotFoundError:
                        current = None
                    else:
                        with os.fdopen(current_fd, "rb") as current_file:
                            current = current_file.read()
                    updated = update(current)
                    write_fd = _open_local_leaf(
                        target_parent_fd,
                        target_name,
                        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                        0o644,
                    )
                    with os.fdopen(write_fd, "wb") as target_file:
                        target_file.write(updated)
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
            finally:
                if target_parent_fd is not None:
                    os.close(target_parent_fd)
                if lock_parent_fd is not None:
                    os.close(lock_parent_fd)
        else:
            raise ValueError("artifact path escapes storage root")
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
        root = _safe_local_root(self.root)
        matches: list[str] = []
        for target in sorted(path for path in root.rglob("*") if path.is_file()):
            relative = _relative_to_root(target, root).as_posix()
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
        return self.get_bytes(path) is not None

    def blob_size(self, path: str) -> int | None:
        safe_path = _safe_blob_path(path)
        root_path = os.path.realpath(os.fspath(self.root))
        target_path = os.path.realpath(os.path.join(root_path, safe_path))
        root_prefix = root_path if root_path.endswith(os.sep) else f"{root_path}{os.sep}"
        if target_path.startswith(root_prefix):
            parent_fd: int | None = None
            try:
                parent_fd, leaf_name = _open_local_parent_fd(self.root, safe_path, create=False)
                file_stat = _local_leaf_stat(parent_fd, leaf_name)
                if stat.S_ISLNK(file_stat.st_mode):
                    raise ValueError("artifact path escapes storage root")
                return file_stat.st_size
            except FileNotFoundError:
                return None
            finally:
                if parent_fd is not None:
                    os.close(parent_fd)
        raise ValueError("artifact path escapes storage root")

    def upload_file(self, path: str, source: Path, content_type: str) -> StoredArtifact:
        import os
        import secrets
        import shutil

        safe_path = _safe_blob_path(path)
        root_path = os.path.realpath(os.fspath(self.root))
        target_path = os.path.realpath(os.path.join(root_path, safe_path))
        root_prefix = root_path if root_path.endswith(os.sep) else f"{root_path}{os.sep}"
        if target_path.startswith(root_prefix):
            # Write to a sibling .tmp file then atomically promote it into place so a
            # crash mid-copy never leaves a partial blob that resume would mistake
            # for a complete checkpoint (issue #410 upload safety).
            target = Path(target_path)
            parent_fd: int | None = None
            tmp_name: str | None = None
            uploaded_size = 0
            try:
                parent_fd, target_name = _open_local_parent_fd(self.root, safe_path, create=True)
                _cleanup_stale_upload_temps(parent_fd, target.name)
                for _ in range(100):
                    candidate = f".{target.name}.{secrets.token_hex(8)}.tmp"
                    try:
                        tmp_fd = os.open(
                            candidate,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=parent_fd,
                        )
                    except FileExistsError:
                        continue
                    tmp_name = candidate
                    break
                else:
                    raise FileExistsError(
                        f"could not allocate temporary upload name for {target.name}"
                    )
                with os.fdopen(tmp_fd, "wb") as tmp_file, source.open("rb") as source_file:
                    shutil.copyfileobj(source_file, tmp_file)
                os.replace(tmp_name, target_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                uploaded_size = _local_leaf_stat(parent_fd, target_name).st_size
            except Exception:
                if parent_fd is not None and tmp_name is not None:
                    try:
                        os.unlink(tmp_name, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass
                raise
            finally:
                if parent_fd is not None:
                    os.close(parent_fd)
        else:
            raise ValueError("artifact path escapes storage root")
        return StoredArtifact(
            path=safe_path,
            url=f"{self.base_url}/{safe_path}",
            size_bytes=uploaded_size,
            content_type=content_type,
        )

    def download_file(self, path: str, dest: Path) -> bool:
        import shutil

        safe_path = _safe_blob_path(path)
        root_path = os.path.realpath(os.fspath(self.root))
        target_path = os.path.realpath(os.path.join(root_path, safe_path))
        root_prefix = root_path if root_path.endswith(os.sep) else f"{root_path}{os.sep}"
        if target_path.startswith(root_prefix):
            parent_fd: int | None = None
            source_fd: int | None = None
            try:
                parent_fd, leaf_name = _open_local_parent_fd(self.root, safe_path, create=False)
                source_fd = _open_local_leaf(parent_fd, leaf_name, os.O_RDONLY | os.O_NOFOLLOW)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with os.fdopen(source_fd, "rb") as source_file, dest.open("wb") as dest_file:
                    source_fd = None
                    shutil.copyfileobj(source_file, dest_file)
                return True
            except FileNotFoundError:
                return False
            finally:
                if source_fd is not None:
                    os.close(source_fd)
                if parent_fd is not None:
                    os.close(parent_fd)
        raise ValueError("artifact path escapes storage root")

    def delete_blob(self, path: str) -> bool:
        safe_path = _safe_blob_path(path)
        root_path = os.path.realpath(os.fspath(self.root))
        target_path = os.path.realpath(os.path.join(root_path, safe_path))
        root_prefix = root_path if root_path.endswith(os.sep) else f"{root_path}{os.sep}"
        if target_path.startswith(root_prefix):
            parent_fd: int | None = None
            try:
                parent_fd, leaf_name = _open_local_parent_fd(self.root, safe_path, create=False)
                file_stat = _local_leaf_stat(parent_fd, leaf_name)
                if stat.S_ISLNK(file_stat.st_mode):
                    raise ValueError("artifact path escapes storage root")
                os.unlink(leaf_name, dir_fd=parent_fd)
                return True
            except FileNotFoundError:
                return False
            finally:
                if parent_fd is not None:
                    os.close(parent_fd)
        raise ValueError("artifact path escapes storage root")

    def delete_prefix(self, prefix: str) -> int:
        safe_prefix = _safe_blob_prefix(prefix)
        if not self.root.exists():
            return 0
        match_dir = safe_prefix.rstrip("/") + "/"
        deleted = 0
        matched_dirs: list[Path] = []
        root = _safe_local_root(self.root)
        # Walk from the constant storage root and match by relative path so the
        # user-derived prefix is only ever used in a string comparison, never
        # flowed into a filesystem sink (path-injection safe). os.walk's
        # onerror and the per-file guards make cleanup resilient to concurrent
        # sibling deletions (e.g. parallel budget-blocked job cleanups), which
        # otherwise surfaced as FileNotFoundError.
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            current = Path(dirpath)
            for name in filenames:
                target = current / name
                relative = _relative_to_root(target, root).as_posix()
                if relative == safe_prefix or relative.startswith(match_dir):
                    try:
                        target.unlink()
                        deleted += 1
                    except FileNotFoundError:
                        continue
            for name in dirnames:
                directory = current / name
                relative = _relative_to_root(directory, root).as_posix()
                if relative == safe_prefix or relative.startswith(match_dir):
                    matched_dirs.append(directory)
        for directory in sorted(matched_dirs, key=lambda path: len(path.parts), reverse=True):
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
    normalized = _normalize_blob_reference(path, allow_trailing_slash=False)
    if normalized.endswith("/"):
        raise ValueError("artifact path must not be empty")
    return normalized


def _safe_blob_prefix(prefix: str) -> str:
    return _normalize_blob_reference(prefix, allow_trailing_slash=True)


def _normalize_blob_reference(value: str, *, allow_trailing_slash: bool) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("artifact path must not be empty")
    if "\\" in value or value.startswith("/"):
        raise ValueError("artifact path must be a relative POSIX path")
    parts = value.split("/")
    if parts and parts[-1] == "" and allow_trailing_slash:
        parts = parts[:-1]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("artifact path must not contain empty or traversal components")
    if any(secure_filename(part) != part for part in parts):
        raise ValueError("artifact path components must be safe filenames")
    first = parts[0]
    if ":" in first:
        raise ValueError("artifact path must be a relative POSIX path")
    return "/".join(parts)


def _safe_local_root(root: Path) -> Path:
    return Path(os.path.realpath(os.fspath(root)))


def _open_local_parent_fd(root: Path, safe_blob_path: str, *, create: bool) -> tuple[int, str]:
    root_path = os.path.realpath(os.fspath(root))
    if create:
        os.makedirs(root_path, exist_ok=True)
    current_fd = _open_local_directory(root_path)
    parts = safe_blob_path.split("/")
    try:
        for part in parts[:-1]:
            safe_part = _safe_local_component(part)
            if safe_part:
                current_path = os.path.realpath(f"/proc/self/fd/{current_fd}")
                child_path = os.path.realpath(os.path.join(current_path, safe_part))
                current_prefix = (
                    current_path if current_path.endswith(os.sep) else f"{current_path}{os.sep}"
                )
                if not child_path.startswith(current_prefix):
                    raise ValueError("artifact path escapes storage root")
                if create:
                    try:
                        os.mkdir(safe_part, 0o755, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                next_fd = _open_local_directory(safe_part, dir_fd=current_fd)
            else:
                raise ValueError("artifact path escapes storage root")
            os.close(current_fd)
            current_fd = next_fd
    except Exception:
        os.close(current_fd)
        raise
    return current_fd, parts[-1]


def _safe_local_component(component: str) -> str:
    safe_component = secure_filename(component)
    if safe_component == component and safe_component not in {"", ".", ".."}:
        return safe_component
    raise ValueError("artifact path escapes storage root")


def _open_local_directory(path: str | Path, *, dir_fd: int | None = None) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        if dir_fd is None:
            return os.open(path, flags)
        safe_path = _safe_local_component(path) if isinstance(path, str) else ""
        if safe_path:
            parent_path = os.path.realpath(f"/proc/self/fd/{dir_fd}")
            child_path = os.path.realpath(os.path.join(parent_path, safe_path))
            parent_prefix = (
                parent_path if parent_path.endswith(os.sep) else f"{parent_path}{os.sep}"
            )
            if child_path.startswith(parent_prefix):
                return os.open(safe_path, flags, dir_fd=dir_fd)
        raise ValueError("artifact path escapes storage root")
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("artifact path escapes storage root") from exc
        raise


def _open_local_leaf(
    parent_fd: int,
    leaf_name: str,
    flags: int,
    mode: int = 0o666,
) -> int:
    try:
        safe_leaf_name = _safe_local_component(leaf_name)
        if safe_leaf_name:
            parent_path = os.path.realpath(f"/proc/self/fd/{parent_fd}")
            child_path = os.path.realpath(os.path.join(parent_path, safe_leaf_name))
            parent_prefix = (
                parent_path if parent_path.endswith(os.sep) else f"{parent_path}{os.sep}"
            )
            if child_path.startswith(parent_prefix):
                return os.open(safe_leaf_name, flags, mode, dir_fd=parent_fd)
        raise ValueError("artifact path escapes storage root")
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("artifact path escapes storage root") from exc
        raise


def _local_leaf_stat(parent_fd: int, leaf_name: str) -> os.stat_result:
    try:
        safe_leaf_name = _safe_local_component(leaf_name)
        if safe_leaf_name:
            parent_path = os.path.realpath(f"/proc/self/fd/{parent_fd}")
            child_path = os.path.realpath(os.path.join(parent_path, safe_leaf_name))
            parent_prefix = (
                parent_path if parent_path.endswith(os.sep) else f"{parent_path}{os.sep}"
            )
            if child_path.startswith(parent_prefix):
                return os.stat(safe_leaf_name, dir_fd=parent_fd, follow_symlinks=False)
        raise ValueError("artifact path escapes storage root")
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("artifact path escapes storage root") from exc
        raise


def _cleanup_stale_upload_temps(parent_fd: int, target_name: str) -> None:
    prefix = f".{target_name}."
    now = time.time()
    try:
        names = os.listdir(parent_fd)
    except OSError:
        return
    for name in names:
        if not name.startswith(prefix) or not name.endswith(".tmp"):
            continue
        try:
            file_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(file_stat.st_mode):
                continue
            if now - file_stat.st_mtime < 24 * 60 * 60:
                continue
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            continue


def _safe_local_blob_path(root: Path, safe_blob_path: str) -> Path:
    root_path = os.path.realpath(os.fspath(_safe_local_root(root)))
    candidate = os.path.realpath(os.path.join(root_path, safe_blob_path))
    root_prefix = root_path if root_path.endswith(os.sep) else f"{root_path}{os.sep}"
    if candidate != root_path and not candidate.startswith(root_prefix):
        raise ValueError("artifact path escapes storage root")
    return Path(candidate)


def _relative_to_root(path: Path, root: Path) -> Path:
    root_path = os.path.realpath(os.fspath(root))
    resolved = os.path.realpath(os.fspath(path))
    root_prefix = root_path if root_path.endswith(os.sep) else f"{root_path}{os.sep}"
    if resolved != root_path and not resolved.startswith(root_prefix):
        raise ValueError("artifact path escapes storage root")
    return Path(resolved).relative_to(root_path)


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
