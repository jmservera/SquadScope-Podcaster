"""Private recovery operator authorization using the API's dedicated Key Vault identity."""

from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

from azure.core.pipeline.transport import RequestsTransport
from azure.identity import ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient

_SECRET_NAME = "video-recovery-operator-key"
_HOST = re.compile(r"podrv[a-z0-9]{13}\.vault\.azure\.net", re.ASCII)
_TTL_SECONDS = 60
_FETCH_TIMEOUT_SECONDS = 5


class RecoveryKeyUnavailable(Exception):
    """The recovery boundary is disabled or cannot obtain a fresh key."""


def _configuration() -> tuple[str, str] | None:
    if "PODCASTER_VIDEO_RECOVERY_OPERATOR_KEY" in os.environ:
        raise RecoveryKeyUnavailable
    uri = os.environ.get("PODCASTER_VIDEO_RECOVERY_OPERATOR_SECRET_URI")
    client_id = os.environ.get("PODCASTER_VIDEO_RECOVERY_IDENTITY_CLIENT_ID")
    if uri is None and client_id is None:
        return None
    if not uri or not client_id or uri.strip() != uri or client_id.strip() != client_id:
        raise RecoveryKeyUnavailable
    try:
        parsed = urlsplit(uri)
        if (
            parsed.scheme != "https"
            or not _HOST.fullmatch(parsed.netloc)
            or parsed.path != f"/secrets/{_SECRET_NAME}"
            or parsed.query
            or parsed.fragment
            or UUID(client_id).version is None
            or client_id.lower() == os.environ.get("AZURE_CLIENT_ID", "").lower()
        ):
            raise RecoveryKeyUnavailable
    except (ValueError, TypeError) as exc:
        raise RecoveryKeyUnavailable from exc
    return f"https://{parsed.netloc}", client_id


def _fetch_secret(vault_url: str, client_id: str) -> tuple[str, float]:
    # Both credential and secret HTTP requests have bounded retries and transport timeouts.
    with RequestsTransport(connection_timeout=2, read_timeout=2) as transport:
        credential = ManagedIdentityCredential(
            client_id=client_id, transport=transport, retry_total=0
        )
        try:
            client = SecretClient(
                vault_url=vault_url,
                credential=credential,
                logging_enable=False,
                retry_total=0,
                connection_timeout=2,
                read_timeout=2,
            )
            secret = client.get_secret(_SECRET_NAME)
            attributes = secret.properties
            now = datetime.now(timezone.utc)
            if (
                not isinstance(secret.value, str)
                or not secret.value
                or not all(0x21 <= ord(char) <= 0x7E for char in secret.value)
                or attributes.enabled is not True
                or (attributes.not_before is not None and attributes.not_before > now)
                or (attributes.expires_on is not None and attributes.expires_on <= now)
            ):
                raise RecoveryKeyUnavailable
            lifetime = _TTL_SECONDS
            if attributes.expires_on is not None:
                lifetime = min(lifetime, (attributes.expires_on - now).total_seconds())
            return secret.value, time.monotonic() + lifetime
        finally:
            credential.close()


class RecoveryOperatorKey:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recovery-key")
        self._config: tuple[str, str] | None = None
        self._value: str | None = None
        self._deadline = 0.0
        self._pending: Future[tuple[str, float]] | None = None

    def configured(self) -> bool:
        try:
            config = _configuration()
        except RecoveryKeyUnavailable:
            self.clear()
            return False
        if config is None:
            self.clear()
            return False
        return True

    def get(self) -> str:
        request_deadline = time.monotonic() + _FETCH_TIMEOUT_SECONDS
        try:
            config = _configuration()
        except RecoveryKeyUnavailable:
            self.clear()
            raise
        if config is None:
            self.clear()
            raise RecoveryKeyUnavailable
        if not self._lock.acquire(timeout=max(0, request_deadline - time.monotonic())):
            raise RecoveryKeyUnavailable
        try:
            if config != self._config:
                self._value = None
                self._deadline = 0
                self._config = config
            if self._value is not None and time.monotonic() < self._deadline:
                return self._value
            self._value = None
            self._deadline = 0
            if self._pending is not None:
                if not self._pending.done():
                    raise RecoveryKeyUnavailable
                self._pending = None  # Discard any result that arrived after a timeout.
            future = self._executor.submit(_fetch_secret, *config)
            self._pending = future
            try:
                value, deadline = future.result(timeout=max(0, request_deadline - time.monotonic()))
                if not value or deadline <= time.monotonic():
                    raise RecoveryKeyUnavailable
                self._value = value
                self._deadline = deadline
                self._pending = None
                return value
            except Exception:
                if future.done():
                    self._pending = None
                else:
                    future.add_done_callback(self._discard_timed_out)
                raise RecoveryKeyUnavailable from None
        finally:
            self._lock.release()

    def _discard_timed_out(self, future: Future[tuple[str, float]]) -> None:
        with self._lock:
            if self._pending is future:
                self._pending = None

    def clear(self) -> None:
        with self._lock:
            self._value = None
            self._deadline = 0
            self._config = None

    def close(self) -> None:
        self.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)


recovery_operator_key = RecoveryOperatorKey()
