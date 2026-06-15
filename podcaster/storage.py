from __future__ import annotations

import logging
import os
import subprocess
import time
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

    def generate_download_url(self, path: str, *, expiry: datetime) -> SignedDownloadUrl:
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
            detail = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
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
