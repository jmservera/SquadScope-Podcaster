"""Queue authority timestamp regression tests."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from podcaster.queue import (
    AzureStorageQueueBackend,
    ConnectionStringQueueBackend,
    QueueMessage,
    _parse_messages,
)


def test_rest_message_carries_authoritative_next_visible_time():
    messages = _parse_messages(
        b"""<?xml version="1.0" encoding="utf-8"?>
<QueueMessagesList>
  <QueueMessage>
    <MessageId>m1</MessageId>
    <PopReceipt>r1</PopReceipt>
    <MessageText>body</MessageText>
    <DequeueCount>2</DequeueCount>
    <TimeNextVisible>Tue, 22 Sep 2026 18:55:00 GMT</TimeNextVisible>
  </QueueMessage>
</QueueMessagesList>"""
    )

    assert messages[0].next_visible_on == datetime(2026, 9, 22, 18, 55, tzinfo=timezone.utc)


def test_invalid_rest_next_visible_time_fails_closed():
    messages = _parse_messages(
        b"""<QueueMessagesList>
  <QueueMessage>
    <MessageId>m1</MessageId>
    <PopReceipt>r1</PopReceipt>
    <MessageText>body</MessageText>
    <DequeueCount>1</DequeueCount>
    <TimeNextVisible>not-a-timestamp</TimeNextVisible>
  </QueueMessage>
</QueueMessagesList>"""
    )

    assert messages[0].next_visible_on is None


def test_rest_renewal_rotates_receipt_and_uses_authoritative_expiry(monkeypatch):
    seen = []

    class Response:
        headers = {
            "x-ms-popreceipt": "rotated",
            "x-ms-time-next-visible": "Thu, 05 Nov 2026 15:30:00 GMT",
        }

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    def urlopen(request, *, timeout):
        seen.append((request, timeout))
        return Response()

    monkeypatch.setattr("podcaster.queue.urlopen", urlopen)
    backend = AzureStorageQueueBackend(
        "https://example.queue.core.windows.net",
        "video-jobs",
        credential=Mock(get_token=Mock(return_value="offline-token")),
    )
    before = QueueMessage("id/one", "old=receipt", "body", 2)
    updated = backend.update_message(before, visibility_timeout=900)
    request, timeout = seen[0]
    assert timeout == 30
    assert request.get_method() == "PUT"
    assert "id%2Fone" in request.full_url
    assert "popreceipt=old%3Dreceipt" in request.full_url
    assert "visibilitytimeout=900" in request.full_url
    assert request.data == (b"<QueueMessage><MessageText>body</MessageText></QueueMessage>")
    assert updated.pop_receipt == "rotated"
    assert updated.next_visible_on == datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)


def test_local_sdk_renewal_uses_rotated_receipt_and_authoritative_expiry():
    backend = ConnectionStringQueueBackend.__new__(ConnectionStringQueueBackend)
    backend._client = Mock()
    backend._client.update_message.return_value = Mock(
        pop_receipt="new-receipt",
        next_visible_on=datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc),
    )
    before = QueueMessage("id", "old-receipt", "body", 2)
    updated = backend.update_message(before, visibility_timeout=900)
    backend._client.update_message.assert_called_once_with(
        "id", "old-receipt", visibility_timeout=900
    )
    assert updated.pop_receipt == "new-receipt"
    assert updated.next_visible_on == datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)


def test_rest_renewal_without_authoritative_receipt_fails_closed(monkeypatch):
    class Response:
        headers = {"x-ms-time-next-visible": "Thu, 05 Nov 2026 15:30:00 GMT"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("podcaster.queue.urlopen", lambda *_args, **_kwargs: Response())
    backend = AzureStorageQueueBackend(
        "https://example.queue.core.windows.net",
        "video-jobs",
        credential=Mock(get_token=Mock(return_value="offline-token")),
    )
    with pytest.raises(ValueError, match="authoritative receipt"):
        backend.update_message(QueueMessage("id", "old", "body", 1), visibility_timeout=900)
