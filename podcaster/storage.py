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
    def put_bytes(self, path: str, content: bytes, content_type: str) -> StoredArtifact:
        ...

    def get_bytes(self, path: str) -> bytes | None:
        ...

    def update_bytes(self, path: str, content_type: str, update: Callable[[bytes | None], bytes]) -> StoredArtifact:
        ...

    def list_blobs(self, prefix: str, *, limit: int = 10) -> list[str]:
        ...

    def generate_download_url(self, path: str, *, expiry: datetime) -> SignedDownloadUrl:
        ...

    def blob_exists(self, path: str) -> bool:
        ...

    def upload_file(self, path: str, source: Path, content_type: str) -> StoredArtifact:
        ...

    def download_file(self, path: str, dest: Path) -> bool:
        ...

    def delete_blob(self, path: str) -> bool:
        ...

    def delete_prefix(self, prefix: str) -> int:
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

    def upload_file(self, path: str, source: Path, content_type: str) -> StoredArtifact:
        import shutil

        safe_path = _safe_blob_path(path)
        target = self.root / safe_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
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
            raise RuntimeError(f"blob list failed for {safe_prefix}: HTTP {exc.code} {detail}") from exc

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
            raise RuntimeError(f"user-delegation SAS generation returned no https URL for {safe_path}")
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
            raise RuntimeError(f"blob existence check failed for {safe_path}: HTTP {exc.code} {detail}") from exc

    def upload_file(self, path: str, source: Path, content_type: str) -> StoredArtifact:
        # Stream the file from disk straight into the PUT body so the whole
        # blob is never held in memory — intermediates can be multi-GB videos.
        safe_path = _safe_blob_path(path)
        size = source.stat().st_size
        encoded_path = "/".join(quote(part, safe="") for part in safe_path.split("/"))
        token = self._credential.get_token("https://storage.azure.com/.default")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Length": str(size),
            "Content-Type": content_type,
            "x-ms-blob-type": "BlockBlob",
            "x-ms-date": formatdate(timeval=None, localtime=False, usegmt=True),
            "x-ms-version": "2023-11-03",
        }
        with source.open("rb") as handle:
            request = Request(
                f"{self._account_url}/{self._container_name}/{encoded_path}",
                data=handle,
                method="PUT",
                headers=headers,
            )
            try:
                with urlopen(request, timeout=300):
                    pass
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"blob file upload failed for {safe_path}: HTTP {exc.code} {detail}") from exc
        return StoredArtifact(
            path=safe_path,
            url=f"{self._account_url}/{self._container_name}/{safe_path}",
            size_bytes=size,
            content_type=content_type,
        )

    def download_file(self, path: str, dest: Path) -> bool:
        # Stream the response body to disk in chunks so the file never has to
        # be materialised in memory.
        import shutil

        safe_path = _safe_blob_path(path)
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
            with urlopen(request, timeout=300) as response:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as handle:
                    shutil.copyfileobj(response, handle, length=1024 * 1024)
        except HTTPError as exc:
            if exc.code == 404:
                return False
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"blob file download failed for {safe_path}: HTTP {exc.code} {detail}") from exc
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
            raise RuntimeError(f"blob delete failed for {safe_path}: HTTP {exc.code} {detail}") from exc

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


def create_storage_backend() -> StorageBackend:
    account_url = os.environ.get("PODCASTER_STORAGE_ACCOUNT_URL")
    container = os.environ.get("PODCASTER_STORAGE_CONTAINER", "podcaster-artifacts")
    if account_url:
        return AzureBlobStorageBackend(account_url=account_url, container_name=container)

    root = Path(os.environ.get("PODCASTER_LOCAL_STORAGE_PATH", ".podcaster-artifacts"))
    base_url = os.environ.get("PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/podcaster-stub")
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
    if account_url:
        return AzureBlobStorageBackend(account_url=account_url, container_name=container)

    root = Path(os.environ.get("PODCASTER_LOCAL_SCRATCH_PATH", ".podcaster-scratch"))
    base_url = os.environ.get("PODCASTER_ARTIFACT_BASE_URL", "https://example.invalid/podcaster-scratch")
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
    client_id = os.environ.get("AZURE_CLIENT_ID")
    if app_service_endpoint and app_service_header:
        params: dict[str, str] = {"api-version": "2019-08-01", "resource": resource}
        if client_id:
            params["client_id"] = client_id
        query = urlencode(params)
        separator = "&" if "?" in app_service_endpoint else "?"
        request = Request(f"{app_service_endpoint}{separator}{query}", headers={"X-IDENTITY-HEADER": app_service_header})
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
                    "managed identity token request retrying after HTTP %s on attempt %s/%s; sleeping %.0fs",
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
                    "managed identity token request retrying after network error on attempt %s/%s; sleeping %.0fs: %s",
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
        raise ValueError("artifact base URL must not contain credentials, query strings, or fragments")
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
