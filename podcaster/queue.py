"""Managed-identity Storage Queue access for the synthesis job runner (#76/#78).

The ACA synthesis Job (ADR 0001, Option C) is triggered by KEDA when messages
land on the synthesis Storage Queue, but the container must itself dequeue,
process, and delete each message. This module provides an identity-only
(no keys/connection strings) REST client for that, plus a tiny in-memory
backend used by tests.

Queue messages carry a ``job_id`` only — never secrets or PII. The agreed
on-the-wire format is base64-encoded JSON ``{"schema_version": ..., "job_id":
...}`` (matching the Azure SDK default encoding), but :func:`parse_job_id`
also accepts raw JSON for resilience.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from email.utils import formatdate
from typing import Protocol
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from podcaster.storage import (
    ManagedIdentityTokenCredential,
    normalize_artifact_base_url,
)

SYNTHESIS_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-synthesis-queue-v1"
_STORAGE_SCOPE = "https://storage.azure.com/.default"
_QUEUE_API_VERSION = "2023-11-03"


@dataclass(frozen=True)
class QueueMessage:
    """One received Storage Queue message (job_id payload only)."""

    message_id: str
    pop_receipt: str
    body: str
    dequeue_count: int


class QueueBackend(Protocol):
    def receive_messages(
        self, max_messages: int = 1, *, visibility_timeout: int = 600
    ) -> list[QueueMessage]:
        ...

    def delete_message(self, message: QueueMessage) -> None:
        ...


def encode_synthesis_message(job_id: str) -> str:
    """Encode a synthesis queue message body for a ``job_id``.

    Returns base64-encoded JSON so the payload round-trips through the Azure
    Storage Queue service the same way the official SDKs encode messages.
    """

    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id is required to build a synthesis message")
    payload = json.dumps(
        {"schema_version": SYNTHESIS_QUEUE_SCHEMA_VERSION, "job_id": job_id.strip()},
        separators=(",", ":"),
    )
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def parse_job_id(body: str) -> str:
    """Extract the ``job_id`` from a synthesis queue message body.

    Accepts base64-encoded JSON (the wire format) and falls back to raw JSON.
    Raises :class:`ValueError` when no ``job_id`` can be recovered so a
    malformed message is treated as a hard (poison) failure rather than being
    silently processed.
    """

    text = (body or "").strip()
    if not text:
        raise ValueError("synthesis queue message was empty")

    candidates: list[str] = []
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8")
        candidates.append(decoded)
    except (ValueError, UnicodeDecodeError):
        pass
    candidates.append(text)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            job_id = data.get("job_id")
            if isinstance(job_id, str) and job_id.strip():
                return job_id.strip()
    raise ValueError("synthesis queue message did not contain a job_id")


class AzureStorageQueueBackend:
    """Identity-only Storage Queue REST client (no keys/connection strings)."""

    def __init__(
        self,
        queue_service_url: str,
        queue_name: str,
        *,
        credential: ManagedIdentityTokenCredential | None = None,
    ) -> None:
        self._credential = credential or ManagedIdentityTokenCredential()
        self._queue_url = f"{normalize_artifact_base_url(queue_service_url)}/{queue_name}"

    def _headers(self) -> dict[str, str]:
        token = self._credential.get_token(_STORAGE_SCOPE)
        return {
            "Authorization": f"Bearer {token}",
            "x-ms-date": formatdate(timeval=None, localtime=False, usegmt=True),
            "x-ms-version": _QUEUE_API_VERSION,
        }

    def receive_messages(
        self, max_messages: int = 1, *, visibility_timeout: int = 600
    ) -> list[QueueMessage]:
        query = urlencode(
            {"numofmessages": max_messages, "visibilitytimeout": visibility_timeout}
        )
        request = Request(
            f"{self._queue_url}/messages?{query}",
            method="GET",
            headers=self._headers(),
        )
        with urlopen(request, timeout=30) as response:
            payload = response.read()
        return _parse_messages(payload)

    def delete_message(self, message: QueueMessage) -> None:
        query = urlencode({"popreceipt": message.pop_receipt})
        encoded_id = quote(message.message_id, safe="")
        request = Request(
            f"{self._queue_url}/messages/{encoded_id}?{query}",
            method="DELETE",
            headers=self._headers(),
        )
        with urlopen(request, timeout=30):
            return


def _parse_messages(payload: bytes) -> list[QueueMessage]:
    root = ElementTree.fromstring(payload)
    messages: list[QueueMessage] = []
    for element in root.findall("QueueMessage"):
        messages.append(
            QueueMessage(
                message_id=(element.findtext("MessageId") or "").strip(),
                pop_receipt=(element.findtext("PopReceipt") or "").strip(),
                body=(element.findtext("MessageText") or ""),
                dequeue_count=int((element.findtext("DequeueCount") or "0").strip() or "0"),
            )
        )
    return messages


def create_queue_backend() -> QueueBackend | None:
    """Build the synthesis queue backend from the environment.

    Returns ``None`` when ``PODCASTER_STORAGE_QUEUE_URL`` is unset so the runner
    can fail with a clear configuration error instead of guessing an endpoint.
    """

    queue_url = os.environ.get("PODCASTER_STORAGE_QUEUE_URL")
    queue_name = os.environ.get("PODCASTER_SYNTHESIS_QUEUE", "synthesis-jobs")
    if not queue_url:
        return None
    return AzureStorageQueueBackend(queue_url, queue_name)
