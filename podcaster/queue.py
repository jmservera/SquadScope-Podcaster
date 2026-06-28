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
import logging
import os
from dataclasses import dataclass
from email.utils import formatdate
from typing import Protocol
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from podcaster.storage import (
    ManagedIdentityTokenCredential,
    normalize_artifact_base_url,
)

SYNTHESIS_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-synthesis-queue-v1"
VIDEO_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-video-queue-v1"
CLIP_QUEUE_SCHEMA_VERSION = "squadscope-podcaster-clip-queue-v1"
_STORAGE_SCOPE = "https://storage.azure.com/.default"
_QUEUE_API_VERSION = "2023-11-03"


def _mask_queue_url(url: str | None) -> str:
    """Mask a queue service URL for safe logging.

    Keeps the scheme and host (so the storage account is identifiable for
    diagnostics) but never logs query strings/SAS tokens or full paths.
    """

    if not url:
        return "<unset>"
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        host = parts.hostname or ""
        if not host:
            return "<set>"
        return f"{parts.scheme}://{host}/***"
    except Exception:
        return "<set>"


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
    ) -> list[QueueMessage]: ...

    def delete_message(self, message: QueueMessage) -> None: ...


class QueueProducer(Protocol):
    def send_message(self, body: str) -> None: ...


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


def encode_video_message(job_id: str) -> str:
    """Encode a video queue message body for a ``job_id``.

    Mirrors :func:`encode_synthesis_message` but stamps the video queue schema
    version so the video job runner (which parses with :func:`parse_job_id`)
    receives a well-formed ``{"schema_version": ..., "job_id": ...}`` payload.
    """

    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id is required to build a video message")
    payload = json.dumps(
        {"schema_version": VIDEO_QUEUE_SCHEMA_VERSION, "job_id": job_id.strip()},
        separators=(",", ":"),
    )
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def encode_clip_message(job_id: str, clip_index: int) -> str:
    """Encode a per-clip queue message body for ``(job_id, clip_index)``.

    Mirrors :func:`encode_video_message` but stamps the clip queue schema
    version and carries the additional ``clip_index`` so a recorder
    (which parses with :func:`parse_clip_job`) records exactly one segment.

    Only ``job_id`` and ``clip_index`` are placed on the wire — never secrets
    or PII.
    """

    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id is required to build a clip message")
    if isinstance(clip_index, bool) or not isinstance(clip_index, int):
        raise ValueError("clip_index must be an int to build a clip message")
    if clip_index < 0:
        raise ValueError("clip_index must be non-negative to build a clip message")
    payload = json.dumps(
        {
            "schema_version": CLIP_QUEUE_SCHEMA_VERSION,
            "job_id": job_id.strip(),
            "clip_index": clip_index,
        },
        separators=(",", ":"),
    )
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def parse_clip_job(body: str) -> tuple[str, int]:
    """Extract ``(job_id, clip_index)`` from a clip queue message body.

    Accepts base64-encoded JSON (the wire format) and falls back to raw JSON,
    mirroring :func:`parse_job_id`'s resilience. Raises :class:`ValueError`
    when either field is missing or malformed so a bad message is treated as a
    hard (poison) failure rather than being silently processed.
    """

    text = (body or "").strip()
    if not text:
        raise ValueError("clip queue message was empty")

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
        if not isinstance(data, dict):
            continue
        schema_version = data.get("schema_version")
        if schema_version is not None and schema_version != CLIP_QUEUE_SCHEMA_VERSION:
            # A present-but-mismatched schema means this is the wrong message
            # type (e.g. a video-jobs envelope) — reject rather than risk
            # consuming it as a clip job. Absent schema stays accepted for
            # raw-JSON resilience, mirroring parse_job_id.
            continue
        job_id = data.get("job_id")
        clip_index = data.get("clip_index")
        if not isinstance(job_id, str) or not job_id.strip():
            continue
        if isinstance(clip_index, bool) or not isinstance(clip_index, int):
            continue
        if clip_index < 0:
            continue
        return job_id.strip(), clip_index
    raise ValueError("clip queue message did not contain a valid job_id and clip_index")


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
        query = urlencode({"numofmessages": max_messages, "visibilitytimeout": visibility_timeout})
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

    def send_message(self, body: str) -> None:
        document = f"<QueueMessage><MessageText>{escape(body)}</MessageText></QueueMessage>"
        headers = self._headers()
        headers["Content-Type"] = "application/xml"
        request = Request(
            f"{self._queue_url}/messages",
            data=document.encode("utf-8"),
            method="POST",
            headers=headers,
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


class ConnectionStringQueueBackend:
    """Connection-string Storage Queue backend for local/test only (Azurite).

    Production is identity-only; Azurite does not speak Azure AD, so this thin
    path is gated on ``AZURE_STORAGE_CONNECTION_STRING`` exactly as
    ``docker-compose.test.yml`` documents. Bodies are passed through verbatim
    (callers already base64-encode JSON), so encoding matches the identity REST
    backend and the official SDK default.
    """

    def __init__(self, connection_string: str, queue_name: str) -> None:
        from azure.storage.queue import QueueClient

        self._client = QueueClient.from_connection_string(connection_string, queue_name)
        self._ensure_queue()

    def _ensure_queue(self) -> None:
        from azure.core.exceptions import ResourceExistsError

        try:
            self._client.create_queue()
        except ResourceExistsError:
            pass

    def receive_messages(
        self, max_messages: int = 1, *, visibility_timeout: int = 600
    ) -> list[QueueMessage]:
        received = self._client.receive_messages(
            messages_per_page=max_messages,
            max_messages=max_messages,
            visibility_timeout=visibility_timeout,
        )
        messages: list[QueueMessage] = []
        for msg in received:
            messages.append(
                QueueMessage(
                    message_id=msg.id,
                    pop_receipt=msg.pop_receipt,
                    body=msg.content,
                    dequeue_count=int(msg.dequeue_count or 0),
                )
            )
        return messages

    def delete_message(self, message: QueueMessage) -> None:
        self._client.delete_message(message.message_id, message.pop_receipt)

    def send_message(self, body: str) -> None:
        self._client.send_message(body)


def _connection_string() -> str | None:
    value = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    return value or None


def create_queue_backend() -> QueueBackend | None:
    """Build the synthesis queue backend from the environment.

    Returns ``None`` when ``PODCASTER_STORAGE_QUEUE_URL`` is unset so the runner
    can fail with a clear configuration error instead of guessing an endpoint.
    """

    queue_url = os.environ.get("PODCASTER_STORAGE_QUEUE_URL")
    queue_name = os.environ.get("PODCASTER_SYNTHESIS_QUEUE", "synthesis-jobs")
    conn = _connection_string()
    if conn:
        return ConnectionStringQueueBackend(conn, queue_name)
    if not queue_url:
        return None
    return AzureStorageQueueBackend(queue_url, queue_name)


def create_video_queue_backend() -> QueueBackend | None:
    """Build the video queue backend from the environment.

    Returns ``None`` when ``PODCASTER_STORAGE_QUEUE_URL`` is unset. The queue
    name defaults to ``video-jobs`` and is overridable with
    ``PODCASTER_VIDEO_QUEUE`` (matching the video job runner's consumer).
    """

    queue_url = os.environ.get("PODCASTER_STORAGE_QUEUE_URL")
    queue_name = os.environ.get("PODCASTER_VIDEO_QUEUE", "video-jobs")
    conn = _connection_string()
    if conn:
        return ConnectionStringQueueBackend(conn, queue_name)
    if not queue_url:
        return None
    return AzureStorageQueueBackend(queue_url, queue_name)


def enqueue_synthesis_job(job_id: str, *, producer: QueueProducer | None = None) -> bool:
    """Enqueue a synthesis message for ``job_id`` on the synthesis queue.

    Returns ``True`` when a message was sent. Returns ``False`` (without raising)
    when the synthesis queue is not configured — until the ACA Job (#76/#77/#78)
    is approved and provisioned, ``/api/generate`` keeps returning the
    deterministic placeholder with no behaviour regression.

    Only ``job_id`` is placed on the wire — never secrets or PII.
    """

    backend = producer
    if backend is None:
        backend = create_queue_backend()
    if backend is None:
        logging.info("synthesis queue not configured; skipping enqueue job_id=%s", job_id)
        return False
    backend.send_message(encode_synthesis_message(job_id))
    logging.info("enqueued synthesis job_id=%s", job_id)
    return True


def create_clip_queue_backend() -> QueueBackend | None:
    """Build the per-clip queue backend from the environment.

    Returns ``None`` when ``PODCASTER_STORAGE_QUEUE_URL`` is unset. The queue
    name defaults to ``video-clip-jobs`` and is overridable with
    ``PODCASTER_VIDEO_CLIP_QUEUE`` (matching the recorder's consumer).
    """

    queue_url = os.environ.get("PODCASTER_STORAGE_QUEUE_URL")
    queue_name = os.environ.get("PODCASTER_VIDEO_CLIP_QUEUE", "video-clip-jobs")
    conn = _connection_string()
    if conn:
        return ConnectionStringQueueBackend(conn, queue_name)
    if not queue_url:
        return None
    return AzureStorageQueueBackend(queue_url, queue_name)


def enqueue_video_job(job_id: str, *, producer: QueueProducer | None = None) -> bool:
    """Enqueue a video-generation message for ``job_id`` on the video queue.

    Returns ``True`` when a message was sent. Returns ``False`` (without raising)
    when the video queue is not configured, so audio-only synthesis keeps
    working when video generation infrastructure is not provisioned.

    Only ``job_id`` is placed on the wire — never secrets or PII.
    """

    queue_url = os.environ.get("PODCASTER_STORAGE_QUEUE_URL")
    queue_name = os.environ.get("PODCASTER_VIDEO_QUEUE", "video-jobs")
    logging.info(
        "enqueue_video_job diagnostics job_id=%s PODCASTER_STORAGE_QUEUE_URL_set=%s "
        "PODCASTER_STORAGE_QUEUE_URL=%s PODCASTER_VIDEO_QUEUE=%s",
        job_id,
        bool(queue_url),
        _mask_queue_url(queue_url),
        queue_name,
    )

    backend = producer
    if backend is None:
        backend = create_video_queue_backend()
    if backend is None:
        logging.info("video queue not configured; skipping enqueue job_id=%s", job_id)
        return False
    backend.send_message(encode_video_message(job_id))
    logging.info("enqueued video job_id=%s", job_id)
    return True


def enqueue_clip_job(
    job_id: str, clip_index: int, *, producer: QueueProducer | None = None
) -> bool:
    """Enqueue a per-clip recording message on the clip queue.

    Returns ``True`` when a message was sent. Returns ``False`` (without
    raising) when the clip queue is not configured, so callers that fan out
    recording can degrade gracefully when the scale-out infrastructure is not
    provisioned.

    Only ``job_id`` and ``clip_index`` are placed on the wire — never secrets
    or PII.
    """

    backend = producer
    if backend is None:
        backend = create_clip_queue_backend()
    if backend is None:
        logging.info(
            "clip queue not configured; skipping enqueue job_id=%s clip_index=%s",
            job_id,
            clip_index,
        )
        return False
    backend.send_message(encode_clip_message(job_id, clip_index))
    logging.info("enqueued clip job_id=%s clip_index=%s", job_id, clip_index)
    return True
